"""Autopsy for extended blacklist — option C.

Step 1: run baseline on 4 walk-forward windows, aggregate trade P&L by
        (coin, direction, strat) to identify structural losers.
Step 2: for each candidate (WLD all, DOGE SHORT, BLUR all, and any other
        token×dir net-negative on all 4 windows), run the backtest WITHOUT
        that token×dir and report delta vs baseline.
Step 3: flag variants that are net-positive on ALL 4 windows AND don't
        meaningfully degrade DD.

A candidate passes if it's net-positive on 4/4 windows with stable DD.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi,
)

# Candidates identified from live data autopsy:
#   WLD: 3× top losers (all directions), net negative
#   DOGE SHORT: 2× perdant recurrent
#   BLUR: 2× (incl. -$19.77 catastrophe)
# Plus, we'll scan ALL tokens for 4/4-negative token×dir combinations.


def run_baseline(features, data, sector_features, dxy_data, oi_data,
                 windows, latest_ts):
    results = {}
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window(features, data, sector_features, dxy_data,
                       start_ts, latest_ts, oi_data=oi_data)
        results[label] = r
    return results


def run_with_skip(features, data, sector_features, dxy_data, oi_data,
                  windows, latest_ts, skip_set):
    """skip_set: set of (coin, dir) to exclude from entries. dir in {1,-1,0}
    where 0 means 'both directions'."""
    def skip_fn(coin, ts, strat, direction):
        if (coin, 0) in skip_set:
            return True
        if (coin, direction) in skip_set:
            return True
        return False

    results = {}
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window(features, data, sector_features, dxy_data,
                       start_ts, latest_ts, oi_data=oi_data, skip_fn=skip_fn)
        results[label] = r
    return results


def aggregate_by_coin_dir(results, windows_labels):
    """Returns dict {(coin, dir): {label: pnl_sum}}."""
    agg = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))
    for label in windows_labels:
        for t in results[label]["trades"]:
            key = (t["coin"], t["dir"])
            agg[key][label] += t["pnl"]
            counts[key][label] += 1
    return agg, counts


def summarize_candidate(name, base, variant, labels):
    line = f"{name:<34}"
    all_positive = True
    dd_ok = True
    for w in labels:
        delta = variant[w]["end_capital"] - base[w]["end_capital"]
        line += f"  {delta:+8.0f}"
        if delta <= 0:
            all_positive = False
        if variant[w]["max_dd_pct"] < base[w]["max_dd_pct"] - 2.0:
            dd_ok = False
    dd_line = "  ".join(f"{variant[w]['max_dd_pct']:+5.1f}%" for w in labels)
    flag = "✓" if (all_positive and dd_ok) else ("+" if all_positive else "-")
    return f"{flag} {line}  DD: {dd_line}", all_positive, dd_ok


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
    labels = ["28 mois", "12 mois", "6 mois", "3 mois"]
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    # ── STEP 1: baseline ──
    print("=== Step 1: Baseline run on 4 windows ===")
    base = run_baseline(features, data, sector_features, dxy_data, oi_data,
                        windows, latest_ts)
    for w in labels:
        r = base[w]
        print(f"  {w}: ${r['end_capital']:>7.0f} ({r['pnl_pct']:+.1f}%) "
              f"DD={r['max_dd_pct']:.1f}% n={r['n_trades']}")

    # ── STEP 2: aggregate by (coin, dir) over all windows ──
    print("\n=== Step 2: token×dir P&L per window ===")
    agg, counts = aggregate_by_coin_dir(base, labels)

    # Find all (coin, dir) net-negative on >= 3 windows, with enough trades
    losers_3w = []
    losers_4w = []
    for (coin, dr), by_win in agg.items():
        negatives = [w for w in labels if by_win.get(w, 0) < 0]
        total_n = sum(counts[(coin, dr)].get(w, 0) for w in labels)
        if total_n < 10:
            continue  # skip tokens with too few trades
        if len(negatives) == 4:
            losers_4w.append((coin, dr, by_win, counts[(coin, dr)], total_n))
        elif len(negatives) == 3:
            losers_3w.append((coin, dr, by_win, counts[(coin, dr)], total_n))

    print("\n--- Token×dir net-negative on 4/4 windows (min 10 trades) ---")
    print(f"{'Token/Dir':<18} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  trades")
    for coin, dr, by_win, cnt, total in sorted(losers_4w, key=lambda x: sum(x[2].values())):
        d_label = "LONG" if dr == 1 else "SHORT"
        line = f"{coin:<10} {d_label:<6}"
        for w in labels:
            line += f"  {by_win.get(w, 0):+8.0f}"
        line += f"  n={total}"
        print(line)

    print("\n--- Token×dir net-negative on 3/4 windows (informational) ---")
    for coin, dr, by_win, cnt, total in sorted(losers_3w, key=lambda x: sum(x[2].values())):
        d_label = "LONG" if dr == 1 else "SHORT"
        line = f"{coin:<10} {d_label:<6}"
        for w in labels:
            line += f"  {by_win.get(w, 0):+8.0f}"
        line += f"  n={total}"
        print(line)

    # ── STEP 3: test removing candidates ──
    print("\n=== Step 3: skip-test individual candidates + combos ===")

    # Pool of candidates: all 4/4-negative + manual watchlist
    auto_candidates = [(coin, dr) for coin, dr, *_ in losers_4w]
    manual = [("WLD", 0), ("DOGE", -1), ("BLUR", 0)]  # dr=0 = both dirs

    # De-dup (manual takes precedence: a manual "both" subsumes per-dir auto)
    all_tested = []
    seen = set()
    for key in manual:
        if key not in seen:
            all_tested.append(key)
            seen.add(key)
    for key in auto_candidates:
        coin, dr = key
        # Skip if a manual "both" entry for same coin is already tested
        if (coin, 0) in seen:
            continue
        if key not in seen:
            all_tested.append(key)
            seen.add(key)

    individual_results = []
    print(f"\n{'Candidate':<34} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  DDs")
    print("-" * 100)

    def label_for(key):
        coin, dr = key
        if dr == 0:
            return f"{coin} (both)"
        elif dr == 1:
            return f"{coin} LONG"
        else:
            return f"{coin} SHORT"

    for key in all_tested:
        result = run_with_skip(features, data, sector_features, dxy_data, oi_data,
                                windows, latest_ts, skip_set={key})
        summary, all_pos, dd_ok = summarize_candidate(
            label_for(key), base, result, labels)
        print(summary)
        individual_results.append((key, result, all_pos, dd_ok))

    # Combine passing candidates
    passing = [key for key, _, ap, dd in individual_results if ap and dd]
    if passing:
        print(f"\n=== Combined: skip all {len(passing)} passing candidates ===")
        combo = run_with_skip(features, data, sector_features, dxy_data, oi_data,
                               windows, latest_ts, skip_set=set(passing))
        summary, _, _ = summarize_candidate(
            "COMBO: " + ", ".join(label_for(k) for k in passing),
            base, combo, labels)
        print(summary)

    print("\n=== RECOMMENDATION ===")
    if passing:
        for key in passing:
            coin, dr = key
            if dr == 0:
                print(f"  ✓ Add {coin} to TRADE_BLACKLIST (all directions)")
            elif dr == 1:
                print(f"  ✓ Add {coin} LONG to a direction-specific blacklist")
            else:
                print(f"  ✓ Add {coin} SHORT to a direction-specific blacklist")
    else:
        print("  ✗ No candidate passed 4/4 positive + DD stable. Keep current blacklist.")


if __name__ == "__main__":
    main()
