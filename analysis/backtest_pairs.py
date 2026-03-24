"""Pairs Trading / Statistical Arbitrage — Hyperliquid.

Strategy: Find correlated token pairs. When spread deviates, trade convergence.
- Long the underperformer, short the outperformer
- Market-neutral (works in bull AND bear)
- No directional prediction needed

Usage:
    python3 -m analysis.backtest_pairs
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from itertools import combinations

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
os.makedirs(DATA_DIR, exist_ok=True)

# Tokens to analyze — grouped by sector for logical pairs
TOKENS = [
    # L2s
    "ARB", "OP", "STRK", "MANTA",
    # L1s
    "ETH", "SOL", "AVAX", "SUI", "APT", "SEI", "TIA", "NEAR",
    # DeFi
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    # Meme/Culture
    "DOGE", "WLD", "BLUR",
    # Infrastructure
    "LINK", "PYTH", "EIGEN",
    # Major
    "BTC",
]

COST_BPS = 7.0  # taker roundtrip both legs (3.5 × 2)
MAKER_COST_BPS = 2.0  # maker roundtrip both legs (1 × 2)


def fetch_candles(coin: str, interval: str = "1d", days: int = 365) -> list[dict]:
    cache = os.path.join(DATA_DIR, f"{coin}_{interval}.json")
    if os.path.exists(cache):
        age = (time.time() - os.path.getmtime(cache)) / 3600
        if age < 12:
            with open(cache) as f:
                return json.load(f)

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86400 * 1000
    payload = json.dumps({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval, "startTime": start_ts, "endTime": end_ts
    }}).encode()
    try:
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if data:
            with open(cache, "w") as f:
                json.dump(data, f)
        return data or []
    except Exception as e:
        return []


def load_prices(interval="1d", days=365) -> dict[str, dict[str, float]]:
    """Load daily close prices for all tokens. Returns {coin: {date: price}}."""
    prices = {}
    for coin in TOKENS:
        candles = fetch_candles(coin, interval, days)
        if not candles:
            continue
        p = {}
        for c in candles:
            dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
            p[dt.strftime("%Y-%m-%d")] = float(c["c"])
        if len(p) > 100:
            prices[coin] = p
        time.sleep(0.3)
    return prices


def compute_returns(prices: dict[str, dict[str, float]], dates: list[str]) -> dict[str, np.ndarray]:
    """Compute daily log returns for each token."""
    returns = {}
    for coin, p in prices.items():
        rets = []
        for i in range(1, len(dates)):
            if dates[i] in p and dates[i-1] in p and p[dates[i-1]] > 0:
                rets.append(np.log(p[dates[i]] / p[dates[i-1]]))
            else:
                rets.append(np.nan)
        returns[coin] = np.array(rets)
    return returns


def find_pairs(returns: dict[str, np.ndarray], min_corr: float = 0.7) -> list[tuple]:
    """Find highly correlated pairs."""
    coins = list(returns.keys())
    pairs = []

    for a, b in combinations(coins, 2):
        ra, rb = returns[a], returns[b]
        # Align (remove NaN)
        mask = ~(np.isnan(ra) | np.isnan(rb))
        if mask.sum() < 60:
            continue
        corr = np.corrcoef(ra[mask], rb[mask])[0, 1]
        if corr >= min_corr:
            pairs.append((a, b, round(corr, 4)))

    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


def compute_spread(prices_a: dict, prices_b: dict, dates: list[str],
                    window: int = 30) -> list[dict]:
    """Compute log price ratio (spread) and its z-score."""
    spread_data = []
    ratios = []

    for d in dates:
        if d in prices_a and d in prices_b and prices_a[d] > 0 and prices_b[d] > 0:
            ratio = np.log(prices_a[d] / prices_b[d])
            ratios.append(ratio)

            if len(ratios) >= window:
                recent = ratios[-window:]
                mean = np.mean(recent)
                std = np.std(recent)
                zscore = (ratio - mean) / std if std > 0 else 0
            else:
                zscore = 0

            spread_data.append({
                "date": d, "ratio": ratio, "zscore": zscore,
                "price_a": prices_a[d], "price_b": prices_b[d],
            })

    return spread_data


def backtest_pair(spread_data: list[dict], pair_name: str,
                   entry_z: float = 2.0, exit_z: float = 0.5,
                   max_hold_days: int = 14,
                   cost_bps: float = COST_BPS,
                   size: float = 250.0) -> list[dict]:
    """
    Backtest mean-reversion on spread z-score.
    When z > entry_z: spread too wide → short A, long B (expect convergence)
    When z < -entry_z: spread too narrow → long A, short B
    Exit when |z| < exit_z or timeout.
    """
    trades = []
    position = None  # {"entry_idx", "direction", "entry_spread"}

    for i, s in enumerate(spread_data):
        z = s["zscore"]

        # Check exit
        if position is not None:
            held = i - position["entry_idx"]
            exit_reason = None

            if position["direction"] == -1 and z < exit_z:
                exit_reason = "converge"
            elif position["direction"] == 1 and z > -exit_z:
                exit_reason = "converge"
            elif held >= max_hold_days:
                exit_reason = "timeout"

            if exit_reason:
                entry_s = spread_data[position["entry_idx"]]
                # P&L: change in spread × direction
                # If we shorted the spread (direction=-1), we profit when spread decreases
                spread_change = (s["ratio"] - entry_s["ratio"]) * 1e4  # in bps
                gross_bps = -position["direction"] * spread_change
                net_bps = gross_bps - cost_bps  # cost for 2 legs × 2 sides

                # Also track individual leg returns
                ret_a = (s["price_a"] / entry_s["price_a"] - 1) * 1e4
                ret_b = (s["price_b"] / entry_s["price_b"] - 1) * 1e4

                trades.append({
                    "pair": pair_name,
                    "entry_date": entry_s["date"],
                    "exit_date": s["date"],
                    "direction": "short_spread" if position["direction"] == -1 else "long_spread",
                    "entry_z": round(spread_data[position["entry_idx"]]["zscore"], 2),
                    "exit_z": round(z, 2),
                    "hold_days": held,
                    "gross_bps": round(gross_bps, 1),
                    "net_bps": round(net_bps, 1),
                    "pnl": round(size * net_bps / 1e4, 2),
                    "ret_a": round(ret_a, 1),
                    "ret_b": round(ret_b, 1),
                    "reason": exit_reason,
                })
                position = None

        # Check entry (only if not in position)
        if position is None:
            if z > entry_z:
                position = {"entry_idx": i, "direction": -1}  # short spread
            elif z < -entry_z:
                position = {"entry_idx": i, "direction": 1}  # long spread

    return trades


def analyze(trades: list[dict], label: str) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0}

    wins = sum(1 for t in trades if t["net_bps"] > 0)
    avg_net = float(np.mean([t["net_bps"] for t in trades]))
    avg_gross = float(np.mean([t["gross_bps"] for t in trades]))
    total_pnl = sum(t["pnl"] for t in trades)
    avg_hold = float(np.mean([t["hold_days"] for t in trades]))

    # Converge vs timeout
    converge = [t for t in trades if t["reason"] == "converge"]
    timeout = [t for t in trades if t["reason"] == "timeout"]

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:    {n} ({n/12:.1f}/month)")
    print(f"  Win rate:  {wins/n*100:.0f}%")
    print(f"  Gross avg: {avg_gross:+.1f} bps | Net avg: {avg_net:+.1f} bps")
    print(f"  P&L:       ${total_pnl:+.2f} (at $250/trade)")
    print(f"  Avg hold:  {avg_hold:.1f} days")
    if converge:
        c_avg = float(np.mean([t["net_bps"] for t in converge]))
        print(f"  Converge:  {len(converge)} trades, {c_avg:+.1f} bps avg")
    if timeout:
        t_avg = float(np.mean([t["net_bps"] for t in timeout]))
        print(f"  Timeout:   {len(timeout)} trades, {t_avg:+.1f} bps avg")

    # By pair
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t["pair"]].append(t)
    pair_stats = sorted([(p, len(ts), float(np.mean([t["net_bps"] for t in ts])),
                          sum(t["pnl"] for t in ts))
                         for p, ts in by_pair.items()], key=lambda x: x[3], reverse=True)
    if pair_stats:
        print(f"\n  {'Pair':<16} {'Trades':>6} {'AvgNet':>8} {'P&L$':>9}")
        print(f"  {'-'*42}")
        for p, cnt, avg, pnl in pair_stats:
            print(f"  {p:<16} {cnt:>6} {avg:>+7.1f} ${pnl:>+8.2f} {'✓' if pnl > 0 else '✗'}")

    # Monthly
    by_month = defaultdict(float)
    for t in trades:
        by_month[t["entry_date"][:7]] += t["pnl"]
    losing = sum(1 for v in by_month.values() if v <= 0)
    print(f"\n  Months: {len(by_month)-losing}/{len(by_month)} winning")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_net": avg_net, "pnl": total_pnl, "avg_hold": avg_hold,
        "losing_months": losing, "total_months": len(by_month),
    }


def main():
    print("=" * 60)
    print("  PAIRS TRADING / STAT ARB — Hyperliquid")
    print("  Correlated pairs, spread mean-reversion")
    print("=" * 60)

    # ── Load Data ────────────────────────────────────────────────
    print("\nLoading daily prices from Hyperliquid...")
    prices = load_prices("1d", 365)
    print(f"Loaded {len(prices)} tokens")

    # Common dates
    all_dates_sets = [set(p.keys()) for p in prices.values()]
    common_dates = sorted(set.intersection(*all_dates_sets)) if all_dates_sets else []
    print(f"Common dates: {len(common_dates)} ({common_dates[0] if common_dates else '?'} → {common_dates[-1] if common_dates else '?'})")

    # ── Find Correlated Pairs ────────────────────────────────────
    print("\nComputing correlations...")
    returns = compute_returns(prices, common_dates)
    pairs = find_pairs(returns, min_corr=0.65)

    print(f"\nFound {len(pairs)} pairs with correlation > 0.65:")
    print(f"  {'Pair':<20} {'Corr':>6}")
    print(f"  {'-'*28}")
    for a, b, corr in pairs[:25]:
        print(f"  {a+'/'+b:<20} {corr:>+.4f}")

    # ── Backtest Top Pairs ───────────────────────────────────────
    print(f"\n\n{'▓'*60}")
    print(f"  BACKTEST: Spread mean-reversion on top pairs")
    print(f"{'▓'*60}")

    # Use top 15 pairs
    top_pairs = pairs[:15]
    results = []

    # Config 1: z=2.0 entry, z=0.5 exit, 14d max hold, taker fees
    print("\n--- Config 1: z=2.0 / exit z=0.5 / 14d hold / taker (7bps) ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=2.0, exit_z=0.5,
                                max_hold_days=14, cost_bps=COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C1. z=2.0/0.5, 14d, taker (7bps)")
    results.append(r)

    # Config 2: z=1.5 entry (more trades)
    print("\n--- Config 2: z=1.5 / exit z=0.3 / 14d / taker ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=1.5, exit_z=0.3,
                                max_hold_days=14, cost_bps=COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C2. z=1.5/0.3, 14d, taker (7bps)")
    results.append(r)

    # Config 3: maker fees (2bps) — if using limit orders
    print("\n--- Config 3: z=2.0/0.5, 14d, MAKER (2bps) ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=2.0, exit_z=0.5,
                                max_hold_days=14, cost_bps=MAKER_COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C3. z=2.0/0.5, 14d, maker (2bps)")
    results.append(r)

    # Config 4: maker, z=1.5
    print("\n--- Config 4: z=1.5/0.3, 14d, maker (2bps) ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=1.5, exit_z=0.3,
                                max_hold_days=14, cost_bps=MAKER_COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C4. z=1.5/0.3, 14d, maker (2bps)")
    results.append(r)

    # Config 5: longer hold (30d)
    print("\n--- Config 5: z=2.0/0.5, 30d hold, maker ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=2.0, exit_z=0.5,
                                max_hold_days=30, cost_bps=MAKER_COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C5. z=2.0/0.5, 30d, maker (2bps)")
    results.append(r)

    # Config 6: tighter z, shorter hold
    print("\n--- Config 6: z=2.5/0.5, 7d, maker ---")
    all_trades = []
    for a, b, corr in top_pairs:
        spread = compute_spread(prices[a], prices[b], common_dates, window=30)
        trades = backtest_pair(spread, f"{a}/{b}", entry_z=2.5, exit_z=0.5,
                                max_hold_days=7, cost_bps=MAKER_COST_BPS)
        all_trades.extend(trades)
    r = analyze(all_trades, "C6. z=2.5/0.5, 7d, maker (2bps)")
    results.append(r)

    # Config 7: hourly data for more granularity (4h candles)
    # Use only top 5 pairs with 4h data
    print("\n--- Config 7: 4h candles, z=2.0/0.5, 3d hold, maker ---")
    top5 = top_pairs[:5]
    prices_4h = {}
    for a, b, _ in top5:
        for coin in [a, b]:
            if coin not in prices_4h:
                candles = fetch_candles(coin, "4h", 90)
                if candles:
                    p = {}
                    for c in candles:
                        dt = datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc)
                        p[dt.strftime("%Y-%m-%d %H:%M")] = float(c["c"])
                    prices_4h[coin] = p
                time.sleep(0.3)

    if len(prices_4h) >= 2:
        common_4h = sorted(set.intersection(*[set(p.keys()) for p in prices_4h.values()]))
        all_trades = []
        for a, b, corr in top5:
            if a in prices_4h and b in prices_4h:
                spread = compute_spread(prices_4h[a], prices_4h[b], common_4h, window=42)  # 42×4h = 7 days
                trades = backtest_pair(spread, f"{a}/{b}", entry_z=2.0, exit_z=0.5,
                                        max_hold_days=18, cost_bps=MAKER_COST_BPS)  # 18×4h = 3 days
                all_trades.extend(trades)
        r = analyze(all_trades, "C7. 4h candles, z=2.0/0.5, ~3d, maker")
        results.append(r)

    # ── SUMMARY ──────────────────────────────────────────────────
    print(f"\n\n{'█'*70}")
    print(f"  SUMMARY — PAIRS TRADING / STAT ARB")
    print(f"{'█'*70}")
    print(f"\n  {'Config':<42} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'Hold':>5} {'L.Mo':>5}")
    print(f"  {'-'*78}")
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r["pnl"] > 0 else "✗"
        lm = f"{r.get('losing_months','?')}/{r.get('total_months','?')}"
        print(f"  {r['label']:<42} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+8.2f} {r.get('avg_hold',0):>4.1f}d {lm:>5} {m}")

    best = max(results, key=lambda r: r.get("pnl", -9999))
    if best["trades"] > 0:
        monthly = best["pnl"] / max(1, best.get("total_months", 12))
        print(f"\n  BEST: {best['label']}")
        print(f"  → ${best['pnl']:+.2f}/an = ${monthly:+.2f}/mois")


if __name__ == "__main__":
    main()
