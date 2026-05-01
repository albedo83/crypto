"""Walk-forward sweep — cross-sectional dispersion filter on entries.

Hypothesis: when alts are flying in all directions (high cross-sectional
std of ret_24h), individual signals are noisier and entries are more
likely to whipsaw. Skip entries when dispersion exceeds a threshold.

Distribution of disp_24h on the 3y data: median ~260 bps, p90 ~490, p95
~600, max ~1000. Thresholds tested span the upper tail.

Sweep tests:
  - threshold ∈ {500, 600, 700, 800, 900}
  - applied to: all strats, S5 only, S5+S9 (mean-reversion family)

Usage:
    python3 -m backtests.backtest_dispersion_filter
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def precompute_dispersion(features: dict) -> dict[int, float]:
    """For each timestamp, compute std of ret_6h across all coins."""
    by_ts: dict[int, list[float]] = {}
    for coin, fs in features.items():
        for f in fs:
            ts = f.get("ts") or f.get("t")
            if ts is None:
                continue
            r = f.get("ret_6h")
            if r is None:
                continue
            by_ts.setdefault(ts, []).append(r)
    return {ts: statistics.stdev(rs) for ts, rs in by_ts.items() if len(rs) > 4}


def make_disp_filter(disp_map: dict[int, float], threshold: float, strats: set[str] | None = None):
    def f(coin, ts, strat, direction):
        if strats is not None and strat not in strats:
            return False
        d = disp_map.get(ts)
        return d is not None and d >= threshold
    return f


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Precomputing dispersion at every timestamp...")
    disp_map = precompute_dispersion(features)
    vals = sorted(disp_map.values())
    print(f"  n={len(vals)} timestamps  median={vals[len(vals)//2]:.0f}  "
          f"p75={vals[3*len(vals)//4]:.0f}  p90={vals[int(0.9*len(vals))]:.0f}  "
          f"p95={vals[int(0.95*len(vals))]:.0f}  max={max(vals):.0f}")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    window_specs = []
    for label, months in WINDOWS:
        start_dt = end_dt - relativedelta(months=months)
        window_specs.append((label, int(start_dt.timestamp() * 1000)))
    end_ts = latest_ts

    common = dict(
        sector_features=sector_features,
        dxy_data=dxy_data,
        end_ts_ms=end_ts,
        start_capital=CAP,
        oi_data=oi_data,
        early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    candidates = []
    for thr in [500, 600, 700, 800, 900]:
        candidates.append((f"skip ALL when disp≥{thr}", make_disp_filter(disp_map, thr)))
        candidates.append((f"skip S5 when disp≥{thr}", make_disp_filter(disp_map, thr, {"S5"})))
        candidates.append((f"skip S5+S9 when disp≥{thr}", make_disp_filter(disp_map, thr, {"S5", "S9"})))

    print(f"\nSweep: {len(candidates)} filters × {len(WINDOWS)} windows = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, fn in candidates:
        results[name] = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=fn, **common)
            results[name][label] = r
        d = {lab: results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        n_trades = {lab: results[name][lab]["n_trades"] for lab, _ in window_specs}
        print(f"  {name:35s}  Δ28m={d['28m']:+7.1f}  Δ12m={d['12m']:+7.1f}  "
              f"Δ6m={d['6m']:+6.1f}  Δ3m={d['3m']:+5.1f}  "
              f"trades28m={n_trades['28m']}  {positives}/4")

    print(f"\n{'=' * 100}\n{'Robust 4/4 candidates':^100}\n{'=' * 100}")
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [results[name][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl):
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}: avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.1f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
