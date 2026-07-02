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
import statistics
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

# Breadth marché-large : perps exclus du calcul (stablecoins ~0%, diluent le signal).
_STABLE_SYMBOLS = frozenset({"USDC", "USDT", "DAI", "USDE", "FDUSD", "TUSD", "USD"})


def _compute_breadth(rets_bps: list[float]) -> dict:
    """Indice de capitulation marché-large depuis les rendements 24h (bps) de
    TOUS les perps (hors stables). Signal de contexte pour les arbitres IA —
    pas une règle. down20/down10 = % d'alts en ≤−20%/≤−10% sur 24h."""
    n = len(rets_bps)
    if n == 0:
        return {"n": 0, "down20_pct": None, "down10_pct": None,
                "median_24h_bps": None}
    d20 = sum(1 for r in rets_bps if r <= -2000) / n * 100.0
    d10 = sum(1 for r in rets_bps if r <= -1000) / n * 100.0
    return {"n": n, "down20_pct": round(d20, 1), "down10_pct": round(d10, 1),
            "median_24h_bps": round(statistics.median(rets_bps), 0)}


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
    capitulation: dict = field(default_factory=dict)   # breadth marché-large (down20/10, médiane)
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


def sync_funding_hourly(db: Database, symbols, floor_days: int = 7,
                        sleep_s: float = 0.25) -> int:
    """Funding horaire RÉALISÉ (REST fundingHistory) → table funding_hourly.

    Resume par symbole depuis le dernier ts stocké (plancher now−floor_days :
    le deep history pré-Alfred vit dans funding_history.db côté backtests).
    Une page API = 500 rows max — un trou plus large se résorbe sur les
    appels suivants (resume). Retourne le nombre de rows insérées.
    """
    total = 0
    now_ms = int(time.time() * 1000)
    for sym in sorted(symbols):
        try:
            with db.lock:   # connexion partagée entre threads
                last = db.conn.execute(
                    "SELECT MAX(ts) FROM funding_hourly WHERE symbol=?",
                    (sym,)).fetchone()[0] or 0
            start = max(last + 1, now_ms - floor_days * 86400_000)
            payload = json.dumps({"type": "fundingHistory", "coin": sym,
                                  "startTime": start}).encode()
            raw = json.loads(http_fetch(INFO_URL, payload))
            rows = [(sym, int(r["time"]), float(r["fundingRate"]),
                     float(r.get("premium") or 0)) for r in raw]
            if rows:
                db.write("INSERT OR IGNORE INTO funding_hourly VALUES (?,?,?,?)",
                         rows)
                total += len(rows)
        except Exception as e:
            log.warning("Funding sync %s: %s", sym, e)
        time.sleep(sleep_s)
    return total


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
        # A2 — downtime détecté au boot ({from_ms, to_ms, seconds} | None).
        # Lu par BotInstance.load() pour le rattrapage d'excursions (A3) et
        # par la vue /master (couverture données).
        self.last_downtime: dict | None = None
        self._degraded: list[str] = []
        self._dxy_cache: tuple[float, float] = (0.0, 0.0)  # (value, fetched_at)
        self._capitulation: dict = {}   # breadth marché-large, rafraîchi à chaque fetch_prices
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
            rets_bps: list[float] = []
            for i, asset in enumerate(meta["universe"]):
                name = asset["name"]
                # Breadth marché-large : rendement 24h de TOUS les perps (hors
                # stables), capturé avant le filtre aux 36 suivis.
                if name not in _STABLE_SYMBOLS:
                    try:
                        _mpx = float(ctxs[i].get("markPx") or 0)
                        _ppx = float(ctxs[i].get("prevDayPx") or 0)
                        if _mpx > 0 and _ppx > 0:
                            rets_bps.append((_mpx / _ppx - 1) * 1e4)
                    except (TypeError, ValueError):
                        pass
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
            self._capitulation = _compute_breadth(rets_bps)
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

    def _refill_symbol(self, symbol: str, days: int,
                       merge: bool = False) -> bool:
        """Refresh a symbol's candles from a REST snapshot.

        merge=False : atomic swap of the whole deque (full backfill).
        merge=True  : union with the existing deque keyed by t — used by the
        boot repair when fetching a window SHORTER than the restored history
        (a swap would wipe the DB-restored candles down to the fetch window).

        A1 — persists the fetched bars (closed flag derived per bar)."""
        try:
            candles = self._fetch_candle_snapshot(symbol, days)
        except Exception as e:
            log.warning("Candle fetch %s: %s", symbol, e)
            return False
        if not candles:
            return False
        st = self.states[symbol]
        if merge and st.candles_4h:
            by_t = {c["t"]: c for c in st.candles_4h}
            by_t.update({c["t"]: c for c in candles})
            merged = [by_t[t] for t in sorted(by_t)]
            st.candles_4h = deque(merged, maxlen=st.candles_4h.maxlen)
        else:
            st.candles_4h = deque(candles, maxlen=st.candles_4h.maxlen)
        st.last_candle_ts = st.candles_4h[-1]["t"]
        now_ms = int(time.time() * 1000)
        done = [c for c in candles if c["t"] + PERIOD_MS <= now_ms]
        cur = [c for c in candles if c["t"] + PERIOD_MS > now_ms]
        if done:
            self._persist_candles(symbol, done, closed=True)
        if cur:
            self._persist_candles(symbol, cur, closed=False)
        return True

    def _days_for(self, symbol: str) -> int:
        # BTC needs 30d lookback + 180d z-window for the macro modulator.
        return 250 if symbol == "BTC" else 45

    def _load_from_db(self) -> int:
        """A2 — fill the deques from the candles table. Returns the number of
        symbols restored. Instant and offline-capable."""
        ok = 0
        for sym in self.p.all_symbols:
            st = self.states[sym]
            candles = self.db.load_candles(sym, st.candles_4h.maxlen)
            if not candles:
                continue
            # In-memory format drops T (derived where needed)
            rows = [{"t": c["t"], "o": c["o"], "c": c["c"],
                     "h": c["h"], "l": c["l"], "v": c["v"]} for c in candles]
            st.candles_4h = deque(rows, maxlen=st.candles_4h.maxlen)
            st.last_candle_ts = rows[-1]["t"]
            ok += 1
        return ok

    def _detect_downtime(self):
        """A2 — compare the freshest closed candle in DB to now. A gap larger
        than one full period + margin = the process was down (or the WS dead
        without repair) over that window. Logged + kept on self for the
        excursion catch-up (A3) and the /master data-coverage panel."""
        last_close_ms = self.db.last_closed_candle_ts()
        if not last_close_ms:
            return  # empty table = first boot, no downtime concept
        now_ms = int(time.time() * 1000)
        gap_ms = now_ms - last_close_ms
        # One full 4h period can legitimately be "open" — beyond
        # PERIOD_MS + 10 min, candles that should have closed are missing.
        if gap_ms > PERIOD_MS + 600_000:
            self.last_downtime = {
                "from_ms": last_close_ms, "to_ms": now_ms,
                "seconds": round(gap_ms / 1000),
            }
            log.warning("DOWNTIME detected: %.1fh without closed candles "
                        "(since %s)", gap_ms / 3_600_000,
                        time.strftime("%F %T", time.gmtime(last_close_ms / 1000)))
            self.db.log_event("DOWNTIME", None, self.last_downtime)

    async def backfill(self):
        """A2 — boot recovery: DB first, REST only for what's missing.

        1. Restore deques from the candles table (instant, offline OK).
        2. Detect downtime (gap between last closed candle and now).
        3. REST repair, scoped: full-history fetch ONLY for symbols absent
           from the DB (first boot); for the rest, fetch a window sized to
           the actual gap (gap repair semantics).
        """
        t0 = time.time()
        restored = self._load_from_db()
        self._detect_downtime()

        fetched = 0
        if restored:
            # Targeted repair: only the missing window per symbol.
            gap_days_default = 2
            if self.last_downtime:
                gap_days_default = max(
                    2, int(self.last_downtime["seconds"] / 86400) + 1)
            for sym in self.p.all_symbols:
                st = self.states[sym]
                if not st.candles_4h:
                    days, merge = self._days_for(sym), False   # absent from DB
                elif sym == "BTC" and len(st.candles_4h) < 220 * 6:
                    days, merge = self._days_for(sym), False   # macro window incomplete
                else:
                    days = min(gap_days_default, self._days_for(sym))
                    merge = True                               # partial window
                if await asyncio.to_thread(self._refill_symbol, sym, days, merge):
                    fetched += 1
                await asyncio.sleep(self.candle_fetch_sleep)
            mode = "db-restore+repair"
        else:
            # First boot: full REST backfill (legacy behavior).
            for sym in self.p.all_symbols:
                if await asyncio.to_thread(
                        self._refill_symbol, sym, self._days_for(sym)):
                    fetched += 1
                await asyncio.sleep(self.candle_fetch_sleep)
            mode = "full-rest"

        log.info("Backfill (%s): %d restored from DB, %d REST-refreshed in %.0fs",
                 mode, restored, fetched, time.time() - t0)
        self.db.log_event("BACKFILL", None, {
            "mode": mode, "restored_db": restored, "fetched_rest": fetched,
            "total": len(self.p.all_symbols),
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

    async def _safe_repair(self, reason: str):
        """Wrapper pour les lancements fire-and-forget (create_task) — sans
        lui, une exception du repair serait silencieusement perdue."""
        try:
            await self.repair_gaps(reason)
        except Exception:
            log.exception("repair_gaps(%s) failed", reason)

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

    def _ingest_candle(self, c: dict) -> list[tuple]:
        """MAJ mémoire (rapide, sur l'event loop). Retourne les opérations de
        persistance DB [(sym, rows, closed), …] à flusher HORS event loop par
        l'appelant — vide pour les updates in-progress, non-vide aux rolls."""
        sym = c.get("s")
        st = self.states.get(sym)
        if st is None:
            return []
        try:
            row = {"t": int(c["t"]), "o": float(c["o"]), "c": float(c["c"]),
                   "h": float(c["h"]), "l": float(c["l"]),
                   "v": float(c.get("v", 0))}
        except (KeyError, TypeError, ValueError):
            return []
        pending: list[tuple] = []
        if st.candles_4h and st.candles_4h[-1]["t"] == row["t"]:
            st.candles_4h[-1] = row          # in-progress candle update
        elif not st.candles_4h or row["t"] > st.candles_4h[-1]["t"]:
            # A1 — period roll: the previous in-memory bar just became final.
            # Persist it closed=1 (immutable) + the new bar closed=0. The
            # in-progress refinements between rolls are persisted by the
            # hourly loop (persist_in_progress) — a crash in between is
            # repaired at boot by the REST gap repair.
            # Les écritures DB sont RETOURNÉES (pas exécutées ici) pour être
            # flushées hors de l'event loop : au close 4h ~34 symboles rollent
            # en quelques secondes et un commit SQLite synchrone bloquerait
            # l'ingestion WS pile au moment des entrées (peer-review 2026-06-14).
            if st.candles_4h:
                pending.append((sym, [st.candles_4h[-1]], True))
            st.candles_4h.append(row)        # new period opened
            pending.append((sym, [row], False))
        # older-than-last updates are ignored (REST backfill owns history)
        st.last_candle_ts = row["t"]
        self.ws_candle_updates += 1
        return pending

    def _persist_batch(self, pending: list[tuple]):
        """Flush des persists de candle hors de l'event loop (appelé via
        asyncio.to_thread depuis ws_loop). Chaque item = (sym, rows, closed)."""
        for sym, rows, closed in pending:
            self._persist_candles(sym, rows, closed)

    def _persist_candles(self, sym: str, rows: list[dict], closed: bool):
        """A1 — upsert in-memory format candles ({t,o,h,l,c,v}) to the DB,
        deriving close_t (the in-memory rows don't carry HL's 'T')."""
        self.db.upsert_candles(sym, [
            {**r, "T": r.get("T", r["t"] + PERIOD_MS - 1)} for r in rows
        ], closed=closed)

    def persist_in_progress(self):
        """A1 — hourly checkpoint of every symbol's in-progress bar (and a
        safety re-upsert of the latest closed one)."""
        now_ms = int(time.time() * 1000)
        for sym, st in self.states.items():
            if not st.candles_4h:
                continue
            last = st.candles_4h[-1]
            is_closed = last["t"] + PERIOD_MS <= now_ms
            self._persist_candles(sym, [last], closed=is_closed)
            if len(st.candles_4h) >= 2:
                self._persist_candles(sym, [st.candles_4h[-2]], closed=True)

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
                            self._safe_repair("ws_reconnect"))
                    attempt = 0
                    async for raw in ws:
                        msg = orjson.loads(raw)
                        channel = msg.get("channel")
                        if channel == "candle":
                            data = msg.get("data")
                            pend: list[tuple] = []
                            if isinstance(data, list):
                                for c in data:
                                    pend += self._ingest_candle(c)
                            elif isinstance(data, dict):
                                pend = self._ingest_candle(data)
                            # commits SQLite hors event loop (rolls uniquement)
                            if pend:
                                await asyncio.to_thread(self._persist_batch, pend)
                        elif channel == "trades":
                            self.flow.ingest(msg["data"])
                # `async for` se termine SANS exception quand le serveur
                # ferme proprement (HL recycle ses connexions ~3h) — il faut
                # repasser par la même comptabilité que le chemin d'erreur
                # (event WS_RECONNECT + gap repair au reconnect suivant).
                self.ws_connected = False
                if self.running:
                    log.info("WS closed by server — reconnecting")
                    attempt = max(attempt, 1)
                    await asyncio.sleep(1)
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
            capitulation=dict(self._capitulation),
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
                # A1 — checkpoint the in-progress bars (crash window between
                # two period rolls is then covered by DB + boot gap repair).
                await asyncio.to_thread(self.persist_in_progress)
                await self.audit_candles()
                n_funding = await asyncio.to_thread(
                    sync_funding_hourly, self.db, self.p.all_symbols)
                if n_funding > len(self.p.all_symbols) * 3:
                    # backfill inhabituel (boot après downtime) — trace event
                    self.db.log_event("FUNDING_SYNC", None, {"rows": n_funding})
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
