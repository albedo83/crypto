"""Walk-forward sweep — concurrent-exposure throttle.

Hypothesis: when many positions are already open, the portfolio is more
correlated to a single market move. Reducing size of new entries (rather
than blocking them entirely) preserves diversification while clipping
dollar exposure during high-concurrency moments.

Tested params: trigger threshold (n_positions ≥ K) × multiplier × scope.
Walk-forward across 28m / 12m / 6m / 3m. Robust = 4/4 ΔPnL > 0 with no
DD penalty.

Note on relation to existing protections:
- MAX_POSITIONS=6 already hard-caps total
- MAX_SAME_DIRECTION=4 caps directional concentration
- LOSS_STREAK_MULTIPLIER reduces sizing after recent losses (different trigger)

This sweep tests a concurrent-exposure trigger, not yet covered.

Usage:
    python3 -m backtests.backtest_concurrent_throttle
"""
from __future__ import annotations

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


def make_throttle_fn(threshold: int, mult: float, scope: str = "all"):
    """Returns size_fn: when n_positions >= threshold, apply multiplier.
    scope = 'all' or strategy name (S5/S9/etc) to limit reduction."""
    def f(cand, feat, n_positions=0):
        if scope != "all" and cand["strat"] != scope:
            return 1.0
        if n_positions >= threshold:
            return mult
        return 1.0
    return f


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

    print("\nBaseline (with v11.7.28 dispersion gate active):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    candidates: list[tuple[str, int, float, str]] = []
    for thr in [3, 4, 5]:
        for mult in [0.5, 0.7]:
            for scope in ["all", "S5", "S9"]:
                candidates.append((f"n≥{thr} ×{mult} on {scope}", thr, mult, scope))

    print(f"\nSweep: {len(candidates)} combos × {len(WINDOWS)} windows")
    t0 = time.time()
    results = {}
    for name, thr, mult, scope in candidates:
        fn = make_throttle_fn(thr, mult, scope)
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=fn, **common)
            rs[label] = r
        results[name] = rs
        d = {lab: rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        print(f"  {name:25s}  Δ28m={d['28m']:+8.1f}  Δ12m={d['12m']:+7.1f}  "
              f"Δ6m={d['6m']:+6.1f}  Δ3m={d['3m']:+5.1f}  {positives}/4")

    print(f"\n{'=' * 100}\n{'Robust (4/4 ΔPnL>0 AND ΔDD avg ≤ 0.5pp)':^100}\n{'=' * 100}")
    found = []
    for name, _, _, _ in candidates:
        d_pnl = [results[name][lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs]
        d_dd = [results[name][lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd)/4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none with DD intact)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}: avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.1f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
