"""Build per-coin `*_oi_4h.json` files used by `backtest_rolling`.

Two data sources, merged in priority order:
    1. `backtests/output/oi_history.db` — long history from Hyperliquid S3 archive
       (typically 12-day lag from current — refresh via fetch_oi_history.py)
    2. `analysis/output_live/reversal_ticks.db` `market_snapshots` — recent
       hourly OI captured live by the running bot (fills the lag of source #1)

Output: 4h-aligned series at UTC 00, 04, 08, 12, 16, 20.

Usage:
    python3 -m backtests.convert_oi_to_json
    python3 -m backtests.convert_oi_to_json --symbols BTC,ETH
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import OrderedDict

from .backtest_genetic import TOKENS, REF_TOKENS

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
ARCHIVE_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")
LIVE_DB = "/home/crypto/analysis/output_live/reversal_ticks.db"
INTERVAL_S = 4 * 3600


def load_archive(coin: str) -> list:
    """Return [(ts_s, oi)] from S3 archive DB (ts in seconds)."""
    if not os.path.exists(ARCHIVE_DB):
        return []
    con = sqlite3.connect(ARCHIVE_DB)
    rows = con.execute(
        "SELECT ts, oi FROM asset_ctx WHERE symbol = ? AND oi > 0 ORDER BY ts",
        (coin,)).fetchall()
    con.close()
    return [(int(r[0]), float(r[1])) for r in rows]


def load_live(coin: str) -> list:
    """Return [(ts_s, oi)] from live bot's market_snapshots table.

    Schema: ts INTEGER (seconds), symbol TEXT, oi REAL, ...
    """
    if not os.path.exists(LIVE_DB):
        return []
    con = sqlite3.connect(LIVE_DB)
    try:
        rows = con.execute(
            "SELECT ts, oi FROM market_snapshots WHERE symbol = ? AND oi > 0 ORDER BY ts",
            (coin,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    return [(int(r[0]), float(r[1])) for r in rows]


def downsample_4h(rows: list) -> list:
    """Snap each (ts_s, oi) to its 4h-aligned bucket; keep the latest per bucket.

    Buckets are aligned to UTC 00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00.
    """
    by_bucket: OrderedDict[int, float] = OrderedDict()
    for ts_s, oi in rows:
        bucket = (ts_s // INTERVAL_S) * INTERVAL_S
        by_bucket[bucket] = oi  # later overwrites earlier
    return sorted(by_bucket.items())


def merge_sources(coin: str) -> list:
    """Archive (long history) + live (recent days), merged & deduped 4h-aligned."""
    archive = load_archive(coin)
    live = load_live(coin)
    combined = archive + live
    if not combined:
        return []
    combined.sort()
    bucketed = downsample_4h(combined)
    return [{"t": ts * 1000, "oi": oi} for ts, oi in bucketed]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="Comma-separated subset; default = all bot tokens")
    args = ap.parse_args()

    symbols = (args.symbols.split(",") if args.symbols
               else TOKENS + REF_TOKENS)
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"Merging OI sources for {len(symbols)} tokens...")
    print(f"  archive: {ARCHIVE_DB}")
    print(f"  live:    {LIVE_DB}")
    written = 0
    for coin in symbols:
        rows = merge_sources(coin)
        if not rows:
            print(f"  {coin:<6} (no data, skipped)")
            continue
        path = os.path.join(DATA_DIR, f"{coin}_oi_4h.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rows, f)
        os.replace(tmp, path)
        first = time.strftime("%Y-%m-%d", time.gmtime(rows[0]["t"] / 1000))
        last = time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[-1]["t"] / 1000))
        print(f"  {coin:<6} {len(rows):>5} pts  ({first} → {last})")
        written += 1
    print(f"\nDone. {written} files updated.")


if __name__ == "__main__":
    main()
