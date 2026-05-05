"""Walk-forward sweep — vol_z-conditional sizing reduction.

Hypothesis: high entry-time vol_z signals an "elevated regime" for that
specific token. Reduce position size on the highest-vol entries to clip
dollar losses without skipping the trade entirely (preserves potential
upside, just smaller).

Differs from the rejected `Extreme-condition entry filters` (hard cap
S5 vol_z > 4/5/6) — that was a SKIP, this is a SIZE reduction. Softer
intervention, may pass where hard caps failed.

Tests grid: (vol_z_threshold, size_multiplier) on S5/S9/S5+S9, walk-forward
on the 4 reference windows.

Uses the size_fn hook added to run_window in v11.7.28+.

Usage:
    python3 -m backtests.backtest_volz_sizing
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


def make_size_fn(vol_z_thr: float, mult: float, target_strats: set[str]):
    """Returns size_fn: applies `mult` to candidates of target_strats whose
    entry-time vol_z exceeds threshold. 1.0 (no change) otherwise."""
    def f(cand, feat, _n_positions=0):
        if cand["strat"] not in target_strats:
            return 1.0
        if feat is None:
            return 1.0
        vz = feat.get("vol_z", 0)
        if vz >= vol_z_thr:
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

    print("\nBaseline (no vol-conditional sizing — note: dispersion gate v11.7.28 is active):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    candidates: list[tuple[str, float, float, set]] = []
    for thr in [1.5, 2.0, 2.5, 3.0]:
        for mult in [0.5, 0.7]:
            for strats in [{"S5", "S9"}, {"S5"}, {"S9"}]:
                tag = "+".join(sorted(strats))
                candidates.append((f"{tag} vol_z≥{thr} ×{mult}", thr, mult, strats))

    print(f"\nSweep: {len(candidates)} combos × {len(WINDOWS)} windows")
    t0 = time.time()
    results = {}
    for name, thr, mult, strats in candidates:
        size_fn = make_size_fn(thr, mult, strats)
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common)
            rs[label] = r
        results[name] = rs
        d = {lab: rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        print(f"  {name:30s}  Δ28m={d['28m']:+7.1f}  Δ12m={d['12m']:+6.1f}  "
              f"Δ6m={d['6m']:+5.1f}  Δ3m={d['3m']:+4.1f}  {positives}/4")

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
