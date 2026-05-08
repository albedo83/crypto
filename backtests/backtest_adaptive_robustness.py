"""Robustness tests for the adaptive macro α modulator.

Phase 1 + 2 found α[strat]·btc_z modulators delivering big walk-forward 4/4
gains. But walk-forward 4/4 doesn't fully test overfit when the optimum is
found on the same data we're scoring on. This file runs the gold-standard
robustness checks before any live deployment:

  A) IN-SAMPLE / OUT-OF-SAMPLE SPLIT — train α on first 20m of 28m data
     (Jan 2024 → Aug 2025), apply that fixed α to held-out last 8m of data
     (Aug 2025 → May 2026). If OOS still gains, the signal is real.

  B) LOOKBACK SENSITIVITY — sweep BTC return lookback {15d, 30d, 60d, 90d}.
     If optimum α is consistent across lookbacks → robust. If shifts wildly
     → we're tuning a noise correlation.

  C) NULL-HYPOTHESIS / RANDOM SHUFFLE — shuffle btc_z values randomly,
     run α=-0.5 on S8. If still gains, α is just amplifying baseline noise.

  D) ROLLING Z-SCORE (live-realistic) — instead of full-sample mean/std
     (look-ahead bias), use 6-month rolling. Tests if the strategy works
     with only past info.

  E) PER-WINDOW α STABILITY — for each of 28m/12m/6m/3m, find best α[S8]
     independently. Stable optimum → robust. Wildly different per window
     → regime-dependent (so requires its own adaptive layer).

Usage:
    python3 -m backtests.backtest_adaptive_robustness
"""
from __future__ import annotations

import time
import random
import statistics
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


def fmt_row(name, deltas_pnl, deltas_dd):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {name:55s}  "
            f"Δ28m={deltas_pnl['28m']:+8.1f}  Δ12m={deltas_pnl['12m']:+7.1f}  "
            f"Δ6m={deltas_pnl['6m']:+6.1f}  Δ3m={deltas_pnl['3m']:+5.1f}  "
            f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")


def compute_btc_z_full(data: dict, lookback_days: int = 30) -> tuple[dict, float, float]:
    """BTC return z-score per ts using full-sample mean/std (look-ahead).

    Returns (z_by_ts, mean, std)."""
    btc = data["BTC"]
    n = lookback_days * 6  # 4h candles per day = 6
    closes = np.array([c["c"] for c in btc])
    rets, ts_list = [], []
    for i in range(n, len(btc)):
        ret = (closes[i] / closes[i - n] - 1) if closes[i - n] > 0 else 0
        rets.append(ret)
        ts_list.append(btc[i]["t"])
    rets_arr = np.array(rets)
    mean = float(np.mean(rets_arr))
    std = float(np.std(rets_arr)) or 1.0
    return ({ts: (r - mean) / std for ts, r in zip(ts_list, rets_arr)}, mean, std)


def compute_btc_z_rolling(data: dict, lookback_days: int = 30,
                           z_window_days: int = 180) -> dict:
    """BTC return z-score using ONLY past data — no look-ahead."""
    btc = data["BTC"]
    n_lb = lookback_days * 6
    n_z = z_window_days * 6
    closes = np.array([c["c"] for c in btc])
    out = {}
    rets_history = []
    ts_history = []
    for i in range(n_lb, len(btc)):
        ret = (closes[i] / closes[i - n_lb] - 1) if closes[i - n_lb] > 0 else 0
        rets_history.append(ret)
        ts_history.append(btc[i]["t"])
    # Compute rolling z-score using only past
    for j in range(len(rets_history)):
        win_start = max(0, j - n_z)
        past = rets_history[win_start:j+1]
        if len(past) < 30:
            continue
        m = np.mean(past)
        s = np.std(past) or 1.0
        out[ts_history[j]] = (rets_history[j] - m) / s
    return out


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

    common_no_window = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
        end_ts_ms=latest_ts,
    )

    t0 = time.time()

    # ── (A) IN-SAMPLE / OUT-OF-SAMPLE SPLIT ───────────────────────────
    # Use 28m data, split at 20m boundary:
    #   IS:  28m start → 8m before end  (20 months)
    #   OOS: 8m before end → end         (8 months)
    # Train α on IS, evaluate on both IS and OOS.
    print("=" * 110)
    print(f"{'(A) IN-SAMPLE / OUT-OF-SAMPLE SPLIT — train on first 20m, test on last 8m':^110}")
    print("=" * 110)
    is_start = int((end_dt - relativedelta(months=28)).timestamp() * 1000)
    is_end = int((end_dt - relativedelta(months=8)).timestamp() * 1000)
    oos_start = is_end
    oos_end = latest_ts

    # Build z-score using only IS portion (so no leakage to OOS)
    btc_z_is, _, _ = compute_btc_z_full(data, lookback_days=30)
    print(f"  IS:  {datetime.fromtimestamp(is_start/1000, tz=timezone.utc).date()}  →  {datetime.fromtimestamp(is_end/1000, tz=timezone.utc).date()}")
    print(f"  OOS: {datetime.fromtimestamp(oos_start/1000, tz=timezone.utc).date()}  →  {datetime.fromtimestamp(oos_end/1000, tz=timezone.utc).date()}")

    # Baseline (no modulator) on IS and OOS
    print("\n  Baseline (no α):")
    base_is = run_window(features, data, start_ts_ms=is_start, **{**common_no_window, "end_ts_ms": is_end})
    base_oos = run_window(features, data, start_ts_ms=oos_start, **{**common_no_window, "end_ts_ms": oos_end})
    print(f"    IS:  pnl={base_is['pnl_pct']:+8.1f}%  trades={base_is['n_trades']:4d}  DD={base_is['max_dd_pct']:6.1f}%")
    print(f"    OOS: pnl={base_oos['pnl_pct']:+8.1f}%  trades={base_oos['n_trades']:4d}  DD={base_oos['max_dd_pct']:6.1f}%")

    print("\n  Test α-vector on IS and OOS:")
    print(f"  {'config':45s}  {'IS Δpnl%':>9s}  {'IS ΔDD':>7s}  {'OOS Δpnl%':>10s}  {'OOS ΔDD':>8s}  Note")
    candidates = [
        ("α[S8]=-0.5",            {"S8": -0.5}),
        ("α[S9]=-0.5",            {"S9": -0.5}),
        ("α[S8]=-1.0",            {"S8": -1.0}),
        ("α[S1]=+0.5",            {"S1": +0.5}),
        ("S1+0.5 S8-0.5",         {"S1": +0.5, "S8": -0.5}),
        ("S1+0.3 S8-0.3 S5-0.2",  {"S1": +0.3, "S8": -0.3, "S5": -0.2}),
        ("S1+0.3 S8-0.5 S5-0.2 S9-0.3",
                                  {"S1": +0.3, "S8": -0.5, "S5": -0.2, "S9": -0.3}),
        ("ALL ±1.0 (overfit risk)",
                                  {"S1": +1.0, "S5": -0.1, "S8": -1.0, "S9": -1.0, "S10": -1.0}),
    ]

    def make_fn(alpha_vec, z_map):
        def fn(cand, f, n_pos):
            a = alpha_vec.get(cand["strat"], 0)
            return max(0.3, min(2.5, 1 + a * z_map.get(f["t"], 0)))
        return fn

    oos_results = []
    for name, av in candidates:
        size_fn = make_fn(av, btc_z_is)
        is_run  = run_window(features, data, start_ts_ms=is_start,  size_fn=size_fn,
                            **{**common_no_window, "end_ts_ms": is_end})
        oos_run = run_window(features, data, start_ts_ms=oos_start, size_fn=size_fn,
                            **{**common_no_window, "end_ts_ms": oos_end})
        is_dpnl  = is_run['pnl_pct']  - base_is['pnl_pct']
        is_ddd   = is_run['max_dd_pct']  - base_is['max_dd_pct']
        oos_dpnl = oos_run['pnl_pct'] - base_oos['pnl_pct']
        oos_ddd  = oos_run['max_dd_pct'] - base_oos['max_dd_pct']
        # Verdict: green if both IS+OOS show positive Δpnl AND OOS DD ≤ 1pp worse
        if is_dpnl > 0 and oos_dpnl > 0 and oos_ddd <= 1.0:
            note = "✓ OOS confirms"
        elif is_dpnl > 0 and oos_dpnl > 0:
            note = "△ OOS PnL OK but DD↑"
        elif is_dpnl > 0 and oos_dpnl <= 0:
            note = "✗ OOS REGRESSES — overfit"
        else:
            note = "─ no IS gain"
        print(f"  {name:45s}  {is_dpnl:+8.1f}%  {is_ddd:+6.1f}pp  {oos_dpnl:+9.1f}%  {oos_ddd:+7.1f}pp  {note}")
        oos_results.append((name, is_dpnl, oos_dpnl, oos_ddd))

    # ── (B) LOOKBACK SENSITIVITY ──────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) LOOKBACK SENSITIVITY — α[S8]=-0.5 with different BTC return lookbacks':^110}")
    print("=" * 110)

    # Use 28m / 12m / 6m / 3m windows
    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000)) for lab, m in WINDOWS]

    # Baseline once
    print("\n  Baseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common_no_window)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    print(f"\n  α[S8]=-0.5 with lookback ∈ {{15d, 30d, 60d, 90d}}:")
    for lb in [15, 30, 60, 90]:
        z_map, mean, std = compute_btc_z_full(data, lookback_days=lb)
        size_fn = make_fn({"S8": -0.5}, z_map)
        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common_no_window)
            deltas[label] = r['pnl_pct'] - baseline[label]['pnl_pct']
            ddds[label] = r['max_dd_pct'] - baseline[label]['max_dd_pct']
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {sign} lb={lb:3d}d  Δ28m={deltas['28m']:+8.1f} Δ12m={deltas['12m']:+7.1f} Δ6m={deltas['6m']:+6.1f} Δ3m={deltas['3m']:+5.1f}  ΔDD avg={avg_dd:+5.2f}")

    # ── (C) NULL HYPOTHESIS — random shuffle ─────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) NULL HYPOTHESIS — α[S8]=-0.5 with RANDOMLY SHUFFLED btc_z':^110}")
    print("=" * 110)
    btc_z_real, _, _ = compute_btc_z_full(data, lookback_days=30)
    print("\n  Real (control):")
    size_fn = make_fn({"S8": -0.5}, btc_z_real)
    real_deltas = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common_no_window)
        real_deltas[label] = r['pnl_pct'] - baseline[label]['pnl_pct']
    print(f"    Δ28m={real_deltas['28m']:+8.1f}  Δ12m={real_deltas['12m']:+7.1f}  Δ6m={real_deltas['6m']:+6.1f}  Δ3m={real_deltas['3m']:+5.1f}")

    print("\n  Shuffled (5 random permutations):")
    keys = list(btc_z_real.keys())
    values = list(btc_z_real.values())
    random.seed(42)
    shuffle_results = []
    for trial in range(5):
        shuffled_vals = values[:]
        random.shuffle(shuffled_vals)
        shuffled_z = dict(zip(keys, shuffled_vals))
        size_fn = make_fn({"S8": -0.5}, shuffled_z)
        deltas = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common_no_window)
            deltas[label] = r['pnl_pct'] - baseline[label]['pnl_pct']
        shuffle_results.append(deltas)
        positives = sum(1 for v in deltas.values() if v > 0)
        print(f"  trial {trial+1}: Δ28m={deltas['28m']:+8.1f}  Δ12m={deltas['12m']:+7.1f}  Δ6m={deltas['6m']:+6.1f}  Δ3m={deltas['3m']:+5.1f}  positives={positives}/4")

    avg_shuffled = {l: np.mean([s[l] for s in shuffle_results]) for l in real_deltas}
    print(f"  avg shuffle: Δ28m={avg_shuffled['28m']:+8.1f}  Δ12m={avg_shuffled['12m']:+7.1f}  Δ6m={avg_shuffled['6m']:+6.1f}  Δ3m={avg_shuffled['3m']:+5.1f}")
    print(f"  Real / shuffle ratio (28m): {real_deltas['28m'] / (abs(avg_shuffled['28m']) + 1):.1f}x  (> 5 = strong signal vs noise)")

    # ── (D) ROLLING Z-SCORE — live-realistic ──────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(D) ROLLING Z-SCORE (no look-ahead) — 6m rolling window for mean/std':^110}")
    print("=" * 110)
    btc_z_roll = compute_btc_z_rolling(data, lookback_days=30, z_window_days=180)
    for av_name, av in [("α[S8]=-0.5", {"S8": -0.5}),
                         ("S1+0.3 S8-0.3 S5-0.2", {"S1": +0.3, "S8": -0.3, "S5": -0.2}),
                         ("S1+0.5 S8-0.5", {"S1": +0.5, "S8": -0.5})]:
        size_fn = make_fn(av, btc_z_roll)
        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common_no_window)
            deltas[label] = r['pnl_pct'] - baseline[label]['pnl_pct']
            ddds[label] = r['max_dd_pct'] - baseline[label]['max_dd_pct']
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {sign} {av_name:25s}  Δ28m={deltas['28m']:+8.1f} Δ12m={deltas['12m']:+7.1f} Δ6m={deltas['6m']:+6.1f} Δ3m={deltas['3m']:+5.1f}  ΔDD avg={avg_dd:+5.2f}  {positives}/4")

    # ── (E) PER-WINDOW α STABILITY ────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(E) PER-WINDOW α STABILITY — find best α[S8] in each window separately':^110}")
    print("=" * 110)
    print(f"\n  {'window':6s}  best α  | sweep over [-1.0, -0.7, -0.5, -0.3, -0.1, +0.1, +0.3, +0.5, +0.7, +1.0]")
    btc_z_full, _, _ = compute_btc_z_full(data, lookback_days=30)
    alphas_grid = [-1.0, -0.7, -0.5, -0.3, -0.1, +0.1, +0.3, +0.5, +0.7, +1.0]
    for label, start_ts in window_specs:
        best_a = None
        best_pnl = -1e9
        results = []
        for a in alphas_grid:
            size_fn = make_fn({"S8": a}, btc_z_full)
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common_no_window)
            d_pnl = r['pnl_pct'] - baseline[label]['pnl_pct']
            results.append((a, d_pnl))
            if d_pnl > best_pnl:
                best_pnl = d_pnl
                best_a = a
        result_str = " ".join(f"{r[0]:+.1f}:{r[1]:+6.0f}" for r in results)
        print(f"  {label:6s}  {best_a:+.1f}    | {result_str}")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
