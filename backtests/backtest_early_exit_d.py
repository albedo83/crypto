"""Walk-forward sweep for 'early exit on dead timeouts' (option D).

Calls the main run_window (backtest_rolling) with an early_exit_params dict.
Tests 6 variants + baseline on 4 official rolling windows.

Trigger: at T-K candles before timeout, if trade has never revealed upside
(MFE <= mfe_cap) AND is deeply underwater (MAE <= mae_floor) AND is still
pinned near its low (current <= MAE + slack), exit now.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi,
)


VARIANTS = [
    ("BASELINE",                                None),
    ("D1  lead=3 mfe<=100 mae<=-800  slack=200",  dict(exit_lead_candles=3, mfe_cap_bps=100,  mae_floor_bps=-800,  slack_bps=200)),
    ("D2  lead=3 mfe<=150 mae<=-1000 slack=300",  dict(exit_lead_candles=3, mfe_cap_bps=150,  mae_floor_bps=-1000, slack_bps=300)),
    ("D3  lead=3 mfe<=200 mae<=-600  slack=300",  dict(exit_lead_candles=3, mfe_cap_bps=200,  mae_floor_bps=-600,  slack_bps=300)),
    ("D4  lead=6 mfe<=150 mae<=-800  slack=300",  dict(exit_lead_candles=6, mfe_cap_bps=150,  mae_floor_bps=-800,  slack_bps=300)),
    ("D5  lead=2 mfe<=100 mae<=-800  slack=200",  dict(exit_lead_candles=2, mfe_cap_bps=100,  mae_floor_bps=-800,  slack_bps=200)),
    ("D6  lead=3 mfe<=0   mae<=-800  slack=200",  dict(exit_lead_candles=3, mfe_cap_bps=0,    mae_floor_bps=-800,  slack_bps=200)),
    ("D7  lead=3 mfe<=50  mae<=-500  slack=300",  dict(exit_lead_candles=3, mfe_cap_bps=50,   mae_floor_bps=-500,  slack_bps=300)),
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

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.strftime('%Y-%m-%d')}\n")

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    all_results = {}
    for name, params in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=params)
            all_results[name][label] = r
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={r['n_trades']} wr={r['win_rate']:.0f}%")
        print()

    # ── Delta vs baseline ──
    print("=" * 100)
    print(f"{'Variant':<42} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  {'DD28m':>7} {'DD12m':>7} {'DD6m':>7} {'DD3m':>7}")
    print("-" * 100)
    base = all_results["BASELINE"]
    for name, _ in VARIANTS:
        r = all_results[name]
        pnl_row = []
        for w in ["28 mois", "12 mois", "6 mois", "3 mois"]:
            if name == "BASELINE":
                pnl_row.append(f"${r[w]['end_capital']:>7.0f}")
            else:
                delta = r[w]["end_capital"] - base[w]["end_capital"]
                pnl_row.append(f"{delta:+8.0f}")
        dd_row = [f"{r[w]['max_dd_pct']:+6.1f}%" for w in ["28 mois", "12 mois", "6 mois", "3 mois"]]
        print(f"{name:<42} " + " ".join(pnl_row) + "  " + " ".join(dd_row))

    # Detailed trade attribution for the best variant
    print()
    print("=" * 100)
    print("Dead-timeout exits breakdown (best non-baseline variant that improves all 4 windows):")
    for name, _ in VARIANTS:
        if name == "BASELINE":
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
