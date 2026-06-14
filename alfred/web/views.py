"""API response builders — ported from analysis/bot/web.py (v12.17.3),
parameterized by BotInstance (bot.p Params + master-fed wrappers) instead of
module-level config. The reversal.html dashboard consumes these unchanged.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from ..analytics import (is_bot_trade, compute_signal_drift,
                         compute_signal_drift_by_dir, get_recent_regime_alerts,
                         compute_strategy_advice, compute_s10_health,
                         filter_by_perf_scope, estimate_win_prob,
                         filter_recent_trades)
from .. import rules
from ..features import oi_delta_24h_bps

log = logging.getLogger("alfred")


def _to_py(v):
    """Coerce numpy / arbitrary types to JSON-safe Python natives. Recursive."""
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _to_py(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_py(x) for x in v]
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    if hasattr(v, "item"):
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _skip_reason(bot, sig: dict, c: rules.PortfolioCounters,
                 disp_24h: float | None) -> str | None:
    """Dashboard preview — same gates as the entry path (rules.entry_skip_reason)."""
    sym = sig["symbol"]
    side = "LONG" if sig["direction"] == 1 else "SHORT"
    st = bot.states.get(sym)
    return rules.entry_skip_reason(
        sig, c, rules.MarketCtx(btc_z=bot._btc_z, disp_24h=disp_24h),
        bot.p, bot._capital + bot._total_pnl, bot.token_sector,
        in_position=sym in bot.positions,
        in_cooldown=sym in bot._cooldowns and time.time() < bot._cooldowns[sym],
        paused=(sig["strategy"], side) in bot._paused_strats,
        oi_delta_24h=oi_delta_24h_bps(st.oi_history) if st else None,
        check_size_floor=True)


def _strategy_dashboard_extras(bot) -> dict:
    """Per-(strat, dir) advice/drift bundle for the dashboard pause toggles."""
    drift = compute_signal_drift_by_dir(bot.trades, bot._perf_track_start_ts,
                                        recent_n=10)
    alerts = get_recent_regime_alerts(bot.db.conn, hours=48)
    cross = bot._cross_ctx_cache or {}
    disp_7d = cross.get("disp_7d")
    disp_24h = cross.get("disp_24h")
    advice = compute_strategy_advice(drift, alerts, bot._btc_z, disp_7d)
    return _to_py({
        "signal_drift_by_dir": drift,
        "regime_recent_alerts": alerts,
        "strategy_advice": advice,
        "cross_disp_7d": disp_7d,
        "cross_disp_24h": disp_24h,
    })


def _collect_active_signals(bot, btc_f) -> list:
    """Active signal one-liners for the dashboard + admin cards."""
    p = bot.p
    active = []
    if btc_f.get("btc_30d", 0) > p.s1_btc_30d_min_bps:
        active.append(f"S1: BTC 30d = {btc_f['btc_30d']:+.0f}bps → LONG")
    s5, s8, s9, s10 = [], [], [], []
    for sym in p.trade_symbols:
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= p.s5_div_threshold and sd["vol_z"] >= p.s5_vol_z_min:
            s5.append(f"{sym}({'L' if sd['divergence'] > 0 else 'S'})")
        f = bot._get_cached_features(sym)
        if f:
            if (f.get("drawdown", 0) < p.s8_drawdown_thresh
                    and f.get("vol_z", 0) > p.s8_vol_z_min
                    and f.get("ret_24h", 0) < p.s8_ret_24h_thresh
                    and btc_f.get("btc_7d", 0) < p.s8_btc_7d_thresh):
                s8.append(sym)
            if abs(f.get("ret_24h", 0)) >= p.s9_ret_thresh:
                s9.append(f"{sym}({'S' if f['ret_24h'] > 0 else 'L'})")
        sq = bot._detect_squeeze(sym)
        if sq:
            s10.append(f"{sym}({'L' if sq['direction'] == 1 else 'S'})")
    if s5:
        active.append(f"S5: {', '.join(s5[:5])} sector divergence")
    if s8:
        active.append(f"S8: {', '.join(s8[:5])} capitulation flush")
    if s9:
        active.append(f"S9: {', '.join(s9[:5])} fade extreme")
    if s10:
        active.append(f"S10: {', '.join(s10[:5])} squeeze")
    return active


def build_daily_summary(bot) -> str:
    """Daily digest string (Telegram)."""
    today = datetime.now(timezone.utc).isoformat()[:10]
    cap = bot._capital
    realized = bot._total_pnl
    now = datetime.now(timezone.utc)
    sum_unreal = 0.0
    with bot._pos_lock:
        pos_snapshot = dict(bot.positions)
    for _p in pos_snapshot.values():
        _st = bot.states.get(_p.symbol)
        _px = _st.price if _st and _st.price > 0 else _p.entry_price
        if _p.entry_price > 0:
            _ur = _p.direction * (_px / _p.entry_price - 1) * 1e4
            sum_unreal += _p.size_usdt * _ur / 1e4
    equity = cap + realized + sum_unreal
    equity_delta = equity - cap
    equity_pct = (equity_delta / cap * 100) if cap > 0 else 0
    ab = [t for t in bot.trades if is_bot_trade(t)]
    n = len(ab)
    wr = (sum(1 for t in ab if t.pnl_usdt > 0) / n * 100) if n else 0
    pnl_pct = (realized / cap * 100) if cap > 0 else 0
    lines = []
    for sym, pos in sorted(pos_snapshot.items(), key=lambda kv: kv[0]):
        st = bot.states.get(sym)
        px = st.price if st and st.price > 0 else pos.entry_price
        ur = pos.direction * (px / pos.entry_price - 1) * 1e4 if pos.entry_price > 0 else 0
        pnl_pos = pos.size_usdt * ur / 1e4
        rem_h = max(0, (pos.target_exit - now).total_seconds() / 3600)
        direction = "LONG" if pos.direction == 1 else "SHORT"
        lines.append(f"  • {sym} {direction} {pos.strategy} | "
                     f"{ur:+.0f} bps (${pnl_pos:+.2f}) | {rem_h:.0f}h left")
    pos_block = ("\n".join(lines)) if lines else "  (none)"
    return (f"📊 Daily {today}\n"
            f"💰 Equity: ${equity:.2f} "
            f"({equity_delta:+.2f} / {equity_pct:+.1f}% on ${cap:.0f})\n"
            f"📈 P&L: ${realized:+.2f} ({pnl_pct:+.1f}%) | "
            f"{wr:.0f}% win on {n}\n"
            f"📌 Open ({len(pos_snapshot)}):\n{pos_block}")


def build_state_response(bot) -> dict:
    """Full dashboard state dict (consumed by reversal.html and the admin view)."""
    p = bot.p
    now = datetime.now(timezone.utc)
    bt = [t for t in filter_by_perf_scope(bot.trades, bot._perf_track_start_ts)
          if is_bot_trade(t)]
    n_bot, wins = len(bt), sum(1 for t in bt if t.pnl_usdt > 0)
    balance = bot._capital + bot._total_pnl
    _sum_unreal = 0.0
    with bot._pos_lock:
        pos_snapshot = dict(bot.positions)
    for _p in pos_snapshot.values():
        _st = bot.states.get(_p.symbol)
        _px = _st.price if _st and _st.price > 0 else _p.entry_price
        if _p.entry_price > 0:
            _ur = _p.direction * (_px / _p.entry_price - 1) * 1e4
            _sum_unreal += _p.size_usdt * _ur / 1e4
    equity = balance + _sum_unreal
    _acct = bot._exchange_account
    hl_equity = float(_acct["equity"]) if _acct and "equity" in _acct else None
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()

    cross = bot._cross_ctx_cache or {}
    n_stress = cross.get("n_stress_global", 0)
    btc30 = btc_f.get("btc_30d", 0)
    btc7 = btc_f.get("btc_7d", 0)
    z = bot._btc_z
    if n_stress >= 5:
        regime = "STRESSED"
    elif z is not None:
        if z >= 1.0:
            regime = "RALLY" if btc7 > 800 else "BULL"
        elif z <= -1.0:
            regime = "FLUSH" if btc7 < -800 else "BEAR"
        else:
            regime = "CHOPPY"
    else:
        if btc30 > 2000:
            regime = "BULL"
        elif btc30 < -1500:
            regime = "BEAR"
        elif btc7 > 1000 and btc30 > 500:
            regime = "RALLY"
        elif btc7 < -700:
            regime = "FLUSH"
        else:
            regime = "CHOPPY"

    recent_trades = filter_recent_trades(list(bot.trades))

    def _prop_trail_state(strategy: str, mfe_bps: float) -> dict:
        if strategy not in p.prop_trail_params or bot._btc_z is None:
            return {"active": False, "stop_bps": None, "regime": None}
        zz = bot._btc_z
        if zz < -p.prop_trail_z_threshold:
            bucket = "bear"
        elif zz > p.prop_trail_z_threshold:
            bucket = "bull"
        else:
            bucket = "neutral"
        cfg = p.prop_trail_params[strategy].get(bucket)
        if cfg is None:
            return {"active": False, "stop_bps": None, "regime": f"{bucket} (off)"}
        arm, lock = cfg
        if mfe_bps < arm:
            return {"active": False, "stop_bps": None, "regime": f"{bucket} (arm@{arm})"}
        stop = round(arm + (mfe_bps - arm) * lock, 0)
        return {"active": True, "stop_bps": stop, "regime": bucket}

    positions = []
    for sym, pos in pos_snapshot.items():
        st = bot.states.get(sym)
        px = st.price if st else pos.entry_price
        ur = pos.direction * (px / pos.entry_price - 1) * 1e4 if pos.entry_price > 0 else 0
        pt = _prop_trail_state(pos.strategy, pos.mfe_bps)
        rem = max(0, (pos.target_exit - now).total_seconds() / 3600)
        hold_h = round((now - pos.entry_time).total_seconds() / 3600, 1)
        effective_stop = rules.effective_stop(
            rules.PosView(strategy=pos.strategy, direction=pos.direction,
                          entry_price=pos.entry_price, size_usdt=pos.size_usdt,
                          stop_bps=pos.stop_bps, mfe_bps=pos.mfe_bps,
                          mae_bps=pos.mae_bps, hours_held=hold_h,
                          hours_to_timeout=rem, mfe_at_h=pos.mfe_at_h), p)
        stop_progress = (max(0.0, min(1.0, -ur / abs(effective_stop)))
                         if effective_stop < 0 else 0.0)
        total_hold = hold_h + rem
        hold_progress = round(hold_h / total_hold, 3) if total_hold > 0 else 0.0
        win_prob = estimate_win_prob(pos, recent_trades,
                                     hours_held=hold_h,
                                     hold_target_h=hold_h + max(rem, 0),
                                     pre_filtered=True,
                                     current_ur_bps=ur)
        # v12.7.13 status badge
        cur_pnl_usdt = pos.size_usdt * ur / 1e4
        at_mae = (ur - pos.mae_bps) <= 50
        t_since_mfe = max(0, hold_h - pos.mfe_at_h)
        if ur <= effective_stop + 200:
            status = {"key": "danger", "icon": "🚨", "label": "DANGER",
                      "tip": f"Within 200 bps of catastrophe stop ({int(effective_stop)} bps)"}
        elif pos.mfe_bps >= 500 and ur <= -100 and t_since_mfe >= 4:
            status = {"key": "decide", "icon": "⚡", "label": "DECIDE",
                      "tip": f"Giveback pattern: MFE peaked {int(pos.mfe_bps)}→{int(ur)} bps, {t_since_mfe:.1f}h ago"}
        elif (cur_pnl_usdt >= 20 or ur >= 600) and hold_h >= 4 and pos.manual_stop_usdt is None:
            status = {"key": "decide", "icon": "⚡", "label": "DECIDE",
                      "tip": f"Lock-floor opportunity: ${cur_pnl_usdt:+.1f} unrealized, no manual_stop set"}
        elif at_mae and hold_h >= 4 and ur <= -200:
            status = {"key": "decide", "icon": "⚡", "label": "DECIDE",
                      "tip": f"Pinned at MAE ({int(pos.mae_bps)} bps) for {hold_h:.1f}h"}
        elif hold_h < 1:
            status = {"key": "early", "icon": "🕐", "label": "EARLY",
                      "tip": f"Held {hold_h:.1f}h — too early to classify"}
        elif ur > 0:
            status = {"key": "profit", "icon": "🟢", "label": "PROFIT",
                      "tip": f"In profit ({int(ur)} bps)"}
        else:
            status = {"key": "wait", "icon": "⌛", "label": "WAIT",
                      "tip": f"Modest red ({int(ur)} bps), not at MAE — empirical 57% recover from this zone"}
        positions.append({
            "symbol": sym, "direction": "LONG" if pos.direction == 1 else "SHORT",
            "strategy": pos.strategy, "entry_price": pos.entry_price,
            "current_price": px, "size_usdt": pos.size_usdt,
            "signal_info": pos.signal_info, "unrealized_bps": round(ur, 1),
            "pnl_usdt": round(pos.size_usdt * ur / 1e4, 2),
            "entry_time": pos.entry_time.isoformat(),
            "hold_hours": hold_h,
            "remaining_hours": round(rem, 1),
            "remaining": f"{int(rem)}h{int((rem % 1) * 60):02d}m",
            "mae_bps": round(pos.mae_bps, 1), "mfe_bps": round(pos.mfe_bps, 1),
            "stop_bps": round(effective_stop, 0),
            "stop_progress": round(stop_progress, 3),
            "hold_progress": hold_progress,
            # + point LIVE en fin de trajectoire : les points sont horaires,
            # sans lui la sparkline (courbe ET couleur) retarde jusqu'à 1h
            # sur le P&L réel (vu sur le short CRV de JUNIOR, 2026-06-11).
            "trajectory": list(pos.trajectory) + [(round(hold_h, 2), round(ur, 1))],
            "trailing_active": bool(pos.strategy == "S10"
                                    and pos.mfe_bps >= p.s10_trailing_trigger),
            "trailing_floor_bps": (round(pos.mfe_bps - p.s10_trailing_offset, 0)
                                   if pos.strategy == "S10"
                                   and pos.mfe_bps >= p.s10_trailing_trigger else None),
            "prop_trail_active": pt["active"],
            "prop_trail_stop_bps": pt["stop_bps"],
            "prop_trail_regime": pt["regime"],
            "win_prob": win_prob,
            "manual_stop_usdt": (round(pos.manual_stop_usdt, 2)
                                 if pos.manual_stop_usdt is not None else None),
            "opp_floor_bps": (round(pos.opp_floor_bps, 0)
                              if pos.opp_floor_bps is not None else None),
            "status": status,
        })

    oi_deltas = {}
    for _sym in p.trade_symbols:
        _st = bot.states.get(_sym)
        if _st:
            _d = oi_delta_24h_bps(_st.oi_history)
            if _d is not None:
                oi_deltas[_sym] = round(_d, 0)

    # Scan preview — ENTER / SKIP with reason, same gates as the entry path.
    preview: list = []
    c = rules.PortfolioCounters(
        n_total=len(pos_snapshot),
        n_longs=sum(1 for q in pos_snapshot.values() if q.direction == 1),
        n_shorts=sum(1 for q in pos_snapshot.values() if q.direction == -1),
        n_macro=sum(1 for q in pos_snapshot.values() if q.strategy in p.macro_strategies),
        n_token=sum(1 for q in pos_snapshot.values() if q.strategy not in p.macro_strategies))
    for q in pos_snapshot.values():
        _s = bot.token_sector.get(q.symbol)
        if _s:
            c.sector_counts[_s] = c.sector_counts.get(_s, 0) + 1
    _disp_24h = cross.get("disp_24h")

    if btc_f.get("btc_30d", 0) > p.s1_btc_30d_min_bps:
        _r = _skip_reason(bot, {"symbol": "ALTS", "strategy": "S1", "direction": 1},
                          c, _disp_24h)
        preview.append({"symbol": "ALTS", "strategy": "S1", "direction": "LONG",
                        "status": _r or "would enter"})

    for _sym in p.trade_symbols:
        _st = bot.states.get(_sym)
        _f = bot._get_cached_features(_sym)
        if not _st or not _f:
            continue
        _fires: list = []
        _sd = bot._compute_sector_divergence(_sym)
        if _sd and abs(_sd["divergence"]) >= p.s5_div_threshold and _sd["vol_z"] >= p.s5_vol_z_min:
            _fires.append(("S5", 1 if _sd["divergence"] > 0 else -1))
        if (_f.get("drawdown", 0) < p.s8_drawdown_thresh
                and _f.get("vol_z", 0) > p.s8_vol_z_min
                and _f.get("ret_24h", 0) < p.s8_ret_24h_thresh
                and btc_f.get("btc_7d", 0) < p.s8_btc_7d_thresh):
            _fires.append(("S8", 1))
        _ret24 = _f.get("ret_24h", 0)
        if abs(_ret24) >= p.s9_ret_thresh:
            _fires.append(("S9", -1 if _ret24 > 0 else 1))
        _sq = bot._detect_squeeze(_sym)
        if _sq:
            if not ((not p.s10_allow_longs and _sq["direction"] == 1)
                    or _sym not in p.s10_allowed_tokens):
                _fires.append(("S10", _sq["direction"]))
        for _strat, _dir in _fires:
            _reason = _skip_reason(
                bot, {"symbol": _sym, "strategy": _strat, "direction": _dir},
                c, _disp_24h)
            preview.append({"symbol": _sym, "strategy": _strat,
                            "direction": "LONG" if _dir == 1 else "SHORT",
                            "status": _reason or "would enter"})

    _prio = {"would enter": 0, "already in position": 1}
    preview.sort(key=lambda x: (_prio.get(x["status"], 2), x["strategy"]))

    # Sector stats
    sector_stats: dict[str, dict] = {}
    for _sym, _sect in bot.token_sector.items():
        s = sector_stats.setdefault(_sect, {"n_tokens": 0, "n_positions": 0,
                                            "unrealized_pnl": 0.0, "ret_24h_sum": 0.0,
                                            "ret_24h_n": 0})
        s["n_tokens"] += 1
        _f = bot._get_cached_features(_sym)
        if _f and _f.get("ret_24h") is not None:
            s["ret_24h_sum"] += _f["ret_24h"]
            s["ret_24h_n"] += 1
    for _p in positions:
        _sect = bot.token_sector.get(_p["symbol"])
        if _sect and _sect in sector_stats:
            sector_stats[_sect]["n_positions"] += 1
            sector_stats[_sect]["unrealized_pnl"] += _p["pnl_usdt"]
    for _sect, s in sector_stats.items():
        s["avg_ret_24h_bps"] = round(s["ret_24h_sum"] / s["ret_24h_n"], 0) if s["ret_24h_n"] else 0
        s["unrealized_pnl"] = round(s["unrealized_pnl"], 2)
        del s["ret_24h_sum"], s["ret_24h_n"]

    return {
        "version": bot.version, "strategy": "Multi-Signal (S1+S5+S8+S9+S10)",
        "execution_mode": bot.mode,
        "bot_id": bot.id,
        "bot_label": bot.label, "bot_label_color": bot.color,
        "bot_status": bot.status,
        "paused": bot._paused, "running": bot.running,
        "paused_strategies": sorted([list(q) for q in bot._paused_strats]),
        "degraded": list(bot._degraded), "loss_streak": bot._consecutive_losses,
        "balance": round(balance, 2), "capital": bot._capital,
        "capital_cap": bot.cfg.capital_cap if bot.cfg.capital_cap > 0 else None,
        "equity": round(equity, 2),
        "hl_equity": round(hl_equity, 2) if hl_equity is not None else None,
        "exchange_account": bot._exchange_account,
        "total_pnl": round(bot._total_pnl, 2), "total_trades": n_bot,
        "win_rate": round(wins / n_bot, 3) if n_bot > 0 else 0,
        "n_positions": len(pos_snapshot), "max_positions": p.max_positions,
        "positions": positions,
        "active_signals": _collect_active_signals(bot, btc_f),
        "oi_deltas_24h": oi_deltas, "oi_gate_bps": p.oi_long_gate_bps,
        "blacklist": sorted(p.trade_blacklist),
        "regime": regime, "regime_stress": n_stress,
        "sector_stats": sector_stats,
        "preview": preview,
        "market": {
            "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "btc_7d": round(btc_f.get("btc_7d", 0), 0),
            "alt_index_7d": round(alt_idx, 0),
            "dxy_7d": round(bot._dxy_cache[0], 0),
            "oi_falling": bot._oi_summary["falling"],
            "oi_rising": bot._oi_summary["rising"],
        },
        "params": {"hold_h": p.hold_hours_default, "hold_s5_h": p.hold_hours_for("S5"),
                   "cost_bps": p.cost_bps, "stop_bps": p.stop_loss_bps,
                   "max_pos": p.max_positions},
        "uptime_s": (now - bot.started_at).total_seconds() if bot.started_at else 0,
        "started_at": bot.started_at.isoformat() if bot.started_at else None,
        "first_trade_date": bot.trades[0].entry_time if bot.trades else None,
        "last_price_s": time.time() - bot._last_price_fetch if bot._last_price_fetch else None,
        "last_scan_s": time.time() - bot._last_scan if bot._last_scan else None,
        "next_scan_s": (max(0, p.scan_interval - (time.time() - bot._last_scan))
                        if bot._last_scan else 0),
        "scan_interval": p.scan_interval,
        "signal_drift": compute_signal_drift(bot.trades, bot._perf_track_start_ts),
        **_strategy_dashboard_extras(bot),
        "perf_track_start_ts": bot._perf_track_start_ts,
        "s10_health": compute_s10_health(bot.trades),
        "btc_z_30d": round(bot._btc_z, 3) if bot._btc_z is not None else None,
        "unpause_thresholds": {"btc_z": -1.0, "disp_7d": 900.0, "stress": 6},
        "basket_metrics": bot._basket_metrics,
        "peak_balance": round(bot._peak_balance, 2),
        "drawdown_pct": (round((balance - bot._peak_balance) / bot._peak_balance * 100, 2)
                         if bot._peak_balance > 0 else 0),
        "pnl_pct": (round((balance - bot._capital) / bot._capital * 100, 2)
                    if bot._capital > 0 else 0),
        "capital_utilization_pct": round(
            sum(q.size_usdt / p.leverage for q in pos_snapshot.values())
            / max(balance, 1) * 100, 1),
    }


def _signal_proximity(p, btc_f, f, sd) -> dict:
    """Per-strategy activation score 0..1 (1 = firing right now)."""
    btc30 = btc_f.get("btc_30d", 0)
    btc7 = btc_f.get("btc_7d", 0)
    ret_24h = f.get("ret_24h", 0)
    vol_z = f.get("vol_z", 0)
    drawdown = f.get("drawdown", 0)
    vol_ratio = f.get("vol_ratio", 2.0)

    s1 = min(1.0, max(0.0, btc30 / p.s1_btc_30d_min_bps))
    if sd:
        div_prox = min(1.0, abs(sd["divergence"]) / p.s5_div_threshold)
        volz_prox = min(1.0, max(0.0, sd["vol_z"] / p.s5_vol_z_min))
        s5 = min(div_prox, volz_prox)
    else:
        s5 = 0.0
    s8 = min(
        min(1.0, max(0.0, drawdown / p.s8_drawdown_thresh)) if p.s8_drawdown_thresh != 0 else 0.0,
        min(1.0, max(0.0, vol_z / p.s8_vol_z_min)),
        min(1.0, max(0.0, ret_24h / p.s8_ret_24h_thresh)) if p.s8_ret_24h_thresh != 0 else 0.0,
        min(1.0, max(0.0, btc7 / p.s8_btc_7d_thresh)) if p.s8_btc_7d_thresh != 0 else 0.0,
    )
    s9 = min(1.0, abs(ret_24h) / p.s9_ret_thresh)
    s10 = min(1.0, max(0.0, (1.5 - vol_ratio) / 0.6)) if vol_ratio > 0 else 0.0
    return {"S1": round(s1, 2), "S5": round(s5, 2), "S8": round(s8, 2),
            "S9": round(s9, 2), "S10": round(s10, 2)}


def build_signals_response(bot) -> dict:
    """All symbols with their current features and signal status."""
    p = bot.p
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()
    sigs = {}
    for sym in p.trade_symbols:
        st = bot.states.get(sym)
        f = bot._get_cached_features(sym) or bot._compute_features(sym)
        if not st or not f:
            continue
        triggered = []
        if btc_f.get("btc_30d", 0) > p.s1_btc_30d_min_bps:
            triggered.append("S1:LONG")
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= p.s5_div_threshold and sd["vol_z"] >= p.s5_vol_z_min:
            triggered.append(f"S5:{'LONG' if sd['divergence'] > 0 else 'SHORT'}")
        if (f.get("drawdown", 0) < p.s8_drawdown_thresh
                and f.get("vol_z", 0) > p.s8_vol_z_min
                and f.get("ret_24h", 0) < p.s8_ret_24h_thresh
                and btc_f.get("btc_7d", 0) < p.s8_btc_7d_thresh):
            triggered.append("S8:LONG")
        oi_f = bot._compute_oi_features(sym)
        crowd = bot._compute_crowding_score(sym, oi_f=oi_f)
        pos = bot.positions.get(sym)
        sigs[sym] = {
            "price": st.price, "ret_7d_bps": round(f.get("ret_42h", 0), 1),
            "vol_ratio": round(f.get("vol_ratio", 0), 2),
            "range_bps": round(f.get("range_pct", 0), 0),
            "sector": bot.token_sector.get(sym, "?"),
            "sector_div": round(sd["divergence"], 0) if sd else 0,
            "oi_delta_1h": oi_f["oi_delta_1h"], "funding_bps": oi_f["funding_bps"],
            "crowding": crowd, "triggered": triggered,
            "in_position": pos is not None,
            "position_strategy": pos.strategy if pos else None,
            "proximity": _signal_proximity(p, btc_f, f, sd),
        }
    return {"signals": sigs, "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "alt_index": round(alt_idx, 0)}


def build_trades_list(trades, limit: int = 50) -> list:
    tl = list(trades)
    return [t.__dict__ for t in tl[-limit:][::-1]]


# ── Counterfactual P&L — impact des interventions ────────────────────
# Trades clos AUTREMENT que par timeout naturel : on rejoue le P&L qu'aurait
# eu la position si on l'avait laissée courir jusqu'au timeout (avec plancher
# catastrophe-stop). Sert à mesurer l'impact des sorties anticipées.
_CF_EXCLUDE = frozenset({"timeout", "runner_ext", "stale_price", "retry_close"})
_RE_S9_STOP = re.compile(r"stop=(-?\d+)")
_RE_S9_R24H = re.compile(r"r24h=([+-]?\d+)")


def _parse_s9_stop(info: str, p) -> float:
    """Niveau catastrophe-stop adaptatif S9. Stocké dans signal_info
    ('… stop=-625 …', signals.py). Fallback: recalcul depuis r24h, puis défaut."""
    m = _RE_S9_STOP.search(info or "")
    if m:
        return float(m.group(1))
    m = _RE_S9_R24H.search(info or "")
    if m:
        return max(p.stop_loss_bps, -500 - abs(float(m.group(1))) / 8)
    return p.stop_loss_bps


def _cf_stop_bps(t, p) -> float:
    """Reconstruit le niveau catastrophe-stop d'un trade clos (mirroir de
    rules.effective_stop, mais stop_bps n'est pas persisté → reconstruction)."""
    if t.strategy == "S8":
        return p.stop_loss_s8
    if t.strategy == "S9":
        return _parse_s9_stop(t.signal_info, p)
    return p.stop_loss_bps


def build_intervention_impact(bot, master) -> dict:
    """Pour chaque trade clos hors timeout naturel, calcule le P&L
    contrefactuel (tenu jusqu'au timeout, plancher catastrophe-stop) et le
    delta réel−contrefactuel. Delta>0 = l'intervention a aidé."""
    p = bot.p
    now_ms = int(time.time() * 1000)
    out = []
    sum_actual = sum_cf = 0.0
    n_no_data = n_live = 0

    for t in bot.trades:
        if t.reason in _CF_EXCLUDE:
            continue
        dir_i = 1 if t.direction == "LONG" else -1
        try:
            entry_dt = datetime.fromisoformat(t.entry_time)
            exit_dt = datetime.fromisoformat(t.exit_time)
        except (ValueError, TypeError):
            continue
        hold = p.hold_hours_for(t.strategy)
        timeout_dt = entry_dt + timedelta(hours=hold)
        exit_ms = int(exit_dt.timestamp() * 1000)
        timeout_ms = int(timeout_dt.timestamp() * 1000)

        stop_bps = _cf_stop_bps(t, p)
        stop_price = t.entry_price * (1 + dir_i * stop_bps / 1e4)
        cost_bps = t.gross_bps - t.net_bps
        cf_note = None

        row = {"symbol": t.symbol, "strategy": t.strategy,
               "direction": t.direction, "reason": t.reason,
               "entry_time": t.entry_time, "exit_time": t.exit_time,
               "actual_pnl": round(t.pnl_usdt, 2), "stop_bps": round(stop_bps),
               "hold_hours": hold}

        # Garde : sortie déjà au-delà du timeout (anormal pour une intervention)
        if exit_ms >= timeout_ms:
            row.update(cf_exit_price=t.exit_price, cf_pnl=round(t.pnl_usdt, 2),
                       cf_status="n/a", delta=0.0, cf_note=cf_note)
            out.append(row)
            sum_actual += t.pnl_usdt
            sum_cf += t.pnl_usdt
            continue

        # Fenêtre de bougies 4h après la sortie réelle, jusqu'au timeout (borné à now)
        upper_ms = min(timeout_ms, now_ms)
        try:
            with master.db.lock:
                rows = master.db.conn.execute(
                    "SELECT t, close_t, h, l, c FROM candles "
                    "WHERE symbol=? AND interval='4h' AND closed=1 "
                    "AND close_t > ? AND t <= ? ORDER BY t",
                    (t.symbol, exit_ms, upper_ms)).fetchall()
        except Exception as e:
            log.warning("intervention_impact candle query failed (%s): %s",
                        t.symbol, e)
            rows = []

        cf_exit_price = None
        cf_status = "no_data"
        for _t, _ct, h, l, c in rows:
            breached = (l <= stop_price) if dir_i == 1 else (h >= stop_price)
            if breached:
                cf_exit_price = stop_price
                cf_status = "stopped"
                break
        else:
            if rows:
                if timeout_ms <= now_ms:
                    cf_exit_price = rows[-1][4]   # close de la bougie du timeout
                    cf_status = "timeout"
                    if rows[-1][1] < timeout_ms:
                        cf_note = "partial_window"
                else:
                    st = master.states.get(t.symbol)
                    cf_exit_price = (st.price if st and st.price > 0
                                     else rows[-1][4])
                    cf_status = "live"

        if cf_exit_price is None:
            row.update(cf_exit_price=None, cf_pnl=None, cf_status="no_data",
                       delta=None, cf_note=cf_note)
            out.append(row)
            n_no_data += 1
            continue

        _, _, cf_pnl = rules.compute_trade_pnl(
            dir_i, t.entry_price, cf_exit_price, t.size_usdt, cost_bps, 0.0)
        delta = t.pnl_usdt - cf_pnl
        if cf_status == "live":
            n_live += 1
        row.update(cf_exit_price=round(cf_exit_price, 6),
                   cf_pnl=round(cf_pnl, 2), cf_status=cf_status,
                   delta=round(delta, 2), cf_note=cf_note)
        out.append(row)
        sum_actual += t.pnl_usdt
        sum_cf += cf_pnl

    out.sort(key=lambda r: r["exit_time"], reverse=True)
    return {
        "summary": {
            "n_trades": len(out) - n_no_data,
            "sum_actual": round(sum_actual, 2),
            "sum_cf": round(sum_cf, 2),
            "net_impact": round(sum_actual - sum_cf, 2),
            "n_skipped_no_data": n_no_data,
            "n_live": n_live,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        },
        "trades": out,
    }


def build_pnl_curve(trades, baseline: float, perf_track_start_ts: float = 0.0,
                    total_pnl_at_reset: float = 0.0) -> dict:
    """Running balance curve (lifetime or post-soft-reset scoped)."""
    pts: list = []
    if perf_track_start_ts <= 0:
        cum = 0.0
        for t in trades:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                        "balance": round(baseline + cum, 2)})
        return {"baseline": round(baseline, 2), "points": pts}

    cum = total_pnl_at_reset
    reset_iso = datetime.fromtimestamp(perf_track_start_ts, timezone.utc).isoformat()
    pts.append({"time": reset_iso, "cum_pnl": round(cum, 2),
                "balance": round(baseline + cum, 2)})
    for t in trades:
        try:
            ts = datetime.fromisoformat(t.exit_time).timestamp()
        except (ValueError, AttributeError):
            ts = 0.0
        if ts == 0 or ts >= perf_track_start_ts:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                        "balance": round(baseline + cum, 2)})
    return {"baseline": round(baseline, 2), "points": pts}


def build_admin_summary(bots: dict, master) -> list:
    """One card per bot for the admin view — in-process calls, no proxy."""
    out = []
    for bot in bots.values():
        try:
            st = build_state_response(bot)
            price_age = (time.time() - master.last_price_fetch
                         if master.last_price_fetch else None)
            out.append({
                "id": bot.id, "label": bot.label, "mode": bot.mode,
                "online": True,
                "status": ("paused" if bot._paused else
                           ("ok" if bot.status == "running" else bot.status)),
                "paused": bot._paused,
                "version": st["version"],
                "bot_label_color": bot.color,
                "balance": st["balance"], "capital": st["capital"],
                "total_pnl": st["total_pnl"], "total_trades": st["total_trades"],
                "win_rate": st["win_rate"], "drawdown_pct": st["drawdown_pct"],
                "peak_balance": st["peak_balance"],
                "n_positions": st["n_positions"], "max_positions": st["max_positions"],
                "positions": st["positions"],
                "active_signals": st["active_signals"],
                "exchange_account": st["exchange_account"],
                "uptime_s": st["uptime_s"],
                "first_trade_date": st["first_trade_date"],
                "price_age_s": price_age,
                "paused_strats": st["paused_strategies"],
            })
        except Exception as e:
            log.exception("admin summary failed for %s", bot.id)
            out.append({"id": bot.id, "label": bot.label, "mode": bot.mode,
                        "online": False, "error": str(e)})
    return out


# ── /master supervision builders ──────────────────────────────────────


def build_master_health(master, bots: dict, bots_cfg_path: str) -> dict:
    """Niveau 1 — santé système : WS, fraîcheur par symbole, snapshot,
    couverture données (DOWNTIME, candles en DB), config pending-restart."""
    import os as _os
    now = time.time()
    freshness = {sym: round(now - st.updated_at, 1) if st.updated_at else None
                 for sym, st in master.states.items()}
    candle_cov = {}
    try:
        with master.db.lock:
            rows = master.db.conn.execute(
                """SELECT symbol, COUNT(*), MAX(close_t) FROM candles
                   WHERE closed=1 GROUP BY symbol""").fetchall()
        for sym, n, last_close in rows:
            candle_cov[sym] = {"n_closed": n,
                               "last_close_age_s": round(now - last_close / 1000)}
    except Exception:
        pass
    # Config pending : le fichier sur disque diffère-t-il de la config chargée ?
    pending = False
    try:
        from ..settings import load_bots_config
        on_disk = {c.id: c for c in load_bots_config(bots_cfg_path)}
        loaded = {b.id: b.cfg for b in bots.values()}
        pending = (set(on_disk) != set(loaded)
                   or any(on_disk[i] != loaded[i] for i in on_disk))
    except Exception:
        pending = None  # fichier invalide ou absent
    snap = master.snapshot
    db_size = 0
    try:
        db_size = _os.path.getsize(master.db.path)
    except OSError:
        pass
    return {
        "ws": {"connected": master.ws_connected,
               "reconnects": master.ws_reconnects,
               "candle_updates": master.ws_candle_updates},
        "last_price_age_s": (round(now - master.last_price_fetch, 1)
                             if master.last_price_fetch else None),
        "freshness": freshness,
        "snapshot": ({"version": snap.version,
                      "age_s": round(now - snap.ts, 1),
                      "btc_z": snap.btc_z,
                      "disp_7d": snap.cross_ctx.get("disp_7d"),
                      "disp_24h": snap.cross_ctx.get("disp_24h"),
                      "dxy_7d": snap.dxy_7d,
                      "alt_index": snap.alt_index,
                      "oi_summary": snap.oi_summary} if snap else None),
        "degraded": list(master._degraded),
        "downtime": master.last_downtime,
        "candle_coverage": candle_cov,
        "market_db_bytes": db_size,
        "config_pending_restart": pending,
        "n_bots": len(bots),
    }


def build_gates_status() -> dict:
    """Niveau 1 — santé données (ex-phase 2 observation). La phase 3
    (parallel-run vs legacy paper) a été retirée le 2026-06-12 avec le
    décommission du legacy — plus de bot legacy à comparer."""
    from ..tools.daily_report import observation_summary
    obs_line, obs_ok = observation_summary()
    return {"phase2_observation": {"ok": obs_ok, "summary": obs_line}}


def build_fleet_response(bots: dict, master) -> dict:
    """Niveau 2 — cartes par bot + expositions agrégées + courbes equity
    normalisées (% du capital initial)."""
    cards = build_admin_summary(bots, master)

    # Expositions agrégées tous bots (symbole × direction, secteur, direction)
    by_combo: dict[tuple, dict] = {}
    by_sector: dict[str, float] = {}
    by_dir = {"LONG": 0.0, "SHORT": 0.0}
    total_notional = 0.0
    total_capital = 0.0
    for bot in bots.values():
        total_capital += bot._capital + bot._total_pnl
        sector_map = bot.token_sector
        with bot._pos_lock:
            poss = list(bot.positions.values())
        for p in poss:
            dir_str = "LONG" if p.direction == 1 else "SHORT"
            key = (p.symbol, dir_str)
            slot = by_combo.setdefault(key, {"notional": 0.0, "bots": []})
            slot["notional"] += p.size_usdt
            slot["bots"].append(bot.id)
            sect = sector_map.get(p.symbol, "?")
            by_sector[sect] = by_sector.get(sect, 0.0) + p.size_usdt
            by_dir[dir_str] += p.size_usdt
            total_notional += p.size_usdt
    exposures = [{"symbol": k[0], "direction": k[1],
                  "notional": round(v["notional"], 2),
                  "n_bots": len(v["bots"]), "bots": v["bots"],
                  "concentrated": len(v["bots"]) >= 3}
                 for k, v in sorted(by_combo.items(),
                                    key=lambda kv: -kv[1]["notional"])]

    # Courbes equity normalisées
    curves = {}
    for bot in bots.values():
        pts = build_pnl_curve(list(bot.trades), bot._capital,
                              bot._perf_track_start_ts,
                              bot._total_pnl_at_perf_reset)
        cap0 = bot.cfg.capital_initial if hasattr(bot, "cfg") else bot._capital
        curve = [{"time": p["time"],
                  "pct": round((p["balance"] / cap0 - 1) * 100, 2)}
                 for p in (pts.get("points", []) if isinstance(pts, dict) else pts)]
        curves[bot.id] = {"label": bot.label, "color": bot.color,
                          "points": curve}

    return {"bots": cards,
            "exposures": exposures,
            "by_sector": {k: round(v, 2) for k, v in
                          sorted(by_sector.items(), key=lambda kv: -kv[1])},
            "by_direction": {k: round(v, 2) for k, v in by_dir.items()},
            "total_notional": round(total_notional, 2),
            "total_capital": round(total_capital, 2),
            "curves": curves}


def build_audit_trail(master, limit: int = 100) -> list:
    """Niveau 3 — journal des actions admin (table dédiée admin_audit)."""
    try:
        with master.db.lock:
            rows = master.db.conn.execute(
                """SELECT ts, ip, route, bot_id, payload, result
                   FROM admin_audit ORDER BY ts DESC LIMIT ?""",
                (limit,)).fetchall()
        return [{"ts": r[0], "ip": r[1], "route": r[2], "bot_id": r[3],
                 "payload": r[4], "result": r[5]} for r in rows]
    except Exception:
        return []
