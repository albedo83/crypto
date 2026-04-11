"""Download Hyperliquid asset_ctxs history from S3 (Requester Pays).

Each daily file contains minute-resolution snapshots of every Hyperliquid
perp token (funding, open_interest, premium, mark_px, volume, impact bid/ask).
We filter to the bot's 30 tokens and downsample to hourly (first minute of
each hour kept) to keep storage manageable.

Usage:
    python3 -m backtests.fetch_oi_history
    python3 -m backtests.fetch_oi_history --since 2024-01-01
    python3 -m backtests.fetch_oi_history --workers 20

Output: backtests/output/oi_history.db
    asset_ctx (ts INTEGER, symbol TEXT, oi REAL, funding REAL, premium REAL,
               mark_px REAL, oracle_px REAL, day_ntl_vlm REAL,
               impact_bid REAL, impact_ask REAL)
    PRIMARY KEY (symbol, ts)

AWS credentials read from .env via AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
"""

from __future__ import annotations

import argparse
import io
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3
import lz4.frame

from analysis.bot.config import ALL_SYMBOLS

BUCKET = "hyperliquid-archive"
PREFIX = "asset_ctxs/"
DB_PATH = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")
SYMBOLS = set(ALL_SYMBOLS)


def load_aws_creds() -> None:
    """Read AWS creds from .env into os.environ (boto3 picks them up)."""
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        raise RuntimeError(".env not found and AWS_ACCESS_KEY_ID not set")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and line.startswith("AWS_"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")


def init_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("""CREATE TABLE IF NOT EXISTS asset_ctx (
        ts INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        oi REAL,
        funding REAL,
        premium REAL,
        mark_px REAL,
        oracle_px REAL,
        day_ntl_vlm REAL,
        impact_bid REAL,
        impact_ask REAL,
        PRIMARY KEY (symbol, ts)
    ) WITHOUT ROWID""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_asset_ctx_ts ON asset_ctx(ts)")
    db.execute("""CREATE TABLE IF NOT EXISTS fetch_log (
        date TEXT PRIMARY KEY,
        rows INTEGER,
        fetched_at TEXT
    )""")
    db.commit()
    return db


def done_dates(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT date FROM fetch_log")}


def list_available_dates(s3, since: str | None) -> list[str]:
    """Return sorted list of YYYYMMDD strings present in the bucket."""
    dates = []
    paginator = s3.get_paginator("list_objects_v2")
    since_key = f"{PREFIX}{since.replace('-', '')}.csv.lz4" if since else None
    for page in paginator.paginate(
        Bucket=BUCKET, Prefix=PREFIX,
        RequestPayer="requester",
        StartAfter=since_key or PREFIX,
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # asset_ctxs/YYYYMMDD.csv.lz4
            fname = key[len(PREFIX):]
            if fname.endswith(".csv.lz4") and len(fname) == len("YYYYMMDD.csv.lz4"):
                dates.append(fname[:8])
    return sorted(set(dates))


def parse_timestamp(s: str) -> int:
    """2024-01-01T00:00:00Z → epoch seconds."""
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def parse_file(raw: bytes) -> list[tuple]:
    """Decompress, filter to our symbols, downsample to hourly (HH:00 only)."""
    text = lz4.frame.decompress(raw).decode("utf-8")
    lines = text.split("\n")
    header = lines[0].split(",")
    # Find column indices
    col = {name: i for i, name in enumerate(header)}
    required = ["time", "coin", "funding", "open_interest", "premium",
                "mark_px", "oracle_px", "day_ntl_vlm",
                "impact_bid_px", "impact_ask_px"]
    idx = {k: col[k] for k in required}

    rows = []
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split(",")
        if len(fields) < len(header):
            continue
        coin = fields[idx["coin"]]
        if coin not in SYMBOLS:
            continue
        ts_str = fields[idx["time"]]
        # Only keep HH:00 (downsample from 1min to 1h)
        if not ts_str.endswith(":00:00Z"):
            continue
        try:
            ts = parse_timestamp(ts_str)
            rows.append((
                ts, coin,
                float(fields[idx["open_interest"]] or 0),
                float(fields[idx["funding"]] or 0),
                float(fields[idx["premium"]] or 0),
                float(fields[idx["mark_px"]] or 0),
                float(fields[idx["oracle_px"]] or 0),
                float(fields[idx["day_ntl_vlm"]] or 0),
                float(fields[idx["impact_bid_px"]] or 0),
                float(fields[idx["impact_ask_px"]] or 0),
            ))
        except (ValueError, IndexError):
            continue
    return rows


def fetch_one(s3, date: str) -> tuple[str, list[tuple]]:
    """Download and parse a single day. Returns (date, rows)."""
    key = f"{PREFIX}{date}.csv.lz4"
    obj = s3.get_object(Bucket=BUCKET, Key=key, RequestPayer="requester")
    raw = obj["Body"].read()
    rows = parse_file(raw)
    return date, rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", help="YYYY-MM-DD (default: from bucket start)")
    p.add_argument("--workers", type=int, default=10, help="parallel downloads")
    args = p.parse_args()

    load_aws_creds()
    s3 = boto3.client("s3", region_name="us-east-1")
    db = init_db(DB_PATH)

    print(f"Listing available dates in s3://{BUCKET}/{PREFIX}...")
    all_dates = list_available_dates(s3, args.since)
    done = done_dates(db)
    todo = [d for d in all_dates if d not in done]
    print(f"  available: {len(all_dates)} days")
    print(f"  done:      {len(done)} days")
    print(f"  todo:      {len(todo)} days")
    if not todo:
        print("Nothing to do.")
        return 0
    print(f"  range:     {todo[0]} → {todo[-1]}")
    est_mb = len(todo) * 6
    print(f"  estimated download: {est_mb} MB (~${est_mb / 1024 * 0.09:.2f} egress)")

    t0 = time.time()
    total_rows = 0
    done_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, s3, d): d for d in todo}
        for fut in as_completed(futures):
            date = futures[fut]
            try:
                _, rows = fut.result()
            except Exception as e:
                print(f"  ! {date}: {e}", file=sys.stderr)
                continue
            if rows:
                db.executemany(
                    "INSERT OR IGNORE INTO asset_ctx "
                    "(ts, symbol, oi, funding, premium, mark_px, oracle_px, "
                    "day_ntl_vlm, impact_bid, impact_ask) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)",
                       (date, len(rows), datetime.now(timezone.utc).isoformat()))
            db.commit()
            total_rows += len(rows)
            done_count += 1
            if done_count % 20 == 0 or done_count == len(todo):
                elapsed = time.time() - t0
                rate = done_count / elapsed if elapsed else 0
                eta = (len(todo) - done_count) / rate if rate else 0
                print(f"  [{done_count:4d}/{len(todo)}] {date} "
                      f"+{len(rows):5d} rows  "
                      f"total={total_rows:,}  "
                      f"{rate:.1f} d/s  eta={eta:.0f}s")

    elapsed = time.time() - t0
    size = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"\nDone: +{total_rows:,} rows in {elapsed:.0f}s. DB size: {size:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
