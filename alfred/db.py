"""SQLite databases — one market DB (written by the master only) and one DB
per bot (written by its BotInstance only).

Single-writer-per-file replaces the legacy global db_lock: each Database
instance carries its own lock serializing writes across the asyncio tasks
and threads that share it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time

log = logging.getLogger("alfred")

_MARKET_SCHEMA = [
    # 60s tick data (price, OI, funding, premium, volume, book depth)
    """CREATE TABLE IF NOT EXISTS ticks (
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
    ) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts)",
    # Hourly market snapshots (per token)
    """CREATE TABLE IF NOT EXISTS market_snapshots (
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
    ) WITHOUT ROWID""",
    # 60s aggregated trade flow from WebSocket
    """CREATE TABLE IF NOT EXISTS trade_flow (
        ts INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        buy_vol REAL,
        sell_vol REAL,
        buy_count INTEGER,
        sell_count INTEGER,
        max_trade_usd REAL,
        vwap REAL,
        PRIMARY KEY (ts, symbol)
    ) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS idx_tf_symbol_ts ON trade_flow(symbol, ts)",
    # Master events (WS_RECONNECT, CANDLE_GAP_REPAIR, CANDLE_AUDIT, …)
    """CREATE TABLE IF NOT EXISTS events (
        ts INTEGER NOT NULL,
        event TEXT NOT NULL,
        symbol TEXT,
        data TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event)",
    # Candles 4h — store canonique (A1). Écrit par le WS (bougie en cours,
    # closed=0→1 à la finalisation) et le REST backfill/gap repair (closed=1).
    # Le boot recharge les deques depuis cette table (reprise hors-ligne) ;
    # à terme (phase 6) les backtests sourceront ici.
    """CREATE TABLE IF NOT EXISTS candles (
        symbol TEXT NOT NULL,
        interval TEXT NOT NULL DEFAULT '4h',
        t INTEGER NOT NULL,
        close_t INTEGER NOT NULL,
        o REAL, h REAL, l REAL, c REAL, v REAL, n INTEGER,
        closed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (symbol, interval, t)
    ) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS idx_candles_closed ON candles(symbol, interval, closed, t)",
    # Audit des actions d'administration (A5) — table dédiée, pas mélangée
    # aux events système.
    """CREATE TABLE IF NOT EXISTS admin_audit (
        ts INTEGER NOT NULL,
        ip TEXT,
        route TEXT NOT NULL,
        bot_id TEXT,
        payload TEXT,
        result TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON admin_audit(ts)",
    # Versioning de schéma (A5) — posé à la rupture pour les migrations futures.
    """CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    ) WITHOUT ROWID""",
    "INSERT OR IGNORE INTO schema_meta VALUES ('version', '1')",
]

_BOT_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS trades (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)",
    """CREATE TABLE IF NOT EXISTS trajectories (
        symbol TEXT NOT NULL,
        strategy TEXT NOT NULL,
        entry_time TEXT NOT NULL,
        hours REAL NOT NULL,
        unrealized_bps REAL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_traj_entry ON trajectories(symbol, entry_time)",
    """CREATE TABLE IF NOT EXISTS basket_snapshots (
        ts INTEGER PRIMARY KEY,
        n_positions INTEGER,
        mean_corr_to_btc REAL,
        max_pairwise_corr REAL,
        effective_n REAL
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS events (
        ts INTEGER NOT NULL,
        event TEXT NOT NULL,
        symbol TEXT,
        data TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event)",
]


class Database:
    """A SQLite connection + its write lock. One instance per file."""

    def __init__(self, path: str, schema: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in (_MARKET_SCHEMA if schema == "market" else _BOT_SCHEMA):
            self.conn.execute(stmt)
        self.conn.commit()
        log.info("Database ready: %s (%s)", path, schema)

    def write(self, sql: str, rows: list[tuple]) -> None:
        """Serialized executemany + commit. Logs and swallows errors —
        a failed observation write must not kill the loop."""
        if not rows:
            return
        try:
            with self.lock:
                self.conn.executemany(sql, rows)
                self.conn.commit()
        except Exception as e:
            log.warning("DB write failed (%s): %s", self.path, e)

    def log_event(self, event: str, symbol: str | None = None,
                  data: dict | None = None) -> None:
        self.write("INSERT INTO events VALUES (?,?,?,?)",
                   [(int(time.time()), event, symbol,
                     json.dumps(data) if data else None)])

    # ── Candles (market DB only, A1) ─────────────────────────────────

    def upsert_candles(self, symbol: str, candles: list[dict],
                       closed: bool, interval: str = "4h") -> None:
        """Upsert HL-format candles ({t,T,o,h,l,c,v,n}). closed=True marks
        finalized bars (REST backfill, or WS bar superseded by the next).
        A closed bar is never downgraded back to in-progress."""
        rows = []
        for c in candles:
            try:
                rows.append((symbol, interval, int(c["t"]), int(c["T"]),
                             float(c["o"]), float(c["h"]), float(c["l"]),
                             float(c["c"]), float(c.get("v", 0) or 0),
                             int(c.get("n", 0) or 0), 1 if closed else 0))
            except (KeyError, TypeError, ValueError):
                continue
        self.write(
            """INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol, interval, t) DO UPDATE SET
                 close_t=excluded.close_t, o=excluded.o, h=excluded.h,
                 l=excluded.l, c=excluded.c, v=excluded.v, n=excluded.n,
                 closed=MAX(candles.closed, excluded.closed)""", rows)

    def load_candles(self, symbol: str, limit: int,
                     interval: str = "4h") -> list[dict]:
        """Latest `limit` candles (HL dict format, ascending t). Read path —
        short lock hold, used only at boot."""
        try:
            with self.lock:
                rows = self.conn.execute(
                    """SELECT t, close_t, o, h, l, c, v, n FROM candles
                       WHERE symbol=? AND interval=?
                       ORDER BY t DESC LIMIT ?""",
                    (symbol, interval, limit)).fetchall()
        except Exception as e:
            log.warning("load_candles failed (%s): %s", symbol, e)
            return []
        rows.reverse()
        return [{"t": r[0], "T": r[1], "s": symbol, "i": interval,
                 "o": r[2], "h": r[3], "l": r[4], "c": r[5],
                 "v": r[6], "n": r[7]} for r in rows]

    def last_closed_candle_ts(self, interval: str = "4h") -> int:
        """max(close_t) over all closed candles — downtime detection anchor."""
        try:
            with self.lock:
                row = self.conn.execute(
                    "SELECT MAX(close_t) FROM candles WHERE closed=1 AND interval=?",
                    (interval,)).fetchone()
            return int(row[0]) if row and row[0] else 0
        except Exception:
            return 0

    def log_admin_action(self, ip: str, route: str, bot_id: str | None,
                         payload: dict | None, result: str) -> None:
        self.write("INSERT INTO admin_audit VALUES (?,?,?,?,?,?)",
                   [(int(time.time()), ip, route, bot_id,
                     json.dumps(payload) if payload else None, result)])

    def close(self) -> None:
        try:
            with self.lock:
                self.conn.commit()
                self.conn.close()
        except Exception:
            pass


def log_ticks(db: Database, ctxs: list, meta_universe: list,
              symbols) -> None:
    """Write one tick row per tracked symbol from a metaAndAssetCtxs response."""
    ts = int(time.time())
    name_to_idx = {a["name"]: i for i, a in enumerate(meta_universe)}
    rows = []
    for sym in symbols:
        idx = name_to_idx.get(sym)
        if idx is None:
            continue
        ctx = ctxs[idx]
        mark = float(ctx.get("markPx") or 0)
        if mark <= 0:
            continue
        impacts = ctx.get("impactPxs") or []
        rows.append((
            ts, sym, mark,
            float(ctx.get("oraclePx") or 0),
            float(ctx.get("openInterest") or 0),
            float(ctx.get("funding") or 0),
            float(ctx.get("premium") or 0),
            float(ctx.get("dayNtlVlm") or 0),
            float(impacts[0]) if len(impacts) > 0 else None,
            float(impacts[1]) if len(impacts) > 1 else None,
        ))
    db.write("INSERT OR IGNORE INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
