"""basket_haircut_eda — risk-side EDA.

Question: do low-effective_n events precede balance drawdowns, and on which
lookback window (7d / 14d / 30d) is the signal strongest?

Method:
  1. Reconstruct the per-ts equity curve from the basket_ts dump (capital +
     basket_unrealized = mark-to-market equity).
  2. Identify "DD events" on equity: peak-to-valley drops of ≥ X bps within
     ≤ Y candles. Calibrate (X, Y) so 28m yields ~20-40 events.
  3. For each DD event, look at eff_n_{7,14,30}d at start-of-DD and N candles
     prior (1, 3, 7, 14 candles ≈ 4h, 12h, 28h, 56h before DD onset).
  4. Mann-Whitney U: mean(eff_n at DD start) vs mean(eff_n outside DD).
  5. Decile analysis: what fraction of "low eff_n" candles are followed by
     a DD onset within 7 days vs the base rate?

Reads:  backtests/basket_haircut_eda_data/basket_ts_28m.jsonl
Writes: backtests/basket_haircut_eda_data/riskside_summary.json
"""
from __future__ import annotations
import json
import os
import math
from collections import defaultdict
from statistics import mean, median, stdev

import numpy as np
from scipy.stats import mannwhitneyu  # type: ignore


DATA_DIR = "/home/crypto/backtests/basket_haircut_eda_data"
WINDOWS = (7, 14, 30)


def load_ts(label: str) -> list[dict]:
    path = f"{DATA_DIR}/basket_ts_{label}.jsonl"
    out = []
    with open(path) as fh:
        for line in fh:
            out.append(json.loads(line))
    return out


def equity_curve(rows: list[dict]) -> np.ndarray:
    """capital + basket_unrealized at each ts."""
    eq = np.array([r["capital"] + r["basket_unreal"] for r in rows], dtype=float)
    return eq


def detect_dd_events(eq: np.ndarray, drop_pct: float, max_candles: int) -> list[dict]:
    """Find peak-to-valley drops where the valley is within `max_candles`
    candles of the peak and the drop is ≥ `drop_pct` percent of the peak.

    Greedy: walks left→right tracking a rolling peak. When current price falls
    `drop_pct` below the peak AND the peak occurred within `max_candles` of
    now, emit an event with start=peak_idx, end=current_idx. After emit, reset
    the rolling peak to the current valley + continue.
    """
    events = []
    n = len(eq)
    peak_idx = 0
    peak_val = eq[0]
    for i in range(1, n):
        if eq[i] > peak_val:
            peak_val = eq[i]
            peak_idx = i
            continue
        drop = (peak_val - eq[i]) / peak_val * 100 if peak_val > 0 else 0
        if drop >= drop_pct and (i - peak_idx) <= max_candles:
            events.append({
                "peak_idx": peak_idx,
                "valley_idx": i,
                "duration": i - peak_idx,
                "peak_val": float(peak_val),
                "valley_val": float(eq[i]),
                "drop_pct": float(drop),
            })
            peak_idx = i
            peak_val = eq[i]
    return events


def calibrate_dd_threshold(eq: np.ndarray, target_n: int = 30) -> tuple[float, int]:
    """Sweep to find a (drop_pct, max_candles) producing ~target_n events.

    Holds max_candles fixed at 21 candles (3.5 days for 4h grid) and sweeps
    drop_pct. Returns the threshold whose event count is closest to target_n.
    """
    max_c = 21
    best = None
    for dp in [5, 7, 10, 12, 15, 18, 20, 25, 30]:
        ev = detect_dd_events(eq, drop_pct=dp, max_candles=max_c)
        diff = abs(len(ev) - target_n)
        if best is None or diff < best[0]:
            best = (diff, dp, max_c, len(ev))
    return best[1], best[2]


def main():
    print("Loading 28m basket time series...")
    rows = load_ts("28m")
    print(f"  {len(rows)} ts")

    eq = equity_curve(rows)
    print(f"  equity: start={eq[0]:.0f} peak={eq.max():.0f} end={eq[-1]:.0f}")

    dp, mc = calibrate_dd_threshold(eq, target_n=30)
    events = detect_dd_events(eq, drop_pct=dp, max_candles=mc)
    print(f"\nDD events calibration: drop≥{dp}% within ≤{mc} candles → "
          f"{len(events)} events on 28m")

    # Show event distribution
    drops = [e["drop_pct"] for e in events]
    durs = [e["duration"] for e in events]
    if drops:
        print(f"  drop_pct: min={min(drops):.1f} median={median(drops):.1f} "
              f"max={max(drops):.1f}")
        print(f"  duration: min={min(durs)} median={int(median(durs))} max={max(durs)} candles")

    # Build a "in-DD" mask: True from peak_idx to valley_idx for each event.
    # Then evaluate eff_n_W at start-of-DD (peak_idx) and N candles before.
    n_total = len(rows)
    in_dd_mask = np.zeros(n_total, dtype=bool)
    for e in events:
        in_dd_mask[e["peak_idx"]:e["valley_idx"] + 1] = True

    # Per-window analysis
    LAGS = (0, 1, 3, 7, 14)  # candles before peak (0 = at peak)
    summary = {"events": len(events), "drop_pct": dp, "max_candles": mc,
               "windows": {}}

    print("\n=== Eff_n at DD-onset vs baseline ===")
    print(f"{'Window':>8} {'Lag':>4} {'Mean(DD start)':>15} {'Mean(non-DD)':>15} "
          f"{'Delta':>8} {'MW p-value':>12} {'n_dd':>6}")
    for w in WINDOWS:
        key = f"eff_n_{w}d"
        w_results = {"by_lag": {}}
        # Baseline: eff_n where in_dd_mask is False AND value not None
        baseline_vals = [rows[i][key] for i in range(n_total)
                         if not in_dd_mask[i] and rows[i][key] is not None]
        if not baseline_vals:
            print(f"  {w}d: no baseline data")
            continue
        baseline_mean = mean(baseline_vals)
        w_results["baseline_mean"] = baseline_mean
        w_results["baseline_n"] = len(baseline_vals)

        for lag in LAGS:
            dd_vals = []
            for e in events:
                idx = e["peak_idx"] - lag
                if 0 <= idx < n_total:
                    v = rows[idx][key]
                    if v is not None:
                        dd_vals.append(v)
            if len(dd_vals) < 5:
                continue
            dd_mean = mean(dd_vals)
            delta = dd_mean - baseline_mean
            try:
                stat, p = mannwhitneyu(dd_vals, baseline_vals,
                                       alternative="less")
            except ValueError:
                p = float("nan")
            print(f"{w:>7}d {lag:>4} {dd_mean:>15.3f} {baseline_mean:>15.3f} "
                  f"{delta:>+8.3f} {p:>12.4g} {len(dd_vals):>6}")
            w_results["by_lag"][lag] = {
                "dd_mean": dd_mean,
                "delta_vs_baseline": delta,
                "mw_p": float(p),
                "n_dd_samples": len(dd_vals),
            }
        summary["windows"][f"{w}d"] = w_results

    # ── Decile-based predictive analysis ──
    # For each window, partition all ts into eff_n deciles. For each decile,
    # what fraction "leads to a DD onset within 7 days" (42 candles)?
    print("\n=== Decile predictive analysis (DD onset within 7d / 42 candles) ===")
    DD_ONSET_HORIZON = 42  # 7 days * 6 candles/day
    onset_mask = np.zeros(n_total, dtype=bool)
    for e in events:
        # Mark candles such that "peak occurs within next 7 days" — anchor at
        # candle = peak_idx - lag. Easier: for each candle i, set future_onset[i]
        # to True if any event peak falls in (i, i+horizon].
        for back in range(DD_ONSET_HORIZON):
            j = e["peak_idx"] - back
            if j >= 0:
                onset_mask[j] = True
    base_rate = onset_mask.mean()
    print(f"Base rate of 'DD onset within next {DD_ONSET_HORIZON} candles': "
          f"{base_rate * 100:.2f}%")

    for w in WINDOWS:
        key = f"eff_n_{w}d"
        # Use only ts with ≥2 positions (eff_n defined)
        valid = [(i, rows[i][key]) for i in range(n_total)
                 if rows[i][key] is not None and rows[i]["n_pos"] >= 2]
        if len(valid) < 100:
            print(f"  {w}d: only {len(valid)} valid ts, skipping decile analysis")
            continue
        idxs = np.array([v[0] for v in valid])
        vals = np.array([v[1] for v in valid])
        sort_order = np.argsort(vals)
        vals_sorted = vals[sort_order]
        idxs_sorted = idxs[sort_order]
        n_v = len(vals_sorted)
        decile_size = n_v // 10
        print(f"\n  {w}d ({n_v} valid ts, decile size = {decile_size})")
        print(f"    {'Decile':>6} {'eff_n range':>16} {'Hit rate':>10} {'Lift':>8}")
        decile_results = []
        for d in range(10):
            lo = d * decile_size
            hi = (d + 1) * decile_size if d < 9 else n_v
            decile_idxs = idxs_sorted[lo:hi]
            hit_rate = onset_mask[decile_idxs].mean()
            lift = hit_rate / base_rate if base_rate > 0 else 0
            eff_lo = vals_sorted[lo]
            eff_hi = vals_sorted[hi - 1]
            print(f"    {d+1:>6} {eff_lo:.2f}-{eff_hi:.2f}     {hit_rate*100:>7.2f}%  {lift:>5.2f}x")
            decile_results.append({
                "decile": d + 1,
                "eff_n_lo": float(eff_lo),
                "eff_n_hi": float(eff_hi),
                "hit_rate": float(hit_rate),
                "lift": float(lift),
            })
        summary["windows"][f"{w}d"]["deciles"] = decile_results
        summary["windows"][f"{w}d"]["base_rate"] = float(base_rate)

    # ── Median lead time analysis ──
    # For each DD event and each window, find the earliest candle in the 14d
    # preceding the DD onset where eff_n < threshold T (sweep T = 1.5, 2.0, 2.5).
    # Report median lead time across events.
    print("\n=== Median lead time: eff_n < T before DD onset (14d window) ===")
    LOOKBACK_CANDLES = 14 * 6  # 14 days
    for T in (1.5, 2.0, 2.5):
        print(f"\n  Threshold T = {T}")
        print(f"  {'Window':>8} {'Median lead (h)':>16} {'TP rate':>10} {'FP rate':>10}")
        for w in WINDOWS:
            key = f"eff_n_{w}d"
            leads = []  # candles before DD where eff_n first < T
            for e in events:
                p_idx = e["peak_idx"]
                first_lo = None
                for back in range(LOOKBACK_CANDLES):
                    idx = p_idx - back
                    if idx < 0:
                        break
                    v = rows[idx][key]
                    if v is not None and v < T:
                        first_lo = back  # candles before peak
                        break  # we want the FIRST (closest to peak)
                if first_lo is not None:
                    leads.append(first_lo)
            tp = len(leads) / len(events) if events else 0
            # FP: fraction of "low eff_n" candles outside DD-lead-up zone
            lead_zone = np.zeros(n_total, dtype=bool)
            for e in events:
                lead_zone[max(0, e["peak_idx"] - LOOKBACK_CANDLES):e["peak_idx"] + 1] = True
            n_low_outside = sum(1 for i in range(n_total)
                                if rows[i][key] is not None and rows[i][key] < T
                                and not lead_zone[i])
            n_outside = sum(1 for i in range(n_total) if not lead_zone[i])
            fp = n_low_outside / n_outside if n_outside else 0
            med_h = median(leads) * 4 if leads else None  # 4h candles
            print(f"  {w:>7}d {str(round(med_h,1) if med_h else '-'):>16} "
                  f"{tp*100:>8.1f}% {fp*100:>8.1f}%")
            summary["windows"].setdefault(f"{w}d", {})\
                .setdefault("lead_time", {})[f"T={T}"] = {
                    "median_lead_h": med_h,
                    "tp_rate": float(tp),
                    "fp_rate": float(fp),
                    "n_leads": len(leads),
                }

    # Write summary
    out_path = f"{DATA_DIR}/riskside_summary.json"
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=float)
    print(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
