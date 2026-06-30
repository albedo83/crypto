"""Observation-only Telegram alerts (WR drift, giveback, lock-floor, regime).

Ported from analysis/bot/bot.py `_check_*_alerts` methods (v12.17.3), reading
per-bot Params instead of module config. NO trading action — these inform the
user's manual decisions (hybrid alpha: bot detects, user acts).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import analytics

log = logging.getLogger("alfred")


def check_wr_alerts(bot) -> None:
    """v12.4.0 — alert when an open position's estimated WR drops < 25%."""
    with bot._pos_lock:
        bot._wr_alerted &= set(bot.positions.keys())
        positions = list(bot.positions.items())
    if not positions or not bot.trades:
        return
    trades_list = list(bot.trades)
    now = datetime.now(timezone.utc)
    for sym, pos in positions:
        if sym in bot._wr_alerted:
            continue
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        hold_target = max(1.0, (pos.target_exit - pos.entry_time).total_seconds() / 3600)
        st = bot.states.get(sym)
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
        # Suppress when currently up or already showed a strong pulse (v12.5.4)
        if cur_pnl > 0 or pos.mfe_bps >= 500:
            continue
        side = "LONG" if pos.direction == 1 else "SHORT"
        # TG « Consider manual close » retiré (2026-06-30) — supplanté par
        # l'arbitre IA de sortie qui AGIT. Event conservé pour le dashboard.
        bot.db.log_event("WR_ALERT", sym, {
            "strategy": pos.strategy, "dir": side,
            "wr_pct": wp["wr_pct"], "base_wr_pct": wp["base_wr_pct"],
            "n": wp["n"], "scope": wp["scope"], "note": wp["note"],
            "hold_h": round(hold_h, 1)})
        bot._wr_alerted.add(sym)


def check_giveback_alerts(bot) -> None:
    """v12.7.2 — alert on the "giveback through middle" pattern."""
    p = bot.p
    if not p.giveback_alert_strategies:
        return
    with bot._pos_lock:
        bot._giveback_alerted &= set(bot.positions.keys())
        positions = list(bot.positions.items())
    if not positions:
        return
    now = datetime.now(timezone.utc)
    for sym, pos in positions:
        if sym in bot._giveback_alerted or pos.strategy not in p.giveback_alert_strategies:
            continue
        st = bot.states.get(sym)
        if not st or st.price <= 0 or pos.entry_price <= 0:
            continue
        ur_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
        if pos.mfe_bps < p.giveback_alert_mfe_min_bps:
            continue
        if ur_bps > p.giveback_alert_cur_max_bps:
            continue
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        t_since_mfe = hold_h - pos.mfe_at_h
        if t_since_mfe < p.giveback_alert_time_since_mfe_min_h:
            continue
        cur_pnl = pos.size_usdt * ur_bps / 1e4
        mae_pnl = pos.size_usdt * pos.mae_bps / 1e4
        mfe_pnl = pos.size_usdt * pos.mfe_bps / 1e4
        side = "LONG" if pos.direction == 1 else "SHORT"
        retr = (pos.mfe_bps - ur_bps) / pos.mfe_bps * 100 if pos.mfe_bps > 0 else 0
        # TG « Consider manual_close » retiré (2026-06-30) — supplanté par
        # l'arbitre IA de sortie. Event conservé pour le dashboard.
        bot.db.log_event("GIVEBACK_ALERT", sym, {
            "strategy": pos.strategy, "dir": side,
            "mfe_bps": round(pos.mfe_bps, 1), "cur_bps": round(ur_bps, 1),
            "t_since_mfe_h": round(t_since_mfe, 1),
            "cur_pnl": round(cur_pnl, 2), "mfe_pnl": round(mfe_pnl, 2)})
        bot._giveback_alerted.add(sym)


def check_lock_floor_alerts(bot) -> None:
    """v12.7.2 — alert when substantial unrealized profit has no manual_stop."""
    p = bot.p
    if not p.lock_floor_alert_strategies:
        return
    with bot._pos_lock:
        bot._lock_floor_alerted &= set(bot.positions.keys())
        positions = list(bot.positions.items())
    if not positions:
        return
    now = datetime.now(timezone.utc)
    for sym, pos in positions:
        if sym in bot._lock_floor_alerted or pos.strategy not in p.lock_floor_alert_strategies:
            continue
        if pos.manual_stop_usdt is not None:
            continue
        st = bot.states.get(sym)
        if not st or st.price <= 0 or pos.entry_price <= 0:
            continue
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        if hold_h < p.lock_floor_alert_min_hold_h:
            continue
        ur_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
        cur_pnl = pos.size_usdt * ur_bps / 1e4
        if cur_pnl < p.lock_floor_alert_min_usd and ur_bps < p.lock_floor_alert_min_bps:
            continue
        side = "LONG" if pos.direction == 1 else "SHORT"
        suggested = max(0.0, round(cur_pnl - p.lock_floor_alert_buffer_usd))
        # TG « Consider manual_stop » retiré (2026-06-30) — l'arbitre IA de sortie
        # pose désormais lui-même le stop protecteur (LOCK). Event conservé.
        bot.db.log_event("LOCK_FLOOR_ALERT", sym, {
            "strategy": pos.strategy, "dir": side,
            "cur_bps": round(ur_bps, 1), "cur_pnl": round(cur_pnl, 2),
            "suggested_floor": suggested, "hold_h": round(hold_h, 1)})
        bot._lock_floor_alerted.add(sym)


def check_regime_alert(bot, cross_ctx: dict) -> None:
    """v12.7.14 — alert when disp_7d elevated AND recent (strat, dir) WR poor."""
    p = bot.p
    if p.regime_alert_disp_7d_bps >= 99000:
        return
    disp_7d = cross_ctx.get("disp_7d")
    if disp_7d is None or disp_7d < p.regime_alert_disp_7d_bps:
        return
    now_ts = time.time()
    if now_ts - bot._regime_alert_last_ts < p.regime_alert_cooldown_h * 3600:
        return
    side_str = "LONG" if p.regime_alert_direction == 1 else "SHORT"
    recent = [t for t in reversed(bot.trades)
              if t.strategy == p.regime_alert_strategy and t.direction == side_str]
    recent = recent[:p.regime_alert_lookback]
    if len(recent) < p.regime_alert_lookback:
        return
    wins = sum(1 for t in recent if t.pnl_usdt > 0)
    wr_pct = wins / len(recent) * 100
    if wr_pct >= p.regime_alert_wr_pct:
        return
    sum_pnl = sum(t.pnl_usdt for t in recent)
    bot.notifier.send(
        f"🌪️ Regime alert: {p.regime_alert_strategy} {side_str} struggling\n"
        f"  disp_7d={disp_7d:.0f} bps (≥{p.regime_alert_disp_7d_bps:.0f}) "
        f"+ recent WR={wr_pct:.0f}% on last {len(recent)} ({sum_pnl:+.2f} $)\n"
        f"  Consider pausing {p.regime_alert_strategy} {side_str} manually "
        f"if pattern persists. Cooldown {p.regime_alert_cooldown_h:.0f}h.",
        category="trade", actionable=True)
    bot.db.log_event("REGIME_ALERT", None, {
        "strategy": p.regime_alert_strategy, "dir": side_str,
        "disp_7d": round(disp_7d, 0), "wr_pct": round(wr_pct, 0),
        "n_recent": len(recent), "sum_pnl": round(sum_pnl, 2)})
    bot._regime_alert_last_ts = now_ts


def run_all(bot, cross_ctx: dict) -> None:
    """All four checks, individually fail-safe (one broken alert must not
    block the scan)."""
    for fn, args in ((check_wr_alerts, (bot,)),
                     (check_giveback_alerts, (bot,)),
                     (check_lock_floor_alerts, (bot,)),
                     (check_regime_alert, (bot, cross_ctx))):
        try:
            fn(*args)
        except Exception as e:
            log.warning("[%s] alert check %s failed: %s", bot.id, fn.__name__, e)
