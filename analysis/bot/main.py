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
    VERSION, EXECUTION_MODE, CAPITAL_USDT, LEVERAGE, TRADE_SYMBOLS,
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

    # Startup sanity check: sum of bot trades should match stored _total_pnl.
    # Mismatch indicates a crash between close_position's DB commit and the
    # subsequent _save_state() write. Non-fatal but logged for audit.
    from .trading import is_bot_trade
    trades_sum = sum(t.pnl_usdt for t in bot.trades if is_bot_trade(t))
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

    mode_tag = "LIVE \U0001f534" if EXECUTION_MODE == "live" else "PAPER"
    log.info("Multi-Signal Bot v%s | %s | $%.0f capital | %dx leverage | %d symbols | port %d",
             VERSION, mode_tag, CAPITAL_USDT, LEVERAGE, len(TRADE_SYMBOLS), WEB_PORT)
    log.info("Sizing (initial, adjusts with P&L): %d%%+%d%% z-weighted | S1=$%.0f S5=$%.0f S8=$%.0f S9=$%.0f S10=$%.0f (at $%.0f)",
             SIZE_PCT * 100, SIZE_BONUS * 100,
             strat_size("S1", CAPITAL_USDT), strat_size("S5", CAPITAL_USDT),
             strat_size("S8", CAPITAL_USDT), strat_size("S9", CAPITAL_USDT),
             strat_size("S10", CAPITAL_USDT), CAPITAL_USDT)
    log.info("Hold: %dh (S5: %dh, S8: %dh) | Stop: %d bps (S8: %d) | Lev: %.0fx | Max: %d pos / %d dir / %d sect / %d macro / %d token",
             HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8,
             STOP_LOSS_BPS, STOP_LOSS_S8, LEVERAGE, MAX_POSITIONS, MAX_SAME_DIRECTION,
             MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS)
    log.info("Kill-switch: loss cap $%.0f | streak threshold %d \u2192 %.0f%% sizing for %dh",
             TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER * 100,
             LOSS_STREAK_COOLDOWN // 3600)
    send_telegram(f"\U0001f916 Bot v{VERSION} started | {mode_tag} | ${CAPITAL_USDT:.0f} | {len(bot.positions)} pos",
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
