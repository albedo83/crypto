"""Walk-forward sweep for S14 — Dispersion Collapse Breakout.

S14 logic (from backtest_new_signals.py isolation tests):
1. Compute cross-sectional dispersion = std(ret_42h across all coins) per ts.
2. Compute rolling percentile over 180 days (1080 candles of 4h).
3. State: timestamps where disp_pctile <= dp_threshold AND vol_ratio < vrm
   are flagged as "compressed".
4. Trigger: at current ts, if any of the last 24h had a compressed state
   AND |ret_6h| > 500 bps NOW → enter in direction of ret_6h, hold H candles.

In isolation (`backtest_new_signals.py`) the best variant was:
  disp<p15 vr<0.9 hold=48h → n=426, WR 50%, +220 bps avg, +$1538 P&L.

This sweep validates the candidate in PORTFOLIO context (with S1/S5/S8/S9/S10
slot allocation, OI gate, blacklist, D2, sizing, real funding) across 4
walk-forward windows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, rolling_windows, load_dxy, load_oi, load_funding,
    HOLD_CANDLES, STRAT_Z,
)


# Best isolation candidates (from backtest_new_signals.py)
VARIANTS = [
    ("BASELINE (no S14)",                      None),
    ("S14  disp<p15 vr<0.9 hold=48h",          dict(disp_pctile_max=15, vol_ratio_max=0.9, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p15 vr<0.8 hold=48h",          dict(disp_pctile_max=15, vol_ratio_max=0.8, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p10 vr<0.9 hold=48h",          dict(disp_pctile_max=10, vol_ratio_max=0.9, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p20 vr<0.9 hold=48h",          dict(disp_pctile_max=20, vol_ratio_max=0.9, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p25 vr<0.7 hold=48h",          dict(disp_pctile_max=25, vol_ratio_max=0.7, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p25 vr<0.8 hold=48h",          dict(disp_pctile_max=25, vol_ratio_max=0.8, hold_candles=12, breakout_bps=500)),
    ("S14  disp<p15 vr<0.9 hold=24h",          dict(disp_pctile_max=15, vol_ratio_max=0.9, hold_candles=6,  breakout_bps=500)),
    ("S14  disp<p15 vr<0.9 hold=72h",          dict(disp_pctile_max=15, vol_ratio_max=0.9, hold_candles=18, breakout_bps=500)),
    ("S14  disp<p15 vr<0.9 hold=48h breakout>700", dict(disp_pctile_max=15, vol_ratio_max=0.9, hold_candles=12, breakout_bps=700)),
]


def precompute_dispersion_state(features, all_ts_sorted, p_max, vr_max):
    """Build dict: ts -> True if compressed (disp<=p_max AND vol_ratio<vr_max for any coin).

    Per the S14 isolation test, the marker is GLOBAL (cross-sectional dispersion
    of ret_42h across coins) but the per-coin trigger requires that coin's
    vol_ratio < vrm too. To match the isolation logic, we mark each (ts, coin)
    as compressed when disp_pctile <= p_max AND coin's vol_ratio < vr_max.
    """
    feat_by_ts = defaultdict(dict)
    for coin, fs in features.items():
        for f in fs:
            feat_by_ts[f["t"]][coin] = f

    # Cross-sectional dispersion of ret_42h per ts
    disp_history = {}
    for ts in all_ts_sorted:
        rets = [f.get("ret_42h", 0) for f in feat_by_ts[ts].values() if "ret_42h" in f]
        if len(rets) > 5:
            disp_history[ts] = float(np.std(rets))

    # Rolling percentile (180 days = 1080 candles of 4h)
    sorted_ts = sorted(disp_history.keys())
    disp_pctile = {}
    window = 180 * 6
    for i, ts in enumerate(sorted_ts):
        start = max(0, i - window)
        vals = [disp_history[sorted_ts[j]] for j in range(start, i + 1)]
        if len(vals) > 50:
            current = disp_history[ts]
            disp_pctile[ts] = float(np.searchsorted(np.sort(vals), current) / len(vals) * 100)

    # Per-(ts, coin) compressed marker
    compressed = defaultdict(dict)
    for ts in sorted_ts:
        pct = disp_pctile.get(ts, 50)
        if pct > p_max:
            continue
        for coin, f in feat_by_ts[ts].items():
            if f.get("vol_ratio", 1) < vr_max:
                compressed[ts][coin] = True

    return compressed, feat_by_ts


def make_s14_extra(compressed, feat_by_ts, hold_candles, breakout_bps):
    """Returns the extra_candidate_fn matching S14 logic for run_window."""
    # Z-score arbitrary; place between S5 (high) and S10 (high). Use 4.0 for now.
    S14_Z = 4.0

    def extra_fn(ts, coins, _feat_by_ts, data, coin_by_ts, positions, cooldown):
        # Check if any of last 24h (6 candles) had a compressed state per coin
        cands = []
        # Build lookback window in ms
        candle_ms = 4 * 3600 * 1000
        lookback_ts = [ts - i * candle_ms for i in range(0, 7)]  # ts now + 6 prior
        for coin in coins:
            if coin in positions: continue
            if coin in cooldown and ts < cooldown[coin]: continue
            recent_compressed = any(compressed.get(t, {}).get(coin) for t in lookback_ts)
            if not recent_compressed: continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue
            ret = f.get("ret_6h", 0)
            if abs(ret) <= breakout_bps: continue
            cands.append({
                "coin": coin, "dir": 1 if ret > 0 else -1, "strat": "S14",
                "z": S14_Z, "hold": hold_candles, "strength": abs(ret),
            })
        return cands
    return extra_fn


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

    # Build sorted ts for dispersion calc (all coins)
    all_ts = set()
    for fs in features.values():
        for f in fs: all_ts.add(f["t"])
    all_ts_sorted = sorted(all_ts)

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    # Mirror live: D2 dead-timeout (current params)
    early_exit_params = dict(
        exit_lead_candles=3,
        mfe_cap_bps=150,
        mae_floor_bps=-800,
        slack_bps=300,
    )

    all_results = {}
    cache = {}  # cache (p_max, vr_max) -> compressed dict
    for name, params in VARIANTS:
        print(f"=== {name} ===")
        all_results[name] = {}
        if params is None:
            extra_fn = None
        else:
            key = (params["disp_pctile_max"], params["vol_ratio_max"])
            if key not in cache:
                cache[key], _ = precompute_dispersion_state(features, all_ts_sorted, *key)
            compressed, feat_by_ts = cache[key], None
            # rebuild feat_by_ts only once (pure dict for the lookup)
            if "_feat_by_ts" not in cache:
                fbt = defaultdict(dict)
                for coin, fs in features.items():
                    for f in fs: fbt[f["t"]][coin] = f
                cache["_feat_by_ts"] = fbt
            extra_fn = make_s14_extra(compressed, cache["_feat_by_ts"],
                                       params["hold_candles"], params["breakout_bps"])

        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts, latest_ts, oi_data=oi_data,
                           early_exit_params=early_exit_params,
                           extra_candidate_fn=extra_fn,
                           funding_data=funding_data)
            all_results[name][label] = r
            n_s14 = sum(1 for t in r["trades"] if t["strat"] == "S14")
            print(f"  {label}: end=${r['end_capital']:.0f} "
                  f"({r['pnl_pct']:+.1f}%) DD={r['max_dd_pct']:.1f}% "
                  f"n={r['n_trades']} wr={r['win_rate']:.0f}% | S14={n_s14}")
        print()

    # ── Summary ──
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
        all_pos = (name != VARIANTS[0][0]) and all(
            r[w]["end_capital"] > base[w]["end_capital"]
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        dd_ok = (name != VARIANTS[0][0]) and all(
            r[w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0
            for w in ["28 mois", "12 mois", "6 mois", "3 mois"])
        flag = "✓" if (all_pos and dd_ok) else (
               "+" if all_pos else ("=" if name == VARIANTS[0][0] else "-"))
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
        print("  (none — S14 doesn't survive walk-forward portfolio constraints either)")


if __name__ == "__main__":
    main()
