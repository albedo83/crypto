"""Walk-forward sweep: filter extreme-condition entries.

Live observation: big losers had extreme features at entry —
- BLUR S9 SHORT: r24h=+2325 (fade too violent), -$20
- DYDX S9 SHORT: r24h=+2004, -$15
- LDO S5 LONG: vol_z=7.2 (extreme volatility), -$21
- PENDLE S5 LONG: ret_24h=+1017 (too stretched divergence), -$16

Test:
  F1: skip S9 if |ret_24h| > X bps (X = 1500/2000/2500)
  F2: skip S5 if vol_z > X (X = 4/5/6)

Uses skip_fn hook on run_window. The features (ret_24h, vol_z) are already
computed per token/ts in the backtest features dict.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)


def make_skip_fn(features, max_s9_ret24h=None, max_s5_volz=None):
    """Build skip_fn that filters S9 on r24h and S5 on vol_z."""
    feat_by_ts = defaultdict(dict)
    for coin, fs in features.items():
        for f in fs:
            feat_by_ts[f["t"]][coin] = f

    def skip_fn(coin, ts, strat, direction):
        f = feat_by_ts.get(ts, {}).get(coin)
        if not f: return False
        if strat == "S9" and max_s9_ret24h is not None:
            r = f.get("ret_6h", 0)  # ret_6h = 6 candles × 4h = 24h in feature naming
            if abs(r) > max_s9_ret24h:
                return True
        if strat == "S5" and max_s5_volz is not None:
            vz = f.get("vol_z", 0)
            if vz > max_s5_volz:
                return True
        return False
    return skip_fn


VARIANTS = [
    ("BASELINE",                                None),
    ("F1a S9 r24h cap=1500",                    dict(max_s9_ret24h=1500)),
    ("F1b S9 r24h cap=2000",                    dict(max_s9_ret24h=2000)),
    ("F1c S9 r24h cap=2500",                    dict(max_s9_ret24h=2500)),
    ("F2a S5 vol_z cap=4",                      dict(max_s5_volz=4)),
    ("F2b S5 vol_z cap=5",                      dict(max_s5_volz=5)),
    ("F2c S5 vol_z cap=6",                      dict(max_s5_volz=6)),
    ("F3 combo S9<2000 + S5 vz<5",              dict(max_s9_ret24h=2000, max_s5_volz=5)),
    ("F4 combo S9<2500 + S5 vz<6",              dict(max_s9_ret24h=2500, max_s5_volz=6)),
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
    end_dt = datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.date()}\n")

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]
    # Add YTD for direct relevance
    windows.append(("YTD 2026", datetime(2026, 1, 1, tzinfo=timezone.utc)))

    early_exit_params = dict(
        exit_lead_candles=3, mfe_cap_bps=150,
        mae_floor_bps=-800, slack_bps=300,
    )

    all_results = {}
    for name, params in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        skip = make_skip_fn(features, **(params or {}))
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=early_exit_params,
                           skip_fn=skip if params else None,
                           funding_data=funding_data)
            all_results[name][label] = r
            print(f"  {label:<12} end=${r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%) "
                  f"DD={r['max_dd_pct']:.1f}% n={r['n_trades']} wr={r['win_rate']:.0f}%")
        print()

    # Summary
    win_labels = ["28 mois", "12 mois", "6 mois", "3 mois", "YTD 2026"]
    print("=" * 130)
    print(f"{'Variant':<35} " + "".join(f"{w:>14}" for w in win_labels) + "  pass")
    print("-" * 130)
    base = all_results[VARIANTS[0][0]]
    for name, _ in VARIANTS:
        r = all_results[name]
        row = f"{name:<35} "
        for w in win_labels:
            if name == VARIANTS[0][0]:
                row += f"{'$' + str(int(r[w]['end_capital'])):>14}"
            else:
                d = r[w]['end_capital'] - base[w]['end_capital']
                row += f"{d:+13.0f}"
        all_pos = (name != VARIANTS[0][0]) and all(
            r[w]['end_capital'] > base[w]['end_capital'] for w in win_labels)
        dd_ok = (name != VARIANTS[0][0]) and all(
            r[w]['max_dd_pct'] >= base[w]['max_dd_pct'] - 2.0 for w in win_labels)
        flag = "✓" if (all_pos and dd_ok) else (
               "+" if all_pos else ("=" if name == VARIANTS[0][0] else "-"))
        print(row + f"  {flag}")

    print()
    print("PASSING (5-window strict):")
    found = False
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]: continue
        r = all_results[name]
        if (all(r[w]['end_capital'] > base[w]['end_capital'] for w in win_labels)
                and all(r[w]['max_dd_pct'] >= base[w]['max_dd_pct'] - 2.0 for w in win_labels)):
            tot = sum(r[w]['end_capital'] - base[w]['end_capital'] for w in win_labels)
            print(f"  ✓ {name}  cumulative Δ=${tot:+.0f}")
            found = True
    if not found:
        print("  (none)")
    # YTD-only
    print()
    print("Beats baseline on YTD 2026 specifically:")
    for name, _ in VARIANTS:
        if name == VARIANTS[0][0]: continue
        r = all_results[name]
        ytd_d = r['YTD 2026']['end_capital'] - base['YTD 2026']['end_capital']
        if ytd_d > 0:
            print(f"  + {name}: YTD Δ=${ytd_d:+.0f}  DD YTD {r['YTD 2026']['max_dd_pct']:+.1f}%")


if __name__ == "__main__":
    main()
