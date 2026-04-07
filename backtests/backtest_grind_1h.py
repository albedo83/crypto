"""Grind signals on 1h candles — download 7 months + test high-frequency patterns.

Downloads 1h candles from Hyperliquid (Sept 2025 → now) for all 30 tokens,
then tests the same mean-reversion patterns as backtest_grind but at 1h resolution.

S10-quality target: frequent, 60%+ WR, +50-200 bps avg, short hold.

Usage:
    python3 -m backtests.backtest_grind_1h
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

TOKENS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
]
REF = ["BTC", "ETH"]
ALL = TOKENS + REF

COST_BPS = 12.0
SIZE = 250.0
MAX_POS = 6
COOLDOWN_H = 24  # hours

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

# Train/test split: first 5 months train, last 2 months test
TRAIN_END = datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp() * 1000
TEST_START = TRAIN_END


def download_1h_candles():
    """Download 1h candles for all tokens, cache locally."""
    data = {}
    for coin in ALL:
        cache = os.path.join(DATA_DIR, f"{coin}_1h_7m.json")
        if os.path.exists(cache):
            with open(cache) as f:
                candles = json.load(f)
            if len(candles) > 100:
                data[coin] = candles
                continue

        print(f"  Downloading {coin} 1h...", end=" ", flush=True)
        all_candles = []
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - 210 * 86400 * 1000  # ~7 months back
        chunk = 7 * 86400 * 1000  # 1 week per request

        cursor = start_ts
        while cursor < end_ts:
            chunk_end = min(cursor + chunk, end_ts)
            payload = json.dumps({
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1h",
                        "startTime": cursor, "endTime": chunk_end}
            }).encode()
            try:
                req = urllib.request.Request(
                    "https://api.hyperliquid.xyz/info",
                    data=payload,
                    headers={"Content-Type": "application/json"})
                resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
                if resp:
                    all_candles.extend(resp)
            except Exception as e:
                print(f"err: {e}", end=" ")
            cursor = chunk_end
            time.sleep(0.1)

        # Dedupe by timestamp
        seen = set()
        deduped = []
        for c in all_candles:
            if c["t"] not in seen:
                seen.add(c["t"])
                deduped.append(c)
        deduped.sort(key=lambda c: c["t"])

        if len(deduped) > 100:
            # Convert to float
            for c in deduped:
                for k in ("o", "h", "l", "c", "v"):
                    if k in c:
                        c[k] = float(c[k])
            with open(cache, "w") as f:
                json.dump(deduped, f)
            data[coin] = deduped
            first = datetime.fromtimestamp(deduped[0]["t"]/1000).strftime("%Y-%m-%d")
            last = datetime.fromtimestamp(deduped[-1]["t"]/1000).strftime("%Y-%m-%d")
            print(f"{len(deduped)} candles ({first} → {last})")
        else:
            print(f"only {len(deduped)} candles, skipping")

    return data


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
            entry = float(candles[idx]["o"]) if float(candles[idx]["o"]) > 0 else float(candles[idx]["c"])
            exit_p = float(candles[idx + hold]["c"])
            if entry <= 0:
                continue
            gross = direction * (exit_p / entry - 1) * 1e4
            sim += SIZE * (gross - COST_BPS) / 1e4
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
    cooldown_ms = COOLDOWN_H * 3600 * 1000
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
                exit_p = float(candles[exit_idx]["c"])
                if exit_p > 0:
                    gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
                    net = gross - COST_BPS
                    trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                                   "t": candles[exit_idx]["t"], "direction": pos["direction"]})
                del active[c]
                cooldowns[c] = candles[exit_idx]["t"] + cooldown_ms
        coin = sig["coin"]
        if len(active) >= MAX_POS or coin in active:
            continue
        if coin in cooldowns and t < cooldowns[coin]:
            continue
        candles = data[coin]
        entry_idx = sig["entry_idx"]
        if entry_idx >= len(candles) or entry_idx + hold >= len(candles):
            continue
        entry_price = float(candles[entry_idx]["o"])
        if entry_price <= 0:
            continue
        active[coin] = {"entry_idx": entry_idx, "entry_price": entry_price,
                        "direction": sig["direction"]}
    for c, pos in active.items():
        candles = data[c]
        exit_idx = min(pos["entry_idx"] + hold, len(candles) - 1)
        exit_p = float(candles[exit_idx]["c"])
        if exit_p > 0:
            gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
            net = gross - COST_BPS
            trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                           "t": candles[exit_idx]["t"], "direction": pos["direction"]})
    return trades


def run_test(name, signals, data, hold):
    tr = simple_bt(signals, data, hold, "train")
    te = simple_bt(signals, data, hold, "test")
    s_tr, s_te = score(tr), score(te)
    all_t = simple_bt(signals, data, hold, "all")
    s_all = score(all_t)
    months = 7
    passes = (s_tr["n"] >= 20 and s_te["n"] >= 10
              and s_tr["pnl"] > 0 and s_te["pnl"] > 0
              and s_tr["avg"] > 0 and s_te["avg"] > 0)
    z = None
    if passes:
        entries = _all_entries(data, hold)
        z = monte_carlo(all_t, entries, hold)
    return {
        "name": name, "hold": hold, "z": z,
        "train": s_tr, "test": s_te, "all": s_all,
        "passes": passes, "freq": s_all["n"] / months,
        "raw": len(signals),
    }


# ═══════════════════════════════════════════════════════════
# SIGNAL DETECTORS (1h resolution)
# ═══════════════════════════════════════════════════════════

def detect_overextension(data, min_bps):
    """Single 1h candle move > threshold → fade."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(1, len(candles) - 1):
            o, c = float(candles[i]["o"]), float(candles[i]["c"])
            if o <= 0:
                continue
            ret = (c / o - 1) * 1e4
            if abs(ret) < min_bps:
                continue
            signals.append({
                "coin": coin, "t": candles[i]["t"], "entry_idx": i + 1,
                "direction": -1 if ret > 0 else 1, "strength": abs(ret),
            })
    return signals


def detect_multi_candle_move(data, n_candles, min_bps):
    """Move > threshold over N candles → fade."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(n_candles, len(candles) - 1):
            p_now = float(candles[i]["c"])
            p_prev = float(candles[i - n_candles]["c"])
            if p_prev <= 0 or p_now <= 0:
                continue
            ret = (p_now / p_prev - 1) * 1e4
            if abs(ret) < min_bps:
                continue
            signals.append({
                "coin": coin, "t": candles[i]["t"], "entry_idx": i + 1,
                "direction": -1 if ret > 0 else 1, "strength": abs(ret),
            })
    return signals


def detect_squeeze_breakout(data, squeeze_window, vol_max_ratio, expansion_bps):
    """Squeeze (low vol) → expansion → fade breakout. S10 on 1h."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(squeeze_window + 30, len(candles) - 1):
            c = candles[i]
            o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
            if cl <= 0:
                continue
            rng = (h - l) / cl * 1e4

            # Check squeeze: recent ranges smaller than longer-term avg
            recent_rngs = []
            for j in range(1, squeeze_window + 1):
                cj = candles[i - j]
                if float(cj["c"]) > 0:
                    recent_rngs.append((float(cj["h"]) - float(cj["l"])) / float(cj["c"]) * 1e4)
            if not recent_rngs:
                continue
            avg_recent = np.mean(recent_rngs)

            long_rngs = []
            for j in range(squeeze_window + 1, squeeze_window + 31):
                cj = candles[i - j]
                if float(cj["c"]) > 0:
                    long_rngs.append((float(cj["h"]) - float(cj["l"])) / float(cj["c"]) * 1e4)
            if not long_rngs:
                continue
            avg_long = np.mean(long_rngs)

            if avg_long <= 0 or avg_recent / avg_long > vol_max_ratio:
                continue  # not squeezed enough

            # Current candle must be an expansion
            if rng < expansion_bps:
                continue

            # Fade the breakout
            direction = -1 if cl > o else 1
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction,
                "strength": rng / avg_recent if avg_recent > 0 else rng,
            })
    return signals


def detect_wick_rejection(data, wick_ratio, min_rng_bps):
    """Long wick with small body = rejection → trade in wick direction."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
            if o <= 0 or cl <= 0:
                continue
            rng = h - l
            if rng <= 0:
                continue
            rng_bps = rng / cl * 1e4
            if rng_bps < min_rng_bps:
                continue
            body = abs(cl - o)
            if body <= 0:
                continue
            upper_wick = h - max(o, cl)
            lower_wick = min(o, cl) - l

            direction = 0
            if lower_wick > body * wick_ratio and lower_wick > upper_wick * 1.5:
                direction = 1   # hammer → LONG
            elif upper_wick > body * wick_ratio and upper_wick > lower_wick * 1.5:
                direction = -1  # shooting star → SHORT

            if direction == 0:
                continue
            signals.append({
                "coin": coin, "t": c["t"], "entry_idx": i + 1,
                "direction": direction,
                "strength": max(upper_wick, lower_wick) / body,
            })
    return signals


def detect_btc_divergence(data, lookback, btc_move_bps, alt_max_bps):
    """BTC moves but alt doesn't follow → alt catches up."""
    if "BTC" not in data:
        return []
    btc_candles = data["BTC"]
    btc_by_t = {c["t"]: c for c in btc_candles}

    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(lookback, len(candles) - 1):
            t = candles[i]["t"]
            # Find BTC candle at same time
            btc_c = btc_by_t.get(t)
            if not btc_c:
                continue

            # BTC move over lookback
            btc_t_prev = candles[i - lookback]["t"]
            btc_prev = btc_by_t.get(btc_t_prev)
            if not btc_prev:
                continue

            btc_ret = (float(btc_c["c"]) / float(btc_prev["c"]) - 1) * 1e4
            if abs(btc_ret) < btc_move_bps:
                continue

            # Alt move over same period
            alt_ret = (float(candles[i]["c"]) / float(candles[i - lookback]["c"]) - 1) * 1e4
            if abs(alt_ret) > alt_max_bps:
                continue  # already moved

            # Alt should follow BTC
            direction = 1 if btc_ret > 0 else -1
            signals.append({
                "coin": coin, "t": t, "entry_idx": i + 1,
                "direction": direction,
                "strength": abs(btc_ret) - abs(alt_ret),
            })
    return signals


def detect_consecutive(data, n_consec):
    """N consecutive same-direction 1h candles → fade."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(n_consec, len(candles) - 1):
            dirs = []
            for j in range(n_consec):
                c = candles[i - j]
                d = 1 if float(c["c"]) > float(c["o"]) else (-1 if float(c["c"]) < float(c["o"]) else 0)
                dirs.append(d)
            if 0 in dirs or len(set(dirs)) != 1:
                continue
            total = (float(candles[i]["c"]) / float(candles[i - n_consec + 1]["o"]) - 1) * 1e4
            signals.append({
                "coin": coin, "t": candles[i]["t"], "entry_idx": i + 1,
                "direction": -dirs[0], "strength": abs(total),
            })
    return signals


def detect_engulfing(data, min_rng_bps):
    """Engulfing pattern on 1h."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(2, len(candles) - 1):
            prev = candles[i - 1]
            curr = candles[i]
            po, pc = float(prev["o"]), float(prev["c"])
            co, cc = float(curr["o"]), float(curr["c"])
            if po <= 0 or co <= 0:
                continue
            rng_bps = (float(curr["h"]) - float(curr["l"])) / cc * 1e4
            if rng_bps < min_rng_bps:
                continue
            prev_up = pc > po
            curr_up = cc > co
            if prev_up == curr_up:
                continue
            if max(co, cc) >= max(po, pc) and min(co, cc) <= min(po, pc):
                direction = 1 if curr_up else -1
                signals.append({
                    "coin": coin, "t": curr["t"], "entry_idx": i + 1,
                    "direction": direction, "strength": rng_bps,
                })
    return signals


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Downloading 1h candles (cached)...")
    data = download_1h_candles()
    print(f"Loaded {len(data)} tokens")

    if len(data) < 20:
        print("Not enough tokens, aborting")
        exit(1)

    results = []

    # ── 1. Overextension fade ────────────────────────────
    print("\n" + "=" * 60)
    print("1. OVEREXTENSION FADE (single 1h candle)")
    print("=" * 60)
    for bps in [100, 150, 200, 300, 400, 500]:
        for hold in [1, 2, 3, 4, 6, 12, 24]:
            sigs = detect_overextension(data, bps)
            r = run_test(f"OE bps={bps} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  bps={bps} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 2. Multi-candle move fade ────────────────────────
    print("\n" + "=" * 60)
    print("2. MULTI-CANDLE MOVE FADE (N hours move → fade)")
    print("=" * 60)
    for n in [2, 3, 4, 6]:
        for bps in [200, 300, 500, 800, 1000]:
            for hold in [2, 4, 6, 12, 24]:
                sigs = detect_multi_candle_move(data, n, bps)
                r = run_test(f"MC n={n} bps={bps} h={hold}", sigs, data, hold)
                results.append(r)
                if r["passes"] and r["z"] and r["z"] >= 2.0:
                    print(f"  n={n}h bps={bps} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 3. Squeeze breakout fade (S10 on 1h) ─────────────
    print("\n" + "=" * 60)
    print("3. SQUEEZE BREAKOUT FADE (S10 on 1h)")
    print("=" * 60)
    for sq_w in [6, 12, 24]:
        for vol_max in [0.5, 0.6, 0.7]:
            for exp_bps in [100, 150, 200, 300]:
                for hold in [3, 6, 12, 24]:
                    sigs = detect_squeeze_breakout(data, sq_w, vol_max, exp_bps)
                    r = run_test(f"SQ w={sq_w} vm={vol_max} exp={exp_bps} h={hold}", sigs, data, hold)
                    results.append(r)
                    if r["passes"] and r["z"] and r["z"] >= 2.0:
                        print(f"  w={sq_w} vm={vol_max} exp={exp_bps} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 4. Wick rejection ────────────────────────────────
    print("\n" + "=" * 60)
    print("4. WICK REJECTION (hammer/shooting star 1h)")
    print("=" * 60)
    for wr in [1.5, 2.0, 3.0, 4.0]:
        for min_rng in [50, 100, 150, 200]:
            for hold in [1, 2, 3, 6, 12]:
                sigs = detect_wick_rejection(data, wr, min_rng)
                r = run_test(f"WR r={wr} rng={min_rng} h={hold}", sigs, data, hold)
                results.append(r)
                if r["passes"] and r["z"] and r["z"] >= 2.0:
                    print(f"  r={wr} rng={min_rng} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 5. BTC divergence ────────────────────────────────
    print("\n" + "=" * 60)
    print("5. BTC DIVERGENCE (BTC moves, alt doesn't → follow)")
    print("=" * 60)
    for lb in [2, 4, 6, 12]:
        for btc_mv in [100, 200, 300, 500]:
            for alt_max in [50, 100, 150]:
                for hold in [2, 4, 6, 12, 24]:
                    sigs = detect_btc_divergence(data, lb, btc_mv, alt_max)
                    r = run_test(f"BD lb={lb} btc={btc_mv} alt={alt_max} h={hold}", sigs, data, hold)
                    results.append(r)
                    if r["passes"] and r["z"] and r["z"] >= 2.0:
                        print(f"  lb={lb} btc={btc_mv} alt={alt_max} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 6. Consecutive fade ──────────────────────────────
    print("\n" + "=" * 60)
    print("6. CONSECUTIVE 1H CANDLES FADE")
    print("=" * 60)
    for n in [3, 4, 5, 6, 7, 8]:
        for hold in [1, 2, 3, 6, 12]:
            sigs = detect_consecutive(data, n)
            r = run_test(f"CC n={n} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  n={n} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── 7. Engulfing ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("7. ENGULFING PATTERN 1H")
    print("=" * 60)
    for rng in [50, 100, 150, 200, 300]:
        for hold in [1, 2, 3, 6, 12]:
            sigs = detect_engulfing(data, rng)
            r = run_test(f"EG rng={rng} h={hold}", sigs, data, hold)
            results.append(r)
            if r["passes"] and r["z"] and r["z"] >= 2.0:
                print(f"  rng={rng} h={hold} | {r['freq']:.1f}/mo | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}% | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}% | z={r['z']}")

    # ── Summary ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TOP 20 SIGNALS (z >= 2.0)")
    print("=" * 60)
    winners = [r for r in results if r["passes"] and r["z"] and r["z"] >= 2.0]
    winners.sort(key=lambda r: r["z"], reverse=True)
    for r in winners[:20]:
        print(f"  z={r['z']:5.2f} | {r['name']:35s} | {r['freq']:.1f}/mo"
              f" | Tr: {r['train']['n']}t {r['train']['avg']:+.0f}bps {r['train']['win']:.0f}%"
              f" | Te: {r['test']['n']}t {r['test']['avg']:+.0f}bps {r['test']['win']:.0f}%"
              f" | ${r['all']['pnl']:+.0f}")

    if not winners:
        print("  Nothing passes z >= 2.0 on train+test")

    print(f"\nTotal configs tested: {len(results)}")
    print(f"Configs passing train+test: {sum(1 for r in results if r['passes'])}")
    print(f"Configs with z >= 2.0: {len(winners)}")
