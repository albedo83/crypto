"""Funding Carry Backtest — Collect funding rate by shorting high-funding tokens.

Concept: On Hyperliquid, funding is paid every 8h. When funding is very positive
(longs pay shorts), short the token and collect funding. Vice versa.

The edge is structural: retail tends to be overleveraged in one direction,
creating persistent funding imbalances.

Risk: price moves against you more than funding pays. Need to size small
and diversify across multiple tokens.

Sweeps:
  - Funding threshold: 5, 10, 20, 50, 100 bps annualized
  - Hold: 8h (1 funding), 24h (3), 72h (9), 168h (21 fundings)
  - Lookback for signal: current funding vs 7d avg vs 30d avg
  - Max positions: 3, 4, 6

Validation: train/test split + Monte Carlo

Usage:
    python3 -m analysis.backtest_funding
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

TOKENS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
]

COST_BPS = 12.0
POSITION_SIZE = 250.0

TRAIN_END = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000
TEST_START = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000


def load_funding():
    """Load full funding history for all tokens."""
    funding = {}
    for coin in TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_funding_full.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        if len(raw) < 100:
            continue
        entries = []
        for r in raw:
            entries.append({
                "t": r["time"],
                "rate": float(r["fundingRate"]),   # per 8h, e.g. 0.0001 = 1 bps
                "premium": float(r.get("premium", 0)),
            })
        funding[coin] = entries
    return funding


def load_prices():
    """Load 4h candle data for price reference."""
    from analysis.backtest_genetic import load_3y_candles
    return load_3y_candles()


def align_funding_with_prices(funding, prices):
    """Create aligned dataset: for each funding event, find closest price.

    Returns {coin: [{t, rate, premium, price, price_24h_later, price_72h_later, ...}]}
    """
    aligned = {}

    for coin in funding:
        if coin not in prices:
            continue

        # Build price lookup: timestamp → close
        price_ts = [(c["t"], c["c"]) for c in prices[coin]]
        price_times = np.array([p[0] for p in price_ts])
        price_vals = np.array([p[1] for p in price_ts])

        entries = []
        for fe in funding[coin]:
            t = fe["t"]
            # Find closest price (within 4h = 14400000 ms)
            idx = np.searchsorted(price_times, t)
            if idx >= len(price_times):
                idx = len(price_times) - 1
            if idx > 0 and abs(price_times[idx - 1] - t) < abs(price_times[idx] - t):
                idx -= 1
            if abs(price_times[idx] - t) > 14400000:  # >4h gap
                continue

            entry = {
                "t": t,
                "rate": fe["rate"],
                "premium": fe["premium"],
                "price": price_vals[idx],
                "price_idx": idx,
            }

            # Future prices for P&L calculation
            for offset_name, offset_candles in [("8h", 2), ("24h", 6), ("72h", 18), ("168h", 42)]:
                future_idx = idx + offset_candles
                if future_idx < len(price_vals):
                    entry[f"price_{offset_name}"] = price_vals[future_idx]

            entries.append(entry)

        if entries:
            aligned[coin] = entries

    return aligned


def compute_funding_signals(aligned, lookback=21):
    """Compute funding signals: current rate vs rolling average.

    Returns {timestamp: [(coin, signal_strength, direction, rate, avg_rate)]}
    signal_strength = abs(rate - avg_rate) in annualized bps
    direction = -1 if rate > 0 (short to collect), +1 if rate < 0 (long to collect)
    """
    signals = defaultdict(list)

    for coin, entries in aligned.items():
        rates = [e["rate"] for e in entries]

        for i in range(lookback, len(entries)):
            e = entries[i]
            current_rate = e["rate"]

            # Rolling average
            window = rates[i - lookback:i]
            avg_rate = float(np.mean(window))

            # Annualized funding: rate per 8h × 3 × 365
            ann_bps = current_rate * 3 * 365 * 1e4  # annualized bps
            avg_ann_bps = avg_rate * 3 * 365 * 1e4

            # Signal: current funding is extreme
            # If funding very positive → short (collect from longs)
            # If funding very negative → long (collect from shorts)
            direction = -1 if current_rate > 0 else 1
            strength = abs(ann_bps)

            signals[e["t"]].append({
                "coin": coin,
                "direction": direction,
                "strength": strength,
                "rate": current_rate,
                "rate_ann_bps": round(ann_bps, 1),
                "avg_ann_bps": round(avg_ann_bps, 1),
                "excess": round(ann_bps - avg_ann_bps, 1),
                "entry": e,
            })

    return signals


def backtest_funding(signals, aligned, config):
    """Backtest funding carry strategy.

    config:
        threshold_ann_bps: min annualized funding to enter (absolute)
        hold_hours: hold period in hours (8, 24, 72, 168)
        max_positions: max concurrent positions
        mode: "absolute" (enter on abs funding) or "excess" (enter on funding vs avg)
        period: "train", "test", "all"
    """
    threshold = config.get("threshold_ann_bps", 2000)  # 20% annualized
    hold_h = config.get("hold_hours", 24)
    max_pos = config.get("max_positions", 4)
    mode = config.get("mode", "absolute")
    period = config.get("period", "all")
    cost = config.get("cost", COST_BPS)
    size = config.get("size", POSITION_SIZE)

    # Map hold_hours to price key
    hold_key = f"price_{hold_h}h"

    positions = {}  # coin → {direction, entry_price, entry_rate, entry_t}
    trades = []
    cooldown = {}

    for ts in sorted(signals.keys()):
        if period == "train" and ts >= TRAIN_END:
            continue
        if period == "test" and ts < TEST_START:
            continue

        # ── Exits: check if hold expired ──────────────
        for coin in list(positions.keys()):
            pos = positions[coin]
            elapsed_h = (ts - pos["entry_t"]) / 3600000
            if elapsed_h >= hold_h:
                # Find exit price
                entry_data = pos["entry_data"]
                exit_price = entry_data.get(hold_key)
                if exit_price is None or exit_price <= 0:
                    # Fallback: find price at current timestamp
                    coin_entries = aligned.get(coin, [])
                    exit_price = None
                    for ce in coin_entries:
                        if ce["t"] >= ts:
                            exit_price = ce["price"]
                            break
                    if not exit_price:
                        continue

                # P&L from price move
                price_bps = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4

                # Funding collected during hold
                n_fundings = int(hold_h / 8)
                # Each funding period, we collect `rate` if we're on the right side
                # Approximate: use entry rate × n_fundings (conservative)
                funding_collected = abs(pos["entry_rate"]) * n_fundings * 1e4  # in bps

                gross_bps = price_bps + funding_collected
                net_bps = gross_bps - cost
                pnl = size * net_bps / 1e4

                trades.append({
                    "coin": coin,
                    "direction": "SHORT" if pos["direction"] == -1 else "LONG",
                    "hold_h": hold_h,
                    "entry_rate_ann": pos["entry_rate_ann"],
                    "price_bps": round(price_bps, 1),
                    "funding_bps": round(funding_collected, 1),
                    "gross_bps": round(gross_bps, 1),
                    "net_bps": round(net_bps, 1),
                    "pnl": round(pnl, 2),
                    "entry_t": pos["entry_t"],
                    "exit_t": ts,
                })
                del positions[coin]
                cooldown[coin] = ts + 8 * 3600000  # 8h cooldown

        # ── Entries ───────────────────────────────────
        if len(positions) >= max_pos:
            continue

        candidates = signals.get(ts, [])

        # Filter by threshold
        if mode == "absolute":
            candidates = [c for c in candidates if c["strength"] >= threshold]
        elif mode == "excess":
            candidates = [c for c in candidates if abs(c["excess"]) >= threshold]

        # Sort by strength (most extreme first)
        candidates.sort(key=lambda x: x["strength"], reverse=True)

        for cand in candidates:
            if len(positions) >= max_pos:
                break
            coin = cand["coin"]
            if coin in positions:
                continue
            if coin in cooldown and ts < cooldown[coin]:
                continue

            entry = cand["entry"]
            if hold_key not in entry:
                continue

            positions[coin] = {
                "direction": cand["direction"],
                "entry_price": entry["price"],
                "entry_rate": cand["rate"],
                "entry_rate_ann": cand["rate_ann_bps"],
                "entry_t": ts,
                "entry_data": entry,
            }

    return trades


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0, "monthly": 0, "avg_funding": 0, "avg_price": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net_bps"] for t in trades]))
    wins = sum(1 for t in trades if t["net_bps"] > 0)
    avg_funding = float(np.mean([t["funding_bps"] for t in trades]))
    avg_price = float(np.mean([t["price_bps"] for t in trades]))
    t_min = min(t["entry_t"] for t in trades)
    t_max = max(t["exit_t"] for t in trades)
    months = max(1, (t_max - t_min) / (30.44 * 86400 * 1000))
    return {
        "n": n,
        "pnl": round(pnl, 2),
        "avg": round(avg, 1),
        "win": round(wins / n * 100, 0),
        "monthly": round(pnl / months, 1),
        "avg_funding": round(avg_funding, 1),
        "avg_price": round(avg_price, 1),
    }


def monte_carlo(signals, aligned, config, n_sims=500):
    """Monte Carlo: randomize entry timing."""
    real_trades = backtest_funding(signals, aligned, {**config, "period": "all"})
    if len(real_trades) < 10:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)

    all_ts = sorted(signals.keys())
    n_trades = len(real_trades)

    sim_pnls = []
    hold_key = f"price_{config.get('hold_hours', 24)}h"

    for _ in range(n_sims):
        sim_total = 0
        sim_count = 0
        sampled = random.sample(range(len(all_ts)), min(n_trades * 5, len(all_ts)))

        for si in sampled:
            if sim_count >= n_trades:
                break
            ts = all_ts[si]
            cands = signals.get(ts, [])
            if not cands:
                continue
            c = random.choice(cands)
            entry = c["entry"]
            if hold_key not in entry or entry.get(hold_key, 0) <= 0:
                continue

            direction = random.choice([-1, 1])
            price_bps = direction * (entry[hold_key] / entry["price"] - 1) * 1e4
            funding_bps = abs(c["rate"]) * int(config.get("hold_hours", 24) / 8) * 1e4
            net = price_bps + funding_bps - COST_BPS
            sim_total += POSITION_SIZE * net / 1e4
            sim_count += 1

        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0

    return {
        "real_pnl": round(real_pnl, 2),
        "sim_mean": round(sim_mean, 2),
        "sim_std": round(sim_std, 2),
        "z": round(z, 2),
    }


def main():
    print("=" * 70)
    print("FUNDING CARRY BACKTEST")
    print("=" * 70)

    print("\nLoading funding data...")
    funding = load_funding()
    print(f"  {len(funding)} tokens")

    print("Loading price data...")
    prices = load_prices()
    print(f"  {len(prices)} tokens with prices")

    print("Aligning funding with prices...")
    aligned = align_funding_with_prices(funding, prices)
    print(f"  {sum(len(v) for v in aligned.values())} aligned entries")

    # Quick stats
    print("\nFunding stats (annualized bps):")
    for coin in sorted(aligned.keys()):
        rates = [e["rate"] * 3 * 365 * 1e4 for e in aligned[coin]]
        print(f"  {coin:6s} avg={np.mean(rates):+6.0f} std={np.std(rates):5.0f} min={np.min(rates):+7.0f} max={np.max(rates):+7.0f} n={len(rates)}")

    # ── Sweep parameters ────────────────────────────────
    lookbacks = [7, 21, 63]  # funding periods for rolling avg (×8h = 56h, 168h, 504h)
    thresholds = [1000, 2000, 5000, 10000, 20000]  # annualized bps
    hold_hours_list = [8, 24, 72, 168]
    modes = ["absolute"]

    results = []

    for lookback in lookbacks:
        lb_label = f"{lookback * 8}h"
        print(f"\n{'─' * 60}")
        print(f"Lookback: {lb_label} ({lookback} funding periods)")
        print(f"{'─' * 60}")

        sigs = compute_funding_signals(aligned, lookback=lookback)
        print(f"  {len(sigs)} timestamps with signals")

        for threshold in thresholds:
            for hold_h in hold_hours_list:
                for mode in modes:
                    for period in ["train", "test"]:
                        cfg = {
                            "threshold_ann_bps": threshold,
                            "hold_hours": hold_h,
                            "mode": mode,
                            "period": period,
                        }
                        trades = backtest_funding(sigs, aligned, cfg)
                        s = score(trades)
                        results.append({
                            "lb": lb_label, "thresh": threshold,
                            "hold": f"{hold_h}h", "mode": mode,
                            "period": period, **s,
                        })

        # Best train results for this lookback
        train_results = [r for r in results if r["lb"] == lb_label and r["period"] == "train" and r["n"] >= 15]
        if train_results:
            train_results.sort(key=lambda r: r["pnl"], reverse=True)
            print(f"\n  Top 5 (train):")
            print(f"  {'Thresh':>7} {'Hold':>5} {'N':>5} {'P&L':>8} {'Avg':>6} {'Win%':>5} {'Fund':>6} {'Price':>6}")
            for r in train_results[:5]:
                print(f"  {r['thresh']:>7} {r['hold']:>5} {r['n']:>5} ${r['pnl']:>7.0f} {r['avg']:>+6.1f} {r['win']:>4.0f}% {r['avg_funding']:>+6.1f} {r['avg_price']:>+6.1f}")

                # Test match
                test = [t for t in results if t["lb"] == lb_label and t["thresh"] == r["thresh"]
                        and t["hold"] == r["hold"] and t["period"] == "test"]
                if test:
                    t = test[0]
                    flag = "✓" if t["avg"] > 0 else "✗"
                    print(f"    → test: {flag} n={t['n']} P&L=${t['pnl']:.0f} avg={t['avg']:+.1f} fund={t['avg_funding']:+.1f} price={t['avg_price']:+.1f}")

    # ── Passing strategies ──────────────────────────────
    print(f"\n{'=' * 70}")
    print("STRATEGIES PASSING TRAIN + TEST (avg > 0 both)")
    print(f"{'=' * 70}")

    passing = []
    for lb_label in [f"{lb * 8}h" for lb in lookbacks]:
        for threshold in thresholds:
            for hold_h in hold_hours_list:
                hold_label = f"{hold_h}h"
                train = [r for r in results if r["lb"] == lb_label and r["thresh"] == threshold
                         and r["hold"] == hold_label and r["period"] == "train"]
                test = [r for r in results if r["lb"] == lb_label and r["thresh"] == threshold
                        and r["hold"] == hold_label and r["period"] == "test"]
                if train and test:
                    tr, te = train[0], test[0]
                    if tr["n"] >= 15 and te["n"] >= 5 and tr["avg"] > 0 and te["avg"] > 0:
                        passing.append({
                            "lb": lb_label, "thresh": threshold, "hold": hold_label,
                            "train": tr, "test": te,
                            "total_pnl": tr["pnl"] + te["pnl"],
                        })

    if not passing:
        print("\n  No strategy passes train + test.")
        print("  Funding carry doesn't work on these tokens, or costs eat the edge.")
    else:
        passing.sort(key=lambda x: x["total_pnl"], reverse=True)
        print(f"\n  {'LB':>5} {'Thresh':>7} {'Hold':>5} | {'TrN':>4} {'TrP&L':>8} {'TrAvg':>6} | {'TeN':>4} {'TeP&L':>8} {'TeAvg':>6} | {'Total':>8}")
        for p in passing[:15]:
            tr, te = p["train"], p["test"]
            print(f"  {p['lb']:>5} {p['thresh']:>7} {p['hold']:>5} | {tr['n']:>4} ${tr['pnl']:>7.0f} {tr['avg']:>+6.1f} | {te['n']:>4} ${te['pnl']:>7.0f} {te['avg']:>+6.1f} | ${p['total_pnl']:>7.0f}")

        # Monte Carlo on best
        print(f"\n{'─' * 60}")
        print("MONTE CARLO (top 5)")
        print(f"{'─' * 60}")

        for p in passing[:5]:
            lb_periods = int(p["lb"].replace("h", "")) // 8
            hold_h = int(p["hold"].replace("h", ""))
            sigs = compute_funding_signals(aligned, lookback=lb_periods)
            cfg = {"threshold_ann_bps": p["thresh"], "hold_hours": hold_h}
            mc = monte_carlo(sigs, aligned, cfg)
            if mc:
                flag = "✓ PASS" if mc["z"] >= 2.0 else "✗ FAIL"
                print(f"  {p['lb']} thresh={p['thresh']} hold={p['hold']}: z={mc['z']:.2f} {flag}")
                print(f"    real=${mc['real_pnl']:.0f} vs random=${mc['sim_mean']:.0f}±{mc['sim_std']:.0f}")

    # ── Breakdown ──────────────────────────────────────
    if passing:
        best = passing[0]
        lb_periods = int(best["lb"].replace("h", "")) // 8
        hold_h = int(best["hold"].replace("h", ""))
        sigs = compute_funding_signals(aligned, lookback=lb_periods)
        all_trades = backtest_funding(sigs, aligned, {
            "threshold_ann_bps": best["thresh"],
            "hold_hours": hold_h,
            "period": "all",
        })

        print(f"\n{'─' * 60}")
        print(f"BEST: lb={best['lb']} thresh={best['thresh']} hold={best['hold']}")
        print(f"{'─' * 60}")

        by_dir = defaultdict(list)
        for t in all_trades:
            by_dir[t["direction"]].append(t)
        for d in ["LONG", "SHORT"]:
            if by_dir[d]:
                s = score(by_dir[d])
                print(f"  {d:5s}: n={s['n']:>4} P&L=${s['pnl']:>7.0f} avg={s['avg']:+.1f}bps fund={s['avg_funding']:+.1f} price={s['avg_price']:+.1f}")

        by_coin = defaultdict(list)
        for t in all_trades:
            by_coin[t["coin"]].append(t)
        print(f"\n  Per token (top 10 by P&L):")
        coin_scores = [(coin, score(ts)) for coin, ts in by_coin.items()]
        coin_scores.sort(key=lambda x: x[1]["pnl"], reverse=True)
        for coin, s in coin_scores[:10]:
            print(f"  {coin:6s} n={s['n']:>3} P&L=${s['pnl']:>6.0f} avg={s['avg']:+.1f} win={s['win']:.0f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
