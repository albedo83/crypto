"""Live vs Backtest audit — compare real trades against backtest expectations.

Reads the live bot's trades from SQLite and analyzes:
  1. Real cost structure (taker fees, funding, slippage)
  2. MFE/MAE patterns vs timeout exits
  3. Strategy performance vs backtest expectations

Usage:
    python3 -m backtests.backtest_live_audit
"""

from __future__ import annotations

import sqlite3
import os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.bot.config import COST_BPS, TAKER_FEE_BPS, FUNDING_DRAG_BPS

LIVE_DB = os.path.join(os.path.dirname(__file__), "..", "analysis", "output_live", "reversal_ticks.db")
PAPER_DB = os.path.join(os.path.dirname(__file__), "..", "analysis", "output", "reversal_ticks.db")


def load_trades(db_path: str) -> list[dict]:
    """Load all trades from a bot's SQLite database."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    trades = [dict(r) for r in db.execute("SELECT * FROM trades ORDER BY id")]
    db.close()
    return trades


def load_trajectories(db_path: str) -> dict[str, list[tuple]]:
    """Load trajectories grouped by (symbol, entry_time)."""
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT symbol, entry_time, hours, unrealized_bps FROM trajectories ORDER BY hours").fetchall()
    db.close()
    trajs = defaultdict(list)
    for sym, entry_t, hours, bps in rows:
        trajs[(sym, entry_t)].append((hours, bps))
    return trajs


def analyze_trades(trades: list[dict], label: str):
    """Print comprehensive trade analysis."""
    print(f"\n{'=' * 70}")
    print(f"  {label}: {len(trades)} trades")
    print(f"{'=' * 70}")

    if not trades:
        print("  No trades found.")
        return

    # Basic stats
    pnls = [t["pnl_usdt"] for t in trades]
    nets = [t["net_bps"] for t in trades]
    grosses = [t["gross_bps"] for t in trades]
    maes = [t["mae_bps"] for t in trades if t["mae_bps"] is not None]
    mfes = [t["mfe_bps"] for t in trades if t["mfe_bps"] is not None]
    holds = [t["hold_hours"] for t in trades if t["hold_hours"] is not None]

    wins = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    wr = wins / len(pnls) * 100

    print(f"\n  P&L: ${total_pnl:+.2f} | WR: {wr:.0f}% ({wins}/{len(pnls)})")
    print(f"  Avg P&L: ${np.mean(pnls):.2f} | Median: ${np.median(pnls):.2f}")
    print(f"  Avg gross: {np.mean(grosses):.1f} bps | Avg net: {np.mean(nets):.1f} bps")
    print(f"  Implied cost: {np.mean(grosses) - np.mean(nets):.1f} bps "
          f"(config COST_BPS={COST_BPS})")

    if maes:
        print(f"\n  MAE: avg={np.mean(maes):.0f} bps, worst={min(maes):.0f} bps")
    if mfes:
        print(f"  MFE: avg={np.mean(mfes):.0f} bps, best={max(mfes):.0f} bps")
    if maes and mfes:
        gave_back = [mfe - net for mfe, net in zip(mfes, nets)]
        print(f"  Gave back: avg={np.mean(gave_back):.0f} bps "
              f"(avg MFE {np.mean(mfes):.0f} → avg exit {np.mean(nets):.0f})")
    if holds:
        print(f"  Hold: avg={np.mean(holds):.1f}h, min={min(holds):.1f}h, max={max(holds):.1f}h")

    # By strategy
    print(f"\n  --- Per Strategy ---")
    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t["strategy"]].append(t)
    for strat in sorted(by_strat.keys()):
        st = by_strat[strat]
        s_pnl = sum(t["pnl_usdt"] for t in st)
        s_wr = sum(1 for t in st if t["pnl_usdt"] > 0) / len(st) * 100
        s_avg_net = np.mean([t["net_bps"] for t in st])
        s_avg_gross = np.mean([t["gross_bps"] for t in st])
        s_mfes = [t["mfe_bps"] for t in st if t["mfe_bps"] is not None]
        s_avg_mfe = np.mean(s_mfes) if s_mfes else 0
        s_gave_back = np.mean([t["mfe_bps"] - t["net_bps"] for t in st
                               if t["mfe_bps"] is not None])
        print(f"  {strat:3s}: n={len(st):2d}, P&L=${s_pnl:+6.2f}, WR={s_wr:4.0f}%, "
              f"avg_gross={s_avg_gross:+6.0f}bps, avg_net={s_avg_net:+6.0f}bps, "
              f"avg_MFE={s_avg_mfe:5.0f}, gave_back={s_gave_back:5.0f}")

    # By exit reason
    print(f"\n  --- Per Exit Reason ---")
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["reason"]].append(t)
    for reason in sorted(by_reason.keys()):
        rt = by_reason[reason]
        r_pnl = sum(t["pnl_usdt"] for t in rt)
        r_avg_net = np.mean([t["net_bps"] for t in rt])
        print(f"  {reason:18s}: n={len(rt):2d}, P&L=${r_pnl:+6.2f}, avg_net={r_avg_net:+6.0f}bps")

    # Cost analysis
    print(f"\n  --- Cost Analysis ---")
    costs_implied = [t["gross_bps"] - t["net_bps"] for t in trades]
    print(f"  Implied cost per trade: avg={np.mean(costs_implied):.1f} bps "
          f"(expected: {COST_BPS:.0f} bps)")
    print(f"  Components: taker={TAKER_FEE_BPS} bps + funding={FUNDING_DRAG_BPS} bps "
          f"= {COST_BPS} bps")

    # Trades that reached high MFE but exited poorly
    print(f"\n  --- Biggest MFE→Exit Gaps (trades that peaked then crashed) ---")
    gaps = [(t, t["mfe_bps"] - t["net_bps"]) for t in trades
            if t["mfe_bps"] is not None and t["mfe_bps"] > 200]
    gaps.sort(key=lambda x: -x[1])
    for t, gap in gaps[:10]:
        print(f"  {t['symbol']:5s} {t['strategy']:3s} {t['direction']:5s}: "
              f"MFE={t['mfe_bps']:+6.0f}→net={t['net_bps']:+6.0f} "
              f"(gave back {gap:+.0f}bps, ${t['pnl_usdt']:+.2f}) "
              f"reason={t['reason']}, hold={t['hold_hours']:.0f}h")


def analyze_trajectories(trades: list[dict], trajs: dict, label: str):
    """Analyze trajectory patterns for winning vs losing trades."""
    print(f"\n  --- Trajectory Analysis ({label}) ---")

    winners = [t for t in trades if t["pnl_usdt"] > 0]
    losers = [t for t in trades if t["pnl_usdt"] <= 0]

    for group_name, group in [("Winners", winners), ("Losers", losers)]:
        if not group:
            continue
        # Find trajectories for these trades
        traj_data = []
        for t in group:
            key = (t["symbol"], t["entry_time"])
            if key in trajs:
                traj_data.append(trajs[key])

        if not traj_data:
            print(f"  {group_name}: no trajectory data")
            continue

        # Analyze: how quickly do winners/losers separate?
        for check_h in [4, 8, 12, 24]:
            vals = []
            for traj in traj_data:
                # Find the trajectory point closest to check_h
                closest = min(traj, key=lambda p: abs(p[0] - check_h), default=None)
                if closest and abs(closest[0] - check_h) < 2:
                    vals.append(closest[1])
            if vals:
                print(f"  {group_name:7s} at {check_h:2d}h: "
                      f"avg={np.mean(vals):+.0f}bps, "
                      f"median={np.median(vals):+.0f}bps, "
                      f"n={len(vals)}")


def main():
    print("=" * 70)
    print("LIVE vs BACKTEST AUDIT")
    print("=" * 70)
    print(f"\nConfig: COST_BPS={COST_BPS} (taker={TAKER_FEE_BPS} + funding={FUNDING_DRAG_BPS})")

    # Load live trades
    if os.path.exists(LIVE_DB):
        live_trades = load_trades(LIVE_DB)
        live_trajs = load_trajectories(LIVE_DB)
        analyze_trades(live_trades, "LIVE BOT")
        analyze_trajectories(live_trades, live_trajs, "LIVE")
    else:
        print(f"\nLive DB not found at {LIVE_DB}")

    # Load paper trades for comparison
    if os.path.exists(PAPER_DB):
        paper_trades = load_trades(PAPER_DB)
        paper_trajs = load_trajectories(PAPER_DB)
        analyze_trades(paper_trades, "PAPER BOT")
        analyze_trajectories(paper_trades, paper_trajs, "PAPER")
    else:
        print(f"\nPaper DB not found at {PAPER_DB}")

    # Cross-comparison
    if os.path.exists(LIVE_DB) and os.path.exists(PAPER_DB):
        live_trades = load_trades(LIVE_DB)
        paper_trades = load_trades(PAPER_DB)

        print(f"\n{'=' * 70}")
        print("  LIVE vs PAPER COMPARISON")
        print(f"{'=' * 70}")

        # Find overlapping time period
        live_start = min(t["entry_time"] for t in live_trades)
        live_end = max(t["entry_time"] for t in live_trades)
        paper_overlap = [t for t in paper_trades
                         if live_start <= t["entry_time"] <= live_end]

        print(f"  Live period: {live_start[:10]} to {live_end[:10]}")
        print(f"  Paper trades in same period: {len(paper_overlap)}")
        print(f"  Live trades: {len(live_trades)}")

        if paper_overlap:
            p_pnl = sum(t["pnl_usdt"] for t in paper_overlap)
            l_pnl = sum(t["pnl_usdt"] for t in live_trades)
            p_wr = sum(1 for t in paper_overlap if t["pnl_usdt"] > 0) / len(paper_overlap) * 100
            l_wr = sum(1 for t in live_trades if t["pnl_usdt"] > 0) / len(live_trades) * 100

            # Normalize to same capital basis
            p_avg_size = np.mean([t["size_usdt"] for t in paper_overlap])
            l_avg_size = np.mean([t["size_usdt"] for t in live_trades])
            print(f"\n  Paper: P&L=${p_pnl:+.2f}, WR={p_wr:.0f}%, avg_size=${p_avg_size:.0f}")
            print(f"  Live:  P&L=${l_pnl:+.2f}, WR={l_wr:.0f}%, avg_size=${l_avg_size:.0f}")

            # Per-strategy comparison
            print(f"\n  Per-strategy (same period):")
            all_strats = set(t["strategy"] for t in live_trades) | set(t["strategy"] for t in paper_overlap)
            for strat in sorted(all_strats):
                p_s = [t for t in paper_overlap if t["strategy"] == strat]
                l_s = [t for t in live_trades if t["strategy"] == strat]
                if p_s or l_s:
                    p_n = len(p_s)
                    l_n = len(l_s)
                    p_avg = np.mean([t["net_bps"] for t in p_s]) if p_s else 0
                    l_avg = np.mean([t["net_bps"] for t in l_s]) if l_s else 0
                    print(f"    {strat}: paper={p_n} trades avg {p_avg:+.0f}bps, "
                          f"live={l_n} trades avg {l_avg:+.0f}bps, "
                          f"Δ={l_avg - p_avg:+.0f}bps")

    print(f"\n{'=' * 70}")
    print("DONE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
