"""Entry and exit trading logic — position management, P&L tracking, kill-switch.

All functions take a `bot` instance for state access (positions, trades, locks,
exchange handles, counters). This avoids circular imports while keeping logic
close to the data it mutates.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from .config import (CAPITAL_USDT, LEVERAGE, COST_BPS, MAX_POSITIONS, MAX_SAME_DIRECTION,
                     MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES,
                     TOKEN_SECTOR, STOP_LOSS_BPS, STOP_LOSS_S8, COOLDOWN_HOURS,
                     TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER,
                     LOSS_STREAK_COOLDOWN, HOLD_HOURS_DEFAULT, TRADES_CSV, OUTPUT_DIR,
                     S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS, strat_size)
from .models import Position, Trade
from .exchange import execute_open, execute_close
from .persistence import write_trade, write_trajectory
from .db import log_event
from .net import send_telegram

log = logging.getLogger("multisignal")


# ── Helpers ──────────────────────────────────────────────────────────

def is_bot_trade(t) -> bool:
    """True if trade was a bot decision (not manual_stop or reset)."""
    return t.reason not in ("manual_stop", "reset")


def compute_signal_drift(trades) -> dict:
    """Rolling stats per signal on last 20 bot trades (excludes manual stops).

    Used by quarantine logic and exposed via /api/state for monitoring.
    """
    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        if is_bot_trade(t):
            by_strat[t.strategy].append(t)
    result = {}
    for strat, strat_trades in by_strat.items():
        recent = strat_trades[-20:]
        if recent:
            result[strat] = {
                "n": len(recent),
                "win_rate": round(sum(1 for t in recent if t.pnl_usdt > 0) / len(recent), 2),
                "avg_bps": round(sum(t.net_bps for t in recent) / len(recent), 1),
                "total_pnl": round(sum(t.pnl_usdt for t in recent), 2),
            }
    return result


# ── Exit Logic ───────────────────────────────────────────────────────

def check_exits(bot) -> int:
    """Close positions that hit timeout or stop loss. Returns count of exits."""
    now = datetime.now(timezone.utc)
    exits = 0

    # Retry any previously failed exchange closes
    with bot._pos_lock:
        failed_snapshot = list(bot._failed_closes)
    for sym in failed_snapshot:
        if sym in bot.positions:
            st = bot.states.get(sym)
            if st and st.price > 0:
                log.info("Retrying failed close for %s", sym)
                close_position(sym, st.price, now, "retry_close", bot)
                if sym not in bot.positions:
                    exits += 1

    for sym in list(bot.positions.keys()):
        # Snapshot position under lock to avoid race with api_pause
        with bot._pos_lock:
            pos = bot.positions.get(sym)
            if not pos:
                continue
            entry_price = pos.entry_price
            direction = pos.direction
            strategy = pos.strategy
            stop_bps = pos.stop_bps
            target_exit = pos.target_exit

        st = bot.states.get(sym)
        if not st or st.price == 0:
            # If price has been dead for >30 min and position expired, force close at entry price
            if st and now >= target_exit:
                log.warning("Force-closing %s: price unavailable, hold time expired", sym)
                close_position(sym, entry_price, now, "stale_price", bot)
                exits += 1
            continue

        unrealized = direction * (st.price / entry_price - 1) * 1e4 * LEVERAGE

        # Track MAE/MFE + trajectory (updated every 60s via main loop)
        with bot._pos_lock:
            if sym not in bot.positions:
                continue  # closed between snapshot and here
            if unrealized < pos.mae_bps:
                pos.mae_bps = unrealized
            if unrealized > pos.mfe_bps:
                pos.mfe_bps = unrealized
            hours_held = (now - pos.entry_time).total_seconds() / 3600
            last_h = pos.trajectory[-1][0] if pos.trajectory else -1
            if hours_held - last_h >= 0.95:
                if len(pos.trajectory) < 200:
                    pos.trajectory.append((round(hours_held, 1), round(unrealized, 1)))

        # Per-strategy stop loss: S8 tighter, S9 adaptive, others default
        if strategy == "S8":
            stop = STOP_LOSS_S8
        elif stop_bps != 0:
            stop = stop_bps  # S9 adaptive stop stored at entry
        else:
            stop = STOP_LOSS_BPS

        exit_reason = None
        if now >= target_exit:
            exit_reason = "timeout"
        elif unrealized < stop:
            exit_reason = "catastrophe_stop"
        elif strategy == "S9" and hours_held >= S9_EARLY_EXIT_HOURS and unrealized < S9_EARLY_EXIT_BPS:
            exit_reason = "s9_early_exit"
        if exit_reason:
            close_position(sym, st.price, now, exit_reason, bot)
            exits += 1

    return exits


def close_position(sym: str, exit_price: float, now: datetime, reason: str, bot) -> None:
    """Exit a position, record the trade, and update portfolio state."""
    if sym not in bot.positions:
        return  # already closed (race with api_pause)

    # Execute close on exchange FIRST (live mode) — only pop if successful
    if bot._exchange:
        try:
            fill_px = execute_close(bot._exchange, bot._hl_info, bot._hl_address, sym)
            if fill_px:
                exit_price = fill_px
            with bot._pos_lock:
                bot._failed_closes.discard(sym)
        except Exception as e:
            with bot._pos_lock:
                bot._failed_closes.add(sym)
            log.error("EXEC CLOSE FAILED %s: %s — keeping position, will retry next scan", sym, e)
            send_telegram(f"\u274c Close failed {sym}: {e} \u2014 will retry")
            return  # don't pop — position stays tracked, retried next scan

    with bot._pos_lock:
        if sym not in bot.positions:
            return  # closed by concurrent api_pause/check_exits
        pos = bot.positions.pop(sym)

        hold_h = (now - pos.entry_time).total_seconds() / 3600
        # Record final trajectory point at exit
        final_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4 * LEVERAGE
        pos.trajectory.append((round(hold_h, 1), round(final_bps, 1)))
        # P&L calc: direction * price change * leverage, then subtract round-trip costs.
        # Costs scale with leverage because notional = size * leverage.
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4 * LEVERAGE
        effective_cost = COST_BPS * LEVERAGE
        net_bps = gross_bps - effective_cost
        pnl = pos.size_usdt * net_bps / 1e4

        bot._total_pnl += pnl
        balance = CAPITAL_USDT + bot._total_pnl
        if balance > bot._peak_balance:
            bot._peak_balance = balance
        if pnl > 0:
            bot._wins += 1

        # Track consecutive losses — protects against correlated drawdowns.
        if pnl > 0:
            bot._consecutive_losses = 0
        else:
            bot._consecutive_losses += 1
            if bot._consecutive_losses >= LOSS_STREAK_THRESHOLD:
                bot._loss_streak_until = time.time() + LOSS_STREAK_COOLDOWN
                log.warning("Loss streak: %d consecutive losses \u2014 sizing reduced for 24h",
                            bot._consecutive_losses)

        # Kill-switch: if total P&L breaches cap, stop all trading.
        if bot._total_pnl <= TOTAL_LOSS_CAP:
            bot._paused = True
            log.critical("KILL-SWITCH: P&L $%.2f below cap $%.0f \u2014 auto-paused",
                         bot._total_pnl, TOTAL_LOSS_CAP)

        # Cooldown
        bot._cooldowns[sym] = time.time() + COOLDOWN_HOURS * 3600

    # Kill-switch telegram (outside lock — I/O)
    if bot._total_pnl <= TOTAL_LOSS_CAP:
        send_telegram(
            f"\U0001f6d1 KILL-SWITCH: P&L ${bot._total_pnl:.2f} < cap ${TOTAL_LOSS_CAP:.0f} \u2014 bot paused")

    trade = Trade(
        symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
        strategy=pos.strategy,
        entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
        entry_price=pos.entry_price, exit_price=exit_price,
        hold_hours=round(hold_h, 1), size_usdt=pos.size_usdt,
        signal_info=pos.signal_info,
        gross_bps=round(gross_bps, 1), net_bps=round(net_bps, 1),
        pnl_usdt=round(pnl, 2),
        mae_bps=round(pos.mae_bps, 1), mfe_bps=round(pos.mfe_bps, 1),
        reason=reason,
        entry_oi_delta=pos.entry_oi_delta, entry_crowding=pos.entry_crowding,
        entry_confluence=pos.entry_confluence, entry_session=pos.entry_session,
    )
    bot.trades.append(trade)
    write_trade(trade, TRADES_CSV, bot._db)
    write_trajectory(sym, pos, OUTPUT_DIR, bot._db)

    n = len(bot.trades)
    balance = CAPITAL_USDT + bot._total_pnl
    wr = bot._wins / n * 100 if n > 0 else 0
    arrow = "\u2713" if pnl > 0 else "\u2717"
    log.info("%s %s %s %s | %.0fh | %s | gross %+.1f | net %+.1f | $%+.2f | mae %+.0f | mfe %+.0f | bal $%.0f (#%d %.0f%%)",
             arrow, pos.strategy, trade.direction, sym, hold_h, reason,
             gross_bps, net_bps, pnl, pos.mae_bps, pos.mfe_bps, balance, n, wr)
    emoji = "\u2705" if pnl > 0 else "\U0001f534"
    send_telegram(
        f"{emoji} {pos.strategy} {trade.direction} {sym} | {net_bps:+.0f} bps | ${pnl:+.2f} | bal ${balance:.0f}")


# ── Entry Logic ──────────────────────────────────────────────────────

def rank_and_enter(signals: list, now: datetime, bot) -> int:
    """Sort signals by z-score, apply position limits, create positions.

    Returns count of new entries.
    """
    # Priority: highest z-score first (strongest statistical edge), then
    # by signal strength within same z. This ensures S1/S8 get slots
    # before S5 when multiple signals fire simultaneously.
    signals.sort(key=lambda s: (s["z"], s["strength"]), reverse=True)

    n_longs = sum(1 for p in bot.positions.values() if p.direction == 1)
    n_shorts = sum(1 for p in bot.positions.values() if p.direction == -1)

    entries = 0
    seen_symbols: set = set()       # one entry per symbol per scan
    drift = compute_signal_drift(bot.trades)  # compute once, not per candidate
    for sig in signals:
        sym = sig["symbol"]
        side = "LONG" if sig["direction"] == 1 else "SHORT"

        if len(bot.positions) >= MAX_POSITIONS:
            log.debug("SKIP %s %s %s: max_positions", sig["strategy"], side, sym)
            log_event(bot._db, "SKIP", sym, {"strategy": sig["strategy"], "dir": side, "reason": "max_positions"})
            break

        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)

        if sig["direction"] == 1 and n_longs >= MAX_SAME_DIRECTION:
            log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
            continue
        if sig["direction"] == -1 and n_shorts >= MAX_SAME_DIRECTION:
            log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
            continue

        # Slot reservation: macro vs token-level signals
        n_macro = sum(1 for p in bot.positions.values() if p.strategy in MACRO_STRATEGIES)
        n_token_sig = sum(1 for p in bot.positions.values() if p.strategy not in MACRO_STRATEGIES)
        if sig["strategy"] in MACRO_STRATEGIES and n_macro >= MAX_MACRO_SLOTS:
            log.debug("SKIP %s %s %s: max_macro (%d/%d)", sig["strategy"], side, sym, n_macro, MAX_MACRO_SLOTS)
            log_event(bot._db, "SKIP", sym, {"strategy": sig["strategy"], "dir": side, "reason": "max_macro"})
            continue
        if sig["strategy"] not in MACRO_STRATEGIES and n_token_sig >= MAX_TOKEN_SLOTS:
            log.debug("SKIP %s %s %s: max_token (%d/%d)", sig["strategy"], side, sym, n_token_sig, MAX_TOKEN_SLOTS)
            log_event(bot._db, "SKIP", sym, {"strategy": sig["strategy"], "dir": side, "reason": "max_token"})
            continue

        # Sector concentration limit
        sym_sector = TOKEN_SECTOR.get(sym)
        if sym_sector:
            sector_count = sum(1 for p in bot.positions.values() if TOKEN_SECTOR.get(p.symbol) == sym_sector)
            if sector_count >= MAX_PER_SECTOR:
                log.debug("SKIP %s %s %s: max_sector (%s)", sig["strategy"], side, sym, sym_sector)
                continue

        st = bot.states[sym]
        hold_h = sig.get("hold_hours", HOLD_HOURS_DEFAULT)
        target_exit = now + timedelta(hours=hold_h)
        current_capital = CAPITAL_USDT + bot._total_pnl
        size = strat_size(sig["strategy"], current_capital)
        # Loss streak penalty: protects against correlated losses
        # (e.g. flash crash hitting multiple positions simultaneously)
        if time.time() < bot._loss_streak_until:
            size = round(size * LOSS_STREAK_MULTIPLIER, 2)

        # Quarantine and exposure cap DISABLED — backtest shows they destroy
        # compounding returns (-59% to -95% P&L). Per-trade stops are sufficient.
        # Signal drift is still tracked via /api/state for monitoring.

        # Execute order (live) or use market price (paper)
        entry_price = st.price
        if entry_price <= 0:
            log.warning("SKIP %s %s: invalid price %s", sig["strategy"], sym, entry_price)
            continue
        side = "LONG" if sig["direction"] == 1 else "SHORT"
        if bot._exchange:
            try:
                entry_price = execute_open(
                    bot._exchange, bot._hl_info, bot._hl_address, bot._sz_decimals,
                    sym, sig["direction"] == 1, size, st.price)
                send_telegram(
                    f"\U0001f7e2 {sig['strategy']} {side} {sym} @ ${entry_price:.4f} | ${size:.0f}")
            except Exception as e:
                log.error("EXEC OPEN FAILED %s %s: %s", sym, sig["strategy"], e)
                send_telegram(f"\u274c Open failed {sym} {sig['strategy']}: {e}")
                continue

        ctx = sig.get("ctx", {})
        with bot._pos_lock:
            bot.positions[sym] = Position(
                symbol=sym, direction=sig["direction"],
                strategy=sig["strategy"],
                entry_price=entry_price, entry_time=now,
                size_usdt=size, signal_info=sig["info"],
                target_exit=target_exit,
                trajectory=[(0.0, 0.0)],  # t=0 anchor point
                stop_bps=sig.get("stop_bps", 0.0),
                entry_oi_delta=ctx.get("oi_delta", 0.0),
                entry_crowding=ctx.get("crowding", 0),
                entry_confluence=ctx.get("confluence", 0),
                entry_session=ctx.get("session", ""),
            )

        if sig["direction"] == 1:
            n_longs += 1
        else:
            n_shorts += 1
        entries += 1

        log.info("\u2192 %s %s %s @ $%.4f | %s | $%.0f | exit ~%s | %d/%d pos",
                 sig["strategy"], side, sym, entry_price, sig["info"],
                 size, target_exit.strftime("%m-%d %H:%M"),
                 len(bot.positions), MAX_POSITIONS)

    return entries
