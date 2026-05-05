"""Estimate the distribution of 30-day P&L from backtest trades.

Question: is our live result (+$14.82 / +5% on $300 over 30 days) within
the normal statistical variance of the strategy, or in the tail?

Method:
1. Run baseline backtest for 28 months → all trades.
2. Compute P&L for every rolling 30-day window in the backtest.
3. Plot the distribution; place the live result.
4. Bootstrap: 1000 random samples of 48 trades → sum P&L distribution.

This separates "we're unlucky" (within ±1σ) from "the model is broken"
(deep tail).
"""
from __future__ import annotations

import random
import statistics
from datetime import datetime, timezone, timedelta

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    start_28m_ts = int((end_dt - timedelta(days=28*30)).timestamp() * 1000)

    early_exit_params = dict(
        exit_lead_candles=3, mfe_cap_bps=150,
        mae_floor_bps=-800, slack_bps=300,
    )

    print(f"Running 28-month backtest from {datetime.fromtimestamp(start_28m_ts/1000).date()}…")
    r = run_window(features, data, sector_features, dxy_data,
                   start_28m_ts, latest_ts,
                   start_capital=1000.0,
                   oi_data=oi_data,
                   early_exit_params=early_exit_params,
                   funding_data=funding_data)
    trades = r["trades"]
    print(f"  {len(trades)} trades, end ${r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%)")

    # ── 30-day rolling P&L windows (non-compounded sum) ──
    # Convert pnl which is in compounded $ → normalize to %-of-prior-capital
    # Build cumulative capital trace; compute window % returns.
    trades_sorted = sorted(trades, key=lambda t: t["exit_t"])
    # Map to (exit_dt, pnl_pct_of_capital_at_exit)
    capital = 1000.0
    points = []
    for t in trades_sorted:
        prev_cap = capital
        capital += t["pnl"]
        if prev_cap > 0:
            pct = t["pnl"] / prev_cap * 100
        else:
            pct = 0
        points.append((t["exit_t"], pct, t["pnl"], prev_cap))

    # Rolling 30-day windows
    DAY_MS = 86400 * 1000
    win_size = 30 * DAY_MS
    monthly_pcts = []
    monthly_dollars_per1k = []
    cur_start_ms = points[0][0]
    end_ms = points[-1][0]
    while cur_start_ms + win_size < end_ms:
        cur_end_ms = cur_start_ms + win_size
        in_win = [p for p in points if cur_start_ms <= p[0] < cur_end_ms]
        if len(in_win) >= 5:
            # Compound-style return
            rets = [1 + p[1] / 100 for p in in_win]
            cum = 1.0
            for r_ in rets: cum *= r_
            pct = (cum - 1) * 100
            # Also: dollar P&L if started with $1000
            dollars = 1000 * (cum - 1)
            monthly_pcts.append(pct)
            monthly_dollars_per1k.append(dollars)
        cur_start_ms += 7 * DAY_MS  # slide by 1 week

    arr = np.array(monthly_pcts)
    print()
    print(f"=== Distribution of 30-day P&L (rolling, sliding by 1 week, n={len(arr)}) ===")
    print(f"  mean:   {arr.mean():+.1f}%")
    print(f"  median: {np.median(arr):+.1f}%")
    print(f"  std:    {arr.std():.1f}%")
    print(f"  min:    {arr.min():+.1f}%")
    print(f"  max:    {arr.max():+.1f}%")
    print()
    print(f"  Percentiles:")
    for p in [5, 10, 25, 50, 75, 90, 95]:
        print(f"    p{p:>2}: {np.percentile(arr, p):+.1f}%")
    print()

    # Live result: +$14.82 on $300 = +5.0%
    LIVE_PCT = 14.82 / 300 * 100
    pct_below = (arr < LIVE_PCT).sum() / len(arr) * 100
    pct_above = (arr > LIVE_PCT).sum() / len(arr) * 100
    print(f"=== Live result: {LIVE_PCT:+.1f}% over 30 days ===")
    print(f"  {pct_below:.0f}% of historical 30-day windows had LOWER P&L")
    print(f"  {pct_above:.0f}% had HIGHER P&L")
    if LIVE_PCT < arr.mean() - arr.std():
        print(f"  → LIVE est SOUS la moyenne −1σ (mean−σ = {arr.mean()-arr.std():+.1f}%)")
    elif LIVE_PCT < arr.mean():
        print(f"  → LIVE est sous la moyenne ({arr.mean():+.1f}%) mais dans ±1σ")
    else:
        print(f"  → LIVE est au-dessus de la moyenne")

    print()
    print("=== Bootstrap: 48-trade samples (matching live N) ===")
    pnls = [p[1] for p in points]  # pct returns per trade
    random.seed(42)
    boot_sums = []
    for _ in range(2000):
        sample = random.sample(pnls, 48) if len(pnls) >= 48 else pnls
        cum = 1.0
        for r_ in sample: cum *= (1 + r_ / 100)
        boot_sums.append((cum - 1) * 100)
    boot = np.array(boot_sums)
    print(f"  Bootstrap mean:   {boot.mean():+.1f}%")
    print(f"  Bootstrap std:    {boot.std():.1f}%")
    print(f"  Bootstrap median: {np.median(boot):+.1f}%")
    print(f"  Percentiles:  p10 {np.percentile(boot,10):+.1f}%  p25 {np.percentile(boot,25):+.1f}%  "
          f"p50 {np.percentile(boot,50):+.1f}%  p75 {np.percentile(boot,75):+.1f}%  p90 {np.percentile(boot,90):+.1f}%")
    pct_below_boot = (boot < LIVE_PCT).sum() / len(boot) * 100
    print(f"  Live ({LIVE_PCT:+.1f}%) : {pct_below_boot:.0f}% des bootstraps font moins, {100-pct_below_boot:.0f}% font plus")


if __name__ == "__main__":
    main()
