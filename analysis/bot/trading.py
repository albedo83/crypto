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

from .config import (COST_BPS, FUNDING_DRAG_BPS, MAX_POSITIONS, MAX_SAME_DIRECTION,
                     MAX_PER_SECTOR, MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES,
                     TOKEN_SECTOR, STOP_LOSS_BPS, STOP_LOSS_S8, COOLDOWN_HOURS,
                     HOLD_HOURS_DEFAULT,
                     S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
                     S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
                     S8_INLIFE_PARAMS, S8_INLIFE_Z_THRESHOLD,
                     S8_DEAD_T_H, S8_DEAD_MFE_MAX_BPS,
                     DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
                     DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
                     RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
                     RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
                     OI_LONG_GATE_BPS, TRADE_BLACKLIST, strat_size,
                     get_adaptive_alpha, MACRO_Z_CLIP, MACRO_MULT_MIN, MACRO_MULT_MAX,
                     DISP_GATE_BPS, DISP_GATE_STRATEGIES,
                     TRAJ_CUT_STRATEGIES, TRAJ_CUT_BTC_Z_THRESHOLD,
                     TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H, TRAJ_CUT_TIME_SINCE_MFE_MIN_H,
                     TRAJ_CUT_AT_MAE_SLACK_BPS, TRAJ_CUT_MIN_LOSS_BPS)
from .features import oi_delta_24h_bps
from .models import Position, Trade
from .exchange import execute_open, execute_close, fetch_position_funding
from .persistence import write_trade, write_trajectory
from .db import log_event
from .net import send_telegram

log = logging.getLogger("multisignal")


# ── Shared skip-reason helper (consumed by rank_and_enter & dashboard preview) ──

def signal_skip_reason(bot, sig: dict, *,
                       n_total: int, n_longs: int, n_shorts: int,
                       n_macro: int, n_token: int,
                       sector_counts: dict, disp_24h: float | None = None,
                       check_size_floor: bool = True) -> str | None:
    """Pure check: if `sig` would be skipped by rank_and_enter under the given
    counters, return the reason string; else None ("would enter").

    Mirrors rank_and_enter's check order exactly. Use this in:
      - rank_and_enter itself (single source of truth)
      - dashboard preview (no more drift between displayed status and actual
        scan decision)

    `disp_24h` is the cross-sectional 24h dispersion. Pass None if unknown
    (the disp_gate then skips its check, same as fail-open in production).
    """
    sym = sig["symbol"]
    direction = sig["direction"]
    strategy = sig["strategy"]

    # Already in position
    if sym in bot.positions:
        return "already in position"

    # Cooldown (post-loss)
    if sym in bot._cooldowns and time.time() < bot._cooldowns[sym]:
        return "cooldown"

    # Dispersion gate (S5 / S9 mean-reversion strats)
    if (strategy in DISP_GATE_STRATEGIES and disp_24h is not None
            and disp_24h >= DISP_GATE_BPS):
        return "disp_gate"

    # Total positions cap
    if n_total >= MAX_POSITIONS:
        return "max_positions"

    # Trade blacklist (structurally negative tokens)
    if sym in TRADE_BLACKLIST:
        return "blacklist"

    # Direction cap
    if direction == 1 and n_longs >= MAX_SAME_DIRECTION:
        return "max_long"
    if direction == -1 and n_shorts >= MAX_SAME_DIRECTION:
        return "max_short"

    # Macro / token slot reservation
    if strategy in MACRO_STRATEGIES and n_macro >= MAX_MACRO_SLOTS:
        return "max_macro"
    if strategy not in MACRO_STRATEGIES and n_token >= MAX_TOKEN_SLOTS:
        return "max_token"

    # Sector concentration cap
    sym_sector = TOKEN_SECTOR.get(sym)
    if sym_sector and sector_counts.get(sym_sector, 0) >= MAX_PER_SECTOR:
        return "max_sector"

    # OI gate (LONG only): block when 24h OI delta beats threshold downward
    st = bot.states.get(sym)
    if st is not None and direction == 1:
        oi_d = oi_delta_24h_bps(st.oi_history)
        if oi_d is not None and oi_d < -OI_LONG_GATE_BPS:
            return "oi_gate"

    # Modulator floor: post-modulator size < $10 → exchange would reject
    if check_size_floor:
        current_capital = bot._capital + bot._total_pnl
        size = strat_size(strategy, current_capital)
        alpha = get_adaptive_alpha(strategy, direction)
        if alpha != 0 and bot._btc_z is not None:
            z_clip = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, bot._btc_z))
            mult = max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z_clip))
            size = round(size * mult, 2)
        if size < 10:
            return "modulator_floor"

    return None  # would enter


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
            hours_held = (now - pos.entry_time).total_seconds() / 3600
            if unrealized > pos.mfe_bps:
                pos.mfe_bps = unrealized
                # v12.7.1: track when MFE was last set (used by traj_cut to
                # measure time-since-MFE). Default 0 means MFE was set at
                # entry, so time_since_mfe == hours_held until the first peak.
                pos.mfe_at_h = hours_held
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
        elif pos.manual_stop_usdt is not None and (
                pos.size_usdt * (unrealized - COST_BPS) / 1e4) <= pos.manual_stop_usdt:
            # v12.5.29: compare NET pnl (after estimated COST_BPS) to the
            # user-stated dollar threshold. Earlier v12.5.25 compared gross
            # unrealized × notional, which over-shot the user's threshold by
            # ~COST_BPS in dollars — a $40 stop locked $40 gross but only
            # ~$39.50 net after taker fees, slippage and the flat funding drag.
            # The synthetic exit_price reproduces the user's net target exactly:
            #   size × (gross_bps - COST_BPS) / 1e4 = manual_stop_usdt
            # In live mode close_position overrides exit_price with the real
            # avgPx and reapplies real funding history; the user's net target
            # is preserved up to a few bps of funding noise (FUNDING_DRAG_BPS).
            exit_reason = "manual_stop_set"
            target_gross_bps = pos.manual_stop_usdt / pos.size_usdt * 1e4 + COST_BPS
            exit_price = entry_price * (1 + direction * target_gross_bps / 1e4)
        elif strategy == "S9" and hours_held >= S9_EARLY_EXIT_HOURS and unrealized < S9_EARLY_EXIT_BPS:
            exit_reason = "s9_early_exit"
            exit_price = entry_price * (1 + direction * S9_EARLY_EXIT_BPS / 1e4)
        elif strategy == "S10" and pos.mfe_bps >= S10_TRAILING_TRIGGER:
            trailing_bps = pos.mfe_bps - S10_TRAILING_OFFSET
            if unrealized <= trailing_bps:
                exit_reason = "s10_trailing"
                exit_price = entry_price * (1 + direction * trailing_bps / 1e4)
        elif (strategy == "S8" and direction == 1
                and hours_held >= S8_DEAD_T_H
                and pos.mfe_bps <= S8_DEAD_MFE_MAX_BPS):
            # v12.6.0 — Dead-in-water exit. If at T+8h the S8 LONG has never
            # crossed +0.5% MFE, the capitulation thesis is invalidated. mfe_bps
            # is monotonically non-decreasing so the check is naturally
            # idempotent: once MFE crosses the ceiling, the rule never fires.
            # Walk-forward 28m/12m/6m all positive ΔPnL (+207872/+1723/+138pp),
            # 3m null intersection (rule didn't trigger), 0 DD degradation,
            # +8.39pp DD improvement on 6m. See backtests/s8_dead_in_water_walkforward.md.
            # Opposite tail of S8_INLIFE_PARAMS (MFE >= 300/1500) — no overlap.
            exit_reason = "s8_dead_in_water"
            # exit_price stays at st.price (live mark)
        elif strategy == "S8" and bot._btc_z is not None and S8_INLIFE_PARAMS:
            # v12.5.30 — regime-conditioned MFE trail.
            # Walk-forward 4/4 strict + null-shuffle z=+10.52 (12/13 shuffles
            # produce negative ΔPnL, real beats by ~10σ). See
            # backtests/inlife_exit_results.md.
            z = bot._btc_z
            if z < -S8_INLIFE_Z_THRESHOLD:
                bucket = "bear"
            elif z > S8_INLIFE_Z_THRESHOLD:
                bucket = "bull"
            else:
                bucket = "neutral"
            act, off = S8_INLIFE_PARAMS.get(bucket, (99999, 0))
            if pos.mfe_bps >= act:
                trailing_bps = pos.mfe_bps - off
                if unrealized <= trailing_bps:
                    exit_reason = "s8_inlife"
                    exit_price = entry_price * (1 + direction * trailing_bps / 1e4)
        # v12.7.1 — Trajectory cut (regime-conditioned, S5 only by default).
        # Codifies the user's manual_close intuition: cut a position whose
        # curve is in steep decline from MFE, currently pinned near MAE,
        # meaningfully losing, AND we're in a bear macro regime where these
        # patterns historically materialize into catastrophe rather than
        # recovery. Walk-forward 4/4 strict on R1 (btc_z < -0.5) via
        # `backtest_trajectory_cut_v2.py`. Null-shuffle confirms the regime
        # filter is the thing doing the work (real edge above null
        # distribution, p<0.08 on 13 shuffles, all nulls below real).
        # Without the regime gate, the rule fails walk-forward 1/4 (cuts
        # too many recoverable positions in choppy/bull markets).
        # Kill-switch: empty TRAJ_CUT_STRATEGIES in config.py.
        if (not exit_reason
                and strategy in TRAJ_CUT_STRATEGIES
                and bot._btc_z is not None
                and bot._btc_z < TRAJ_CUT_BTC_Z_THRESHOLD
                and unrealized <= TRAJ_CUT_MIN_LOSS_BPS
                and (unrealized - pos.mae_bps) <= TRAJ_CUT_AT_MAE_SLACK_BPS):
            t_since_mfe = hours_held - pos.mfe_at_h
            if t_since_mfe >= TRAJ_CUT_TIME_SINCE_MFE_MIN_H:
                decline_rate = (pos.mfe_bps - unrealized) / max(t_since_mfe, 1.0)
                if decline_rate >= TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H:
                    exit_reason = "traj_cut"
                    # exit_price stays at st.price (live mark) — the rule
                    # fires on observed price evolution, not a synthetic
                    # stop level.
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
                # v12.5.25 BUGFIX: the prior reconcile overwrote pos.size_usdt
                # with `sz × close_price` (the close-time notional). Since
                # pos.size_usdt is the OPEN notional and P&L uses
                # `pos.size_usdt × bps`, that overwrite inflated profits for
                # winners (and shrank losses for losers) by `(close/open − 1)`.
                # We now compare in COINS, not notional. If coin count differs
                # significantly from what we expected to close, log a warning
                # but DO NOT overwrite pos.size_usdt — the open notional is
                # the right quantity for the P&L formula.
                with bot._pos_lock:
                    if sym in bot.positions and close_result["sz"] > 0:
                        pos_now = bot.positions[sym]
                        expected_coins = (pos_now.size_usdt / pos_now.entry_price
                                          if pos_now.entry_price > 0 else 0)
                        actual_coins = close_result["sz"]
                        if expected_coins > 0 and abs(actual_coins - expected_coins) / expected_coins > 0.01:
                            log.warning(
                                "CLOSE coin reconcile %s: expected=%.4f filled=%.4f — partial fill?",
                                sym, expected_coins, actual_coins)
                            # If partial, scale size_usdt proportionally to the
                            # actual coin count (preserves the open-notional
                            # semantics: size_usdt = coins × open_price).
                            pos_now.size_usdt = actual_coins * pos_now.entry_price
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
    # Note: pos_peek read outside _pos_lock. Safe because (a) entry_time never
    # mutates after Position creation, (b) the _closing mutex below guards
    # against concurrent close races on the same sym.
    # Time-boxed via thread + future.result(timeout=) so a slow HL history
    # endpoint can't hang the close path; falls back to 0 (fail-open).
    funding_usdt = 0.0
    if bot._exchange:
        pos_peek = bot.positions.get(sym)
        if pos_peek:
            start_ms = int(pos_peek.entry_time.timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTO
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(fetch_position_funding,
                                    bot._hl_info, bot._hl_address, sym,
                                    start_ms, end_ms)
                    funding_usdt = fut.result(timeout=5.0)
            except _FTO:
                log.warning("Funding fetch timed out for %s — using 0.0", sym)
            except Exception as e:
                log.warning("Funding fetch error for %s: %s — using 0.0", sym, e)

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

        # Track consecutive losses (observation only — no penalty currently wired).
        if pnl > 0:
            bot._consecutive_losses = 0
        else:
            bot._consecutive_losses += 1

        # Cooldown
        bot._cooldowns[sym] = time.time() + COOLDOWN_HOURS * 3600

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
    log_event(bot._db, "CLOSE", sym, {
        "strategy": pos.strategy, "dir": trade.direction,
        "exit_price": round(exit_price, 6), "hold_h": round(hold_h, 1),
        "gross_bps": round(gross_bps, 1), "net_bps": round(net_bps, 1),
        "pnl_usdt": round(pnl, 2), "mae_bps": round(pos.mae_bps, 1),
        "mfe_bps": round(pos.mfe_bps, 1), "reason": reason,
    })

    # v12.5.26: per-trade P&L coherence check. Compare the bot-recorded P&L
    # against the coin-based ground truth and the expected cost/funding adjustments.
    # Catches future regressions where size_usdt or bps math goes off.
    coins = pos.size_usdt / pos.entry_price if pos.entry_price > 0 else 0
    coin_pnl_gross = pos.direction * coins * (exit_price - pos.entry_price)
    expected_cost = -pos.size_usdt * effective_cost / 1e4
    if bot._exchange:
        expected_funding_adj = funding_usdt - flat_funding_usdt
    else:
        expected_funding_adj = 0.0
    expected_pnl = coin_pnl_gross + expected_cost + expected_funding_adj
    discrepancy = pnl - expected_pnl
    if abs(discrepancy) > 1.0 or (abs(expected_pnl) > 0 and abs(discrepancy / expected_pnl) > 0.05):
        log.warning(
            "PNL_DISCREPANCY %s: recorded=$%.2f coin-based=$%.2f Δ$%.2f (size=$%.2f coins=%.4f)",
            sym, pnl, expected_pnl, discrepancy, pos.size_usdt, coins)
        log_event(bot._db, "PNL_DISCREPANCY", sym, {
            "recorded_pnl": round(pnl, 4),
            "coin_pnl_gross": round(coin_pnl_gross, 4),
            "expected_cost": round(expected_cost, 4),
            "expected_funding": round(expected_funding_adj, 4),
            "expected_pnl_total": round(expected_pnl, 4),
            "discrepancy": round(discrepancy, 4),
            "size_usdt": round(pos.size_usdt, 4),
            "coins": round(coins, 6),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
        })
        send_telegram(
            f"⚠️ PNL_DISCREPANCY {sym}: recorded ${pnl:.2f} vs expected ${expected_pnl:.2f} "
            f"(Δ${discrepancy:+.2f}) — check trading.close_position",
            category="reconcile")

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
        # NOTE: v12.1.0 introduced a static `S5_SHORT_BLACKLIST` (DOGE, SNX,
        # LDO, AAVE, MINA). v12.2.0 replaced it with an adaptive modulator
        # (config.ADAPTIVE_ALPHA_DIR `(S5, -1) → -0.5`). The modulator
        # reduces ALL S5 SHORT trades in BULL regime regardless of token,
        # which catches SEI-type events and provides better walk-forward
        # PnL/DD trade-off than the static list.

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
        # Adaptive macro modulator (v11.10.0 + v12.2.0): scale by
        # `1 + α × btc_z`, where btc_z is the rolling z-score of BTC ret_30d
        # and α depends on (strategy, direction). v11.10.0: S1/S8/S9 any
        # direction. v12.2.0: S5 SHORT specifically.
        # See config.get_adaptive_alpha + backtest_adaptive_robustness.py.
        alpha = get_adaptive_alpha(sig["strategy"], sig["direction"])
        modulator_mult: float | None = None
        modulator_z: float | None = None
        if alpha != 0 and bot._btc_z is not None:
            z_clip = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, bot._btc_z))
            modulator_mult = max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z_clip))
            modulator_z = bot._btc_z
            size = round(size * modulator_mult, 2)
        # v11.10.1: skip explicit if post-modulator size below the live exchange
        # minimum ($10). Without this, execute_open raises ValueError and the
        # try/except logs an alarming "Order too small" — clean SKIP instead.
        if size < 10:
            log.debug("SKIP %s %s %s: post-modulator size $%.2f < $10",
                      sig["strategy"], side, sym, size)
            log_event(bot._db, "SKIP", sym, {
                "strategy": sig["strategy"], "dir": side,
                "reason": "modulator_floor",
                "size": size, "btc_z": round(modulator_z, 3) if modulator_z is not None else None,
                "mult": round(modulator_mult, 3) if modulator_mult is not None else None,
            })
            continue

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
        basket_at_entry = getattr(bot, "_basket_metrics", None)
        from .features import compute_entry_side_imbalance as _esi_fn
        esi_at_entry = _esi_fn(sig["direction"], st.price,
                                st.impact_bid, st.impact_ask)
        log_event(bot._db, "OPEN", sym, {
            "strategy": sig["strategy"], "dir": side,
            "entry_price": round(entry_price, 6), "size_usdt": round(filled_size, 2),
            "target_exit": target_exit.isoformat(),
            "stop_bps": round(sig.get("stop_bps", 0.0), 1),
            "btc_z": round(modulator_z, 3) if modulator_z is not None else None,
            "mult": round(modulator_mult, 3) if modulator_mult is not None else None,
            "basket_mean_corr_btc": basket_at_entry["mean_corr_to_btc"] if basket_at_entry else None,
            "basket_max_pairwise": basket_at_entry["max_pairwise_corr"] if basket_at_entry else None,
            "basket_effective_n": basket_at_entry["effective_n"] if basket_at_entry else None,
            "basket_n_positions": basket_at_entry["n_positions"] if basket_at_entry else None,
            "entry_side_imbalance": esi_at_entry["esi"] if esi_at_entry else None,
            "book_skew": esi_at_entry["skew"] if esi_at_entry else None,
            "book_spread_bps": esi_at_entry["spread_bps"] if esi_at_entry else None,
        })

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
