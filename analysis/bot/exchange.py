"""Hyperliquid SDK interaction — init, order execution, reconciliation.

All functions are standalone (not methods). Only imported/used when HL_MODE=live.
"""

from __future__ import annotations

import logging
import time

from .config import TRADE_SYMBOLS, LEVERAGE

log = logging.getLogger("multisignal")


def init_exchange(private_key: str) -> tuple:
    """Initialize Hyperliquid SDK for live trading.

    Lazy-imports eth_account and hyperliquid SDK so paper mode has zero
    SDK dependency. Sets 2x cross leverage on all traded symbols.

    Returns (exchange, hl_info, address, sz_decimals).
    """
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info as HLInfo

    if not private_key:
        log.critical("HL_MODE=live but HL_PRIVATE_KEY not set — aborting")
        raise SystemExit(1)

    wallet = Account.from_key(private_key)
    exchange = Exchange(wallet)
    hl_info = HLInfo(skip_ws=True)
    address = wallet.address

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

    log.info("LIVE MODE: wallet %s…, leverage %dx set on %d symbols",
             address[:10], int(LEVERAGE), len(TRADE_SYMBOLS))

    return exchange, hl_info, address, sz_decimals


def execute_open(exchange, hl_info, address: str, sz_decimals: dict,
                 sym: str, is_buy: bool, size_usdt: float, price: float) -> float:
    """Place market order on Hyperliquid. Returns fill price or raises."""
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
        return float(filled["avgPx"])

    # Fallback: query fills API (may lag a few hundred ms behind L1)
    try:
        time.sleep(0.5)  # brief wait for indexer to catch up
        fills = hl_info.user_fills_by_time(
            address, int((time.time() - 30) * 1000))
        for f in reversed(fills):
            if f.get("coin") == sym:
                return float(f["px"])
    except Exception as e:
        log.warning("Fill lookup failed: %s — using market price", e)

    return price  # last resort fallback


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


def fetch_account_state(hl_info, address: str) -> dict | None:
    """Fetch real account state from Hyperliquid. Returns equity, unrealized, available."""
    if not hl_info:
        return None
    try:
        state = hl_info.user_state(address)
        spot = hl_info.spot_user_state(address)

        # Spot USDC total = real equity
        equity = 0.0
        for b in spot.get("balances", []):
            if b["coin"] == "USDC":
                equity = float(b["total"])
                break

        # Unrealized from perps
        unrealized = 0.0
        for p in state.get("assetPositions", []):
            sz = float(p["position"].get("szi", 0))
            if abs(sz) > 0:
                unrealized += float(p["position"].get("unrealizedPnl", 0))

        # Margin used
        margin_used = float(state.get("marginSummary", {}).get("totalMarginUsed", 0))
        available = equity - margin_used

        return {
            "equity": round(equity, 2),
            "unrealized": round(unrealized, 2),
            "margin_used": round(margin_used, 2),
            "available": round(available, 2),
        }
    except Exception as e:
        log.warning("Account state fetch failed: %s", e)
        return None


def reconcile(hl_info, address: str, bot_positions: dict, send_telegram_fn) -> None:
    """Compare bot positions vs exchange. Log and alert on discrepancies."""
    if not hl_info:
        return
    try:
        state = hl_info.user_state(address)
        exchange_positions: set[str] = set()
        for pos in state.get("assetPositions", []):
            p = pos["position"]
            sz = float(p.get("szi", 0))
            if abs(sz) > 0:
                exchange_positions.add(p["coin"])

        bot_syms = set(bot_positions.keys())
        orphans = exchange_positions - bot_syms
        ghosts = bot_syms - exchange_positions

        if orphans:
            log.warning("RECONCILE: orphan positions on exchange: %s", orphans)
            send_telegram_fn(f"⚠️ Orphan on exchange (not in bot): {orphans}")
        if ghosts:
            log.warning("RECONCILE: ghost positions in bot (not on exchange): %s", ghosts)
            send_telegram_fn(f"⚠️ Ghost in bot (not on exchange): {ghosts}")
        if not orphans and not ghosts:
            log.debug("Reconcile OK: %d positions match", len(bot_syms))
    except Exception as e:
        log.warning("Reconcile error: %s", e)
