"""Token Unlock — Control test.

Compare unlock-timed shorts vs random-date shorts on same tokens.
If random dates perform equally well → unlock timing is worthless (just bear market).
If unlock dates outperform → the signal is real.

Usage:
    python3 -m analysis.backtest_unlock_control
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "unlock_data")

COST_BPS = 7.0

# Same unlock calendar as backtest_unlock2
UNLOCKS = [
    ("ARB", "2025-04-16", 2.65), ("ARB", "2025-05-16", 2.65), ("ARB", "2025-06-16", 2.65),
    ("ARB", "2025-07-16", 2.65), ("ARB", "2025-08-16", 2.65), ("ARB", "2025-09-16", 2.65),
    ("ARB", "2025-10-16", 2.65), ("ARB", "2025-11-16", 2.65), ("ARB", "2025-12-16", 2.65),
    ("ARB", "2026-01-16", 2.65), ("ARB", "2026-02-16", 2.65), ("ARB", "2026-03-16", 2.65),
    ("APT", "2025-04-12", 2.0), ("APT", "2025-05-12", 2.0), ("APT", "2025-06-12", 2.0),
    ("APT", "2025-07-12", 2.0), ("APT", "2025-08-12", 2.0), ("APT", "2025-09-12", 2.0),
    ("APT", "2025-10-12", 2.0), ("APT", "2025-11-12", 2.0), ("APT", "2025-12-12", 2.0),
    ("APT", "2026-01-12", 2.0), ("APT", "2026-02-12", 2.0), ("APT", "2026-03-12", 2.0),
    ("TIA", "2025-10-31", 66.0),
    ("SUI", "2025-04-01", 2.5), ("SUI", "2025-05-01", 2.5), ("SUI", "2025-06-01", 2.5),
    ("SUI", "2025-07-01", 2.5), ("SUI", "2025-08-01", 2.5), ("SUI", "2025-09-01", 2.5),
    ("SUI", "2025-10-01", 2.5), ("SUI", "2025-11-01", 2.5), ("SUI", "2025-12-01", 2.5),
    ("SUI", "2026-01-01", 2.5), ("SUI", "2026-02-01", 2.5), ("SUI", "2026-03-01", 2.5),
    ("OP", "2025-04-30", 2.0), ("OP", "2025-05-31", 2.0), ("OP", "2025-06-30", 2.0),
    ("OP", "2025-07-31", 2.0), ("OP", "2025-08-31", 2.0), ("OP", "2025-09-30", 2.0),
    ("OP", "2025-10-31", 2.0), ("OP", "2025-11-30", 2.0), ("OP", "2025-12-31", 2.0),
    ("OP", "2026-01-31", 2.0), ("OP", "2026-02-28", 2.0),
    ("STRK", "2025-04-15", 6.0), ("STRK", "2025-07-15", 6.0),
    ("STRK", "2025-10-15", 6.0), ("STRK", "2026-01-15", 6.0),
    ("SEI", "2025-04-15", 3.0), ("SEI", "2025-05-15", 3.0), ("SEI", "2025-06-15", 3.0),
    ("SEI", "2025-07-15", 3.0), ("SEI", "2025-08-15", 3.0), ("SEI", "2025-09-15", 3.0),
    ("SEI", "2025-10-15", 3.0), ("SEI", "2025-11-15", 3.0), ("SEI", "2025-12-15", 3.0),
    ("SEI", "2026-01-15", 3.0), ("SEI", "2026-02-15", 3.0),
    ("EIGEN", "2025-05-08", 5.0), ("EIGEN", "2025-06-08", 5.0), ("EIGEN", "2025-07-08", 5.0),
    ("EIGEN", "2025-08-08", 5.0), ("EIGEN", "2025-09-08", 5.0), ("EIGEN", "2025-10-08", 5.0),
    ("EIGEN", "2025-11-08", 5.0), ("EIGEN", "2025-12-08", 5.0), ("EIGEN", "2026-01-08", 5.0),
    ("EIGEN", "2026-02-08", 5.0),
    ("WLD", "2025-07-24", 10.0),
    ("ENA", "2025-04-02", 3.0), ("ENA", "2025-07-02", 3.0),
    ("ENA", "2025-10-02", 3.0), ("ENA", "2026-01-02", 3.0),
]


def fetch_hl_candles(coin: str) -> dict[str, float]:
    cache = os.path.join(DATA_DIR, f"{coin}_candles_1d.json")
    if os.path.exists(cache):
        with open(cache) as f:
            candles = json.load(f)
        return {datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"): float(c["c"]) for c in candles}

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - 400 * 86400 * 1000
    payload = json.dumps({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1d", "startTime": start_ts, "endTime": end_ts
    }}).encode()
    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            candles = json.loads(resp.read())
        with open(cache, "w") as f:
            json.dump(candles, f)
    except Exception:
        return {}
    return {datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"): float(c["c"]) for c in candles}


def get_price(prices, date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for off in range(4):
        for s in [0, 1, -1]:
            d = (dt + timedelta(days=off * s)).strftime("%Y-%m-%d")
            if d in prices and prices[d] > 0:
                return prices[d], d
    return None, None


def run_trades(events, price_data, entry_before, exit_after):
    """Run short trades for a list of (ticker, date) events."""
    results = []
    for ticker, date_str, *_ in events:
        prices = price_data.get(ticker, {})
        if not prices:
            continue
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        entry_dt = dt + timedelta(days=entry_before)
        exit_dt = dt + timedelta(days=exit_after)

        ep, _ = get_price(prices, entry_dt.strftime("%Y-%m-%d"))
        xp, _ = get_price(prices, exit_dt.strftime("%Y-%m-%d"))
        if not ep or not xp:
            continue

        gross = -(xp / ep - 1) * 1e4
        net = gross - COST_BPS
        results.append({"ticker": ticker, "date": date_str, "net_bps": net, "gross_bps": gross})
    return results


def stats(results):
    if not results:
        return {"n": 0, "avg_net": 0, "avg_gross": 0, "win_rate": 0, "total": 0}
    nets = [r["net_bps"] for r in results]
    grosses = [r["gross_bps"] for r in results]
    wins = sum(1 for n in nets if n > 0)
    return {
        "n": len(results),
        "avg_net": float(np.mean(nets)),
        "avg_gross": float(np.mean(grosses)),
        "median_net": float(np.median(nets)),
        "win_rate": wins / len(results),
        "total": sum(nets),
    }


def main():
    print("=" * 60)
    print("  UNLOCK vs RANDOM — Control Test")
    print("  Is the unlock timing signal real, or just bear market?")
    print("=" * 60)

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    valid = [(tk, dt, pct) for tk, dt, pct in UNLOCKS
             if datetime.strptime(dt, "%Y-%m-%d") < cutoff.replace(tzinfo=None)]

    tickers = sorted(set(u[0] for u in valid))
    print(f"\n  Tokens: {tickers}")
    print(f"  Unlock events: {len(valid)}")

    # Load prices
    print("\nLoading prices...")
    price_data = {}
    for tk in tickers:
        price_data[tk] = fetch_hl_candles(tk)
        time.sleep(0.3)

    # Configs to test
    configs = [
        ("Short -2d / +3d", -2, 3),
        ("Short -3d / +7d", -3, 7),
        ("Short -2d / +5d", -2, 5),
    ]

    for label, entry_before, exit_after in configs:
        print(f"\n\n{'▓'*60}")
        print(f"  CONFIG: {label}")
        print(f"{'▓'*60}")

        # ── 1. Unlock-timed trades ───────────────────────────────
        unlock_results = run_trades(valid, price_data, entry_before, exit_after)
        us = stats(unlock_results)

        print(f"\n  UNLOCK-TIMED SHORTS:")
        print(f"    Trades: {us['n']}, Win: {us['win_rate']*100:.0f}%, "
              f"Gross: {us['avg_gross']:+.1f} bps, Net: {us['avg_net']:+.1f} bps, "
              f"Median: {us['median_net']:+.1f} bps")

        # Per ticker
        by_tk = defaultdict(list)
        for r in unlock_results:
            by_tk[r["ticker"]].append(r)

        # ── 2. Random-date control (same tokens, same # trades) ──
        # For each token, generate same number of random dates within the same date range
        # Run 100 Monte Carlo simulations to get distribution
        n_sims = 200
        random_avgs = []
        random_totals = []
        random_wins = []

        for _ in range(n_sims):
            random_events = []
            for tk in tickers:
                tk_unlocks = [u for u in valid if u[0] == tk]
                n_trades = len(tk_unlocks)
                dates = sorted(price_data[tk].keys())
                if len(dates) < 20:
                    continue
                # Sample random dates (avoid first/last 10 days)
                available = dates[10:-10]
                if len(available) < n_trades:
                    continue
                sampled = random.sample(available, n_trades)
                for d in sampled:
                    random_events.append((tk, d, 0))

            rr = run_trades(random_events, price_data, entry_before, exit_after)
            rs = stats(rr)
            random_avgs.append(rs["avg_net"])
            random_totals.append(rs["total"])
            random_wins.append(rs["win_rate"])

        rand_avg = float(np.mean(random_avgs))
        rand_avg_std = float(np.std(random_avgs))
        rand_total = float(np.mean(random_totals))
        rand_win = float(np.mean(random_wins))

        print(f"\n  RANDOM-DATE SHORTS ({n_sims} Monte Carlo simulations):")
        print(f"    Avg Net: {rand_avg:+.1f} bps (std: {rand_avg_std:.1f})")
        print(f"    Avg Total: {rand_total:+.0f} bps")
        print(f"    Avg Win Rate: {rand_win*100:.0f}%")

        # ── 3. Statistical comparison ────────────────────────────
        # How many standard deviations is the unlock result from random?
        if rand_avg_std > 0:
            z_score = (us["avg_net"] - rand_avg) / rand_avg_std
        else:
            z_score = 0

        # What % of random sims beat the unlock result?
        pct_random_better = sum(1 for r in random_avgs if r >= us["avg_net"]) / n_sims * 100

        print(f"\n  COMPARISON:")
        print(f"    Unlock avg net:  {us['avg_net']:+.1f} bps")
        print(f"    Random avg net:  {rand_avg:+.1f} bps")
        print(f"    Difference:      {us['avg_net'] - rand_avg:+.1f} bps")
        print(f"    Z-score:         {z_score:+.2f}")
        print(f"    Random beats unlock: {pct_random_better:.0f}% of simulations")

        if z_score > 2:
            print(f"    → SIGNIFICANT (z > 2): unlock timing adds real value")
        elif z_score > 1:
            print(f"    → MARGINAL (1 < z < 2): some signal but not conclusive")
        else:
            print(f"    → NOT SIGNIFICANT (z < 1): likely just bear market")

        # ── 4. Per-token breakdown ───────────────────────────────
        print(f"\n  PER-TOKEN ANALYSIS (unlock vs random avg):")
        print(f"  {'Token':<8} {'Unlock':>9} {'Random':>9} {'Diff':>9} {'Signal?':>10}")
        print(f"  {'-'*48}")

        for tk in tickers:
            tk_unlock = [r for r in unlock_results if r["ticker"] == tk]
            if not tk_unlock:
                continue
            tk_avg = float(np.mean([r["net_bps"] for r in tk_unlock]))

            # Random avg for this token
            tk_random_avgs = []
            for _ in range(200):
                dates = sorted(price_data[tk].keys())
                available = dates[10:-10]
                if len(available) < len(tk_unlock):
                    continue
                sampled = random.sample(available, len(tk_unlock))
                rr = run_trades([(tk, d, 0) for d in sampled], price_data, entry_before, exit_after)
                if rr:
                    tk_random_avgs.append(float(np.mean([r["net_bps"] for r in rr])))

            if tk_random_avgs:
                tk_rand = float(np.mean(tk_random_avgs))
                tk_rand_std = float(np.std(tk_random_avgs))
                tk_z = (tk_avg - tk_rand) / tk_rand_std if tk_rand_std > 0 else 0
                sig = "✓ YES" if tk_z > 1.5 else ("~ maybe" if tk_z > 0.5 else "✗ NO")
                print(f"  {tk:<8} {tk_avg:>+8.1f} {tk_rand:>+8.1f} {tk_avg-tk_rand:>+8.1f} {sig:>10} (z={tk_z:+.1f})")
            else:
                print(f"  {tk:<8} {tk_avg:>+8.1f} {'?':>9}")

    # ── 5. Overall verdict ───────────────────────────────────────
    print(f"\n\n{'█'*60}")
    print(f"  VERDICT")
    print(f"{'█'*60}")
    print(f"\n  The key question: do unlock dates SHORT better than random dates?")
    print(f"  If z-score > 2 → the signal is real")
    print(f"  If z-score < 1 → it's just the bear market")


if __name__ == "__main__":
    main()
