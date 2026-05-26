"""FastAPI app, routes, auth, and API response builders."""
from __future__ import annotations
import hashlib, hmac, json, logging, math, os, time, secrets as _secrets

ROOT_PATH = os.environ.get("HL_ROOT_PATH", "")  # e.g. "/bot" when behind nginx subpath
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .config import (
    VERSION, EXECUTION_MODE, BOT_LABEL, BOT_LABEL_COLOR,
    CAPITAL_USDT, JUNIOR_CAPITAL_CAP, LEVERAGE, DASHBOARD_USER,
    DASHBOARD_PASS, AUTH_SALT, HTML_PATH, CHANGELOG_PATH, BACKTESTS_PATH, TRADE_SYMBOLS, TOKEN_SECTOR,
    MAX_POSITIONS, SCAN_INTERVAL,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, COST_BPS, STOP_LOSS_BPS, STOP_LOSS_S8,
    OI_LONG_GATE_BPS, TRADE_BLACKLIST, S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
    MACRO_STRATEGIES, MAX_SAME_DIRECTION, MAX_PER_SECTOR, MAX_MACRO_SLOTS,
    MAX_TOKEN_SLOTS, COOLDOWN_HOURS,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH,
)
from .concurrency import db_lock as _db_lock
from .features import oi_delta_24h_bps

def _mode_label() -> str:
    """Display label (BOT_LABEL override, else PAPER/LIVE from EXECUTION_MODE)."""
    if BOT_LABEL:
        return BOT_LABEL
    return "LIVE" if EXECUTION_MODE == "live" else "PAPER"

def _mode_color() -> str:
    """Color for the top border / tag (BOT_LABEL_COLOR override or defaults)."""
    if BOT_LABEL_COLOR:
        return BOT_LABEL_COLOR
    return "#da3633" if EXECUTION_MODE == "live" else "#58a6ff"

log = logging.getLogger("multisignal")

# DRY: shared helpers live in trading.py
from .analytics import (is_bot_trade, compute_signal_drift, compute_s10_health,
                         estimate_win_prob, filter_recent_trades)
from .trading import signal_skip_reason
from .net import send_telegram

def _collect_active_signals(bot, btc_f) -> list:
    """Scan all symbols and return active signal descriptions for the dashboard."""
    active = []
    if btc_f.get("btc_30d", 0) > 2000:
        active.append(f"S1: BTC 30d = {btc_f['btc_30d']:+.0f}bps \u2192 LONG")
    s5, s8, s9, s10 = [], [], [], []
    for sym in TRADE_SYMBOLS:
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
            s5.append(f"{sym}({'L' if sd['divergence'] > 0 else 'S'})")
        f = bot._get_cached_features(sym) or bot._compute_features(sym)
        if f:
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                s8.append(sym)
            if abs(f.get("ret_24h", 0)) >= S9_RET_THRESH:
                s9.append(f"{sym}({'S' if f['ret_24h'] > 0 else 'L'})")
        sq = bot._detect_squeeze(sym)
        if sq:
            s10.append(f"{sym}({'L' if sq['direction'] == 1 else 'S'})")
    if s5:  active.append(f"S5: {', '.join(s5[:5])} sector divergence")
    if s8:  active.append(f"S8: {', '.join(s8[:5])} capitulation flush")
    if s9:  active.append(f"S9: {', '.join(s9[:5])} fade extreme")
    if s10: active.append(f"S10: {', '.join(s10[:5])} squeeze")
    return active

# ── Response Builders ─────────────────────────────────────────────────

def build_daily_summary(bot) -> str:
    """Build the daily summary message string. Caller sends via Telegram.

    Mirrors the dashboard's first two cards (Equity + P&L) and lists a brief
    detail of each open position.
    """
    today = datetime.now(timezone.utc).isoformat()[:10]
    cap = bot._capital
    realized = bot._total_pnl
    # Card 1 — Equity: internal accounting (capital + realized + sum of open
    # positions' unrealized at current price). v12.5.13 switch: was previously
    # reading bot._exchange_account.equity, which can be transiently incorrect
    # when HL's two info APIs (user_state vs spot_user_state) return
    # desynchronised data. The internal formula is deterministic, refreshes on
    # every /api/state call from the latest market price, and won't show
    # phantom jumps. HL equity is kept in exchange_account for the drift alert
    # system and is exposed separately as `hl_equity` for cross-checking.
    acct = bot._exchange_account
    now_eq = datetime.now(timezone.utc)
    sum_unreal = 0.0
    with bot._pos_lock:
        _pos_iter = list(bot.positions.values())
    for _p in _pos_iter:
        _st = bot.states.get(_p.symbol)
        _px = _st.price if _st and _st.price > 0 else _p.entry_price
        if _p.entry_price > 0:
            _ur = _p.direction * (_px / _p.entry_price - 1) * 1e4
            sum_unreal += _p.size_usdt * _ur / 1e4
    equity = cap + realized + sum_unreal
    equity_label = "Equity"
    equity_delta = equity - cap
    equity_pct = (equity_delta / cap * 100) if cap > 0 else 0
    hl_equity = float(acct["equity"]) if acct and "equity" in acct else None
    # Card 2 — Total P&L: realized + percentage
    ab = [t for t in bot.trades if is_bot_trade(t)]
    n = len(ab)
    wr = (sum(1 for t in ab if t.pnl_usdt > 0) / n * 100) if n else 0
    pnl_pct = (realized / cap * 100) if cap > 0 else 0
    # Open positions brief detail
    now = datetime.now(timezone.utc)
    with bot._pos_lock:
        pos_snapshot = dict(bot.positions)
    lines = []
    for sym, pos in sorted(pos_snapshot.items(), key=lambda kv: kv[0]):
        st = bot.states.get(sym)
        px = st.price if st and st.price > 0 else pos.entry_price
        ur = pos.direction * (px / pos.entry_price - 1) * 1e4 if pos.entry_price > 0 else 0
        pnl_pos = pos.size_usdt * ur / 1e4
        rem_h = max(0, (pos.target_exit - now).total_seconds() / 3600)
        direction = "LONG" if pos.direction == 1 else "SHORT"
        lines.append(f"  \u2022 {sym} {direction} {pos.strategy} | "
                     f"{ur:+.0f} bps (${pnl_pos:+.2f}) | {rem_h:.0f}h left")
    pos_block = ("\n".join(lines)) if lines else "  (none)"
    return (f"\U0001f4ca Daily {today}\n"
            f"\U0001f4b0 {equity_label}: ${equity:.2f} "
            f"({equity_delta:+.2f} / {equity_pct:+.1f}% on ${cap:.0f})\n"
            f"\U0001f4c8 P&L: ${realized:+.2f} ({pnl_pct:+.1f}%) | "
            f"{wr:.0f}% win on {n}\n"
            f"\U0001f4cc Open ({len(pos_snapshot)}):\n{pos_block}")

def build_state_response(bot) -> dict:
    """Full dashboard state dict."""
    now = datetime.now(timezone.utc)
    bt = [t for t in bot.trades if is_bot_trade(t)]
    n_bot, wins = len(bt), sum(1 for t in bt if t.pnl_usdt > 0)
    balance = bot._capital + bot._total_pnl
    # v12.5.13: deterministic equity = balance + sum of unrealized at current
    # price. Computed here so the dashboard never sees the HL transient races
    # that affect bot._exchange_account.equity.
    _sum_unreal = 0.0
    with bot._pos_lock:
        _pos_iter = list(bot.positions.values())
    for _p in _pos_iter:
        _st = bot.states.get(_p.symbol)
        _px = _st.price if _st and _st.price > 0 else _p.entry_price
        if _p.entry_price > 0:
            _ur = _p.direction * (_px / _p.entry_price - 1) * 1e4
            _sum_unreal += _p.size_usdt * _ur / 1e4
    equity = balance + _sum_unreal
    _acct = bot._exchange_account
    hl_equity = float(_acct["equity"]) if _acct and "equity" in _acct else None
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()
    # Regime classifier (v12.5.9): aligned with the adaptive modulator's
    # btc_z (rolling z-score) instead of raw return thresholds. Fallback to
    # raw-return logic when btc_z hasn't been computed yet (cold boot).
    # Stress always wins as override.
    from . import signals as signals_mod
    cross = signals_mod.compute_cross_context(bot._feature_cache)
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
        # Cold boot fallback (no btc_z yet) — use legacy raw-return thresholds
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
    positions = []
    with bot._pos_lock:
        pos_snapshot = dict(bot.positions)
    # I4: filter trades once per /api/state call instead of per-position.
    # The estimate_win_prob signature accepts pre_filtered=True to skip its
    # internal cutoff filter (which would otherwise run len(positions) times).
    recent_trades = filter_recent_trades(list(bot.trades))
    for sym, pos in pos_snapshot.items():
        st = bot.states.get(sym)
        px = st.price if st else pos.entry_price
        # size_usdt is notional (passed to execute_open as sz = size_usdt/price)
        # so unrealized = direction * price_change_pct * size_usdt (no extra leverage)
        ur = pos.direction * (px / pos.entry_price - 1) * 1e4 if pos.entry_price > 0 else 0
        rem = max(0, (pos.target_exit - now).total_seconds() / 3600)
        hold_h = round((now - pos.entry_time).total_seconds() / 3600, 1)
        # Effective stop: S8 uses STOP_LOSS_S8, S9 has per-position adaptive stop
        if pos.strategy == "S8":
            effective_stop = STOP_LOSS_S8
        elif pos.stop_bps != 0:
            effective_stop = pos.stop_bps
        else:
            effective_stop = STOP_LOSS_BPS
        # 0.0 = at entry or in profit; 1.0 = at stop; clamps to [0, 1]
        stop_progress = max(0.0, min(1.0, -ur / abs(effective_stop))) if effective_stop < 0 else 0.0
        total_hold = hold_h + rem
        hold_progress = round(hold_h / total_hold, 3) if total_hold > 0 else 0.0
        # v12.3.0 — historical-pattern win probability estimate
        # v12.3.2: pass hours_held + hold_target so the maturity gate can mute
        # noisy early-hold MAE-based adjustments.
        win_prob = estimate_win_prob(pos, recent_trades,
                                      hours_held=hold_h,
                                      hold_target_h=hold_h + max(rem, 0),
                                      pre_filtered=True,
                                      current_ur_bps=ur)
        positions.append({
            "symbol": sym, "direction": "LONG" if pos.direction == 1 else "SHORT",
            "strategy": pos.strategy, "entry_price": pos.entry_price,
            "current_price": px, "size_usdt": pos.size_usdt,
            "signal_info": pos.signal_info, "unrealized_bps": round(ur, 1),
            "pnl_usdt": round(pos.size_usdt * ur / 1e4, 2),
            "hold_hours": hold_h,
            "remaining_hours": round(rem, 1),
            "remaining": f"{int(rem)}h{int((rem % 1) * 60):02d}m",
            "mae_bps": round(pos.mae_bps, 1), "mfe_bps": round(pos.mfe_bps, 1),
            "stop_bps": round(effective_stop, 0),
            "stop_progress": round(stop_progress, 3),
            "hold_progress": hold_progress,
            "trajectory": list(pos.trajectory),
            # S10 trailing: exit when unrealized drops below mfe - offset (if mfe >= trigger)
            "trailing_active": bool(pos.strategy == "S10" and pos.mfe_bps >= S10_TRAILING_TRIGGER),
            "trailing_floor_bps": round(pos.mfe_bps - S10_TRAILING_OFFSET, 0)
                                   if pos.strategy == "S10" and pos.mfe_bps >= S10_TRAILING_TRIGGER else None,
            "win_prob": win_prob,
            "manual_stop_usdt": round(pos.manual_stop_usdt, 2)
                                if pos.manual_stop_usdt is not None else None,
        })
    # OI delta 24h per token (for dashboard gauge + gate visualization)
    oi_deltas = {}
    for _sym in TRADE_SYMBOLS:
        _st = bot.states.get(_sym)
        if _st:
            _d = oi_delta_24h_bps(_st.oi_history)
            if _d is not None:
                oi_deltas[_sym] = round(_d, 0)
    # Scan preview (batch 3, #9): for each token currently firing a signal,
    # report what the bot would do right now: ENTER / SKIP with reason.
    preview: list = []
    n_long = sum(1 for p in pos_snapshot.values() if p.direction == 1)
    n_short = sum(1 for p in pos_snapshot.values() if p.direction == -1)
    n_macro = sum(1 for p in pos_snapshot.values() if p.strategy in MACRO_STRATEGIES)
    n_token_sig = sum(1 for p in pos_snapshot.values() if p.strategy not in MACRO_STRATEGIES)
    sector_counts: dict[str, int] = {}
    for p in pos_snapshot.values():
        _s = TOKEN_SECTOR.get(p.symbol)
        if _s:
            sector_counts[_s] = sector_counts.get(_s, 0) + 1
    # Shared ctx for signal_skip_reason — same source of truth as rank_and_enter.
    _disp_24h = cross.get("disp_24h")
    _skip_ctx = dict(n_total=len(pos_snapshot), n_longs=n_long, n_shorts=n_short,
                     n_macro=n_macro, n_token=n_token_sig,
                     sector_counts=sector_counts, disp_24h=_disp_24h)

    # S1: macro-wide signal, no per-token state. Emit once with synthetic symbol.
    if btc_f.get("btc_30d", 0) > 2000:
        _s1 = {"symbol": "ALTS", "strategy": "S1", "direction": 1}
        _r = signal_skip_reason(bot, _s1, **_skip_ctx)
        preview.append({"symbol": "ALTS", "strategy": "S1", "direction": "LONG",
                        "status": _r or "would enter"})

    for _sym in TRADE_SYMBOLS:
        _st = bot.states.get(_sym)
        _f = bot._get_cached_features(_sym) if hasattr(bot, "_get_cached_features") else None
        if not _st or not _f:
            continue
        # Re-detect signals from cached features (same thresholds as signals.py).
        _fires: list = []
        _sd = bot._compute_sector_divergence(_sym) if hasattr(bot, "_compute_sector_divergence") else None
        if _sd and abs(_sd["divergence"]) >= S5_DIV_THRESHOLD and _sd["vol_z"] >= S5_VOL_Z_MIN:
            _fires.append(("S5", 1 if _sd["divergence"] > 0 else -1))
        if (_f.get("drawdown", 0) < S8_DRAWDOWN_THRESH and _f.get("vol_z", 0) > S8_VOL_Z_MIN
                and _f.get("ret_24h", 0) < S8_RET_24H_THRESH
                and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
            _fires.append(("S8", 1))
        _ret24 = _f.get("ret_24h", 0)
        if abs(_ret24) >= S9_RET_THRESH:
            _fires.append(("S9", -1 if _ret24 > 0 else 1))
        if hasattr(bot, "_detect_squeeze"):
            _sq = bot._detect_squeeze(_sym)
            if _sq:
                from .config import S10_ALLOW_LONGS as _SAL, S10_ALLOWED_TOKENS as _SAT
                if not ((not _SAL and _sq["direction"] == 1) or _sym not in _SAT):
                    _fires.append(("S10", _sq["direction"]))
        for _strat, _dir in _fires:
            _sig = {"symbol": _sym, "strategy": _strat, "direction": _dir}
            _reason = signal_skip_reason(bot, _sig, **_skip_ctx)
            preview.append({"symbol": _sym, "strategy": _strat,
                            "direction": "LONG" if _dir == 1 else "SHORT",
                            "status": _reason or "would enter"})

    # Sort so "would enter" at top, then by strategy priority
    _prio = {"would enter": 0, "already in position": 1}
    preview.sort(key=lambda x: (_prio.get(x["status"], 2), x["strategy"]))

    # Sector stats (batch 2): positions + unrealized + avg 24h return per sector
    sector_stats: dict[str, dict] = {}
    for _sym, _sect in TOKEN_SECTOR.items():
        s = sector_stats.setdefault(_sect, {"n_tokens": 0, "n_positions": 0,
                                            "unrealized_pnl": 0.0, "ret_24h_sum": 0.0,
                                            "ret_24h_n": 0})
        s["n_tokens"] += 1
        _f = bot._get_cached_features(_sym) if hasattr(bot, "_get_cached_features") else None
        if _f and _f.get("ret_24h") is not None:
            s["ret_24h_sum"] += _f["ret_24h"]
            s["ret_24h_n"] += 1
    for _p in positions:
        _sect = TOKEN_SECTOR.get(_p["symbol"])
        if _sect and _sect in sector_stats:
            sector_stats[_sect]["n_positions"] += 1
            sector_stats[_sect]["unrealized_pnl"] += _p["pnl_usdt"]
    # Finalize: avg return, round
    for _sect, s in sector_stats.items():
        s["avg_ret_24h_bps"] = round(s["ret_24h_sum"] / s["ret_24h_n"], 0) if s["ret_24h_n"] else 0
        s["unrealized_pnl"] = round(s["unrealized_pnl"], 2)
        del s["ret_24h_sum"], s["ret_24h_n"]
    return {
        "version": VERSION, "strategy": "Multi-Signal (S1+S5+S8+S9+S10)",
        "execution_mode": EXECUTION_MODE,
        "bot_label": _mode_label(), "bot_label_color": _mode_color(),
        "paused": bot._paused, "running": bot.running,
        "degraded": list(bot._degraded), "loss_streak": bot._consecutive_losses,
        "balance": round(balance, 2), "capital": bot._capital,
        # Capital cap exposed for the DCA dashboard widget. Only Junior bot
        # has a hard cap (JUNIOR_CAPITAL_CAP); Live/Paper have no limit.
        "capital_cap": (JUNIOR_CAPITAL_CAP if BOT_LABEL == "JUNIOR" and JUNIOR_CAPITAL_CAP > 0 else None),
        "equity": round(equity, 2),  # v12.5.13 = balance + sum_unrealized (deterministic)
        "hl_equity": round(hl_equity, 2) if hl_equity is not None else None,
        "exchange_account": bot._exchange_account,  # real exchange balance (live only)
        "total_pnl": round(bot._total_pnl, 2), "total_trades": n_bot,
        "win_rate": round(wins / n_bot, 3) if n_bot > 0 else 0,
        "n_positions": len(pos_snapshot), "max_positions": MAX_POSITIONS,
        "positions": positions, "active_signals": _collect_active_signals(bot, btc_f),
        "oi_deltas_24h": oi_deltas, "oi_gate_bps": OI_LONG_GATE_BPS,
        "blacklist": sorted(TRADE_BLACKLIST),
        "regime": regime, "regime_stress": n_stress,
        "sector_stats": sector_stats,
        "preview": preview,
        "market": {
            "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "btc_7d": round(btc_f.get("btc_7d", 0), 0),
            "alt_index_7d": round(alt_idx, 0), "dxy_7d": round(bot._dxy_cache[0], 0),
            "oi_falling": bot._oi_summary["falling"],
            "oi_rising": bot._oi_summary["rising"],
        },
        "params": {"hold_h": HOLD_HOURS_DEFAULT, "hold_s5_h": HOLD_HOURS_S5,
                   "cost_bps": COST_BPS, "stop_bps": STOP_LOSS_BPS,
                   "max_pos": MAX_POSITIONS},
        "uptime_s": (now - bot.started_at).total_seconds() if bot.started_at else 0,
        "started_at": bot.started_at.isoformat() if bot.started_at else None,
        "first_trade_date": bot.trades[0].entry_time if bot.trades else None,
        "last_price_s": time.time() - bot._last_price_fetch if bot._last_price_fetch else None,
        "last_scan_s": time.time() - bot._last_scan if bot._last_scan else None,
        "next_scan_s": max(0, SCAN_INTERVAL - (time.time() - bot._last_scan)) if bot._last_scan else 0,
        "scan_interval": SCAN_INTERVAL,
        "signal_drift": compute_signal_drift(bot.trades),
        "s10_health": compute_s10_health(bot.trades),
        "btc_z_30d": round(bot._btc_z, 3) if bot._btc_z is not None else None,
        "basket_metrics": bot._basket_metrics,
        "peak_balance": round(bot._peak_balance, 2),
        "drawdown_pct": round((balance - bot._peak_balance) / bot._peak_balance * 100, 2) if bot._peak_balance > 0 else 0,
        "pnl_pct": round((balance - bot._capital) / bot._capital * 100, 2) if bot._capital > 0 else 0,
        "capital_utilization_pct": round(sum(p.size_usdt / LEVERAGE for p in pos_snapshot.values()) / max(balance, 1) * 100, 1),
    }

def _signal_proximity(btc_f, f, sd) -> dict:
    """Per-strategy activation score 0..1 (1 = firing right now).

    Distance from firing threshold normalized to [0, 1]. For multi-condition
    signals (S8), the score is min across conditions (all must be met).
    """
    btc30 = btc_f.get("btc_30d", 0)
    btc7 = btc_f.get("btc_7d", 0)
    ret_24h = f.get("ret_24h", 0)
    vol_z = f.get("vol_z", 0)
    drawdown = f.get("drawdown", 0)
    vol_ratio = f.get("vol_ratio", 2.0)

    # S1: BTC 30d > 2000 bps (global, not per-token)
    s1 = min(1.0, max(0.0, btc30 / 2000.0))
    # S5: need divergence + vol_z both above threshold
    if sd:
        div_prox = min(1.0, abs(sd["divergence"]) / S5_DIV_THRESHOLD)
        volz_prox = min(1.0, max(0.0, sd["vol_z"] / S5_VOL_Z_MIN))
        s5 = min(div_prox, volz_prox)
    else:
        s5 = 0.0
    # S8: 4 conditions — drawdown, vol_z, ret_24h, btc_7d (all must fire; clamp each to [0,1])
    s8 = min(
        min(1.0, max(0.0, drawdown / S8_DRAWDOWN_THRESH)) if S8_DRAWDOWN_THRESH != 0 else 0.0,
        min(1.0, max(0.0, vol_z / S8_VOL_Z_MIN)),
        min(1.0, max(0.0, ret_24h / S8_RET_24H_THRESH)) if S8_RET_24H_THRESH != 0 else 0.0,
        min(1.0, max(0.0, btc7 / S8_BTC_7D_THRESH)) if S8_BTC_7D_THRESH != 0 else 0.0,
    )
    # S9: |ret_24h| / threshold
    s9 = min(1.0, abs(ret_24h) / S9_RET_THRESH)
    # S10: normalize so vol_ratio == S10_VOL_RATIO_MAX (0.9) → 1.0 (firing), higher = further away
    s10 = min(1.0, max(0.0, (1.5 - vol_ratio) / 0.6)) if vol_ratio > 0 else 0.0
    return {"S1": round(s1, 2), "S5": round(s5, 2), "S8": round(s8, 2),
            "S9": round(s9, 2), "S10": round(s10, 2)}


def build_signals_response(bot) -> dict:
    """All symbols with their current features and signal status."""
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()
    signals = {}
    for sym in TRADE_SYMBOLS:
        st = bot.states.get(sym)
        f = bot._get_cached_features(sym) or bot._compute_features(sym)
        if not st or not f:
            continue
        triggered = []
        if btc_f.get("btc_30d", 0) > 2000:
            triggered.append("S1:LONG")
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
            triggered.append(f"S5:{'LONG' if sd['divergence'] > 0 else 'SHORT'}")
        if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH and f.get("vol_z", 0) > S8_VOL_Z_MIN
                and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
            triggered.append("S8:LONG")
        oi_f = bot._compute_oi_features(sym)
        crowd = bot._compute_crowding_score(sym, oi_f=oi_f)
        pos = bot.positions.get(sym)
        signals[sym] = {
            "price": st.price, "ret_7d_bps": round(f.get("ret_42h", 0), 1),
            "vol_ratio": round(f.get("vol_ratio", 0), 2),
            "range_bps": round(f.get("range_pct", 0), 0),
            "sector": TOKEN_SECTOR.get(sym, "?"),
            "sector_div": round(sd["divergence"], 0) if sd else 0,
            "oi_delta_1h": oi_f["oi_delta_1h"], "funding_bps": oi_f["funding_bps"],
            "crowding": crowd, "triggered": triggered,
            "in_position": pos is not None,
            "position_strategy": pos.strategy if pos else None,
            "proximity": _signal_proximity(btc_f, f, sd),
        }
    return {"signals": signals, "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "alt_index": round(alt_idx, 0)}

def build_trades_list(trades, limit: int = 50) -> list:
    """Return recent trades, newest first. Converts deque to list (no slicing on deque)."""
    tl = list(trades)
    return [t.__dict__ for t in tl[-limit:][::-1]]

def build_pnl_curve(trades, capital: float) -> list:
    """Cumulative P&L curve for the dashboard chart.

    `balance` uses the *initial* CAPITAL_USDT constant as baseline rather than
    the live bot._capital — this keeps historical balance points consistent
    after DCA injections. DCA itself shifts the current reference, not the
    historical curve.
    """
    _ = capital  # accepted for backwards compat but unused; baseline is CAPITAL_USDT
    cum, pts = 0.0, []
    for t in trades:
        cum += t.pnl_usdt
        pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                    "balance": round(CAPITAL_USDT + cum, 2)})
    return pts

_BACKTESTS_TAIL = """## Méthodologie

- Source : 4h candles Hyperliquid, 28 tokens tradés + BTC/ETH en référence.
- Entry timing : open de la bougie suivante (no look-ahead).
- Exit : stop / timeout selon la configuration courante du bot.
- Positions restantes en fin de fenêtre : mark-to-market au dernier close.
- Coût de transaction round-trip appliqué à chaque trade ; pas de multiplication par le levier.

## Limites

- Le backtest n'utilise que les bougies 4h ; certaines features live-only (book depth, ticks 60s) ne sont pas modélisées.
- Pas de modélisation du slippage variable selon la liquidité du carnet — coût fixe.
- Pas de modélisation du funding variable — coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées. Prendre les résultats avec précaution.
"""


def sanitize_backtests_md(content: str) -> str:
    """Strip strategy-revealing details from docs/backtests.md for the
    dashboard modal. Keeps:
      - The header (date, version, data-through, capitals tested)
      - The intro paragraph
      - The "Résumé par fenêtre" table (with "Best strat" column dropped)

    Drops everything else — Filtres actifs, Breakdown par stratégie,
    detailed Méthodologie / Limites — and replaces the trailing material
    with a fixed sanitized methodology + limits paragraph (`_BACKTESTS_TAIL`).
    This guarantees no parameter name, source script, threshold, token
    whitelist or per-strategy P&L can leak through.
    """
    lines = content.split("\n")
    out: list[str] = []
    state = "header"  # header → skip → summary → done
    best_strat_col: int | None = None  # index in the |-split, set on header detection

    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if any(k in heading for k in ("résumé", "summary")):
                state = "summary"
                out.append(line)
                continue
            else:
                # Any non-summary heading: if we were in summary, we're done.
                # If we were in header, we hit a section we want to skip.
                if state == "summary":
                    state = "done"
                    break
                state = "skip"
                continue

        if state == "skip":
            continue
        if state == "summary":
            # Inside summary table: drop the "Best strat" column.
            # The header is parsed once to locate the column, then the same
            # index is dropped from every subsequent row (separator + data).
            # Keeps the sanitizer robust to new strategy names (S11, S12…).
            if line.startswith("|"):
                cols = line.split("|")
                if best_strat_col is None and "Best strat" in line:
                    for i, c in enumerate(cols):
                        if "Best strat" in c:
                            best_strat_col = i
                            break
                if best_strat_col is not None and 0 <= best_strat_col < len(cols):
                    cols = cols[:best_strat_col] + cols[best_strat_col + 1:]
                    line = "|".join(cols)
            out.append(line)
        else:  # state == "header"
            # Drop the auto-regen instruction (mentions backtest_rolling source)
            if "backtest_rolling" in line or "régénéré automatiquement" in line:
                continue
            # Drop the detailed costs breakdown line
            if "13 bps" in line or "round-trip" in line:
                continue
            out.append(line)

    text = "\n".join(out).rstrip() + "\n\n" + _BACKTESTS_TAIL
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


# ── FastAPI App Factory ───────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Trading Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:360px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.login-box h1{font-size:20px;margin-bottom:8px;text-align:center}
.login-box .sub{color:#7d8590;font-size:13px;text-align:center;margin-bottom:24px}
.login-box label{display:block;font-size:13px;color:#7d8590;margin-bottom:4px}
.login-box input[type=text],.login-box input[type=password]{
  width:100%;padding:10px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;
  color:#e6edf3;font-size:15px;margin-bottom:16px;outline:none;transition:border .2s}
.login-box input:focus{border-color:#58a6ff}
.login-box button{width:100%;padding:10px;background:#238636;color:#fff;border:none;border-radius:6px;
  font-size:15px;font-weight:600;cursor:pointer;transition:background .2s}
.login-box button:hover{background:#2ea043}
.error{background:#da363322;border:1px solid #da363388;color:#f85149;padding:8px 12px;
  border-radius:6px;margin-bottom:16px;font-size:13px;text-align:center;display:none}
</style>
</head><body>
<form class="login-box" method="POST" action="login" autocomplete="on">
  <h1>Trading Bot</h1>
  <div class="sub">{{VERSION}} — {{MODE}}</div>
  <div class="error" id="err">{{ERROR}}</div>
  <label for="username">Username</label>
  <input type="text" id="username" name="username" autocomplete="username" required autofocus>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
</form>
<script>if(document.getElementById('err').textContent.trim())document.getElementById('err').style.display='block'</script>
</body></html>"""


def create_app(bot) -> FastAPI:
    """Create the FastAPI app with all routes wired to *bot*."""
    # ── Stateless signed sessions (survive restarts) ──
    # AUTH_SALT adds entropy so a leaked cookie does not allow offline password
    # brute-force (attacker would need the salt too, which lives only in .env).
    # If the salt changes, all existing sessions become invalid.
    # If USER is set but PASS is empty, _SECRET would degenerate to b"" and
    # compare_digest("", "") would accept any login with an empty password —
    # complete auth bypass. Refuse to start the app in that misconfiguration.
    if DASHBOARD_USER and not DASHBOARD_PASS:
        raise RuntimeError("DASHBOARD_USER is set but DASHBOARD_PASS is empty — "
                           "refusing to start (auth would accept any password)")
    _SECRET = (hashlib.sha256((DASHBOARD_PASS + AUTH_SALT).encode()).digest()
               if DASHBOARD_PASS else b"")
    _SESSION_MAX_AGE = 30 * 86400
    # Exponential backoff per IP: each failed login doubles the required delay
    # before the next attempt from the same IP (1s → 2s → 4s → ... up to 300s).
    # Stored as (fail_count, last_failed_ts) per IP. State is in-memory only;
    # a bot restart resets counters — acceptable given realistic restart cadence.
    _login_failures: dict[str, tuple[int, float]] = {}
    _BACKOFF_BASE = 1.0   # seconds after 1st failure
    _BACKOFF_MAX = 300.0  # cap at 5 min
    _BACKOFF_RESET = 3600 # success or long idle clears the counter
    # v12.5.29: server-side session revocation epoch. /logout bumps this; any
    # session token signed before _revoked_before is rejected on subsequent
    # requests. Without this, /logout only deletes the cookie client-side and
    # a stolen cookie remains valid for 30 days regardless. Single-user model
    # → one epoch covers all sessions.
    _revoked_before: dict[str, float] = {"ts": 0.0}
    # v12.5.29: rate-limit per IP for mutating endpoints (close, manual_stop,
    # capital, pause, resume, reset). Authenticated callers are not infinite;
    # protect against a stolen cookie spamming /api/reset or /api/close.
    _MUT_LIMIT_PER_MIN = 30
    _MUT_WINDOW = 60.0
    _mutation_log: dict[str, deque] = {}

    def _sign_token(ts: float) -> str:
        msg = str(int(ts)).encode()
        sig = hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()[:16]
        return f"{int(ts)}:{sig}"

    def _verify_token(token: str) -> bool:
        if not token or ":" not in token:
            return False
        parts = token.split(":", 1)
        if len(parts) != 2:
            return False
        ts_str, sig = parts
        try:
            ts = int(ts_str)
        except ValueError:
            return False
        if time.time() - ts > _SESSION_MAX_AGE:
            return False
        # v12.5.29: reject tokens issued before the current revocation epoch.
        if ts < _revoked_before["ts"]:
            return False
        expected = hmac.new(_SECRET, ts_str.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected)

    def _client_ip(request: Request) -> str:
        """Trusted client IP — XFF only honored if direct peer is localhost (nginx)."""
        xff = request.headers.get("x-forwarded-for", "")
        direct_ip = request.client.host if request.client else "unknown"
        return xff.split(",")[0].strip() if xff and direct_ip == "127.0.0.1" else direct_ip

    def _check_mutation_rate(ip: str) -> bool:
        """True = allowed. False = over the per-minute mutation budget."""
        now = time.time()
        dq = _mutation_log.get(ip)
        if dq is None:
            # v12.5.31: lazy prune of idle entries when adding a new IP, so
            # _mutation_log can't grow unbounded across the bot's lifetime.
            # Drop deques that are empty or whose newest mutation is older
            # than 10× the rate window — those IPs are no longer rate-limited
            # by any active record, the entry is dead memory.
            if _mutation_log:
                stale = [k for k, v in _mutation_log.items()
                         if not v or now - v[-1] > _MUT_WINDOW * 10]
                for k in stale:
                    _mutation_log.pop(k, None)
            dq = deque(maxlen=_MUT_LIMIT_PER_MIN * 4)
            _mutation_log[ip] = dq
        while dq and now - dq[0] > _MUT_WINDOW:
            dq.popleft()
        if len(dq) >= _MUT_LIMIT_PER_MIN:
            return False
        dq.append(now)
        return True

    def _backoff_delay(ip: str) -> float:
        """Returns seconds remaining before this IP may attempt login again."""
        rec = _login_failures.get(ip)
        if not rec:
            return 0.0
        n_fails, last_ts = rec
        if time.time() - last_ts > _BACKOFF_RESET:
            # long idle: clear the counter
            _login_failures.pop(ip, None)
            return 0.0
        required = min(_BACKOFF_BASE * (2 ** (n_fails - 1)), _BACKOFF_MAX)
        elapsed = time.time() - last_ts
        return max(0.0, required - elapsed)

    def _record_failure(ip: str) -> None:
        n_fails, _ = _login_failures.get(ip, (0, 0.0))
        _login_failures[ip] = (n_fails + 1, time.time())

    def _record_success(ip: str) -> None:
        _login_failures.pop(ip, None)

    app = FastAPI(root_path=ROOT_PATH)
    _html_cache: dict[str, str | None] = {"v": None}

    # ── Security headers on every response ──
    from starlette.middleware.base import BaseHTTPMiddleware

    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
            # Allow unpkg.com for LightweightCharts lib + inline scripts/styles
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
            # HSTS only over HTTPS (detected via X-Forwarded-Proto from nginx)
            if request.headers.get("x-forwarded-proto") == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
            return response

    # v12.5.29: per-IP rate-limit on mutating endpoints (POST). Prevents a
    # stolen-cookie attacker from spamming /api/reset, /api/close, etc.
    # Read endpoints (/api/state, /api/trades…) are unaffected.
    class _MutationRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                ip = _client_ip(request)
                if not _check_mutation_rate(ip):
                    log.warning("MUTATION RATE LIMIT: ip=%s path=%s", ip, request.url.path)
                    return JSONResponse(
                        {"error": "rate limit exceeded — slow down"},
                        status_code=429)
            return await call_next(request)

    if DASHBOARD_USER:
        class _AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                if path in ("/login", "/favicon.ico") or path.startswith("/auth"):
                    return await call_next(request)
                token = request.cookies.get("session")
                if not token or not _verify_token(token):
                    if path.startswith("/api/"):
                        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
                    return RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
                return await call_next(request)
        app.add_middleware(_AuthMiddleware)

    # Middlewares run outer-first; add rate-limit LAST so it lives inside
    # auth (rate-limit applies only to authenticated requests).
    app.add_middleware(_MutationRateLimitMiddleware)
    app.add_middleware(_SecurityHeadersMiddleware)

    @app.get("/auth")
    async def auth_bridge(token: str = ""):
        """Auto-login via signed token (used by admin panel 'Open' button)."""
        if _verify_token(token):
            resp = RedirectResponse(f"{ROOT_PATH}/", status_code=303)
            resp.set_cookie("session", token, httponly=True, samesite="strict", max_age=30 * 86400)
            return resp
        return RedirectResponse(f"{ROOT_PATH}/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        ml = _mode_label()
        return _LOGIN_HTML.replace("{{VERSION}}", VERSION).replace("{{MODE}}", ml).replace("{{ERROR}}", "")

    @app.post("/login")
    async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        ml = _mode_label()
        # Trust X-Forwarded-For only for known proxies (nginx on localhost).
        # Falls back to the socket peer for direct connections.
        xff = request.headers.get("x-forwarded-for", "")
        direct_ip = request.client.host if request.client else "unknown"
        client_ip = xff.split(",")[0].strip() if xff and direct_ip == "127.0.0.1" else direct_ip

        delay = _backoff_delay(client_ip)
        if delay > 0:
            html = (_LOGIN_HTML.replace("{{VERSION}}", VERSION).replace("{{MODE}}", ml)
                    .replace("{{ERROR}}", f"Too many failed attempts — retry in {int(delay)}s"))
            return HTMLResponse(html, status_code=429)

        # Suppress Telegram noise for internal service calls (admin panel
        # authenticating with each bot via localhost). Log is still written.
        internal = client_ip == "127.0.0.1"

        if (_secrets.compare_digest(username, DASHBOARD_USER)
                and _secrets.compare_digest(password, DASHBOARD_PASS)):
            _record_success(client_ip)
            token = _sign_token(time.time())
            resp = RedirectResponse(f"{ROOT_PATH}/", status_code=303)
            resp.set_cookie("session", token, httponly=True, samesite="strict", max_age=30 * 86400)
            log.info("LOGIN OK: user=%s ip=%s label=%s", username, client_ip, ml)
            if not internal:
                send_telegram(f"\U0001f511 Login OK {ml} — user={username} ip={client_ip}",
                              category="security")
            return resp

        _record_failure(client_ip)
        n_fails = _login_failures.get(client_ip, (0, 0))[0]
        log.warning("LOGIN FAIL: user=%s ip=%s attempts=%d label=%s",
                    username, client_ip, n_fails, ml)
        # Alert on every external failure. Internal localhost failures are
        # admin-panel credential drift — log only.
        if not internal:
            send_telegram(f"\u26a0\ufe0f Login FAIL {ml} — user={username} ip={client_ip} (attempt #{n_fails})",
                          category="security")
        html = (_LOGIN_HTML.replace("{{VERSION}}", VERSION)
                .replace("{{MODE}}", ml).replace("{{ERROR}}", "Invalid username or password"))
        return HTMLResponse(html, status_code=401)

    @app.get("/logout")
    async def logout():
        # v12.5.29: bump the revocation epoch so any session token issued
        # before this moment is rejected by _verify_token. Without this, a
        # stolen cookie remains valid until natural 30-day expiry regardless
        # of how many times the legitimate user logged out.
        _revoked_before["ts"] = time.time()
        log.info("LOGOUT: session epoch bumped to %.0f", _revoked_before["ts"])
        resp = RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
        resp.delete_cookie("session")
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if _html_cache["v"] is None:
            if os.path.exists(HTML_PATH):
                ml = _mode_label()
                mc = _mode_color()
                _html_cache["v"] = (Path(HTML_PATH).read_text()
                                    .replace("{{VERSION}}", VERSION)
                                    .replace("{{MODE}}", ml)
                                    .replace("{{MODE_COLOR}}", mc))
            else:
                _html_cache["v"] = (
                    f'<html><body style="background:#0d1117;color:#e6edf3;font-family:monospace">'
                    f"<h1>Multi-Signal Bot v{VERSION}</h1>"
                    f'<pre id="s"></pre><script>'
                    f"setInterval(()=>fetch('/api/state').then(r=>r.json()).then("
                    f"d=>document.getElementById('s').textContent=JSON.stringify(d,null,2)),5000);"
                    f"</script></body></html>")
        return _html_cache["v"]

    @app.get("/api/health")
    async def api_health():
        pa = time.time() - bot._last_price_fetch if bot._last_price_fetch else 9999
        sa = time.time() - bot._last_scan if bot._last_scan else 9999
        stale = pa > 300 or sa > 7200
        status = "stale" if stale else ("degraded" if bot._degraded else "ok")
        return JSONResponse({
            "status": status, "price_age_s": round(pa, 0), "scan_age_s": round(sa, 0),
            "exchange_ok": bot._exchange is not None if EXECUTION_MODE == "live" else True,
            "degraded": list(bot._degraded),
            "positions_count": len(bot.positions), "paused": bot._paused,
        }, status_code=503 if stale else 200)

    @app.get("/api/chart/{symbol}")
    async def api_chart(symbol: str, hours: int = 24):
        """Price history for chart: 4h candles + 60s ticks merged.

        Ticks are bucketed so the response stays under ~600 points regardless of
        the requested window — LightweightCharts can't render denser data than
        its minimum bar spacing on a typical dashboard width.
        """
        from .config import ALL_SYMBOLS as _all_syms
        if symbol not in _all_syms:
            return JSONResponse({"symbol": symbol, "points": [], "position": None})
        hours = max(1, min(hours, 168))
        MAX_POINTS = 200

        def _build_chart():
            pts = []
            st = bot.states.get(symbol)
            if st and st.candles_4h:
                cutoff = time.time() - hours * 3600
                for c in st.candles_4h:
                    if c["t"] / 1000 >= cutoff:
                        pts.append({"ts": c["t"] // 1000, "price": c["c"]})
            if bot._db:
                try:
                    cutoff_ts = int(time.time() - hours * 3600)
                    # Compute bucket size so we return at most MAX_POINTS tick samples.
                    bucket_s = max(60, (hours * 3600) // MAX_POINTS)
                    rows = bot._db.execute(
                        """SELECT (ts / ?) * ? AS bucket, AVG(mark_px)
                           FROM ticks
                           WHERE symbol = ? AND ts > ?
                           GROUP BY bucket
                           ORDER BY bucket""",
                        (bucket_s, bucket_s, symbol, cutoff_ts)).fetchall()
                    tick_start = rows[0][0] if rows else 0
                    pts_filtered = [p for p in pts if p["ts"] < tick_start]
                    pts_filtered.extend({"ts": r[0], "price": r[1]} for r in rows)
                    return pts_filtered
                except Exception:
                    pass
            return pts

        import asyncio
        pts = await asyncio.to_thread(_build_chart)
        pos_info = None
        pos = bot.positions.get(symbol)
        if pos:
            pos_info = {"entry_price": pos.entry_price, "direction": "LONG" if pos.direction == 1 else "SHORT",
                        "strategy": pos.strategy, "entry_ts": int(pos.entry_time.timestamp())}
        return JSONResponse({"symbol": symbol, "points": pts, "position": pos_info})

    @app.get("/api/state")
    def api_state(): return JSONResponse(build_state_response(bot))  # sync — numpy in threadpool
    @app.get("/api/changelog")
    def api_changelog():
        """Return CHANGELOG.md as plain text for display in dashboard modal."""
        try:
            with open(CHANGELOG_PATH) as f:
                return JSONResponse({"content": f.read()})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/backtests")
    def api_backtests():
        """Return docs/backtests.md SANITIZED for public display.

        Drops sections that reveal strategy mechanics (filter parameter
        names, token whitelists/blacklists, per-strategy P&L breakdown,
        source script names, threshold values, the "Best strat" column).
        Keeps the rolling-window summary table + sanitized methodology
        and limits notes.
        """
        try:
            with open(BACKTESTS_PATH) as f:
                raw = f.read()
            return JSONResponse({"content": sanitize_backtests_md(raw)})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    @app.get("/api/signals")
    def api_signals(): return JSONResponse(build_signals_response(bot))  # sync — numpy in threadpool
    @app.get("/api/trades")
    async def api_trades(limit: int = 50): return JSONResponse(build_trades_list(bot.trades, limit))
    @app.get("/api/pnl")
    async def api_pnl(): return JSONResponse(build_pnl_curve(bot.trades, bot._capital))

    @app.get("/api/events")
    def api_events(limit: int = 30):
        """Recent events (SKIP / S9F_OBS / SUPERVISOR_REPORT / etc) for timeline ticker."""
        if not bot._db:
            return JSONResponse([])
        limit = max(1, min(limit, 200))  # cap to prevent accidental / abusive large queries
        try:
            cur = bot._db.execute(
                "SELECT ts, event, symbol, data FROM events ORDER BY ts DESC LIMIT ?",
                (limit,))
            rows = [{"ts": r[0], "event": r[1], "symbol": r[2], "data": r[3]} for r in cur]
            return JSONResponse(rows)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/close/{symbol}")
    def api_close_symbol(symbol: str):  # sync -- runs in threadpool
        """Close a single position early (manual exit)."""
        sym = symbol.upper()
        if sym not in bot.positions:
            return JSONResponse({"error": f"{sym} not in positions"}, status_code=404)
        st = bot.states.get(sym)
        if not st or st.price <= 0:
            return JSONResponse({"error": f"no price for {sym}"}, status_code=400)
        if not bot.close_and_check(sym, st.price, datetime.now(timezone.utc), "manual_close"):
            return JSONResponse({"error": f"close failed for {sym}, will retry"}, status_code=500)
        bot._save_state()
        return JSONResponse({"status": "closed", "symbol": sym})

    @app.post("/api/manual_stop/{symbol}")
    async def api_manual_stop(symbol: str, request: Request):
        """Set or clear a per-position manual stop (v12.5.10).

        Body:
          {"stop_usdt": 40}    → set the stop at +$40 unrealized
          {"clear": true}      → remove any previously-set manual stop

        Validation:
          - position must exist
          - stop_usdt is converted to bps via size_usdt
          - manual stop_bps must be strictly less than current unrealized_bps
            (would trigger immediately — use /api/close instead)
          - manual stop_bps must be strictly greater than the catastrophe stop
            (otherwise redundant with the auto-stop)
        """
        sym = symbol.upper()
        # v12.5.29: malformed/non-JSON body must not 500. Symbol whitelisted
        # against TRADE_SYMBOLS to keep log-forgery payloads out of the audit
        # trail (positions only contain whitelisted symbols anyway but the
        # check happens BEFORE any log.info call referencing `sym`).
        if sym not in TRADE_SYMBOLS:
            return JSONResponse({"error": "unknown symbol"}, status_code=400)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        if sym not in bot.positions:
            return JSONResponse({"error": f"{sym} not in positions"}, status_code=404)
        pos = bot.positions[sym]

        if body.get("clear"):
            with bot._pos_lock:
                pos.manual_stop_usdt = None
            bot._save_state()
            log.info("MANUAL_STOP %s: cleared", sym)
            return JSONResponse({"status": "cleared", "symbol": sym})

        stop_usdt = body.get("stop_usdt")
        if stop_usdt is None:
            return JSONResponse({"error": "missing 'stop_usdt' or 'clear' field"},
                                status_code=400)
        try:
            stop_usdt = float(stop_usdt)
        except (TypeError, ValueError):
            return JSONResponse({"error": "stop_usdt must be a number"}, status_code=400)
        # v12.5.29: reject NaN/inf which sail past float() and then corrupt
        # pos.manual_stop_usdt + every downstream P&L computation.
        if not math.isfinite(stop_usdt):
            return JSONResponse({"error": "stop_usdt must be finite"}, status_code=400)

        if pos.size_usdt <= 0:
            return JSONResponse({"error": "position has invalid size"}, status_code=400)
        # v12.5.29: validation mirrors the new exit logic (net-of-fees).
        # trigger_gross_bps is the bps level at which size × (gross - COST_BPS) / 1e4
        # equals stop_usdt — i.e. the gross-bps frontier of the manual stop.
        trigger_gross_bps = stop_usdt / pos.size_usdt * 1e4 + COST_BPS

        # Current unrealized (gross)
        st = bot.states.get(sym)
        if not st or st.price <= 0 or pos.entry_price <= 0:
            return JSONResponse({"error": "no current price"}, status_code=400)
        current_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
        current_pnl_net = pos.size_usdt * (current_bps - COST_BPS) / 1e4

        if stop_usdt >= current_pnl_net:
            return JSONResponse({"error": (
                f"stop ${stop_usdt:.2f} is at or above current net pnl "
                f"${current_pnl_net:.2f} ({current_bps:+.0f} bps gross) — would "
                f"trigger immediately. Use /api/close to close now, or pick a lower value.")},
                status_code=400)

        # Effective catastrophe stop floor for this strategy (gross bps)
        if pos.strategy == "S8":
            cata_stop = STOP_LOSS_S8
        elif pos.stop_bps != 0:
            cata_stop = pos.stop_bps
        else:
            cata_stop = STOP_LOSS_BPS
        if trigger_gross_bps <= cata_stop:
            return JSONResponse({"error": (
                f"stop ${stop_usdt:.2f} ({trigger_gross_bps:+.0f} bps gross) is at "
                f"or below the catastrophe stop {cata_stop:+.0f} bps — redundant. "
                f"Pick a higher dollar value.")}, status_code=400)
        stop_bps = trigger_gross_bps  # local only — for log + response display
        with bot._pos_lock:
            pos.manual_stop_usdt = stop_usdt
        bot._save_state()
        log.info("MANUAL_STOP %s: set at $%.2f (≈%.0f bps), current %+.0f bps",
                 sym, stop_usdt, stop_bps, current_bps)
        return JSONResponse({"status": "set", "symbol": sym,
                             "stop_usdt": round(stop_usdt, 2),
                             "stop_bps": round(stop_bps, 0)})

    @app.post("/api/capital")
    async def api_capital(request: Request):
        """Adjust capital (DCA injection or withdrawal). Body: {"amount": 100}

        Junior bot (BOT_LABEL=="JUNIOR") caps capital at JUNIOR_CAPITAL_CAP.
        Live/Paper have no cap. Withdrawals (amount < 0) are always allowed.
        """
        # v12.5.29: same hardening as /api/manual_stop — guard JSON parse,
        # type, and NaN/inf. A NaN propagated into bot._capital would corrupt
        # every percentage/ratio in the dashboard until next manual reset.
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        amount = body.get("amount")
        if amount is None:
            return JSONResponse({"error": "missing 'amount' field"}, status_code=400)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return JSONResponse({"error": "amount must be a number"}, status_code=400)
        if not math.isfinite(amount):
            return JSONResponse({"error": "amount must be finite"}, status_code=400)
        if amount == 0:
            return JSONResponse({"error": "amount cannot be zero"}, status_code=400)

        if BOT_LABEL == "JUNIOR" and JUNIOR_CAPITAL_CAP > 0 and amount > 0:
            new_capital = bot._capital + amount
            if new_capital > JUNIOR_CAPITAL_CAP:
                room = round(JUNIOR_CAPITAL_CAP - bot._capital, 2)
                if room <= 0:
                    msg = (f"Deposit refused. Junior's capital is fully allocated "
                           f"(${bot._capital:.0f}). No further deposit possible.")
                else:
                    msg = (f"Deposit refused. ${amount:.0f} would exceed Junior's "
                           f"allowed capital. Maximum deposit right now: ${room:.0f}.")
                return JSONResponse({"error": msg, "max_dca": max(room, 0.0)},
                                    status_code=400)

        if amount < 0 and bot._capital + amount < 0:
            return JSONResponse({"error": (
                f"Withdrawal ${-amount:.0f} exceeds current capital ${bot._capital:.0f}. "
                f"Maximum withdrawal: ${bot._capital:.0f}.")}, status_code=400)

        old_capital = bot._capital
        with bot._pos_lock:
            bot._capital += amount
            # DCA rebases the drawdown baseline. Capital flows are not trading
            # P&L: an injection that pushes balance above the prior peak should
            # not become the new "high water mark" against which DD is measured;
            # a withdrawal should not surface as drawdown.
            bot._peak_balance = bot._capital + bot._total_pnl
        bot._save_state()
        log.info("CAPITAL: $%.0f → $%.0f (%+.0f)", old_capital, bot._capital, amount)
        send_telegram(f"\U0001f4b0 Capital adjusted: ${old_capital:.0f} → ${bot._capital:.0f} ({amount:+.0f})",
                      category="admin")
        return JSONResponse({"status": "ok", "old": round(old_capital, 2),
                            "new": round(bot._capital, 2), "amount": amount})

    @app.post("/api/pause")
    def api_pause():  # sync -- FastAPI runs in threadpool
        now, failed = datetime.now(timezone.utc), []
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0:
                if not bot.close_and_check(sym, st.price, now, "manual_stop"):
                    failed.append(sym)
        bot._paused = True
        bot._save_state()
        resp = {"status": "paused"}
        if failed:
            log.warning("PAUSE: %d positions failed to close: %s", len(failed), failed)
            resp["warning"] = f"failed to close: {failed}"
        return JSONResponse(resp)

    @app.post("/api/resume")
    async def api_resume():
        bot._paused, bot._last_scan = False, 0  # force immediate scan
        bot._save_state()
        return JSONResponse({"status": "resumed"})

    @app.post("/api/reset")
    def api_reset():  # sync -- FastAPI runs in threadpool
        now = datetime.now(timezone.utc)
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0: bot._close_position(sym, st.price, now, "reset")
        with bot._pos_lock:
            bot._total_pnl, bot._wins, bot._peak_balance = 0.0, 0, bot._capital
            bot._consecutive_losses = 0
            bot._paused, bot._last_scan = False, 0
            for c in (bot._cooldowns, bot.trades, bot._degraded,
                      bot._feature_cache, bot._signal_first_seen): c.clear()
            bot._oi_summary = {"falling": 0, "rising": 0}
        # Clear trades from DB
        if bot._db:
            with _db_lock:
                bot._db.execute("DELETE FROM trades")
                bot._db.execute("DELETE FROM trajectories")
                bot._db.commit()
        bot._save_state()
        log.info("RESET: capital $%.0f, all state cleared", bot._capital)
        return JSONResponse({"status": "reset"})

    return app
