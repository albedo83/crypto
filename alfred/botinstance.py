"""BotInstance — one trading bot (paper or live) inside the Alfred process.

Holds the per-bot mutable state that analysis/bot/bot.py:MultiSignalBot used
to mix with market data: positions, trades, capital/P&L, cooldowns, pauses,
alert dedup. Market data (states, features, btc_z, cross_ctx…) is READ from
the shared MarketDataMaster — never recomputed per bot.

Decision logic is the pure shared core (alfred/rules.py) — the same code the
backtests run. This file is only the impure shell: broker calls, DB/Telegram
writes, locks, persistence.

Attribute names intentionally mirror MultiSignalBot so the dashboard
response builders (web/views.py) port with minimal churn.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta

from . import ALFRED_VERSION
from . import alerts, features, persistence, rules, signals
from .brokers import PaperBroker
from .db import Database
from .models import Position, Trade
from .settings import BotConfig
from .telegram import Notifier

log = logging.getLogger("alfred")


class BotInstance:
    def __init__(self, cfg: BotConfig, master, data_dir: str):
        import os
        self.cfg = cfg
        self.id = cfg.id
        self.label = cfg.label
        self.mode = cfg.mode
        self.color = cfg.color
        self.version = ALFRED_VERSION
        self.p = cfg.params()
        self.master = master
        self.states = master.states            # shared market state (read-only use)
        self.token_sector = self.p.token_sector()

        bot_dir = os.path.join(data_dir, "bots", cfg.id)
        os.makedirs(bot_dir, exist_ok=True)
        self.db = Database(os.path.join(bot_dir, "bot.db"), "bot")
        self.state_file = os.path.join(bot_dir, "state.json")
        self.notifier = Notifier(
            token=os.environ.get(cfg.tg_token_env, "") if cfg.tg_token_env else "",
            chat_id=os.environ.get(cfg.tg_chat_id_env, "") if cfg.tg_chat_id_env else "",
            categories=cfg.tg_categories, label=cfg.label,
            public_url=cfg.public_url)
        if cfg.mode == "live":
            from .hl import HLAccount
            from .brokers import LiveBroker
            key = os.environ.get(cfg.private_key_env or "", "")
            if not key:
                raise ValueError(
                    f"[{cfg.id}] live mode but env var "
                    f"{cfg.private_key_env!r} is empty/unset")
            account = HLAccount(key, cfg.account_address,
                                self.p.trade_symbols, self.p.leverage,
                                self.p.min_fill_abort_usdt)
            self.broker = LiveBroker(self.p, account)
        else:
            self.broker = PaperBroker(self.p)

        # ── Per-bot mutable state (names mirror MultiSignalBot) ──
        self.positions: dict[str, Position] = {}
        self.trades: deque[Trade] = deque(maxlen=5000)
        self.status = "running"               # running | paused | error | stopped
        self.running = True
        self.started_at: datetime | None = None
        self._capital = cfg.capital_initial
        self._total_pnl = 0.0
        self._wins = 0
        self._peak_balance = cfg.capital_initial
        self._paused = cfg.start_paused
        self._paused_strats: set[tuple[str, str]] = set()
        self._cooldowns: dict[str, float] = {}
        self._consecutive_losses = 0
        self._signal_first_seen: dict[str, float] = {}
        self._pos_lock = threading.Lock()
        self._failed_closes: set[str] = set()
        self._closing: set[str] = set()
        self._inflight_open: set[str] = set()
        self._btc_z: float | None = None
        self._wr_alerted: set[str] = set()
        self._giveback_alerted: set[str] = set()
        self._lock_floor_alerted: set[str] = set()
        self._regime_alert_last_ts = 0.0
        self._basket_metrics: dict | None = None
        # Bot neuf (pas de state) : la période 4h courante est marquée déjà
        # consommée — la première entrée attend la prochaine frontière 4h au
        # lieu de fire au boot sur un prix intra-bougie (cas live 2026-06-10 :
        # CRV/COMP entrés à T+2h09 du close). Un state existant override via load().
        self._last_entry_scan_4h_close = (int(time.time()) // 14400) * 14400
        self._last_scan: float = 0.0
        self._last_daily_report: float = 0.0
        self._perf_track_start_ts = 0.0
        self._capital_at_perf_reset = 0.0
        self._total_pnl_at_perf_reset = 0.0
        self._exchange = None                  # live-only (phase 4)
        self._exchange_account: dict | None = None

    # ── Boot ─────────────────────────────────────────────────────────

    def load(self) -> None:
        """Restore state.json + trade history. Call once before scheduling."""
        st = persistence.load_state(self.state_file, set(self.p.all_symbols))
        if st:
            self._capital = st.get("capital", self._capital)
            self._total_pnl = st.get("total_pnl", 0.0)
            self._wins = st.get("wins", 0)
            self._peak_balance = max(st.get("peak_balance", 0.0),
                                     self._capital + self._total_pnl)
            self._last_daily_report = st.get("last_daily_report", 0)
            self._paused = st.get("paused", self._paused)
            self._consecutive_losses = st.get("consecutive_losses", 0)
            self._cooldowns = st.get("cooldowns", {})
            self._signal_first_seen = st.get("signal_first_seen", {})
            self._last_entry_scan_4h_close = st.get("_last_entry_scan_4h_close", 0)
            self._perf_track_start_ts = st.get("_perf_track_start_ts", 0.0)
            self._capital_at_perf_reset = st.get("_capital_at_perf_reset", 0.0)
            self._total_pnl_at_perf_reset = st.get("_total_pnl_at_perf_reset", 0.0)
            self._btc_z = st.get("_btc_z")
            self._paused_strats = st.get("_paused_strats", set())
            self.positions = st.get("positions", {})
            log.info("[%s] restored: %d positions, capital $%.2f, P&L $%.2f",
                     self.id, len(self.positions), self._capital, self._total_pnl)
        else:
            # Bot neuf : l'ancre perf/fees démarre à sa naissance — sinon les
            # diagnostics fees/funding (account_state) remontent 90 j dans
            # l'historique du wallet, qui peut précéder le bot (migration).
            self._perf_track_start_ts = time.time()
            self._capital_at_perf_reset = self._capital
        for t in persistence.load_trades(self.db):
            self.trades.append(t)
        self._catch_up_excursions()
        self.started_at = datetime.now(timezone.utc)

    def _catch_up_excursions(self) -> None:
        """A3 — après un downtime, les MAE/MFE des positions ouvertes ignorent
        les extrêmes traversés pendant la coupure. Replay des candles CLOSED
        de la fenêtre [max(entry, downtime_from), now] pour les corriger —
        sinon les règles MFE-based (s10_trailing, s8_inlife, prop_trail,
        runner_ext, dead_timeout) raisonnent sur des excursions fausses.

        Un stop/trail qui aurait dû partir pendant la coupure fire au premier
        tick post-boot, au mark courant (sémantique legacy, documentée)."""
        dt_info = getattr(self.master, "last_downtime", None)
        if not dt_info or not self.positions:
            return
        from_ms = dt_info["from_ms"]
        n_adj = 0
        with self._pos_lock:
            for sym, pos in self.positions.items():
                st = self.states.get(sym)
                if not st or not st.candles_4h or pos.entry_price <= 0:
                    continue
                entry_ms = int(pos.entry_time.timestamp() * 1000)
                lo_ms = max(entry_ms, from_ms)
                now_ms = int(time.time() * 1000)
                old_mfe, old_mae = pos.mfe_bps, pos.mae_bps
                for c in st.candles_4h:
                    # candles closed uniquement, dans la fenêtre du gap
                    if c["t"] + 14_400_000 > now_ms:
                        continue
                    if c["t"] + 14_400_000 < lo_ms or c["t"] > now_ms:
                        continue
                    best, worst = rules.candle_excursions(
                        pos.direction, pos.entry_price, c["h"], c["l"])
                    if best > pos.mfe_bps:
                        pos.mfe_bps = best
                    if worst < pos.mae_bps:
                        pos.mae_bps = worst
                if pos.mfe_bps != old_mfe or pos.mae_bps != old_mae:
                    n_adj += 1
                    self.db.log_event("EXCURSION_CATCHUP", sym, {
                        "mfe_before": round(old_mfe, 1),
                        "mfe_after": round(pos.mfe_bps, 1),
                        "mae_before": round(old_mae, 1),
                        "mae_after": round(pos.mae_bps, 1),
                        "downtime_s": dt_info["seconds"]})
                    log.info("[%s] excursion catch-up %s: mfe %+.0f→%+.0f "
                             "mae %+.0f→%+.0f", self.id, sym,
                             old_mfe, pos.mfe_bps, old_mae, pos.mae_bps)
        if n_adj:
            self._save_state()
            self.notifier.send(
                f"🩹 Excursion catch-up post-downtime "
                f"({dt_info['seconds']/3600:.1f}h) : {n_adj} position(s) "
                f"MAE/MFE corrigées", category="system")

    def _save_state(self) -> None:
        persistence.save_state(self)

    # ── Market-data wrappers (read the master; used by views too) ────

    @property
    def snapshot(self):
        return self.master.snapshot

    @property
    def _feature_cache(self) -> dict:
        snap = self.master.snapshot
        return snap.feature_cache if snap else {}

    @property
    def _cross_ctx_cache(self) -> dict | None:
        snap = self.master.snapshot
        return snap.cross_ctx if snap else None

    @property
    def _dxy_cache(self) -> tuple[float, float]:
        snap = self.master.snapshot
        return (snap.dxy_7d, snap.ts) if snap else (0.0, 0.0)

    @property
    def _oi_summary(self) -> dict:
        snap = self.master.snapshot
        return snap.oi_summary if snap else {"falling": 0, "rising": 0}

    @property
    def _degraded(self) -> list:
        return self.master._degraded

    @property
    def _last_price_fetch(self) -> float:
        return self.master.last_price_fetch

    def _get_cached_features(self, sym: str) -> dict | None:
        return self._feature_cache.get(sym)

    def _compute_features(self, sym: str) -> dict | None:
        return self.master._compute_features_for(sym)

    def _compute_btc_features(self) -> dict:
        snap = self.master.snapshot
        return snap.btc_f if snap else {}

    def _compute_alt_index(self) -> float:
        snap = self.master.snapshot
        return snap.alt_index if snap else 0.0

    def _compute_oi_features(self, sym: str) -> dict:
        return self.master._oi_features(sym)

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
        return features.compute_sector_divergence(
            sym, self._feature_cache, self.p.sectors, self.token_sector)

    def _detect_squeeze(self, sym: str) -> dict | None:
        st = self.states.get(sym)
        if not st or len(st.candles_4h) < 8:
            return None
        f = self._feature_cache.get(sym)
        vr = f.get("vol_ratio", 2) if f else 2
        from .market import closed_candles
        candles = closed_candles(st.candles_4h)
        if len(candles) < 8:
            return None
        return signals.detect_squeeze(candles, vr, self.p)

    def _market_ctx(self) -> rules.MarketCtx:
        snap = self.master.snapshot
        return rules.MarketCtx(
            btc_z=self._btc_z,
            btc_ret_4h_bps=snap.btc_ret_4h_bps if snap else None,
            disp_24h=(snap.cross_ctx.get("disp_24h") if snap else None))

    # ── Exits (every tick) ───────────────────────────────────────────

    def on_tick(self, now: datetime | None = None) -> int:
        """Update excursions and evaluate the exit chain for every position.
        Returns the number of exits."""
        now = now or datetime.now(timezone.utc)
        exits = 0
        m = self._market_ctx()

        # Retry any previously failed closes (live mode; no-op on paper)
        with self._pos_lock:
            failed_snapshot = list(self._failed_closes)
        for sym in failed_snapshot:
            if sym in self.positions:
                st = self.states.get(sym)
                if st and st.price > 0:
                    self.close_position(sym, st.price, now, "retry_close")
                    if sym not in self.positions:
                        exits += 1

        for sym in list(self.positions.keys()):
            with self._pos_lock:
                pos = self.positions.get(sym)
                if not pos:
                    continue
                entry_price = pos.entry_price
                direction = pos.direction
                target_exit = pos.target_exit

            st = self.states.get(sym)
            if not st or st.price == 0:
                if st and now >= target_exit:
                    log.warning("[%s] force-closing %s: price unavailable, hold expired",
                                self.id, sym)
                    self.close_position(sym, entry_price, now, "stale_price")
                    exits += 1
                continue

            unrealized = direction * (st.price / entry_price - 1) * 1e4
            with self._pos_lock:
                if sym not in self.positions:
                    continue
                hours_held = (now - pos.entry_time).total_seconds() / 3600
                rules.update_excursions(pos, unrealized, hours_held)
                last_h = pos.trajectory[-1][0] if pos.trajectory else -1
                if hours_held - last_h >= 0.95 and len(pos.trajectory) < 200:
                    pos.trajectory.append((round(hours_held, 1), round(unrealized, 1)))
                pv = rules.PosView(
                    strategy=pos.strategy, direction=direction,
                    entry_price=entry_price, size_usdt=pos.size_usdt,
                    stop_bps=pos.stop_bps,
                    mfe_bps=pos.mfe_bps, mae_bps=pos.mae_bps,
                    hours_held=hours_held,
                    hours_to_timeout=(pos.target_exit - now).total_seconds() / 3600,
                    mfe_at_h=pos.mfe_at_h, extended=pos.extended,
                    manual_stop_usdt=pos.manual_stop_usdt,
                    opp_floor_bps=pos.opp_floor_bps)

            dec = rules.evaluate_exit(pv, unrealized, m, self.p)
            if dec is None:
                continue
            if dec.action == "extend":
                new_target = target_exit + timedelta(hours=dec.extend_hours)
                with self._pos_lock:
                    if sym in self.positions:
                        self.positions[sym].target_exit = new_target
                        self.positions[sym].extended = True
                log.info("[%s] ⏭ RUNNER_EXT %s %s: MFE %+.0f cur %+.0f → hold +%dh",
                         self.id, pos.strategy, sym, pos.mfe_bps, unrealized,
                         int(dec.extend_hours))
                self.db.log_event("RUNNER_EXT", sym, {
                    "strategy": pos.strategy, "mfe_bps": round(pos.mfe_bps, 1),
                    "current_bps": round(unrealized, 1),
                    "extra_hours": dec.extend_hours,
                    "new_exit": new_target.isoformat()})
                self._save_state()
                continue
            exit_price = dec.exit_price if dec.exit_price is not None else st.price
            self.close_position(sym, exit_price, now, dec.reason)
            exits += 1
        return exits

    # ── Close ────────────────────────────────────────────────────────

    def close_position(self, sym: str, exit_price: float, now: datetime,
                       reason: str) -> None:
        """Exit a position, record the trade, update portfolio state.
        _closing mutex guards concurrent paths (tick exit + dashboard close)."""
        with self._pos_lock:
            if sym not in self.positions or sym in self._closing:
                return
            self._closing.add(sym)
        try:
            self._close_inner(sym, exit_price, now, reason)
        finally:
            with self._pos_lock:
                self._closing.discard(sym)

    def _close_inner(self, sym: str, exit_price: float, now: datetime,
                     reason: str) -> None:
        st = self.states.get(sym)
        mark = st.price if st and st.price > 0 else exit_price
        with self._pos_lock:
            pos = self.positions.get(sym)
            if pos is None:
                return
        # Execute on the exchange FIRST (live) — only pop if it succeeded.
        # On failure: keep the position tracked, flag it for the next-tick
        # retry, alert. Paper broker never raises.
        try:
            fill = self.broker.close(sym, pos.direction, exit_price, mark)
        except Exception as e:
            with self._pos_lock:
                self._failed_closes.add(sym)
            self.db.log_event("CLOSE_FAILED", sym,
                              {"reason": reason, "error": str(e)[:200]})
            log.error("[%s] EXEC CLOSE FAILED %s: %s — keeping position, "
                      "retry next tick", self.id, sym, e)
            self.notifier.send(f"❌ Close failed {sym}: {e} — will retry",
                               category="trade", actionable=True)
            return
        with self._pos_lock:
            self._failed_closes.discard(sym)
        exit_price = fill.avg_px
        # Live partial-fill coin reconcile (legacy v12.5.25): if the closed
        # coin count differs >1% from what we tracked, scale size_usdt to
        # `coins × open_price` (preserves open-notional P&L semantics).
        if self.broker.is_live and fill.size_usdt > 0 and pos.entry_price > 0:
            expected_coins = pos.size_usdt / pos.entry_price
            actual_coins = fill.size_usdt          # LiveBroker.close: sz in coins
            if expected_coins > 0 and abs(actual_coins - expected_coins) / expected_coins > 0.01:
                log.warning("[%s] CLOSE coin reconcile %s: expected=%.4f "
                            "filled=%.4f — partial fill?", self.id, sym,
                            expected_coins, actual_coins)
                with self._pos_lock:
                    if sym in self.positions:
                        self.positions[sym].size_usdt = actual_coins * pos.entry_price
                        pos = self.positions[sym]
        funding_adj = self.broker.trade_funding_usdt(
            sym, pos.direction, pos.size_usdt, accrued=0.0,
            entry_ms=int(pos.entry_time.timestamp() * 1000),
            exit_ms=int(now.timestamp() * 1000))

        with self._pos_lock:
            if sym not in self.positions:
                return
            pos = self.positions.pop(sym)
            hold_h = (now - pos.entry_time).total_seconds() / 3600
            final_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
            pos.trajectory.append((round(hold_h, 1), round(final_bps, 1)))
            gross_bps, net_bps, pnl = rules.compute_trade_pnl(
                pos.direction, pos.entry_price, exit_price, pos.size_usdt,
                self.p.cost_bps, funding_usdt=-funding_adj)
            self._total_pnl += pnl
            balance = self._capital + self._total_pnl
            if balance > self._peak_balance:
                self._peak_balance = balance
            if pnl > 0:
                self._wins += 1
                self._consecutive_losses = 0
            else:
                self._consecutive_losses += 1
            self._cooldowns[sym] = time.time() + self.p.cooldown_hours * 3600

        trade = Trade(
            symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
            strategy=pos.strategy,
            entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
            entry_price=pos.entry_price, exit_price=exit_price,
            hold_hours=round(hold_h, 1), size_usdt=pos.size_usdt,
            signal_info=pos.signal_info,
            gross_bps=round(gross_bps, 1), net_bps=round(net_bps, 1),
            pnl_usdt=round(pnl, 2),
            mae_bps=round(pos.mae_bps, 1), mfe_bps=round(pos.mfe_bps, 1),
            reason=reason,
            entry_oi_delta=pos.entry_oi_delta, entry_crowding=pos.entry_crowding,
            entry_confluence=pos.entry_confluence, entry_session=pos.entry_session,
            funding_usdt=round(funding_adj, 4))
        self.trades.append(trade)   # en mémoire d'abord : survit à un échec DB
        try:
            persistence.write_trade(trade, self.db)
        except Exception as e:
            # Perte d'écriture de trade = registre P&L canonique incomplet.
            # Ne plus avaler en silence (finding revue db#1) : alerter fort.
            log.error("[%s] ÉCHEC écriture trade %s en bot.db: %s — gardé en "
                      "mémoire, registre incomplet", self.id, sym, e)
            self.notifier.send(
                f"⚠️ Échec écriture trade {sym} ({self.id}) en bot.db — P&L "
                f"réalisé non persisté, vérifier la DB", category="system",
                actionable=True)
        persistence.write_trajectory(sym, pos, self.db)
        self.db.log_event("CLOSE", sym, {
            "strategy": pos.strategy, "dir": trade.direction,
            "exit_price": round(exit_price, 6), "hold_h": round(hold_h, 1),
            "gross_bps": round(gross_bps, 1), "net_bps": round(net_bps, 1),
            "pnl_usdt": round(pnl, 2), "mae_bps": round(pos.mae_bps, 1),
            "mfe_bps": round(pos.mfe_bps, 1), "reason": reason})

        n = len(self.trades)
        balance = self._capital + self._total_pnl
        wr = self._wins / n * 100 if n > 0 else 0
        arrow = "✓" if pnl > 0 else "✗"
        log.info("[%s] %s %s %s %s | %.0fh | %s | gross %+.1f | net %+.1f | "
                 "$%+.2f | mae %+.0f | mfe %+.0f | bal $%.0f (#%d %.0f%%)",
                 self.id, arrow, pos.strategy, trade.direction, sym, hold_h,
                 reason, gross_bps, net_bps, pnl, pos.mae_bps, pos.mfe_bps,
                 balance, n, wr)
        emoji = "🟩" if pnl > 0 else "🟥"
        self.notifier.send(
            f"{emoji} CLOSE {pos.strategy} {trade.direction} {sym} | "
            f"{net_bps:+.0f} bps | ${pnl:+.2f} | bal ${balance:.0f}",
            category="trade")
        self._save_state()

    def close_and_check(self, sym: str, exit_price: float, now, reason: str) -> bool:
        """Close + report success via _failed_closes (canonical check)."""
        self.close_position(sym, exit_price, now, reason)
        return sym not in self._failed_closes

    # ── Entries (4h-boundary scans) ──────────────────────────────────

    def _build_token_signals(self, now: datetime, btc_f: dict,
                             cross_ctx: dict) -> list:
        """Per-token detection. Mirrors bot.py:_build_token_signals (v12.17.3)
        — same SKIP/S9F_OBS observability events, shared detection code."""
        import numpy as np
        all_signals: list = []
        for sym in self.p.trade_symbols:
            if sym in self.positions:
                self.db.log_event("SKIP", sym, {"reason": "already_in_position"})
                continue
            if sym in self._cooldowns and time.time() < self._cooldowns[sym]:
                self.db.log_event("SKIP", sym, {
                    "reason": "cooldown",
                    "expires_at": int(self._cooldowns[sym]),
                    "remaining_h": round((self._cooldowns[sym] - time.time()) / 3600, 2)})
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

            sym_sector = self.token_sector.get(sym, "?")
            sect_stress = cross_ctx["stress_by_sector"].get(sym_sector, 0)
            dd = abs(f.get("drawdown", 0))
            shock = round(abs(f.get("ret_24h", 0)) / dd, 2) if dd > 100 else 0
            r24 = abs(f.get("ret_24h", 0))
            clean = round(f.get("range_pct", 0) / r24, 2) if r24 > 50 else 0
            _sect = self.token_sector.get(sym)
            _peers = ([self._feature_cache.get(pp) for pp in self.p.sectors.get(_sect, [])
                       if pp != sym] if _sect else [])
            _peer_rets = [abs(pf.get("ret_42h", 0)) for pf in _peers if pf]
            _peer_avg = np.mean(_peer_rets) if _peer_rets else 0
            lead = round(abs(f.get("ret_42h", 0)) / _peer_avg, 1) if _peer_avg > 100 else 0
            conf = int(sum([
                abs(f.get("drawdown", 0)) > 3000, f.get("vol_z", 0) > 1.5,
                abs(f.get("ret_24h", 0)) > 200, cross_ctx["n_stress_global"] >= 5,
                oi_f["oi_delta_1h"] < -1.0]))
            _h = now.hour
            _session = ("Asia" if _h < 8 else "EU" if _h < 14
                        else "US" if _h < 21 else "Night")
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
                sym, f, btc_f, sd, sq, oi_tag, entry_ctx, self.p)
            token_sigs = [s for s in token_sigs
                          if s["strategy"] in self.p.enabled_strategies]
            all_signals.extend(token_sigs)

            s9f = signals.check_s9f_observation(st.price_ticks, st.price)
            if s9f:
                self.db.log_event("S9F_OBS", sym, s9f)
        return all_signals

    def _rank_and_enter(self, sigs: list, now: datetime, m: rules.MarketCtx) -> int:
        """Sort by z, apply the shared gates, open positions via the broker.
        Mirrors trading.rank_and_enter (v12.17.3) wired on rules.py."""
        sigs.sort(key=lambda s: (s["z"], s["strength"]), reverse=True)
        with self._pos_lock:
            pos_snapshot = list(self.positions.values())
        c = rules.PortfolioCounters(
            n_total=len(pos_snapshot),
            n_longs=sum(1 for p in pos_snapshot if p.direction == 1),
            n_shorts=sum(1 for p in pos_snapshot if p.direction == -1),
            n_macro=sum(1 for p in pos_snapshot if p.strategy in self.p.macro_strategies),
            n_token=sum(1 for p in pos_snapshot if p.strategy not in self.p.macro_strategies))
        for _p in pos_snapshot:
            _s = self.token_sector.get(_p.symbol)
            if _s:
                c.sector_counts[_s] = c.sector_counts.get(_s, 0) + 1

        # ── Arbitrage IA (SENIOR only) : un seul appel sur le lot, fail-open ──
        # L'IA peut annuler (veto) ou réduire la taille. Overlay live-only, hors
        # rules.py (backtest inchangé). Mesuré en live par ai_arbiter_scorecard.
        arb, arb_mode, arb_cfg = {}, "off", None
        if self.id == "live":
            try:
                import ai_entry_arbiter as _aia
                arb_cfg = _aia.config()
                if arb_cfg["mode"] != "off":
                    arb_mode = "shadow" if _aia.is_tripped() else arb_cfg["mode"]
                    batch, seen_b = [], set()
                    for s in sigs:
                        sy = s["symbol"]
                        if sy in seen_b or sy in self.positions:
                            continue
                        seen_b.add(sy)
                        cx = s.get("ctx", {})
                        batch.append({
                            "symbol": sy, "strategy": s["strategy"],
                            "dir": "LONG" if s["direction"] == 1 else "SHORT",
                            "z": round(s.get("z", 0.0), 3),
                            "signal_info": s.get("info", ""),
                            "oi_delta": cx.get("oi_delta"),
                            "crowding": cx.get("crowding"),
                            "confluence": cx.get("confluence"),
                            "session": cx.get("session")})
                    if batch:
                        cc = getattr(self, "_cross_ctx_cache", None) or {}
                        market = {"btc_z": self._btc_z,
                                  "disp_24h": cc.get("disp_24h"),
                                  "disp_7d": cc.get("disp_7d"),
                                  "n_stress_global": cc.get("n_stress_global")}
                        res = _aia.arbitrate_safe(
                            batch, market, model=arb_cfg["model"],
                            timeout=arb_cfg["timeout"], factor_min=arb_cfg["factor_min"])
                        arb = res.get("verdicts", {}) or {}
                        meta = res.get("meta", {}) or {}
                        if meta.get("failopen"):
                            self.db.log_event("ARBITER_FAILOPEN", None,
                                              {"reason": meta["failopen"], "n": len(batch)})
                        log.info("[%s] arbiter %s: %d candidats → %d verdicts%s",
                                 self.id, arb_mode, len(batch), len(arb),
                                 " FAIL-OPEN" if meta.get("failopen") else "")
            except Exception as e:
                log.warning("[%s] arbiter skip: %s", self.id, e)
                arb, arb_mode = {}, "off"

        entries = 0
        seen: set[str] = set()
        capital = self._capital + self._total_pnl
        # Marge réellement disponible sur l'exchange (live only) : sert à
        # clamper le notionnel avant l'ordre au lieu de laisser HL rejeter
        # ("Insufficient margin", junior 2026-06-11 — compte plus petit que
        # le sizing modulé). Décrémentée au fil des fills du scan (le cache
        # equity n'est rafraîchi qu'au tick de 20s).
        avail_margin = None
        if self.broker.is_live and self._exchange_account:
            avail_margin = self._exchange_account.get("available")
        for sig in sigs:
            sym = sig["symbol"]
            side = "LONG" if sig["direction"] == 1 else "SHORT"
            if sym in seen:
                continue
            seen.add(sym)
            st = self.states.get(sym)
            oi_d = features.oi_delta_24h_bps(st.oi_history) if st else None
            reason = rules.entry_skip_reason(
                sig, c, m, self.p, capital, self.token_sector,
                in_position=sym in self.positions,
                in_cooldown=sym in self._cooldowns and time.time() < self._cooldowns[sym],
                paused=(sig["strategy"], side) in self._paused_strats,
                oi_delta_24h=oi_d, check_size_floor=True)
            if reason == "max_positions":
                self.db.log_event("SKIP", sym, {"strategy": sig["strategy"],
                                                "dir": side, "reason": reason})
                break
            if reason in ("already in position", "cooldown"):
                continue  # already logged at signal-building stage
            if reason:
                self.db.log_event("SKIP", sym, {"strategy": sig["strategy"],
                                                "dir": side, "reason": reason})
                continue
            if not st or st.price <= 0:
                continue
            with self._pos_lock:
                if sym in self._inflight_open:
                    continue
                # réserve le symbole : ferme la course avec api_manual_open
                # (qui checke positions + _inflight_open sous le même lock)
                self._inflight_open.add(sym)

            size = rules.position_size(sig["strategy"], sig["direction"],
                                       capital, self._btc_z, self.p)
            if avail_margin is not None:
                max_notional = max(0.0, (avail_margin - 5.0) * self.p.leverage)
                if size > max_notional:
                    if max_notional < 50.0:
                        log.info("[%s] SKIP %s %s: marge dispo $%.0f "
                                 "insuffisante (sizing $%.0f)",
                                 self.id, sym, sig["strategy"],
                                 avail_margin, size)
                        self.db.log_event("SKIP", sym, {
                            "strategy": sig["strategy"], "dir": side,
                            "reason": "insufficient_margin"})
                        with self._pos_lock:
                            self._inflight_open.discard(sym)
                        continue
                    log.info("[%s] %s %s réduit $%.0f → $%.0f (marge dispo "
                             "$%.0f)", self.id, sym, sig["strategy"],
                             size, max_notional, avail_margin)
                    size = max_notional
            # ── Application arbitrage IA (taille post-clamp = "règles seules") ──
            rules_size = size
            v = arb.get(sym)
            if v is not None and arb_mode != "off" and arb_cfg is not None:
                acted = (arb_mode == "act")
                hard_veto = (v["decision"] == "VETO"
                             and v["confidence"] >= arb_cfg["veto_conf_min"])
                if hard_veto:
                    eff_factor = 0.0
                elif v["decision"] == "VETO":      # veto basse-confiance → haircut
                    eff_factor = arb_cfg["factor_min"]
                else:                               # GO
                    eff_factor = v["factor"]
                applied_size = round(rules_size * eff_factor, 2)
                self.db.log_event("ARBITER_DECISION", sym, {
                    "mode": arb_mode, "acted": acted,
                    "strategy": sig["strategy"], "dir": side,
                    "decision": v["decision"], "hard_veto": hard_veto,
                    "factor": round(eff_factor, 3), "confidence": v["confidence"],
                    "reason": v["reason"], "risk_flags": v["risk_flags"],
                    "rules_size": round(rules_size, 2),
                    "applied_size": applied_size if acted else round(rules_size, 2),
                    "entry_time": now.isoformat(),
                    # pour le rejeu contrefactuel des vetos (mode act) :
                    "ref_price": round(st.price, 6),
                    "entry_ts_ms": int(now.timestamp() * 1000),
                    "hold_hours": sig.get("hold_hours", self.p.hold_hours_default),
                    "stop_bps": round(sig.get("stop_bps", 0.0), 1),
                    "btc_z": round(self._btc_z, 3) if self._btc_z is not None else None})
                if acted:
                    if eff_factor <= 0.0:
                        log.info("[%s] ARBITER VETO %s %s (conf %.2f): %s",
                                 self.id, sym, sig["strategy"], v["confidence"],
                                 v["reason"])
                        with self._pos_lock:
                            self._inflight_open.discard(sym)
                        continue
                    size = applied_size
            mult = rules.modulator_mult(sig["strategy"], sig["direction"],
                                        self._btc_z, self.p)
            hold_h = sig.get("hold_hours", self.p.hold_hours_default)
            target_exit = now + timedelta(hours=hold_h)
            try:
                fill = self.broker.open(sym, sig["direction"], size, st.price)
            except Exception as e:
                log.error("[%s] EXEC OPEN FAILED %s %s: %s",
                          self.id, sym, sig["strategy"], e)
                # Insufficient-margin cascades at a saturated boundary are
                # normal — don't page for each one (legacy v12.13.9).
                _es = str(e).lower()
                if ("insufficient margin" not in _es
                        and "margin to place order" not in _es):
                    self.notifier.send(
                        f"❌ Open failed {sym} {sig['strategy']}: {e}",
                        category="trade", actionable=True)
                with self._pos_lock:
                    self._inflight_open.discard(sym)
                continue
            entry_price, filled_size = fill.avg_px, fill.size_usdt
            if avail_margin is not None:
                avail_margin -= filled_size / self.p.leverage

            ctx = sig.get("ctx", {})
            with self._pos_lock:
                self.positions[sym] = Position(
                    symbol=sym, direction=sig["direction"],
                    strategy=sig["strategy"],
                    entry_price=entry_price, entry_time=now,
                    size_usdt=filled_size, signal_info=sig["info"],
                    target_exit=target_exit,
                    trajectory=[(0.0, 0.0)],
                    stop_bps=sig.get("stop_bps", 0.0),
                    entry_oi_delta=float(ctx.get("oi_delta", 0.0)),
                    entry_crowding=int(ctx.get("crowding", 0) or 0),
                    entry_confluence=int(ctx.get("confluence", 0) or 0),
                    entry_session=ctx.get("session", "") or "")
                self._inflight_open.discard(sym)
            # Persiste tout de suite : un crash entre le fill exchange et le
            # save de fin de scan laisserait une position réelle non trackée
            # (orphan au boot_reconcile, jamais auto-importée).
            self._save_state()
            esi = features.compute_entry_side_imbalance(
                sig["direction"], st.price, st.impact_bid, st.impact_ask)
            self.db.log_event("OPEN", sym, {
                "strategy": sig["strategy"], "dir": side,
                "entry_price": round(entry_price, 6),
                "size_usdt": round(filled_size, 2),
                "target_exit": target_exit.isoformat(),
                "stop_bps": round(sig.get("stop_bps", 0.0), 1),
                "btc_z": round(self._btc_z, 3) if self._btc_z is not None else None,
                "mult": round(mult, 3) if mult is not None else None,
                "basket_effective_n": (self._basket_metrics or {}).get("effective_n"),
                "entry_side_imbalance": esi["esi"] if esi else None})
            # Observation IA (SENIOR seul) : fige le contexte de décision exact
            # que le bot a vu, pour jugement asynchrone par entry_judge.py.
            # Donnée pure et purement additive (aucun réseau/SDK ici) — ne peut
            # pas affecter l'entrée/sortie ni le backtest. Voir le plan IA SENIOR.
            if self.id == "live":
                feat = self._feature_cache.get(sym) or {}
                self.db.log_event("ENTRY_CONTEXT", sym, {
                    "entry_time": now.isoformat(),
                    "strategy": sig["strategy"], "dir": side,
                    "entry_price": round(entry_price, 6),
                    "size_usdt": round(filled_size, 2),
                    "signal_info": sig["info"],
                    "signal_z": round(sig["z"], 3),
                    "signal_strength": round(float(sig.get("strength", 0.0)), 3),
                    "stop_bps": round(sig.get("stop_bps", 0.0), 1),
                    "btc_z": round(self._btc_z, 3) if self._btc_z is not None else None,
                    "mult": round(mult, 3) if mult is not None else None,
                    "entry_oi_delta": round(float(ctx.get("oi_delta", 0.0)), 2),
                    "entry_crowding": int(ctx.get("crowding", 0) or 0),
                    "entry_confluence": int(ctx.get("confluence", 0) or 0),
                    "entry_session": ctx.get("session", "") or "",
                    "features": {k: round(v, 1) for k, v in feat.items()
                                 if isinstance(v, (int, float))}})
            self.notifier.send(
                f"🟢 OPEN {sig['strategy']} {side} {sym} @ ${entry_price:.4f} | ${filled_size:.0f}",
                category="trade")

            c.n_total += 1
            if sig["direction"] == 1:
                c.n_longs += 1
            else:
                c.n_shorts += 1
            if sig["strategy"] in self.p.macro_strategies:
                c.n_macro += 1
            else:
                c.n_token += 1
            _sect = self.token_sector.get(sym)
            if _sect:
                c.sector_counts[_sect] = c.sector_counts.get(_sect, 0) + 1
            entries += 1
            log.info("[%s] → %s %s %s @ $%.4f | %s | $%.0f | exit ~%s | %d/%d pos",
                     self.id, sig["strategy"], side, sym, entry_price, sig["info"],
                     filled_size, target_exit.strftime("%m-%d %H:%M"),
                     c.n_total, self.p.max_positions)
        return entries

    def on_scan(self, now: datetime | None = None) -> int:
        """Hourly scan: alerts always; entries gated to 4h boundaries.
        Mirrors bot.py:_scan_and_trade (v12.17.3)."""
        now = now or datetime.now(timezone.utc)
        snap = self.master.snapshot
        if snap is None:
            return 0
        self._last_scan = time.time()
        self._btc_z = snap.btc_z
        cross_ctx = snap.cross_ctx
        btc_f = snap.btc_f

        alerts.run_all(self, cross_ctx)

        # Daily Telegram digest at 00 UTC
        if now.hour == 0 and time.time() - self._last_daily_report > 43200:
            try:
                from .web import views
                self.notifier.send(views.build_daily_summary(self), category="daily")
                self._last_daily_report = time.time()
            except Exception as e:
                log.warning("[%s] daily summary failed: %s", self.id, e)

        if self._paused:
            return 0

        # Defensive: never consume the 4h gate without live prices (the gate
        # marks the period as evaluated — burning it on empty data would skip
        # the period's entries entirely).
        if not self.master.last_price_fetch:
            return 0

        # 4h candle alignment gate (v12.9.0) — entries at most once per period
        now_ts = int(time.time())
        last_4h_close = (now_ts // 14400) * 14400
        if self._last_entry_scan_4h_close >= last_4h_close:
            return 0
        self._last_entry_scan_4h_close = last_4h_close

        with self._pos_lock:
            pos_for_basket = dict(self.positions)
        self._basket_metrics = features.compute_basket_correlation(
            pos_for_basket, self.states)
        persistence.log_basket_snapshot(self._basket_metrics, self.db)

        m = self._market_ctx()
        sigs = self._build_token_signals(now, btc_f, cross_ctx)
        signals.track_signal_age(sigs, self._signal_first_seen, time.time())
        self._arm_opp_floors(btc_f, cross_ctx)
        n_new = self._rank_and_enter(sigs, now, m)
        self._save_state()
        return n_new

    def _arm_opp_floors(self, btc_f: dict, cross_ctx: dict) -> None:
        """v1.2.0 — détection des signaux sur les tokens DÉTENUS (le flux
        candidats les skippe) : un signal de direction opposée sur une
        position gagnante arme un plancher cliquet à
        `opp_floor_lock_ratio` × gain courant (déclenché au tick par
        rules.opp_floor_rule). Même cadence que les entrées (scan 4h)."""
        if self.p.opp_floor_lock_ratio <= 0:
            return
        with self._pos_lock:
            held = list(self.positions.items())
        for sym, pos in held:
            st = self.states.get(sym)
            f = self._feature_cache.get(sym)
            if not st or st.price <= 0 or not f or pos.entry_price <= 0:
                continue
            ur = pos.direction * (st.price / pos.entry_price - 1) * 1e4
            level = rules.opp_floor_level(ur, self.p)
            if level is None or level <= (pos.opp_floor_bps or float("-inf")):
                continue
            sd = self._compute_sector_divergence(sym)
            sq = self._detect_squeeze(sym)
            try:
                sigs = signals.detect_token_signals(
                    sym, f, btc_f, sd, sq, "", {}, self.p)
            except Exception:
                log.exception("[%s] opp_floor signal detection failed %s",
                              self.id, sym)
                continue
            if not any(s["direction"] == -pos.direction for s in sigs):
                continue
            with self._pos_lock:
                cur = self.positions.get(sym)
                if cur is None:
                    continue
                old = cur.opp_floor_bps
                cur.opp_floor_bps = level
            opp = [s["strategy"] for s in sigs if s["direction"] == -pos.direction]
            log.info("[%s] 🛡 OPP_FLOOR %s %s: signal opposé %s à ur %+.0f bps "
                     "→ plancher %+.0f bps%s", self.id, pos.strategy, sym,
                     "/".join(opp), ur, level,
                     f" (était {old:+.0f})" if old is not None else "")
            self.db.log_event("OPP_FLOOR", sym, {
                "strategy": pos.strategy,
                "opp_signals": opp,
                "ur_bps": round(ur, 1),
                "floor_bps": round(level, 1),
                "prev_floor_bps": round(old, 1) if old is not None else None})
            self.notifier.send(
                f"🛡 {sym} {pos.strategy}: signal opposé ({'/'.join(opp)}) — "
                f"plancher posé à {level:+.0f} bps (gain {ur:+.0f})",
                category="trade")

    # ── Live-only duties (phase 4) ───────────────────────────────────

    def refresh_equity(self, full: bool = False) -> None:
        """Refresh self._exchange_account from HL. `full` adds fees/funding
        diagnostics (4 SDK calls vs 2) — called at the slower scan cadence;
        the cheap variant runs every equity tick. No-op on paper."""
        if not self.broker.is_live:
            return
        fees_start = (int(self._perf_track_start_ts * 1000)
                      if self._perf_track_start_ts else None)
        acct = (self.broker.account.account_state(fees_start) if full
                else self.broker.account.equity_only())
        if acct:
            if self._exchange_account and not full:
                # keep the slow diagnostic fields from the last full refresh
                merged = dict(self._exchange_account)
                merged.update(acct)
                acct = merged
            self._exchange_account = acct

    def reconcile(self) -> None:
        """Hourly bot-vs-exchange position reconcile. No-op on paper."""
        if not self.broker.is_live:
            return
        with self._pos_lock:
            pos_snapshot = dict(self.positions)
        self.broker.account.reconcile(pos_snapshot, self.notifier.send)

    def boot_reconcile(self) -> None:
        """Once at boot (after load): drop ghost positions (in the bot but
        absent on the exchange — e.g. manually closed in the HL UI while the
        bot was down). Orphans are alerted, never auto-imported. No-op paper."""
        if not self.broker.is_live:
            return
        try:
            exch = self.broker.account.exchange_positions()
        except Exception as e:
            log.warning("[%s] boot reconcile fetch failed: %s — keeping all "
                        "positions, hourly reconcile will flag", self.id, e)
            return
        with self._pos_lock:
            ghosts = [s for s in self.positions if s not in exch]
            for sym in ghosts:
                pos = self.positions.pop(sym)
                log.warning("[%s] boot reconcile: dropping ghost %s %s "
                            "(absent on exchange)", self.id, sym, pos.strategy)
        if ghosts:
            self.notifier.send(
                f"🧹 Boot reconcile: dropped ghost positions {ghosts} "
                f"(absent on exchange)", category="reconcile")
            self._save_state()
        orphans = [s for s in exch if s not in self.positions]
        if orphans:
            self.notifier.send(
                f"⚠️ Boot reconcile: orphan positions on exchange {orphans} "
                f"(not tracked — manage manually)", category="reconcile",
                actionable=True)

    def safe_refresh_equity(self, full: bool = False) -> None:
        try:
            self.refresh_equity(full=full)
        except Exception:
            log.exception("[%s] refresh_equity failed", self.id)

    def safe_reconcile(self) -> None:
        try:
            self.reconcile()
        except Exception:
            log.exception("[%s] reconcile failed", self.id)

    # ── Safe wrappers (scheduler entry points) ───────────────────────

    def safe_on_tick(self) -> None:
        if self.status == "stopped":
            return
        try:
            if self.on_tick():
                self._save_state()
            if self.status == "error":
                self.status = "running"
        except Exception:
            log.exception("[%s] on_tick failed", self.id)
            self.status = "error"

    def safe_on_scan(self) -> None:
        if self.status == "stopped":
            return
        try:
            n = self.on_scan()
            if n:
                log.info("[%s] opened %d new positions", self.id, n)
            if self.status == "error":
                self.status = "running"
        except Exception:
            log.exception("[%s] on_scan failed", self.id)
            self.status = "error"
            self.notifier.send(f"💥 [{self.label}] scan error — bot en statut error, "
                               f"voir alfred.log", category="system")
