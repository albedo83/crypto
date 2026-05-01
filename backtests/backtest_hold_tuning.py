"""Walk-forward sweep — per-strategy hold-length tuning.

Current (analysis/bot/config.py):
  S1: 72h (3 days, default)
  S5: 48h
  S8: 60h
  S9: 48h
  S10: 24h

Sweeps each strategy through ±50% of its current hold to see if a more
patient or more impatient hold produces better walk-forward results.

Usage:
    python3 -m backtests.backtest_hold_tuning
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
import backtests.backtest_rolling as br
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


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

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS, mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    window_specs = []
    for label, months in WINDOWS:
        start_dt = end_dt - relativedelta(months=months)
        window_specs.append((label, int(start_dt.timestamp() * 1000)))
    end_ts = latest_ts

    common = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        end_ts_ms=end_ts, start_capital=CAP,
        oi_data=oi_data, early_exit_params=early_exit, funding_data=funding_data,
    )

    # Snapshot current HOLD_CANDLES so we can restore between runs
    saved_holds = dict(br.HOLD_CANDLES)
    print(f"Current HOLD_HOURS: {[f'{k}={v*4}h' for k,v in saved_holds.items()]}")

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # Sweep each strategy independently. Set the target strat's hold candles
    # to a new value, restore the others.
    candidates = []
    grid = {
        "S5":  [12, 24, 36, 48, 60, 72, 96],   # current 48h → 12 candles
        "S8":  [24, 36, 48, 60, 72, 96],        # current 60h → 15 candles
        "S9":  [12, 24, 36, 48, 60, 72],        # current 48h → 12 candles
        "S10": [8, 12, 16, 24, 36, 48],         # current 24h → 6 candles
        "S1":  [48, 60, 72, 96, 120],           # current 72h → 18 candles
    }
    for strat, hours_list in grid.items():
        for h in hours_list:
            candidates.append((strat, h, h//4))

    print(f"\nSweep: {len(candidates)} (strat, hold_h) combos × {len(WINDOWS)} windows")
    t0 = time.time()
    results = {}
    for strat, hours, candles in candidates:
        # Mutate HOLD_CANDLES temporarily
        br.HOLD_CANDLES = dict(saved_holds)
        br.HOLD_CANDLES[strat] = candles
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, **common)
            rs[label] = r
        results[(strat, hours)] = rs
        d_pnl = {lab: rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs}
        d_dd = {lab: rs[lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        avg_dd = sum(d_dd.values())/4
        marker = " *" if hours == saved_holds[strat]*4 else "  "
        print(f"  {strat} hold={hours:>3d}h{marker}  Δ28m={d_pnl['28m']:+8.1f}  Δ12m={d_pnl['12m']:+7.1f}  "
              f"Δ6m={d_pnl['6m']:+6.1f}  Δ3m={d_pnl['3m']:+5.1f}  ΔDD={avg_dd:+5.1f}  {positives}/4")
    # Restore
    br.HOLD_CANDLES = saved_holds

    # Report best per strat
    print(f"\n{'=' * 100}\n{'Best per strategy (4/4 with DD intact)':^100}\n{'=' * 100}")
    for strat in grid:
        for_strat = [(h, results[(strat, h)]) for h in grid[strat]]
        for h, rs in for_strat:
            d_pnl = [rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs]
            d_dd = [rs[lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs]
            if all(p > 0 for p in d_pnl) and sum(d_dd)/4 <= 0.5:
                print(f"  {strat} hold={h}h: avg ΔPnL {sum(d_pnl)/4:+.1f}pp ΔDD {sum(d_dd)/4:+.1f}pp  "
                      f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
