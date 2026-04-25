"""Walk-forward sweep for 'early MFE absence' exit.

Motivation: live data over 30 days shows big losers (4 trades worth -$62)
never crossed MFE +303 bps, while big winners (5 trades worth +$83) all
crossed +1800 bps within hours. The signal is in the *absence* of upside
early in the trade — not the price erosion (rejected) nor late-stage MAE
depth (D2 already handles that).

Rule: at hour H of the trade, if the rolling MFE is still below threshold X,
exit immediately. Stack ON TOP OF D2 dead-timeout (which catches deep MAE
near timeout).

Variants vary H (8h-24h) and X (50-500 bps). Smaller H + lower X = more
trades cut. Larger H + higher X = more selective.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


# H in candles of 4h: 2 = 8h, 3 = 12h, 4 = 16h, 6 = 24h, 8 = 32h
VARIANTS = [
    ("BASELINE (D2 only)",                                 None),
    # Sweep H × MFE threshold (all strategies)
    ("E1  H=8h   mfe<100 (all)",                            dict(check_after_candles=2,  mfe_min_bps=100)),
    ("E2  H=8h   mfe<200 (all)",                            dict(check_after_candles=2,  mfe_min_bps=200)),
    ("E3  H=12h  mfe<100 (all)",                            dict(check_after_candles=3,  mfe_min_bps=100)),
    ("E4  H=12h  mfe<200 (all)",                            dict(check_after_candles=3,  mfe_min_bps=200)),
    ("E5  H=12h  mfe<300 (all)",                            dict(check_after_candles=3,  mfe_min_bps=300)),
    ("E6  H=16h  mfe<200 (all)",                            dict(check_after_candles=4,  mfe_min_bps=200)),
    ("E7  H=16h  mfe<300 (all)",                            dict(check_after_candles=4,  mfe_min_bps=300)),
    ("E8  H=24h  mfe<300 (all)",                            dict(check_after_candles=6,  mfe_min_bps=300)),
    ("E9  H=24h  mfe<500 (all)",                            dict(check_after_candles=6,  mfe_min_bps=500)),
    ("E10 H=32h  mfe<500 (all)",                            dict(check_after_candles=8,  mfe_min_bps=500)),
    # S5-only scope (S5 is the dominant strategy)
    ("E11 H=12h  mfe<200 (S5 only)",                        dict(check_after_candles=3,  mfe_min_bps=200, strategies=["S5"])),
    ("E12 H=24h  mfe<300 (S5 only)",                        dict(check_after_candles=6,  mfe_min_bps=300, strategies=["S5"])),
    # Non-macro scope (skip S1)
    ("E13 H=12h  mfe<200 (non-macro)",                      dict(check_after_candles=3,  mfe_min_bps=200, strategies=["S5","S8","S9","S10"])),
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

    # D2 dead-timeout active to mirror live (current params)
    early_exit_params = dict(
        exit_lead_candles=3,
        mfe_cap_bps=150,
        mae_floor_bps=-800,  # tightened in v11.7.16
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
                           early_mfe_exit=params,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_emfe = sum(1 for t in r["trades"] if t["reason"] == "early_mfe_absence")
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={r['n_trades']} wr={r['win_rate']:.0f}% | emfe_trig={n_emfe}")
        print()

    # ── Delta vs baseline ──
    print("=" * 122)
    print(f"{'Variant':<48} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  {'DD28':>6} {'DD12':>6} {'DD6':>6} {'DD3':>6}  pass")
    print("-" * 122)
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
        all_positive = (name != VARIANTS[0][0]) and all(
            r[w]["end_capital"] > base[w]["end_capital"]
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        dd_stable = (name != VARIANTS[0][0]) and all(
            r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        flag = "✓" if (all_positive and dd_stable) else (
               "+" if all_positive else ("=" if name == VARIANTS[0][0] else "-"))
        print(f"{name:<48} " + " ".join(pnl_row) + "  " + " ".join(dd_row) + f"  {flag}")

    print()
    print("=" * 122)
    print("PASSING VARIANTS (4-window positive + DD stable):")
    found = False
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]: continue
        r = all_results[name]
        if (all(r[w]["end_capital"] > base[w]["end_capital"] for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
                and all(r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0 for w in ["28 mois", "12 mois", "6 mois", "3 mois"])):
            tot = sum(r[w]["end_capital"] - base[w]["end_capital"] for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
            print(f"  ✓ {name}  (cumulative Δ = ${tot:+.0f})")
            found = True
    if not found:
        print("  (none — early MFE absence isn't a free lunch either)")


if __name__ == "__main__":
    main()
