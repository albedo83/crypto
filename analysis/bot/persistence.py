"""DB writes, market snapshots, state save/load, trade loading.

All persistence goes through SQLite. CSV writing removed in v11.3.1.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import orjson

from .config import OUTPUT_DIR, STATE_FILE, CAPITAL_USDT, VERSION
from .models import Position, Trade

log = logging.getLogger("multisignal")

# ── Trade & Trajectory Writing ────────────────────────────────────────

def write_trade(trade: Trade, db: sqlite3.Connection | None) -> None:
    """Write trade to SQLite."""
    if not db:
        log.warning("No DB — trade recorded in memory only")
        return
    from .db import _db_lock
    try:
        with _db_lock:
            db.execute("""INSERT INTO trades
                (symbol, direction, strategy, entry_time, exit_time, entry_price,
                 exit_price, hold_hours, size_usdt, signal_info, gross_bps, net_bps,
                 pnl_usdt, mae_bps, mfe_bps, reason, entry_oi_delta, entry_crowding,
                 entry_confluence, entry_session, funding_usdt)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade.symbol, trade.direction, trade.strategy,
                 trade.entry_time, trade.exit_time,
                 trade.entry_price, trade.exit_price, trade.hold_hours, trade.size_usdt,
                 trade.signal_info, trade.gross_bps, trade.net_bps, trade.pnl_usdt,
                 trade.mae_bps, trade.mfe_bps, trade.reason,
                 trade.entry_oi_delta, trade.entry_crowding, trade.entry_confluence,
                 trade.entry_session, trade.funding_usdt))
            db.commit()
    except Exception as e:
        log.warning("Trade DB write failed: %s", e)


def write_trajectory(sym: str, pos: Position, db: sqlite3.Connection | None) -> None:
    """Write hourly trajectory to SQLite. One row per hour of the trade's life."""
    if not pos.trajectory or not db:
        return
    from .db import _db_lock
    entry_t = pos.entry_time.isoformat(timespec="seconds")
    try:
        with _db_lock:
            db.executemany("""INSERT INTO trajectories
                (symbol, strategy, entry_time, hours, unrealized_bps)
                VALUES (?,?,?,?,?)""",
                [(sym, pos.strategy, entry_t, h, b) for h, b in pos.trajectory])
            db.commit()
    except Exception as e:
        log.warning("Trajectory DB write failed: %s", e)


# ── Market Snapshots ──────────────────────────────────────────────────

def log_market_snapshot(states: dict, feature_cache: dict,
                        trade_symbols: list,
                        db: sqlite3.Connection | None,
                        compute_oi_fn, compute_crowding_fn) -> None:
    """Write hourly snapshot of OI/funding/premium/crowding for all tokens to SQLite.

    compute_oi_fn(sym) -> dict with 'oi_delta_1h' key.
    compute_crowding_fn(sym, oi_f=oi_f) -> int (0-100).
    """
    if not db:
        return
    from .db import _db_lock
    ts_epoch = int(time.time())
    rows: list[tuple] = []
    for sym in trade_symbols:
        st = states.get(sym)
        if not st or st.price == 0:
            continue
        oi_f = compute_oi_fn(sym)
        feat = feature_cache.get(sym)
        crowd = compute_crowding_fn(sym, oi_f=oi_f)
        rows.append((ts_epoch, sym, round(st.price, 6), round(st.oi, 2),
                      oi_f["oi_delta_1h"], round(st.funding * 1e6, 2),
                      round(st.premium * 1e6, 2), crowd,
                      round(feat.get("vol_z", 0), 2) if feat else 0))
    if rows:
        try:
            with _db_lock:
                db.executemany("""INSERT OR IGNORE INTO market_snapshots
                    (ts, symbol, price, oi, oi_delta_1h_pct, funding_ppm, premium_ppm, crowding, vol_z)
                    VALUES (?,?,?,?,?,?,?,?,?)""", rows)
                db.commit()
        except Exception as e:
            log.warning("Market snapshot DB write failed: %s", e)


# ── State Persistence ─────────────────────────────────────────────────

def save_state(state_file: str, positions: dict, pos_lock,
               total_pnl: float, wins: int, peak_balance: float,
               last_daily_report: float, paused: bool,
               consecutive_losses: int, loss_streak_until: float,
               cooldowns: dict, signal_first_seen: dict,
               feature_cache: dict, capital: float = 0) -> None:
    """Atomically persist bot state (write to .tmp then os.replace)."""
    output_dir = os.path.dirname(state_file)
    os.makedirs(output_dir, exist_ok=True)
    with pos_lock:
        pos_snapshot = [{
            "symbol": p.symbol, "direction": p.direction,
            "strategy": p.strategy,
            "entry_price": p.entry_price, "entry_time": p.entry_time.isoformat(),
            "size_usdt": p.size_usdt, "signal_info": p.signal_info,
            "target_exit": p.target_exit.isoformat(),
            "mae_bps": p.mae_bps, "mfe_bps": p.mfe_bps, "stop_bps": p.stop_bps,
            "trajectory": p.trajectory,
            "entry_oi_delta": p.entry_oi_delta, "entry_crowding": p.entry_crowding,
            "entry_confluence": p.entry_confluence, "entry_session": p.entry_session,
        } for p in positions.values()]
    data = {
        "version": VERSION, "capital": capital,
        "total_pnl": total_pnl, "wins": wins,
        "peak_balance": peak_balance, "last_daily_report": last_daily_report,
        "paused": paused,
        "consecutive_losses": consecutive_losses,
        "loss_streak_until": loss_streak_until,
        "cooldowns": {k: v for k, v in cooldowns.items() if v > time.time()},
        "signal_first_seen": signal_first_seen,
        "feature_cache": {k: {fk: float(fv) if hasattr(fv, '__float__') else fv
                              for fk, fv in v.items()} for k, v in feature_cache.items() if v},
        "feature_cache_ts": time.time(),
        "positions": pos_snapshot,
    }
    tmp = state_file + ".tmp"
    try:
        payload = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
    except Exception:
        log.exception("State serialization failed — keeping existing state file")
        return
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, state_file)  # atomic on POSIX


def load_state(state_file: str, states: dict) -> dict:
    """Restore positions + P&L from disk. Keeps .loaded backup for debugging.

    Returns dict with all restored fields. Caller applies them to the bot instance.
    Keys: total_pnl, wins, peak_balance, last_daily_report, paused,
          consecutive_losses, loss_streak_until, cooldowns, signal_first_seen,
          feature_cache, positions.
    """
    result: dict = {}
    if not os.path.exists(state_file):
        return result
    try:
        with open(state_file, "rb") as f:
            data = orjson.loads(f.read())
        result["total_pnl"] = data.get("total_pnl", 0)
        result["wins"] = data.get("wins", 0)
        total_pnl = result["total_pnl"]
        result["peak_balance"] = max(data.get("peak_balance", 0), CAPITAL_USDT + total_pnl)
        result["last_daily_report"] = data.get("last_daily_report", 0)
        result["paused"] = data.get("paused", False)
        result["consecutive_losses"] = data.get("consecutive_losses", 0)
        result["loss_streak_until"] = data.get("loss_streak_until", 0)
        result["cooldowns"] = data.get("cooldowns", {})
        result["signal_first_seen"] = data.get("signal_first_seen", {})
        # Restore feature cache if recent enough (avoids blank dashboard on restart)
        fc_ts = data.get("feature_cache_ts", 0)
        if time.time() - fc_ts < 7200:  # < 2h old
            result["feature_cache"] = data.get("feature_cache", {})
            log.info("Restored feature cache (%.0fm old)", (time.time() - fc_ts) / 60)
        # Restore positions with per-position validation
        positions: dict[str, Position] = {}
        for p in data.get("positions", []):
            sym = p.get("symbol", "?")
            if sym not in states:
                log.warning("Skipping unknown symbol from state: %s", sym)
                continue
            try:
                positions[sym] = Position(
                    symbol=sym, direction=p["direction"],
                    strategy=p.get("strategy", "?"),
                    entry_price=p["entry_price"],
                    entry_time=datetime.fromisoformat(p["entry_time"]),
                    size_usdt=p["size_usdt"],
                    signal_info=p.get("signal_info", ""),
                    target_exit=datetime.fromisoformat(p["target_exit"]),
                    mae_bps=p.get("mae_bps", 0.0),
                    mfe_bps=p.get("mfe_bps", 0.0),
                    stop_bps=p.get("stop_bps", 0.0),
                    trajectory=p.get("trajectory", []),
                    entry_oi_delta=p.get("entry_oi_delta", 0.0),
                    entry_crowding=p.get("entry_crowding", 0),
                    entry_confluence=p.get("entry_confluence", 0),
                    entry_session=p.get("entry_session", ""),
                )
            except (KeyError, ValueError, TypeError) as e:
                log.error("Skipping corrupt position %s: %s", sym, e)
        result["positions"] = positions
        if positions or total_pnl:
            log.info("Restored: %d positions, P&L $%.2f", len(positions), total_pnl)
        # Keep backup but don't remove original — next save_state() overwrites it
        shutil.copy2(state_file, state_file + ".loaded")
    except Exception:
        log.exception("Load state failed")
    return result


# ── Trade History Loading ─────────────────────────────────────────────

def load_trades(db: sqlite3.Connection | None) -> list[Trade]:
    """Reload trade history from SQLite (needed for drift computation and dashboard).

    Returns list of Trade objects (caller appends to its deque).

    Called once at startup before the scan thread and collector start, so no
    _db_lock is needed here. Do not call from another thread after boot — SQLite
    in WAL mode allows concurrent reads, but this function is not meant to be
    called on hot paths.
    """
    result: list[Trade] = []
    if not db:
        return result
    try:
        rows = db.execute("""SELECT symbol, direction, strategy, entry_time, exit_time,
            entry_price, exit_price, hold_hours, size_usdt, signal_info,
            gross_bps, net_bps, pnl_usdt, mae_bps, mfe_bps, reason,
            entry_oi_delta, entry_crowding, entry_confluence, entry_session,
            COALESCE(funding_usdt, 0)
            FROM trades ORDER BY exit_time""").fetchall()
        for r in rows:
            result.append(Trade(
                symbol=r[0], direction=r[1], strategy=r[2] or "?",
                entry_time=r[3], exit_time=r[4],
                entry_price=float(r[5] or 0), exit_price=float(r[6] or 0),
                hold_hours=float(r[7] or 0), size_usdt=float(r[8] or 0),
                signal_info=r[9] or "",
                gross_bps=float(r[10] or 0), net_bps=float(r[11] or 0),
                pnl_usdt=float(r[12] or 0),
                mae_bps=float(r[13] or 0), mfe_bps=float(r[14] or 0),
                reason=r[15] or "?",
                entry_oi_delta=float(r[16] or 0),
                entry_crowding=int(r[17] or 0) if isinstance(r[17], (int, float)) else 0,
                entry_confluence=int(r[18] or 0) if isinstance(r[18], (int, float)) else 0,
                entry_session=r[19] if isinstance(r[19], str) else "",
                funding_usdt=float(r[20] or 0),
            ))
        if result:
            log.info("Loaded %d historical trades from DB", len(result))
    except Exception:
        log.exception("Load trades failed")
    return result
