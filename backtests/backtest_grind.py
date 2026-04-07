"""Grind signals — high-frequency mean-reversion patterns on 4h candles.

Looking for S10-quality: ~1 trade/day, 60%+ WR, +50-200 bps avg, short hold.

Tests:
1. Candle overextension fade (big 4h candle → fade next candle)
2. Consecutive candles fade (3+ same direction → fade)
3. Hammer/shooting star (candle pattern with wick > 2x body)
4. Range expansion fade (range > X * avg range → fade)
5. Intraday mean reversion (close near high/low of range → fade)

Usage:
    python3 -m backtests.backtest_grind
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
COOLDOWN_MS = 24 * 3600 * 1000


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


def simple_bt(signals, data, hold, period="all"):
    sorted_sigs = sorted(signals, key=lambda s: (s["t"], -s.get("strength", 0)))
    cooldowns = {}
    active = {}
    trades = []
    for sig in sorted_sigs:
        t = sig["t"]
        if period == "train" and t >= TRAIN_END:
            continue
        if period == "test" and t < TEST_START:
            continue
        for c in list(active.keys()):
            pos = active[c]
            candles = data[c]
            exit_idx = pos["entry_idx"] + hold
            if exit_idx < len(candles) and candles[exit_idx]["t"] <= t:
                exit_p = candles[exit_idx]["c"]
                if exit_p > 0:
                    gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
                    net = gross - COST
                    trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                                   "t": candles[exit_idx]["t"], "direction": pos["direction"]})
                del active[c]
                cooldowns[c] = candles[exit_idx]["t"] + COOLDOWN_MS
        coin = sig["coin"]
        if len(active) >= MAX_POS or coin in active:
            continue
        if coin in cooldowns and t < cooldowns[coin]:
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


def run_test(name, signals, data, hold):
    """Run train/test + MC for a signal set."""
    tr = simple_bt(signals, data, hold, "train")
    te = simple_bt(signals, data, hold, "test")
    s_tr, s_te = score(tr), score(te)

    all_t = simple_bt(signals, data, hold, "all")
    s_all = score(all_t)
    months = 27  # approximate dataset span

    passes = (s_tr["n"] >= 30 and s_te["n"] >= 15
              and s_tr["pnl"] > 0 and s_te["pnl"] > 0
              and s_tr["avg"] > 0 and s_te["avg"] > 0)

    z = None
    if passes:
        entries = _all_entries(data, hold)
        z = monte_carlo(all_t, entries, hold)

    freq = s_all["n"] / months
    return {
        "name": name, "hold": hold, "z": z,
        "train": s_tr, "test": s_te, "all": s_all,
        "passes": passes, "freq": freq,
        "raw_signals": len(signals),
    }


# ═══════════════════════════════════════════════════════════
# SIGNAL DETECTORS
# ═══════════════════════════════════════════════════════════

def detect_overextension(data, min_move_bps, hold):
    """Big 4h candle → fade in next candle(s)."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(1, len(candles) - 1):
            c = candles[i]
            o, cl = c["o"], c["c"]
            if o <= 0:
                continue
            ret = (cl / o - 1) * 1e4
            if abs(ret) < min_move_bps:
                continue
            direction = -1 if ret > 0 else 1  # fade
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction, "strength": abs(ret),
            })
    return signals


def detect_consecutive(data, n_consec, hold):
    """N consecutive same-direction candles → fade."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(n_consec, len(candles) - 1):
            # Check last n_consec candles are same direction
            dirs = []
            for j in range(n_consec):
                c = candles[i - j]
                if c["c"] > c["o"]:
                    dirs.append(1)
                elif c["c"] < c["o"]:
                    dirs.append(-1)
                else:
                    dirs.append(0)
            if 0 in dirs:
                continue
            if len(set(dirs)) != 1:
                continue
            trend_dir = dirs[0]
            # Compute total move
            total_ret = (candles[i]["c"] / candles[i - n_consec + 1]["o"] - 1) * 1e4
            signals.append({
                "coin": coin, "t": candles[i]["t"], "entry_idx": i + 1,
                "direction": -trend_dir,  # fade
                "strength": abs(total_ret),
            })
    return signals


def detect_hammer(data, wick_body_ratio, hold):
    """Hammer/shooting star: wick > ratio * body, fade the wick direction."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
            if o <= 0:
                continue
            body = abs(cl - o)
            rng = h - l
            if rng <= 0 or body <= 0:
                continue

            upper_wick = h - max(o, cl)
            lower_wick = min(o, cl) - l

            direction = 0
            # Hammer (bullish): long lower wick
            if lower_wick > body * wick_body_ratio and lower_wick > upper_wick * 1.5:
                direction = 1  # LONG
            # Shooting star (bearish): long upper wick
            elif upper_wick > body * wick_body_ratio and upper_wick > lower_wick * 1.5:
                direction = -1  # SHORT

            if direction == 0:
                continue
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction,
                "strength": max(upper_wick, lower_wick) / body,
            })
    return signals


def detect_range_expansion(data, lookback, mult, hold):
    """Range > mult * avg_range → fade the candle direction."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(lookback + 1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
            if o <= 0 or cl <= 0:
                continue
            rng = (h - l) / cl * 1e4  # range in bps

            avg_rng = np.mean([(candles[i-j]["h"] - candles[i-j]["l"]) / candles[i-j]["c"] * 1e4
                               for j in range(1, lookback + 1)
                               if candles[i-j]["c"] > 0])
            if avg_rng <= 0 or rng < avg_rng * mult:
                continue

            direction = -1 if cl > o else 1  # fade
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction,
                "strength": rng / avg_rng,
            })
    return signals


def detect_close_position(data, pct_threshold, hold):
    """Close near high/low of candle range → fade.
    If close in top X% of range → SHORT, bottom X% → LONG."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
            if o <= 0:
                continue
            rng = h - l
            if rng <= 0:
                continue
            # Where does close sit in the range?
            pos_in_range = (cl - l) / rng  # 0 = at low, 1 = at high

            # Need minimum range to be meaningful
            rng_bps = rng / cl * 1e4
            if rng_bps < 100:  # skip tiny candles
                continue

            direction = 0
            if pos_in_range >= (1 - pct_threshold):
                direction = -1  # close near high → SHORT
            elif pos_in_range <= pct_threshold:
                direction = 1   # close near low → LONG

            if direction == 0:
                continue
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction,
                "strength": rng_bps * (1 - pos_in_range if direction == 1 else pos_in_range),
            })
    return signals


def detect_engulfing(data, min_rng_bps):
    """Engulfing pattern: current candle engulfs previous, opposite direction → follow engulfing."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(2, len(candles) - 1):
            prev = candles[i - 1]
            curr = candles[i]
            po, pc = prev["o"], prev["c"]
            co, cc = curr["o"], curr["c"]
            if po <= 0 or co <= 0:
                continue

            # Current range must be significant
            rng_bps = (curr["h"] - curr["l"]) / cc * 1e4
            if rng_bps < min_rng_bps:
                continue

            # Previous candle direction
            prev_up = pc > po
            curr_up = cc > co

            # Must be opposite direction
            if prev_up == curr_up:
                continue

            # Engulfing: current body contains previous body
            curr_body_hi = max(co, cc)
            curr_body_lo = min(co, cc)
            prev_body_hi = max(po, pc)
            prev_body_lo = min(po, pc)

            if curr_body_hi >= prev_body_hi and curr_body_lo <= prev_body_lo:
                direction = 1 if curr_up else -1  # follow the engulfing
                signals.append({
                    "coin": coin, "t": curr["t"], "entry_idx": i + 1,
                    "direction": direction,
                    "strength": rng_bps,
                })
    return signals


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

    results = []

    # ── 1. Overextension fade ────────────────────────────────
    print("\n" + "=" * 60)
    print("1. OVEREXTENSION FADE (big 4h candle → fade)")
    print("=" * 60)
    for move in [200, 300, 400, 500, 600, 800]:
        for hold in [1, 2, 3, 6]:
            sigs = detect_overextension(data, move, hold)
            r = run_test(f"OE mv={move} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  mv={move} h={hold} | {r['freq']:.1f}/mo"
                      f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                      f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                      f" | z={r['z']}")

    # ── 2. Consecutive candles fade ──────────────────────────
    print("\n" + "=" * 60)
    print("2. CONSECUTIVE CANDLES FADE (N same dir → fade)")
    print("=" * 60)
    for n_con in [3, 4, 5, 6]:
        for hold in [1, 2, 3, 6]:
            sigs = detect_consecutive(data, n_con, hold)
            r = run_test(f"CC n={n_con} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  n={n_con} h={hold} | {r['freq']:.1f}/mo"
                      f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                      f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                      f" | z={r['z']}")

    # ── 3. Hammer / Shooting star ────────────────────────────
    print("\n" + "=" * 60)
    print("3. HAMMER / SHOOTING STAR (wick pattern)")
    print("=" * 60)
    for ratio in [1.5, 2.0, 2.5, 3.0, 4.0]:
        for hold in [1, 2, 3, 6]:
            sigs = detect_hammer(data, ratio, hold)
            r = run_test(f"HS r={ratio} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  r={ratio} h={hold} | {r['freq']:.1f}/mo"
                      f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                      f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                      f" | z={r['z']}")

    # ── 4. Range expansion fade ──────────────────────────────
    print("\n" + "=" * 60)
    print("4. RANGE EXPANSION FADE (big range → fade)")
    print("=" * 60)
    for lb in [12, 24, 42]:
        for mult in [2.0, 2.5, 3.0, 4.0]:
            for hold in [1, 2, 3, 6]:
                sigs = detect_range_expansion(data, lb, mult, hold)
                r = run_test(f"RE lb={lb} m={mult} h={hold}", sigs, data, hold)
                results.append(r)
                if r["passes"] and r["z"] and r["z"] >= 2.0:
                    print(f"  lb={lb} m={mult} h={hold} | {r['freq']:.1f}/mo"
                          f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                          f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                          f" | z={r['z']}")

    # ── 5. Close position fade ───────────────────────────────
    print("\n" + "=" * 60)
    print("5. CLOSE POSITION FADE (close near high/low → fade)")
    print("=" * 60)
    for pct in [0.05, 0.10, 0.15, 0.20]:
        for hold in [1, 2, 3, 6]:
            sigs = detect_close_position(data, pct, hold)
            r = run_test(f"CP p={pct} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  p={pct} h={hold} | {r['freq']:.1f}/mo"
                      f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                      f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                      f" | z={r['z']}")

    # ── 6. Engulfing pattern ─────────────────────────────────
    print("\n" + "=" * 60)
    print("6. ENGULFING PATTERN (reversal candle → follow)")
    print("=" * 60)
    for min_rng in [150, 200, 300, 400, 500]:
        for hold in [1, 2, 3, 6]:
            sigs = detect_engulfing(data, min_rng)
            r = run_test(f"EG rng={min_rng} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  rng={min_rng} h={hold} | {r['freq']:.1f}/mo"
                      f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps"
                      f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps"
                      f" | z={r['z']}")

    # ── Summary ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TOP 15 (z >= 2.0, sorted by z)")
    print("=" * 60)
    winners = [r for r in results if r["passes"] and r["z"] and r["z"] >= 2.0]
    winners.sort(key=lambda r: r["z"], reverse=True)
    for r in winners[:15]:
        print(f"  z={r['z']:5.2f} | {r['name']:25s} | {r['freq']:.1f}/mo"
              f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}%"
              f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}%"
              f" | Total ${r['all']['pnl']:+.0f}")
