"""Squeeze Expansion Backtest — Compression + failed breakout + fade.

Concept: detect tight range (squeeze), wait for breakout that fails
(reintegrates range), then trade the real expansion.

Mode A: same direction (second breakout succeeds)
Mode B: opposite direction (false breakout traps, real move is opposite)

On 28 altcoins with 4h candles. Key advantage over single-token:
28 tokens × rare squeeze = enough total frequency.

Features already in bot: vol_ratio, range_pct, vol_z.

Usage:
    python3 -m analysis.backtest_squeeze
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS,
    TRAIN_END, TEST_START,
)

COST = COST_BPS
SIZE = POSITION_SIZE


def detect_squeeze_signals(data, features, config):
    """Detect squeeze → false breakout → reintegration patterns.

    config:
        squeeze_window: candles to measure range (2=8h, 3=12h, 6=24h)
        vol_ratio_max: max vol_ratio to qualify as squeeze (0.5, 0.7, 0.9)
        breakout_pct: min breakout beyond range as % of range (0.3 = 30%)
        reintegration_candles: max candles to return inside range (1, 2, 3)

    Returns list of signal dicts.
    """
    sq_window = config.get("squeeze_window", 3)
    vol_max = config.get("vol_ratio_max", 0.7)
    bo_pct = config.get("breakout_pct", 0.3)
    reint_candles = config.get("reintegration_candles", 2)

    signals = []

    for coin in TOKENS:
        if coin not in data or coin not in features:
            continue
        candles = data[coin]
        feat_by_idx = {}
        for f in features[coin]:
            feat_by_idx[f["_idx"]] = f

        for i in range(sq_window + reint_candles + 2, len(candles) - 2):
            f = feat_by_idx.get(i)
            if not f:
                continue

            # Check vol_ratio (squeeze condition)
            vol_ratio = f.get("vol_ratio", 1.0)
            if vol_ratio > vol_max:
                continue

            # Compute range over squeeze window
            window = candles[i - sq_window:i]
            highs = [c["h"] for c in window]
            lows = [c["l"] for c in window]
            range_high = max(highs)
            range_low = min(lows)
            range_size = range_high - range_low
            if range_size <= 0 or range_low <= 0:
                continue
            range_pct = range_size / range_low  # as fraction

            # Current candle = potential breakout
            bo_candle = candles[i]
            bo_above = bo_candle["h"] > range_high + range_size * bo_pct
            bo_below = bo_candle["l"] < range_low - range_size * bo_pct

            if not bo_above and not bo_below:
                continue

            # Determine breakout direction
            if bo_above and bo_below:
                # Both sides broken — skip (too volatile, not a clean squeeze)
                continue
            bo_direction = 1 if bo_above else -1  # 1 = broke above, -1 = broke below

            # Check reintegration: does price return inside range within N candles?
            reintegrated = False
            reint_idx = None
            for j in range(i + 1, min(i + 1 + reint_candles, len(candles))):
                c = candles[j]
                # Reintegrated if close is back inside the range
                if range_low <= c["c"] <= range_high:
                    reintegrated = True
                    reint_idx = j
                    break

            if not reintegrated or reint_idx is None:
                continue

            # We have: squeeze + breakout + reintegration
            # Entry at next candle open after reintegration
            entry_idx = reint_idx + 1
            if entry_idx >= len(candles):
                continue

            signals.append({
                "coin": coin,
                "t": candles[entry_idx]["t"],
                "entry_idx": entry_idx,
                "bo_direction": bo_direction,  # which way it TRIED to break
                "range_pct": round(range_pct * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "range_high": range_high,
                "range_low": range_low,
            })

    return signals


def backtest_squeeze(signals, data, config):
    """Backtest squeeze signals.

    config:
        mode: "A" (same dir as failed bo) or "B" (opposite = fade)
        hold: hold in candles
        period: "train", "test", "all"
    """
    mode = config.get("mode", "B")
    hold = config.get("hold", 6)
    period = config.get("period", "all")
    max_pos = config.get("max_positions", MAX_POSITIONS)

    positions = {}
    trades = []
    cooldown = {}

    # Sort signals by time
    sorted_sigs = sorted(signals, key=lambda s: s["t"])

    for sig in sorted_sigs:
        t = sig["t"]
        coin = sig["coin"]

        if period == "train" and t >= TRAIN_END:
            continue
        if period == "test" and t < TEST_START:
            continue

        # Check exits first
        for key in list(positions.keys()):
            pos = positions[key]
            pc = pos["coin"]
            candles = data[pc]
            exit_idx = pos["entry_idx"] + hold
            if exit_idx >= len(candles):
                exit_idx = len(candles) - 1

            if sig["entry_idx"] >= pos["entry_idx"] + hold or t > pos["t"] + hold * 4 * 3600 * 1000:
                exit_price = candles[min(exit_idx, len(candles) - 1)]["c"]
                if exit_price <= 0:
                    del positions[key]
                    continue
                gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net = gross - COST
                trades.append({
                    "coin": pc, "net": net, "pnl": SIZE * net / 1e4,
                    "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                    "range_pct": pos["range_pct"], "vol_ratio": pos["vol_ratio"],
                    "entry_t": pos["t"], "exit_t": t,
                })
                del positions[key]
                cooldown[pc] = t + 6 * 4 * 3600 * 1000  # 24h cooldown

        # Entry
        if len(positions) >= max_pos:
            continue
        if coin in positions:
            continue
        if coin in cooldown and t < cooldown[coin]:
            continue

        candles = data[coin]
        idx = sig["entry_idx"]
        if idx + hold >= len(candles):
            continue

        entry_price = candles[idx]["o"]
        if entry_price <= 0:
            continue

        # Direction based on mode
        if mode == "A":
            direction = sig["bo_direction"]  # same as failed breakout
        else:  # Mode B — fade
            direction = -sig["bo_direction"]  # opposite of failed breakout

        key = f"{coin}_{t}"
        positions[key] = {
            "coin": coin, "direction": direction,
            "entry_price": entry_price, "entry_idx": idx,
            "t": t, "range_pct": sig["range_pct"],
            "vol_ratio": sig["vol_ratio"],
        }

    # Close remaining
    for key in list(positions.keys()):
        pos = positions[key]
        candles = data[pos["coin"]]
        exit_idx = min(pos["entry_idx"] + hold, len(candles) - 1)
        exit_price = candles[exit_idx]["c"]
        if exit_price > 0:
            gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
            net = gross - COST
            trades.append({
                "coin": pos["coin"], "net": net, "pnl": SIZE * net / 1e4,
                "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                "range_pct": pos["range_pct"], "vol_ratio": pos["vol_ratio"],
                "entry_t": pos["t"], "exit_t": candles[exit_idx]["t"],
            })

    return trades


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1), "win": round(wins / n * 100, 0)}


def monte_carlo(signals, data, config, n_sims=500):
    real_trades = backtest_squeeze(signals, data, {**config, "period": "all"})
    if len(real_trades) < 10:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    hold = config.get("hold", 6)
    n_trades = len(real_trades)

    all_entries = []
    for sig in signals:
        coin = sig["coin"]
        idx = sig["entry_idx"]
        if idx + hold < len(data.get(coin, [])):
            all_entries.append((coin, idx, data[coin]))

    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, idx, candles in sampled:
            direction = random.choice([-1, 1])
            entry = candles[idx]["o"]
            exit_p = candles[idx + hold]["c"]
            if entry <= 0:
                continue
            gross = direction * (exit_p / entry - 1) * 1e4
            sim += SIZE * (gross - COST) / 1e4
        sim_pnls.append(sim)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    return round(z, 2)


def main():
    print("=" * 60)
    print("SQUEEZE EXPANSION BACKTEST — 28 altcoins")
    print("=" * 60)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"  {len(data)} tokens")

    print("Building features...")
    features = build_features(data)
    print(f"  {sum(len(v) for v in features.values())} feature rows")

    # Sweep parameters
    squeeze_windows = [2, 3, 6]        # 8h, 12h, 24h
    vol_ratio_maxes = [0.5, 0.7, 0.9]
    breakout_pcts = [0.2, 0.3, 0.5]
    reint_candles = [1, 2, 3]
    holds = [3, 6, 12, 18]             # 12h, 24h, 48h, 72h
    modes = ["A", "B"]

    # First: detect signals for each parameter combo
    print("\nDetecting squeeze signals...")
    signal_cache = {}
    for sw in squeeze_windows:
        for vm in vol_ratio_maxes:
            for bp in breakout_pcts:
                for rc in reint_candles:
                    key = (sw, vm, bp, rc)
                    sigs = detect_squeeze_signals(data, features, {
                        "squeeze_window": sw, "vol_ratio_max": vm,
                        "breakout_pct": bp, "reintegration_candles": rc,
                    })
                    signal_cache[key] = sigs

    total_configs = len(signal_cache) * len(holds) * len(modes) * 2
    print(f"  {len(signal_cache)} signal configs, {total_configs} total backtest configs")

    # Quick stats
    for key, sigs in sorted(signal_cache.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
        sw, vm, bp, rc = key
        n_tokens = len(set(s["coin"] for s in sigs))
        print(f"  sw={sw*4}h vm={vm} bo={bp} rc={rc}: {len(sigs)} signals across {n_tokens} tokens")

    results = []

    for (sw, vm, bp, rc), sigs in signal_cache.items():
        if len(sigs) < 20:
            continue
        for mode in modes:
            for hold in holds:
                for period in ["train", "test"]:
                    trades = backtest_squeeze(sigs, data, {
                        "mode": mode, "hold": hold, "period": period,
                    })
                    s = score(trades)
                    results.append({
                        "sw": sw * 4, "vm": vm, "bp": bp, "rc": rc,
                        "mode": mode, "hold": hold * 4, "period": period, **s,
                    })

    # Best train for each mode
    for mode in ["A", "B"]:
        print(f"\n{'=' * 60}")
        print(f"MODE {mode} {'(same direction)' if mode == 'A' else '(fade / opposite)'}")
        print(f"{'=' * 60}")

        train = [r for r in results if r["mode"] == mode and r["period"] == "train" and r["n"] >= 15]
        if not train:
            print("  Not enough trades")
            continue

        train.sort(key=lambda r: r["pnl"], reverse=True)
        print(f"\n  Top 10 (train):")
        print(f"  {'SW':>4} {'VM':>4} {'BO':>4} {'RC':>3} {'Hold':>5} {'N':>5} {'P&L':>8} {'Avg':>7} {'Win%':>5}")
        for r in train[:10]:
            te = [t for t in results if t["mode"] == mode and t["sw"] == r["sw"]
                  and t["vm"] == r["vm"] and t["bp"] == r["bp"] and t["rc"] == r["rc"]
                  and t["hold"] == r["hold"] and t["period"] == "test"]
            flag = ""
            if te:
                t = te[0]
                flag = f" → test: {'✓' if t['avg'] > 0 else '✗'} n={t['n']} avg={t['avg']:+.1f} ${t['pnl']:.0f}"
            print(f"  {r['sw']:>4} {r['vm']:>4} {r['bp']:>4} {r['rc']:>3} {r['hold']:>4}h {r['n']:>5} ${r['pnl']:>7.0f} {r['avg']:>+7.1f} {r['win']:>4.0f}%{flag}")

    # Passing train+test
    print(f"\n{'=' * 60}")
    print("ALL PASSING (train + test avg > 0, n >= 15 train, n >= 5 test)")
    print(f"{'=' * 60}")

    passing = []
    for r in results:
        if r["period"] != "train" or r["n"] < 15 or r["avg"] <= 0:
            continue
        te = [t for t in results if t["period"] == "test" and t["mode"] == r["mode"]
              and t["sw"] == r["sw"] and t["vm"] == r["vm"] and t["bp"] == r["bp"]
              and t["rc"] == r["rc"] and t["hold"] == r["hold"]]
        if te and te[0]["avg"] > 0 and te[0]["n"] >= 5:
            passing.append({"train": r, "test": te[0],
                            "total": r["pnl"] + te[0]["pnl"]})

    if not passing:
        print("  None. Squeeze expansion doesn't work on these altcoins.")
    else:
        passing.sort(key=lambda x: x["total"], reverse=True)
        print(f"\n  {'Mode':>4} {'SW':>4} {'VM':>4} {'BO':>4} {'RC':>3} {'Hold':>5} | {'TrN':>4} {'TrAvg':>7} {'TrPnL':>7} | {'TeN':>4} {'TeAvg':>7} {'TePnL':>7}")
        for p in passing[:15]:
            tr, te = p["train"], p["test"]
            print(f"  {tr['mode']:>4} {tr['sw']:>4} {tr['vm']:>4} {tr['bp']:>4} {tr['rc']:>3} {tr['hold']:>4}h | {tr['n']:>4} {tr['avg']:>+7.1f} ${tr['pnl']:>6.0f} | {te['n']:>4} {te['avg']:>+7.1f} ${te['pnl']:>6.0f}")

        # Monte Carlo on top 5
        print(f"\n  Monte Carlo (top 5):")
        for p in passing[:5]:
            tr = p["train"]
            key = (tr["sw"] // 4, tr["vm"], tr["bp"], tr["rc"])
            sigs = signal_cache.get(key, [])
            z = monte_carlo(sigs, data, {
                "mode": tr["mode"], "hold": tr["hold"] // 4,
            })
            if z is not None:
                flag = "✓" if z >= 2.0 else "✗"
                print(f"    Mode {tr['mode']} sw={tr['sw']}h vm={tr['vm']} bo={tr['bp']} rc={tr['rc']} hold={tr['hold']}h: z={z:.2f} {flag}")
            else:
                print(f"    Mode {tr['mode']}: not enough data for MC")

        # Per-token breakdown of best
        if passing:
            best = passing[0]
            tr = best["train"]
            key = (tr["sw"] // 4, tr["vm"], tr["bp"], tr["rc"])
            sigs = signal_cache.get(key, [])
            all_trades = backtest_squeeze(sigs, data, {
                "mode": tr["mode"], "hold": tr["hold"] // 4, "period": "all",
            })

            by_coin = defaultdict(list)
            for t in all_trades:
                by_coin[t["coin"]].append(t)

            print(f"\n  Best config per token:")
            print(f"  {'Token':>6} {'N':>4} {'P&L':>7} {'Avg':>7} {'Win%':>5}")
            for coin in sorted(by_coin.keys()):
                s = score(by_coin[coin])
                if s["n"] >= 3:
                    print(f"  {coin:>6} {s['n']:>4} ${s['pnl']:>6.0f} {s['avg']:>+7.1f} {s['win']:>4.0f}%")

            # Direction breakdown
            by_dir = defaultdict(list)
            for t in all_trades:
                by_dir[t["direction"]].append(t)
            print(f"\n  By direction:")
            for d in ["LONG", "SHORT"]:
                if by_dir[d]:
                    s = score(by_dir[d])
                    print(f"    {d}: n={s['n']} avg={s['avg']:+.1f} win={s['win']:.0f}% ${s['pnl']:.0f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
