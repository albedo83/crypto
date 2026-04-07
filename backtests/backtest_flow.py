"""Flow-based backtest — 3 signals using volume & cross-token dynamics.

Tests:
1. Volume divergence (price up + volume down = weakness → SHORT, and reverse)
2. Sector lead-lag (one token moves, others follow with delay)
3. Volume spike reversal (extreme volume + reversal candle = flush → fade)

Usage:
    python3 -m backtests.backtest_flow
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

SECTORS = {
    "L1":    ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":  ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO", "GMX"],
    "Gaming":["GALA", "IMX", "SAND"],
    "Infra": ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":  ["DOGE", "WLD", "BLUR", "MINA"],
}
TOKEN_SECTOR = {}
for sec, toks in SECTORS.items():
    for t in toks:
        TOKEN_SECTOR[t] = sec


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


# ═══════════════════════════════════════════════════════════
# 1. VOLUME DIVERGENCE
# ═══════════════════════════════════════════════════════════

def test_volume_divergence(data, features):
    """Price trending but volume declining = exhaustion → fade."""
    print("\n" + "=" * 60)
    print("1. VOLUME DIVERGENCE (price vs volume)")
    print("=" * 60)

    results = []
    for price_window in [6, 12, 18]:       # candles for price trend (1d, 2d, 3d)
        for vol_window in [6, 12, 18]:     # candles for volume trend
            for threshold in [500, 1000, 1500, 2000]:  # min price move bps
                for hold in [6, 12]:       # hold 1d, 2d
                    for period in ["train", "test"]:
                        trades = []
                        for coin in TOKENS:
                            if coin not in data:
                                continue
                            candles = data[coin]
                            for i in range(max(price_window, vol_window) + 1, len(candles) - hold):
                                t = candles[i]["t"]
                                if period == "train" and t >= TRAIN_END:
                                    continue
                                if period == "test" and t < TEST_START:
                                    continue

                                # Price return over window
                                p_now = candles[i]["c"]
                                p_prev = candles[i - price_window]["c"]
                                if p_prev <= 0 or p_now <= 0:
                                    continue
                                price_ret = (p_now / p_prev - 1) * 1e4

                                # Volume trend: average recent vs average prior
                                half = vol_window // 2
                                vol_recent = np.mean([candles[i - j]["v"] for j in range(half)])
                                vol_prior = np.mean([candles[i - half - j]["v"] for j in range(half)])
                                if vol_prior <= 0:
                                    continue
                                vol_change = (vol_recent / vol_prior - 1)  # ratio

                                # Divergence: price up + volume down (or price down + volume down)
                                direction = 0
                                if price_ret > threshold and vol_change < -0.3:
                                    direction = -1  # price up, volume down → SHORT (exhaustion)
                                elif price_ret < -threshold and vol_change < -0.3:
                                    direction = 1   # price down, volume down → LONG (selling exhaustion)

                                if direction == 0:
                                    continue

                                entry = candles[i + 1]["o"]
                                exit_p = candles[i + hold]["c"]
                                if entry <= 0:
                                    continue
                                gross = direction * (exit_p / entry - 1) * 1e4
                                net = gross - COST
                                trades.append({"coin": coin, "net": net,
                                               "pnl": SIZE * net / 1e4, "t": t,
                                               "direction": direction})

                        s = score(trades)
                        results.append({
                            "pw": price_window, "vw": vol_window,
                            "thr": threshold, "hold": hold,
                            "period": period, **s
                        })

    # Find train+test winners
    by_key = defaultdict(dict)
    for r in results:
        key = (r["pw"], r["vw"], r["thr"], r["hold"])
        by_key[key][r["period"]] = r

    winners = []
    for key, periods in by_key.items():
        tr, te = periods.get("train", {}), periods.get("test", {})
        if tr.get("pnl", 0) > 0 and te.get("pnl", 0) > 0 and tr.get("n", 0) >= 15 and te.get("n", 0) >= 5:
            winners.append((key, tr, te))

    winners.sort(key=lambda x: x[1]["pnl"] + x[2]["pnl"], reverse=True)
    print(f"\n{len(winners)} configs pass train+test (out of {len(by_key)})")
    for key, tr, te in winners[:10]:
        pw, vw, thr, hold = key
        print(f"  pw={pw} vw={vw} thr={thr} hold={hold}h"
              f" | Train: {tr['n']}t avg={tr['avg']:+.0f}bps ${tr['pnl']:+.0f}"
              f" | Test: {te['n']}t avg={te['avg']:+.0f}bps ${te['pnl']:+.0f}")

    # MC on best
    if winners:
        best_key = winners[0][0]
        pw, vw, thr, hold = best_key
        # Re-run best on full period to get all trades
        all_trades = []
        for coin in TOKENS:
            if coin not in data:
                continue
            candles = data[coin]
            for i in range(max(pw, vw) + 1, len(candles) - hold):
                p_now = candles[i]["c"]
                p_prev = candles[i - pw]["c"]
                if p_prev <= 0 or p_now <= 0:
                    continue
                price_ret = (p_now / p_prev - 1) * 1e4
                half = vw // 2
                vol_recent = np.mean([candles[i - j]["v"] for j in range(half)])
                vol_prior = np.mean([candles[i - half - j]["v"] for j in range(half)])
                if vol_prior <= 0:
                    continue
                vol_change = vol_recent / vol_prior - 1
                direction = 0
                if price_ret > thr and vol_change < -0.3:
                    direction = -1
                elif price_ret < -thr and vol_change < -0.3:
                    direction = 1
                if direction == 0:
                    continue
                entry = candles[i + 1]["o"]
                exit_p = candles[i + hold]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST
                all_trades.append({"coin": coin, "net": net, "pnl": SIZE * net / 1e4})

        entries = _all_entries(data, hold)
        z = monte_carlo(all_trades, entries, hold)
        print(f"  Monte Carlo z-score (best): {z}")

    return winners


# ═══════════════════════════════════════════════════════════
# 2. SECTOR LEAD-LAG
# ═══════════════════════════════════════════════════════════

def test_sector_leadlag(data, features):
    """When one token in a sector moves strongly, others follow with delay."""
    print("\n" + "=" * 60)
    print("2. SECTOR LEAD-LAG (follow the leader)")
    print("=" * 60)

    results = []
    for lookback in [1, 2, 3]:          # candles to detect leader move (4h, 8h, 12h)
        for leader_thr in [300, 500, 800, 1200]:  # min leader move bps
            for lag_max in [200, 400]:   # max follower move bps (hasn't moved yet)
                for hold in [6, 12]:     # hold 1d, 2d
                    for period in ["train", "test"]:
                        trades = []
                        for sector, members in SECTORS.items():
                            valid = [c for c in members if c in data]
                            if len(valid) < 3:
                                continue
                            # For each candle, find if there's a leader
                            max_len = min(len(data[c]) for c in valid)
                            for i in range(lookback + 1, max_len - hold):
                                t = data[valid[0]][i]["t"]
                                if period == "train" and t >= TRAIN_END:
                                    continue
                                if period == "test" and t < TEST_START:
                                    continue

                                # Compute returns for each member
                                rets = {}
                                for c in valid:
                                    candles = data[c]
                                    if i >= len(candles) or i - lookback < 0:
                                        continue
                                    p_now = candles[i]["c"]
                                    p_prev = candles[i - lookback]["c"]
                                    if p_prev > 0 and p_now > 0:
                                        rets[c] = (p_now / p_prev - 1) * 1e4

                                if len(rets) < 3:
                                    continue

                                # Find leader(s) and laggards
                                for leader, lr in rets.items():
                                    if abs(lr) < leader_thr:
                                        continue
                                    direction = 1 if lr > 0 else -1

                                    # Find followers that haven't moved yet
                                    for follower, fr in rets.items():
                                        if follower == leader:
                                            continue
                                        if abs(fr) > lag_max:
                                            continue  # already moved

                                        candles_f = data[follower]
                                        if i + 1 >= len(candles_f) or i + hold >= len(candles_f):
                                            continue
                                        entry = candles_f[i + 1]["o"]
                                        exit_p = candles_f[i + hold]["c"]
                                        if entry <= 0:
                                            continue
                                        gross = direction * (exit_p / entry - 1) * 1e4
                                        net = gross - COST
                                        trades.append({
                                            "coin": follower, "net": net,
                                            "pnl": SIZE * net / 1e4, "t": t,
                                            "leader": leader, "sector": sector
                                        })

                        s = score(trades)
                        results.append({
                            "lb": lookback, "lthr": leader_thr,
                            "lag": lag_max, "hold": hold,
                            "period": period, **s
                        })

    by_key = defaultdict(dict)
    for r in results:
        key = (r["lb"], r["lthr"], r["lag"], r["hold"])
        by_key[key][r["period"]] = r

    winners = []
    for key, periods in by_key.items():
        tr, te = periods.get("train", {}), periods.get("test", {})
        if tr.get("pnl", 0) > 0 and te.get("pnl", 0) > 0 and tr.get("n", 0) >= 15 and te.get("n", 0) >= 5:
            winners.append((key, tr, te))

    winners.sort(key=lambda x: x[1]["pnl"] + x[2]["pnl"], reverse=True)
    print(f"\n{len(winners)} configs pass train+test (out of {len(by_key)})")
    for key, tr, te in winners[:10]:
        lb, lthr, lag, hold = key
        print(f"  lb={lb} lthr={lthr} lag={lag} hold={hold}h"
              f" | Train: {tr['n']}t avg={tr['avg']:+.0f}bps ${tr['pnl']:+.0f}"
              f" | Test: {te['n']}t avg={te['avg']:+.0f}bps ${te['pnl']:+.0f}")

    if winners:
        best_key = winners[0][0]
        lb, lthr, lag, hold = best_key
        all_trades = []
        for sector, members in SECTORS.items():
            valid = [c for c in members if c in data]
            if len(valid) < 3:
                continue
            max_len = min(len(data[c]) for c in valid)
            for i in range(lb + 1, max_len - hold):
                rets = {}
                for c in valid:
                    candles = data[c]
                    if i >= len(candles) or i - lb < 0:
                        continue
                    p_now = candles[i]["c"]
                    p_prev = candles[i - lb]["c"]
                    if p_prev > 0 and p_now > 0:
                        rets[c] = (p_now / p_prev - 1) * 1e4
                if len(rets) < 3:
                    continue
                for leader, lr in rets.items():
                    if abs(lr) < lthr:
                        continue
                    direction = 1 if lr > 0 else -1
                    for follower, fr in rets.items():
                        if follower == leader or abs(fr) > lag:
                            continue
                        candles_f = data[follower]
                        if i + hold >= len(candles_f):
                            continue
                        entry = candles_f[i + 1]["o"]
                        exit_p = candles_f[i + hold]["c"]
                        if entry <= 0:
                            continue
                        gross = direction * (exit_p / entry - 1) * 1e4
                        net = gross - COST
                        all_trades.append({"coin": follower, "net": net, "pnl": SIZE * net / 1e4})
        entries = _all_entries(data, hold)
        z = monte_carlo(all_trades, entries, hold)
        print(f"  Monte Carlo z-score (best): {z}")

    return winners


# ═══════════════════════════════════════════════════════════
# 3. VOLUME SPIKE REVERSAL (flush detection)
# ═══════════════════════════════════════════════════════════

def test_volume_spike_reversal(data, features):
    """Extreme volume + reversal candle = liquidation flush → fade."""
    print("\n" + "=" * 60)
    print("3. VOLUME SPIKE REVERSAL (flush → fade)")
    print("=" * 60)

    results = []
    for vol_lookback in [30, 42, 60]:      # candles for vol baseline (5d, 7d, 10d)
        for vol_mult in [2.0, 3.0, 4.0, 5.0]:  # spike threshold (x times avg)
            for wick_ratio in [0.3, 0.5]:  # min wick ratio (reversal strength)
                for hold in [3, 6, 12]:    # hold 12h, 1d, 2d
                    for period in ["train", "test"]:
                        trades = []
                        for coin in TOKENS:
                            if coin not in data:
                                continue
                            candles = data[coin]
                            for i in range(vol_lookback + 1, len(candles) - hold):
                                t = candles[i]["t"]
                                if period == "train" and t >= TRAIN_END:
                                    continue
                                if period == "test" and t < TEST_START:
                                    continue

                                c = candles[i]
                                o, h, l, cl, v = c["o"], c["h"], c["l"], c["c"], c["v"]
                                if o <= 0 or v <= 0:
                                    continue
                                rng = h - l
                                if rng <= 0:
                                    continue

                                # Volume spike?
                                avg_vol = np.mean([candles[i - j]["v"] for j in range(1, vol_lookback + 1)])
                                if avg_vol <= 0 or v < avg_vol * vol_mult:
                                    continue

                                # Reversal candle? (long wick = rejection)
                                body = abs(cl - o)
                                direction = 0

                                if cl < o:
                                    # Red candle — check for bullish reversal (long lower wick)
                                    lower_wick = min(o, cl) - l
                                    if lower_wick / rng >= wick_ratio and cl < o:
                                        direction = 1  # LONG: selling flush with rejection
                                elif cl > o:
                                    # Green candle — check for bearish reversal (long upper wick)
                                    upper_wick = h - max(o, cl)
                                    if upper_wick / rng >= wick_ratio and cl > o:
                                        direction = -1  # SHORT: buying flush with rejection

                                if direction == 0:
                                    continue

                                entry = candles[i + 1]["o"]
                                exit_p = candles[i + hold]["c"]
                                if entry <= 0:
                                    continue
                                gross = direction * (exit_p / entry - 1) * 1e4
                                net = gross - COST
                                trades.append({"coin": coin, "net": net,
                                               "pnl": SIZE * net / 1e4, "t": t,
                                               "direction": direction})

                        s = score(trades)
                        results.append({
                            "vlb": vol_lookback, "vm": vol_mult,
                            "wr": wick_ratio, "hold": hold,
                            "period": period, **s
                        })

    by_key = defaultdict(dict)
    for r in results:
        key = (r["vlb"], r["vm"], r["wr"], r["hold"])
        by_key[key][r["period"]] = r

    winners = []
    for key, periods in by_key.items():
        tr, te = periods.get("train", {}), periods.get("test", {})
        if tr.get("pnl", 0) > 0 and te.get("pnl", 0) > 0 and tr.get("n", 0) >= 15 and te.get("n", 0) >= 5:
            winners.append((key, tr, te))

    winners.sort(key=lambda x: x[1]["pnl"] + x[2]["pnl"], reverse=True)
    print(f"\n{len(winners)} configs pass train+test (out of {len(by_key)})")
    for key, tr, te in winners[:10]:
        vlb, vm, wr, hold = key
        print(f"  vlb={vlb} vm={vm:.0f}x wick={wr:.1f} hold={hold}h"
              f" | Train: {tr['n']}t avg={tr['avg']:+.0f}bps ${tr['pnl']:+.0f}"
              f" | Test: {te['n']}t avg={te['avg']:+.0f}bps ${te['pnl']:+.0f}")

    if winners:
        best_key = winners[0][0]
        vlb, vm, wr, hold = best_key
        all_trades = []
        for coin in TOKENS:
            if coin not in data:
                continue
            candles = data[coin]
            for i in range(vlb + 1, len(candles) - hold):
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
                body = abs(cl - o)
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
                entry = candles[i + 1]["o"]
                exit_p = candles[i + hold]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST
                all_trades.append({"coin": coin, "net": net, "pnl": SIZE * net / 1e4})
        entries = _all_entries(data, hold)
        z = monte_carlo(all_trades, entries, hold)
        print(f"  Monte Carlo z-score (best): {z}")

    return winners


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Loading candles...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    # Convert string prices to float (some older files have string values)
    for coin in list(data.keys()):
        for c in data[coin]:
            for k in ("o", "h", "l", "c", "v"):
                if k in c:
                    c[k] = float(c[k])

    print("Building features...")
    features = build_features(data)

    test_volume_divergence(data, features)
    test_sector_leadlag(data, features)
    test_volume_spike_reversal(data, features)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
