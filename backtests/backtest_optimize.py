"""Optimization — Improve S1+S2+S4 with smart exits and sizing.

Tests:
1. Stop loss: none vs -15% vs -10% vs -25%
2. Trailing stop: activate at +X%, trail by Y%
3. Signal-reversal exit: exit early when signal disappears
4. Smart sizing: weight by signal z-score
5. Combined optimal config
6. Final Monte Carlo validation

Usage:
    python3 -m analysis.backtest_optimize
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy,
    TOKENS, COST_BPS, MAX_POSITIONS, MAX_SAME_DIR,
    TRAIN_END, TEST_START,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

# The 3 validated strategies
# NOTE: S1 hold=18 (3 days), NOT 42 — hold=42 was optimized on test data (review bug #1)
# Z-scores from initial scan (hold=18 for all) — not from hold-optimized version
STRATS = {
    "S1": {"rules": [("btc_30d", ">", 2000, 1)], "hold": 18, "z": 6.42},
    "S2": {"rules": [("alt_index_7d", "<", -1000, 1)], "hold": 18, "z": 4.00},
    "S4": {"rules": [("vol_ratio", "<", 1.0, -1), ("range_pct", "<", 200, -1)], "hold": 18, "z": 2.95},
}


def make_strat(name):
    s = STRATS[name]
    rules = [Rule(f, op, t, d) for f, op, t, d in s["rules"]]
    return Strategy(rules, hold=s["hold"])


# ═══════════════════════════════════════════════════════════════════
# Core backtester with all exit modes
# ═══════════════════════════════════════════════════════════════════

def backtest_combined(features, data, config):
    """Combined backtest with configurable exits and sizing.

    config keys:
        stop_loss_bps: None or negative bps (-1500 = -15%)
        trailing_activate: None or bps to activate trailing stop
        trailing_distance: bps distance from peak to trail
        signal_exit: bool, exit when signal condition no longer met
        sizing_mode: "fixed" or "z_weighted"
        base_size: base position size in $
        max_pos: max concurrent positions
        max_dir: max same direction
        cost_bps: total cost per trade
    """
    stop_loss = config.get("stop_loss_bps", None)
    trail_activate = config.get("trailing_activate", None)
    trail_distance = config.get("trailing_distance", None)
    signal_exit = config.get("signal_exit", False)
    sizing_mode = config.get("sizing_mode", "fixed")
    base_size = config.get("base_size", 250.0)
    max_pos = config.get("max_pos", MAX_POSITIONS)
    max_dir = config.get("max_dir", MAX_SAME_DIR)
    cost = config.get("cost_bps", COST_BPS)

    coins = [c for c in TOKENS if c in features and c in data]
    strategies = {name: make_strat(name) for name in STRATS}

    # Build unified timeline
    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i
    sorted_ts = sorted(all_ts)

    # Signal lookup
    signal_lookup = defaultdict(list)
    for sname, strat in strategies.items():
        for coin in coins:
            if coin not in features:
                continue
            for f in features[coin]:
                direction = strat.signal(f)
                if direction is not None:
                    signal_lookup[(f["t"], coin)].append({
                        "direction": direction,
                        "strat": sname,
                        "strength": abs(f.get("ret_42h", 0)),
                        "hold": strat.hold,
                        "idx": f["_idx"],
                        "z": STRATS[sname]["z"],
                    })

    # Build feature lookup for signal_exit
    feat_by_ts_coin = {}
    if signal_exit:
        for coin in coins:
            if coin not in features:
                continue
            for f in features[coin]:
                feat_by_ts_coin[(f["t"], coin)] = f

    # State
    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted_ts:
        # ── Check exits ──
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]:
                continue

            ci = coin_by_ts[coin][ts]
            candle = data[coin][ci]
            held = ci - pos["entry_idx"]
            if held <= 0:
                continue

            current = candle["c"]
            if current <= 0:
                continue

            # Track peak for trailing stop
            if pos["direction"] == 1:
                unrealized = (current / pos["entry_price"] - 1) * 1e4
                peak_check = (candle["h"] / pos["entry_price"] - 1) * 1e4
            else:
                unrealized = -(current / pos["entry_price"] - 1) * 1e4
                peak_check = -(candle["l"] / pos["entry_price"] - 1) * 1e4

            pos["peak_bps"] = max(pos.get("peak_bps", 0), peak_check)

            exit_reason = None
            exit_price = current

            # 1. Stop loss
            if stop_loss is not None:
                if pos["direction"] == 1:
                    worst = (candle["l"] / pos["entry_price"] - 1) * 1e4
                    if worst < stop_loss:
                        exit_reason = "stop"
                        exit_price = pos["entry_price"] * (1 + stop_loss / 1e4)
                else:
                    worst = -(candle["h"] / pos["entry_price"] - 1) * 1e4
                    if worst < stop_loss:
                        exit_reason = "stop"
                        exit_price = pos["entry_price"] * (1 - stop_loss / 1e4)

            # 2. Trailing stop
            if trail_activate and trail_distance and not exit_reason:
                if pos["peak_bps"] >= trail_activate:
                    if unrealized < pos["peak_bps"] - trail_distance:
                        exit_reason = "trail"

            # 3. Signal reversal exit (after min hold of 2 candles)
            if signal_exit and held >= 2 and not exit_reason:
                f = feat_by_ts_coin.get((ts, coin))
                if f:
                    strat = strategies[pos["strat"]]
                    still_active = strat.signal(f)
                    if still_active is None or still_active != pos["direction"]:
                        exit_reason = "signal_off"

            # 4. Timeout
            if held >= pos["hold"] and not exit_reason:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net = gross - cost
                pnl = pos["size"] * net / 1e4

                trades.append({
                    "coin": coin,
                    "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                    "strat": pos["strat"],
                    "entry_t": pos["entry_t"],
                    "exit_t": ts,
                    "hold": held,
                    "size": pos["size"],
                    "gross": round(gross, 1),
                    "net": round(net, 1),
                    "pnl": round(pnl, 2),
                    "reason": exit_reason,
                    "peak_bps": round(pos.get("peak_bps", 0), 1),
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Check entries ──
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for coin in coins:
            if coin in positions:
                continue
            if coin in cooldown and ts < cooldown[coin]:
                continue

            signals = signal_lookup.get((ts, coin), [])
            if not signals:
                continue

            best = max(signals, key=lambda s: s["z"])
            direction = best["direction"]

            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            idx = best["idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry_price = data[coin][idx + 1]["o"]
            if entry_price <= 0:
                continue

            # Sizing
            if sizing_mode == "z_weighted":
                weight = best["z"] / 4.0  # normalize: z=4 → 1.0x, z=6 → 1.5x, z=3 → 0.75x
                size = base_size * max(0.5, min(2.0, weight))
            else:
                size = base_size

            candidates.append({
                "coin": coin, "direction": direction,
                "entry_price": entry_price,
                "entry_idx": idx + 1,
                "entry_t": data[coin][idx + 1]["t"],
                "strength": best["strength"],
                "strat": best["strat"],
                "hold": best["hold"],
                "size": size,
                "z": best["z"],
            })

        # Rank by z-score (higher z first)
        candidates.sort(key=lambda x: x["z"], reverse=True)
        slots = max_pos - len(positions)

        for cand in candidates[:slots]:
            positions[cand["coin"]] = {
                "direction": cand["direction"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_t": cand["entry_t"],
                "strat": cand["strat"],
                "hold": cand["hold"],
                "size": cand["size"],
                "peak_bps": 0,
            }

    return trades


def analyze(trades, label, verbose=True):
    """Quick analysis."""
    if not trades:
        if verbose:
            print(f"  {label}: 0 trades")
        return {"pnl": 0, "n": 0}

    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    by_month = defaultdict(float)
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        by_month[dt.strftime("%Y-%m")] += t["pnl"]
    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m] > 0)

    # Train/test
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    # By exit reason
    by_reason = defaultdict(lambda: {"n": 0, "pnl": 0})
    for t in trades:
        by_reason[t["reason"]]["n"] += 1
        by_reason[t["reason"]]["pnl"] += t["pnl"]

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for m in months:
        cum += by_month[m]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    result = {
        "label": label, "n": n, "pnl": pnl, "avg": avg,
        "win": wins / n * 100, "months": len(months),
        "winning_months": winning,
        "train_pnl": train_pnl, "test_pnl": test_pnl,
        "max_dd": max_dd, "by_reason": dict(by_reason),
    }

    if verbose:
        valid = "✓" if train_pnl > 0 and test_pnl > 0 else "✗"
        print(f"  {label:<45} ${pnl:>+7.0f} ({n:>4}t, avg={avg:>+5.1f}bp, "
              f"win={wins/n*100:.0f}%) {winning}/{len(months)}mo "
              f"trn=${train_pnl:>+.0f} tst=${test_pnl:>+.0f} DD=${max_dd:.0f} {valid}")

        # Exit reason breakdown
        for reason in sorted(by_reason):
            r = by_reason[reason]
            ravg = r["pnl"] / r["n"] * 1e4 / 250 if r["n"] > 0 else 0
            print(f"    {reason:<12} {r['n']:>4}t ${r['pnl']:>+7.0f} "
                  f"(avg={ravg:>+.0f}bp)")

    return result


def main():
    print("=" * 70)
    print("  OPTIMIZATION — Smart exits + sizing")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    features = build_features(data)
    print(f"Built features\n")

    # ═══════════════════════════════════════════════════════════════
    # 1. STOP LOSS COMPARISON
    # ═══════════════════════════════════════════════════════════════
    print("=" * 70)
    print("  TEST 1: Stop Loss")
    print("=" * 70)

    for sl in [None, -500, -1000, -1500, -2000, -2500]:
        label = f"SL={sl}bp" if sl else "NO stop"
        trades = backtest_combined(features, data, {
            "stop_loss_bps": sl, "base_size": 250,
        })
        analyze(trades, label)

    # ═══════════════════════════════════════════════════════════════
    # 2. TRAILING STOP
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 2: Trailing Stop")
    print("=" * 70)

    for activate, distance in [
        (100, 50), (200, 100), (300, 150), (500, 200),
        (200, 50), (300, 100), (500, 150),
    ]:
        label = f"Trail act={activate}bp dist={distance}bp"
        trades = backtest_combined(features, data, {
            "stop_loss_bps": None,
            "trailing_activate": activate,
            "trailing_distance": distance,
            "base_size": 250,
        })
        analyze(trades, label)

    # ═══════════════════════════════════════════════════════════════
    # 3. SIGNAL EXIT
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 3: Signal Reversal Exit")
    print("=" * 70)

    for se in [False, True]:
        label = f"Signal exit={'ON' if se else 'OFF'}"
        trades = backtest_combined(features, data, {
            "stop_loss_bps": None,
            "signal_exit": se,
            "base_size": 250,
        })
        analyze(trades, label)

    # ═══════════════════════════════════════════════════════════════
    # 4. SIZING MODE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 4: Z-Weighted Sizing")
    print("=" * 70)

    for mode in ["fixed", "z_weighted"]:
        label = f"Sizing={mode}"
        trades = backtest_combined(features, data, {
            "stop_loss_bps": None,
            "sizing_mode": mode,
            "base_size": 250,
        })
        analyze(trades, label)

    # ═══════════════════════════════════════════════════════════════
    # 5. COMBINATIONS — Find optimal config
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TEST 5: Optimal Combinations")
    print("=" * 70)

    combos = [
        ("BASELINE (no SL, fixed)",
         {"stop_loss_bps": None, "base_size": 250}),
        ("No SL + z_weighted",
         {"stop_loss_bps": None, "sizing_mode": "z_weighted", "base_size": 250}),
        ("Trail 200/100 + z_weighted",
         {"trailing_activate": 200, "trailing_distance": 100,
          "sizing_mode": "z_weighted", "base_size": 250}),
        ("Trail 300/150 + z_weighted",
         {"trailing_activate": 300, "trailing_distance": 150,
          "sizing_mode": "z_weighted", "base_size": 250}),
        ("Signal exit + z_weighted",
         {"signal_exit": True, "sizing_mode": "z_weighted", "base_size": 250}),
        ("Trail 200/100 + signal exit",
         {"trailing_activate": 200, "trailing_distance": 100,
          "signal_exit": True, "base_size": 250}),
        ("Trail 300/150 + signal exit + z_weighted",
         {"trailing_activate": 300, "trailing_distance": 150,
          "signal_exit": True, "sizing_mode": "z_weighted", "base_size": 250}),
        ("SL -2500 + Trail 300/150 + z_weighted",
         {"stop_loss_bps": -2500, "trailing_activate": 300, "trailing_distance": 150,
          "sizing_mode": "z_weighted", "base_size": 250}),
    ]

    best_config = None
    best_pnl = -1e9
    results = []

    for label, config in combos:
        trades = backtest_combined(features, data, config)
        r = analyze(trades, label)
        results.append((label, config, trades, r))
        if r["pnl"] > best_pnl and r["train_pnl"] > 0 and r["test_pnl"] > 0:
            best_pnl = r["pnl"]
            best_config = (label, config, trades, r)

    # ═══════════════════════════════════════════════════════════════
    # 6. DETAILED ANALYSIS OF BEST CONFIG
    # ═══════════════════════════════════════════════════════════════
    if best_config:
        label, config, trades, r = best_config
        print(f"\n{'█'*70}")
        print(f"  BEST CONFIG: {label}")
        print(f"  P&L: ${r['pnl']:+.0f} | {r['n']} trades | DD: ${r['max_dd']:.0f}")
        print(f"{'█'*70}")

        # Monthly breakdown
        by_month = defaultdict(lambda: {"pnl": 0, "n": 0, "strats": defaultdict(float)})
        for t in trades:
            dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
            m = dt.strftime("%Y-%m")
            by_month[m]["pnl"] += t["pnl"]
            by_month[m]["n"] += 1
            by_month[m]["strats"][t["strat"]] += t["pnl"]

        months = sorted(by_month)
        print(f"\n  Monthly ({r['winning_months']}/{r['months']} winning):")
        cum = 0
        for m in months:
            d = by_month[m]
            cum += d["pnl"]
            parts = [f"{s}:{v:+.0f}" for s, v in sorted(d["strats"].items()) if abs(v) > 0.5]
            marker = "✓" if d["pnl"] > 0 else "✗"
            print(f"    {m}: ${d['pnl']:>+7.1f} ({d['n']:>3}t) cum=${cum:>+.0f} "
                  f"{' '.join(parts)} {marker}")

        # By strategy
        by_strat = defaultdict(list)
        for t in trades:
            by_strat[t["strat"]].append(t)

        print(f"\n  By strategy:")
        for sname in sorted(by_strat):
            st = by_strat[sname]
            sp = sum(t["pnl"] for t in st)
            sa = float(np.mean([t["net"] for t in st]))
            sw = sum(1 for t in st if t["net"] > 0) / len(st) * 100
            sizes = [t["size"] for t in st]
            print(f"    {sname}: ${sp:>+7.0f} ({len(st):>3}t, avg={sa:>+5.1f}bp, "
                  f"win={sw:.0f}%, avg_size=${np.mean(sizes):.0f})")

        # By period
        periods = [
            ("2023-H2", "2023-07", "2023-12"),
            ("2024-H1", "2024-01", "2024-06"),
            ("2024-H2", "2024-07", "2024-12"),
            ("2025-H1", "2025-01", "2025-06"),
            ("2025-H2", "2025-07", "2025-12"),
            ("2026-Q1", "2026-01", "2026-03"),
        ]
        print(f"\n  By period:")
        for name, start, end in periods:
            pt = [t for t in trades if start <= datetime.fromtimestamp(
                t["entry_t"]/1000, tz=timezone.utc).strftime("%Y-%m") <= end]
            if not pt:
                print(f"    {name:<10} —")
                continue
            pp = sum(t["pnl"] for t in pt)
            nm = len(set(datetime.fromtimestamp(
                t["entry_t"]/1000, tz=timezone.utc).strftime("%Y-%m") for t in pt))
            marker = "✓" if pp > 0 else "✗"
            print(f"    {name:<10} ${pp:>+7.0f} ({len(pt):>3}t, ${pp/max(1,nm):>+.0f}/mo) {marker}")

        # Monte Carlo
        print(f"\n  Monte Carlo (1000 sims)...")
        actual_pnl = sum(t["pnl"] for t in trades)
        by_coin = defaultdict(lambda: {"long": 0, "short": 0, "sizes": []})
        for t in trades:
            by_coin[t["coin"]]["long" if t["direction"] == "LONG" else "short"] += 1
            by_coin[t["coin"]]["sizes"].append(t["size"])

        avg_hold = int(np.mean([t["hold"] for t in trades]))
        sim_pnls = []
        for _ in range(1000):
            sim_total = 0
            for coin, counts in by_coin.items():
                if coin not in data:
                    continue
                candles = data[coin]
                nc = len(candles)
                available = list(range(180, nc - avg_hold - 1))
                n_needed = counts["long"] + counts["short"]
                if len(available) < n_needed:
                    continue
                avg_size = float(np.mean(counts["sizes"]))
                sampled = random.sample(available, n_needed)
                for j, idx in enumerate(sampled):
                    direction = 1 if j < counts["long"] else -1
                    entry = candles[min(idx+1, nc-1)]["o"]
                    exit_p = candles[min(idx+1+avg_hold, nc-1)]["c"]
                    if entry <= 0:
                        continue
                    gross = direction * (exit_p / entry - 1) * 1e4
                    net = gross - COST_BPS
                    sim_total += avg_size * net / 1e4
            sim_pnls.append(sim_total)

        sim_mean = float(np.mean(sim_pnls))
        sim_std = float(np.std(sim_pnls))
        z = (actual_pnl - sim_mean) / sim_std if sim_std > 0 else 0

        print(f"    Actual:      ${actual_pnl:>+.0f}")
        print(f"    Random mean: ${sim_mean:>+.0f} (std: ${sim_std:.0f})")
        print(f"    Z-score:     {z:+.2f}")
        if z > 2.5:
            print(f"    → ✓✓ STRONGLY SIGNIFICANT")
        elif z > 2.0:
            print(f"    → ✓ SIGNIFICANT")
        else:
            print(f"    → ⚠ MARGINAL")

    # ═══════════════════════════════════════════════════════════════
    # FINAL SUMMARY TABLE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'█'*70}")
    print(f"  OPTIMIZATION SUMMARY")
    print(f"{'█'*70}")
    print(f"\n  {'Config':<45} {'P&L':>8} {'N':>5} {'W%':>5} {'DD':>7} {'Train':>8} {'Test':>8}")
    print(f"  {'-'*90}")

    for label, config, trades, r in results:
        valid = "✓" if r.get("train_pnl", 0) > 0 and r.get("test_pnl", 0) > 0 else ""
        print(f"  {label:<45} ${r['pnl']:>+7.0f} {r['n']:>4} "
              f"{r['win']:>4.0f}% ${r['max_dd']:>+6.0f} "
              f"${r.get('train_pnl',0):>+7.0f} ${r.get('test_pnl',0):>+7.0f} {valid}")


if __name__ == "__main__":
    main()
