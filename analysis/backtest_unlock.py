"""Token Unlock backtest — 1 year.

Strategy: Short tokens before large unlock events (vesting, cliff).
VC/team tokens unlock → selling pressure → price drops.

Data sources:
- DeFiLlama emissions breakdown (unlock schedules)
- Hyperliquid candles (price data, 1d)

Usage:
    python3 -m analysis.backtest_unlock
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "unlock_data")
os.makedirs(DATA_DIR, exist_ok=True)

# Mapping: DeFiLlama protocol name → Hyperliquid ticker
PROTOCOL_MAP = {
    "arbitrum": "ARB",
    "optimism": "OP",
    "aptos": "APT",
    "sui": "SUI",
    "celestia": "TIA",
    "jito": "JTO",
    "pyth": "PYTH",
    "wormhole": "W",
    "starknet": "STRK",
    "eigenlayer": "EIGEN",
    "layerzero": "ZRO",
    "altlayer": "ALT",
    "manta-network": "MANTA",
    "dymension": "DYM",
    "ondo-finance": "ONDO",
    "ethena": "ENA",
    "pendle": "PENDLE",
    "worldcoin": "WLD",
    "blur": "BLUR",
    "dydx": "DYDX",
    "immutable-x": "IMX",
    "the-sandbox": "SAND",
    "axie-infinity": "AXS",
    "gala": "GALA",
    "jupiter": "JUP",
    "sei-network": "SEI",
}

# Costs on Hyperliquid
MAKER_FEE_BPS = 1.0     # maker per side
TAKER_FEE_BPS = 3.5     # taker per side
COST_BPS = TAKER_FEE_BPS * 2  # 7 bps roundtrip (taker both sides, conservative)


def fetch_json(url: str, payload=None, retries=3) -> dict | list | None:
    for attempt in range(retries):
        try:
            if payload:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(2 ** attempt)
    return None


# ── Data Collection ──────────────────────────────────────────────────

def fetch_emissions_breakdown() -> dict:
    """Fetch DeFiLlama emissions breakdown (all protocols)."""
    cache = os.path.join(DATA_DIR, "emissions_breakdown.json")
    if os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 24:
            with open(cache) as f:
                return json.load(f)

    print("Fetching DeFiLlama emissions breakdown...")
    data = fetch_json("https://defillama-datasets.llama.fi/emissionsBreakdown")
    if data:
        with open(cache, "w") as f:
            json.dump(data, f)
        print(f"  Got {len(data)} protocols")
    return data or {}


def parse_unlock_events(emissions: dict) -> list[dict]:
    """Parse emissions data into unlock events with dates and amounts."""
    events = []

    for protocol, pdata in emissions.items():
        ticker = PROTOCOL_MAP.get(protocol)
        if not ticker:
            continue

        # DeFiLlama emissions format: categorized time series
        # Each category has a list of [timestamp, amount] pairs
        categories = pdata.get("categories", {})
        if not categories and isinstance(pdata, dict):
            # Try flat format
            for key, vals in pdata.items():
                if isinstance(vals, list) and len(vals) > 0:
                    if isinstance(vals[0], list) and len(vals[0]) == 2:
                        categories[key] = vals

        for category, series in categories.items():
            if not isinstance(series, list):
                continue
            cat_lower = category.lower()
            # Focus on categories that represent actual token unlocks
            is_unlock = any(kw in cat_lower for kw in [
                "investor", "team", "advisor", "private", "seed", "series",
                "foundation", "ecosystem", "treasury", "strategic",
                "contributor", "insider", "venture", "vc",
            ])

            for point in series:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                ts, amount = point[0], point[1]
                if not isinstance(ts, (int, float)) or not isinstance(amount, (int, float)):
                    continue
                if amount <= 0:
                    continue

                # Convert timestamp (could be seconds or milliseconds)
                if ts > 1e12:
                    ts = ts / 1000
                try:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                except (ValueError, OSError):
                    continue

                events.append({
                    "protocol": protocol,
                    "ticker": ticker,
                    "date": dt,
                    "amount_tokens": amount,
                    "category": category,
                    "is_investor_unlock": is_unlock,
                })

    return events


def fetch_hl_candles(coin: str, days: int = 400) -> list[dict]:
    """Fetch daily candles from Hyperliquid."""
    cache = os.path.join(DATA_DIR, f"{coin}_candles_1d.json")
    if os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:
            with open(cache) as f:
                return json.load(f)

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86400 * 1000

    data = fetch_json("https://api.hyperliquid.xyz/info", {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": "1d", "startTime": start_ts, "endTime": end_ts}
    })

    if data:
        with open(cache, "w") as f:
            json.dump(data, f)

    return data or []


def build_price_index(coin: str) -> dict[str, float]:
    """Build date → close price mapping."""
    candles = fetch_hl_candles(coin)
    prices = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        prices[day_str] = float(c["c"])
    return prices


# ── Backtest ─────────────────────────────────────────────────────────

@dataclass
class Trade:
    ticker: str
    protocol: str
    category: str
    unlock_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    hold_days: int
    gross_bps: float
    net_bps: float
    pnl_pct: float
    unlock_size_pct: float  # unlock as % of circulating supply (if known)


def backtest_unlock_strategy(events: list[dict],
                              entry_days_before: int = 2,
                              exit_days_after: int = 5,
                              min_unlock_tokens: float = 0,
                              only_investor: bool = False,
                              size_usdt: float = 250.0) -> list[Trade]:
    """
    Short before unlock, cover after.
    entry_days_before: days before unlock to enter short
    exit_days_after: days after unlock to exit
    """
    # Group events by ticker and date (aggregate same-day unlocks)
    grouped = defaultdict(lambda: defaultdict(lambda: {"total": 0, "categories": [], "is_investor": False}))
    for e in events:
        day_str = e["date"].strftime("%Y-%m-%d")
        g = grouped[e["ticker"]][day_str]
        g["total"] += e["amount_tokens"]
        g["categories"].append(e["category"])
        if e["is_investor_unlock"]:
            g["is_investor"] = True
        g["protocol"] = e["protocol"]

    # Load prices for each ticker
    price_cache = {}
    trades = []

    for ticker in grouped:
        if ticker not in price_cache:
            prices = build_price_index(ticker)
            if not prices:
                continue
            price_cache[ticker] = prices
            time.sleep(0.3)

        prices = price_cache[ticker]
        if not prices:
            continue

        for unlock_day, info in grouped[ticker].items():
            if only_investor and not info["is_investor"]:
                continue
            if info["total"] < min_unlock_tokens:
                continue

            # Calculate entry and exit dates
            unlock_dt = datetime.strptime(unlock_day, "%Y-%m-%d")
            entry_dt = unlock_dt - timedelta(days=entry_days_before)
            exit_dt = unlock_dt + timedelta(days=exit_days_after)

            entry_day = entry_dt.strftime("%Y-%m-%d")
            exit_day = exit_dt.strftime("%Y-%m-%d")

            # Get prices (find nearest available)
            entry_price = None
            for offset in range(0, 4):
                d = (entry_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                if d in prices:
                    entry_price = prices[d]
                    entry_day = d
                    break
            exit_price = None
            for offset in range(0, 4):
                d = (exit_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                if d in prices:
                    exit_price = prices[d]
                    exit_day = d
                    break

            if not entry_price or not exit_price or entry_price == 0:
                continue

            # SHORT: profit when price goes down
            gross_bps = -(exit_price / entry_price - 1) * 1e4  # negative price change = positive for short
            net_bps = gross_bps - COST_BPS
            pnl_pct = net_bps / 1e4 * 100

            trades.append(Trade(
                ticker=ticker, protocol=info["protocol"],
                category="; ".join(set(info["categories"]))[:50],
                unlock_date=unlock_day, entry_date=entry_day, exit_date=exit_day,
                entry_price=entry_price, exit_price=exit_price,
                hold_days=entry_days_before + exit_days_after,
                gross_bps=round(gross_bps, 2), net_bps=round(net_bps, 2),
                pnl_pct=round(pnl_pct, 4), unlock_size_pct=0,
            ))

    return trades


def analyze(trades: list[Trade], label: str) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0}

    wins = sum(1 for t in trades if t.net_bps > 0)
    avg_net = float(np.mean([t.net_bps for t in trades]))
    avg_gross = float(np.mean([t.gross_bps for t in trades]))
    total_pnl_bps = sum(t.net_bps for t in trades)

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:      {n}")
    print(f"  Win rate:    {wins/n*100:.0f}%")
    print(f"  Gross avg:   {avg_gross:+.1f} bps")
    print(f"  Net avg:     {avg_net:+.1f} bps (after {COST_BPS:.0f} bps cost)")
    print(f"  Total:       {total_pnl_bps:+.0f} bps")

    # If we invest $250 per trade
    size = 250.0
    total_pnl_usd = sum(size * t.net_bps / 1e4 for t in trades)
    print(f"  P&L ($250/t): ${total_pnl_usd:+.2f}")

    # By ticker
    by_ticker = defaultdict(list)
    for t in trades:
        by_ticker[t.ticker].append(t)
    ticker_stats = [(tk, len(ts), float(np.mean([t.net_bps for t in ts])),
                      sum(size * t.net_bps / 1e4 for t in ts))
                     for tk, ts in by_ticker.items()]
    ticker_stats.sort(key=lambda x: x[3], reverse=True)

    print(f"\n  {'Ticker':<8} {'Trades':>7} {'AvgNet':>8} {'P&L$':>9}")
    print(f"  {'-'*35}")
    for tk, cnt, avg, pnl in ticker_stats:
        marker = "✓" if pnl > 0 else "✗"
        print(f"  {tk:<8} {cnt:>7} {avg:>+7.1f} ${pnl:>+8.2f} {marker}")

    # By month
    by_month = defaultdict(list)
    for t in trades:
        by_month[t.unlock_date[:7]].append(t)
    print(f"\n  {'Month':<10} {'Trades':>7} {'AvgNet':>8} {'P&L$':>9}")
    print(f"  {'-'*38}")
    losing_months = 0
    for m in sorted(by_month.keys()):
        mt = by_month[m]
        mavg = float(np.mean([t.net_bps for t in mt]))
        mpnl = sum(size * t.net_bps / 1e4 for t in mt)
        marker = "✓" if mpnl > 0 else "✗"
        if mpnl <= 0:
            losing_months += 1
        print(f"  {m:<10} {len(mt):>7} {mavg:>+7.1f} ${mpnl:>+8.2f} {marker}")

    print(f"\n  Losing months: {losing_months}/{len(by_month)}")

    # By category type (investor vs other)
    investor_trades = [t for t in trades if any(kw in t.category.lower() for kw in
                       ['investor', 'team', 'advisor', 'private', 'seed', 'venture', 'vc', 'strategic'])]
    other_trades = [t for t in trades if t not in investor_trades]

    if investor_trades:
        inv_avg = float(np.mean([t.net_bps for t in investor_trades]))
        inv_pnl = sum(size * t.net_bps / 1e4 for t in investor_trades)
        print(f"\n  Investor/Team unlocks: {len(investor_trades)} trades, {inv_avg:+.1f} bps, ${inv_pnl:+.2f}")
    if other_trades:
        oth_avg = float(np.mean([t.net_bps for t in other_trades]))
        oth_pnl = sum(size * t.net_bps / 1e4 for t in other_trades)
        print(f"  Other unlocks:        {len(other_trades)} trades, {oth_avg:+.1f} bps, ${oth_pnl:+.2f}")

    # Top 10 best/worst individual trades
    sorted_trades = sorted(trades, key=lambda t: t.net_bps, reverse=True)
    print(f"\n  Top 5 winners:")
    for t in sorted_trades[:5]:
        print(f"    {t.ticker:<8} {t.unlock_date} | {t.net_bps:>+7.1f} bps | {t.category[:30]}")
    print(f"  Top 5 losers:")
    for t in sorted_trades[-5:]:
        print(f"    {t.ticker:<8} {t.unlock_date} | {t.net_bps:>+7.1f} bps | {t.category[:30]}")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_net": avg_net, "avg_gross": avg_gross,
        "total_bps": total_pnl_bps, "pnl_usd": total_pnl_usd,
        "losing_months": losing_months, "total_months": len(by_month),
    }


def main():
    print("=" * 60)
    print("  TOKEN UNLOCK BACKTEST — Short before vesting unlocks")
    print("=" * 60)

    # ── Fetch data ───────────────────────────────────────────────
    emissions = fetch_emissions_breakdown()
    if not emissions:
        print("ERROR: Could not fetch emissions data")
        return

    events = parse_unlock_events(emissions)
    print(f"\nParsed {len(events)} unlock events across {len(set(e['ticker'] for e in events))} tokens")

    # Filter to last year
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    events = [e for e in events if e["date"] > cutoff and e["date"] < datetime.now(timezone.utc) - timedelta(days=7)]
    print(f"After date filter (last year, excluding last 7d): {len(events)} events")

    tickers = set(e["ticker"] for e in events)
    print(f"Tokens: {sorted(tickers)}")

    # Event stats
    by_type = defaultdict(int)
    for e in events:
        by_type["investor" if e["is_investor_unlock"] else "other"] += 1
    print(f"Investor/team unlocks: {by_type.get('investor', 0)}")
    print(f"Other unlocks: {by_type.get('other', 0)}")

    results = []

    # ── Strategy configs ─────────────────────────────────────────
    print("\n" + "▓" * 60)
    print("  BACKTEST RESULTS")
    print("▓" * 60)

    configs = [
        ("A1. All unlocks: short -2d, cover +5d", {"entry_days_before": 2, "exit_days_after": 5}),
        ("A2. All unlocks: short -1d, cover +3d", {"entry_days_before": 1, "exit_days_after": 3}),
        ("A3. All unlocks: short -3d, cover +7d", {"entry_days_before": 3, "exit_days_after": 7}),
        ("A4. All unlocks: short -1d, cover +1d", {"entry_days_before": 1, "exit_days_after": 1}),
        ("B1. Investor only: -2d / +5d", {"entry_days_before": 2, "exit_days_after": 5, "only_investor": True}),
        ("B2. Investor only: -1d / +3d", {"entry_days_before": 1, "exit_days_after": 3, "only_investor": True}),
        ("B3. Investor only: -3d / +7d", {"entry_days_before": 3, "exit_days_after": 7, "only_investor": True}),
        ("B4. Investor only: -1d / +7d", {"entry_days_before": 1, "exit_days_after": 7, "only_investor": True}),
    ]

    for label, kwargs in configs:
        print(f"\nRunning: {label}")
        trades = backtest_unlock_strategy(events, **kwargs)
        r = analyze(trades, label)
        results.append(r)

    # ── SUMMARY ──────────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  FINAL SUMMARY — TOKEN UNLOCK STRATEGIES")
    print("█" * 70)
    print(f"\n  {'Config':<40} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'L.Mo':>5}")
    print(f"  {'-'*72}")
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r.get("pnl_usd", 0) > 0 else "✗"
        lm = f"{r.get('losing_months', '?')}/{r.get('total_months', '?')}"
        print(f"  {r['label']:<40} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r.get('pnl_usd',0):>+8.2f} {lm:>5} {m}")

    valid = [r for r in results if r["trades"] >= 10]
    if valid:
        best = max(valid, key=lambda r: r.get("pnl_usd", -9999))
        print(f"\n  BEST: {best['label']} → ${best.get('pnl_usd',0):+.2f}")


if __name__ == "__main__":
    main()
