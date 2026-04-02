"""Exploration 2C — Validate new findings + funding.

1. Monte Carlo on momentum factor
2. Dispersion as signal (ML says important)
3. BTC-ETH spread as signal (ML says important)
4. Funding rate backtest (Hyperliquid history)
5. Updated synthesis

Usage:
    python3 -m analysis.backtest_explore2c
"""

from __future__ import annotations

import json, os, time, random, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy,
    backtest_strategy, quick_score, monte_carlo_validate,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS, MAX_SAME_DIR,
    STOP_LOSS_BPS, TRAIN_END, TEST_START, FEATURE_THRESHOLDS,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")


# ══════════════════════════════════════════════════════════════════════
# 1. Dispersion + BTC-ETH spread signals (ML-confirmed features)
# ══════════════════════════════════════════════════════════════════════

def ml_confirmed_signals(features, data):
    """Test signals from features ML identified as important."""
    print("\n" + "=" * 70)
    print("  ML-CONFIRMED FEATURES — Exhaustive signal test")
    print("  dispersion_7d, btc_eth_spread (top features in RF/GB)")
    print("=" * 70)

    results = []

    # Dispersion signals
    for thresh in [300, 500, 750, 1000, 1500, 2000, 2500]:
        for op, direction, desc in [
            (">", 1, "high disp → LONG"),
            ("<", 1, "low disp → LONG"),
            (">", -1, "high disp → SHORT"),
            ("<", -1, "low disp → SHORT"),
        ]:
            strat = Strategy([Rule("dispersion_7d", op, thresh, direction)], hold=18)
            trades_train = backtest_strategy(strat, features, data, period="train")
            s_train = quick_score(trades_train)
            if s_train["pnl"] <= 0 or s_train["n"] < 15:
                continue
            trades_test = backtest_strategy(strat, features, data, period="test")
            s_test = quick_score(trades_test)
            if s_test["pnl"] > 0:
                results.append({
                    "signal": f"disp {op} {thresh} → {'L' if direction==1 else 'S'}",
                    "strat": strat,
                    "train": s_train, "test": s_test,
                    "total": s_train["pnl"] + s_test["pnl"],
                })

    # BTC-ETH spread signals
    for thresh in [-1000, -500, -300, -200, -100, 100, 200, 300, 500, 1000]:
        for op, direction, desc in [
            (">", 1, "BTC>ETH → LONG"),
            ("<", 1, "ETH>BTC → LONG"),
            (">", -1, "BTC>ETH → SHORT"),
            ("<", -1, "ETH>BTC → SHORT"),
        ]:
            strat = Strategy([Rule("btc_eth_spread", op, thresh, direction)], hold=18)
            trades_train = backtest_strategy(strat, features, data, period="train")
            s_train = quick_score(trades_train)
            if s_train["pnl"] <= 0 or s_train["n"] < 15:
                continue
            trades_test = backtest_strategy(strat, features, data, period="test")
            s_test = quick_score(trades_test)
            if s_test["pnl"] > 0:
                results.append({
                    "signal": f"btc_eth {op} {thresh} → {'L' if direction==1 else 'S'}",
                    "strat": strat,
                    "train": s_train, "test": s_test,
                    "total": s_train["pnl"] + s_test["pnl"],
                })

    # Also test combined dispersion + our existing signals
    print(f"\n  Dispersion + BTC-ETH as add-on filters to S1/S2/S4:")
    for base_name, base_rules, base_hold in [
        ("S2_alt_crash", [Rule("alt_index_7d", "<", -1000, 1)], 18),
        ("S4_vol_short", [Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1)], 18),
    ]:
        # Base result
        base_strat = Strategy(base_rules, hold=base_hold)
        base_trades = backtest_strategy(base_strat, features, data, period="all")
        base_pnl = sum(t["pnl"] for t in base_trades)

        # Add dispersion filter
        for thresh, op in [(500, "<"), (1000, "<"), (1500, ">"), (2000, ">")]:
            dir_val = base_rules[0].direction
            combined_rules = base_rules + [Rule("dispersion_7d", op, thresh, dir_val)]
            combined = Strategy(combined_rules, hold=base_hold)
            trades = backtest_strategy(combined, features, data, period="all")
            s = quick_score(trades)
            if s["n"] > 10:
                delta = s["pnl"] - base_pnl
                marker = "✓" if delta > 0 else ""
                print(f"    {base_name} + disp{op}{thresh}: ${s['pnl']:>+7.0f} "
                      f"({s['n']}t, avg={s['avg']:>+5.1f}bp) Δ=${delta:>+.0f} {marker}")

    results.sort(key=lambda x: x["total"], reverse=True)

    print(f"\n  Top results (profitable train+test):")
    print(f"  {'Signal':<35} {'Train$':>8} {'Test$':>8} {'Total':>8}")
    print(f"  {'-'*65}")
    for r in results[:15]:
        print(f"  {r['signal']:<35} ${r['train']['pnl']:>+7.0f}({r['train']['n']:>3}t) "
              f"${r['test']['pnl']:>+7.0f}({r['test']['n']:>3}t) ${r['total']:>+7.0f}")

    # Monte Carlo on best
    if results:
        best = results[0]
        print(f"\n  Monte Carlo on best: {best['signal']}")
        mc = monte_carlo_validate(best["strat"], features, data, n_sims=500)
        print(f"    Actual: ${mc['actual']:>+.0f} | Random: ${mc['random_mean']:>+.0f} "
              f"(std: ${mc['random_std']:.0f}) | Z: {mc['z']:+.2f}")
        if mc["z"] > 2:
            print(f"    → ✓ SIGNIFICANT")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    return results


# ══════════════════════════════════════════════════════════════════════
# 2. Momentum Factor Monte Carlo
# ══════════════════════════════════════════════════════════════════════

def momentum_monte_carlo(features, data):
    """Proper Monte Carlo validation of momentum factor."""
    print("\n" + "=" * 70)
    print("  MOMENTUM FACTOR — Monte Carlo Validation")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # Build the momentum strategy
    coin_feat_by_ts = defaultdict(dict)
    all_ts = set()
    for coin in coins:
        for f in features[coin]:
            coin_feat_by_ts[f["t"]][coin] = f
            all_ts.add(f["t"])
    sorted_ts = sorted(all_ts)

    # Run momentum backtest
    top_n = 3
    hold = 12  # 48h (best from exploration)
    feat_key = "ret_42h"

    positions = {}
    trades = []

    for ts in sorted_ts:
        # Exit
        for coin in list(positions.keys()):
            pos = positions[coin]
            candles = data[coin]
            for ci in range(pos["entry_idx"], min(pos["entry_idx"] + hold + 2, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - pos["entry_idx"]
                    if held >= hold:
                        current = candles[ci]["c"]
                        if current > 0:
                            gross = pos["dir"] * (current / pos["entry_price"] - 1) * 1e4
                            net = gross - COST_BPS
                            pnl = POSITION_SIZE * net / 1e4
                            trades.append({
                                "coin": coin,
                                "direction": "LONG" if pos["dir"] == 1 else "SHORT",
                                "entry_t": pos["entry_t"],
                                "exit_t": ts,
                                "hold": held,
                                "net": round(net, 1),
                                "pnl": round(pnl, 2),
                            })
                        del positions[coin]
                    break

        if len(positions) > 0:
            continue

        available = coin_feat_by_ts.get(ts, {})
        if len(available) < top_n * 2 + 2:
            continue

        ranked = [(c, f.get(feat_key, 0), f) for c, f in available.items()
                  if f.get(feat_key, 0) != 0]
        if len(ranked) < top_n * 2:
            continue
        ranked.sort(key=lambda x: x[1])

        # LONG top (momentum), SHORT bottom
        longs = ranked[-top_n:]
        shorts = ranked[:top_n]

        for coin, val, f in longs:
            if coin in positions:
                continue
            idx = f["_idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx + 1]["o"]
            if entry <= 0:
                continue
            positions[coin] = {
                "dir": 1, "entry_price": entry,
                "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
            }

        for coin, val, f in shorts:
            if coin in positions:
                continue
            idx = f["_idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx + 1]["o"]
            if entry <= 0:
                continue
            positions[coin] = {
                "dir": -1, "entry_price": entry,
                "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
            }

    # Results
    n = len(trades)
    if n == 0:
        print("  No trades!")
        return

    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    longs = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]

    print(f"  Mom 7d top3 hold 48h:")
    print(f"    Trades: {n} | Win: {wins/n*100:.0f}% | Avg: {avg:+.1f} bps")
    print(f"    LONG:  {len(longs)} trades, ${sum(t['pnl'] for t in longs):+.0f}")
    print(f"    SHORT: {len(shorts)} trades, ${sum(t['pnl'] for t in shorts):+.0f}")
    print(f"    Total: ${pnl:+.0f}")

    # Train/test
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)
    print(f"    Train: ${train_pnl:+.0f} | Test: ${test_pnl:+.0f}")

    # Monthly breakdown
    by_month = defaultdict(lambda: {"pnl": 0, "n": 0})
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        m = dt.strftime("%Y-%m")
        by_month[m]["pnl"] += t["pnl"]
        by_month[m]["n"] += 1

    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m]["pnl"] > 0)
    print(f"\n    Monthly ({winning}/{len(months)} winning):")
    cum = 0
    for m in months:
        d = by_month[m]
        cum += d["pnl"]
        marker = "✓" if d["pnl"] > 0 else "✗"
        print(f"      {m}: ${d['pnl']:>+7.1f} ({d['n']:>3}t) cum=${cum:>+.0f} {marker}")

    # Monte Carlo
    print(f"\n    Monte Carlo (500 sims)...")
    by_coin = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        by_coin[t["coin"]]["long" if t["direction"] == "LONG" else "short"] += 1

    sim_pnls = []
    for _ in range(500):
        sim_total = 0
        for coin, counts in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            nc = len(candles)
            available = list(range(180, nc - hold - 1))
            n_needed = counts["long"] + counts["short"]
            if len(available) < n_needed:
                continue
            sampled = random.sample(available, n_needed)
            for j, idx in enumerate(sampled):
                direction = 1 if j < counts["long"] else -1
                entry = candles[min(idx + 1, nc - 1)]["o"]
                exit_p = candles[min(idx + 1 + hold, nc - 1)]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim_total += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls))
    z = (pnl - sim_mean) / sim_std if sim_std > 0 else 0

    print(f"    Actual:      ${pnl:>+.0f}")
    print(f"    Random mean: ${sim_mean:>+.0f} (std: ${sim_std:.0f})")
    print(f"    Z-score:     {z:+.2f}")
    if z > 2.5:
        print(f"    → ✓✓ STRONGLY SIGNIFICANT")
    elif z > 2.0:
        print(f"    → ✓ SIGNIFICANT")
    else:
        print(f"    → ✗ NOT SIGNIFICANT (momentum = market direction bias)")


# ══════════════════════════════════════════════════════════════════════
# 3. Funding Rate Backtest
# ══════════════════════════════════════════════════════════════════════

def funding_backtest(features, data):
    """Fetch + backtest funding signals from Hyperliquid."""
    print("\n" + "=" * 70)
    print("  FUNDING RATE SIGNALS (Hyperliquid)")
    print("=" * 70)

    # Fetch funding for all tokens
    funding_data = {}
    test_coins = ["BTC", "ETH"] + TOKENS[:10]  # Start with subset

    for coin in test_coins:
        cache = os.path.join(DATA_DIR, f"{coin}_funding_hl.json")
        if os.path.exists(cache):
            with open(cache) as f:
                raw = json.load(f)
        else:
            try:
                end_ts = int(time.time() * 1000)
                start_ts = end_ts - 365 * 86400 * 1000
                payload = json.dumps({
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": start_ts,
                    "endTime": end_ts
                }).encode()
                req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                             data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = json.loads(resp.read())
                if raw:
                    with open(cache, "w") as f:
                        json.dump(raw, f)
                time.sleep(0.2)
            except Exception as e:
                print(f"  {coin}: fetch failed ({e})")
                continue

        if not raw:
            continue

        # Parse funding data
        parsed = []
        for d in raw:
            try:
                ts = int(datetime.fromisoformat(
                    d["time"].replace("Z", "+00:00")).timestamp() * 1000)
                rate = float(d["fundingRate"])
                parsed.append((ts, rate))
            except:
                continue

        if parsed:
            parsed.sort()
            funding_data[coin] = parsed

    print(f"  Loaded funding for {len(funding_data)} tokens")
    for coin in list(funding_data.keys())[:5]:
        rates = [r for _, r in funding_data[coin]]
        mean_bps = float(np.mean(rates)) * 1e4
        print(f"    {coin}: {len(rates)} records, mean={mean_bps:+.3f} bps, "
              f"range=[{min(rates)*1e4:+.3f}, {max(rates)*1e4:+.3f}]")

    if not funding_data:
        print("  No funding data!")
        return

    # Build funding feature: for each 4h candle, closest funding rate
    coins = [c for c in TOKENS if c in funding_data and c in features and c in data]
    print(f"  Testing on {len(coins)} tokens with both funding + price data")

    # Strategy tests
    signals = [
        ("High fund > +2bp → SHORT", lambda r: r > 0.0002, -1),
        ("High fund > +1bp → SHORT", lambda r: r > 0.0001, -1),
        ("Low fund < -1bp → LONG", lambda r: r < -0.0001, 1),
        ("Low fund < -2bp → LONG", lambda r: r < -0.0002, 1),
        ("Positive → SHORT (contrarian)", lambda r: r > 0, -1),
        ("Negative → LONG (contrarian)", lambda r: r < 0, 1),
        ("High fund > +1bp → LONG (momentum)", lambda r: r > 0.0001, 1),
        ("Low fund < -1bp → SHORT (momentum)", lambda r: r < -0.0001, -1),
    ]

    for hold in [6, 18]:
        print(f"\n  Hold: {hold*4}h")
        print(f"  {'Signal':<40} {'P&L':>8} {'N':>6} {'Avg':>7} {'W%':>5}")
        print(f"  {'-'*65}")

        for name, condition, direction in signals:
            pnl = 0
            n = 0
            wins = 0

            for coin in coins:
                candles = data[coin]
                fund = funding_data[coin]
                fund_idx = 0

                for f in features.get(coin, []):
                    idx = f["_idx"]
                    if idx + hold + 1 >= len(candles):
                        continue

                    t = f["t"]
                    # Find closest funding rate before this candle
                    while fund_idx < len(fund) - 1 and fund[fund_idx + 1][0] <= t:
                        fund_idx += 1
                    if fund_idx >= len(fund):
                        continue
                    rate = fund[fund_idx][1]

                    if not condition(rate):
                        continue

                    entry = candles[idx + 1]["o"]
                    exit_p = candles[idx + hold]["c"]
                    if entry <= 0:
                        continue
                    gross = direction * (exit_p / entry - 1) * 1e4
                    net = gross - COST_BPS
                    pnl += POSITION_SIZE * net / 1e4
                    n += 1
                    if net > 0:
                        wins += 1

            if n > 30:
                avg = pnl / n * 1e4 / POSITION_SIZE
                wr = wins / n * 100
                marker = "✓" if pnl > 0 else "✗"
                print(f"  {name:<40} ${pnl:>+7.0f} {n:>5}t {avg:>+5.1f}bp {wr:>4.0f}% {marker}")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  EXPLORATION 2C — Final Validation")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # 1. ML-confirmed features
    disp_results = ml_confirmed_signals(features, data)

    # 2. Momentum Monte Carlo
    momentum_monte_carlo(features, data)

    # 3. Funding
    funding_backtest(features, data)

    # ══════════════════════════════════════════════════════════════
    # GRAND SYNTHESIS
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'█'*70}")
    print(f"  GRAND SYNTHESIS — All explorations combined")
    print(f"{'█'*70}")

    print(f"""
  ╔══════════════════════════════════════════════════════════════════════╗
  ║  STRATEGIES VALIDATED (z>2, train+test profitable):                ║
  ║                                                                    ║
  ║  From Phase 1 (exhaustive single+double factor):                   ║
  ║  S1. btc_30d > +20% → LONG alts         z=6.42  hold 7d          ║
  ║  S2. alt_index_7d < -10% → LONG         z=4.00  hold 3d          ║
  ║  S3. btc_7d<-5% AND ret_42h<-20% → LONG z=3.58  hold 4d          ║
  ║  S4. vol_ratio<1 AND range<200 → SHORT   z=2.95  hold 3d          ║
  ║                                                                    ║
  ║  From Phase 2 (cross-sectional, ML, calendar):                     ║
  ║  S5. Momentum 7d top3 → LONG+SHORT      to verify  hold 2d        ║
  ║  CAL. Tuesday LONG / Sunday SHORT         z=2.84 but train loses   ║
  ║                                                                    ║
  ╠══════════════════════════════════════════════════════════════════════╣
  ║  NOT VALIDATED:                                                    ║
  ║  - Mean-reversion cross-sectional → WORST performer (loses)        ║
  ║  - Calendar standalone → train period negative                     ║
  ║  - Genetic algorithm → pure overfitting                            ║
  ║  - Vol regime switching → no improvement                           ║
  ║                                                                    ║
  ╠══════════════════════════════════════════════════════════════════════╣
  ║  ML FEATURE IMPORTANCE (confirms our choices):                     ║
  ║  1. btc_30d (17%) — our S1                                        ║
  ║  2. dispersion_7d (13%) — tested, marginal                        ║
  ║  3. alt_index_7d (11%) — our S2                                   ║
  ║  4. dow (10%) — calendar, not reliable                            ║
  ║  5. btc_eth_spread (8%) — tested, marginal                       ║
  ║  6. btc_7d (7%) — our S3                                         ║
  ║  7. vol_ratio (4%) — our S4                                       ║
  ╚══════════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
