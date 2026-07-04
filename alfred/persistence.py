"""Per-bot persistence — state.json save/load, trade/trajectory writes.

Taken from analysis/bot/persistence.py, adapted to the alfred Database
wrapper (per-bot bot.db) and to per-instance paths (no module-level config).
State schema is IDENTICAL to the legacy one so tools/migrate_bot.py is a
plain copy and a rollback stays trivial.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime

import orjson

from .db import Database
from .models import Position, Trade

log = logging.getLogger("alfred")


# ── Trade & Trajectory Writing ────────────────────────────────────────

def write_trade(trade: Trade, db: Database) -> None:
    db.write_critical("""INSERT INTO trades
        (symbol, direction, strategy, entry_time, exit_time, entry_price,
         exit_price, hold_hours, size_usdt, signal_info, gross_bps, net_bps,
         pnl_usdt, mae_bps, mfe_bps, reason, entry_oi_delta, entry_crowding,
         entry_confluence, entry_session, funding_usdt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(trade.symbol, trade.direction, trade.strategy,
          trade.entry_time, trade.exit_time,
          trade.entry_price, trade.exit_price, trade.hold_hours, trade.size_usdt,
          trade.signal_info, trade.gross_bps, trade.net_bps, trade.pnl_usdt,
          trade.mae_bps, trade.mfe_bps, trade.reason,
          trade.entry_oi_delta, trade.entry_crowding, trade.entry_confluence,
          trade.entry_session, trade.funding_usdt)])


def write_trajectory(sym: str, pos: Position, db: Database) -> None:
    if not pos.trajectory:
        return
    entry_t = pos.entry_time.isoformat(timespec="seconds")
    db.write("""INSERT INTO trajectories
        (symbol, strategy, entry_time, hours, unrealized_bps)
        VALUES (?,?,?,?,?)""",
        [(sym, pos.strategy, entry_t, h, b) for h, b in pos.trajectory])


def log_basket_snapshot(metrics: dict | None, db: Database) -> None:
    if not metrics:
        return
    db.write("""INSERT OR IGNORE INTO basket_snapshots
        (ts, n_positions, mean_corr_to_btc, max_pairwise_corr, effective_n)
        VALUES (?,?,?,?,?)""",
        [(int(time.time()), metrics["n_positions"], metrics["mean_corr_to_btc"],
          metrics["max_pairwise_corr"], metrics["effective_n"])])


# ── State Persistence (schema identical to legacy reversal_state.json) ──

def save_state(bot) -> None:
    """Atomically persist a BotInstance's state (.tmp then os.replace)."""
    state_file = bot.state_file
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with bot._pos_lock:
        pos_snapshot = [{
            "symbol": p.symbol, "direction": p.direction,
            "strategy": p.strategy,
            "entry_price": p.entry_price, "entry_time": p.entry_time.isoformat(),
            "size_usdt": p.size_usdt, "signal_info": p.signal_info,
            "target_exit": p.target_exit.isoformat(),
            "mae_bps": p.mae_bps, "mfe_bps": p.mfe_bps, "mfe_at_h": p.mfe_at_h,
            "stop_bps": p.stop_bps,
            "trajectory": p.trajectory,
            "entry_oi_delta": p.entry_oi_delta, "entry_crowding": p.entry_crowding,
            "entry_confluence": p.entry_confluence, "entry_session": p.entry_session,
            "extended": p.extended,
            "manual_stop_usdt": p.manual_stop_usdt,
            "opp_floor_bps": p.opp_floor_bps,
            "stop_oid": p.stop_oid,
            "stop_px": p.stop_px,
            "mfe_trail_bps": p.mfe_trail_bps,
            "mfe_trail_at_h": p.mfe_trail_at_h,
        } for p in bot.positions.values()]
    data = {
        "version": bot.version, "capital": bot._capital,
        "total_pnl": bot._total_pnl, "wins": bot._wins,
        "peak_balance": bot._peak_balance,
        "last_daily_report": bot._last_daily_report,
        "paused": bot._paused,
        "consecutive_losses": bot._consecutive_losses,
        "cooldowns": {k: v for k, v in bot._cooldowns.items() if v > time.time()},
        "signal_first_seen": bot._signal_first_seen,
        "positions": pos_snapshot,
        "_last_entry_scan_4h_close": int(bot._last_entry_scan_4h_close),
        "_perf_track_start_ts": round(bot._perf_track_start_ts, 0),
        "_capital_at_perf_reset": round(bot._capital_at_perf_reset, 2),
        "_total_pnl_at_perf_reset": round(bot._total_pnl_at_perf_reset, 4),
        "_paused_strats": sorted([list(p) for p in bot._paused_strats]),
        # legacy fields kept for migrate/rollback compatibility
        "_pnl_realign_offset": round(getattr(bot, "_pnl_realign_offset", 0.0), 4),
        "_fees_track_start_ts": round(getattr(bot, "_fees_track_start_ts", 0.0), 0),
        "_btc_z": round(bot._btc_z, 4) if bot._btc_z is not None else None,
    }
    tmp = state_file + ".tmp"
    try:
        payload = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
    except Exception:
        log.exception("[%s] state serialization failed — keeping existing file", bot.id)
        return
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, state_file)


def load_state(state_file: str, known_symbols) -> dict:
    """Restore a bot's persisted state. Returns {} if no file. Keeps .loaded
    backup. Schema-compatible with legacy reversal_state.json (migration =
    file copy)."""
    result: dict = {}
    if not os.path.exists(state_file):
        return result
    try:
        with open(state_file, "rb") as f:
            data = orjson.loads(f.read())
        if "capital" in data:
            result["capital"] = data["capital"]
        result["total_pnl"] = data.get("total_pnl", 0)
        result["wins"] = data.get("wins", 0)
        capital_for_floor = data.get("capital", 0)
        result["peak_balance"] = max(data.get("peak_balance", 0),
                                     capital_for_floor + result["total_pnl"])
        result["last_daily_report"] = data.get("last_daily_report", 0)
        result["paused"] = data.get("paused", False)
        result["consecutive_losses"] = data.get("consecutive_losses", 0)
        result["cooldowns"] = data.get("cooldowns", {})
        result["signal_first_seen"] = data.get("signal_first_seen", {})
        result["_pnl_realign_offset"] = data.get("_pnl_realign_offset", 0.0)
        result["_last_entry_scan_4h_close"] = int(data.get("_last_entry_scan_4h_close", 0))
        result["_fees_track_start_ts"] = float(data.get("_fees_track_start_ts", 0))
        result["_perf_track_start_ts"] = float(data.get("_perf_track_start_ts", 0))
        result["_capital_at_perf_reset"] = float(data.get("_capital_at_perf_reset", 0))
        result["_total_pnl_at_perf_reset"] = float(data.get("_total_pnl_at_perf_reset", 0))
        _bz = data.get("_btc_z")
        result["_btc_z"] = float(_bz) if _bz is not None else None
        _ps = data.get("_paused_strats", [])
        result["_paused_strats"] = {tuple(x) for x in _ps
                                    if isinstance(x, list) and len(x) == 2}
        positions: dict[str, Position] = {}
        for p in data.get("positions", []):
            sym = p.get("symbol", "?")
            if sym not in known_symbols:
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
                    mfe_at_h=p.get("mfe_at_h", 0.0),
                    stop_bps=p.get("stop_bps", 0.0),
                    trajectory=p.get("trajectory", []),
                    entry_oi_delta=p.get("entry_oi_delta", 0.0),
                    entry_crowding=p.get("entry_crowding", 0),
                    entry_confluence=p.get("entry_confluence", 0),
                    entry_session=p.get("entry_session", ""),
                    extended=p.get("extended", False),
                    manual_stop_usdt=p.get("manual_stop_usdt"),
                    opp_floor_bps=p.get("opp_floor_bps"),
                    stop_oid=p.get("stop_oid"),
                    stop_px=p.get("stop_px"),
                    mfe_trail_bps=p.get("mfe_trail_bps", 0.0),
                    mfe_trail_at_h=p.get("mfe_trail_at_h", 0.0),
                )
            except (KeyError, ValueError, TypeError) as e:
                log.error("Skipping corrupt position %s: %s", sym, e)
        result["positions"] = positions
        shutil.copy2(state_file, state_file + ".loaded")
    except Exception:
        log.exception("Load state failed (%s)", state_file)
    return result


# ── Trade History Loading ─────────────────────────────────────────────

def load_trades(db: Database) -> list[Trade]:
    """Reload trade history from bot.db at boot (before any concurrent writer)."""
    result: list[Trade] = []
    try:
        rows = db.conn.execute("""SELECT symbol, direction, strategy, entry_time,
            exit_time, entry_price, exit_price, hold_hours, size_usdt, signal_info,
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
            log.info("Loaded %d historical trades from %s", len(result), db.path)
    except Exception:
        log.exception("Load trades failed")
    return result
