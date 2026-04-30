"""Entry point — async runner, signal handlers, startup logging, uvicorn."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import signal
import time
from datetime import datetime, timezone

import uvicorn

from .config import (
    VERSION, EXECUTION_MODE, LEVERAGE, TRADE_SYMBOLS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8,
    STOP_LOSS_BPS, STOP_LOSS_S8, MAX_POSITIONS, MAX_SAME_DIRECTION,
    MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS,
    TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER,
    LOSS_STREAK_COOLDOWN, SIZE_PCT, SIZE_BONUS, WEB_PORT,
    strat_size,
)
from .bot import MultiSignalBot
from .web import create_app
from .config import STATE_FILE
from .persistence import load_state, load_trades
from .net import send_telegram
from .collector import TradeFlowCollector

log = logging.getLogger("multisignal")


async def run():
    # Prevent two instances from running on the same state file
    lock_path = STATE_FILE + ".lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.critical("Another bot instance is running (lock: %s) — aborting", lock_path)
        raise SystemExit(1)

    bot = MultiSignalBot()
    app = create_app(bot)

    bot.running = True
    bot.started_at = datetime.now(timezone.utc)
    bot._shutdown_event = asyncio.Event()

    # Load history from SQLite
    for t in load_trades(bot._db):
        bot.trades.append(t)

    state = load_state(STATE_FILE, bot.states)
    if state:
        if "capital" in state:
            bot._capital = state["capital"]
            log.info("Restored capital: $%.0f", bot._capital)
        bot._total_pnl = state.get("total_pnl", 0)
        bot._wins = state.get("wins", 0)
        bot._peak_balance = state.get("peak_balance", bot._capital)
        bot._last_daily_report = state.get("last_daily_report", 0)
        bot._paused = state.get("paused", False)
        bot._consecutive_losses = state.get("consecutive_losses", 0)
        bot._loss_streak_until = state.get("loss_streak_until", 0)
        bot._cooldowns = state.get("cooldowns", {})
        bot._signal_first_seen = state.get("signal_first_seen", {})
        if "feature_cache" in state:
            bot._feature_cache = state["feature_cache"]
        if "positions" in state:
            bot.positions = state["positions"]
        if bot.positions or bot._total_pnl:
            log.info("Restored: %d positions, P&L $%.2f", len(bot.positions), bot._total_pnl)

    # Startup sanity check: sum of *all* trades should match stored _total_pnl.
    # close_position credits _total_pnl on every close (bot, manual_stop, reset)
    # so the sum must include all of them — filtering by is_bot_trade was the
    # earlier mistake that produced false drift warnings after any manual close.
    # Mismatch indicates a crash between close_position's DB commit and the
    # subsequent _save_state() write. Non-fatal but logged for audit.
    trades_sum = sum(t.pnl_usdt for t in bot.trades)
    drift = trades_sum - bot._total_pnl
    if abs(drift) > 1.0:
        log.warning("STARTUP P&L DRIFT: stored=$%.2f, trades_sum=$%.2f (Δ$%.2f) — "
                    "possible crash during close_position between DB commit and state save",
                    bot._total_pnl, trades_sum, drift)

    def _sig(sig, frame):
        log.info("Shutdown signal")
        bot.running = False
        if bot._shutdown_event:
            bot._shutdown_event.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # Boot reconcile (live mode): sync bot.positions with the exchange before
    # the main loop starts. If a position was manually closed via the HL UI
    # while the bot was offline, drop the ghost so check_exits doesn't try to
    # close it again. Orphan positions on the exchange are flagged but not
    # auto-imported (the bot doesn't know the original entry conditions).
    if bot._exchange:
        try:
            ex_state = bot._hl_info.user_state(bot._hl_address)
            exch_syms = set()
            for ap in ex_state.get("assetPositions", []):
                if abs(float(ap["position"].get("szi", 0))) > 0:
                    exch_syms.add(ap["position"]["coin"])
            with bot._pos_lock:
                ghosts = set(bot.positions.keys()) - exch_syms
                for s in ghosts:
                    bot.positions.pop(s, None)
                orphans = exch_syms - set(bot.positions.keys())
            if ghosts:
                log.warning("BOOT RECONCILE: dropped %d ghost positions: %s",
                            len(ghosts), sorted(ghosts))
                from .db import log_event as _log_ev
                _log_ev(bot._db, "GHOST", None, {"symbols": sorted(ghosts)})
                send_telegram(
                    f"⚠️ Boot reconcile: ghost positions dropped "
                    f"(closed on exchange while offline): {sorted(ghosts)}",
                    category="reconcile")
                bot._save_state()
            if orphans:
                log.warning("BOOT RECONCILE: %d orphan positions on exchange: %s",
                            len(orphans), sorted(orphans))
                from .db import log_event as _log_ev
                _log_ev(bot._db, "ORPHAN", None, {"symbols": sorted(orphans)})
                send_telegram(
                    f"⚠️ Boot reconcile: orphan positions on exchange "
                    f"(not in bot): {sorted(orphans)}",
                    category="reconcile")
            if not ghosts and not orphans:
                log.info("Boot reconcile: %d positions match exchange", len(bot.positions))
        except Exception:
            log.exception("Boot reconcile failed (continuing startup)")

    # Use bot._capital (DCA-tracked, restored from state.json) — not the env
    # CAPITAL_USDT, which only matters as a default when state.json is missing.
    mode_tag = "LIVE \U0001f534" if EXECUTION_MODE == "live" else "PAPER"
    log.info("Multi-Signal Bot v%s | %s | $%.0f capital | %dx leverage | %d symbols | port %d",
             VERSION, mode_tag, bot._capital, LEVERAGE, len(TRADE_SYMBOLS), WEB_PORT)
    log.info("Sizing (initial, adjusts with P&L): %d%%+%d%% z-weighted | S1=$%.0f S5=$%.0f S8=$%.0f S9=$%.0f S10=$%.0f (at $%.0f)",
             SIZE_PCT * 100, SIZE_BONUS * 100,
             strat_size("S1", bot._capital), strat_size("S5", bot._capital),
             strat_size("S8", bot._capital), strat_size("S9", bot._capital),
             strat_size("S10", bot._capital), bot._capital)
    log.info("Hold: %dh (S5: %dh, S8: %dh) | Stop: %d bps (S8: %d) | Lev: %.0fx | Max: %d pos / %d dir / %d sect / %d macro / %d token",
             HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8,
             STOP_LOSS_BPS, STOP_LOSS_S8, LEVERAGE, MAX_POSITIONS, MAX_SAME_DIRECTION,
             MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS)
    log.info("Kill-switch: loss cap $%.0f | streak threshold %d \u2192 %.0f%% sizing for %dh",
             TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER * 100,
             LOSS_STREAK_COOLDOWN // 3600)
    send_telegram(f"\U0001f916 Bot v{VERSION} started | {mode_tag} | ${bot._capital:.0f} | {len(bot.positions)} pos",
                  category="system")

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)

    collector = TradeFlowCollector(bot._db)
    tasks = [
        asyncio.create_task(bot.main_loop()),
        asyncio.create_task(server.serve()),
        asyncio.create_task(collector.run()),
    ]

    await bot._shutdown_event.wait()
    bot.running = False
    bot._save_state()
    for t in tasks:
        t.cancel()
    log.info("Shutdown | P&L $%.2f | %d trades", bot._total_pnl, len(bot.trades))


if __name__ == "__main__":
    asyncio.run(run())
