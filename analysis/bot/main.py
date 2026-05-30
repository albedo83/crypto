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
    SIZE_PCT, SIZE_BONUS, WEB_PORT,
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
        bot._cooldowns = state.get("cooldowns", {})
        bot._signal_first_seen = state.get("signal_first_seen", {})
        if "feature_cache" in state:
            bot._feature_cache = state["feature_cache"]
        if "positions" in state:
            bot.positions = state["positions"]
        if bot.positions or bot._total_pnl:
            log.info("Restored: %d positions, P&L $%.2f", len(bot.positions), bot._total_pnl)

    # v12.6.1 — `_pnl_realign_offset` accumulates manual realigns made via
    # `analysis/equity_realign.py` (one-shot tool that aligns `_total_pnl` to
    # the exchange truth — used to absorb the residue of pre-v12.5.25 P&L
    # over-recording on winners). The offset is the signed delta applied at
    # realign time; bot._total_pnl has already been corrected before save.
    # Persisted so subsequent restarts know to ignore the expected gap.
    bot._pnl_realign_offset = state.get("_pnl_realign_offset", 0.0) if state else 0.0
    if bot._pnl_realign_offset:
        log.info("Restored _pnl_realign_offset: $%+.2f", bot._pnl_realign_offset)

    # v12.9.0 — last 4h candle close timestamp for which an entry-scan ran.
    # Prevents duplicate entries within the same 4h period across restarts.
    bot._last_entry_scan_4h_close = int(state.get("_last_entry_scan_4h_close", 0)) if state else 0

    # Startup sanity check: sum of *all* trades should match stored _total_pnl
    # plus any accumulated realign offset.
    # close_position credits _total_pnl on every close (bot, manual_stop, reset)
    # so the sum must include all of them — filtering by is_bot_trade was the
    # earlier mistake that produced false drift warnings after any manual close.
    # Mismatch indicates a crash between close_position's DB commit and the
    # subsequent _save_state() write. Non-fatal but logged for audit.
    trades_sum = sum(t.pnl_usdt for t in bot.trades)
    drift = trades_sum - bot._total_pnl + bot._pnl_realign_offset
    if abs(drift) > 1.0:
        log.warning("STARTUP P&L DRIFT: stored=$%.2f, trades_sum=$%.2f, "
                    "realign_offset=$%+.2f (Δ$%.2f) — possible crash during "
                    "close_position between DB commit and state save",
                    bot._total_pnl, trades_sum, bot._pnl_realign_offset, drift)

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
    #
    # v12.5.29: two-attempt confirmation. A single transient HL response (cache
    # lag, partial data, network glitch) was previously enough to silently
    # drop a real position from tracking — leaving it open on the exchange
    # outside the bot's stop-loss management. Now a position is only dropped
    # if BOTH consecutive user_state() calls confirm its absence. Symbols
    # missing from only one of the two attempts are flagged as "disputed"
    # and KEPT — the hourly reconcile alerts will keep firing until either
    # the exchange catches up or the user investigates.
    if bot._exchange:
        try:
            # v12.5.31: timeout-wrap each user_state probe. Without this a hung
            # HL endpoint at boot would block the whole startup indefinitely
            # (the outer try/except can't catch a hang). _sdk_call raises
            # TimeoutError after 10s; the except block at the bottom drops to
            # "continuing startup" and the bot proceeds normally.
            from .exchange import _sdk_call
            attempts: list[set[str]] = []
            for _i in range(2):
                ex_state = _sdk_call(bot._hl_info.user_state, bot._hl_address,
                                     timeout=10.0)
                syms = set()
                for ap in ex_state.get("assetPositions", []):
                    if abs(float(ap["position"].get("szi", 0))) > 0:
                        syms.add(ap["position"]["coin"])
                attempts.append(syms)
                if _i == 0:
                    time.sleep(1.5)
            exch_union = attempts[0] | attempts[1]
            with bot._pos_lock:
                bot_syms = set(bot.positions.keys())
                ghosts_a = bot_syms - attempts[0]
                ghosts_b = bot_syms - attempts[1]
                ghosts = ghosts_a & ghosts_b     # confirmed absent on both
                disputed = (ghosts_a | ghosts_b) - ghosts
                for s in ghosts:
                    bot.positions.pop(s, None)
                orphans = exch_union - set(bot.positions.keys())
            if disputed:
                # Position appeared on the exchange in ONE of the two probes
                # but not the other — likely a transient HL response. Keep
                # the position; hourly reconcile will keep watch.
                log.warning("BOOT RECONCILE: %d disputed (kept) — %s",
                            len(disputed), sorted(disputed))
                from .db import log_event as _log_ev
                _log_ev(bot._db, "DISPUTED", None, {"symbols": sorted(disputed)})
                send_telegram(
                    f"⚠️ Boot reconcile: disputed positions (kept, transient "
                    f"HL response): {sorted(disputed)}", category="reconcile",
                    actionable=True)
            if ghosts:
                log.warning("BOOT RECONCILE: dropped %d ghost positions: %s",
                            len(ghosts), sorted(ghosts))
                from .db import log_event as _log_ev
                _log_ev(bot._db, "GHOST", None, {"symbols": sorted(ghosts)})
                send_telegram(
                    f"⚠️ Boot reconcile: ghost positions dropped "
                    f"(closed on exchange while offline): {sorted(ghosts)}",
                    category="reconcile", actionable=True)
                bot._save_state()
            if orphans:
                log.warning("BOOT RECONCILE: %d orphan positions on exchange: %s",
                            len(orphans), sorted(orphans))
                from .db import log_event as _log_ev
                _log_ev(bot._db, "ORPHAN", None, {"symbols": sorted(orphans)})
                send_telegram(
                    f"⚠️ Boot reconcile: orphan positions on exchange "
                    f"(not in bot): {sorted(orphans)}",
                    category="reconcile", actionable=True)
            if not ghosts and not orphans and not disputed:
                log.info("Boot reconcile: %d positions match exchange (2/2 confirms)",
                         len(bot.positions))
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
    if bot._exchange:
        # v12.5.12: fast equity refresh (15s) keeps the dashboard Equity
        # card aligned with HL state without waiting for the 60s main_loop.
        tasks.append(asyncio.create_task(bot.equity_refresh_loop()))

    await bot._shutdown_event.wait()
    bot.running = False
    bot._save_state()
    for t in tasks:
        t.cancel()
    log.info("Shutdown | P&L $%.2f | %d trades", bot._total_pnl, len(bot.trades))


if __name__ == "__main__":
    asyncio.run(run())
