"""All storage: SQLite DB, CSV writes, market snapshots, state save/load, trade loading.

Functions are standalone (not methods). Caller passes DB connections, file paths,
and callback functions as arguments to avoid coupling to the bot class.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import orjson

from .config import (OUTPUT_DIR, TRADES_CSV, MARKET_CSV, STATE_FILE, TICKS_DB,
                     TRADE_SYMBOLS, ALL_SYMBOLS, CAPITAL_USDT, VERSION)
from .models import Position, Trade

log = logging.getLogger("multisignal")


# ── SQLite Init & Migration ───────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection | None:
    """Create SQLite database with full schema: ticks, events, trades, trajectories, market snapshots.

    Returns the connection or None if init fails.
    """
    try:
        db = sqlite3.connect(db_path, check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        # 60s tick data (price, OI, funding, premium, volume, book depth)
        db.execute("""CREATE TABLE IF NOT EXISTS ticks (
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            mark_px REAL,
            oracle_px REAL,
            open_interest REAL,
            funding REAL,
            premium REAL,
            day_ntl_vlm REAL,
            impact_bid REAL,
            impact_ask REAL,
            PRIMARY KEY (ts, symbol)
        ) WITHOUT ROWID""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts)")
        # Events (S9F_OBS, signal skips, etc.)
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts INTEGER NOT NULL,
            event TEXT NOT NULL,
            symbol TEXT,
            data TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event)")
        # Trades (complete history, replaces CSV as source of truth)
        db.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            strategy TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            hold_hours REAL,
            size_usdt REAL,
            signal_info TEXT,
            gross_bps REAL,
            net_bps REAL,
            pnl_usdt REAL,
            mae_bps REAL,
            mfe_bps REAL,
            reason TEXT,
            entry_oi_delta REAL,
            entry_crowding INTEGER,
            entry_confluence INTEGER,
            entry_session TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        # Trajectories (hourly unrealized P&L per trade)
        db.execute("""CREATE TABLE IF NOT EXISTS trajectories (
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            hours REAL NOT NULL,
            unrealized_bps REAL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_traj_entry ON trajectories(symbol, entry_time)")
        # Hourly market snapshots (28 tokens x 24/day)
        db.execute("""CREATE TABLE IF NOT EXISTS market_snapshots (
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            oi REAL,
            oi_delta_1h_pct REAL,
            funding_ppm REAL,
            premium_ppm REAL,
            crowding INTEGER,
            vol_z REAL,
            PRIMARY KEY (ts, symbol)
        ) WITHOUT ROWID""")
        db.commit()
        # Migrate existing CSV data if tables are empty
        migrate_csv_to_db(db, TRADES_CSV, MARKET_CSV, OUTPUT_DIR)
        log.info("Tick database ready: %s", db_path)
        return db
    except Exception as e:
        log.warning("Tick DB init failed: %s — continuing without tick logging", e)
        return None


def migrate_csv_to_db(db: sqlite3.Connection, trades_csv: str,
                      market_csv: str, output_dir: str) -> None:
    """One-time migration of existing CSV data into SQLite tables."""
    if not db:
        return
    # Trades
    count = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if count == 0 and os.path.exists(trades_csv):
        try:
            with open(trades_csv) as f:
                rows = list(csv.DictReader(f))
            if rows:
                db.executemany("""INSERT INTO trades
                    (symbol, direction, strategy, entry_time, exit_time, entry_price,
                     exit_price, hold_hours, size_usdt, signal_info, gross_bps, net_bps,
                     pnl_usdt, mae_bps, mfe_bps, reason, entry_oi_delta, entry_crowding,
                     entry_confluence, entry_session)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(r["symbol"], r["direction"], r.get("strategy", "?"),
                      r["entry_time"], r["exit_time"],
                      float(r["entry_price"]), float(r["exit_price"]),
                      float(r["hold_hours"]), float(r["size_usdt"]),
                      r.get("signal_info", ""),
                      float(r["gross_bps"]), float(r["net_bps"]), float(r["pnl_usdt"]),
                      float(r.get("mae_bps", 0)), float(r.get("mfe_bps", 0)),
                      r["reason"],
                      float(r.get("entry_oi_delta", 0)),
                      int(float(r.get("entry_crowding", 0))),
                      int(float(r.get("entry_confluence", 0))),
                      r.get("entry_session", ""))
                     for r in rows])
                db.commit()
                log.info("Migrated %d trades from CSV to SQLite", len(rows))
        except Exception as e:
            log.warning("Trade CSV migration failed: %s", e)
    # Trajectories
    count = db.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
    traj_csv = os.path.join(output_dir, "reversal_trajectories.csv")
    if count == 0 and os.path.exists(traj_csv):
        try:
            with open(traj_csv) as f:
                rows = list(csv.DictReader(f))
            if rows:
                db.executemany("""INSERT INTO trajectories
                    (symbol, strategy, entry_time, hours, unrealized_bps)
                    VALUES (?,?,?,?,?)""",
                    [(r["symbol"], r["strategy"], r["entry_time"],
                      float(r["hours"]), float(r["unrealized_bps"])) for r in rows])
                db.commit()
                log.info("Migrated %d trajectory points from CSV to SQLite", len(rows))
        except Exception as e:
            log.warning("Trajectory CSV migration failed: %s", e)
    # Market snapshots
    count = db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    if count == 0 and os.path.exists(market_csv):
        try:
            with open(market_csv) as f:
                rows = list(csv.DictReader(f))
            if rows:
                from datetime import datetime as _dt
                db.executemany("""INSERT OR IGNORE INTO market_snapshots
                    (ts, symbol, price, oi, oi_delta_1h_pct, funding_ppm, premium_ppm, crowding, vol_z)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    [(int(_dt.fromisoformat(r["timestamp"]).timestamp()),
                      r["symbol"], float(r["price"]), float(r.get("oi", 0)),
                      float(r.get("oi_delta_1h_pct", 0)),
                      float(r.get("funding_ppm", 0)), float(r.get("premium_ppm", 0)),
                      int(float(r.get("crowding", 0))),
                      float(r.get("vol_z", 0)))
                     for r in rows])
                db.commit()
                log.info("Migrated %d market snapshots from CSV to SQLite", len(rows))
        except Exception as e:
            log.warning("Market CSV migration failed: %s", e)
    db.commit()


# ── Tick & Event Logging ──────────────────────────────────────────────

def log_ticks(db: sqlite3.Connection | None, api_ctxs: list | None,
              meta_universe: list | None, all_symbols: list | None = None) -> None:
    """Write current tick data for all symbols to SQLite."""
    if not db or not api_ctxs:
        return
    syms = all_symbols or ALL_SYMBOLS
    try:
        ts = int(time.time())
        rows = []
        name_to_idx: dict[str, int] = {}
        for i, asset in enumerate(meta_universe):
            name_to_idx[asset["name"]] = i
        for sym in syms:
            idx = name_to_idx.get(sym)
            if idx is None:
                continue
            ctx = api_ctxs[idx]
            mark = float(ctx.get("markPx") or 0)
            if mark <= 0:
                continue
            oracle = float(ctx.get("oraclePx") or 0)
            oi = float(ctx.get("openInterest") or 0)
            funding = float(ctx.get("funding") or 0)
            premium = float(ctx.get("premium") or 0)
            vlm = float(ctx.get("dayNtlVlm") or 0)
            impacts = ctx.get("impactPxs") or []
            bid = float(impacts[0]) if len(impacts) > 0 else None
            ask = float(impacts[1]) if len(impacts) > 1 else None
            rows.append((ts, sym, mark, oracle, oi, funding, premium, vlm, bid, ask))
        if rows:
            db.executemany(
                "INSERT OR IGNORE INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
    except Exception as e:
        log.warning("Tick log error: %s", e)


def log_event(db: sqlite3.Connection | None, event: str,
              symbol: str | None = None, data: dict | None = None) -> None:
    """Write an event (S9F_OBS, signal trigger, etc.) to SQLite."""
    if not db:
        return
    try:
        db.execute("INSERT INTO events VALUES (?,?,?,?)",
                   (int(time.time()), event, symbol,
                    json.dumps(data) if data else None))
        db.commit()
    except Exception as e:
        log.warning("Event log error: %s", e)

