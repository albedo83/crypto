"""MultiSignalBot — thin orchestrator holding state + wiring modules together.

The class holds all mutable state (positions, trades, feature cache, locks).
Computation logic lives in features.py, signals.py, trading.py, etc.
Wrapper methods translate between module-level functions and bot state.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

from .config import (
    VERSION, EXECUTION_MODE, HL_PRIVATE_KEY, CAPITAL_USDT,
    TRADE_SYMBOLS, ALL_SYMBOLS, SCAN_INTERVAL, TICKS_DB, STATE_FILE, MARKET_CSV,
)
from .models import SymbolState, Position, Trade
from . import features, signals, db as db_mod, net, persistence, trading, web

log = logging.getLogger("multisignal")


class MultiSignalBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s: SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        self.trades: deque[Trade] = deque(maxlen=500)
        self.running = False
        self._paused = False
        self._feature_cache: dict[str, dict | None] = {}
        self._oi_summary: dict = {"falling": 0, "rising": 0}
        self._dxy_cache: tuple[float, float] = (0.0, 0.0)
        self._shutdown_event: asyncio.Event | None = None
        self.started_at: datetime | None = None
        self._capital = CAPITAL_USDT  # mutable — adjusted by DCA injections
        self._total_pnl = 0.0
        self._wins = 0
        self._peak_balance = CAPITAL_USDT
        self._last_scan: float = 0
        self._last_price_fetch: float = 0
        self._last_daily_report: float = 0
        self._cooldowns: dict[str, float] = {}
        self._degraded: list[str] = []
        self._consecutive_losses = 0
        self._signal_first_seen: dict[str, float] = {}
        self._loss_streak_until: float = 0
        self._pos_lock = threading.Lock()
        self._failed_closes: set[str] = set()  # symbols with exchange close failures
        self._exchange_account: dict | None = None  # real exchange balance (live only)

        # SQLite tick database
        self._db = db_mod.init_db(TICKS_DB)

        # Live exchange (lazy — only when HL_MODE=live)
        self._exchange = None
        self._hl_info = None
        self._hl_address = ""
        self._sz_decimals: dict[str, int] = {}
        if EXECUTION_MODE == "live":
            from .exchange import init_exchange
            self._exchange, self._hl_info, self._hl_address, self._sz_decimals = \
                init_exchange(HL_PRIVATE_KEY)

    # ── Feature wrappers (called by web.py response builders) ──

    def _compute_features(self, sym: str) -> dict | None:
        st = self.states.get(sym)
        if not st or len(st.candles_4h) < 50:
            return None
        return features.compute_features(list(st.candles_4h))

    def _get_cached_features(self, sym: str) -> dict | None:
        return self._feature_cache.get(sym)

    def _compute_btc_features(self) -> dict:
        btc = self.states.get("BTC")
        if not btc or len(btc.candles_4h) < 50:
            return {}
        return features.compute_btc_features(list(btc.candles_4h))

    def _compute_alt_index(self) -> float:
        return features.compute_alt_index(self._feature_cache)

    def _compute_oi_features(self, sym: str) -> dict:
        st = self.states.get(sym)
        if not st:
            return {"oi_delta_1h": 0.0, "oi_delta_4h": 0.0, "funding_bps": 0.0}
        return features.compute_oi_features(list(st.oi_history), st.funding)

    def _compute_crowding_score(self, sym: str, oi_f: dict | None = None) -> int:
        st = self.states.get(sym)
        if not st:
            return 0
        if oi_f is None:
            oi_f = self._compute_oi_features(sym)
        f = self._feature_cache.get(sym)
        return features.compute_crowding_score(
            st.funding, st.premium, oi_f["oi_delta_1h"],
            f.get("vol_z", 0) if f else None)

    def _compute_sector_divergence(self, sym: str) -> dict | None:
        return features.compute_sector_divergence(sym, self._feature_cache)

    def _detect_squeeze(self, sym: str) -> dict | None:
        st = self.states.get(sym)
        if not st or len(st.candles_4h) < 8:
            return None
        f = self._feature_cache.get(sym)
        vr = f.get("vol_ratio", 2) if f else 2
        return signals.detect_squeeze(list(st.candles_4h), vr)

    def _close_position(self, sym, exit_price, now, reason):
        """Wrapper for web.py pause/reset handlers."""
        trading.close_position(sym, exit_price, now, reason, self)

    # ── Core bot operations ──────────────────────────────────

    def _refresh_feature_cache(self):
        # Include ETH for observation (signals logged but not traded)
        self._feature_cache = {sym: self._compute_features(sym) for sym in TRADE_SYMBOLS + ["ETH"]}
        falling = rising = 0
        for sym in TRADE_SYMBOLS:
            d = self._compute_oi_features(sym)["oi_delta_1h"]
            if d < -0.5:
                falling += 1
            elif d > 0.5:
                rising += 1
        self._oi_summary = {"falling": falling, "rising": rising}

    def _save_state(self):
        persistence.save_state(
            STATE_FILE, self.positions, self._pos_lock,
            self._total_pnl, self._wins, self._peak_balance,
            self._last_daily_report, self._paused,
            self._consecutive_losses, self._loss_streak_until,
            self._cooldowns, self._signal_first_seen, self._feature_cache,
            capital=self._capital)

    def _scan_and_trade(self) -> int:
        """Detect signals across all tokens, then rank and enter positions."""
        now = datetime.now(timezone.utc)
        btc_f = self._compute_btc_features()
        alt_index = self._compute_alt_index()
        dxy_7d = features.fetch_dxy(self._degraded, features.DXY_CACHE)
        self._dxy_cache = (dxy_7d, time.time())

        cross_ctx = signals.compute_cross_context(self._feature_cache)
        all_signals = []

        for sym in TRADE_SYMBOLS:
            if sym in self.positions or (sym in self._cooldowns and time.time() < self._cooldowns[sym]):
                continue
            f = self._feature_cache.get(sym) or self._compute_features(sym)
            if not f:
                continue
            st = self.states.get(sym)
            if not st or st.price == 0:
                continue

            oi_f = self._compute_oi_features(sym)
            crowd = self._compute_crowding_score(sym, oi_f=oi_f)
            sd = self._compute_sector_divergence(sym)
            sq = self._detect_squeeze(sym)

            # Build observation context string + structured entry context
            sym_sector = features.TOKEN_SECTOR.get(sym, "?")
            sect_stress = cross_ctx["stress_by_sector"].get(sym_sector, 0)
            dd = abs(f.get("drawdown", 0))
            shock = round(abs(f.get("ret_24h", 0)) / dd, 2) if dd > 100 else 0
            r24 = abs(f.get("ret_24h", 0))
            clean = round(f.get("range_pct", 0) / r24, 2) if r24 > 50 else 0
            _sect = features.TOKEN_SECTOR.get(sym)
            _peers = [self._feature_cache.get(p) for p in features.SECTORS.get(_sect, []) if p != sym] if _sect else []
            _peer_rets = [abs(pf.get("ret_42h", 0)) for pf in _peers if pf]
            _peer_avg = np.mean(_peer_rets) if _peer_rets else 0
            lead = round(abs(f.get("ret_42h", 0)) / _peer_avg, 1) if _peer_avg > 100 else 0
            conf = sum([
                abs(f.get("drawdown", 0)) > 3000, f.get("vol_z", 0) > 1.5,
                abs(f.get("ret_24h", 0)) > 200, cross_ctx["n_stress_global"] >= 5,
                oi_f["oi_delta_1h"] < -1.0,
            ])
            _h = now.hour
            _session = "Asia" if _h < 8 else "EU" if _h < 14 else "US" if _h < 21 else "Night"
            if now.weekday() >= 5:
                _session = "WE"
            oi_tag = (f" OI1h={oi_f['oi_delta_1h']:+.1f}% CS={crowd}"
                      f" str={cross_ctx['n_stress_global']}/{sect_stress}"
                      f" disp={cross_ctx['disp_24h']:.0f}/{cross_ctx['disp_7d']:.0f}"
                      f" shk={shock:.2f} cln={clean:.1f} lead={lead:.1f}"
                      f" conf={conf} ses={_session}")
            entry_ctx = {"oi_delta": oi_f["oi_delta_1h"], "crowding": crowd,
                         "confluence": conf, "session": _session}

            token_sigs = signals.detect_token_signals(
                sym, f, btc_f, sd, sq, oi_tag, entry_ctx)
            all_signals.extend(token_sigs)

            # S9-fast observation
            s9f = signals.check_s9f_observation(st.price_ticks, st.price)
            if s9f:
                log.info("S9F_OBS: %s %s ret_2h=%+.0f (observation only)", s9f["dir"], sym, s9f["ret_2h"])
                db_mod.log_event(self._db, "S9F_OBS", sym, s9f)

        # ETH observation — log signals but don't trade (not enough data to validate)
        eth_f = self._feature_cache.get("ETH")
        if eth_f:
            eth_st = self.states.get("ETH")
            btc_7d = btc_f.get("btc_7d", 0)
            # S8 on ETH (4/4 wins in backtest, +2089 bps avg)
            if (eth_f.get("drawdown", 0) < -4000 and eth_f.get("vol_z", 0) > 1.0
                    and eth_f.get("ret_24h", 0) < -50 and btc_7d < -300):
                log.info("ETH_OBS: S8 LONG dd=%.0f vz=%.1f r24h=%.0f BTC7d=%+.0f",
                         eth_f["drawdown"], eth_f["vol_z"], eth_f["ret_24h"], btc_7d)
                db_mod.log_event(self._db, "ETH_OBS", "ETH",
                                 {"signal": "S8", "dir": "LONG", "drawdown": eth_f["drawdown"],
                                  "ret_24h": eth_f["ret_24h"], "btc_7d": btc_7d})
            # S9 on ETH (0/3 wins — observation only, do NOT trade)
            if abs(eth_f.get("ret_24h", 0)) >= 2000:
                s9_dir = "SHORT" if eth_f["ret_24h"] > 0 else "LONG"
                log.info("ETH_OBS: S9 %s ret_24h=%+.0f (loses on ETH — observation only)",
                         s9_dir, eth_f["ret_24h"])
                db_mod.log_event(self._db, "ETH_OBS", "ETH",
                                 {"signal": "S9", "dir": s9_dir, "ret_24h": eth_f["ret_24h"]})
            # S9-fast on ETH
            if eth_st and len(eth_st.price_ticks) >= 120:
                s9f = signals.check_s9f_observation(eth_st.price_ticks, eth_st.price)
                if s9f:
                    log.info("S9F_OBS: %s ETH ret_2h=%+.0f (observation only)", s9f["dir"], s9f["ret_2h"])
                    db_mod.log_event(self._db, "S9F_OBS", "ETH", s9f)

        signals.track_signal_age(all_signals, self._signal_first_seen, time.time())
        return trading.rank_and_enter(all_signals, now, self)

    async def main_loop(self):
        """Two cadences: prices every 60s (for stop checks), full scan every hour."""
        while self.running:
            try:
                now = time.time()
                meta_u, ctxs = await asyncio.to_thread(net.fetch_prices, self.states)
                self._last_price_fetch = time.time()
                if meta_u and ctxs:
                    db_mod.log_ticks(self._db, ctxs, meta_u)
                # Fetch real exchange balance (live mode)
                if self._exchange:
                    from .exchange import fetch_account_state
                    acct = await asyncio.to_thread(fetch_account_state, self._hl_info, self._hl_address)
                    if acct:
                        self._exchange_account = acct

                if now - self._last_scan >= SCAN_INTERVAL:
                    log.info("Scanning signals...")
                    for sym in ALL_SYMBOLS:
                        await asyncio.to_thread(net.fetch_candles, sym, self.states)
                        await asyncio.sleep(0.2)

                    self._refresh_feature_cache()
                    if self._exchange:
                        from .exchange import reconcile
                        with self._pos_lock:
                            pos_snapshot = dict(self.positions)
                        await asyncio.to_thread(reconcile, self._hl_info, self._hl_address,
                                                pos_snapshot, net.send_telegram)

                    exits = await asyncio.to_thread(trading.check_exits, self)
                    if exits:
                        self._save_state()
                    if not self._paused:
                        n_new = await asyncio.to_thread(self._scan_and_trade)
                        if n_new:
                            log.info("Opened %d new positions", n_new)
                            self._save_state()  # persist immediately to avoid orphans on crash

                    self._last_scan = now
                    self._save_state()
                    persistence.log_market_snapshot(
                        self.states, self._feature_cache, TRADE_SYMBOLS, MARKET_CSV,
                        self._db, self._compute_oi_features, self._compute_crowding_score)

                    _bt = [t for t in self.trades if trading.is_bot_trade(t)]
                    n = len(_bt)
                    balance = self._capital + self._total_pnl
                    wr = sum(1 for t in _bt if t.pnl_usdt > 0) / n * 100 if n > 0 else 0
                    btc_f = self._compute_btc_features()
                    alt_idx = self._compute_alt_index()
                    log.info("Status: %d pos | $%.0f | %d trades (%.0f%%) | BTC30d=%+.0f | AltIdx=%+.0f",
                             len(self.positions), balance, n, wr,
                             btc_f.get("btc_30d", 0), alt_idx)

                    _utc_h = datetime.now(timezone.utc).hour
                    if _utc_h == 0 and now - self._last_daily_report > 43200:
                        msg = web.build_daily_summary(self)
                        net.send_telegram(msg)
                        self._last_daily_report = time.time()
                else:
                    exits = await asyncio.to_thread(trading.check_exits, self)
                    if exits:
                        self._save_state()
            except Exception:
                log.exception("Loop error")
            await asyncio.sleep(60)
