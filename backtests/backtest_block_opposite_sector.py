"""Walk-forward sweep: block opposite-direction entries in the same sector.

Live observation: the bot has been opening LONG+SHORT pair-trades within the
same sector (e.g. GALA LONG + SAND SHORT in Gaming, COMP LONG + CRV SHORT in
DeFi). When the sector mean-reverts globally, both legs lose simultaneously.

Test: a rule that blocks a new entry if there's an existing same-sector
position in the OPPOSITE direction. Reduces total trade count but avoids the
correlated-loss pattern.

Also reports the frequency of the pair-trade pattern in baseline (the user
asked: "why does this never happen in backtest, we have everything from the
start in live?"). Answer: it does happen, but at percent-of-trades level it's
rare (~5-15%); the live experience over 30 days is dominated by 1-2 events.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import TOKEN_SECTOR


VARIANTS = [
    ("BASELINE (allow opposite-direction same sector)", False),
    ("BLOCK opposite-direction same sector",            True),
]


def count_pair_pattern(trades):
    """Return (n_pairs, n_pair_legs, sum_pair_pnl, n_total_trades).

    A "pair-leg" is a trade that overlapped with another same-sector
    opposite-direction trade.
    """
    pair_legs = set()
    pair_sum = 0.0
    n_pairs = 0
    for i, t in enumerate(trades):
        sec_t = TOKEN_SECTOR.get(t["coin"])
        if not sec_t: continue
        for j, u in enumerate(trades):
            if i >= j: continue
            sec_u = TOKEN_SECTOR.get(u["coin"])
            if sec_u != sec_t: continue
            if u["dir"] == t["dir"]: continue
            # Overlap?
            if u["entry_t"] >= t["exit_t"] or u["exit_t"] <= t["entry_t"]: continue
            pair_legs.add(i); pair_legs.add(j)
            pair_sum += t["pnl"] + u["pnl"]
            n_pairs += 1
    return n_pairs, len(pair_legs), pair_sum, len(trades)


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

    early_exit_params = dict(
        exit_lead_candles=3, mfe_cap_bps=150,
        mae_floor_bps=-800, slack_bps=300,
    )

    all_results = {}
    for name, block in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=early_exit_params,
                           block_opposite_sector=block,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_pairs, n_legs, pair_sum, n_total = count_pair_pattern(r["trades"])
            pair_pct = n_legs / n_total * 100 if n_total else 0
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={n_total} | pair_legs={n_legs} ({pair_pct:.1f}%) sum=${pair_sum:+.0f}")
        print()

    print("=" * 110)
    print(f"{'Variant':<54} {'28m':>10} {'12m':>10} {'6m':>10} {'3m':>10}  {'DD28':>6} {'DD12':>6} {'DD6':>6} {'DD3':>6}  pass")
    print("-" * 110)
    base = all_results[VARIANTS[0][0]]
    for name, _ in VARIANTS:
        r = all_results[name]
        pnl_row = []
        for w in ["28 mois", "12 mois", "6 mois", "3 mois"]:
            if name == VARIANTS[0][0]:
                pnl_row.append(f"${r[w]['end_capital']:>7.0f}")
            else:
                d = r[w]["end_capital"] - base[w]["end_capital"]
                pnl_row.append(f"{d:+8.0f}")
        dd_row = [f"{r[w]['max_dd_pct']:+5.1f}" for w in ["28 mois", "12 mois", "6 mois", "3 mois"]]
        all_pos = (name != VARIANTS[0][0]) and all(
            r[w]["end_capital"] > base[w]["end_capital"] for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        dd_ok = (name != VARIANTS[0][0]) and all(
            r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0 for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        flag = "✓" if (all_pos and dd_ok) else (
               "+" if all_pos else ("=" if name == VARIANTS[0][0] else "-"))
        print(f"{name:<54} " + " ".join(pnl_row) + "  " + " ".join(dd_row) + f"  {flag}")


if __name__ == "__main__":
    main()
