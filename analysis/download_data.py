"""Download Binance Futures historical data for backtesting.

Usage:
    python3 -m analysis.download_data --days 90
    python3 -m analysis.download_data --days 30 --symbols ADAUSDT BTCUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import time
from datetime import datetime, timezone, timedelta

import aiohttp

from analysis.livebot import TRADE_SYMBOLS_LIST, REFERENCE_SYMBOLS

ALL_BACKTEST_SYMBOLS = [s.upper() for s in REFERENCE_SYMBOLS] + TRADE_SYMBOLS_LIST

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")

# Binance API endpoints
BASE = "https://fapi.binance.com"
ENDPOINTS = {
    "klines":    "/fapi/v1/klines",
    "oi":        "/futures/data/openInterestHist",
    "funding":   "/fapi/v1/fundingRate",
    "ls_global": "/futures/data/globalLongShortAccountRatio",
    "ls_top":    "/futures/data/topLongShortPositionRatio",
}

# Rate limiting
SEM_LIMIT = 5
DELAY = 0.2  # seconds between requests


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict,
                     sem: asyncio.Semaphore) -> list:
    """Fetch JSON from Binance with rate limiting and retry."""
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        await asyncio.sleep(DELAY)
                        return data if isinstance(data, list) else []
                    elif resp.status == 429:
                        print(f"    Rate limited, waiting 30s...")
                        await asyncio.sleep(30)
                    elif resp.status == 418:
                        print(f"    IP banned, waiting 120s...")
                        await asyncio.sleep(120)
                    else:
                        text = await resp.text()
                        if attempt == 2:
                            print(f"    HTTP {resp.status}: {text[:100]}")
                        await asyncio.sleep(2)
            except Exception as e:
                if attempt == 2:
                    print(f"    Error: {e}")
                await asyncio.sleep(2)
    return []


async def download_klines(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                          symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Download 1m klines for a symbol."""
    url = BASE + ENDPOINTS["klines"]
    rows = []
    current = start_ms
    limit = 1500

    while current < end_ms:
        params = {"symbol": symbol, "interval": "1m", "startTime": current,
                  "endTime": end_ms, "limit": limit}
        data = await fetch_json(session, url, params, sem)
        if not data:
            break
        for k in data:
            rows.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        current = int(data[-1][0]) + 60000  # next minute
        if len(data) < limit:
            break
    return rows


async def download_oi(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                      symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Download 5m OI history. Binance limits to ~30 days, so we chunk by 28 days."""
    url = BASE + ENDPOINTS["oi"]
    rows = []
    chunk_ms = 28 * 86400 * 1000  # 28 days
    limit = 500

    chunk_start = start_ms
    while chunk_start < end_ms:
        chunk_end = min(chunk_start + chunk_ms, end_ms)
        current = chunk_start
        while current < chunk_end:
            params = {"symbol": symbol, "period": "5m", "startTime": current,
                      "endTime": chunk_end, "limit": limit}
            data = await fetch_json(session, url, params, sem)
            if not data:
                break
            for d in data:
                rows.append({
                    "timestamp": int(d.get("timestamp", 0)),
                    "oi": float(d.get("sumOpenInterest", 0)),
                })
            current = int(data[-1].get("timestamp", current)) + 300000
            if len(data) < limit:
                break
        chunk_start = chunk_end
    return rows


async def download_funding(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                           symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Download funding rate history."""
    url = BASE + ENDPOINTS["funding"]
    rows = []
    current = start_ms
    limit = 1000

    while current < end_ms:
        params = {"symbol": symbol, "startTime": current, "endTime": end_ms, "limit": limit}
        data = await fetch_json(session, url, params, sem)
        if not data:
            break
        for d in data:
            rows.append({
                "timestamp": int(d.get("fundingTime", 0)),
                "funding_rate": float(d.get("fundingRate", 0)),
            })
        current = int(data[-1].get("fundingTime", current)) + 1
        if len(data) < limit:
            break
    return rows


async def download_ls_ratio(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                            symbol: str, start_ms: int, end_ms: int,
                            endpoint_key: str) -> list[dict]:
    """Download long/short ratio history. Binance limits to ~30 days, chunk by 28."""
    url = BASE + ENDPOINTS[endpoint_key]
    rows = []
    chunk_ms = 28 * 86400 * 1000
    limit = 500

    chunk_start = start_ms
    while chunk_start < end_ms:
        chunk_end = min(chunk_start + chunk_ms, end_ms)
        current = chunk_start
        while current < chunk_end:
            params = {"symbol": symbol, "period": "5m", "startTime": current,
                      "endTime": chunk_end, "limit": limit}
            data = await fetch_json(session, url, params, sem)
            if not data:
                break
            for d in data:
                rows.append({
                    "timestamp": int(d.get("timestamp", 0)),
                    "long_account": float(d.get("longAccount", 0.5)),
                })
            current = int(data[-1].get("timestamp", current)) + 300000
            if len(data) < limit:
                break
        chunk_start = chunk_end
    return rows


def save_csv(rows: list[dict], filepath: str):
    """Save list of dicts to CSV."""
    if not rows:
        return
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def csv_is_fresh(filepath: str, end_ms: int) -> bool:
    """Check if existing CSV covers the requested period (within 1 day)."""
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath) as f:
            reader = csv.DictReader(f)
            last_ts = 0
            for row in reader:
                last_ts = int(row["timestamp"])
            return last_ts > (end_ms - 86400000)  # within 1 day
    except Exception:
        return False


async def download_symbol(session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                          symbol: str, start_ms: int, end_ms: int,
                          is_reference: bool = False):
    """Download all data types for one symbol."""
    prefix = os.path.join(OUTPUT_DIR, symbol)

    # Klines (always needed)
    kline_path = f"{prefix}_klines_1m.csv"
    if csv_is_fresh(kline_path, end_ms):
        print(f"  {symbol} klines: cached")
    else:
        print(f"  {symbol} klines: downloading...")
        rows = await download_klines(session, sem, symbol, start_ms, end_ms)
        save_csv(rows, kline_path)
        print(f"  {symbol} klines: {len(rows)} rows")

    # OI
    oi_path = f"{prefix}_oi_5m.csv"
    if csv_is_fresh(oi_path, end_ms):
        print(f"  {symbol} OI: cached")
    else:
        print(f"  {symbol} OI: downloading...")
        rows = await download_oi(session, sem, symbol, start_ms, end_ms)
        save_csv(rows, oi_path)
        print(f"  {symbol} OI: {len(rows)} rows")

    # Funding
    fund_path = f"{prefix}_funding.csv"
    if csv_is_fresh(fund_path, end_ms):
        print(f"  {symbol} funding: cached")
    else:
        print(f"  {symbol} funding: downloading...")
        rows = await download_funding(session, sem, symbol, start_ms, end_ms)
        save_csv(rows, fund_path)
        print(f"  {symbol} funding: {len(rows)} rows")

    # L/S ratios (only for traded symbols, not reference)
    if not is_reference:
        for key, label in [("ls_global", "L/S global"), ("ls_top", "L/S top")]:
            ls_path = f"{prefix}_{key}_5m.csv"
            if csv_is_fresh(ls_path, end_ms):
                print(f"  {symbol} {label}: cached")
            else:
                print(f"  {symbol} {label}: downloading...")
                rows = await download_ls_ratio(session, sem, symbol, start_ms, end_ms, key)
                save_csv(rows, ls_path)
                print(f"  {symbol} {label}: {len(rows)} rows")


async def main():
    parser = argparse.ArgumentParser(description="Download Binance Futures historical data")
    parser.add_argument("--days", type=int, default=90, help="Number of days to download (default: 90)")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to download (default: all)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=args.days)).timestamp() * 1000)

    symbols = args.symbols or ALL_BACKTEST_SYMBOLS
    ref_set = {s.upper() for s in REFERENCE_SYMBOLS}

    print(f"Downloading {args.days} days of data for {len(symbols)} symbols")
    print(f"Period: {now - timedelta(days=args.days):%Y-%m-%d} → {now:%Y-%m-%d}")
    print(f"Output: {OUTPUT_DIR}")
    print()

    sem = asyncio.Semaphore(SEM_LIMIT)
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        for i, sym in enumerate(symbols, 1):
            print(f"[{i}/{len(symbols)}] {sym}")
            is_ref = sym in ref_set
            await download_symbol(session, sem, sym, start_ms, end_ms, is_reference=is_ref)

    elapsed = time.time() - t0
    total_size = sum(
        os.path.getsize(os.path.join(OUTPUT_DIR, f))
        for f in os.listdir(OUTPUT_DIR) if f.endswith(".csv")
    ) if os.path.exists(OUTPUT_DIR) else 0

    print(f"\nDone in {elapsed:.0f}s | {total_size / 1e6:.1f} MB | {len(symbols)} symbols × {args.days} days")


if __name__ == "__main__":
    asyncio.run(main())
