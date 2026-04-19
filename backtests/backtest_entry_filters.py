"""Walk-forward sweep for entry filters 2 & 3 (from rollback audit).

Findings from backtest_mfe_rollback_audit on S5 trades with MFE ≥ +300 bps:
- Rollbacks concentrate when BTC30 > +200 bps (+214 mean vs -48 for kept)
- Rollbacks enter with higher OI delta (+638 mean vs +295 for kept)

This sweep tests:
- P2a/b/c: skip S5 LONG when BTC30 > threshold (200, 500, 1000 bps)
- P3a/b/c: skip S5 when entry OI delta > threshold (400, 600, 800 bps)
- Combos: best P2 + best P3

Pass criteria: net-positive on ALL 4 rolling windows (28m/12m/6m/3m) with
DD not degrading more than 2pp on any window.
"""
from __future__ import annotations

from datetime import datetime, timezone
import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, oi_delta_24h_pct,
)


def make_btc30_filter(btc_data, coin_by_ts_btc, threshold_bps):
    """skip_fn: skip S5 LONG when BTC30 > threshold_bps."""
    btc_closes = np.array([c["c"] for c in btc_data])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_data)}
    LOOKBACK = 180  # 30d in 4h candles

    def btc30(ts):
        if ts not in btc_by_ts: return 0.0
        i = btc_by_ts[ts]
        if i < LOOKBACK or btc_closes[i - LOOKBACK] <= 0: return 0.0
        return (btc_closes[i] / btc_closes[i - LOOKBACK] - 1) * 1e4

    def skip(coin, ts, strat, direction):
        if strat == "S5" and direction == 1 and btc30(ts) > threshold_bps:
            return True
        return False
    return skip


def make_oi_filter(oi_data, threshold_bps):
    """skip_fn: skip S5 (any dir) when entry OI delta > threshold_bps."""
    def skip(coin, ts, strat, direction):
        if strat != "S5": return False
        oi_d = oi_delta_24h_pct(oi_data, coin, ts)
        if oi_d is None: return False
        # oi_delta_24h_pct returns percentage points; multiply by 100 for bps
        if oi_d * 100 > threshold_bps:
            return True
        return False
    return skip


def make_combo_filter(btc_data, oi_data, btc_thresh, oi_thresh):
    btc_closes = np.array([c["c"] for c in btc_data])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_data)}
    LOOKBACK = 180

    def btc30(ts):
        if ts not in btc_by_ts: return 0.0
        i = btc_by_ts[ts]
        if i < LOOKBACK or btc_closes[i - LOOKBACK] <= 0: return 0.0
        return (btc_closes[i] / btc_closes[i - LOOKBACK] - 1) * 1e4

    def skip(coin, ts, strat, direction):
        if strat != "S5": return False
        if direction == 1 and btc30(ts) > btc_thresh:
            return True
        oi_d = oi_delta_24h_pct(oi_data, coin, ts)
        if oi_d is not None and oi_d * 100 > oi_thresh:
            return True
        return False
    return skip


def run_variants(features, data, sector_features, dxy_data, oi_data,
                 windows, latest_ts):
    btc_data = data.get("BTC", [])
    VARIANTS = [
        ("BASELINE", None),
        # P2: BTC30 filter for S5 LONG
        ("P2a: skip S5 LONG when BTC30>+200 bps", make_btc30_filter(btc_data, None, 200)),
        ("P2b: skip S5 LONG when BTC30>+500 bps", make_btc30_filter(btc_data, None, 500)),
        ("P2c: skip S5 LONG when BTC30>+1000 bps", make_btc30_filter(btc_data, None, 1000)),
        ("P2d: skip S5 LONG when BTC30>+1500 bps", make_btc30_filter(btc_data, None, 1500)),
        # P3: OI delta filter for S5
        ("P3a: skip S5 when OI delta>+400 bps", make_oi_filter(oi_data, 400)),
        ("P3b: skip S5 when OI delta>+600 bps", make_oi_filter(oi_data, 600)),
        ("P3c: skip S5 when OI delta>+800 bps", make_oi_filter(oi_data, 800)),
        ("P3d: skip S5 when OI delta>+1000 bps", make_oi_filter(oi_data, 1000)),
        # Combos
        ("P2b+P3b combo (500/600)", make_combo_filter(btc_data, oi_data, 500, 600)),
        ("P2c+P3b combo (1000/600)", make_combo_filter(btc_data, oi_data, 1000, 600)),
        ("P2c+P3c combo (1000/800)", make_combo_filter(btc_data, oi_data, 1000, 800)),
    ]

    results = {}
    for name, skip_fn in VARIANTS:
        results[name] = {}
        print(f"  {name}...")
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, skip_fn=skip_fn, oi_data=oi_data)
            results[name][label] = r
    return results, [v[0] for v in VARIANTS]


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.strftime('%Y-%m-%d')}\n")

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    labels = ["28 mois", "12 mois", "6 mois", "3 mois"]
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    print("=== Running variants (this takes a few minutes) ===")
    results, variant_names = run_variants(
        features, data, sector_features, dxy_data, oi_data,
        windows, latest_ts)

    base = results["BASELINE"]
    print(f"\nBaseline: 28m=${base['28 mois']['end_capital']:.0f} "
          f"12m=${base['12 mois']['end_capital']:.0f} "
          f"6m=${base['6 mois']['end_capital']:.0f} "
          f"3m=${base['3 mois']['end_capital']:.0f}\n")

    print("=" * 120)
    print(f"{'Variant':<44} {'28m Δ':>10} {'12m Δ':>10} {'6m Δ':>10} {'3m Δ':>10}   DD: 28m / 12m / 6m / 3m   Pass")
    print("-" * 120)
    for name in variant_names:
        r = results[name]
        row = f"{name:<44}"
        all_positive = True
        dd_ok = True
        for w in labels:
            if name == "BASELINE":
                row += f"  ${r[w]['end_capital']:>7.0f}"
            else:
                delta = r[w]["end_capital"] - base[w]["end_capital"]
                row += f"  {delta:+8.0f}"
                if delta <= 0:
                    all_positive = False
                if r[w]["max_dd_pct"] < base[w]["max_dd_pct"] - 2.0:
                    dd_ok = False
        dd_str = " / ".join(f"{r[w]['max_dd_pct']:+5.1f}%" for w in labels)
        if name == "BASELINE":
            verdict = "baseline"
        else:
            verdict = "✓ PASS" if (all_positive and dd_ok) else ("+" if all_positive else "-")
        print(f"{row}   {dd_str}   {verdict}")

    print("\n" + "=" * 120)
    passers = [n for n in variant_names if n != "BASELINE"
               and all(results[n][w]["end_capital"] > base[w]["end_capital"] for w in labels)
               and all(results[n][w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0 for w in labels)]
    if passers:
        print("PASSING variants (4/4 positive, DD stable):")
        for p in passers:
            total_delta = sum(results[p][w]["end_capital"] - base[w]["end_capital"]
                             for w in labels)
            print(f"  {p}  (cumulative Δ = ${total_delta:+.0f})")
    else:
        print("No variant passes strict 4/4 + DD. Check the '+' marked ones for partial wins.")


if __name__ == "__main__":
    main()
