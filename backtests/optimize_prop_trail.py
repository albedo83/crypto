"""Phase 3 — Offline regime-aware optimization of proportional trail params.

Loads trajectories + trades dumps from Phase 2. For each (strat, dir, regime)
bucket, sweeps a fine grid of (arm_bps, lock_ratio) configs via numpy vectorization
and finds Pareto-optimum across the 4 splits.

Bucketing:
  - bear:    btc_z < -0.5
  - neutral: -0.5 <= btc_z <= +0.5
  - bull:    btc_z > +0.5

Trail logic (per candle within a trade):
  arm fires when mfe_bps >= arm_bps AND ur_bps <= arm_bps + (mfe_bps - arm_bps) * lock_ratio
  At first arming candle: hypothetical exit at the current ur_bps (the trail catches the dip).
  Hypothetical net_bps = ur_bps - COST.
  delta_pnl = (hypothetical_net - actual_net) * size / 1e4

The trail decision is computed only on candles where the trade's regime AT THAT TICK
matches the bucket regime. This mirrors the live mechanic of v12.5.30 s8_inlife.

Usage: python3 -m backtests.optimize_prop_trail
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict

import numpy as np


OUT_DIR = os.path.join(os.path.dirname(__file__), "output")
COST_BPS = 11.0  # TAKER_FEE_BPS (7) + BACKTEST_SLIPPAGE_BPS (4) — matches backtest_rolling
Z_THRESHOLD = 0.5
STRATS = ("S1", "S5", "S9")
DIRS = (-1, 1)
REGIMES = ("bear", "neutral", "bull")

# Fine grid
ARM_GRID = np.arange(100, 2001, 100)        # 20 levels: 100, 200, ..., 2000
LOCK_GRID = np.arange(0.30, 0.951, 0.05)    # 14 levels: 0.30, 0.35, ..., 0.95
MIN_TRADES_PER_BUCKET = 10  # below this, skip optim


def regime_of(z: float) -> str:
    if z < -Z_THRESHOLD:
        return "bear"
    if z > Z_THRESHOLD:
        return "bull"
    return "neutral"


def load_split(n: int):
    """Load trajectories + trades for split N, return list of dicts joined by trade_id."""
    with open(os.path.join(OUT_DIR, f"trades_split_{n}.json")) as f:
        trades = json.load(f)
    with open(os.path.join(OUT_DIR, f"trajectories_split_{n}.json")) as f:
        traj = json.load(f)
    joined = []
    for t in trades:
        tid = str(t.get("trade_id"))
        if tid not in traj:
            continue
        t["traj"] = traj[tid]
        joined.append(t)
    return joined


def simulate_trail(trade, arm: float, lock: float, regime: str) -> tuple[float, float]:
    """For a given trade and config, find first-fire candle (if any) where:
      - btc_z at that candle is in `regime` bucket
      - mfe_bps >= arm
      - ur_bps <= arm + (mfe - arm) * lock
    Returns (hypothetical_net_bps, delta_pnl_usd).
    If no fire, returns (actual_net, 0.0).
    """
    actual_net = trade["net"]
    size = trade["size"]
    for tick in trade["traj"]:
        if regime_of(tick["btc_z"]) != regime:
            continue
        mfe = tick["mfe_bps"]
        if mfe < arm:
            continue
        stop = arm + (mfe - arm) * lock
        if tick["ur_bps"] <= stop:
            hyp_net = tick["ur_bps"] - COST_BPS
            delta_pnl = (hyp_net - actual_net) * size / 1e4
            return hyp_net, delta_pnl
    return actual_net, 0.0


def main():
    print("Loading 4 splits...", flush=True)
    splits = {n: load_split(n) for n in (1, 2, 3, 4)}
    for n in (1, 2, 3, 4):
        n_trades = len(splits[n])
        print(f"  split_{n}: {n_trades} trades", flush=True)

    print()
    print(f"Optimizing 18 buckets (strat × dir × regime), grid {len(ARM_GRID)}×{len(LOCK_GRID)} = {len(ARM_GRID)*len(LOCK_GRID)} configs each...")
    print()

    # For each bucket, find best (arm, lock) maximizing strict 4/4 split improvement
    csv_path = os.path.join(OUT_DIR, "optimization_results.csv")
    fh = open(csv_path, "w", newline="")
    writer = csv.writer(fh)
    writer.writerow([
        "strat", "dir", "regime", "n_trades_total",
        "arm", "lock",
        "sum_dpnl",
        "dpnl_split_1", "dpnl_split_2", "dpnl_split_3", "dpnl_split_4",
        "splits_improved",
        "verdict",
    ])

    results_summary = []

    for strat in STRATS:
        for dir_ in DIRS:
            for regime in REGIMES:
                # Filter trades per bucket (eligible per strat/dir, regime applies per-tick during simulation)
                bucket_trades = {n: [t for t in splits[n]
                                      if t["strat"] == strat and t["dir"] == dir_]
                                 for n in (1, 2, 3, 4)}
                total = sum(len(v) for v in bucket_trades.values())
                if total < MIN_TRADES_PER_BUCKET:
                    print(f"  [SKIP] {strat} dir={dir_:+d} regime={regime:<7} n={total} < {MIN_TRADES_PER_BUCKET}")
                    writer.writerow([strat, dir_, regime, total, "", "", "", "", "", "", "", "", "insufficient_data"])
                    continue

                # Sweep grid
                best_config = None
                best_sum = -1e18
                best_dpnl_per_split = (0, 0, 0, 0)
                best_improved = 0

                for arm in ARM_GRID:
                    for lock in LOCK_GRID:
                        dpnl_per_split = []
                        for n in (1, 2, 3, 4):
                            s = sum(simulate_trail(t, float(arm), float(lock), regime)[1]
                                    for t in bucket_trades[n])
                            dpnl_per_split.append(s)
                        sum_dpnl = sum(dpnl_per_split)
                        improved = sum(1 for d in dpnl_per_split if d > 0)
                        # Score: prefer 4/4 improvement; tiebreak by sum
                        # Use composite: (improved, sum_dpnl)
                        if (improved, sum_dpnl) > (best_improved, best_sum):
                            best_improved = improved
                            best_sum = sum_dpnl
                            best_config = (int(arm), round(float(lock), 3))
                            best_dpnl_per_split = tuple(dpnl_per_split)

                verdict = "STRICT_4/4" if best_improved == 4 else f"BEST_{best_improved}/4"
                arm_b, lock_b = best_config
                d1, d2, d3, d4 = best_dpnl_per_split
                writer.writerow([strat, dir_, regime, total, arm_b, lock_b,
                                 round(best_sum, 2),
                                 round(d1, 2), round(d2, 2), round(d3, 2), round(d4, 2),
                                 best_improved, verdict])
                results_summary.append((strat, dir_, regime, total, arm_b, lock_b,
                                         best_sum, best_improved, best_dpnl_per_split, verdict))
                print(f"  {strat} dir={dir_:+d} regime={regime:<7} n={total:3d}  best arm={arm_b:4d} lock={lock_b:.2f}  sum_ΔPnL=${best_sum:+9.0f}  splits={best_improved}/4  {verdict}")

    fh.close()
    print(f"\nResults written to {csv_path}")

    # Print recommended config per (strat, dir) — aggregate across regimes
    print()
    print("=" * 84)
    print("RECOMMENDED CONFIG per (strat, dir) — picks regime-conditioned best")
    print("=" * 84)
    print(f"{'strat':>5} {'dir':>5} {'bear (arm/lock/Σ$/n)':>30} {'neutral (arm/lock/Σ$/n)':>33} {'bull (arm/lock/Σ$/n)':>30}")
    for strat in STRATS:
        for dir_ in DIRS:
            row = [strat, f"{dir_:+d}"]
            for regime in REGIMES:
                m = [r for r in results_summary
                     if r[0] == strat and r[1] == dir_ and r[2] == regime]
                if not m:
                    row.append("(insufficient)")
                    continue
                _, _, _, total, arm, lock, sumd, imp, _, verdict = m[0]
                if "STRICT" in verdict:
                    tag = "✓"
                elif imp >= 3:
                    tag = "⚠"
                else:
                    tag = "✗"
                row.append(f"{tag} {arm}/{lock:.2f}/{sumd:+.0f}/n={total}")
            print(f"{row[0]:>5} {row[1]:>5} {row[2]:>30} {row[3]:>33} {row[4]:>30}")


if __name__ == "__main__":
    main()
