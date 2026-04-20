"""Download Hyperliquid funding rate + premium history for all bot tokens.

Stores hourly (fundingRate, premium) per symbol in SQLite. Re-runnable:
each token resumes from the last stored timestamp.

Usage:
    python3 -m backtests.fetch_funding_history
    python3 -m backtests.fetch_funding_history --symbols BTC,ETH
    python3 -m backtests.fetch_funding_history --since 2024-01-01

Output: backtests/output/funding_history.db
    funding (symbol TEXT, ts INTEGER, funding_rate REAL, premium REAL)
    PRIMARY KEY (symbol, ts)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

from hyperliquid.info import Info
from hyperliquid.utils import constants

from analysis.bot.config import ALL_SYMBOLS

DB_PATH = os.path.join(os.path.dirname(__file__), "output", "funding_history.db")
PAGE_SIZE = 500  # Hyperliquid hard cap per call
HOUR_MS = 3600 * 1000


def init_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS funding (
        symbol TEXT NOT NULL,
        ts INTEGER NOT NULL,
        funding_rate REAL NOT NULL,
        premium REAL,
        PRIMARY KEY (symbol, ts)
    ) WITHOUT ROWID""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_funding_ts ON funding(ts)")
    db.commit()
    return db


def ts_bounds(db: sqlite3.Connection, symbol: str) -> tuple[int, int]:
    row = db.execute("SELECT MIN(ts), MAX(ts) FROM funding WHERE symbol = ?", (symbol,)).fetchone()
    if not row or row[0] is None:
        return (0, 0)
    return (int(row[0]), int(row[1]))


def _paginate(info: Info, db: sqlite3.Connection, symbol: str, cursor: int, end_ms: int) -> int:
    """Paginate from cursor to end_ms, inserting with dedup. Returns rows inserted."""
    inserted = 0
    while cursor < end_ms:
        try:
            batch = info.funding_history(symbol, cursor, end_ms)
        except Exception as e:
            print(f"  ! {symbol} @ {cursor}: {e}", file=sys.stderr)
            time.sleep(2)
            continue
        if not batch:
            break
        rows = [(symbol, int(r["time"]), float(r["fundingRate"]), float(r.get("premium", 0)))
                for r in batch]
        db.executemany(
            "INSERT OR IGNORE INTO funding (symbol, ts, funding_rate, premium) VALUES (?, ?, ?, ?)",
            rows,
        )
        db.commit()
        inserted += len(rows)
        last = int(batch[-1]["time"])
        if len(batch) < PAGE_SIZE:
            break  # exhausted
        cursor = last + 1  # next page starts strictly after last row
        time.sleep(0.15)  # be polite to the API
    return inserted


def fetch_symbol(info: Info, db: sqlite3.Connection, symbol: str, start_ms: int, end_ms: int) -> int:
    """Fetch missing rows for symbol covering [start_ms, end_ms].

    Backfills any gap before existing MIN, then extends after MAX. INSERT OR IGNORE
    handles overlap so the worst case is a duplicate page request (~no harm).
    """
    mn, mx = ts_bounds(db, symbol)
    inserted = 0
    if mn == 0:  # empty: single forward sweep
        return _paginate(info, db, symbol, start_ms, end_ms)
    if start_ms < mn:  # backfill older data
        inserted += _paginate(info, db, symbol, start_ms, mn)
    if mx + 1 < end_ms:  # extend forward
        inserted += _paginate(info, db, symbol, mx + 1, end_ms)
    return inserted


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="Comma-separated symbols (default: all bot tokens)")
    p.add_argument("--since", help="ISO date YYYY-MM-DD (default: 2023-01-01)")
    args = p.parse_args()

    symbols = args.symbols.split(",") if args.symbols else ALL_SYMBOLS
    if args.since:
        start_ms = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp() * 1000)
    else:
        start_ms = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(time.time() * 1000)

    db = init_db(DB_PATH)
    info = Info(constants.MAINNET_API_URL, skip_ws=True)

    print(f"Fetching {len(symbols)} symbols → {DB_PATH}")
    print(f"Range: {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).date()} → {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).date()}")
    total = 0
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        added = fetch_symbol(info, db, sym, start_ms, end_ms)
        _, after = ts_bounds(db, sym)
        n = db.execute("SELECT COUNT(*) FROM funding WHERE symbol = ?", (sym,)).fetchone()[0]
        last_str = datetime.fromtimestamp(after/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if after else "—"
        print(f"  [{i:2d}/{len(symbols)}] {sym:6s} +{added:5d} rows → {n:6d} total, last={last_str}")
        total += added
    elapsed = time.time() - t0
    print(f"\nDone: +{total} new rows in {elapsed:.0f}s. DB size: {os.path.getsize(DB_PATH)/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
