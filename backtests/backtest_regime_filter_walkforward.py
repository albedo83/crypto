"""Walk-forward sweep — BTC short-term momentum regime filters.

Phase 2 of regime filter R&D (R&D triggered by live divergence 2026-05-16).

Phase 0/1 EDA (backtests/eda_regime_filter.py) identified 6 strong signals:
- S5 LONG  × BTC-4h:  worst bucket [-2%, -0.5%]  → avg_net -310bps (n=52)
- S5 LONG  × BTC-8h:  worst bucket [-5%, -2%]    → avg_net -179bps (n=31)
- S5 SHORT × BTC-24h: worst bucket [+2%, +5%]    → avg_net -188bps (n=24)
- S9 SHORT × BTC-8h:  worst bucket [-0.5%, +0.5%] → avg_net -257bps (n=25)
- S9 SHORT × BTC-4h:  worst bucket [-0.5%, +0.5%] → avg_net -147bps (n=30)
- S8 LONG  × BTC-4h:  worst bucket [-5%, -2%]    → avg_net -543bps (n=16) [counter-intuitive]

This walk-forward tests:
1. Each filter individually (verify it survives without other filters)
2. Each filter with multiple threshold variants (robustness check)
3. Combinations (additivity / interaction)
4. Strict 4/4 pass: ΔPnL > 0 on all (28m/12m/6m/3m), ΔDD ≤ +2pp each.

Usage:
    .venv/bin/python3 -m backtests.backtest_regime_filter_walkforward
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)


def make_btc_pre_filter(btc_data: list, hours_back: int,
                        skip_strat: str, skip_dir: int,
                        lo_pct: float, hi_pct: float):
    """Returns a skip_fn: skip entries on (strat, dir) when BTC %-return over
    -hours_back is in [lo_pct, hi_pct]."""
    btc_ts = np.array([c["t"] for c in btc_data], dtype=np.int64)
    btc_close = np.array([float(c["c"]) for c in btc_data])

    def btc_return_pct(ts: int) -> float | None:
        target = ts
        start = ts - hours_back * 3600 * 1000
        if start < int(btc_ts[0]) or target > int(btc_ts[-1]):
            return None
        end_idx = int(np.searchsorted(btc_ts, target, side="right")) - 1
        start_idx = int(np.searchsorted(btc_ts, start, side="right")) - 1
        if end_idx < 0 or start_idx < 0:
            return None
        p_end = btc_close[end_idx]
        p_start = btc_close[start_idx]
        if p_start <= 0:
            return None
        return (p_end / p_start - 1) * 100

    def skip(coin: str, ts: int, strat: str, direction: int) -> bool:
        if strat != skip_strat or direction != skip_dir:
            return False
        r = btc_return_pct(ts)
        if r is None:
            return False
        return lo_pct <= r < hi_pct

    return skip


def make_combo_filter(filters: list):
    """OR-combine multiple skip_fns."""
    def skip(coin, ts, strat, direction):
        for f in filters:
            if f(coin, ts, strat, direction):
                return True
        return False
    return skip


def main():
    print("=" * 110)
    print("Walk-forward sweep — BTC short-term momentum regime filters")
    print("=" * 110)

    print("\nLoading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()
    btc_data = sorted(data["BTC"], key=lambda c: c["t"])
    latest_ts = btc_data[-1]["t"]
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  Data ends: {end_dt.date()}\n")

    WIN_LABELS = ["28 mois", "12 mois", "6 mois", "3 mois"]
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]
    windows.sort(key=lambda w: WIN_LABELS.index(w[0]))

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    # ── Build filter variants (each tested independently) ─────────────────
    # Naming convention: F<strat><dir>_<window>_<bucket_label>
    # Lo/hi bounds chosen to test the worst bucket from EDA plus adjacent
    # widenings (asymmetric / wider / narrower) for robustness.

    variants = {
        "BASELINE": None,

        # ── S5 LONG filters ─────────────────────────────────────────────
        "F1a: S5 LONG  skip BTC-4h [-2.0, -0.5]":
            make_btc_pre_filter(btc_data, 4, "S5", 1, -2.0, -0.5),
        "F1b: S5 LONG  skip BTC-4h [-3.0, -0.5] (wider)":
            make_btc_pre_filter(btc_data, 4, "S5", 1, -3.0, -0.5),
        "F1c: S5 LONG  skip BTC-4h [-2.0,  0.0] (asymm)":
            make_btc_pre_filter(btc_data, 4, "S5", 1, -2.0, 0.0),
        "F1d: S5 LONG  skip BTC-8h [-5.0, -2.0]":
            make_btc_pre_filter(btc_data, 8, "S5", 1, -5.0, -2.0),
        "F1e: S5 LONG  skip BTC-8h [-5.0, -1.0] (wider)":
            make_btc_pre_filter(btc_data, 8, "S5", 1, -5.0, -1.0),

        # ── S5 SHORT filters ────────────────────────────────────────────
        "F2a: S5 SHORT skip BTC-24h [+2.0, +5.0]":
            make_btc_pre_filter(btc_data, 24, "S5", -1, 2.0, 5.0),
        "F2b: S5 SHORT skip BTC-24h [+2.0, +10] (wider)":
            make_btc_pre_filter(btc_data, 24, "S5", -1, 2.0, 10.0),
        "F2c: S5 SHORT skip BTC-24h [+1.5, +5.0] (lower)":
            make_btc_pre_filter(btc_data, 24, "S5", -1, 1.5, 5.0),

        # ── S9 SHORT filters (the catastrophic live bleeder) ────────────
        "F3a: S9 SHORT skip BTC-8h [-0.5, +0.5] (flat)":
            make_btc_pre_filter(btc_data, 8, "S9", -1, -0.5, 0.5),
        "F3b: S9 SHORT skip BTC-8h [-1.0, +1.0] (wider)":
            make_btc_pre_filter(btc_data, 8, "S9", -1, -1.0, 1.0),
        "F3c: S9 SHORT skip BTC-4h [-0.5, +0.5]":
            make_btc_pre_filter(btc_data, 4, "S9", -1, -0.5, 0.5),
        "F3d: S9 SHORT skip BTC-4h [-1.0, +1.0] (wider)":
            make_btc_pre_filter(btc_data, 4, "S9", -1, -1.0, 1.0),

        # ── S8 LONG filters (counter-intuitive — needs confirmed bounce) ─
        "F4a: S8 LONG  skip BTC-4h [-5.0, -2.0]":
            make_btc_pre_filter(btc_data, 4, "S8", 1, -5.0, -2.0),
        "F4b: S8 LONG  skip BTC-4h [-5.0, -1.0] (wider)":
            make_btc_pre_filter(btc_data, 4, "S8", 1, -5.0, -1.0),
        "F4c: S8 LONG  skip BTC-4h [-3.0, -1.0]":
            make_btc_pre_filter(btc_data, 4, "S8", 1, -3.0, -1.0),
    }

    # COMBOs (best of each strategy)
    combo_keys = [
        ("F1a", -2.0, -0.5, 4, "S5", 1),
        ("F2a", 2.0, 5.0, 24, "S5", -1),
        ("F3a", -0.5, 0.5, 8, "S9", -1),
        ("F4a", -5.0, -2.0, 4, "S8", 1),
    ]
    combo_filters = [
        make_btc_pre_filter(btc_data, hb, s, d, lo, hi)
        for (_, lo, hi, hb, s, d) in combo_keys
    ]
    variants["COMBO: F1a+F2a+F3a+F4a"] = make_combo_filter(combo_filters)
    variants["COMBO: F1a+F3a (S5L + S9S)"] = make_combo_filter([combo_filters[0], combo_filters[2]])
    variants["COMBO: F3a only (S9 SHORT alone)"] = combo_filters[2]

    # ── Run sweep ─────────────────────────────────────────────────────────
    results = {}
    cap = 500.0  # match live current capital scale
    for name, skip_fn in variants.items():
        print(f"  Running {name}...")
        results[name] = {}
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(
                features, data, sector_features, dxy_data,
                start_ts, latest_ts,
                start_capital=cap,
                oi_data=oi_data,
                early_exit_params=early_exit_params,
                runner_extension=runner_ext_cfg,
                funding_data=funding_data,
                apply_adaptive_modulator=True,
                skip_fn=skip_fn,
            )
            results[name][label] = r

    # ── Report ────────────────────────────────────────────────────────────
    base = results["BASELINE"]
    print(f"\n{'='*110}")
    print(f"Baseline (start capital ${cap:.0f}):")
    for w in WIN_LABELS:
        print(f"  {w}: end=${base[w]['end_capital']:,.0f} ({base[w]['pnl_pct']:+.1f}%) "
              f"DD={base[w]['max_dd_pct']:.1f}% n_trades={base[w]['n_trades']}")

    print(f"\n{'='*110}")
    print(f"{'Variant':<48} {'28m Δ$':>11} {'12m Δ$':>11} {'6m Δ$':>11} {'3m Δ$':>11} {'DD (28/12/6/3)':>27}  Pass")
    print("-" * 130)
    passers = []
    for name in variants:
        if name == "BASELINE":
            continue
        r = results[name]
        row = f"{name:<48}"
        deltas_pos = []
        dd_ok = []
        for w in WIN_LABELS:
            delta = r[w]["end_capital"] - base[w]["end_capital"]
            row += f"  ${delta:>+9.0f}"
            deltas_pos.append(delta > 0)
            ddd = r[w]["max_dd_pct"] - base[w]["max_dd_pct"]
            dd_ok.append(ddd >= -2.0)
        dd_str = " / ".join(f"{r[w]['max_dd_pct']:+5.1f}%" for w in WIN_LABELS)
        all_pos = all(deltas_pos)
        all_dd = all(dd_ok)
        if all_pos and all_dd:
            verdict = "✓ PASS"
            passers.append(name)
        elif all_pos:
            verdict = "+(DD)"
        elif sum(deltas_pos) >= 3:
            verdict = "~3/4"
        else:
            verdict = "✗"
        print(f"{row}  {dd_str}  {verdict}")

    print(f"\n{'='*110}")
    if passers:
        print(f"STRICT 4/4 PASS variants ({len(passers)}):")
        for p in passers:
            r = results[p]
            cum_delta = sum(r[w]["end_capital"] - base[w]["end_capital"] for w in WIN_LABELS)
            avg_ddd = float(np.mean([r[w]["max_dd_pct"] - base[w]["max_dd_pct"] for w in WIN_LABELS]))
            print(f"  {p}")
            print(f"      cum Δ$ = ${cum_delta:+,.0f}    avg ΔDD = {avg_ddd:+.2f}pp")
    else:
        print("NO variant passes strict 4/4 + DD. Check '+' partial wins above.")

    print("\nDone.")


if __name__ == "__main__":
    main()
