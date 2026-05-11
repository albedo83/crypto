"""SQLite init, schema, tick/event logging.

Functions are standalone (not methods). Caller passes DB connections and
callback functions as arguments to avoid coupling to the bot class.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from .config import ALL_SYMBOLS
from .concurrency import db_lock as _db_lock  # re-exported for back-compat (callers may import from here)

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
            entry_session TEXT,
            funding_usdt REAL DEFAULT 0
        )""")
        # Migration for pre-v11.7.5 DBs
        cols = {r[1] for r in db.execute("PRAGMA table_info(trades)")}
        if "funding_usdt" not in cols:
            db.execute("ALTER TABLE trades ADD COLUMN funding_usdt REAL DEFAULT 0")
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
        # Basket correlation snapshots (observation-only). One row per scan
        # when >=2 positions open. Used to study whether basket concentration
        # correlates with drawdown — not a trading gate.
        db.execute("""CREATE TABLE IF NOT EXISTS basket_snapshots (
            ts INTEGER PRIMARY KEY,
            n_positions INTEGER,
            mean_corr_to_btc REAL,
            max_pairwise_corr REAL,
            effective_n REAL
        ) WITHOUT ROWID""")
        # 60s aggregated trade flow from WebSocket (buy/sell pressure, large trades)
        db.execute("""CREATE TABLE IF NOT EXISTS trade_flow (
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            buy_vol REAL,
            sell_vol REAL,
            buy_count INTEGER,
            sell_count INTEGER,
            max_trade_usd REAL,
            vwap REAL,
            PRIMARY KEY (ts, symbol)
        ) WITHOUT ROWID""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tf_symbol_ts ON trade_flow(symbol, ts)")
        db.commit()
        log.info("Tick database ready: %s", db_path)
        return db
    except Exception as e:
        log.warning("Tick DB init failed: %s — continuing without tick logging", e)
        return None


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
            with _db_lock:
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
        with _db_lock:
            db.execute("INSERT INTO events VALUES (?,?,?,?)",
                       (int(time.time()), event, symbol,
                        json.dumps(data) if data else None))
            db.commit()
    except Exception as e:
        log.warning("Event log error: %s", e)

