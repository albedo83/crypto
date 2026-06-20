"""Refresh fine-grained candle history (`*_{interval}_3y.json`) for backtest tokens.

Parametrized clone of fetch_4h_candles for the MFE-realism / exit-frequency study
(étape B). HL serves ~200 days of 1h candles and less for 15m — we fetch as far back
as the API returns. Idempotent (merge + dedupe by ts).

Usage:
    python3 -m backtests.fetch_fine_candles --interval 1h
    python3 -m backtests.fetch_fine_candles --interval 15m --symbols BTC,ETH
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

_INTERVAL_MS = {"1h": 3600 * 1000, "15m": 15 * 60 * 1000, "5m": 5 * 60 * 1000}


def fetch_window(coin, interval, start_ms, end_ms):
    payload = json.dumps({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval,
                "startTime": start_ms, "endTime": end_ms},
    }).encode()
    req = urllib.request.Request(HL_API, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()) or []


def fetch_all(coin, interval, start_ms, end_ms):
    step = _INTERVAL_MS[interval]
    out, cursor = [], start_ms
    while cursor < end_ms:
        chunk_end = min(end_ms, cursor + 5000 * step)
        chunk = fetch_window(coin, interval, cursor, chunk_end)
        if not chunk:
            break
        out.extend(chunk)
        last_ts = chunk[-1]["t"]
        if last_ts <= cursor:
            break
        cursor = last_ts + step
        time.sleep(0.15)
    return out


def update_token(coin, interval, full=False):
    step = _INTERVAL_MS[interval]
    path = os.path.join(DATA_DIR, f"{coin}_{interval}_3y.json")
    existing = []
    if not full and os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)
    now_ms = int(time.time() * 1000)
    if existing:
        last_ts = max(int(c["t"]) for c in existing)
        start_ms = last_ts - 4 * step
    else:
        # HL serves only ~200d of fine candles; starting 3y back returns an empty
        # first chunk and breaks. Cap the fresh lookback to 250d.
        start_ms = now_ms - 250 * 86400 * 1000
    if start_ms >= now_ms - step:
        return {"coin": coin, "added": 0, "total": len(existing), "status": "fresh"}
    try:
        new_candles = fetch_all(coin, interval, start_ms, now_ms)
    except Exception as e:
        return {"coin": coin, "added": 0, "total": len(existing), "status": f"error: {e}"}
    if existing and len(new_candles) < 2:
        return {"coin": coin, "added": 0, "total": len(existing),
                "status": f"partial_skipped ({len(new_candles)})"}
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
    ap.add_argument("--interval", default="1h", choices=list(_INTERVAL_MS))
    ap.add_argument("--symbols", help="Comma-separated subset; default = all")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    symbols = args.symbols.split(",") if args.symbols else TOKENS + REF_TOKENS
    print(f"Refreshing {args.interval} candles for {len(symbols)} tokens "
          f"({'FULL' if args.full else 'incremental'})...", flush=True)
    total = 0
    for coin in symbols:
        r = update_token(coin, args.interval, full=args.full)
        total += r.get("added", 0)
        print(f"  {coin:<6} {r['status']:<22} +{r['added']:<5} (total {r['total']})", flush=True)
        time.sleep(0.1)
    print(f"\nDone. {total} new candles total.")


if __name__ == "__main__":
    main()
