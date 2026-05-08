"""Sliding walk-forward — the gold standard test for adaptive α.

Simulates a real-life adaptive bot:
  - Every 6 months, find best α on the previous 18m of data
  - Apply that α for the next 6m
  - Slide forward 6m, repeat
  - Aggregate the OOS performance over all sliding windows

If the OOS perf summed across all sliding test periods stays positive AND
the chosen α stays stable from period to period, the strategy is robust.
If α flips wildly between trains, we're chasing noise.

Usage:
    python3 -m backtests.backtest_adaptive_walkforward
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from collections import defaultdict

from dateutil.relativedelta import relativedelta  # type: ignore
import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0


def compute_btc_z(data: dict, lookback_days: int = 30) -> dict:
    btc = data["BTC"]
    n = lookback_days * 6
    closes = np.array([c["c"] for c in btc])
    rets, ts_list = [], []
    for i in range(n, len(btc)):
        ret = (closes[i] / closes[i - n] - 1) if closes[i - n] > 0 else 0
        rets.append(ret)
        ts_list.append(btc[i]["t"])
    rets_arr = np.array(rets)
    mean = float(np.mean(rets_arr))
    std = float(np.std(rets_arr)) or 1.0
    return {ts: (r - mean) / std for ts, r in zip(ts_list, rets_arr)}


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    btc_z = compute_btc_z(data, lookback_days=30)

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
    )

    def make_fn(alpha_vec):
        def fn(cand, f, n_pos):
            a = alpha_vec.get(cand["strat"], 0)
            return max(0.3, min(2.5, 1 + a * btc_z.get(f["t"], 0)))
        return fn

    # Build sliding windows: train [t-18m, t-6m), test [t-6m, t]
    # for t = end - 0m, end - 6m, end - 12m, end - 18m (4 OOS test periods)
    # Train windows: (last 28m) − 6m starts, sliding back
    # Effectively: 4 OOS slices of ~6m each, each with their own training period
    print("\n" + "=" * 110)
    print(f"{'SLIDING WALK-FORWARD — train every 6m on past 18m, test on next 6m':^110}")
    print("=" * 110)
    print(f"\n  {'OOS period':30s}  {'best α':40s}  {'IS Δpnl%':>10s}  {'OOS Δpnl%':>10s}  {'OOS ΔDD':>9s}  Status")

    alphas_grid = [-1.0, -0.7, -0.5, -0.3, 0, +0.3, +0.5, +0.7, +1.0]
    # Test on each strategy independently to keep dimensionality manageable
    target_strats = ["S1", "S5", "S8", "S9", "S10"]

    # Define 4 sliding splits
    splits = []
    for offset in [0, 6, 12, 18]:
        oos_end_dt = end_dt - relativedelta(months=offset)
        oos_start_dt = oos_end_dt - relativedelta(months=6)
        is_end_dt = oos_start_dt
        is_start_dt = is_end_dt - relativedelta(months=18)
        splits.append({
            "is_start": int(is_start_dt.timestamp() * 1000),
            "is_end": int(is_end_dt.timestamp() * 1000),
            "oos_start": int(oos_start_dt.timestamp() * 1000),
            "oos_end": int(oos_end_dt.timestamp() * 1000),
            "label": f"{is_start_dt.date()}→{is_end_dt.date()} | OOS {oos_start_dt.date()}→{oos_end_dt.date()}",
        })

    overall_oos = defaultdict(list)
    chosen_alpha = defaultdict(list)

    for split in splits:
        # Baseline IS and OOS
        base_is  = run_window(features, data, start_ts_ms=split["is_start"],
                             end_ts_ms=split["is_end"], **common)
        base_oos = run_window(features, data, start_ts_ms=split["oos_start"],
                             end_ts_ms=split["oos_end"], **common)
        for strat in target_strats:
            best_a = 0
            best_is_pnl = base_is["pnl_pct"]
            for a in alphas_grid:
                if a == 0: continue
                size_fn = make_fn({strat: a})
                r = run_window(features, data, start_ts_ms=split["is_start"],
                               end_ts_ms=split["is_end"], size_fn=size_fn, **common)
                if r["pnl_pct"] > best_is_pnl:
                    best_is_pnl = r["pnl_pct"]
                    best_a = a
            # Apply chosen α to OOS
            if best_a != 0:
                size_fn = make_fn({strat: best_a})
                oos_r = run_window(features, data, start_ts_ms=split["oos_start"],
                                   end_ts_ms=split["oos_end"], size_fn=size_fn, **common)
                oos_dpnl = oos_r["pnl_pct"] - base_oos["pnl_pct"]
                oos_ddd = oos_r["max_dd_pct"] - base_oos["max_dd_pct"]
            else:
                oos_dpnl, oos_ddd = 0, 0
            is_dpnl = best_is_pnl - base_is["pnl_pct"]
            chosen_alpha[strat].append(best_a)
            overall_oos[strat].append(oos_dpnl)
            status = "✓" if oos_dpnl > 0 else "✗"
            print(f"  [{strat}] {split['label']:55s}  α={best_a:+.1f}  IS={is_dpnl:+8.1f}%  OOS={oos_dpnl:+9.1f}%  ΔDD={oos_ddd:+7.1f}pp  {status}")

    # Summary
    print(f"\n  {'Strategy':10s}  {'α picks (chronological, oldest→newest)':50s}  α stability  OOS sum  OOS mean")
    for strat in target_strats:
        picks = chosen_alpha[strat]
        # Reverse to chronological
        picks_chrono = list(reversed(picks))
        alpha_str = " ".join(f"{a:+.1f}" for a in picks_chrono)
        unique_alphas = set(picks)
        stability = "stable" if len(unique_alphas) <= 2 else "varying" if len(unique_alphas) <= 3 else "noisy"
        oos_sum = sum(overall_oos[strat])
        oos_mean = oos_sum / len(overall_oos[strat]) if overall_oos[strat] else 0
        print(f"  {strat:10s}  {alpha_str:50s}  {stability:11s}  {oos_sum:+7.1f}%  {oos_mean:+7.1f}%")


if __name__ == "__main__":
    main()
