"""Exploration 3 — Three novel ideas.

1. Liquidation cascades (proxy: large wicks → trade the bounce)
2. Macro signals (S&P 500, DXY as crypto entry signals)
3. Non-standard candle offsets (shift timing by 1-2 candles)

Usage:
    python3 -m analysis.backtest_explore3
"""

from __future__ import annotations

import json, os, time, random, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy, backtest_strategy, quick_score,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS, MAX_SAME_DIR,
    TRAIN_END, TEST_START,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")


# ═══════════════════════════════════════════════════════════════════
# 1. LIQUIDATION CASCADE DETECTION
# ═══════════════════════════════════════════════════════════════════

def liquidation_cascades(features, data):
    """Detect liquidation cascades via large candle wicks.

    A liquidation cascade shows as a candle with:
    - Very large range (high-low) vs average
    - Long wick relative to body (shadow >> body)

    Strategy ideas:
    A) After a cascade → mean-revert (bounce)
    B) When conditions favor cascades → position before
    """
    print("\n" + "=" * 70)
    print("  LIQUIDATION CASCADE DETECTION")
    print("  Large wicks = liquidation events → trade the bounce")
    print("=" * 70)

    coins = [c for c in TOKENS if c in data]

    # Compute wick metrics for all candles
    for coin in coins:
        candles = data[coin]
        for i, c in enumerate(candles):
            body = abs(c["c"] - c["o"])
            total_range = c["h"] - c["l"]
            if c["c"] > 0 and total_range > 0:
                c["range_bps"] = total_range / c["c"] * 1e4
                c["wick_ratio"] = (total_range - body) / total_range  # 0=no wick, 1=all wick
                # Direction of the wick
                if c["c"] > c["o"]:  # green candle
                    c["lower_wick"] = (min(c["o"], c["c"]) - c["l"]) / total_range
                else:  # red candle
                    c["lower_wick"] = (min(c["o"], c["c"]) - c["l"]) / total_range
            else:
                c["range_bps"] = 0
                c["wick_ratio"] = 0
                c["lower_wick"] = 0

    # Strategy A: After a large wick, trade the bounce
    print(f"\n  Strategy A: After liquidation cascade → mean-revert")

    configs = []
    for range_mult in [2.0, 3.0, 4.0, 5.0]:
        for wick_min in [0.3, 0.5, 0.7]:
            for hold in [3, 6, 12, 18]:
                for direction_mode in ["bounce", "continue"]:
                    configs.append((range_mult, wick_min, hold, direction_mode))

    results = []
    for range_mult, wick_min, hold, dir_mode in configs:
        trades = []
        positions = {}
        cooldown = {}

        for coin in coins:
            candles = data[coin]
            # Compute rolling average range
            for i in range(50, len(candles) - hold - 1):
                if coin in positions:
                    # Check exit
                    pos = positions[coin]
                    held = i - pos["idx"]
                    if held >= hold:
                        exit_p = candles[i]["c"]
                        if exit_p > 0:
                            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4
                            net = gross - COST_BPS
                            trades.append({
                                "pnl": POSITION_SIZE * net / 1e4, "net": net,
                                "dir": pos["dir"], "coin": coin,
                                "entry_t": candles[pos["idx"]]["t"],
                            })
                        del positions[coin]
                    continue

                if coin in cooldown and i < cooldown[coin]:
                    continue

                c = candles[i]
                avg_range = float(np.mean([candles[j]["range_bps"]
                                           for j in range(max(0, i-42), i)])) or 1

                # Cascade detection
                if c["range_bps"] < avg_range * range_mult:
                    continue
                if c["wick_ratio"] < wick_min:
                    continue

                # Direction
                if dir_mode == "bounce":
                    # Candle went down hard → long (bounce)
                    direction = 1 if c["c"] < c["o"] else -1
                else:
                    # Continue the cascade direction
                    direction = -1 if c["c"] < c["o"] else 1

                entry = candles[i + 1]["o"]
                if entry <= 0:
                    continue

                positions[coin] = {
                    "dir": direction, "entry": entry,
                    "idx": i + 1,
                }
                cooldown[coin] = i + hold + 6

        if len(trades) < 15:
            continue

        pnl = sum(t["pnl"] for t in trades)
        avg = float(np.mean([t["net"] for t in trades]))
        train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
        test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

        results.append({
            "label": f"rng×{range_mult} wick>{wick_min} h={hold*4}h {dir_mode}",
            "pnl": pnl, "n": len(trades), "avg": avg,
            "train": train_pnl, "test": test_pnl,
            "trades": trades,
        })

    results.sort(key=lambda r: r["pnl"], reverse=True)

    valid = [r for r in results if r["train"] > 0 and r["test"] > 0 and r["n"] >= 20]
    print(f"  Tested {len(results)} configs → {len(valid)} profitable train+test")

    print(f"\n  {'Config':<45} {'P&L':>7} {'N':>5} {'Avg':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*80}")
    for r in (valid[:10] if valid else results[:10]):
        v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        print(f"  {r['label']:<45} ${r['pnl']:>+6.0f} {r['n']:>4} {r['avg']:>+5.1f} "
              f"${r['train']:>+6.0f} ${r['test']:>+6.0f} {v}")

    # Monte Carlo on best valid
    if valid:
        best = valid[0]
        mc = _monte_carlo(best["trades"], data)
        print(f"\n  Monte Carlo on best: {best['label']}")
        print(f"    Actual: ${best['pnl']:>+.0f} | Random: ${mc['mean']:>+.0f} "
              f"(σ=${mc['std']:.0f}) | Z: {mc['z']:+.2f}")
        if mc["z"] > 2:
            print(f"    → ✓ SIGNIFICANT")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    return valid


# ═══════════════════════════════════════════════════════════════════
# 2. MACRO TRADFI SIGNALS
# ═══════════════════════════════════════════════════════════════════

def macro_signals(features, data):
    """Test traditional market indicators as crypto signals.

    Fetch S&P 500 and DXY data, align with crypto 4h candles,
    test as entry features.
    """
    print("\n" + "=" * 70)
    print("  MACRO TRADFI SIGNALS")
    print("  S&P 500 + DXY as crypto entry signals")
    print("=" * 70)

    # Fetch macro data from Yahoo Finance
    macro_data = {}
    for ticker, name in [("^GSPC", "SP500"), ("DX-Y.NYB", "DXY")]:
        cache = os.path.join(DATA_DIR, f"macro_{name}.json")
        if os.path.exists(cache):
            with open(cache) as f:
                macro_data[name] = json.load(f)
            print(f"  {name}: loaded from cache ({len(macro_data[name])} days)")
            continue

        print(f"  Fetching {name} from Yahoo Finance...")
        try:
            # Yahoo Finance v8 API (free, no key needed)
            end_ts = int(time.time())
            start_ts = end_ts - 1100 * 86400  # ~3 years
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                   f"?period1={start_ts}&period2={end_ts}&interval=1d")
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read())

            result = raw["chart"]["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]

            daily = []
            for i, ts in enumerate(timestamps):
                if closes[i] is not None:
                    daily.append({"t": ts * 1000, "c": closes[i]})

            macro_data[name] = daily
            with open(cache, "w") as f:
                json.dump(daily, f)
            print(f"  {name}: fetched {len(daily)} days")
        except Exception as e:
            print(f"  {name}: FAILED — {str(e)[:100]}")

    if not macro_data:
        print("  No macro data available!")
        return []

    # Build macro features aligned with crypto timestamps
    # For each crypto 4h candle, find the most recent daily macro close
    coins = [c for c in TOKENS if c in features]

    for name, daily in macro_data.items():
        if len(daily) < 100:
            print(f"  {name}: not enough data ({len(daily)} days)")
            continue

        closes = np.array([d["c"] for d in daily])
        timestamps = [d["t"] for d in daily]

        # Compute returns
        macro_rets = {}
        for i in range(30, len(daily)):
            t = timestamps[i]
            macro_rets[t] = {
                f"{name}_7d": (closes[i] / closes[i-5] - 1) * 1e4 if closes[i-5] > 0 else 0,  # 5 trading days
                f"{name}_30d": (closes[i] / closes[i-22] - 1) * 1e4 if closes[i-22] > 0 else 0,  # 22 trading days
            }

        print(f"\n  Testing {name} as crypto signal:")
        print(f"  {name} returns: {len(macro_rets)} daily observations")

        # Find closest macro data for each crypto timestamp
        macro_ts_list = sorted(macro_rets.keys())

        results = []
        for feat_name in [f"{name}_7d", f"{name}_30d"]:
            for thresh in [-300, -200, -100, 100, 200, 300, 500]:
                for op in [">", "<"]:
                    for direction in [1, -1]:
                        trades = []
                        positions = {}
                        cooldown = {}

                        for coin in coins:
                            candles = data[coin]
                            macro_idx = 0

                            for fi, f in enumerate(features.get(coin, [])):
                                t = f["t"]

                                # Find closest macro timestamp
                                while macro_idx < len(macro_ts_list) - 1 and macro_ts_list[macro_idx + 1] <= t:
                                    macro_idx += 1
                                if macro_idx >= len(macro_ts_list):
                                    continue
                                mt = macro_ts_list[macro_idx]
                                if abs(mt - t) > 3 * 86400 * 1000:  # skip if >3 days gap
                                    continue

                                macro_val = macro_rets.get(mt, {}).get(feat_name, 0)

                                # Check condition
                                if op == ">" and macro_val <= thresh:
                                    continue
                                if op == "<" and macro_val >= thresh:
                                    continue

                                # Check position management
                                if coin in positions or (coin in cooldown and t < cooldown.get(coin, 0)):
                                    continue

                                idx = f["_idx"]
                                if idx + 19 >= len(candles):
                                    continue
                                entry = candles[idx + 1]["o"]
                                if entry <= 0:
                                    continue

                                # Simple backtest: entry next candle, hold 18 candles
                                exit_p = candles[idx + 18]["c"]
                                gross = direction * (exit_p / entry - 1) * 1e4
                                net = gross - COST_BPS
                                trades.append({
                                    "pnl": POSITION_SIZE * net / 1e4, "net": net,
                                    "dir": direction, "coin": coin, "entry_t": t,
                                })
                                cooldown[coin] = t + 24 * 3600 * 1000

                        if len(trades) < 20:
                            continue

                        pnl = sum(t["pnl"] for t in trades)
                        avg = float(np.mean([t["net"] for t in trades]))
                        train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
                        test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

                        dir_str = "LONG" if direction == 1 else "SHORT"
                        results.append({
                            "label": f"{feat_name} {op} {thresh} → {dir_str}",
                            "pnl": pnl, "n": len(trades), "avg": avg,
                            "train": train_pnl, "test": test_pnl,
                            "trades": trades,
                        })

        results.sort(key=lambda r: r["pnl"], reverse=True)
        valid = [r for r in results if r["train"] > 0 and r["test"] > 0 and r["n"] >= 20]

        print(f"  Tested {len(results)} rules → {len(valid)} profitable train+test")
        print(f"\n  {'Rule':<40} {'P&L':>7} {'N':>5} {'Avg':>6} {'Trn':>7} {'Tst':>7}")
        print(f"  {'-'*75}")
        for r in (valid[:10] if valid else results[:5]):
            v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
            print(f"  {r['label']:<40} ${r['pnl']:>+6.0f} {r['n']:>4} {r['avg']:>+5.1f} "
                  f"${r['train']:>+6.0f} ${r['test']:>+6.0f} {v}")

        if valid:
            best = valid[0]
            mc = _monte_carlo(best["trades"], data)
            print(f"\n  Monte Carlo: {best['label']}")
            print(f"    Actual: ${best['pnl']:>+.0f} | Random: ${mc['mean']:>+.0f} "
                  f"(σ=${mc['std']:.0f}) | Z: {mc['z']:+.2f}")
            if mc["z"] > 2:
                print(f"    → ✓ SIGNIFICANT")
            else:
                print(f"    → ✗ NOT SIGNIFICANT")

    return []


# ═══════════════════════════════════════════════════════════════════
# 3. NON-STANDARD CANDLE OFFSETS
# ═══════════════════════════════════════════════════════════════════

def offset_candles(features, data):
    """Test if our existing signals work better with time offsets.

    Instead of checking signals at candle boundaries (0h, 4h, 8h...),
    what if we check 1 or 2 candles later? This simulates a timing offset.
    """
    print("\n" + "=" * 70)
    print("  NON-STANDARD CANDLE OFFSETS")
    print("  Does shifting timing by 4-8h improve signals?")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # Build offset features: for each feature row, shift entry by N candles
    # This means: compute signal at candle i, but enter at candle i+N+1 instead of i+1
    from analysis.backtest_genetic import Rule, Strategy

    strategies = {
        "S1 btc_rip": Strategy([Rule("btc_30d", ">", 2000, 1)], hold=18),
        "S2 alt_crash": Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=18),
        "S4 vol_short": Strategy([Rule("vol_ratio", "<", 1.0, -1),
                                   Rule("range_pct", "<", 200, -1)], hold=18),
    }

    for sname, strat in strategies.items():
        print(f"\n  {sname}:")
        for offset in [0, 1, 2, 3, 6]:
            # Backtest with entry delay
            trades = _backtest_offset(strat, features, data, entry_offset=offset)
            if len(trades) < 10:
                continue
            pnl = sum(t["pnl"] for t in trades)
            avg = float(np.mean([t["net"] for t in trades]))
            train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
            test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)
            marker = "◄" if offset == 0 else ("✓" if pnl > 0 else "")
            valid = "✓" if train_pnl > 0 and test_pnl > 0 else ""
            print(f"    offset={offset} ({offset*4}h): ${pnl:>+7.0f} ({len(trades):>3}t, "
                  f"avg={avg:>+5.1f}bp) trn=${train_pnl:>+.0f} tst=${test_pnl:>+.0f} {marker}")


def _backtest_offset(strat, features, data, entry_offset=0, hold=18):
    """Backtest with entry delayed by entry_offset candles."""
    coins = [c for c in TOKENS if c in features and c in data]
    events = []
    for coin in coins:
        for f in features[coin]:
            events.append((f["t"], coin, f))
    events.sort(key=lambda x: x[0])

    by_ts = defaultdict(list)
    for t, coin, f in events:
        by_ts[t].append((coin, f))

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted(by_ts.keys()):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in data:
                continue
            candles = data[coin]
            for ci in range(pos["idx"], min(pos["idx"] + hold + 2, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - pos["idx"]
                    if held >= hold:
                        current = candles[ci]["c"]
                        if current > 0:
                            gross = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                            net = gross - COST_BPS
                            trades.append({"pnl": POSITION_SIZE * net / 1e4,
                                          "net": net, "dir": pos["dir"],
                                          "coin": coin, "entry_t": pos.get("entry_t", ts)})
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000
                    break

        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)

        for coin, f in by_ts[ts]:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            direction = strat.signal(f)
            if direction is None:
                continue
            if direction == 1 and n_long >= MAX_SAME_DIR:
                continue
            if direction == -1 and n_short >= MAX_SAME_DIR:
                continue

            idx = f["_idx"]
            entry_idx = idx + 1 + entry_offset  # shifted entry
            if entry_idx >= len(data[coin]):
                continue
            entry = data[coin][entry_idx]["o"]
            if entry <= 0:
                continue
            if len(positions) >= MAX_POSITIONS:
                continue

            positions[coin] = {"dir": direction, "entry": entry,
                              "idx": entry_idx, "entry_t": data[coin][entry_idx]["t"]}
            if direction == 1:
                n_long += 1
            else:
                n_short += 1

    return trades


# ═══════════════════════════════════════════════════════════════════
# Monte Carlo helper
# ═══════════════════════════════════════════════════════════════════

def _monte_carlo(trades, data, n_sims=500):
    actual_pnl = sum(t["pnl"] for t in trades)
    by_coin = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        by_coin[t["coin"]]["long" if t["dir"] == 1 else "short"] += 1

    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        for coin, counts in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            nc = len(candles)
            available = list(range(50, nc - 20))
            needed = counts["long"] + counts["short"]
            if len(available) < needed:
                continue
            sampled = random.sample(available, needed)
            for j, idx in enumerate(sampled):
                d = 1 if j < counts["long"] else -1
                entry = candles[min(idx+1, nc-1)]["o"]
                exit_p = candles[min(idx+19, nc-1)]["c"]
                if entry <= 0:
                    continue
                gross = d * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim)

    mean = float(np.mean(sim_pnls))
    std = float(np.std(sim_pnls))
    z = (actual_pnl - mean) / std if std > 0 else 0
    return {"mean": mean, "std": std, "z": z}


# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  EXPLORATION 3 — Three novel ideas")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # 1. Liquidation cascades
    liq_results = liquidation_cascades(features, data)

    # 2. Macro signals
    macro_signals(features, data)

    # 3. Offset candles
    offset_candles(features, data)

    # Summary
    print(f"\n\n{'█'*70}")
    print(f"  EXPLORATION 3 — SUMMARY")
    print(f"{'█'*70}")


if __name__ == "__main__":
    main()
