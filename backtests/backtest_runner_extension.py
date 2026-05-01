"""Walk-forward sweep — runner extension.

Hypothesis: at the natural timeout, positions still showing strong MFE
and currently close to that MFE are riding momentum. Extending the hold
by N hours gives them room to continue. Targets the gain-side asymmetry
(opposite of trailing-stop which clips winners).

Sweep: extra_candles × min_mfe_bps × min_cur_to_mfe × scope (per strat).

Usage:
    python3 -m backtests.backtest_runner_extension
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
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
    window_specs = [(lab, int((end_dt-relativedelta(months=m)).timestamp()*1000)) for lab,m in WINDOWS]
    end_ts = latest_ts

    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=CAP, oi_data=oi_data, early_exit_params=early_exit, funding_data=funding_data,
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    candidates = []
    for extra_h in [12, 24, 48]:
        for min_mfe in [300, 500, 800, 1200]:
            for min_ratio in [0.3, 0.5, 0.7]:
                for scope_label, scope in [("all", None), ("S5+S9", {"S5","S9"}), ("S5", {"S5"}), ("S9", {"S9"})]:
                    candidates.append((f"+{extra_h}h MFE≥{min_mfe} cur/mfe≥{min_ratio} scope={scope_label}",
                                       {"extra_candles": extra_h//4, "min_mfe_bps": min_mfe,
                                        "min_cur_to_mfe": min_ratio, "strategies": scope}))

    print(f"\nSweep: {len(candidates)} combos × {len(WINDOWS)} = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, cfg in candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, runner_extension=cfg, **common)
            rs[label] = r
        results[name] = rs
        d = {lab: rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs}
        d_dd = {lab: rs[lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        avg_dd = sum(d_dd.values())/4
        if positives >= 2:  # only print decent ones to keep log readable
            print(f"  {name:55s}  Δ28m={d['28m']:+7.1f} Δ12m={d['12m']:+6.1f} "
                  f"Δ6m={d['6m']:+5.1f} Δ3m={d['3m']:+5.1f} ΔDD={avg_dd:+5.1f} {positives}/4")

    print(f"\n{'=' * 100}\n{'4/4 DD intact':^100}\n{'=' * 100}")
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs]
        d_dd = [results[name][lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd)/4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}: avg ΔPnL {sum(d_pnl)/4:+.1f}pp  ΔDD {sum(d_dd)/4:+.1f}pp")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
