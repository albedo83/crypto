"""Hyperliquid SDK interaction — init, order execution, reconciliation.

All functions are standalone (not methods). Only imported/used when HL_MODE=live.
"""

from __future__ import annotations

import logging
import time

from .config import TRADE_SYMBOLS, LEVERAGE, HL_EQUITY_MODE

log = logging.getLogger("multisignal")


def init_exchange(private_key: str, account_address: str = "") -> tuple:
    """Initialize Hyperliquid SDK for live trading.

    Lazy-imports eth_account and hyperliquid SDK so paper mode has zero
    SDK dependency. Sets 2x cross leverage on all traded symbols.

    If `account_address` is provided, the SDK signs orders with `private_key`
    (an authorized API/agent wallet) but trades on `account_address`'s
    perps account (the master wallet). Required when funds are held by a
    master wallet that has authorized this private key as an agent.

    Returns (exchange, hl_info, address, sz_decimals).
    """
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info as HLInfo

    if not private_key:
        log.critical("HL_MODE=live but HL_PRIVATE_KEY not set — aborting")
        raise SystemExit(1)

    wallet = Account.from_key(private_key)
    if account_address:
        exchange = Exchange(wallet, account_address=account_address)
        address = account_address
    else:
        exchange = Exchange(wallet)
        address = wallet.address
    hl_info = HLInfo(skip_ws=True)

    # Load szDecimals for proper order size rounding
    sz_decimals: dict[str, int] = {}
    meta = hl_info.meta()
    for asset in meta["universe"]:
        sz_decimals[asset["name"]] = asset["szDecimals"]
    missing_sz = [s for s in TRADE_SYMBOLS if s not in sz_decimals]
    if missing_sz:
        log.critical("Missing szDecimals for: %s — orders will use fallback rounding", missing_sz)

    # Set leverage 2x cross on all traded symbols
    for sym in TRADE_SYMBOLS:
        try:
            exchange.update_leverage(int(LEVERAGE), sym, is_cross=True)
        except Exception as e:
            log.warning("Leverage set failed for %s: %s", sym, e)

    if account_address:
        log.info("LIVE MODE: signer %s… → trading on %s… (agent), leverage %dx set on %d symbols",
                 wallet.address[:10], address[:10], int(LEVERAGE), len(TRADE_SYMBOLS))
    else:
        log.info("LIVE MODE: wallet %s…, leverage %dx set on %d symbols",
                 address[:10], int(LEVERAGE), len(TRADE_SYMBOLS))

    return exchange, hl_info, address, sz_decimals


def execute_open(exchange, hl_info, address: str, sz_decimals: dict,
                 sym: str, is_buy: bool, size_usdt: float, price: float) -> dict:
    """Place market order on Hyperliquid.

    Returns {"avgPx": float, "sz": float} so the caller can compute the real
    filled notional (sz × avgPx) and update Position.size_usdt. Rounding to
    szDecimals introduces a quantity discrepancy vs requested notional; using
    the filled notional keeps reconcile + P&L accurate vs exchange reality.
    """
    if price <= 0:
        raise ValueError(f"Invalid price for {sym}: {price}")
    sz = size_usdt / price
    dec = sz_decimals.get(sym, 2)
    sz = round(sz, dec)
    if sz <= 0 or sz * price < 10:
        raise ValueError(f"Order too small: {sz} {sym} = ${sz * price:.1f} (szDec={dec}, need ≥$10)")

    side_str = "BUY" if is_buy else "SELL"
    log.info("EXEC OPEN: %s %s sz=%s (~$%.0f)", side_str, sym, sz, sz * price)
    result = exchange.market_open(sym, is_buy, sz, slippage=0.01)

    # Validate response
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        raise RuntimeError(f"Order returned empty statuses: {result}")
    first = statuses[0]
    if "error" in str(first).lower():
        raise RuntimeError(f"Order error: {first}")

    # Extract fill price from order response (immediate, no API lag)
    filled = first.get("filled") if isinstance(first, dict) else None
    if filled and "avgPx" in filled:
        return {"avgPx": float(filled["avgPx"]), "sz": sz}

    # Fallback: query fills API (may lag a few hundred ms behind L1)
    try:
        time.sleep(0.5)  # brief wait for indexer to catch up
        fills = hl_info.user_fills_by_time(
            address, int((time.time() - 30) * 1000))
        for f in reversed(fills):
            if f.get("coin") == sym:
                return {"avgPx": float(f["px"]), "sz": sz}
    except Exception as e:
        log.warning("Fill lookup failed: %s — using market price", e)

    return {"avgPx": price, "sz": sz}  # last resort fallback


def execute_close(exchange, hl_info, address: str, sym: str) -> float | None:
    """Close position on Hyperliquid. Returns fill price or None."""
    log.info("EXEC CLOSE: %s", sym)
    result = exchange.market_close(sym, slippage=0.01)

    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        raise RuntimeError(f"Close returned empty statuses: {result}")
    first = statuses[0]
    if "error" in str(first).lower():
        raise RuntimeError(f"Close error: {first}")

    # Extract fill price from order response (immediate, no API lag)
    filled = first.get("filled") if isinstance(first, dict) else None
    if filled and "avgPx" in filled:
        return float(filled["avgPx"])

    # Fallback: query fills API
    try:
        time.sleep(0.5)
        fills = hl_info.user_fills_by_time(
            address, int((time.time() - 30) * 1000))
        for f in reversed(fills):
            if f.get("coin") == sym:
                return float(f["px"])
    except Exception as e:
        log.warning("Fill lookup failed: %s", e)

    return None  # caller uses st.price as fallback


def fetch_position_funding(hl_info, address: str, coin: str,
                           start_ms: int, end_ms: int) -> float:
    """Sum funding deltas paid on `coin` between start_ms and end_ms.

    Returns USDC amount (negative = we paid, positive = we received).
    Returns 0.0 on any failure — fail-open, caller keeps the trade's pnl
    unchanged rather than breaking the close path.
    """
    if not hl_info:
        return 0.0
    try:
        # HL API requires a time window; grab slightly wider and filter
        hist = hl_info.user_funding_history(address, start_ms, end_ms)
        total = 0.0
        for ev in hist:
            if ev.get("delta", {}).get("coin") != coin:
                continue
            t = int(ev.get("time", 0))
            if t < start_ms or t > end_ms:
                continue
            total += float(ev["delta"].get("usdc", 0))
        return total
    except Exception as e:
        log.warning("Funding lookup failed for %s: %s", coin, e)
        return 0.0


def fetch_account_state(hl_info, address: str) -> dict | None:
    """Fetch real account state from Hyperliquid. Returns equity, unrealized, available, fees."""
    if not hl_info:
        return None
    try:
        state = hl_info.user_state(address)
        spot = hl_info.spot_user_state(address)

        # Spot USDC balance (collateral)
        spot_usdc = 0.0
        for b in spot.get("balances", []):
            if b["coin"] == "USDC":
                spot_usdc = float(b["total"])
                break

        unrealized = 0.0
        for p in state.get("assetPositions", []):
            sz = float(p["position"].get("szi", 0))
            if abs(sz) > 0:
                unrealized += float(p["position"].get("unrealizedPnl", 0))
        # Equity formula:
        #   Default ("spot+unrealized"): legacy formula assuming USDC sits in
        #   spot and is used as collateral via cross-account access. Correct
        #   for the live wallet which keeps idle USDC in spot.
        #   "perps" mode: account_value (perps) + spot_usdc — correct when the
        #   user has transferred all funds to the perps subaccount (Junior).
        if HL_EQUITY_MODE == "perps":
            account_value = float(state.get("marginSummary", {}).get("accountValue", 0))
            equity = account_value + spot_usdc
        else:
            equity = spot_usdc + unrealized

        # Margin used
        margin_used = float(state.get("marginSummary", {}).get("totalMarginUsed", 0))
        available = equity - margin_used

        # Fees: taker fees from fills, funding from funding history
        import time as _time
        start_ms = int((_time.time() - 90 * 86400) * 1000)
        end_ms = int(_time.time() * 1000)
        try:
            fills = hl_info.user_fills_by_time(address, start_ms, end_ms)
            taker_fees = sum(float(f.get("fee", 0)) for f in fills)
            closed_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
        except Exception:
            taker_fees, closed_pnl = 0.0, 0.0
        try:
            funding_hist = hl_info.user_funding_history(address, start_ms, end_ms)
            funding_paid = sum(float(f["delta"]["usdc"]) for f in funding_hist)
        except Exception:
            funding_paid = 0.0

        return {
            "equity": round(equity, 2),
            "unrealized": round(unrealized, 2),
            "margin_used": round(margin_used, 2),
            "available": round(available, 2),
            "taker_fees": round(taker_fees, 2),
            "funding_paid": round(funding_paid, 2),
            "closed_pnl": round(closed_pnl, 2),
        }
    except Exception as e:
        log.warning("Account state fetch failed: %s", e)
        return None


def reconcile(hl_info, address: str, bot_positions: dict, send_telegram_fn) -> None:
    """Compare bot positions vs exchange. Log and alert on discrepancies.

    Checks three things: missing-on-exchange (ghost), missing-in-bot (orphan),
    and — new in v11.4.1 — direction/size mismatches on symbols present in both.
    Size comparison uses coin-unit quantities (szi), not notional. Notional
    drifts naturally with mark price — a LONG up +15% would falsely appear as
    a size mismatch if we compared positionValue to the entry notional.
    Tolerance 5% on coin quantity to allow for szDecimals rounding.
    """
    if not hl_info:
        return
    try:
        state = hl_info.user_state(address)
        exchange_positions: dict[str, dict] = {}
        for pos in state.get("assetPositions", []):
            p = pos["position"]
            sz = float(p.get("szi", 0))
            if abs(sz) > 0:
                exchange_positions[p["coin"]] = {"szi": sz}

        bot_syms = set(bot_positions.keys())
        exch_syms = set(exchange_positions.keys())
        orphans = exch_syms - bot_syms
        ghosts = bot_syms - exch_syms
        common = bot_syms & exch_syms

        if orphans:
            log.warning("RECONCILE: orphan positions on exchange: %s", orphans)
            send_telegram_fn(f"⚠️ Orphan on exchange (not in bot): {orphans}",
                             category="reconcile")
        if ghosts:
            log.warning("RECONCILE: ghost positions in bot (not on exchange): %s", ghosts)
            send_telegram_fn(f"⚠️ Ghost in bot (not on exchange): {ghosts}",
                             category="reconcile")

        for sym in common:
            ex = exchange_positions[sym]
            bot_pos = bot_positions[sym]
            exch_dir = 1 if ex["szi"] > 0 else -1
            if exch_dir != bot_pos.direction:
                log.critical("RECONCILE: DIRECTION MISMATCH %s bot=%s exch=%s",
                             sym, bot_pos.direction, exch_dir)
                send_telegram_fn(
                    f"🚨 DIRECTION MISMATCH {sym}: bot={'LONG' if bot_pos.direction==1 else 'SHORT'} "
                    f"exch={'LONG' if exch_dir==1 else 'SHORT'}",
                    category="reconcile")
                continue
            # Compare coin quantities (invariant under price moves)
            if bot_pos.entry_price > 0:
                bot_coin_qty = bot_pos.size_usdt / bot_pos.entry_price
                exch_coin_qty = abs(ex["szi"])
                if bot_coin_qty > 0:
                    ratio = exch_coin_qty / bot_coin_qty
                    if ratio < 0.95 or ratio > 1.05:
                        log.warning("RECONCILE: SIZE MISMATCH %s bot=%.4f coins exch=%.4f (ratio %.2f)",
                                    sym, bot_coin_qty, exch_coin_qty, ratio)
                        send_telegram_fn(
                            f"⚠️ Size mismatch {sym}: bot={bot_coin_qty:.4f} "
                            f"exch={exch_coin_qty:.4f} coins",
                            category="reconcile")

        if not orphans and not ghosts:
            log.debug("Reconcile OK: %d positions match", len(bot_syms))
    except Exception as e:
        log.warning("Reconcile error: %s", e)
