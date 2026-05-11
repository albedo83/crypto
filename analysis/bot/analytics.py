"""Read-only analytical helpers — drift stats, S10 health, win-prob estimator.

Pure functions over Trade and Position data. No mutation, no exchange handles,
no locks. Safe to call from any thread.

Extracted from trading.py — these had no business mutating state and were only
co-located historically.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta


def is_bot_trade(t) -> bool:
    """True if trade was a bot decision (not manual_stop or reset)."""
    return t.reason not in ("manual_stop", "reset")


def compute_signal_drift(trades) -> dict:
    """Per-strategy stats — lifetime AND rolling 20 trades.

    Lifetime = all bot trades ever for this strategy (structural edge).
    Recent 20 = last 20 (short-term health). `trend` compares first 10 vs last
    10 within the recent 20: +1 improving, -1 degrading, 0 stable/insufficient.
    """
    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        if is_bot_trade(t):
            by_strat[t.strategy].append(t)
    result = {}
    for strat, strat_trades in by_strat.items():
        if not strat_trades:
            continue
        n_life = len(strat_trades)
        wr_life = sum(1 for t in strat_trades if t.pnl_usdt > 0) / n_life
        avg_life = sum(t.net_bps for t in strat_trades) / n_life
        pnl_life = sum(t.pnl_usdt for t in strat_trades)
        recent = strat_trades[-20:]
        n_rec = len(recent)
        wr_rec = sum(1 for t in recent if t.pnl_usdt > 0) / n_rec
        avg_rec = sum(t.net_bps for t in recent) / n_rec
        pnl_rec = sum(t.pnl_usdt for t in recent)
        trend = 0
        if n_rec >= 10:
            half = n_rec // 2
            wr_first = sum(1 for t in recent[:half] if t.pnl_usdt > 0) / half
            wr_last = sum(1 for t in recent[half:] if t.pnl_usdt > 0) / (n_rec - half)
            if wr_last - wr_first >= 0.10:
                trend = 1
            elif wr_last - wr_first <= -0.10:
                trend = -1
        result[strat] = {
            "n": n_life,
            "win_rate": round(wr_life, 2),
            "avg_bps": round(avg_life, 1),
            "total_pnl": round(pnl_life, 2),
            "trend": trend,
            "lifetime": {"n": n_life, "win_rate": round(wr_life, 2),
                         "avg_bps": round(avg_life, 1),
                         "total_pnl": round(pnl_life, 2)},
            "recent20": {"n": n_rec, "win_rate": round(wr_rec, 2),
                         "avg_bps": round(avg_rec, 1),
                         "total_pnl": round(pnl_rec, 2)},
        }
    return result


def compute_s10_health(trades, days: int = 30) -> dict:
    """S10 rolling health check over the last N days.

    Monitors the v11.3.4 walk-forward filters (SHORT-only + token whitelist).
    The filters improved P&L on 12m OOS but the rule is regime-dependent —
    this health card tells you at a glance whether to flip the kill-switch.

    Status:
      green  — S10 profitable (pnl > 0 and avg net > +10 bps)
      yellow — neutral (pnl >= 0 or avg net >= -20 bps)
      red    — bleeding, consider flipping the kill-switch
      idle   — no S10 trades in the window (too quiet to judge)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for t in trades:
        if not is_bot_trade(t) or t.strategy != "S10":
            continue
        try:
            exit_dt = datetime.fromisoformat(t.exit_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        if exit_dt >= cutoff:
            recent.append(t)

    if not recent:
        return {
            "status": "idle", "n": 0, "days": days,
            "pnl": 0.0, "wr": 0.0, "avg_bps": 0.0,
            "message": f"No S10 trades in last {days}d",
        }

    pnl = sum(t.pnl_usdt for t in recent)
    wins = sum(1 for t in recent if t.pnl_usdt > 0)
    wr = wins / len(recent)
    avg_bps = sum(t.net_bps for t in recent) / len(recent)

    if pnl > 0 and avg_bps > 10:
        status, message = "green", "S10 performing as expected"
    elif pnl >= 0 or avg_bps >= -20:
        status, message = "yellow", "S10 neutral — keep monitoring"
    else:
        status, message = "red", "S10 bleeding — consider flipping kill-switch"

    return {
        "status": status, "n": len(recent), "days": days,
        "pnl": round(pnl, 2), "wr": round(wr, 2),
        "avg_bps": round(avg_bps, 1), "message": message,
    }


def filter_recent_trades(trades, lookback_days: int = 180) -> list:
    """Return trades whose exit_time is within the last `lookback_days`.

    Hot-path helper: call ONCE per scan and reuse the filtered list across
    multiple position-level WR estimates. Avoids the O(N×M) scan that arises
    from filtering inside every estimate_win_prob call.
    """
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    return [t for t in trades if (t.exit_time or "") >= cutoff_iso]


def estimate_win_prob(pos, trades, hours_held: float = 0,
                       hold_target_h: float = 48,
                       lookback_days: int = 180,
                       pre_filtered: bool = False,
                       current_ur_bps: float = 0.0) -> dict | None:
    """Estimate win probability for an open position from historical patterns.

    If `pre_filtered=True`, `trades` is assumed already filtered to the recent
    window — skips the cutoff filter for the hot dashboard path. Otherwise
    filters in-place (back-compat).

    `current_ur_bps` is the position's CURRENT unrealized pnl in bps (signed
    by direction). When >0, the MAE conditional penalty is skipped — the
    position has already moved past its worst point and conditioning on
    "trades that also hit this MAE depth" over-pessimises a recovered trade.

    Strategy:
      1. Exact match: same (strat, symbol, direction). Use WR if ≥5 samples
         (raised from 3 in v12.5.5 to avoid extreme 0%/100% artifacts from
         tiny samples).
      2. Wider match: same (strat, direction). Use WR if ≥8 samples.
      3. MAE conditional adjustment if mature AND currently underwater.
      4. MFE pulse bonus / no-pulse-late penalty if mature.
      5. v12.3.2: maturity gate mutes adjustments in first 2h or 10% of hold.

    Returns dict {wr_pct, base_wr_pct, n, scope, mature, note} or None.
    """
    direction_str = "LONG" if pos.direction == 1 else "SHORT"
    if not pre_filtered:
        trades = filter_recent_trades(trades, lookback_days)

    exact = [t for t in trades if t.strategy == pos.strategy
             and t.symbol == pos.symbol
             and t.direction == direction_str]
    # v12.5.5: tier 1 min raised from 3 to 5 — 3 samples can yield 0%/100%
    # which is statistical noise; the broader strat+dir tier is more reliable.
    if len(exact) >= 5:
        matches, scope = exact, "exact"
    else:
        wider = [t for t in trades if t.strategy == pos.strategy
                 and t.direction == direction_str]
        if len(wider) >= 8:
            matches, scope = wider, "strat+dir"
        else:
            return None
    base_wr = sum(1 for t in matches if t.pnl_usdt > 0) / len(matches) * 100

    mature = hours_held >= max(2.0, 0.10 * hold_target_h)

    cur_mae = pos.mae_bps or 0
    mfe = pos.mfe_bps or 0
    adj_wr = base_wr
    note = f"{scope} match"

    if not mature:
        note += f", fresh ({hours_held:.1f}h)"
    else:
        # v12.5.5: only apply MAE penalty when currently underwater.
        # A position currently in profit has already moved past its worst
        # point; conditioning on historical trades that hit the same MAE
        # depth (regardless of whether they recovered) double-counts the
        # bad news.
        if cur_mae < -200 and current_ur_bps <= 0:
            deep = [t for t in matches if (t.mae_bps or 0) <= cur_mae]
            if len(deep) >= 3:
                cond_wr = sum(1 for t in deep if t.pnl_usdt > 0) / len(deep) * 100
                adj_wr = cond_wr
                if cur_mae < -500:
                    note += f", deep MAE ({int(cur_mae)})"
            else:
                stop_bps = pos.stop_bps or -1250
                proximity = max(0, min(1, cur_mae / stop_bps))
                adj_wr = base_wr * (1 - 0.5 * proximity)
                note += f", MAE near stop ({int(cur_mae)})"
        elif cur_mae < -200 and current_ur_bps > 0:
            note += f", recovered (MAE {int(cur_mae)} → ur +{int(current_ur_bps)})"
        if mfe >= 200:
            adj_wr = min(95, adj_wr * 1.1)
            note += f", MFE pulse ({int(mfe)})"
        elif mfe < 50 and hours_held >= 0.5 * hold_target_h:
            adj_wr *= 0.9
            note += ", no pulse late"

    return {
        "wr_pct": round(adj_wr, 0),
        "base_wr_pct": round(base_wr, 0),
        "n": len(matches),
        "scope": scope,
        "mature": mature,
        "note": note,
    }
