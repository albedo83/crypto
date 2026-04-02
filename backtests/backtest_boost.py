"""Boost — Test leverage, sizing, hold times, more tokens.

Quick parameter sweep on the v9.3.0 portfolio to maximize returns.

Usage:
    python3 -m analysis.backtest_boost
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from backtests.backtest_sector import compute_sector_features, TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67}


def load_dxy():
    path = os.path.join(DATA_DIR, "macro_DXY.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        daily = json.load(f)
    closes = [d["c"] for d in daily]
    result = {}
    for i in range(5, len(daily)):
        result[daily[i]["t"]] = (closes[i] / closes[i-5] - 1) * 1e4 if closes[i-5] > 0 else 0
    return result


def backtest(features, data, sector_features, dxy_data, config):
    """Configurable portfolio backtest."""
    leverage = config.get("leverage", 1.0)
    size_pct = config.get("size_pct", 0.15)
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    hold_mult = config.get("hold_mult", 1.0)  # multiplier on default holds
    start_capital = config.get("capital", 1000)
    cost = config.get("cost", COST_BPS)
    stop_bps = config.get("stop_bps", -2500)

    # Adjust cost for leverage (funding scales with leverage)
    effective_cost = cost + (leverage - 1) * 2  # extra funding for leverage

    coins = [c for c in TOKENS if c in features and c in data]

    # Build lookups
    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    dxy_ts = sorted(dxy_data.keys()) if dxy_data else []
    def get_dxy(ts):
        if not dxy_ts:
            return 0
        lo, hi = 0, len(dxy_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if dxy_ts[mid] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return dxy_data.get(dxy_ts[lo], 0) if abs(dxy_ts[lo] - ts) < 5 * 86400 * 1000 else 0

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
    def btc_30d(ts):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i] / btc_closes[i-180] - 1) * 1e4 if i >= 180 and btc_closes[i-180] > 0 else 0

    def alt_index(ts):
        available = feat_by_ts.get(ts, {})
        rets = [f.get("ret_42h", 0) for f in available.values() if "ret_42h" in f]
        return float(np.mean(rets)) if rets else 0

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0

    for ts in sorted(all_ts):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]:
                continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0:
                continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0:
                continue

            exit_reason = None
            exit_price = current

            # Stop loss (adjusted for leverage)
            effective_stop = stop_bps / leverage
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + effective_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - effective_stop / 1e4)

            target_hold = int(pos["hold"] * hold_mult)
            if held >= target_hold:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * leverage
                net = gross - effective_cost
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100
                max_dd_pct = min(max_dd_pct, dd)

                trades.append({
                    "pnl": round(pnl, 2), "net": round(net, 1),
                    "dir": pos["dir"], "strat": pos["strat"],
                    "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts,
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # Entries
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)

        dxy = get_dxy(ts)
        btc30 = btc_30d(ts)
        alt_idx = alt_index(ts)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})
            if alt_idx < -1000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                    "z": STRAT_Z["S2"], "hold": 18, "strength": abs(alt_idx)})
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200 and dxy > 100:
                candidates.append({"coin": coin, "dir": -1, "strat": "S4",
                    "z": STRAT_Z["S4"], "hold": 18, "strength": (1-f["vol_ratio"])*1000})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            if len(positions) >= max_pos:
                break
            if cand["dir"] == 1 and n_long >= max_dir:
                continue
            if cand["dir"] == -1 and n_short >= max_dir:
                continue

            idx_f = f["_idx"] if (f := feat_by_ts.get(ts, {}).get(coin)) else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            size = capital * size_pct * w

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"], "size": size,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1

    # Results
    if not trades:
        return {"pnl": 0, "n": 0, "capital": start_capital}

    n = len(trades)
    pnl = capital - start_capital
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    by_month = defaultdict(float)
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        by_month[dt.strftime("%Y-%m")] += t["pnl"]
    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m] > 0)

    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    return {
        "pnl": round(pnl, 0), "n": n, "capital": round(capital, 0),
        "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "months": len(months), "winning": winning,
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "max_dd_pct": round(max_dd_pct, 1),
        "trades": trades,
    }


def main():
    print("=" * 70)
    print("  BOOST — Maximize returns")
    print("=" * 70)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 1, "size_pct": 0.15, "max_pos": 6, "max_dir": 4,
            "hold_mult": 1.0, "stop_bps": -2500}

    # ═══════════════════════════════════════════════════════════
    print("=" * 70)
    print("  TEST 1: LEVERAGE")
    print("=" * 70)
    print(f"  {'Config':<35} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*80}")

    for lev in [1, 1.5, 2, 2.5, 3]:
        cfg = {**base, "leverage": lev}
        r = backtest(features, data, sf, dxy, cfg)
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        print(f"  Leverage {lev}x{'':<25} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 2: SIZING")
    print("=" * 70)
    print(f"  {'Config':<35} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*80}")

    for pct in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]:
        cfg = {**base, "size_pct": pct}
        r = backtest(features, data, sf, dxy, cfg)
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        print(f"  Size {pct*100:.0f}%{'':<27} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 3: HOLD TIME")
    print("=" * 70)
    print(f"  {'Config':<35} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*80}")

    for mult in [0.33, 0.5, 0.67, 1.0, 1.5, 2.0]:
        cfg = {**base, "hold_mult": mult}
        r = backtest(features, data, sf, dxy, cfg)
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        h_s1 = int(72 * mult)
        print(f"  Hold ×{mult} (S1={h_s1}h){'':<18} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 4: MAX POSITIONS")
    print("=" * 70)
    print(f"  {'Config':<35} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*80}")

    for mp, md in [(4, 3), (6, 4), (8, 5), (10, 6), (12, 7), (15, 8)]:
        cfg = {**base, "max_pos": mp, "max_dir": md}
        r = backtest(features, data, sf, dxy, cfg)
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        print(f"  Max {mp} pos / {md} dir{'':<19} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 5: BEST COMBINATIONS")
    print("=" * 70)
    print(f"  {'Config':<45} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*90}")

    combos = [
        ("Baseline",                    {**base}),
        ("2x lev",                      {**base, "leverage": 2}),
        ("25% sizing",                  {**base, "size_pct": 0.25}),
        ("Hold ×0.5 (36h)",            {**base, "hold_mult": 0.5}),
        ("10 pos",                      {**base, "max_pos": 10, "max_dir": 6}),
        ("2x lev + 25% sizing",        {**base, "leverage": 2, "size_pct": 0.25}),
        ("2x lev + hold ×0.5",         {**base, "leverage": 2, "hold_mult": 0.5}),
        ("2x lev + 10 pos",            {**base, "leverage": 2, "max_pos": 10, "max_dir": 6}),
        ("25% + hold ×0.5 + 10 pos",   {**base, "size_pct": 0.25, "hold_mult": 0.5, "max_pos": 10, "max_dir": 6}),
        ("2x + 25% + hold ×0.5",       {**base, "leverage": 2, "size_pct": 0.25, "hold_mult": 0.5}),
        ("2x + 25% + 10 pos",          {**base, "leverage": 2, "size_pct": 0.25, "max_pos": 10, "max_dir": 6}),
        ("3x + 20% + hold ×0.67",      {**base, "leverage": 3, "size_pct": 0.20, "hold_mult": 0.67}),
        ("ALL MAX: 3x+30%+×0.5+10pos", {**base, "leverage": 3, "size_pct": 0.30, "hold_mult": 0.5, "max_pos": 10, "max_dir": 6}),
    ]

    results = []
    for name, cfg in combos:
        r = backtest(features, data, sf, dxy, cfg)
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        results.append((name, cfg, r))
        print(f"  {name:<45} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")

    # Best valid
    valid = [(n, c, r) for n, c, r in results if r["train"] > 0 and r["test"] > 0]
    if valid:
        best = max(valid, key=lambda x: x[2]["pnl"])
        print(f"\n  BEST VALID: {best[0]}")
        print(f"    ${best[2]['capital']} final | ${best[2]['pnl']:+} P&L | DD {best[2]['max_dd_pct']}%")

        # Monthly of best
        r = best[2]
        by_month = defaultdict(float)
        for t in r["trades"]:
            dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
            by_month[dt.strftime("%Y-%m")] += t["pnl"]

        print(f"\n    Monthly ({r['winning']}/{r['months']} winning):")
        cum = 1000
        for m in sorted(by_month):
            cum += by_month[m]
            marker = "✓" if by_month[m] > 0 else "✗"
            print(f"      {m}: ${by_month[m]:>+7.0f} bal=${cum:>+.0f} {marker}")


if __name__ == "__main__":
    main()
