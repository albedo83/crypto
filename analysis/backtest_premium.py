"""Premium Mean Reversion — Short when perp trades above spot, long when below.

Concept: perpetual contracts have a premium/discount vs spot. When premium is
very positive (perp > spot), shorts collect premium as it reverts to 0.
Structural edge: premium mean-reverts because arbitrageurs enforce it.

Data: premium field in *_funding_full.json (every 8h, ~3 years)

Usage:
    python3 -m analysis.backtest_premium
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
MAX_POSITIONS = 4

TRAIN_END = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000
TEST_START = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000


def load_data():
    """Load funding (with premium) + price data, align them."""
    from analysis.backtest_genetic import load_3y_candles

    prices = load_3y_candles()
    aligned = {}

    for coin in TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_funding_full.json")
        if not os.path.exists(path) or coin not in prices:
            continue
        with open(path) as f:
            raw = json.load(f)
        if len(raw) < 200:
            continue

        price_ts = np.array([c["t"] for c in prices[coin]])
        price_vals = np.array([c["c"] for c in prices[coin]])

        entries = []
        for r in raw:
            t = r["time"]
            premium = float(r.get("premium", 0))
            idx = np.searchsorted(price_ts, t)
            if idx >= len(price_ts):
                idx = len(price_ts) - 1
            if idx > 0 and abs(price_ts[idx - 1] - t) < abs(price_ts[idx] - t):
                idx -= 1
            if abs(price_ts[idx] - t) > 14400000:
                continue

            entry = {"t": t, "premium": premium, "price": price_vals[idx], "pidx": int(idx)}
            for name, off in [("8h", 2), ("24h", 6), ("72h", 18), ("168h", 42)]:
                fi = idx + off
                if fi < len(price_vals):
                    entry[f"p_{name}"] = price_vals[fi]
            entries.append(entry)

        if len(entries) > 200:
            aligned[coin] = entries

    return aligned


def backtest(aligned, config):
    threshold = config.get("threshold_ppm", 200)  # premium in ppm (parts per million)
    hold_h = config.get("hold_hours", 24)
    max_pos = config.get("max_positions", MAX_POSITIONS)
    lookback = config.get("lookback", 21)
    mode = config.get("mode", "absolute")  # "absolute" or "excess"
    period = config.get("period", "all")
    hold_key = f"p_{hold_h}h"

    positions = {}
    trades = []

    # Build timeline: all timestamps across all coins
    all_events = []
    for coin, entries in aligned.items():
        for i, e in enumerate(entries):
            if i < lookback:
                continue
            if period == "train" and e["t"] >= TRAIN_END:
                continue
            if period == "test" and e["t"] < TEST_START:
                continue
            # Rolling avg premium
            window = [entries[j]["premium"] for j in range(i - lookback, i)]
            avg_prem = float(np.mean(window))
            all_events.append({
                "t": e["t"], "coin": coin,
                "premium": e["premium"], "avg_premium": avg_prem,
                "premium_ppm": e["premium"] * 1e6,
                "excess_ppm": (e["premium"] - avg_prem) * 1e6,
                "entry": e,
            })

    all_events.sort(key=lambda x: x["t"])

    # Group by timestamp
    by_ts = defaultdict(list)
    for ev in all_events:
        by_ts[ev["t"]].append(ev)

    for ts in sorted(by_ts.keys()):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if (ts - pos["entry_t"]) / 3600000 >= hold_h:
                e = pos["entry_data"]
                exit_price = e.get(hold_key)
                if not exit_price or exit_price <= 0:
                    continue
                price_bps = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net = price_bps - COST_BPS
                pnl = POSITION_SIZE * net / 1e4
                trades.append({
                    "coin": coin, "direction": "SHORT" if pos["direction"] == -1 else "LONG",
                    "premium_ppm": pos["premium_ppm"],
                    "price_bps": round(price_bps, 1), "net_bps": round(net, 1),
                    "pnl": round(pnl, 2), "entry_t": pos["entry_t"], "exit_t": ts,
                })
                del positions[coin]

        # Entries
        if len(positions) >= max_pos:
            continue

        events = by_ts[ts]
        candidates = []
        for ev in events:
            ppm = ev["premium_ppm"]
            excess = ev["excess_ppm"]
            strength = abs(ppm) if mode == "absolute" else abs(excess)
            if strength < threshold:
                continue
            direction = -1 if ppm > 0 else 1  # short when premium positive
            candidates.append((ev, direction, strength))

        candidates.sort(key=lambda x: x[2], reverse=True)

        for ev, direction, strength in candidates:
            if len(positions) >= max_pos:
                break
            coin = ev["coin"]
            if coin in positions:
                continue
            e = ev["entry"]
            if hold_key not in e:
                continue
            positions[coin] = {
                "direction": direction, "entry_price": e["price"],
                "premium_ppm": ev["premium_ppm"],
                "entry_t": ts, "entry_data": e,
            }

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
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1),
            "win": round(wins / n * 100, 0), "monthly": round(pnl / months, 1)}


def monte_carlo(aligned, config, n_sims=500):
    real_trades = backtest(aligned, {**config, "period": "all"})
    if len(real_trades) < 10:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    hold_key = f"p_{config.get('hold_hours', 24)}h"
    n_trades = len(real_trades)

    all_entries = []
    for coin, entries in aligned.items():
        for e in entries:
            if hold_key in e:
                all_entries.append((coin, e))

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, e in sampled:
            direction = random.choice([-1, 1])
            price_bps = direction * (e[hold_key] / e["price"] - 1) * 1e4
            net = price_bps - COST_BPS
            sim_total += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    return {"real_pnl": round(real_pnl, 2), "z": round(z, 2)}


def main():
    print("=" * 60)
    print("PREMIUM MEAN REVERSION BACKTEST")
    print("=" * 60)

    print("\nLoading data...")
    aligned = load_data()
    print(f"  {len(aligned)} tokens")

    # Premium stats
    print("\nPremium stats (ppm):")
    for coin in sorted(aligned.keys()):
        prems = [e["premium"] * 1e6 for e in aligned[coin]]
        print(f"  {coin:6s} avg={np.mean(prems):+6.0f} std={np.std(prems):5.0f} |range|={np.max(np.abs(prems)):.0f}")

    thresholds = [50, 100, 200, 500, 1000]
    holds = [8, 24, 72, 168]
    lookbacks = [7, 21, 63]

    results = []
    for lookback in lookbacks:
        lb_h = lookback * 8
        print(f"\n{'─' * 50}")
        print(f"Lookback: {lb_h}h")

        for mode in ["absolute", "excess"]:
            for thresh in thresholds:
                for hold_h in holds:
                    for period in ["train", "test"]:
                        trades = backtest(aligned, {
                            "threshold_ppm": thresh, "hold_hours": hold_h,
                            "lookback": lookback, "mode": mode, "period": period,
                        })
                        s = score(trades)
                        results.append({"lb": f"{lb_h}h", "mode": mode, "thresh": thresh,
                                        "hold": f"{hold_h}h", "period": period, **s})

        # Best train
        train = [r for r in results if r["lb"] == f"{lb_h}h" and r["period"] == "train" and r["n"] >= 15]
        if train:
            train.sort(key=lambda r: r["pnl"], reverse=True)
            print(f"\n  Top 5 (train):")
            print(f"  {'Mode':>7} {'Thr':>5} {'Hold':>5} {'N':>5} {'P&L':>8} {'Avg':>6} {'Win%':>5}")
            for r in train[:5]:
                print(f"  {r['mode']:>7} {r['thresh']:>5} {r['hold']:>5} {r['n']:>5} ${r['pnl']:>7.0f} {r['avg']:>+6.1f} {r['win']:>4.0f}%")
                test = [t for t in results if t["lb"] == r["lb"] and t["mode"] == r["mode"]
                        and t["thresh"] == r["thresh"] and t["hold"] == r["hold"] and t["period"] == "test"]
                if test:
                    t = test[0]
                    f = "✓" if t["avg"] > 0 else "✗"
                    print(f"    → test: {f} n={t['n']} P&L=${t['pnl']:.0f} avg={t['avg']:+.1f}")

    # Passing strategies
    print(f"\n{'=' * 60}")
    print("PASSING TRAIN + TEST")
    passing = []
    for r_train in [r for r in results if r["period"] == "train" and r["n"] >= 15 and r["avg"] > 0]:
        r_test = [t for t in results if t["lb"] == r_train["lb"] and t["mode"] == r_train["mode"]
                  and t["thresh"] == r_train["thresh"] and t["hold"] == r_train["hold"] and t["period"] == "test"]
        if r_test and r_test[0]["avg"] > 0 and r_test[0]["n"] >= 5:
            passing.append({"train": r_train, "test": r_test[0],
                            "total": r_train["pnl"] + r_test[0]["pnl"]})

    if not passing:
        print("  None. Premium mean reversion doesn't work.")
    else:
        passing.sort(key=lambda x: x["total"], reverse=True)
        for p in passing[:10]:
            tr, te = p["train"], p["test"]
            print(f"  {tr['lb']} {tr['mode']:>7} thr={tr['thresh']} hold={tr['hold']}: "
                  f"train ${tr['pnl']:.0f} ({tr['avg']:+.1f}bps) | test ${te['pnl']:.0f} ({te['avg']:+.1f}bps)")

        # Monte Carlo top 3
        print(f"\n  Monte Carlo:")
        for p in passing[:3]:
            tr = p["train"]
            lb_periods = int(tr["lb"].replace("h", "")) // 8
            hold_h = int(tr["hold"].replace("h", ""))
            mc = monte_carlo(aligned, {"threshold_ppm": tr["thresh"], "hold_hours": hold_h,
                                       "lookback": lb_periods, "mode": tr["mode"]})
            if mc:
                f = "✓" if mc["z"] >= 2.0 else "✗"
                print(f"    z={mc['z']:.2f} {f} (${mc['real_pnl']:.0f})")

    print("\nDone.")


if __name__ == "__main__":
    main()
