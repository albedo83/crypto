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
    VERSION, EXECUTION_MODE, HL_PRIVATE_KEY, HL_ACCOUNT_ADDRESS, CAPITAL_USDT,
    TRADE_SYMBOLS, ALL_SYMBOLS, SCAN_INTERVAL, TICKS_DB, STATE_FILE,
    DISP_GATE_BPS, DISP_GATE_STRATEGIES,
    MACRO_LOOKBACK_DAYS, MACRO_Z_WINDOW_DAYS,
    GIVEBACK_ALERT_STRATEGIES, GIVEBACK_ALERT_MFE_MIN_BPS,
    GIVEBACK_ALERT_CUR_MAX_BPS, GIVEBACK_ALERT_TIME_SINCE_MFE_MIN_H,
    LOCK_FLOOR_ALERT_STRATEGIES, LOCK_FLOOR_ALERT_MIN_USD,
    LOCK_FLOOR_ALERT_MIN_BPS, LOCK_FLOOR_ALERT_MIN_HOLD_H,
    LOCK_FLOOR_ALERT_BUFFER_USD,
    REGIME_ALERT_DISP_7D_BPS, REGIME_ALERT_WR_PCT, REGIME_ALERT_LOOKBACK,
    REGIME_ALERT_COOLDOWN_H, REGIME_ALERT_STRATEGY, REGIME_ALERT_DIRECTION,
)
from .models import SymbolState, Position, Trade
from . import analytics, features, signals, db as db_mod, net, persistence, trading, web

log = logging.getLogger("multisignal")


class MultiSignalBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s: SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        # Bumped from 500 → 5000 in v11.6.2: "lifetime" strategy stats
        # (compute_signal_drift) read this deque, and beyond 500 trades the
        # oldest would drop silently → wrong lifetime WR/P&L.
        self.trades: deque[Trade] = deque(maxlen=5000)
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
        self._pos_lock = threading.Lock()
        self._failed_closes: set[str] = set()  # symbols with exchange close failures
        self._closing: set[str] = set()  # symbols currently mid-close (mutex)
        self._exchange_account: dict | None = None  # real exchange balance (live only)
        self._drift_alerted: bool = False  # one-shot alert for bot vs exchange P&L drift
        self._btc_z: float | None = None  # rolling z-score of BTC ret_30d (adaptive modulator)
        self._wr_alerted: set[str] = set()  # v12.4.0 — symbols already alerted on WR drop
        self._giveback_alerted: set[str] = set()  # v12.7.2 — symbols already alerted on giveback pattern
        self._lock_floor_alerted: set[str] = set()  # v12.7.2 — symbols already alerted on lock-floor opportunity
        self._regime_alert_last_ts: float = 0.0  # v12.7.14 — cooldown ts for regime alert (in-memory only)
        self._basket_metrics: dict | None = None  # observation-only basket correlation
        # v12.9.0: gate entry-signal scans to 4h candle close boundaries to
        # match the BT engine's granularity. See _scan_and_trade for details.
        self._last_entry_scan_4h_close: int = 0
        # v12.9.2: optional fees-tracking start ts (seconds since epoch).
        # 0 = use the default 90d rolling window (v11.3.7 behavior).
        # Set to a unix ts (e.g. via a soft reset) to scope the "Exchange fees"
        # dashboard card to a custom period.
        self._fees_track_start_ts: float = 0.0
        # v12.10.1: optional strategy-performance tracking start ts.
        # 0 = lifetime (all bot trades). Set to a unix ts to scope the
        # "Strategy Performance" dashboard section (compute_signal_drift)
        # to trades with entry_time >= start. Used after a soft reset to
        # show fresh perf tracking while keeping DB history for audit.
        self._perf_track_start_ts: float = 0.0

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
                init_exchange(HL_PRIVATE_KEY, HL_ACCOUNT_ADDRESS)

    # ── Feature wrappers (called by web.py response builders) ──

    @staticmethod
    def _closed_candles(candles_iter) -> list:
        """Return only fully-closed 4h candles (drops the in-progress one).

        v12.11.2: BT computes features at candle close exact; live had been
        using the in-progress candle's current price as closes[-1], which
        drifted from BT's reference price by up to 3 minutes of price action.
        Filtering keeps live and BT signal-state semantics identical.
        """
        now_ms = int(time.time() * 1000)
        return [c for c in candles_iter if c["t"] + 14400000 <= now_ms]

    def _compute_features(self, sym: str) -> dict | None:
        st = self.states.get(sym)
        if not st or len(st.candles_4h) < 50:
            return None
        candles = self._closed_candles(st.candles_4h)
        if len(candles) < 50:
            return None
        return features.compute_features(candles)

    def _get_cached_features(self, sym: str) -> dict | None:
        return self._feature_cache.get(sym)

    def _compute_btc_features(self) -> dict:
        btc = self.states.get("BTC")
        if not btc or len(btc.candles_4h) < 50:
            return {}
        # v12.12.1: closed-candles only for BT-equivalent semantics
        candles = self._closed_candles(btc.candles_4h)
        if len(candles) < 50:
            return {}
        return features.compute_btc_features(candles)

    def _compute_alt_index(self) -> float:
        return features.compute_alt_index(self._feature_cache)

    def _check_wr_alerts(self) -> None:
        """v12.4.0 — emit Telegram alert when an open position's estimated WR
        drops into the alarm zone (< 25%) for the first time.

        Maturity gate already inside estimate_win_prob; we only alert when the
        position has had time to develop a meaningful pattern (else we'd spam
        on early MAE noise). Cleared automatically when the position closes.
        """
        # Drop alerts for symbols no longer open
        with self._pos_lock:
            self._wr_alerted &= set(self.positions.keys())
            positions = list(self.positions.items())
        if not positions or not self.trades:
            return
        trades_list = list(self.trades)
        now = datetime.now(timezone.utc)
        for sym, pos in positions:
            if sym in self._wr_alerted:
                continue
            hold_h = (now - pos.entry_time).total_seconds() / 3600
            hold_target = max(1.0, (pos.target_exit - pos.entry_time).total_seconds() / 3600)
            # Compute live pnl from current price for the alert message
            st = self.states.get(sym)
            cur_pnl = 0.0
            ur_bps = 0.0
            if st and st.price > 0 and pos.entry_price > 0:
                ur_bps = (st.price / pos.entry_price - 1) * 1e4 * pos.direction
                cur_pnl = pos.size_usdt * ur_bps / 1e4
            wp = analytics.estimate_win_prob(pos, trades_list,
                                              hours_held=hold_h,
                                              hold_target_h=hold_target,
                                              current_ur_bps=ur_bps)
            if not wp or not wp.get("mature") or wp.get("wr_pct", 100) >= 25:
                continue
            # v12.5.4 — suppress the alarm when the position is currently up
            # OR has already shown strong mean-reversion pulse. Historical WR
            # < 25 % can be a tiny-sample artefact (n=3 all losers etc); if
            # the live trade is profitable right now there is nothing to act on.
            if cur_pnl > 0 or pos.mfe_bps >= 500:
                continue
            side = "LONG" if pos.direction == 1 else "SHORT"
            msg = (f"🚨 {sym} {pos.strategy} {side}: WR drift to {wp['wr_pct']}% "
                   f"(base {wp['base_wr_pct']}%, n={wp['n']} {wp['scope']})\n"
                   f"  pnl=${cur_pnl:+.2f} | MAE={int(pos.mae_bps)} | held {hold_h:.1f}h\n"
                   f"  Consider manual close.")
            net.send_telegram(msg, category="trade", actionable=True)
            db_mod.log_event(self._db, "WR_ALERT", sym, {
                "strategy": pos.strategy, "dir": side,
                "wr_pct": wp["wr_pct"], "base_wr_pct": wp["base_wr_pct"],
                "n": wp["n"], "scope": wp["scope"], "note": wp["note"],
                "hold_h": round(hold_h, 1),
            })
            self._wr_alerted.add(sym)

    def _check_giveback_alerts(self) -> None:
        """v12.7.2 — Telegram alert when an open position shows the
        "giveback through middle" pattern (had real upside, now in the red,
        sustained). NO trading action — purely informational.

        Mechanical exits on this pattern fail walk-forward across 4 R&Ds.
        But the user's manual_close on this pattern wins (+$28 net over 6
        trades in April-May 2026). This alert helps the user spot the
        moment without having to watch the screen.

        Dedup: once per position, cleared automatically on close.
        """
        if not GIVEBACK_ALERT_STRATEGIES:
            return
        with self._pos_lock:
            self._giveback_alerted &= set(self.positions.keys())
            positions = list(self.positions.items())
        if not positions:
            return
        now = datetime.now(timezone.utc)
        for sym, pos in positions:
            if sym in self._giveback_alerted:
                continue
            if pos.strategy not in GIVEBACK_ALERT_STRATEGIES:
                continue
            st = self.states.get(sym)
            if not st or st.price <= 0 or pos.entry_price <= 0:
                continue
            ur_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
            if pos.mfe_bps < GIVEBACK_ALERT_MFE_MIN_BPS:
                continue
            if ur_bps > GIVEBACK_ALERT_CUR_MAX_BPS:
                continue
            hold_h = (now - pos.entry_time).total_seconds() / 3600
            t_since_mfe = hold_h - pos.mfe_at_h
            if t_since_mfe < GIVEBACK_ALERT_TIME_SINCE_MFE_MIN_H:
                continue
            cur_pnl = pos.size_usdt * ur_bps / 1e4
            mae_pnl = pos.size_usdt * pos.mae_bps / 1e4
            mfe_pnl = pos.size_usdt * pos.mfe_bps / 1e4
            side = "LONG" if pos.direction == 1 else "SHORT"
            retracement_pct = (pos.mfe_bps - ur_bps) / pos.mfe_bps * 100 if pos.mfe_bps > 0 else 0
            msg = (f"🪤 GIVEBACK {sym} {pos.strategy} {side}: "
                   f"MFE peaked ${mfe_pnl:+.2f} ({pos.mfe_bps:+.0f}bps) "
                   f"{t_since_mfe:.1f}h ago, now ${cur_pnl:+.2f} "
                   f"({ur_bps:+.0f}bps) — retraced {retracement_pct:.0f}%. "
                   f"MAE ${mae_pnl:.2f}. Consider manual_close.")
            net.send_telegram(msg, category="trade", actionable=True)
            db_mod.log_event(self._db, "GIVEBACK_ALERT", sym, {
                "strategy": pos.strategy, "dir": side,
                "mfe_bps": round(pos.mfe_bps, 1),
                "cur_bps": round(ur_bps, 1),
                "t_since_mfe_h": round(t_since_mfe, 1),
                "cur_pnl": round(cur_pnl, 2),
                "mfe_pnl": round(mfe_pnl, 2),
            })
            self._giveback_alerted.add(sym)

    def _check_lock_floor_alerts(self) -> None:
        """v12.7.2 — Telegram alert when an open position has accumulated
        substantial unrealized profit, suggesting the user might want to
        set a manual_stop_usdt floor proactively. NO trading action.

        Suggested floor = current_pnl - LOCK_FLOOR_ALERT_BUFFER_USD
        (typically $5 buffer). User can pick differently via the dashboard
        🎯 button or POST /api/manual_stop/{symbol}.

        Dedup: once per position. Resets if user clears manual_stop_usdt.
        """
        if not LOCK_FLOOR_ALERT_STRATEGIES:
            return
        with self._pos_lock:
            self._lock_floor_alerted &= set(self.positions.keys())
            positions = list(self.positions.items())
        if not positions:
            return
        now = datetime.now(timezone.utc)
        for sym, pos in positions:
            if sym in self._lock_floor_alerted:
                continue
            if pos.strategy not in LOCK_FLOOR_ALERT_STRATEGIES:
                continue
            # Skip if user already set a manual_stop — they're aware
            if pos.manual_stop_usdt is not None:
                continue
            st = self.states.get(sym)
            if not st or st.price <= 0 or pos.entry_price <= 0:
                continue
            hold_h = (now - pos.entry_time).total_seconds() / 3600
            if hold_h < LOCK_FLOOR_ALERT_MIN_HOLD_H:
                continue
            ur_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
            cur_pnl = pos.size_usdt * ur_bps / 1e4
            # Substantial profit: $ floor OR bps floor
            if cur_pnl < LOCK_FLOOR_ALERT_MIN_USD and ur_bps < LOCK_FLOOR_ALERT_MIN_BPS:
                continue
            side = "LONG" if pos.direction == 1 else "SHORT"
            # Suggested floor — round down to $1 for clean number
            suggested = max(0.0, round(cur_pnl - LOCK_FLOOR_ALERT_BUFFER_USD))
            msg = (f"🎯 LOCK_FLOOR {sym} {pos.strategy} {side}: "
                   f"unrealized ${cur_pnl:+.2f} ({ur_bps:+.0f}bps) after "
                   f"{hold_h:.1f}h. Consider manual_stop @ ${suggested:.0f} "
                   f"to protect ~${suggested:.0f} of the gain.")
            net.send_telegram(msg, category="trade", actionable=True)
            db_mod.log_event(self._db, "LOCK_FLOOR_ALERT", sym, {
                "strategy": pos.strategy, "dir": side,
                "cur_bps": round(ur_bps, 1),
                "cur_pnl": round(cur_pnl, 2),
                "suggested_floor": suggested,
                "hold_h": round(hold_h, 1),
            })
            self._lock_floor_alerted.add(sym)

    def _check_regime_alert(self, cross_ctx: dict) -> None:
        """v12.7.14 — observation-only Telegram when (a) cross-sectional 7d
        dispersion crosses REGIME_ALERT_DISP_7D_BPS AND (b) the last N
        closed (strategy, direction) trades show WR below
        REGIME_ALERT_WR_PCT. No trading action — purely informational so
        the user can decide to pause the (S5, LONG) bucket manually.

        Motivated by 2026-05 EDA showing S5 LONG losers concentrated above
        disp_7d=700 in current regime. Both hard-gate and soft-haircut
        walk-forward FAIL (regime-local signal, anti-robust). Alert lets
        the user catch regime shift without acting on a non-validated rule.

        Cooldown REGIME_ALERT_COOLDOWN_H hours, in-memory only (restart
        clears it — acceptable given restart cadence).
        """
        if REGIME_ALERT_DISP_7D_BPS >= 99000:
            return  # kill-switch
        disp_7d = cross_ctx.get("disp_7d")
        if disp_7d is None or disp_7d < REGIME_ALERT_DISP_7D_BPS:
            return
        # Cooldown
        now_ts = time.time()
        if now_ts - self._regime_alert_last_ts < REGIME_ALERT_COOLDOWN_H * 3600:
            return
        # Rolling WR on last N (strategy, direction) closed trades
        side_str = "LONG" if REGIME_ALERT_DIRECTION == 1 else "SHORT"
        recent = [t for t in reversed(self.trades)
                  if t.strategy == REGIME_ALERT_STRATEGY and t.direction == side_str]
        recent = recent[:REGIME_ALERT_LOOKBACK]
        if len(recent) < REGIME_ALERT_LOOKBACK:
            return  # not enough data
        wins = sum(1 for t in recent if t.pnl_usdt > 0)
        wr_pct = wins / len(recent) * 100
        if wr_pct >= REGIME_ALERT_WR_PCT:
            return
        sum_pnl = sum(t.pnl_usdt for t in recent)
        msg = (f"🌪️ Regime alert: {REGIME_ALERT_STRATEGY} {side_str} struggling\n"
               f"  disp_7d={disp_7d:.0f} bps (≥{REGIME_ALERT_DISP_7D_BPS:.0f}) "
               f"+ recent WR={wr_pct:.0f}% on last {len(recent)} "
               f"({sum_pnl:+.2f} $)\n"
               f"  Consider pausing {REGIME_ALERT_STRATEGY} {side_str} manually "
               f"if pattern persists. Cooldown {REGIME_ALERT_COOLDOWN_H}h.")
        net.send_telegram(msg, category="trade", actionable=True)
        db_mod.log_event(self._db, "REGIME_ALERT", None, {
            "strategy": REGIME_ALERT_STRATEGY, "dir": side_str,
            "disp_7d": round(disp_7d, 0),
            "wr_pct": round(wr_pct, 0),
            "n_recent": len(recent),
            "sum_pnl": round(sum_pnl, 2),
        })
        self._regime_alert_last_ts = now_ts

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
        # v12.11.2: closed-only candles for BT-equivalent semantics
        candles = self._closed_candles(st.candles_4h)
        if len(candles) < 8:
            return None
        return signals.detect_squeeze(candles, vr)

    def _close_position(self, sym, exit_price, now, reason):
        """Wrapper for web.py pause/reset handlers."""
        trading.close_position(sym, exit_price, now, reason, self)

    def close_and_check(self, sym: str, exit_price: float, now, reason: str) -> bool:
        """Close a position and report success via the canonical _failed_closes
        signal. Returns True iff the exchange close succeeded (live mode) or
        the position was closed in memory (paper mode).

        Single source of truth for the "did the close work?" check, used by
        api_close_symbol, api_pause, check_exits retries. See trading.close_position
        for the close mechanics and the _closing mutex that guards re-entry.
        """
        trading.close_position(sym, exit_price, now, reason, self)
        return sym not in self._failed_closes

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
            self._consecutive_losses,
            self._cooldowns, self._signal_first_seen, self._feature_cache,
            capital=self._capital,
            pnl_realign_offset=getattr(self, "_pnl_realign_offset", 0.0),
            last_entry_scan_4h_close=self._last_entry_scan_4h_close,
            fees_track_start_ts=self._fees_track_start_ts,
            perf_track_start_ts=self._perf_track_start_ts,
            btc_z=self._btc_z)

    def _build_token_signals(self, now, btc_f: dict, cross_ctx: dict) -> list:
        """Per-token signal detection loop. Returns the list of fired token-level
        signals for this scan (S5/S8/S9/S10). Applies dispersion gate inline
        and logs S9F_OBS events. Macro signals (S1) are added separately by
        trading.rank_and_enter — this only builds token-level candidates.
        """
        all_signals: list = []
        # v12.10.4 — log scan-stage skips (cooldown + already-in-position)
        # for observability. Previously these were silent `continue`s that made
        # it impossible to explain "why didn't the bot take WLD ?" from the DB.
        # Only fires once per 4h-boundary scan (gated by v12.9.0), so ~6 events
        # per skipped symbol per day max.
        for sym in TRADE_SYMBOLS:
            if sym in self.positions:
                db_mod.log_event(self._db, "SKIP", sym,
                                  {"reason": "already_in_position"})
                continue
            if sym in self._cooldowns and time.time() < self._cooldowns[sym]:
                db_mod.log_event(self._db, "SKIP", sym, {
                    "reason": "cooldown",
                    "expires_at": int(self._cooldowns[sym]),
                    "remaining_h": round((self._cooldowns[sym] - time.time()) / 3600, 2),
                })
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
            # int(...) cast: feature dict values come from numpy/pandas, so
            # `np.float > X` returns np.bool_ and sum(np.bool_, ...) returns
            # np.int64 — which SQLite stores as BLOB and JSON refuses to
            # serialize. Force native int at the source.
            conf = int(sum([
                abs(f.get("drawdown", 0)) > 3000, f.get("vol_z", 0) > 1.5,
                abs(f.get("ret_24h", 0)) > 200, cross_ctx["n_stress_global"] >= 5,
                oi_f["oi_delta_1h"] < -1.0,
            ]))
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
            # v11.7.28 dispersion gate: skip mean-reversion strats (S5/S9) when
            # cross-sectional dispersion is at p98+ extreme. Logs SKIP event so
            # the rule is auditable in events DB.
            if cross_ctx["disp_24h"] >= DISP_GATE_BPS:
                gated = [s for s in token_sigs if s["strategy"] in DISP_GATE_STRATEGIES]
                token_sigs = [s for s in token_sigs if s["strategy"] not in DISP_GATE_STRATEGIES]
                for s in gated:
                    db_mod.log_event(self._db, "SKIP", sym,
                                     {"strategy": s["strategy"],
                                      "dir": "LONG" if s["direction"] == 1 else "SHORT",
                                      "reason": "disp_gate",
                                      "disp_24h_bps": cross_ctx["disp_24h"]})
            all_signals.extend(token_sigs)

            # S9-fast observation
            s9f = signals.check_s9f_observation(st.price_ticks, st.price)
            if s9f:
                log.info("S9F_OBS: %s %s ret_2h=%+.0f (observation only)", s9f["dir"], sym, s9f["ret_2h"])
                db_mod.log_event(self._db, "S9F_OBS", sym, s9f)
        return all_signals

    def _log_eth_observations(self, btc_f: dict) -> None:
        """ETH is tracked but not traded. Log signal fires for future analysis."""
        eth_f = self._feature_cache.get("ETH")
        if not eth_f:
            return
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

    def _scan_and_trade(self) -> int:
        """Detect signals across all tokens, then rank and enter positions.

        Thin orchestrator: refresh macro context, build candidates, hand off
        to trading.rank_and_enter, then surface WR alerts. The heavy lifting
        lives in _build_token_signals and rank_and_enter.

        v12.9.0: entries are gated to 4h candle close boundaries (mirrors
        the backtest engine's scan granularity). Audit on 65d live deployment
        showed 75/119 entries fired intra-candle with cumulative −$257 PnL
        because the signal died before the next 4h close (mean adverse drift
        −105 bps). Exits, reconcile, MAE/MFE, and feature refresh keep their
        hourly cadence via SCAN_INTERVAL. Source: backtests/intracandle_signal_test.py.

        v12.9.5: alerts (WR / giveback / lock-floor / regime) are evaluated at
        every hourly scan, BEFORE the entry gate. Previously (v12.9.0–v12.9.4)
        they were placed after the gate, adding 0-3h latency to time-critical
        Telegram patterns. Identified by code-review v12.9.4.
        """
        cross_ctx = signals.compute_cross_context(self._feature_cache)
        # v12.9.5 — alerts always run at hourly cadence (not gated).
        try:
            self._check_wr_alerts()
        except Exception as e:
            log.warning("WR alert check failed: %s", e)
        try:
            self._check_giveback_alerts()
        except Exception as e:
            log.warning("Giveback alert check failed: %s", e)
        try:
            self._check_lock_floor_alerts()
        except Exception as e:
            log.warning("Lock-floor alert check failed: %s", e)
        try:
            self._check_regime_alert(cross_ctx)
        except Exception as e:
            log.warning("Regime alert check failed: %s", e)

        # v12.11.6: compute btc_z BEFORE the entry gate so exits (s8_inlife,
        # traj_cut, prop_trail) and adaptive modulator have a fresh value at
        # every scan, not just at 4h boundaries. Previously btc_z stayed None
        # for up to 4h after restart, silently disabling those features.
        now = datetime.now(timezone.utc)
        btc_f = self._compute_btc_features()
        btc = self.states.get("BTC")
        if btc and len(btc.candles_4h) >= 200:
            # v12.11.2: drop in-progress candle for BT-equivalent semantics
            btc_closed = self._closed_candles(btc.candles_4h)
            if len(btc_closed) >= 200:
                self._btc_z = features.compute_btc_z(
                    btc_closed,
                    lookback_days=MACRO_LOOKBACK_DAYS,
                    z_window_days=MACRO_Z_WINDOW_DAYS,
                )

        # 4h candle alignment gate — entries fire at most once per 4h period.
        now_ts = int(time.time())
        CANDLE_PERIOD_SEC = 14400  # 4h × 3600
        last_4h_close = (now_ts // CANDLE_PERIOD_SEC) * CANDLE_PERIOD_SEC
        if self._last_entry_scan_4h_close >= last_4h_close:
            return 0  # already evaluated entries for this 4h candle
        self._last_entry_scan_4h_close = last_4h_close
        # Basket correlation (observation-only)
        with self._pos_lock:
            pos_for_basket = dict(self.positions)
        self._basket_metrics = features.compute_basket_correlation(
            pos_for_basket, self.states)
        self._compute_alt_index()  # warms cache used by /api/state widgets
        dxy_7d = features.fetch_dxy(self._degraded, features.DXY_CACHE)
        self._dxy_cache = (dxy_7d, time.time())

        all_signals = self._build_token_signals(now, btc_f, cross_ctx)
        self._log_eth_observations(btc_f)

        signals.track_signal_age(all_signals, self._signal_first_seen, time.time())
        n_new = trading.rank_and_enter(all_signals, now, self)
        return n_new

    async def equity_refresh_loop(self):
        """v12.5.12 fast equity refresh (15s) for live mode only.

        Calls fetch_equity_only (cheap: 2 HL API calls, no fills/funding)
        and merges into self._exchange_account. Keeps the dashboard equity
        card fresh between the 60s main_loop ticks. Falls back gracefully
        if the call errors — main_loop's full refresh still runs at 60s.
        """
        from .exchange import fetch_equity_only
        while self.running:
            try:
                if self._exchange:
                    fast = await asyncio.to_thread(
                        fetch_equity_only, self._hl_info, self._hl_address)
                    if fast:
                        if self._exchange_account is None:
                            self._exchange_account = fast
                        else:
                            # Preserve slower diagnostic fields (taker_fees,
                            # funding_paid, closed_pnl) from the last full refresh.
                            self._exchange_account.update(fast)
            except Exception:
                log.exception("equity_refresh_loop error")
            await asyncio.sleep(10)

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
                    fees_start = int(self._fees_track_start_ts * 1000) if self._fees_track_start_ts else None
                    acct = await asyncio.to_thread(fetch_account_state, self._hl_info, self._hl_address, fees_start)
                    if acct:
                        self._exchange_account = acct
                        # Alert on significant drift between bot accounting and real equity.
                        # Bot P&L uses flat COST_BPS (which already accounts for round-trip
                        # taker fees) and real funding (per-trade since v11.7.5). Exchange
                        # truth = closed_pnl (excludes fees on HL) + funding_paid - taker_fees.
                        # Without the -taker_fees subtraction the comparator looked too lenient
                        # to the exchange side and produced spurious EQUITY_DRIFT warnings on
                        # otherwise-aligned books (v11.7.27 fix).
                        bot_realized = self._total_pnl
                        exch_realized = (acct.get("closed_pnl", 0)
                                         + acct.get("funding_paid", 0)
                                         - acct.get("taker_fees", 0))
                        drift = bot_realized - exch_realized
                        if abs(drift) > 5.0 and not self._drift_alerted:
                            log.warning("EQUITY DRIFT: bot realized $%.2f vs exchange $%.2f (Δ$%.2f)",
                                        bot_realized, exch_realized, drift)
                            db_mod.log_event(self._db, "EQUITY_DRIFT", None,
                                             {"bot_realized": round(bot_realized, 2),
                                              "exch_realized": round(exch_realized, 2),
                                              "drift": round(drift, 2)})
                            net.send_telegram(
                                f"⚠️ Equity drift: bot ${bot_realized:.2f} vs exchange "
                                f"${exch_realized:.2f} (Δ${drift:+.2f}) — check fees/funding",
                                category="reconcile", actionable=True)
                            self._drift_alerted = True
                        elif abs(drift) < 2.0:
                            self._drift_alerted = False  # reset when back in line

                # v12.10.3 — trigger a full scan loop within 3 min after each
                # 4h candle close to reduce live↔BT entry latency. Without
                # this, entries fire whenever the hourly scan happens to land
                # after the close (0-59 min jitter). With this, entries fire
                # within 3 min of the candle close — close to BT timing.
                # v12.10.12: grace bumped 60s → 180s so HL candle data + OI/
                # funding snapshots have more time to settle before the scan
                # evaluates signals at threshold-frontier conditions.
                # See backtests/intracandle_signal_test.py for the motivation.
                _now_t = time.time()
                _last_4h = (int(_now_t) // 14400) * 14400  # most recent 4h boundary
                _post_4h_close = (
                    _now_t - _last_4h >= 180         # 180s grace for HL data settle
                    and self._last_entry_scan_4h_close < _last_4h  # not yet scanned
                )
                if now - self._last_scan >= SCAN_INTERVAL or _post_4h_close:
                    log.info("Scanning signals... (trigger: %s)",
                             "4h-boundary" if _post_4h_close and now - self._last_scan < SCAN_INTERVAL
                             else "hourly")
                    for sym in ALL_SYMBOLS:
                        # BTC needs 210d for the v11.10.0 macro modulator
                        # (30d lookback + 180d rolling z-window). Others 45d.
                        days = 250 if sym == "BTC" else 45
                        await asyncio.to_thread(net.fetch_candles, sym, self.states, days)
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
                        self.states, self._feature_cache, TRADE_SYMBOLS,
                        self._db, self._compute_oi_features, self._compute_crowding_score)
                    persistence.log_basket_snapshot(self._basket_metrics, self._db)

                    _bt = [t for t in self.trades if analytics.is_bot_trade(t)]
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
                        net.send_telegram(msg, category="daily")
                        self._last_daily_report = time.time()
                else:
                    exits = await asyncio.to_thread(trading.check_exits, self)
                    if exits:
                        self._save_state()
            except Exception:
                log.exception("Loop error")
            await asyncio.sleep(60)
