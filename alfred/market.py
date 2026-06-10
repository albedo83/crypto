"""MarketDataMaster — the single Hyperliquid data layer feeding every bot.

One process-wide instance owns:
  - one WebSocket connection (asyncio, reconnect + re-subscribe — the SDK's
    WebsocketManager has no reconnection, so we manage our own, pattern
    proven by the legacy collector.py):
      · `candle` 4h × all symbols — kills the hourly candleSnapshot burst
      · `trades` × all symbols — trade-flow aggregation (flow.py)
  - residual REST:
      · metaAndAssetCtxs poll (markPx/OI/funding/premium/impactPxs) — kept
        on REST because allMids carries mids, not the markPx every rule uses
      · candleSnapshot for the boot backfill and gap repair after reconnects
      · DXY via Yahoo Finance (6h cadence, 48h cache)
  - the shared in-memory state: `states` (SymbolState per symbol, read
    directly by bots on the same event loop) and `snapshot` (immutable
    MarketSnapshot rebuilt hourly: features, btc_z, cross_ctx, …)
  - market.db writes (ticks, market_snapshots, trade_flow, events)

Self-auditing: hourly gap check (re-fetch any symbol whose candle history
has holes) and hourly WS-vs-REST candle audit on a rotating sample, logged
as CANDLE_AUDIT events — this is the phase-2 observation evidence.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field

import orjson
import websockets

from . import features
from .db import Database, log_ticks
from .flow import TradeFlowAggregator
from .models import SymbolState
from .settings import Params
from .telegram import Notifier

log = logging.getLogger("alfred")

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
PERIOD_MS = 14_400_000  # 4h
RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30, 30]


@dataclass(frozen=True)
class MarketSnapshot:
    """Immutable hourly view of the cross-market context, computed once by
    the master instead of once per bot process like the legacy runtime."""
    version: int
    ts: float
    feature_cache: dict = field(default_factory=dict)
    btc_f: dict = field(default_factory=dict)          # btc_30d, btc_7d
    btc_z: float | None = None
    btc_ret_4h_bps: float | None = None
    cross_ctx: dict = field(default_factory=dict)      # disp_24h, disp_7d, stress…
    oi_summary: dict = field(default_factory=dict)     # falling / rising counts
    dxy_7d: float = 0.0
    alt_index: float = 0.0


def http_fetch(url: str, payload: bytes | None = None, headers: dict | None = None,
               timeout: int = 15, retries: int = 3) -> bytes:
    """HTTP request with exponential backoff (1s, 2s, 4s)."""
    hdrs = headers or {"Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            log.debug("http_fetch attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 ** attempt)


def closed_candles(candles_iter) -> list:
    """Only fully-closed 4h candles (drops the in-progress one) — keeps the
    live feature semantics identical to the backtest (v12.11.2 invariant)."""
    now_ms = int(time.time() * 1000)
    return [c for c in candles_iter if c["t"] + PERIOD_MS <= now_ms]


def fetch_dxy(degraded: list, dxy_cache_path: str) -> float:
    """DXY 7-day return (bps) via Yahoo Finance with 3-tier fallback:
    fresh cache (<6h) → live fetch → stale cache (6-48h) → 0.0."""
    def _read_cache() -> tuple[float | None, float]:
        if not os.path.exists(dxy_cache_path):
            return None, 999
        age_h = (time.time() - os.path.getmtime(dxy_cache_path)) / 3600
        try:
            with open(dxy_cache_path) as fh:
                daily = json.load(fh)
            if len(daily) >= 10:
                closes = [d["c"] for d in daily[-10:]]
                if closes[-6] > 0:
                    return (closes[-1] / closes[-6] - 1) * 1e4, age_h
        except Exception:
            pass
        return None, age_h

    cached, age_h = _read_cache()
    if cached is not None and age_h < 6:
        for tag in ["DXY", "DXY_STALE"]:
            if tag in degraded:
                degraded.remove(tag)
        return cached

    try:
        end_ts = int(time.time())
        start_ts = end_ts - 30 * 86400
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
               f"?period1={start_ts}&period2={end_ts}&interval=1d")
        raw = json.loads(http_fetch(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10))
        result = raw["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        daily = [{"t": ts * 1000, "c": c} for ts, c in zip(timestamps, closes) if c]
        if daily and len(daily) >= 6:
            os.makedirs(os.path.dirname(dxy_cache_path), exist_ok=True)
            with open(dxy_cache_path, "w") as fh:
                json.dump(daily, fh)
            for tag in ["DXY", "DXY_STALE"]:
                if tag in degraded:
                    degraded.remove(tag)
            return (daily[-1]["c"] / daily[-6]["c"] - 1) * 1e4
    except Exception as e:
        log.warning("DXY fetch failed: %s", e)

    if cached is not None and age_h < 48:
        if "DXY_STALE" not in degraded:
            degraded.append("DXY_STALE")
        if "DXY" in degraded:
            degraded.remove("DXY")
        log.warning("DXY stale (%.0fh old) -- using cached value", age_h)
        return cached

    if "DXY" not in degraded:
        degraded.append("DXY")
    if "DXY_STALE" in degraded:
        degraded.remove("DXY_STALE")
    log.warning("DXY unavailable (cache >48h or missing)")
    return 0.0


class MarketDataMaster:
    def __init__(self, p: Params, db: Database, notifier: Notifier,
                 data_dir: str,
                 rest_poll_seconds: float = 20.0,
                 candle_fetch_sleep: float = 0.5,
                 audit_sample_size: int = 5):
        self.p = p
        self.db = db
        self.notifier = notifier
        self.data_dir = data_dir
        self.rest_poll_seconds = rest_poll_seconds
        self.candle_fetch_sleep = candle_fetch_sleep
        self.audit_sample_size = audit_sample_size

        self.states: dict[str, SymbolState] = {s: SymbolState() for s in p.all_symbols}
        self.token_sector = p.token_sector()
        self.snapshot: MarketSnapshot | None = None
        self.flow = TradeFlowAggregator(db, p.all_symbols)
        self.last_price_fetch: float = 0.0
        self.ws_connected: bool = False
        self.ws_reconnects: int = 0
        self.ws_candle_updates: int = 0
        self._degraded: list[str] = []
        self._dxy_cache: tuple[float, float] = (0.0, 0.0)  # (value, fetched_at)
        self._audit_cycle = itertools.cycle(sorted(p.all_symbols))
        self._snapshot_version = 0
        self.running = True

    # ── REST: prices (metaAndAssetCtxs) ──────────────────────────────

    def fetch_prices(self) -> tuple[list | None, list | None]:
        """One call for the whole universe. Updates states in place."""
        try:
            payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
            data = json.loads(http_fetch(INFO_URL, payload))
            meta, ctxs = data[0], data[1]
            if len(meta["universe"]) != len(ctxs):
                log.warning("API mismatch: %d universe vs %d ctxs",
                            len(meta["universe"]), len(ctxs))
                return None, None
            now = time.time()
            for i, asset in enumerate(meta["universe"]):
                name = asset["name"]
                st = self.states.get(name)
                if st is None:
                    continue
                price = float(ctxs[i].get("markPx", 0))
                if price <= 0:
                    continue
                st.price = price
                st.updated_at = now
                st.price_ticks.append((now, price))
                oi = float(ctxs[i].get("openInterest") or 0)
                if oi > 0:
                    st.oi = oi
                    st.oi_history.append((now, oi))
                st.funding = float(ctxs[i].get("funding") or 0)
                st.premium = float(ctxs[i].get("premium") or 0)
                impacts = ctxs[i].get("impactPxs") or []
                if len(impacts) >= 2:
                    try:
                        ib, ia = float(impacts[0]), float(impacts[1])
                        if ib > 0 and ia > ib:
                            st.impact_bid = ib
                            st.impact_ask = ia
                    except (TypeError, ValueError):
                        pass
            return meta["universe"], ctxs
        except Exception as e:
            log.warning("Price fetch error: %s", e)
            return None, None

    async def poll_loop(self):
        """REST tick poll. Cadence configurable (20s nominal; 60s in the
        phase-2 observation deployment to spare the shared-IP budget)."""
        while self.running:
            meta_u, ctxs = await asyncio.to_thread(self.fetch_prices)
            if meta_u and ctxs:
                self.last_price_fetch = time.time()
                await asyncio.to_thread(
                    log_ticks, self.db, ctxs, meta_u, self.p.all_symbols)
            await asyncio.sleep(self.rest_poll_seconds)

    # ── REST: candle backfill & repair ───────────────────────────────

    def _fetch_candle_snapshot(self, symbol: str, days: int) -> list[dict]:
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - days * 86400 * 1000
        payload = json.dumps({"type": "candleSnapshot", "req": {
            "coin": symbol, "interval": "4h",
            "startTime": start_ts, "endTime": end_ts,
        }}).encode()
        raw = json.loads(http_fetch(INFO_URL, payload))
        return [{
            "t": c["t"], "o": float(c["o"]), "c": float(c["c"]),
            "h": float(c["h"]), "l": float(c["l"]), "v": float(c.get("v", 0)),
        } for c in raw]

    def _refill_symbol(self, symbol: str, days: int) -> bool:
        """Replace a symbol's candle deque from a REST snapshot (atomic swap)."""
        try:
            candles = self._fetch_candle_snapshot(symbol, days)
        except Exception as e:
            log.warning("Candle fetch %s: %s", symbol, e)
            return False
        if not candles:
            return False
        st = self.states[symbol]
        new_deque: deque = deque(candles, maxlen=st.candles_4h.maxlen)
        st.candles_4h = new_deque
        st.last_candle_ts = candles[-1]["t"]
        return True

    def _days_for(self, symbol: str) -> int:
        # BTC needs 30d lookback + 180d z-window for the macro modulator.
        return 250 if symbol == "BTC" else 45

    async def backfill(self):
        """Boot backfill, sequenced to avoid bursting the shared IP."""
        t0 = time.time()
        ok = 0
        for sym in self.p.all_symbols:
            if await asyncio.to_thread(self._refill_symbol, sym, self._days_for(sym)):
                ok += 1
            await asyncio.sleep(self.candle_fetch_sleep)
        log.info("Backfill: %d/%d symbols in %.0fs",
                 ok, len(self.p.all_symbols), time.time() - t0)
        self.db.log_event("BACKFILL", None, {
            "ok": ok, "total": len(self.p.all_symbols),
            "seconds": round(time.time() - t0, 1)})

    def _gap_symbols(self) -> list[str]:
        """Symbols whose candle history has holes or is stale."""
        now_ms = int(time.time() * 1000)
        cur_period = (now_ms // PERIOD_MS) * PERIOD_MS
        out = []
        for sym, st in self.states.items():
            if not st.candles_4h:
                out.append(sym)
                continue
            # Missing the in-progress candle 10+ min into the period: either
            # the WS missed the candle open or the symbol simply hasn't
            # traded yet — repair is idempotent and cheap.
            if st.last_candle_ts < cur_period and now_ms - cur_period > 600_000:
                out.append(sym)
                continue
            ts_list = [c["t"] for c in st.candles_4h][-50:]
            if any(b - a != PERIOD_MS for a, b in zip(ts_list, ts_list[1:])):
                out.append(sym)
        return out

    async def repair_gaps(self, reason: str):
        gaps = self._gap_symbols()
        if not gaps:
            return
        log.info("Gap repair (%s): %s", reason, ", ".join(sorted(gaps)))
        for sym in gaps:
            await asyncio.to_thread(self._refill_symbol, sym, self._days_for(sym))
            await asyncio.sleep(self.candle_fetch_sleep)
        self.db.log_event("CANDLE_GAP_REPAIR", None,
                          {"reason": reason, "symbols": sorted(gaps)})

    async def audit_candles(self):
        """WS-vs-REST audit on a rotating sample of symbols: compare the
        last ~30 closed candles held in memory against a fresh REST
        snapshot. Phase-2 success evidence (criterion: WS == REST)."""
        sample = [next(self._audit_cycle) for _ in range(self.audit_sample_size)]
        report = {}
        for sym in sample:
            mem = {c["t"]: c for c in closed_candles(self.states[sym].candles_4h)[-30:]}
            if not mem:
                report[sym] = {"status": "no_data"}
                continue
            try:
                ref = await asyncio.to_thread(self._fetch_candle_snapshot, sym, 6)
            except Exception as e:
                report[sym] = {"status": f"rest_error: {e}"}
                continue
            ref_closed = {c["t"]: c for c in closed_candles(ref)}
            common = sorted(set(mem) & set(ref_closed))
            # "missing" = REST candles inside the window mem actually covers
            # (REST snapshots reach further back than the compared 30 candles)
            mem_start = min(mem)
            missing = sorted(t for t in ref_closed
                             if t not in mem and t >= mem_start)[:10]
            mismatch = []
            for t in common:
                a, b = mem[t], ref_closed[t]
                # o/h/l/c must match exactly (same source); volume can settle
                # late on the REST side — tolerate 1%.
                if (a["o"] != b["o"] or a["c"] != b["c"]
                        or a["h"] != b["h"] or a["l"] != b["l"]
                        or abs(a["v"] - b["v"]) > 0.01 * max(b["v"], 1e-9)):
                    mismatch.append(t)
            report[sym] = {"compared": len(common),
                           "mismatch": mismatch, "missing_in_mem": missing}
            await asyncio.sleep(self.candle_fetch_sleep)
        n_bad = sum(1 for r in report.values()
                    if r.get("mismatch") or r.get("missing_in_mem"))
        self.db.log_event("CANDLE_AUDIT", None, report)
        if n_bad:
            log.warning("Candle audit: %d/%d symbols with discrepancies: %s",
                        n_bad, len(sample),
                        {k: v for k, v in report.items()
                         if v.get("mismatch") or v.get("missing_in_mem")})
        else:
            log.info("Candle audit clean (%s)", ", ".join(sample))

    # ── WebSocket: candle + trades ───────────────────────────────────

    def _ingest_candle(self, c: dict):
        sym = c.get("s")
        st = self.states.get(sym)
        if st is None:
            return
        try:
            row = {"t": int(c["t"]), "o": float(c["o"]), "c": float(c["c"]),
                   "h": float(c["h"]), "l": float(c["l"]),
                   "v": float(c.get("v", 0))}
        except (KeyError, TypeError, ValueError):
            return
        if st.candles_4h and st.candles_4h[-1]["t"] == row["t"]:
            st.candles_4h[-1] = row          # in-progress candle update
        elif not st.candles_4h or row["t"] > st.candles_4h[-1]["t"]:
            st.candles_4h.append(row)        # new period opened
        # older-than-last updates are ignored (REST backfill owns history)
        st.last_candle_ts = row["t"]
        self.ws_candle_updates += 1

    async def ws_loop(self):
        """Single WS connection, re-subscribe + gap repair after reconnect."""
        attempt = 0
        while self.running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                              ping_timeout=10, open_timeout=15,
                                              max_size=2**22) as ws:
                    log.info("WS connected (%d symbols × candle+trades)",
                             len(self.p.all_symbols))
                    for sym in self.p.all_symbols:
                        await ws.send(orjson.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "candle", "coin": sym,
                                             "interval": "4h"}}).decode())
                        await ws.send(orjson.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "trades", "coin": sym}
                        }).decode())
                    self.ws_connected = True
                    if attempt > 0:
                        self.ws_reconnects += 1
                        self.db.log_event("WS_RECONNECT", None,
                                          {"attempt": attempt})
                        # candles may have moved while we were away
                        asyncio.get_running_loop().create_task(
                            self.repair_gaps("ws_reconnect"))
                    attempt = 0
                    async for raw in ws:
                        msg = orjson.loads(raw)
                        channel = msg.get("channel")
                        if channel == "candle":
                            data = msg.get("data")
                            if isinstance(data, list):
                                for c in data:
                                    self._ingest_candle(c)
                            elif isinstance(data, dict):
                                self._ingest_candle(data)
                        elif channel == "trades":
                            self.flow.ingest(msg["data"])
            except asyncio.CancelledError:
                self.flow.flush_all()
                log.info("WS loop stopped")
                return
            except Exception as e:
                self.ws_connected = False
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                log.warning("WS: %s — reconnecting in %ds", e, delay)
                await asyncio.sleep(delay)
                attempt += 1

    # ── Hourly snapshot (features, btc_z, cross_ctx, market_snapshots) ──

    def _compute_features_for(self, sym: str) -> dict | None:
        st = self.states.get(sym)
        if not st or len(st.candles_4h) < 50:
            return None
        candles = closed_candles(st.candles_4h)
        if len(candles) < 50:
            return None
        return features.compute_features(candles)

    def _oi_features(self, sym: str) -> dict:
        st = self.states[sym]
        return features.compute_oi_features(list(st.oi_history), st.funding)

    def build_snapshot(self) -> MarketSnapshot:
        """Compute the full cross-market context once (legacy: once per bot
        process). Pure CPU — caller runs it in a thread."""
        p = self.p
        feature_cache = {sym: self._compute_features_for(sym)
                         for sym in p.trade_symbols + ("ETH",)}

        falling = rising = 0
        for sym in p.trade_symbols:
            d = self._oi_features(sym)["oi_delta_1h"]
            if d < -0.5:
                falling += 1
            elif d > 0.5:
                rising += 1

        btc_f: dict = {}
        btc_z = None
        btc_ret_4h = None
        btc = self.states.get("BTC")
        if btc and len(btc.candles_4h) >= 50:
            btc_closed = closed_candles(btc.candles_4h)
            if len(btc_closed) >= 50:
                btc_f = features.compute_btc_features(btc_closed)
            if len(btc_closed) >= 200:
                btc_z = features.compute_btc_z(
                    btc_closed,
                    lookback_days=p.macro_lookback_days,
                    z_window_days=p.macro_z_window_days)
            if len(btc_closed) >= 2 and btc_closed[-2]["c"] > 0:
                btc_ret_4h = (btc_closed[-1]["c"] / btc_closed[-2]["c"] - 1) * 1e4

        from . import signals as _signals
        cross_ctx = _signals.compute_cross_context(
            feature_cache, p.trade_symbols, self.token_sector)

        # DXY: 6h cadence behind the in-memory cache
        dxy_val, dxy_at = self._dxy_cache
        if time.time() - dxy_at > 6 * 3600:
            dxy_val = fetch_dxy(self._degraded,
                                os.path.join(self.data_dir, "macro_DXY.json"))
            self._dxy_cache = (dxy_val, time.time())

        alt_index = features.compute_alt_index(feature_cache, p.trade_symbols)

        self._snapshot_version += 1
        return MarketSnapshot(
            version=self._snapshot_version, ts=time.time(),
            feature_cache=feature_cache, btc_f=btc_f, btc_z=btc_z,
            btc_ret_4h_bps=btc_ret_4h, cross_ctx=cross_ctx,
            oi_summary={"falling": falling, "rising": rising},
            dxy_7d=dxy_val, alt_index=alt_index)

    def log_market_snapshot(self):
        """Hourly per-token observation rows (same schema as legacy)."""
        snap = self.snapshot
        if snap is None:
            return
        ts_epoch = int(time.time())
        rows = []
        for sym in self.p.trade_symbols:
            st = self.states.get(sym)
            if not st or st.price == 0:
                continue
            oi_f = self._oi_features(sym)
            feat = snap.feature_cache.get(sym)
            crowd = features.compute_crowding_score(
                st.funding, st.premium, oi_f["oi_delta_1h"],
                feat.get("vol_z") if feat else None)
            rows.append((ts_epoch, sym, round(st.price, 6), round(st.oi, 2),
                         oi_f["oi_delta_1h"], round(st.funding * 1e6, 2),
                         round(st.premium * 1e6, 2), crowd,
                         round(feat.get("vol_z", 0), 2) if feat else 0))
        self.db.write("""INSERT OR IGNORE INTO market_snapshots
            (ts, symbol, price, oi, oi_delta_1h_pct, funding_ppm, premium_ppm, crowding, vol_z)
            VALUES (?,?,?,?,?,?,?,?,?)""", rows)

    async def hourly_loop(self):
        """Data-health duties: gap repair + WS audit + status line. The
        snapshot refresh + market_snapshots logging are owned by the
        scheduler in __main__ (which also drives the bots' scan cadence)."""
        while self.running:
            try:
                await self.repair_gaps("hourly")
                await self.audit_candles()
                fresh = sum(1 for st in self.states.values()
                            if time.time() - st.updated_at < 120)
                snap = self.snapshot
                log.info(
                    "Status: %d/%d fresh prices | WS %s (%d cand-upd, %d reconnects) "
                    "| snap v%d btc_z=%s disp24h=%s dxy=%.0f",
                    fresh, len(self.states),
                    "up" if self.ws_connected else "DOWN",
                    self.ws_candle_updates, self.ws_reconnects,
                    snap.version,
                    f"{snap.btc_z:+.2f}" if snap.btc_z is not None else "n/a",
                    snap.cross_ctx.get("disp_24h"), snap.dxy_7d)
            except Exception:
                log.exception("hourly_loop error")
            await asyncio.sleep(3600)
