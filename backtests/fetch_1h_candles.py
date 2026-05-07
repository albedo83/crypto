"""Refresh 1h candle history (`*_1h_3y.json`) for all backtest tokens.

Same logic as fetch_4h_candles but for 1h interval. Used by the
hourly-granularity backtest variant (backtest_rolling_1h).

Usage:
    python3 -m backtests.fetch_1h_candles
    python3 -m backtests.fetch_1h_candles --symbols BTC,ETH
    python3 -m backtests.fetch_1h_candles --full  # ignore cache, refetch 3y
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

from .backtest_genetic import TOKENS, REF_TOKENS

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
HL_API = "https://api.hyperliquid.xyz/info"
INTERVAL = "1h"
INTERVAL_MS = 3600 * 1000


def fetch_window(coin: str, start_ms: int, end_ms: int) -> list:
    """Fetch one chunk of 1h candles (HL caps at 5000 per request)."""
    payload = json.dumps({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": INTERVAL,
                "startTime": start_ms, "endTime": end_ms},
    }).encode()
    req = urllib.request.Request(HL_API, data=payload,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()) or []


def fetch_all(coin: str, start_ms: int, end_ms: int) -> list:
    """Fetch (potentially multiple) windows until end_ms reached."""
    out: list = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(end_ms, cursor + 5000 * INTERVAL_MS)
        chunk = fetch_window(coin, cursor, chunk_end)
        if not chunk:
            break
        out.extend(chunk)
        last_ts = chunk[-1]["t"]
        if last_ts <= cursor:
            break
        cursor = last_ts + INTERVAL_MS
        time.sleep(0.15)
    return out


def update_token(coin: str, full: bool = False) -> dict:
    path = os.path.join(DATA_DIR, f"{coin}_1h_3y.json")
    existing: list = []
    if not full and os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)

    now_ms = int(time.time() * 1000)
    if existing:
        last_ts = max(int(c["t"]) for c in existing)
        start_ms = last_ts + INTERVAL_MS
    else:
        # HL public /info serves 1h candles back ~200 days only. Anything
        # older returns []. Start at 195 days back as a safe initial fetch.
        start_ms = now_ms - 195 * 86400 * 1000

    if start_ms >= now_ms - INTERVAL_MS:
        return {"coin": coin, "added": 0, "total": len(existing), "status": "fresh"}

    try:
        new_candles = fetch_all(coin, start_ms, now_ms)
    except Exception as e:
        return {"coin": coin, "added": 0, "total": len(existing),
                "status": f"error: {e}"}

    all_by_ts = {int(c["t"]): c for c in existing}
    for c in new_candles:
        all_by_ts[int(c["t"])] = c
    merged = sorted(all_by_ts.values(), key=lambda x: int(x["t"]))

    if merged:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(merged, f)
        os.replace(tmp, path)

    return {"coin": coin, "added": len(merged) - len(existing),
            "total": len(merged), "status": "updated"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="Comma-separated subset; default = all")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    symbols = (args.symbols.split(",") if args.symbols
               else TOKENS + REF_TOKENS)
    print(f"Refreshing 1h candles for {len(symbols)} tokens "
          f"({'FULL' if args.full else 'incremental'})...")

    total_added = 0
    for coin in symbols:
        r = update_token(coin, full=args.full)
        total_added += r.get("added", 0)
        print(f"  {coin:<6} {r['status']:<10} +{r['added']:<5} "
              f"(total {r['total']})")
        time.sleep(0.1)
    print(f"\nDone. {total_added} new candles total.")


if __name__ == "__main__":
    main()
