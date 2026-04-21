"""Walk-forward sweep for S5 trailing stops.

Motivation: S5 trades frequently show large MFE peaks that are entirely given
back by timeout (observed pattern in live trading: BLUR reached MFE +2460 bps
then exited at +794 bps). S10 already has a trailing stop (trigger +600 bps,
offset 150 bps) that locks MFE. Question: is there a {trigger, offset} pair
for S5 that improves end-of-window P&L across all 4 walk-forward windows
without degrading compounding?

Uses run_window's new `trailing_extra` hook. Baseline = no S5 trailing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


# Trigger = MFE level at which trailing engages. Offset = how many bps below
# MFE triggers the exit. Lower trigger + lower offset = tighter lock (kills
# runners). Higher trigger + higher offset = loose lock (only catches big
# winners, lets normal trades run).
VARIANTS = [
    ("BASELINE (no S5 trailing)",                   None),
    ("T1  trigger=600  offset=150 (S10 params)",    dict(strategy="S5", trigger_bps=600,  offset_bps=150)),
    ("T2  trigger=800  offset=200",                  dict(strategy="S5", trigger_bps=800,  offset_bps=200)),
    ("T3  trigger=800  offset=300 (wider lock)",     dict(strategy="S5", trigger_bps=800,  offset_bps=300)),
    ("T4  trigger=1000 offset=250",                  dict(strategy="S5", trigger_bps=1000, offset_bps=250)),
    ("T5  trigger=1000 offset=400",                  dict(strategy="S5", trigger_bps=1000, offset_bps=400)),
    ("T6  trigger=1200 offset=300",                  dict(strategy="S5", trigger_bps=1200, offset_bps=300)),
    ("T7  trigger=1500 offset=400",                  dict(strategy="S5", trigger_bps=1500, offset_bps=400)),
    ("T8  trigger=1500 offset=600",                  dict(strategy="S5", trigger_bps=1500, offset_bps=600)),
    ("T9  trigger=2000 offset=500 (big-winner lock)", dict(strategy="S5", trigger_bps=2000, offset_bps=500)),
    ("T10 trigger=2000 offset=800",                  dict(strategy="S5", trigger_bps=2000, offset_bps=800)),
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

    # Match live bot: include D2 dead-timeout early-exit so this sweep tests
    # S5 trailing ON TOP OF the current rule set.
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
                           trailing_extra=params,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_s5 = sum(1 for t in r["trades"] if t["strat"] == "S5")
            n_trail = sum(1 for t in r["trades"] if t["reason"] == "s5_trailing")
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={r['n_trades']} wr={r['win_rate']:.0f}% | "
                  f"S5 total={n_s5} trailed={n_trail}")
        print()

    # ── Delta vs baseline ──
    print("=" * 110)
    print(f"{'Variant':<48} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  {'DD28m':>7} {'DD12m':>7} {'DD6m':>7} {'DD3m':>7}")
    print("-" * 110)
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
        dd_row = [f"{r[w]['max_dd_pct']:+6.1f}%" for w in ["28 mois", "12 mois", "6 mois", "3 mois"]]
        print(f"{name:<48} " + " ".join(pnl_row) + "  " + " ".join(dd_row))

    print()
    print("=" * 110)
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
            r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0  # allow 2pp worse
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"]
        )
        flag = "✓" if (all_positive and dd_stable) else ("+" if all_positive else "-")
        print(f"  {flag} {name}: 4-window positive={all_positive}, DD stable={dd_stable}")


if __name__ == "__main__":
    main()
