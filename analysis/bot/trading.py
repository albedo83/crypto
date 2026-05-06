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

from .config import (CAPITAL_USDT, LEVERAGE, COST_BPS, FUNDING_DRAG_BPS, MAX_POSITIONS, MAX_SAME_DIRECTION,
                     MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES,
                     TOKEN_SECTOR, STOP_LOSS_BPS, STOP_LOSS_S8, COOLDOWN_HOURS,
                     TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER,
                     LOSS_STREAK_COOLDOWN, HOLD_HOURS_DEFAULT,
                     S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
                     S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
                     DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
                     DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
                     RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
                     RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
                     OI_LONG_GATE_BPS, TRADE_BLACKLIST, strat_size)
from .features import oi_delta_24h_bps
from .models import Position, Trade
from .exchange import execute_open, execute_close, fetch_position_funding
from .persistence import write_trade, write_trajectory
from .db import log_event
from .net import send_telegram

log = logging.getLogger("multisignal")


# ── Helpers ──────────────────────────────────────────────────────────

def is_bot_trade(t) -> bool:
    """True if trade was a bot decision (not manual_stop or reset)."""
    return t.reason not in ("manual_stop", "reset")


def compute_signal_drift(trades) -> dict:
    """Per-strategy stats — lifetime AND rolling 20 trades.

    Lifetime = all bot trades ever for this strategy (structural edge).
    Recent 20 = last 20 (short-term health). `trend` compares first 10 vs last
    10 within the recent 20: +1 improving, -1 degrading, 0 stable/insufficient.
    """
    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        if is_bot_trade(t):
            by_strat[t.strategy].append(t)
    result = {}
    for strat, strat_trades in by_strat.items():
        if not strat_trades:
            continue
        # Lifetime
        n_life = len(strat_trades)
        wr_life = sum(1 for t in strat_trades if t.pnl_usdt > 0) / n_life
        avg_life = sum(t.net_bps for t in strat_trades) / n_life
        pnl_life = sum(t.pnl_usdt for t in strat_trades)
        # Recent 20
        recent = strat_trades[-20:]
        n_rec = len(recent)
        wr_rec = sum(1 for t in recent if t.pnl_usdt > 0) / n_rec
        avg_rec = sum(t.net_bps for t in recent) / n_rec
        pnl_rec = sum(t.pnl_usdt for t in recent)
        # Trend: first half vs second half WR in the recent window
        trend = 0
        if n_rec >= 10:
            half = n_rec // 2
            wr_first = sum(1 for t in recent[:half] if t.pnl_usdt > 0) / half
            wr_last = sum(1 for t in recent[half:] if t.pnl_usdt > 0) / (n_rec - half)
            if wr_last - wr_first >= 0.10:
                trend = 1
            elif wr_last - wr_first <= -0.10:
                trend = -1
        result[strat] = {
            # Legacy fields (lifetime now — was rolling 20 before v11.5.1)
            "n": n_life,
            "win_rate": round(wr_life, 2),
            "avg_bps": round(avg_life, 1),
            "total_pnl": round(pnl_life, 2),
            "trend": trend,
            # Explicit split
            "lifetime": {"n": n_life, "win_rate": round(wr_life, 2),
                         "avg_bps": round(avg_life, 1),
                         "total_pnl": round(pnl_life, 2)},
            "recent20": {"n": n_rec, "win_rate": round(wr_rec, 2),
                         "avg_bps": round(avg_rec, 1),
                         "total_pnl": round(pnl_rec, 2)},
        }
    return result


def compute_s10_health(trades, days: int = 30) -> dict:
    """S10 rolling health check over the last N days.

    Monitors the v11.3.4 walk-forward filters (SHORT-only + token whitelist).
    The filters improved P&L on 12m OOS but the rule is regime-dependent —
    this health card tells you at a glance whether to flip the kill-switch.

    Status:
      green  — S10 profitable (pnl > 0 and avg net > +10 bps)
      yellow — neutral (pnl >= 0 or avg net >= -20 bps)
      red    — bleeding, consider flipping the kill-switch
      idle   — no S10 trades in the window (too quiet to judge)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for t in trades:
        if not is_bot_trade(t) or t.strategy != "S10":
            continue
        try:
            exit_dt = datetime.fromisoformat(t.exit_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        if exit_dt >= cutoff:
            recent.append(t)

    if not recent:
        return {
            "status": "idle", "n": 0, "days": days,
            "pnl": 0.0, "wr": 0.0, "avg_bps": 0.0,
            "message": f"No S10 trades in last {days}d",
        }

    pnl = sum(t.pnl_usdt for t in recent)
    wins = sum(1 for t in recent if t.pnl_usdt > 0)
    wr = wins / len(recent)
    avg_bps = sum(t.net_bps for t in recent) / len(recent)

    if pnl > 0 and avg_bps > 10:
        status = "green"
        message = "S10 performing as expected"
    elif pnl >= 0 or avg_bps >= -20:
        status = "yellow"
        message = "S10 neutral — keep monitoring"
    else:
        status = "red"
        message = "S10 bleeding — consider flipping kill-switch"

    return {
        "status": status, "n": len(recent), "days": days,
        "pnl": round(pnl, 2), "wr": round(wr, 2),
        "avg_bps": round(avg_bps, 1), "message": message,
    }


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

        unrealized = direction * (st.price / entry_price - 1) * 1e4

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
        # v11.7.32 Runner extension: at natural timeout, if a winner is still
        # close to its MFE peak, extend the hold once by RUNNER_EXT_HOURS to
        # capture continuation. Mirror of dead_timeout but for winners.
        # Walk-forward 4/4 with DD intact on `backtest_runner_extension.py`.
        if (now >= target_exit and not pos.extended
                and strategy in RUNNER_EXT_STRATEGIES
                and pos.mfe_bps >= RUNNER_EXT_MIN_MFE_BPS
                and unrealized / pos.mfe_bps >= RUNNER_EXT_MIN_CUR_TO_MFE):
            new_target = target_exit + timedelta(hours=RUNNER_EXT_HOURS)
            with bot._pos_lock:
                if sym in bot.positions:
                    bot.positions[sym].target_exit = new_target
                    bot.positions[sym].extended = True
            target_exit = new_target  # local update for the rest of this iteration
            log.info("⏭ RUNNER_EXT %s %s: MFE %+.0f cur %+.0f → hold +%dh "
                     "(new exit %s)", strategy, sym, pos.mfe_bps, unrealized,
                     RUNNER_EXT_HOURS, new_target.strftime("%m-%d %H:%M"))
            log_event(bot._db, "RUNNER_EXT", sym,
                      {"strategy": strategy, "mfe_bps": round(pos.mfe_bps, 1),
                       "current_bps": round(unrealized, 1),
                       "extra_hours": RUNNER_EXT_HOURS,
                       "new_exit": new_target.isoformat()})
            # Persist immediately so a crash before the scan-loop save can't
            # lose the `extended` flag and re-fire the extension on restart.
            try:
                bot._save_state()
            except Exception:
                log.exception("save_state after RUNNER_EXT failed")
        # Exit price defaults to live mark (used for timeout and for live-mode
        # fallback). For price-triggered exits, use the trigger price so paper
        # P&L matches the trigger and doesn't book the worse intra-scan drift.
        # In live mode `close_position` overrides with the real fill avgPx.
        exit_price = st.price
        if now >= target_exit:
            exit_reason = "timeout"
        elif unrealized < stop:
            exit_reason = "catastrophe_stop"
            # Stop triggered at bps=stop: synthetic price = entry × (1 + dir × stop/1e4)
            exit_price = entry_price * (1 + direction * stop / 1e4)
        elif strategy == "S9" and hours_held >= S9_EARLY_EXIT_HOURS and unrealized < S9_EARLY_EXIT_BPS:
            exit_reason = "s9_early_exit"
            exit_price = entry_price * (1 + direction * S9_EARLY_EXIT_BPS / 1e4)
        elif strategy == "S10" and pos.mfe_bps >= S10_TRAILING_TRIGGER:
            trailing_bps = pos.mfe_bps - S10_TRAILING_OFFSET
            if unrealized <= trailing_bps:
                exit_reason = "s10_trailing"
                exit_price = entry_price * (1 + direction * trailing_bps / 1e4)
        # Dead-timeout early exit (v11.7.2, walk-forward 4/4 validated).
        # Checked last so stops/trailing take precedence.
        if (not exit_reason
                and (target_exit - now).total_seconds() <= DEAD_TIMEOUT_LEAD_HOURS * 3600
                and pos.mfe_bps <= DEAD_TIMEOUT_MFE_CAP_BPS
                and pos.mae_bps <= DEAD_TIMEOUT_MAE_FLOOR_BPS
                and unrealized <= pos.mae_bps + DEAD_TIMEOUT_SLACK_BPS):
            exit_reason = "dead_timeout"
            # exit_price stays at st.price (live mark)
        if exit_reason:
            close_position(sym, exit_price, now, exit_reason, bot)
            exits += 1

    return exits


def close_position(sym: str, exit_price: float, now: datetime, reason: str, bot) -> None:
    """Exit a position, record the trade, and update portfolio state.

    Concurrent paths (check_exits + api_close_symbol + api_pause) can target
    the same symbol — without a mutex they would each call execute_close,
    sending duplicate orders. bot._closing reserves the symbol under lock;
    the second caller returns silently and the in-flight call finishes.
    """
    with bot._pos_lock:
        if sym not in bot.positions or sym in bot._closing:
            return
        bot._closing.add(sym)
    try:
        return _close_position_inner(sym, exit_price, now, reason, bot)
    finally:
        with bot._pos_lock:
            bot._closing.discard(sym)


def _close_position_inner(sym: str, exit_price: float, now: datetime, reason: str, bot) -> None:
    """Body of close_position, wrapped by _closing mutex in close_position."""
    # Execute close on exchange FIRST (live mode) — only pop if successful
    if bot._exchange:
        try:
            close_result = execute_close(bot._exchange, bot._hl_info, bot._hl_address, sym)
            if close_result:
                exit_price = close_result["avgPx"]
                # Reconcile pos.size_usdt to actual closed notional. Covers the
                # partial-fill-at-open case where the bot has tracked more
                # notional than HL actually held — pnl bookkeeping uses
                # pos.size_usdt downstream, so updating it here keeps the
                # close P&L aligned with the real cash impact.
                actual_notional = close_result["sz"] * close_result["avgPx"]
                with bot._pos_lock:
                    if sym in bot.positions and close_result["sz"] > 0:
                        pos_now = bot.positions[sym]
                        if abs(pos_now.size_usdt - actual_notional) > 1.0:
                            log.warning(
                                "CLOSE size reconcile %s: tracked=$%.2f filled=$%.2f — adjusting",
                                sym, pos_now.size_usdt, actual_notional)
                            pos_now.size_usdt = actual_notional
            with bot._pos_lock:
                bot._failed_closes.discard(sym)
        except Exception as e:
            with bot._pos_lock:
                bot._failed_closes.add(sym)
            log_event(bot._db, "CLOSE_FAILED", sym, {"reason": reason, "error": str(e)[:200]})
            log.error("EXEC CLOSE FAILED %s: %s — keeping position, will retry next scan", sym, e)
            send_telegram(f"\u274c Close failed {sym}: {e} \u2014 will retry", category="trade")
            return  # don't pop — position stays tracked, retried next scan

    # Live only: fetch actual funding paid on this token during the trade.
    # Flat FUNDING_DRAG_BPS in COST_BPS is a rough estimate; this is precise.
    funding_usdt = 0.0
    if bot._exchange:
        pos_peek = bot.positions.get(sym)
        if pos_peek:
            start_ms = int(pos_peek.entry_time.timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)
            funding_usdt = fetch_position_funding(
                bot._hl_info, bot._hl_address, sym, start_ms, end_ms)

    with bot._pos_lock:
        if sym not in bot.positions:
            return  # closed by concurrent api_pause/check_exits
        pos = bot.positions.pop(sym)

        hold_h = (now - pos.entry_time).total_seconds() / 3600
        # Record final trajectory point at exit
        final_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
        pos.trajectory.append((round(hold_h, 1), round(final_bps, 1)))
        # P&L calc: size_usdt is notional (not margin), so no leverage multiplier.
        # gross_bps = leveraged return (direction * price_change * leverage)
        # pnl = notional * unleveraged_return = notional * (exit/entry - 1) - costs
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
        effective_cost = COST_BPS
        net_bps = gross_bps - effective_cost
        # Live: swap the flat FUNDING_DRAG_BPS estimate for the real number.
        # Funding delta is in USDC (negative = paid). Reapply as bps on notional
        # so the net_bps displayed is also precise. Paper keeps flat model.
        if bot._exchange:
            flat_funding_usdt = -pos.size_usdt * FUNDING_DRAG_BPS / 1e4
            pnl = pos.size_usdt * net_bps / 1e4 + funding_usdt - flat_funding_usdt
            if pos.size_usdt > 0:
                real_funding_bps = (funding_usdt - flat_funding_usdt) / pos.size_usdt * 1e4
                net_bps = net_bps + real_funding_bps
        else:
            pnl = pos.size_usdt * net_bps / 1e4

        bot._total_pnl += pnl
        balance = bot._capital + bot._total_pnl
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
            log_event(bot._db, "KILL_SWITCH", None,
                      {"total_pnl": round(bot._total_pnl, 2), "cap": TOTAL_LOSS_CAP})

        # Cooldown
        bot._cooldowns[sym] = time.time() + COOLDOWN_HOURS * 3600

    # Kill-switch telegram (outside lock — I/O)
    if bot._total_pnl <= TOTAL_LOSS_CAP:
        send_telegram(
            f"\U0001f6d1 KILL-SWITCH: P&L ${bot._total_pnl:.2f} < cap ${TOTAL_LOSS_CAP:.0f} \u2014 bot paused",
            category="trade")

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
        funding_usdt=round(funding_usdt, 4),
    )
    bot.trades.append(trade)
    write_trade(trade, bot._db)
    write_trajectory(sym, pos, bot._db)

    n = len(bot.trades)
    balance = bot._capital + bot._total_pnl
    wr = bot._wins / n * 100 if n > 0 else 0
    arrow = "\u2713" if pnl > 0 else "\u2717"
    log.info("%s %s %s %s | %.0fh | %s | gross %+.1f | net %+.1f | $%+.2f | mae %+.0f | mfe %+.0f | bal $%.0f (#%d %.0f%%)",
             arrow, pos.strategy, trade.direction, sym, hold_h, reason,
             gross_bps, net_bps, pnl, pos.mae_bps, pos.mfe_bps, balance, n, wr)
    emoji = "\U0001f7e9" if pnl > 0 else "\U0001f7e5"
    send_telegram(
        f"{emoji} CLOSE {pos.strategy} {trade.direction} {sym} | {net_bps:+.0f} bps | ${pnl:+.2f} | bal ${balance:.0f}",
        category="trade")


# ── Entry Logic ──────────────────────────────────────────────────────

def rank_and_enter(signals: list, now: datetime, bot) -> int:
    """Sort signals by z-score, apply position limits, create positions.

    Returns count of new entries.
    """
    # Priority: highest z-score first (strongest statistical edge), then
    # by signal strength within same z. This ensures S1/S8 get slots
    # before S5 when multiple signals fire simultaneously.
    signals.sort(key=lambda s: (s["z"], s["strength"]), reverse=True)

    # Snapshot positions under lock once, then track counters locally to
    # prevent "dict changed size during iteration" if /api/close or /api/pause
    # runs concurrently. Local counters are updated as we add new positions.
    with bot._pos_lock:
        positions_snapshot = list(bot.positions.values())
    n_total = len(positions_snapshot)
    n_longs = sum(1 for p in positions_snapshot if p.direction == 1)
    n_shorts = sum(1 for p in positions_snapshot if p.direction == -1)
    n_macro = sum(1 for p in positions_snapshot if p.strategy in MACRO_STRATEGIES)
    n_token_sig = sum(1 for p in positions_snapshot if p.strategy not in MACRO_STRATEGIES)
    sector_counts: dict[str, int] = {}
    for _p in positions_snapshot:
        _s = TOKEN_SECTOR.get(_p.symbol)
        if _s:
            sector_counts[_s] = sector_counts.get(_s, 0) + 1

    entries = 0
    seen_symbols: set = set()       # one entry per symbol per scan
    drift = compute_signal_drift(bot.trades)  # compute once, not per candidate
    for sig in signals:
        sym = sig["symbol"]
        side = "LONG" if sig["direction"] == 1 else "SHORT"

        if n_total >= MAX_POSITIONS:
            log.debug("SKIP %s %s %s: max_positions", sig["strategy"], side, sym)
            log_event(bot._db, "SKIP", sym, {"strategy": sig["strategy"], "dir": side, "reason": "max_positions"})
            break

        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)

        # Blacklist: tokens structurally net-negative on walk-forward (v11.4.10)
        if sym in TRADE_BLACKLIST:
            log.debug("SKIP %s %s %s: blacklist", sig["strategy"], side, sym)
            log_event(bot._db, "SKIP", sym,
                      {"strategy": sig["strategy"], "dir": side, "reason": "blacklist"})
            continue

        if sig["direction"] == 1 and n_longs >= MAX_SAME_DIRECTION:
            log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
            continue
        if sig["direction"] == -1 and n_shorts >= MAX_SAME_DIRECTION:
            log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
            continue

        # Slot reservation: macro vs token-level signals (counters tracked locally)
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
        if sym_sector and sector_counts.get(sym_sector, 0) >= MAX_PER_SECTOR:
            log.debug("SKIP %s %s %s: max_sector (%s)", sig["strategy"], side, sym, sym_sector)
            continue

        st = bot.states[sym]

        # OI gate: block LONG entries when OI has fallen heavily in 24h.
        # Longs unwinding = bearish flow not yet exhausted, LONG catches a
        # falling knife. Backtest walk-forward 4/4 (28m/12m/6m/3m), zero DD
        # penalty. Inactive until ~24h of OI history after restart (fail-open).
        if sig["direction"] == 1:
            oi_d = oi_delta_24h_bps(st.oi_history)
            if oi_d is not None and oi_d < -OI_LONG_GATE_BPS:
                log.info("SKIP %s LONG %s: oi_gate Δ24h=%+.0f bps (<%.0f)",
                         sig["strategy"], sym, oi_d, -OI_LONG_GATE_BPS)
                log_event(bot._db, "SKIP", sym,
                          {"strategy": sig["strategy"], "dir": "LONG",
                           "reason": "oi_gate", "oi_delta_24h_bps": round(oi_d, 1)})
                continue
        hold_h = sig.get("hold_hours", HOLD_HOURS_DEFAULT)
        target_exit = now + timedelta(hours=hold_h)
        current_capital = bot._capital + bot._total_pnl
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
        # Live: actual filled notional = sz_rounded × avgPx (drifts from requested
        # `size` by szDecimals rounding). Update size_usdt so reconcile +
        # P&L track exchange reality. Paper: size_usdt == requested size.
        filled_size = size
        if bot._exchange:
            try:
                fill = execute_open(
                    bot._exchange, bot._hl_info, bot._hl_address, bot._sz_decimals,
                    sym, sig["direction"] == 1, size, st.price)
                entry_price = fill["avgPx"]
                filled_size = fill["sz"] * entry_price
                send_telegram(
                    f"\U0001f7e2 OPEN {sig['strategy']} {side} {sym} @ ${entry_price:.4f} | ${filled_size:.0f}",
                    category="trade")
            except Exception as e:
                log.error("EXEC OPEN FAILED %s %s: %s", sym, sig["strategy"], e)
                send_telegram(f"\u274c Open failed {sym} {sig['strategy']}: {e}", category="trade")
                continue

        ctx = sig.get("ctx", {})
        with bot._pos_lock:
            bot.positions[sym] = Position(
                symbol=sym, direction=sig["direction"],
                strategy=sig["strategy"],
                entry_price=entry_price, entry_time=now,
                size_usdt=filled_size, signal_info=sig["info"],
                target_exit=target_exit,
                trajectory=[(0.0, 0.0)],  # t=0 anchor point
                stop_bps=sig.get("stop_bps", 0.0),
                entry_oi_delta=float(ctx.get("oi_delta", 0.0)),
                entry_crowding=int(ctx.get("crowding", 0) or 0),
                entry_confluence=int(ctx.get("confluence", 0) or 0),
                entry_session=ctx.get("session", "") or "",
            )

        # Update local counters (avoid re-reading bot.positions mid-scan)
        n_total += 1
        if sig["direction"] == 1:
            n_longs += 1
        else:
            n_shorts += 1
        if sig["strategy"] in MACRO_STRATEGIES:
            n_macro += 1
        else:
            n_token_sig += 1
        if sym_sector:
            sector_counts[sym_sector] = sector_counts.get(sym_sector, 0) + 1
        entries += 1

        log.info("\u2192 %s %s %s @ $%.4f | %s | $%.0f | exit ~%s | %d/%d pos",
                 sig["strategy"], side, sym, entry_price, sig["info"],
                 size, target_exit.strftime("%m-%d %H:%M"),
                 n_total, MAX_POSITIONS)

    return entries
