"""Token Unlock backtest v2 — Manual calendar + Hyperliquid prices.

Usage:
    python3 -m analysis.backtest_unlock2
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "unlock_data")

COST_BPS = 7.0  # Hyperliquid taker roundtrip (conservative)

# Manual unlock calendar (public knowledge)
UNLOCKS = [
    # (ticker, date, pct_of_supply, category)
    # ARB - monthly ~2.65%
    ("ARB", "2025-04-16", 2.65, "investor+team"), ("ARB", "2025-05-16", 2.65, "investor+team"),
    ("ARB", "2025-06-16", 2.65, "investor+team"), ("ARB", "2025-07-16", 2.65, "investor+team"),
    ("ARB", "2025-08-16", 2.65, "investor+team"), ("ARB", "2025-09-16", 2.65, "investor+team"),
    ("ARB", "2025-10-16", 2.65, "investor+team"), ("ARB", "2025-11-16", 2.65, "investor+team"),
    ("ARB", "2025-12-16", 2.65, "investor+team"), ("ARB", "2026-01-16", 2.65, "investor+team"),
    ("ARB", "2026-02-16", 2.65, "investor+team"), ("ARB", "2026-03-16", 2.65, "investor+team"),
    # APT - monthly ~2%
    ("APT", "2025-04-12", 2.0, "investor+team"), ("APT", "2025-05-12", 2.0, "investor+team"),
    ("APT", "2025-06-12", 2.0, "investor+team"), ("APT", "2025-07-12", 2.0, "investor+team"),
    ("APT", "2025-08-12", 2.0, "investor+team"), ("APT", "2025-09-12", 2.0, "investor+team"),
    ("APT", "2025-10-12", 2.0, "investor+team"), ("APT", "2025-11-12", 2.0, "investor+team"),
    ("APT", "2025-12-12", 2.0, "investor+team"), ("APT", "2026-01-12", 2.0, "investor+team"),
    ("APT", "2026-02-12", 2.0, "investor+team"), ("APT", "2026-03-12", 2.0, "investor+team"),
    # TIA - massive cliff Oct 2025
    ("TIA", "2025-10-31", 66.0, "investor cliff"),
    # SUI - monthly ~2.5%
    ("SUI", "2025-04-01", 2.5, "investor+team"), ("SUI", "2025-05-01", 2.5, "investor+team"),
    ("SUI", "2025-06-01", 2.5, "investor+team"), ("SUI", "2025-07-01", 2.5, "investor+team"),
    ("SUI", "2025-08-01", 2.5, "investor+team"), ("SUI", "2025-09-01", 2.5, "investor+team"),
    ("SUI", "2025-10-01", 2.5, "investor+team"), ("SUI", "2025-11-01", 2.5, "investor+team"),
    ("SUI", "2025-12-01", 2.5, "investor+team"), ("SUI", "2026-01-01", 2.5, "investor+team"),
    ("SUI", "2026-02-01", 2.5, "investor+team"), ("SUI", "2026-03-01", 2.5, "investor+team"),
    # OP - monthly ~2%
    ("OP", "2025-04-30", 2.0, "team"), ("OP", "2025-05-31", 2.0, "team"),
    ("OP", "2025-06-30", 2.0, "team"), ("OP", "2025-07-31", 2.0, "team"),
    ("OP", "2025-08-31", 2.0, "team"), ("OP", "2025-09-30", 2.0, "team"),
    ("OP", "2025-10-31", 2.0, "team"), ("OP", "2025-11-30", 2.0, "team"),
    ("OP", "2025-12-31", 2.0, "team"), ("OP", "2026-01-31", 2.0, "team"),
    ("OP", "2026-02-28", 2.0, "team"),
    # STRK - quarterly ~6%
    ("STRK", "2025-04-15", 6.0, "investor"), ("STRK", "2025-07-15", 6.0, "investor"),
    ("STRK", "2025-10-15", 6.0, "investor"), ("STRK", "2026-01-15", 6.0, "investor"),
    # SEI - monthly ~3%
    ("SEI", "2025-04-15", 3.0, "investor+team"), ("SEI", "2025-05-15", 3.0, "investor+team"),
    ("SEI", "2025-06-15", 3.0, "investor+team"), ("SEI", "2025-07-15", 3.0, "investor+team"),
    ("SEI", "2025-08-15", 3.0, "investor+team"), ("SEI", "2025-09-15", 3.0, "investor+team"),
    ("SEI", "2025-10-15", 3.0, "investor+team"), ("SEI", "2025-11-15", 3.0, "investor+team"),
    ("SEI", "2025-12-15", 3.0, "investor+team"), ("SEI", "2026-01-15", 3.0, "investor+team"),
    ("SEI", "2026-02-15", 3.0, "investor+team"),
    # EIGEN - monthly ~5%
    ("EIGEN", "2025-05-08", 5.0, "investor"), ("EIGEN", "2025-06-08", 5.0, "investor"),
    ("EIGEN", "2025-07-08", 5.0, "investor"), ("EIGEN", "2025-08-08", 5.0, "investor"),
    ("EIGEN", "2025-09-08", 5.0, "investor"), ("EIGEN", "2025-10-08", 5.0, "investor"),
    ("EIGEN", "2025-11-08", 5.0, "investor"), ("EIGEN", "2025-12-08", 5.0, "investor"),
    ("EIGEN", "2026-01-08", 5.0, "investor"), ("EIGEN", "2026-02-08", 5.0, "investor"),
    # WLD - big cliff
    ("WLD", "2025-07-24", 10.0, "investor cliff"),
    # ENA - quarterly
    ("ENA", "2025-04-02", 3.0, "investor+team"), ("ENA", "2025-07-02", 3.0, "investor+team"),
    ("ENA", "2025-10-02", 3.0, "investor+team"), ("ENA", "2026-01-02", 3.0, "investor+team"),
]


def fetch_hl_candles(coin: str, days: int = 400) -> dict[str, float]:
    """Fetch daily candles from Hyperliquid, return date→close mapping."""
    cache = os.path.join(DATA_DIR, f"{coin}_candles_1d.json")
    if os.path.exists(cache):
        age = (time.time() - os.path.getmtime(cache)) / 3600
        if age < 12:
            with open(cache) as f:
                candles = json.load(f)
            return {datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"): float(c["c"]) for c in candles}

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86400 * 1000
    payload = json.dumps({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1d", "startTime": start_ts, "endTime": end_ts
    }}).encode()
    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            candles = json.loads(resp.read())
    except Exception as e:
        print(f"  {coin}: failed to fetch candles: {e}")
        return {}

    if candles:
        with open(cache, "w") as f:
            json.dump(candles, f)

    return {datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc).strftime("%Y-%m-%d"): float(c["c"]) for c in candles}


def get_price(prices: dict, date_str: str, offset_range: int = 3) -> tuple[float, str] | tuple[None, None]:
    """Get price for a date, trying nearby dates if exact match not found."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for off in range(0, offset_range + 1):
        for sign in [0, 1, -1]:
            d = (dt + timedelta(days=off * sign)).strftime("%Y-%m-%d")
            if d in prices and prices[d] > 0:
                return prices[d], d
    return None, None


def main():
    print("=" * 60)
    print("  TOKEN UNLOCK BACKTEST v2")
    print("  Short before unlock, cover after")
    print("=" * 60)

    # Filter unlocks to backtest period (exclude future)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    valid_unlocks = [(tk, dt, pct, cat) for tk, dt, pct, cat in UNLOCKS
                     if datetime.strptime(dt, "%Y-%m-%d") < cutoff.replace(tzinfo=None)]
    print(f"\n  Unlock events to test: {len(valid_unlocks)}")
    print(f"  Tokens: {sorted(set(u[0] for u in valid_unlocks))}")

    # Fetch price data
    print("\nFetching Hyperliquid price data...")
    price_data = {}
    for ticker in sorted(set(u[0] for u in valid_unlocks)):
        prices = fetch_hl_candles(ticker)
        price_data[ticker] = prices
        n = len(prices)
        print(f"  {ticker}: {n} daily prices")
        time.sleep(0.5)

    # ── Test multiple configs ────────────────────────────────────
    configs = [
        ("A1. Short -1d / cover +3d", -1, 3, 0),
        ("A2. Short -2d / cover +5d", -2, 5, 0),
        ("A3. Short -3d / cover +7d", -3, 7, 0),
        ("A4. Short -1d / cover +7d", -1, 7, 0),
        ("A5. Short -2d / cover +3d", -2, 3, 0),
        ("A6. Short -1d / cover +1d", -1, 1, 0),
        ("A7. Short -5d / cover +10d", -5, 10, 0),
        # Big unlocks only (>3% of supply)
        ("B1. Big (>3%) -2d / +5d", -2, 5, 3.0),
        ("B2. Big (>3%) -1d / +7d", -1, 7, 3.0),
        ("B3. Big (>5%) -2d / +5d", -2, 5, 5.0),
        ("B4. Big (>5%) -3d / +10d", -3, 10, 5.0),
    ]

    all_results = []
    size = 250.0

    for label, entry_before, exit_after, min_pct in configs:
        trades = []
        for ticker, unlock_date, pct, category in valid_unlocks:
            if pct < min_pct:
                continue
            prices = price_data.get(ticker, {})
            if not prices:
                continue

            unlock_dt = datetime.strptime(unlock_date, "%Y-%m-%d")
            entry_dt = unlock_dt + timedelta(days=entry_before)
            exit_dt = unlock_dt + timedelta(days=exit_after)

            entry_price, entry_day = get_price(prices, entry_dt.strftime("%Y-%m-%d"))
            exit_price, exit_day = get_price(prices, exit_dt.strftime("%Y-%m-%d"))

            if not entry_price or not exit_price:
                continue

            # SHORT: profit when price drops
            gross_bps = -(exit_price / entry_price - 1) * 1e4
            net_bps = gross_bps - COST_BPS
            pnl = size * net_bps / 1e4

            trades.append({
                "ticker": ticker, "unlock_date": unlock_date,
                "pct": pct, "category": category,
                "entry": entry_day, "exit": exit_day,
                "entry_price": entry_price, "exit_price": exit_price,
                "gross_bps": round(gross_bps, 1), "net_bps": round(net_bps, 1),
                "pnl": round(pnl, 2),
            })

        # Analyze
        n = len(trades)
        if n == 0:
            print(f"\n  {label}: 0 trades")
            all_results.append({"label": label, "trades": 0, "pnl_usd": 0})
            continue

        wins = sum(1 for t in trades if t["net_bps"] > 0)
        avg_net = float(np.mean([t["net_bps"] for t in trades]))
        avg_gross = float(np.mean([t["gross_bps"] for t in trades]))
        total_pnl = sum(t["pnl"] for t in trades)

        print(f"\n{'═'*60}")
        print(f"  {label}")
        print(f"{'═'*60}")
        print(f"  Trades:    {n}")
        print(f"  Win rate:  {wins/n*100:.0f}%")
        print(f"  Gross avg: {avg_gross:+.1f} bps")
        print(f"  Net avg:   {avg_net:+.1f} bps (after {COST_BPS:.0f}bps cost)")
        print(f"  P&L:       ${total_pnl:+.2f} (at $250/trade)")

        # By ticker
        by_tk = defaultdict(list)
        for t in trades:
            by_tk[t["ticker"]].append(t)
        tk_stats = sorted([(tk, len(ts), float(np.mean([t["net_bps"] for t in ts])),
                            sum(t["pnl"] for t in ts)) for tk, ts in by_tk.items()],
                          key=lambda x: x[3], reverse=True)
        print(f"\n  {'Ticker':<8} {'Trades':>6} {'AvgNet':>8} {'P&L$':>9}")
        print(f"  {'-'*35}")
        for tk, cnt, avg, pnl in tk_stats:
            print(f"  {tk:<8} {cnt:>6} {avg:>+7.1f} ${pnl:>+8.2f} {'✓' if pnl > 0 else '✗'}")

        # By month
        by_month = defaultdict(list)
        for t in trades:
            by_month[t["unlock_date"][:7]].append(t)
        losing = sum(1 for ts in by_month.values() if sum(t["pnl"] for t in ts) <= 0)
        print(f"\n  Months: {len(by_month)-losing}/{len(by_month)} winning")

        # Show each trade
        if n <= 30:
            print(f"\n  {'Date':<12} {'Ticker':<8} {'Pct':>5} {'Gross':>8} {'Net':>8} {'P&L$':>8}")
            print(f"  {'-'*52}")
            for t in sorted(trades, key=lambda x: x["unlock_date"]):
                print(f"  {t['unlock_date']:<12} {t['ticker']:<8} {t['pct']:>4.1f}% "
                      f"{t['gross_bps']:>+7.1f} {t['net_bps']:>+7.1f} ${t['pnl']:>+7.2f}")

        all_results.append({
            "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
            "avg_net": avg_net, "avg_gross": avg_gross,
            "pnl_usd": total_pnl, "losing_months": losing, "total_months": len(by_month),
        })

    # ── SUMMARY ──────────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  SUMMARY — TOKEN UNLOCK STRATEGIES")
    print("█" * 70)
    print(f"\n  {'Config':<35} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'L.Mo':>5}")
    print(f"  {'-'*68}")
    for r in all_results:
        if r["trades"] == 0:
            continue
        m = "✓" if r.get("pnl_usd", 0) > 0 else "✗"
        lm = f"{r.get('losing_months','?')}/{r.get('total_months','?')}"
        print(f"  {r['label']:<35} {r['trades']:>6} {r.get('win_rate',0)*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r.get('pnl_usd',0):>+8.2f} {lm:>5} {m}")

    valid = [r for r in all_results if r["trades"] >= 5]
    if valid:
        best = max(valid, key=lambda r: r.get("pnl_usd", -9999))
        print(f"\n  BEST: {best['label']} → ${best.get('pnl_usd',0):+.2f}")


if __name__ == "__main__":
    main()
