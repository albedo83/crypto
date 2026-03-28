"""Pairs Trading Backtest — Market-neutral intra-sector mean reversion.

Concept: within a sector, long the laggard + short the leader (equal $ size).
Bet on convergence. Market-neutral = direction of the market doesn't matter.

Note: S5 (follow divergence, single-leg) works. Fading single-leg doesn't.
But pairs trading is hedged — the market component cancels out.
This might work even though single-leg fading doesn't.

Sweeps:
  - Divergence threshold: 500, 750, 1000, 1500, 2000 bps
  - Lookback: 42 (7d), 84 (14d), 180 (30d) candles
  - Hold: 6 (24h), 12 (48h), 18 (72h), 24 (96h), 36 (6d) candles
  - Sectors: L1, DeFi, Infra, Meme (Gaming too small)

Validation: train/test split + Monte Carlo z-score > 2.0

Usage:
    python3 -m analysis.backtest_pairs
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)

SECTORS = {
    "L1":     ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":   ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO"],
    "Infra":  ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":   ["DOGE", "WLD", "BLUR", "MINA"],
}

TOKEN_SECTOR = {}
for _s, _ts in SECTORS.items():
    for _t in _ts:
        TOKEN_SECTOR[_t] = _s

# Pairs cost = 2 legs × entry + exit = 4 × 12 bps (but we pay cost on each leg)
PAIR_COST_BPS = COST_BPS  # cost per leg per side
PAIR_SIZE = 250.0          # $ per leg (so $500 total notional per pair)
MAX_PAIRS = 4              # max concurrent pairs
STOP_LOSS_PAIR_BPS = -2000.0  # stop on the spread, not individual legs


def compute_pair_signals(features, data, lookback_key="ret_42h"):
    """Find intra-sector pair divergences at each timestamp.

    Returns dict: {timestamp: [(long_coin, short_coin, spread_bps, sector), ...]}
    """
    coins = [c for c in TOKENS if c in features and c in data]

    # Build {timestamp: {coin: features}} lookup
    by_ts = defaultdict(dict)
    for coin in coins:
        for f in features[coin]:
            by_ts[f["t"]][coin] = f

    pair_signals = {}

    for ts in sorted(by_ts.keys()):
        available = by_ts[ts]

        # Group by sector
        sector_coins = defaultdict(list)
        for coin, f in available.items():
            sector = TOKEN_SECTOR.get(coin)
            if sector and lookback_key in f:
                sector_coins[sector].append((coin, f[lookback_key], f.get("_idx", 0)))

        pairs = []
        for sector, coins_data in sector_coins.items():
            if len(coins_data) < 3:  # need at least 3 tokens to form meaningful pairs
                continue

            # Sort by return: best performer first
            coins_data.sort(key=lambda x: x[1], reverse=True)

            # Pair: short the leader, long the laggard
            leader_coin, leader_ret, leader_idx = coins_data[0]
            laggard_coin, laggard_ret, laggard_idx = coins_data[-1]
            spread = leader_ret - laggard_ret  # positive = divergence exists

            pairs.append({
                "long": laggard_coin,
                "short": leader_coin,
                "spread_bps": spread,
                "sector": sector,
                "long_ret": laggard_ret,
                "short_ret": leader_ret,
                "long_idx": laggard_idx,
                "short_idx": leader_idx,
            })

        if pairs:
            pair_signals[ts] = pairs

    return pair_signals


def backtest_pairs(pair_signals, data, config):
    """Backtest pairs trading strategy.

    config:
        div_threshold: min spread to enter (bps)
        hold: hold period in 4h candles
        period: "train", "test", "all"
    """
    div_thresh = config.get("div_threshold", 1000)
    hold = config.get("hold", 18)
    period = config.get("period", "all")
    max_pairs = config.get("max_pairs", MAX_PAIRS)
    size = config.get("size", PAIR_SIZE)
    cost = config.get("cost", PAIR_COST_BPS)
    stop = config.get("stop", STOP_LOSS_PAIR_BPS)

    positions = {}  # key → {long_coin, short_coin, long_entry, short_entry, entry_ts, entry_idx}
    trades = []
    cooldown = {}  # sector → earliest re-entry timestamp

    for ts in sorted(pair_signals.keys()):
        if period == "train" and ts >= TRAIN_END:
            continue
        if period == "test" and ts < TEST_START:
            continue

        # ── Check exits ────────────────────────────────────
        for key in list(positions.keys()):
            pos = positions[key]
            lc, sc = pos["long_coin"], pos["short_coin"]
            if lc not in data or sc not in data:
                continue

            l_candles = data[lc]
            s_candles = data[sc]

            # Find current candle index
            l_idx = pos["long_idx"]
            s_idx = pos["short_idx"]

            # Walk forward to find current timestamp
            l_now_idx = None
            for ci in range(l_idx, min(l_idx + hold + 10, len(l_candles))):
                if l_candles[ci]["t"] == ts:
                    l_now_idx = ci
                    break
            s_now_idx = None
            for ci in range(s_idx, min(s_idx + hold + 10, len(s_candles))):
                if s_candles[ci]["t"] == ts:
                    s_now_idx = ci
                    break

            if l_now_idx is None or s_now_idx is None:
                continue

            # Compute spread P&L
            l_price = l_candles[l_now_idx]["c"]
            s_price = s_candles[s_now_idx]["c"]
            long_bps = (l_price / pos["long_entry"] - 1) * 1e4
            short_bps = -(s_price / pos["short_entry"] - 1) * 1e4
            spread_pnl = long_bps + short_bps  # combined spread return

            held = l_now_idx - l_idx
            exit_reason = None

            if held >= hold:
                exit_reason = "timeout"
            elif spread_pnl < stop:
                exit_reason = "stop"

            if exit_reason:
                # Cost: 2 legs × entry + exit = 4 transactions, but COST_BPS is round-trip per leg
                net_bps = spread_pnl - 2 * cost  # cost on each leg
                pnl = size * net_bps / 1e4  # P&L on one leg's notional

                trades.append({
                    "long": lc, "short": sc, "sector": pos["sector"],
                    "entry_spread": pos["entry_spread"],
                    "hold": held,
                    "long_bps": round(long_bps, 1),
                    "short_bps": round(short_bps, 1),
                    "spread_bps": round(spread_pnl, 1),
                    "net_bps": round(net_bps, 1),
                    "pnl": round(pnl, 2),
                    "reason": exit_reason,
                    "entry_t": pos["entry_ts"],
                    "exit_t": ts,
                })
                del positions[key]
                cooldown[pos["sector"]] = ts + 6 * 3600 * 1000  # 24h cooldown on sector

        # ── Check entries ──────────────────────────────────
        if len(positions) >= max_pairs:
            continue

        candidates = pair_signals.get(ts, [])
        # Sort by spread (widest first)
        candidates.sort(key=lambda x: x["spread_bps"], reverse=True)

        for pair in candidates:
            if len(positions) >= max_pairs:
                break

            if pair["spread_bps"] < div_thresh:
                continue

            sector = pair["sector"]

            # Cooldown check
            if sector in cooldown and ts < cooldown[sector]:
                continue

            # Don't double up on same sector
            if any(p["sector"] == sector for p in positions.values()):
                continue

            lc, sc = pair["long"], pair["short"]

            # Don't enter if either coin already in a pair
            active_coins = set()
            for p in positions.values():
                active_coins.add(p["long_coin"])
                active_coins.add(p["short_coin"])
            if lc in active_coins or sc in active_coins:
                continue

            # Entry at next candle open
            l_idx = pair["long_idx"]
            s_idx = pair["short_idx"]
            if l_idx + 1 >= len(data[lc]) or s_idx + 1 >= len(data[sc]):
                continue

            l_entry = data[lc][l_idx + 1]["o"]
            s_entry = data[sc][s_idx + 1]["o"]
            if l_entry <= 0 or s_entry <= 0:
                continue

            key = f"{lc}_{sc}_{ts}"
            positions[key] = {
                "long_coin": lc, "short_coin": sc,
                "long_entry": l_entry, "short_entry": s_entry,
                "long_idx": l_idx + 1, "short_idx": s_idx + 1,
                "entry_ts": ts, "sector": sector,
                "entry_spread": pair["spread_bps"],
            }

        # Force-close remaining at end of data
    for key in list(positions.keys()):
        pos = positions[key]
        lc, sc = pos["long_coin"], pos["short_coin"]
        l_candles, s_candles = data[lc], data[sc]
        l_price = l_candles[-1]["c"]
        s_price = s_candles[-1]["c"]
        long_bps = (l_price / pos["long_entry"] - 1) * 1e4
        short_bps = -(s_price / pos["short_entry"] - 1) * 1e4
        spread_pnl = long_bps + short_bps
        net_bps = spread_pnl - 2 * cost
        pnl = size * net_bps / 1e4
        trades.append({
            "long": lc, "short": sc, "sector": pos["sector"],
            "entry_spread": pos["entry_spread"],
            "hold": len(l_candles) - pos["long_idx"],
            "long_bps": round(long_bps, 1), "short_bps": round(short_bps, 1),
            "spread_bps": round(spread_pnl, 1), "net_bps": round(net_bps, 1),
            "pnl": round(pnl, 2), "reason": "end_of_data",
            "entry_t": pos["entry_ts"], "exit_t": l_candles[-1]["t"],
        })
        del positions[key]

    return trades


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0, "monthly": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net_bps"] for t in trades]))
    wins = sum(1 for t in trades if t["net_bps"] > 0)
    t_min = min(t["entry_t"] for t in trades)
    t_max = max(t["exit_t"] for t in trades)
    months = max(1, (t_max - t_min) / (30.44 * 86400 * 1000))
    return {
        "n": n,
        "pnl": round(pnl, 2),
        "avg": round(avg, 1),
        "win": round(wins / n * 100, 0),
        "monthly": round(pnl / months, 1),
    }


def monte_carlo(pair_signals, data, config, n_sims=500):
    """Monte Carlo: same pairs, randomized timing."""
    real_trades = backtest_pairs(pair_signals, data, {**config, "period": "all"})
    if len(real_trades) < 5:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)

    # Extract trade signatures: (sector, hold, direction doesn't change — always long laggard / short leader)
    hold = config.get("hold", 18)

    all_timestamps = sorted(pair_signals.keys())
    if len(all_timestamps) < hold + 10:
        return None

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        n_trades = len(real_trades)
        # Randomly sample timestamps and sectors
        sampled_ts = random.sample(all_timestamps[:len(all_timestamps) - hold - 2], min(n_trades * 3, len(all_timestamps) - hold - 2))

        sim_count = 0
        for ts in sampled_ts:
            if sim_count >= n_trades:
                break
            pairs = pair_signals.get(ts, [])
            if not pairs:
                continue
            pair = random.choice(pairs)
            lc, sc = pair["long"], pair["short"]
            l_idx, s_idx = pair["long_idx"], pair["short_idx"]

            if l_idx + 1 + hold >= len(data[lc]) or s_idx + 1 + hold >= len(data[sc]):
                continue

            l_entry = data[lc][l_idx + 1]["o"]
            s_entry = data[sc][s_idx + 1]["o"]
            if l_entry <= 0 or s_entry <= 0:
                continue

            l_exit = data[lc][l_idx + 1 + hold]["c"]
            s_exit = data[sc][s_idx + 1 + hold]["c"]

            long_bps = (l_exit / l_entry - 1) * 1e4
            short_bps = -(s_exit / s_entry - 1) * 1e4
            spread = long_bps + short_bps
            net = spread - 2 * COST_BPS
            pnl = PAIR_SIZE * net / 1e4

            sim_total += pnl
            sim_count += 1

        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls))
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0

    return {
        "real_pnl": round(real_pnl, 2),
        "sim_mean": round(sim_mean, 2),
        "sim_std": round(sim_std, 2),
        "z": round(z, 2),
        "p_value": round(sum(1 for s in sim_pnls if s >= real_pnl) / n_sims, 3),
    }


def main():
    print("=" * 70)
    print("PAIRS TRADING BACKTEST — Intra-sector mean reversion")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"  {len(data)} tokens loaded")

    print("Building features...")
    features = build_features(data)
    print(f"  {sum(len(v) for v in features.values())} feature rows")

    # ── Lookback sweep ─────────────────────────────────────────
    lookback_keys = [
        ("ret_42h", "7d"),
        ("ret_84h", "14d"),
        ("ret_180h", "30d"),
    ]

    div_thresholds = [500, 750, 1000, 1500, 2000]
    holds = [6, 12, 18, 24, 36]  # 24h, 48h, 72h, 96h, 6d

    results = []

    for lb_key, lb_label in lookback_keys:
        print(f"\n{'─' * 60}")
        print(f"Lookback: {lb_label} ({lb_key})")
        print(f"{'─' * 60}")

        pair_signals = compute_pair_signals(features, data, lookback_key=lb_key)
        print(f"  {len(pair_signals)} timestamps with pair signals")

        for div in div_thresholds:
            for hold in holds:
                hold_label = f"{hold * 4}h"

                for period in ["train", "test"]:
                    cfg = {
                        "div_threshold": div,
                        "hold": hold,
                        "period": period,
                    }
                    trades = backtest_pairs(pair_signals, data, cfg)
                    s = score(trades)

                    results.append({
                        "lookback": lb_label,
                        "div": div,
                        "hold": hold_label,
                        "period": period,
                        **s,
                    })

        # Print best for this lookback
        train_results = [r for r in results if r["lookback"] == lb_label and r["period"] == "train" and r["n"] >= 10]
        if train_results:
            train_results.sort(key=lambda r: r["pnl"], reverse=True)
            print(f"\n  Top 5 (train):")
            print(f"  {'Div':>6} {'Hold':>6} {'N':>4} {'P&L':>8} {'Avg':>6} {'Win%':>5} {'$/mo':>6}")
            for r in train_results[:5]:
                print(f"  {r['div']:>6} {r['hold']:>6} {r['n']:>4} ${r['pnl']:>7.0f} {r['avg']:>+6.1f} {r['win']:>4.0f}% ${r['monthly']:>5.0f}")

            # Check corresponding test performance
            for r in train_results[:5]:
                test_match = [t for t in results if t["lookback"] == lb_label
                              and t["div"] == r["div"] and t["hold"] == r["hold"]
                              and t["period"] == "test"]
                if test_match:
                    t = test_match[0]
                    flag = "✓" if t["pnl"] > 0 and t["avg"] > 0 else "✗"
                    print(f"    → test: {flag} n={t['n']} P&L=${t['pnl']:.0f} avg={t['avg']:+.1f}bps win={t['win']:.0f}%")

    # ── Find strategies that pass train AND test ───────────────
    print(f"\n{'=' * 70}")
    print("STRATEGIES PASSING TRAIN + TEST")
    print(f"{'=' * 70}")

    passing = []
    for lb_key, lb_label in lookback_keys:
        for div in div_thresholds:
            for hold_c in holds:
                hold_label = f"{hold_c * 4}h"
                train = [r for r in results if r["lookback"] == lb_label
                         and r["div"] == div and r["hold"] == hold_label
                         and r["period"] == "train"]
                test = [r for r in results if r["lookback"] == lb_label
                        and r["div"] == div and r["hold"] == hold_label
                        and r["period"] == "test"]
                if train and test:
                    tr, te = train[0], test[0]
                    if tr["n"] >= 10 and te["n"] >= 5 and tr["avg"] > 0 and te["avg"] > 0:
                        passing.append({
                            "lb": lb_label, "div": div, "hold": hold_label,
                            "train": tr, "test": te,
                            "total_pnl": tr["pnl"] + te["pnl"],
                            "total_avg": round((tr["avg"] * tr["n"] + te["avg"] * te["n"]) / (tr["n"] + te["n"]), 1),
                        })

    if not passing:
        print("\n  No strategy passes train + test. Pairs trading doesn't work here.")
    else:
        passing.sort(key=lambda x: x["total_pnl"], reverse=True)
        print(f"\n  {'LB':>4} {'Div':>6} {'Hold':>6} | {'Train':>8} {'TrAvg':>6} {'TrN':>4} | {'Test':>8} {'TeAvg':>6} {'TeN':>4} | {'Total':>8}")
        for p in passing[:15]:
            tr, te = p["train"], p["test"]
            print(f"  {p['lb']:>4} {p['div']:>6} {p['hold']:>6} | ${tr['pnl']:>7.0f} {tr['avg']:>+6.1f} {tr['n']:>4} | ${te['pnl']:>7.0f} {te['avg']:>+6.1f} {te['n']:>4} | ${p['total_pnl']:>7.0f}")

        # Monte Carlo on best passing strategies
        print(f"\n{'─' * 60}")
        print("MONTE CARLO VALIDATION (top 5 passing)")
        print(f"{'─' * 60}")

        for p in passing[:5]:
            lb_key = {"7d": "ret_42h", "14d": "ret_84h", "30d": "ret_180h"}[p["lb"]]
            hold_candles = int(p["hold"].replace("h", "")) // 4

            pair_signals = compute_pair_signals(features, data, lookback_key=lb_key)
            cfg = {"div_threshold": p["div"], "hold": hold_candles}
            mc = monte_carlo(pair_signals, data, cfg)

            if mc:
                flag = "✓ PASS" if mc["z"] >= 2.0 else "✗ FAIL"
                print(f"  {p['lb']:>4} div={p['div']} hold={p['hold']}: z={mc['z']:.2f} {flag}")
                print(f"    real=${mc['real_pnl']:.0f} vs random=${mc['sim_mean']:.0f}±{mc['sim_std']:.0f} (p={mc['p_value']:.3f})")
            else:
                print(f"  {p['lb']:>4} div={p['div']} hold={p['hold']}: not enough data for MC")

    # ── Per-sector analysis ────────────────────────────────────
    if passing:
        best = passing[0]
        lb_key = {"7d": "ret_42h", "14d": "ret_84h", "30d": "ret_180h"}[best["lb"]]
        hold_candles = int(best["hold"].replace("h", "")) // 4
        pair_signals = compute_pair_signals(features, data, lookback_key=lb_key)
        all_trades = backtest_pairs(pair_signals, data, {
            "div_threshold": best["div"], "hold": hold_candles, "period": "all"
        })

        print(f"\n{'─' * 60}")
        print(f"BEST STRATEGY BREAKDOWN: {best['lb']} div={best['div']} hold={best['hold']}")
        print(f"{'─' * 60}")

        by_sector = defaultdict(list)
        for t in all_trades:
            by_sector[t["sector"]].append(t)

        print(f"\n  {'Sector':>8} {'N':>4} {'P&L':>8} {'Avg':>6} {'Win%':>5}")
        for sector in sorted(by_sector.keys()):
            st = by_sector[sector]
            s = score(st)
            print(f"  {sector:>8} {s['n']:>4} ${s['pnl']:>7.0f} {s['avg']:>+6.1f} {s['win']:>4.0f}%")

        # Show some example trades
        print(f"\n  Last 10 trades:")
        print(f"  {'Long':>6} {'Short':>6} {'Sector':>8} {'Spread':>7} {'L_bps':>7} {'S_bps':>7} {'Net':>7} {'P&L':>7} {'Reason':>8}")
        for t in all_trades[-10:]:
            print(f"  {t['long']:>6} {t['short']:>6} {t['sector']:>8} {t['entry_spread']:>+7.0f} {t['long_bps']:>+7.1f} {t['short_bps']:>+7.1f} {t['net_bps']:>+7.1f} ${t['pnl']:>6.2f} {t['reason']:>8}")

    print("\nDone.")


if __name__ == "__main__":
    main()
