"""Re-test S9 sizing reduction with current config (D2 + OI gate + blacklist).

Prior test (`backtest_adaptive_sizing.py` era, before D2/OI gate) rejected S9
sizing reduction 0/4. This re-run validates with the current bot rules.

Adds a special window: 2026-01-01 → 2026-04-26 (year-to-date through yesterday)
to match the user's exact request and overlap maximally with live data.

S9 default sizing in live = z=8.71 → ~$248 avg per trade.
Test multipliers: 1.00 (baseline), 0.75, 0.50, 0.25, 0.10, 0.00 (kill).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


VARIANTS = [
    ("BASELINE  S9 ×1.00",          {}),
    ("S9 ×0.75",                    {"S9": 0.75}),
    ("S9 ×0.50",                    {"S9": 0.50}),
    ("S9 ×0.25",                    {"S9": 0.25}),
    ("S9 ×0.10",                    {"S9": 0.10}),
    ("S9 ×0.00 (kill)",             {"S9": 0.00}),
]


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.strftime('%Y-%m-%d')}\n")

    # Standard rolling windows
    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]
    # Add the user's specific window: YTD (2026-01-01 → end_dt)
    ytd_start_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    windows.append(("YTD 2026", ytd_start_dt))

    early_exit_params = dict(
        exit_lead_candles=3, mfe_cap_bps=150,
        mae_floor_bps=-800, slack_bps=300,
    )

    all_results = {}
    for name, multipliers in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=early_exit_params,
                           size_multiplier=multipliers if multipliers else None,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_s9 = sum(1 for t in r["trades"] if t["strat"] == "S9")
            s9_pnl = sum(t["pnl"] for t in r["trades"] if t["strat"] == "S9")
            print(f"  {label:<12} end=${r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%) "
                  f"DD={r['max_dd_pct']:.1f}% n={r['n_trades']} wr={r['win_rate']:.0f}% | "
                  f"S9 n={n_s9} sum=${s9_pnl:+.0f}")
        print()

    # ── Summary table ──
    print("=" * 130)
    win_labels = ["28 mois", "12 mois", "6 mois", "3 mois", "YTD 2026"]
    head = f"{'Variant':<22}"
    for w in win_labels:
        head += f"{w:>14}"
    print(head)
    print("-" * 130)
    base = all_results[VARIANTS[0][0]]
    for name, _ in VARIANTS:
        r = all_results[name]
        row = f"{name:<22}"
        for w in win_labels:
            if name == VARIANTS[0][0]:
                row += f"{'$' + str(int(r[w]['end_capital'])):>14}"
            else:
                d = r[w]['end_capital'] - base[w]['end_capital']
                row += f"{d:+13.0f}"
        print(row)

    print()
    print("=" * 130)
    print("DD per variant per window:")
    print(f"{'Variant':<22}" + "".join(f"{w:>14}" for w in win_labels))
    print("-" * 130)
    for name, _ in VARIANTS:
        r = all_results[name]
        row = f"{name:<22}" + "".join(f"{r[w]['max_dd_pct']:>13.1f}%" for w in win_labels)
        print(row)

    print()
    print("=" * 130)
    print("PASSING VARIANTS (5-window positive, including YTD):")
    found = False
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]: continue
        r = all_results[name]
        all_pos = all(r[w]['end_capital'] > base[w]['end_capital'] for w in win_labels)
        dd_ok = all(r[w]['max_dd_pct'] >= base[w]['max_dd_pct'] - 2.0 for w in win_labels)
        if all_pos and dd_ok:
            tot = sum(r[w]['end_capital'] - base[w]['end_capital'] for w in win_labels)
            print(f"  ✓ {name}  (cumulative Δ across 5 windows = ${tot:+.0f})")
            found = True
    if not found:
        print("  (no variant strictly dominates baseline on all 5 windows)")

    print()
    print("=" * 130)
    print("PARTIAL: variants beating baseline on YTD only (recent regime):")
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]: continue
        r = all_results[name]
        ytd_d = r['YTD 2026']['end_capital'] - base['YTD 2026']['end_capital']
        if ytd_d > 0:
            print(f"  + {name}: YTD Δ=${ytd_d:+.0f}  DD YTD {r['YTD 2026']['max_dd_pct']:+.1f}% (vs {base['YTD 2026']['max_dd_pct']:+.1f}%)")


if __name__ == "__main__":
    main()
