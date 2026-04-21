"""Walk-forward sweep for momentum-reversal exits.

Unlike div_erosion (which tried to exit when the entry signal weakens), this
test uses the raw price tape as a reversal detector:

    If position is in profit >= min_gain AND the last N×4h candles showed a
    move AGAINST the position direction of magnitude >= adverse → exit.

Different from S10-style trailing (which locks MFE-offset): here we only
trigger when the recent bars actively oppose us. A trade at +500 bps that
stays at +500 bps won't exit; only one that moved +500 → +X while the
adverse short-window move is large will.

Applied to all non-S10 trades (S10 already has a trailing rule).
Baseline = current live (D2 dead-timeout + OI gate + blacklist, no rev exit).
"""
from __future__ import annotations

from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


# Each variant: lookback in 4h candles, adverse move in bps, min_gain in bps,
# optionally scope to specific strategies. The adverse move is measured in the
# position's direction (i.e. dir × (current/prev - 1)), so a LONG that dumps
# from $10 to $9 has adverse = -1000 bps.
VARIANTS = [
    ("BASELINE (no reversal exit)",                          None),
    # Fast (single 4h bar)
    ("R1  1c adv=300 gain=300 (all)",                        dict(lookback_candles=1, adverse_bps=300, min_gain_bps=300)),
    ("R2  1c adv=500 gain=300 (all)",                        dict(lookback_candles=1, adverse_bps=500, min_gain_bps=300)),
    ("R3  1c adv=500 gain=500 (all)",                        dict(lookback_candles=1, adverse_bps=500, min_gain_bps=500)),
    # Medium (8h = 2 candles)
    ("R4  2c adv=500 gain=500 (all)",                        dict(lookback_candles=2, adverse_bps=500, min_gain_bps=500)),
    ("R5  2c adv=800 gain=500 (all)",                        dict(lookback_candles=2, adverse_bps=800, min_gain_bps=500)),
    ("R6  2c adv=800 gain=800 (all)",                        dict(lookback_candles=2, adverse_bps=800, min_gain_bps=800)),
    # Slower (12h = 3 candles)
    ("R7  3c adv=600 gain=500 (all)",                        dict(lookback_candles=3, adverse_bps=600, min_gain_bps=500)),
    ("R8  3c adv=1000 gain=800 (all)",                       dict(lookback_candles=3, adverse_bps=1000, min_gain_bps=800)),
    # S5 only
    ("R9  2c adv=800 gain=500 (S5 only)",                    dict(lookback_candles=2, adverse_bps=800, min_gain_bps=500, strategies=["S5"])),
    ("R10 3c adv=1000 gain=800 (S5 only)",                   dict(lookback_candles=3, adverse_bps=1000, min_gain_bps=800, strategies=["S5"])),
    # Very strict — only huge profit + hard adverse
    ("R11 2c adv=1500 gain=1500 (all)",                      dict(lookback_candles=2, adverse_bps=1500, min_gain_bps=1500)),
]


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    print(f"{len(data)} coins")

    print("Computing sector features…")
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.strftime('%Y-%m-%d')}\n")

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    # D2 dead-timeout matches live
    early_exit_params = dict(
        exit_lead_candles=3,
        mfe_cap_bps=150,
        mae_floor_bps=-1000,
        slack_bps=300,
    )

    all_results = {}
    for name, params in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=early_exit_params,
                           reversal_exit=params,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_rev = sum(1 for t in r["trades"] if t["reason"] == "reversal_momentum")
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={r['n_trades']} wr={r['win_rate']:.0f}% | rev_trig={n_rev}")
        print()

    # ── Delta vs baseline ──
    print("=" * 118)
    print(f"{'Variant':<55} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  {'DD28':>6} {'DD12':>6} {'DD6':>6} {'DD3':>6}")
    print("-" * 118)
    base = all_results[VARIANTS[0][0]]
    for name, _ in VARIANTS:
        r = all_results[name]
        pnl_row = []
        for w in ["28 mois", "12 mois", "6 mois", "3 mois"]:
            if name == VARIANTS[0][0]:
                pnl_row.append(f"${r[w]['end_capital']:>7.0f}")
            else:
                delta = r[w]["end_capital"] - base[w]["end_capital"]
                pnl_row.append(f"{delta:+8.0f}")
        dd_row = [f"{r[w]['max_dd_pct']:+5.1f}" for w in ["28 mois", "12 mois", "6 mois", "3 mois"]]
        print(f"{name:<55} " + " ".join(pnl_row) + "  " + " ".join(dd_row))

    print()
    print("=" * 118)
    print("4-window validation:")
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]:
            continue
        r = all_results[name]
        all_positive = all(
            r[w]["end_capital"] > base[w]["end_capital"]
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"]
        )
        dd_stable = all(
            r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"]
        )
        flag = "✓" if (all_positive and dd_stable) else ("+" if all_positive else "-")
        print(f"  {flag} {name}: 4-window positive={all_positive}, DD stable={dd_stable}")


if __name__ == "__main__":
    main()
