"""Flow signals deep validation — portfolio backtest with position limits.

Takes the 2 promising signals from backtest_flow.py and validates them
with realistic constraints: max positions, cooldowns, no overlap.

Usage:
    python3 -m backtests.backtest_flow2
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE,
    TRAIN_END, TEST_START,
)

COST = COST_BPS
SIZE = POSITION_SIZE
MAX_POS = 6
COOLDOWN_CANDLES = 6  # 24h cooldown per symbol


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1), "win": round(wins / n * 100, 0)}


def monte_carlo(real_trades, all_entries, hold, n_sims=500):
    if len(real_trades) < 10 or not all_entries:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    n_trades = len(real_trades)
    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, idx, candles in sampled:
            if idx + hold >= len(candles):
                continue
            direction = random.choice([-1, 1])
            entry = candles[idx]["o"] if candles[idx]["o"] > 0 else candles[idx]["c"]
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


def _all_entries(data, hold):
    entries = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(len(candles) - hold):
            entries.append((coin, i, candles))
    return entries


def _detect_signals_voldiv(data, pw, vw, thr):
    """Volume divergence: price trending + volume declining → fade."""
    signals = []
    half = vw // 2
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(max(pw, vw) + 1, len(candles) - 1):
            p_now = candles[i]["c"]
            p_prev = candles[i - pw]["c"]
            if p_prev <= 0 or p_now <= 0:
                continue
            price_ret = (p_now / p_prev - 1) * 1e4

            vol_recent = np.mean([candles[i - j]["v"] for j in range(half)])
            vol_prior = np.mean([candles[i - half - j]["v"] for j in range(half)])
            if vol_prior <= 0:
                continue
            vol_change = vol_recent / vol_prior - 1

            direction = 0
            if price_ret > thr and vol_change < -0.3:
                direction = -1  # exhaustion top
            elif price_ret < -thr and vol_change < -0.3:
                direction = 1   # selling exhaustion

            if direction == 0:
                continue

            signals.append({
                "coin": coin, "t": candles[i]["t"],
                "entry_idx": i + 1, "direction": direction,
                "strength": abs(price_ret) * abs(vol_change),  # for ranking
                "info": f"voldiv pr={price_ret:.0f} vc={vol_change:.2f}",
            })
    return signals


def _detect_signals_volspike(data, vlb, vm, wr):
    """Volume spike + reversal candle → fade the flush."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(vlb + 1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl, v = c["o"], c["h"], c["l"], c["c"], c["v"]
            if o <= 0 or v <= 0:
                continue
            rng = h - l
            if rng <= 0:
                continue

            avg_vol = np.mean([candles[i - j]["v"] for j in range(1, vlb + 1)])
            if avg_vol <= 0 or v < avg_vol * vm:
                continue

            direction = 0
            if cl < o:
                lower_wick = min(o, cl) - l
                if lower_wick / rng >= wr:
                    direction = 1
            elif cl > o:
                upper_wick = h - max(o, cl)
                if upper_wick / rng >= wr:
                    direction = -1

            if direction == 0:
                continue

            signals.append({
                "coin": coin, "t": candles[i]["t"],
                "entry_idx": i + 1, "direction": direction,
                "strength": v / avg_vol,
                "info": f"volspike {v/avg_vol:.1f}x wick",
            })
    return signals


def portfolio_backtest(signals, data, hold, period="all", label=""):
    """Realistic portfolio backtest with position limits and cooldowns."""
    # Sort by time, then by strength (strongest first for slot allocation)
    sorted_sigs = sorted(signals, key=lambda s: (s["t"], -s["strength"]))

    positions = {}      # coin -> {entry_idx, entry_price, direction, exit_idx}
    cooldowns = {}      # coin -> next_available_idx
    trades = []

    # Build time-indexed signal groups
    by_time = defaultdict(list)
    for sig in sorted_sigs:
        by_time[sig["t"]].append(sig)

    all_times = sorted(by_time.keys())

    for t in all_times:
        if period == "train" and t >= TRAIN_END:
            continue
        if period == "test" and t < TEST_START:
            continue

        # First: check exits for existing positions
        for coin in list(positions.keys()):
            pos = positions[coin]
            candles = data[coin]
            # Check stop loss at each candle
            exit_idx = pos["exit_idx"]
            entry_price = pos["entry_price"]

            # Find current candle index (approximate via timestamp matching)
            # Actually we track exit_idx directly
            curr_t_idx = None
            for ci in range(pos["entry_idx"], min(exit_idx + 1, len(candles))):
                if candles[ci]["t"] >= t:
                    curr_t_idx = ci
                    break

            if curr_t_idx and curr_t_idx >= exit_idx:
                # Timeout exit
                exit_price = candles[exit_idx]["c"] if exit_idx < len(candles) else entry_price
                if exit_price > 0:
                    gross = pos["direction"] * (exit_price / entry_price - 1) * 1e4
                    net = gross - COST
                    trades.append({
                        "coin": coin, "net": net,
                        "pnl": SIZE * net / 1e4, "t": t,
                        "direction": pos["direction"],
                        "reason": "timeout",
                    })
                del positions[coin]
                cooldowns[coin] = exit_idx + COOLDOWN_CANDLES

        # Then: try new entries
        sigs_now = by_time[t]
        sigs_now.sort(key=lambda s: -s["strength"])

        for sig in sigs_now:
            coin = sig["coin"]
            if len(positions) >= MAX_POS:
                break
            if coin in positions:
                continue
            entry_idx = sig["entry_idx"]
            if coin in cooldowns and entry_idx < cooldowns[coin]:
                continue
            candles = data[coin]
            if entry_idx >= len(candles) or entry_idx + hold >= len(candles):
                continue

            entry_price = candles[entry_idx]["o"]
            if entry_price <= 0:
                continue

            # Check stop loss during hold period
            stopped = False
            exit_idx = entry_idx + hold
            exit_price = candles[exit_idx]["c"]

            for ci in range(entry_idx + 1, exit_idx + 1):
                if ci >= len(candles):
                    break
                # Use high/low for stop check
                worst = candles[ci]["l"] if sig["direction"] == 1 else candles[ci]["h"]
                ur = sig["direction"] * (worst / entry_price - 1) * 1e4 * 2  # 2x leverage
                if ur < -2500:
                    exit_price = worst
                    exit_idx = ci
                    stopped = True
                    break

            positions[coin] = {
                "entry_idx": entry_idx, "entry_price": entry_price,
                "direction": sig["direction"], "exit_idx": exit_idx,
            }

    # Close remaining positions
    for coin, pos in positions.items():
        candles = data[coin]
        exit_idx = min(pos["exit_idx"], len(candles) - 1)
        exit_price = candles[exit_idx]["c"]
        if exit_price > 0:
            gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
            net = gross - COST
            trades.append({
                "coin": coin, "net": net, "pnl": SIZE * net / 1e4,
                "t": candles[exit_idx]["t"], "direction": pos["direction"],
                "reason": "final",
            })

    return trades


def simple_backtest(signals, data, hold, period="all"):
    """Simple backtest: enter next candle, exit after hold, cooldown per symbol."""
    sorted_sigs = sorted(signals, key=lambda s: s["t"])
    cooldowns = {}  # coin -> next_available_t
    active = {}     # coin -> position info
    trades = []

    for sig in sorted_sigs:
        t = sig["t"]
        if period == "train" and t >= TRAIN_END:
            continue
        if period == "test" and t < TEST_START:
            continue

        coin = sig["coin"]
        if coin in cooldowns and t < cooldowns[coin]:
            continue
        if coin in active:
            continue
        if len(active) >= MAX_POS:
            # Check if any can be closed
            for c in list(active.keys()):
                pos = active[c]
                candles = data[c]
                if pos["entry_idx"] + hold < len(candles):
                    exit_p = candles[pos["entry_idx"] + hold]["c"]
                    if exit_p > 0:
                        gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
                        net = gross - COST
                        trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                                       "t": candles[pos["entry_idx"] + hold]["t"],
                                       "direction": pos["direction"]})
                    del active[c]
                    cooldowns[c] = candles[pos["entry_idx"] + hold]["t"] + 24 * 3600 * 1000
            if len(active) >= MAX_POS:
                continue

        candles = data[coin]
        entry_idx = sig["entry_idx"]
        if entry_idx >= len(candles) or entry_idx + hold >= len(candles):
            continue
        entry_price = candles[entry_idx]["o"]
        if entry_price <= 0:
            continue

        active[coin] = {"entry_idx": entry_idx, "entry_price": entry_price,
                        "direction": sig["direction"]}

    # Close remaining
    for c, pos in active.items():
        candles = data[c]
        exit_idx = min(pos["entry_idx"] + hold, len(candles) - 1)
        exit_p = candles[exit_idx]["c"]
        if exit_p > 0:
            gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
            net = gross - COST
            trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                           "t": candles[exit_idx]["t"], "direction": pos["direction"]})

    return trades


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Loading candles...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    for coin in list(data.keys()):
        for c in data[coin]:
            for k in ("o", "h", "l", "c", "v"):
                if k in c:
                    c[k] = float(c[k])

    print("Building features...")
    features = build_features(data)

    # ── SIGNAL 1: Volume Divergence ──────────────────────────
    print("\n" + "=" * 60)
    print("VOLUME DIVERGENCE — Portfolio Backtest")
    print("=" * 60)

    # Test top configs from Phase 1
    vd_configs = [
        (12, 12, 1500, 6),   # best combined P&L
        (18, 12, 2000, 6),
        (12, 6, 2000, 12),
        (12, 12, 2000, 6),
        (18, 6, 2000, 6),
    ]

    for pw, vw, thr, hold in vd_configs:
        signals = _detect_signals_voldiv(data, pw, vw, thr)
        print(f"\n  Config: pw={pw} vw={vw} thr={thr} hold={hold}")
        print(f"  Raw signals: {len(signals)}")

        for period in ["train", "test"]:
            trades = simple_backtest(signals, data, hold, period)
            s = score(trades)
            longs = [t for t in trades if t["direction"] == 1]
            shorts = [t for t in trades if t["direction"] == -1]
            sl = score(longs)
            ss = score(shorts)
            print(f"    {period:5s}: {s['n']}t avg={s['avg']:+.0f}bps WR={s['win']:.0f}% ${s['pnl']:+.0f}"
                  f"  [L:{sl['n']}t {sl['avg']:+.0f}bps | S:{ss['n']}t {ss['avg']:+.0f}bps]")

        # MC on full period
        all_trades = simple_backtest(signals, data, hold, "all")
        entries = _all_entries(data, hold)
        z = monte_carlo(all_trades, entries, hold)
        print(f"    MC z-score: {z}")

    # ── SIGNAL 2: Volume Spike Reversal ──────────────────────
    print("\n" + "=" * 60)
    print("VOLUME SPIKE REVERSAL — Portfolio Backtest")
    print("=" * 60)

    vs_configs = [
        (30, 5, 0.5, 12),   # best from Phase 1
        (30, 4, 0.5, 12),
        (42, 5, 0.3, 3),
        (42, 5, 0.5, 12),
        (30, 5, 0.3, 3),
        (42, 4, 0.3, 3),
        (30, 3, 0.5, 12),   # lower vol threshold
        (30, 3, 0.5, 6),    # shorter hold
    ]

    for vlb, vm, wr, hold in vs_configs:
        signals = _detect_signals_volspike(data, vlb, vm, wr)
        print(f"\n  Config: vlb={vlb} vm={vm}x wick={wr} hold={hold}")
        print(f"  Raw signals: {len(signals)}")

        for period in ["train", "test"]:
            trades = simple_backtest(signals, data, hold, period)
            s = score(trades)
            longs = [t for t in trades if t["direction"] == 1]
            shorts = [t for t in trades if t["direction"] == -1]
            sl = score(longs)
            ss = score(shorts)
            print(f"    {period:5s}: {s['n']}t avg={s['avg']:+.0f}bps WR={s['win']:.0f}% ${s['pnl']:+.0f}"
                  f"  [L:{sl['n']}t {sl['avg']:+.0f}bps | S:{ss['n']}t {ss['avg']:+.0f}bps]")

        all_trades = simple_backtest(signals, data, hold, "all")
        entries = _all_entries(data, hold)
        z = monte_carlo(all_trades, entries, hold)
        print(f"    MC z-score: {z}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
