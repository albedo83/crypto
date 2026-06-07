"""Hyperliquid SDK interaction — init, order execution, reconciliation.

All functions are standalone (not methods). Only imported/used when HL_MODE=live.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTO

from .config import TRADE_SYMBOLS, LEVERAGE, MIN_FILL_ABORT_USDT

log = logging.getLogger("multisignal")

# v12.5.29: bounded executor + per-call timeout cap for SDK calls. Hyperliquid's
# SDK doesn't expose request timeouts on market_open/close — a hung HTTP call
# previously stalled close_position indefinitely with the _closing mutex held,
# locking the symbol out of management until reboot. ThreadPoolExecutor.submit
# + future.result(timeout=) lets the caller unblock; the underlying thread
# keeps running (best-effort cancel) but the bot resumes scanning.
#
# NOTE: we deliberately do NOT wrap SDK access in a process-wide lock. A single
# hung call would otherwise paralyze every subsequent SDK operation until
# restart (lock acquisition would itself time out). requests.Session is
# documented thread-safe for stateless GET/POST; if torn responses ever surface
# under load, a lock can be added later — but the catastrophic-failure mode
# of lock-based serialization is worse than the rare-race mode without it.
_SDK_MAX_WORKERS = 6
_SDK_EXECUTOR = ThreadPoolExecutor(max_workers=_SDK_MAX_WORKERS,
                                   thread_name_prefix="hl-sdk")
# v12.5.31: track in-flight futures so a sustained HL outage that pegs all
# workers can be detected before the (max_workers+1)th caller blocks at
# submit(). The set is mutated under _SDK_INFLIGHT_LOCK; done_callback removes
# entries when the underlying thread eventually completes (could be long after
# the calling _sdk_call already raised TimeoutError).
_SDK_INFLIGHT: set = set()
_SDK_INFLIGHT_LOCK = threading.Lock()


def _sdk_call(fn, *args, timeout: float = 20.0, **kwargs):
    """Run a Hyperliquid SDK call with a timeout cap.

    Raises TimeoutError if the call doesn't return within `timeout` seconds.
    The underlying thread may still be running — acceptable because SDK calls
    are idempotent reads (user_state, user_fills_by_time) or already-sent
    orders whose effect is reconciled on the next scan.
    """
    fut = _SDK_EXECUTOR.submit(fn, *args, **kwargs)
    with _SDK_INFLIGHT_LOCK:
        _SDK_INFLIGHT.add(fut)
        inflight_n = len(_SDK_INFLIGHT)

    def _cleanup(f):
        with _SDK_INFLIGHT_LOCK:
            _SDK_INFLIGHT.discard(f)
    fut.add_done_callback(_cleanup)

    # v12.5.31: warn when the executor is close to saturation. At max_workers
    # in-flight, the next _sdk_call will block at submit() until a worker
    # frees, defeating the timeout cap. Log loudly so the operator knows HL
    # latency is the proximate cause of any subsequent slowdown.
    if inflight_n >= _SDK_MAX_WORKERS - 1:
        log.warning("SDK executor saturated: %d/%d futures in-flight — "
                    "HL latency stalling workers, next call may block at submit()",
                    inflight_n, _SDK_MAX_WORKERS)

    try:
        return fut.result(timeout=timeout)
    except _FTO:
        # Best-effort cancel: if the worker has already started executing
        # (which it has if we hit the timeout), Future.cancel() returns False
        # and is a no-op. The worker keeps running until HL responds; once it
        # does, _cleanup removes the future from _SDK_INFLIGHT.
        fut.cancel()
        raise TimeoutError(f"SDK call {fn.__name__} exceeded {timeout}s")


# v12.16.6 — observed recurring HTTP 429s at 4h candle close (cascade of
# fetch_candles × 35 tokens + reconcile + multi-order in a tight burst).
# v12.17.0 — tightened delays (0.5s/1.5s) to cap blocking at ~2s vs original
# 9s ; the 4h-close burst typically clears in <3s so longer delays just blocked
# the calling thread without benefit. _is_429 also strengthened to cover
# `status_code` attribute, full args scan, and string-fallback.
_RETRY_DELAYS_429 = (0.5, 1.5)


def _is_429(exc: Exception) -> bool:
    """Return True if `exc` is a Hyperliquid rate-limit (HTTP 429) error.

    Robust to SDK exception-shape drift: checks `status_code` attribute
    (Hyperliquid's ClientError sets this), then any arg == 429 (covers
    the historical `ClientError(429, ...)` tuple signature), then a
    last-resort string match on `'429'` in the exception text (catches
    wrapped errors like ConnectionError("HTTP 429 ...")).
    """
    if getattr(exc, "status_code", None) == 429:
        return True
    for a in getattr(exc, "args", ()):
        if a == 429 or a == "429":
            return True
    try:
        return "429" in str(exc)
    except Exception:
        return False


def _sdk_call_with_429_retry(fn, *args, timeout: float = 20.0, **kwargs):
    """Wrapper over `_sdk_call` that retries on Hyperliquid HTTP 429.

    Tightened delays (v12.17.0): 0.5s, 1.5s ; cap of ~2s wall-clock sleep on
    top of the underlying _sdk_call timeouts. The 4h-close burst that
    motivates this typically clears in <3s — longer backoffs were just
    blocking the asyncio to_thread worker for no benefit.

    Safe to call from any path (write or read) — the predicate is cheap
    and the retry budget bounded. Note: still blocking time.sleep ; not
    safe to call directly from the asyncio event loop (callers must wrap
    via asyncio.to_thread, which all current call sites do).
    """
    last_exc = None
    for attempt, delay in enumerate([0.0, *_RETRY_DELAYS_429]):
        if delay > 0:
            log.warning("%s: HTTP 429, retry %d/%d after %.1fs",
                        fn.__name__, attempt, len(_RETRY_DELAYS_429), delay)
            time.sleep(delay)
        try:
            return _sdk_call(fn, *args, timeout=timeout, **kwargs)
        except Exception as e:
            last_exc = e
            if not _is_429(e):
                raise
    raise last_exc


_SDK_PATCHES_APPLIED = False


def _apply_hyperliquid_sdk_patches() -> None:
    """Idempotent runtime patches on top of the upstream hyperliquid SDK.

    Issue (observed 2026-05-30): HL `spotMeta` occasionally returns
    `universe` entries whose `tokens` indices exceed `len(spot_meta["tokens"])`
    — e.g. `@367` referencing index 479 while `len(tokens)==464`. The SDK's
    `Info.__init__` indexes into `spot_meta["tokens"][base]` with no bounds
    check and crashes on `IndexError`, which prevents `Exchange()` from
    being created and takes down live + junior bots at boot.

    Our bot trades perps only, so spot mappings have zero functional impact.
    This patch wraps `Info.__init__` to fetch + sanitize `spot_meta` (when
    not provided by the caller) before delegating to the original. Filters
    out universe entries whose token indices are out of range.

    Persisted in our code (not a venv-local patch) so a
    `pip install --force-reinstall hyperliquid-python-sdk` does not bring
    back the crash.
    """
    global _SDK_PATCHES_APPLIED
    if _SDK_PATCHES_APPLIED:
        return
    try:
        from hyperliquid import info as _hl_info
    except ImportError:
        return  # SDK not installed (paper mode)

    _orig_init = _hl_info.Info.__init__

    def _patched_init(self, base_url=None, skip_ws=False, meta=None,
                      spot_meta=None, perp_dexs=None, timeout=None):
        # Sanitize spot_meta : either the one passed in, or one we fetch.
        if spot_meta is None:
            try:
                import requests
                api_url = base_url or "https://api.hyperliquid.xyz"
                r = requests.post(f"{api_url}/info",
                                  json={"type": "spotMeta"}, timeout=10)
                if r.status_code == 200:
                    spot_meta = r.json()
            except Exception:
                spot_meta = None  # fall through to original SDK fetch path
        if spot_meta and isinstance(spot_meta, dict) \
                and "universe" in spot_meta and "tokens" in spot_meta:
            ntok = len(spot_meta["tokens"])
            original_len = len(spot_meta["universe"])
            cleaned = dict(spot_meta)
            cleaned["universe"] = [
                u for u in spot_meta["universe"]
                if all(isinstance(t, int) and 0 <= t < ntok
                       for t in u.get("tokens", []))
            ]
            dropped = original_len - len(cleaned["universe"])
            if dropped:
                log.warning("Hyperliquid SDK patch: dropped %d spot_meta "
                            "universe entries with out-of-range token indices",
                            dropped)
            spot_meta = cleaned
        return _orig_init(self, base_url, skip_ws, meta, spot_meta,
                          perp_dexs, timeout)

    _hl_info.Info.__init__ = _patched_init
    _SDK_PATCHES_APPLIED = True
    log.info("Hyperliquid SDK patches applied (spot_meta sanitization)")


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
    _apply_hyperliquid_sdk_patches()
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
    # v12.5.29: timeout-capped + serialized via _sdk_call.
    # v12.16.6: retry on HTTP 429 (rate limit cascade at 4h close).
    result = _sdk_call_with_429_retry(exchange.market_open, sym, is_buy, sz,
                                       slippage=0.01, timeout=20.0)

    # Validate response
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        raise RuntimeError(f"Order returned empty statuses: {result}")
    first = statuses[0]
    if "error" in str(first).lower():
        raise RuntimeError(f"Order error: {first}")

    # Extract fill price AND actual filled size from order response. Hyperliquid
    # may partially fill a market order if the book lacks depth at the slippage
    # cap — in that case `totalSz` is smaller than the requested `sz`. Returning
    # the requested sz instead of `totalSz` would inflate Position.size_usdt and
    # surface as a hourly RECONCILE SIZE MISMATCH alert until close.
    # v12.5.29: validate avgPx > 0 and sz > 0. A malformed filled response with
    # avgPx="0" would otherwise propagate as entry_price=0 and crater the P&L
    # formula (divide-by-zero in check_exits, or a fake -10000 bps gross).
    filled = first.get("filled") if isinstance(first, dict) else None
    if filled and "avgPx" in filled:
        try:
            avg_px = float(filled["avgPx"])
            actual_sz = float(filled.get("totalSz", sz))
        except (TypeError, ValueError):
            avg_px, actual_sz = 0.0, 0.0
        if avg_px > 0 and actual_sz > 0:
            return _abort_if_micro_fill(exchange, sym, avg_px, actual_sz, size_usdt)
        log.warning("OPEN %s: invalid avgPx=%s sz=%s in filled response — falling back to user_fills",
                    sym, filled.get("avgPx"), filled.get("totalSz"))

    # Fallback: query fills API (may lag a few hundred ms behind L1).
    # Sum sz across recent fills on this coin to handle multi-fill orders.
    try:
        time.sleep(0.5)
        fills = _sdk_call_with_429_retry(hl_info.user_fills_by_time,
                          address, int((time.time() - 30) * 1000),
                          timeout=10.0)
        coin_fills = [f for f in fills if f.get("coin") == sym]
        if coin_fills:
            total_sz = sum(float(f["sz"]) for f in coin_fills)
            avg_px = sum(float(f["px"]) * float(f["sz"]) for f in coin_fills) / total_sz
            return _abort_if_micro_fill(exchange, sym, avg_px, total_sz, size_usdt)
    except Exception as e:
        log.warning("Fill lookup failed: %s — using market price", e)

    return {"avgPx": price, "sz": sz}  # last resort fallback


def _abort_if_micro_fill(exchange, sym: str, avg_px: float, actual_sz: float,
                         requested_usdt: float) -> dict:
    """v12.13.5: reject micro-fills below MIN_FILL_ABORT_USDT and reverse them.

    Saturated cross-margin or thin order books occasionally return fills at <5%
    of requested notional. A position smaller than the HL minimum order ($10)
    is intradable AND pollutes the dashboard with no upside. We close it
    immediately and raise so rank_and_enter skips creating the Position.

    Failure mode: if the reverse close itself fails, we keep the mini-position
    in management (return normally) rather than leaving an orphan on the
    exchange. The bot will manage it via normal stop/timeout — the only
    downside is dashboard noise until natural exit.
    """
    filled_usdt = avg_px * actual_sz
    if filled_usdt >= MIN_FILL_ABORT_USDT:
        return {"avgPx": avg_px, "sz": actual_sz}
    log.warning("MICRO FILL ABORT %s: filled $%.2f of requested $%.0f — closing",
                sym, filled_usdt, requested_usdt)
    try:
        _sdk_call_with_429_retry(exchange.market_close, sym, slippage=0.02, timeout=20.0)
    except Exception as e:
        log.error("MICRO FILL abort %s: reverse close failed (%s) — "
                  "keeping mini-position in management", sym, e)
        return {"avgPx": avg_px, "sz": actual_sz}
    raise ValueError(
        f"micro_fill_aborted: filled ${filled_usdt:.2f} of ${requested_usdt:.0f} "
        f"(<${MIN_FILL_ABORT_USDT:.0f}) — position closed")


def execute_close(exchange, hl_info, address: str, sym: str) -> dict | None:
    """Close position on Hyperliquid.

    Returns {"avgPx": float, "sz": float} so the caller can reconcile
    Position.size_usdt to the actually-closed quantity (not what the bot
    thought was open) — covers partial fills at open AND any drift from
    external interference. Returns None on no-fill, caller falls back to
    market price + tracked size.
    """
    log.info("EXEC CLOSE: %s", sym)
    # v12.5.29: timeout-capped + serialized. Hung close was the worst case —
    # _closing mutex held forever, position stuck outside management.
    # v12.16.6: retry on HTTP 429 (rate limit cascade at 4h close).
    result = _sdk_call_with_429_retry(exchange.market_close, sym, slippage=0.01,
                                       timeout=20.0)

    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        raise RuntimeError(f"Close returned empty statuses: {result}")
    first = statuses[0]
    if "error" in str(first).lower():
        raise RuntimeError(f"Close error: {first}")

    # v12.5.29: validate avgPx > 0 and totalSz > 0 (see execute_open rationale).
    # A zero avgPx propagated as exit_price would book a synthetic -10000 bps
    # gross PnL — catastrophic. Fall through to user_fills_by_time on failure.
    filled = first.get("filled") if isinstance(first, dict) else None
    if filled and "avgPx" in filled and "totalSz" in filled:
        try:
            avg_px = float(filled["avgPx"])
            total_sz = float(filled["totalSz"])
        except (TypeError, ValueError):
            avg_px, total_sz = 0.0, 0.0
        if avg_px > 0 and total_sz > 0:
            return {"avgPx": avg_px, "sz": total_sz}
        log.warning("CLOSE %s: invalid avgPx=%s sz=%s in filled response — falling back to user_fills",
                    sym, filled.get("avgPx"), filled.get("totalSz"))

    # Fallback: query fills API and sum recent fills on this coin.
    try:
        time.sleep(0.5)
        fills = _sdk_call_with_429_retry(hl_info.user_fills_by_time,
                          address, int((time.time() - 30) * 1000),
                          timeout=10.0)
        coin_fills = [f for f in fills if f.get("coin") == sym]
        if coin_fills:
            total_sz = sum(float(f["sz"]) for f in coin_fills)
            avg_px = sum(float(f["px"]) * float(f["sz"]) for f in coin_fills) / total_sz
            return {"avgPx": avg_px, "sz": total_sz}
    except Exception as e:
        log.warning("Fill lookup failed: %s", e)

    return None  # caller uses st.price + tracked pos.size_usdt as fallback


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
        hist = _sdk_call_with_429_retry(hl_info.user_funding_history, address, start_ms, end_ms,
                         timeout=10.0)
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


def fetch_equity_only(hl_info, address: str) -> dict | None:
    """Cheap fast-path: only user_state + spot_user_state for live equity refresh.
    Returns {equity, unrealized, margin_used, available}. Skips the expensive
    user_fills_by_time / user_funding_history calls (those drive diagnostic
    fields refreshed at the slower main_loop cadence).
    """
    if not hl_info:
        return None
    try:
        state = _sdk_call_with_429_retry(hl_info.user_state, address, timeout=60.0)
        spot = _sdk_call_with_429_retry(hl_info.spot_user_state, address, timeout=60.0)
        spot_usdc = 0.0
        spot_hold = 0.0
        for b in spot.get("balances", []):
            if b["coin"] == "USDC":
                spot_usdc = float(b["total"])
                spot_hold = float(b.get("hold", 0))
                break
        unrealized = 0.0
        for p in state.get("assetPositions", []):
            sz = float(p["position"].get("szi", 0))
            if abs(sz) > 0:
                unrealized += float(p["position"].get("unrealizedPnl", 0))
        account_value = float(state.get("marginSummary", {}).get("accountValue", 0))
        equity = (spot_usdc - spot_hold) + account_value
        margin_used = float(state.get("marginSummary", {}).get("totalMarginUsed", 0))
        available = equity - margin_used
        return {
            "equity": round(equity, 2),
            "unrealized": round(unrealized, 2),
            "margin_used": round(margin_used, 2),
            "available": round(available, 2),
        }
    except Exception:
        log.exception("fetch_equity_only failed")
        return None


def fetch_account_state(hl_info, address: str, fees_start_ms: int | None = None) -> dict | None:
    """Fetch real account state from Hyperliquid. Returns equity, unrealized, available, fees.

    fees_start_ms : optional override for the fees/funding window start (in ms).
    If None, defaults to the last 90 days (v11.3.7 behavior).
    """
    if not hl_info:
        return None
    try:
        state = _sdk_call_with_429_retry(hl_info.user_state, address, timeout=60.0)
        spot = _sdk_call_with_429_retry(hl_info.spot_user_state, address, timeout=60.0)

        # Spot USDC: total includes the cross-margin "hold" (collateral
        # earmarked for perps); we capture both to avoid double-counting.
        spot_usdc = 0.0
        spot_hold = 0.0
        for b in spot.get("balances", []):
            if b["coin"] == "USDC":
                spot_usdc = float(b["total"])
                spot_hold = float(b.get("hold", 0))
                break

        unrealized = 0.0
        for p in state.get("assetPositions", []):
            sz = float(p["position"].get("szi", 0))
            if abs(sz) > 0:
                unrealized += float(p["position"].get("unrealizedPnl", 0))
        account_value = float(state.get("marginSummary", {}).get("accountValue", 0))
        # Equity = free spot + perps subaccount equity. Works for both:
        #   - Live (USDC in spot, cross-margined into perps): hold≈accountValue
        #     and free_spot dominates
        #   - Junior (perps mode, USDC in perps subaccount): spot_usdc≈0,
        #     spot_hold≈0, equity≈account_value
        # Old "spot+unrealized" formula double-counted because spot.total
        # already includes the hold AND unrealized is already inside accountValue.
        equity = (spot_usdc - spot_hold) + account_value

        # Margin used
        margin_used = float(state.get("marginSummary", {}).get("totalMarginUsed", 0))
        available = equity - margin_used

        # Fees: taker fees from fills, funding from funding history
        import time as _time
        end_ms = int(_time.time() * 1000)
        default_start_ms = int((_time.time() - 90 * 86400) * 1000)
        start_ms = fees_start_ms if (fees_start_ms and fees_start_ms > 0) else default_start_ms
        # Clamp to ≥ 90 days back for HL API safety (very old queries can be slow)
        start_ms = max(start_ms, default_start_ms - 365 * 86400 * 1000)
        period_days = max(0.0, (end_ms - start_ms) / 86400000)
        try:
            fills = _sdk_call_with_429_retry(hl_info.user_fills_by_time, address, start_ms, end_ms,
                              timeout=15.0)
            taker_fees = sum(float(f.get("fee", 0)) for f in fills)
            closed_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
        except Exception:
            taker_fees, closed_pnl = 0.0, 0.0
        try:
            funding_hist = _sdk_call_with_429_retry(hl_info.user_funding_history, address, start_ms, end_ms,
                                     timeout=15.0)
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
            "fees_period_days": round(period_days, 1),
            "fees_period_start_ms": start_ms,
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
        state = _sdk_call_with_429_retry(hl_info.user_state, address, timeout=60.0)
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
                             category="reconcile", actionable=True)
        if ghosts:
            log.warning("RECONCILE: ghost positions in bot (not on exchange): %s", ghosts)
            send_telegram_fn(f"⚠️ Ghost in bot (not on exchange): {ghosts}",
                             category="reconcile", actionable=True)

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
                    category="reconcile", actionable=True)
                continue
            # Compare coin quantities (invariant under price moves)
            if bot_pos.entry_price > 0:
                bot_coin_qty = bot_pos.size_usdt / bot_pos.entry_price
                exch_coin_qty = abs(ex["szi"])
                if bot_coin_qty > 0:
                    ratio = exch_coin_qty / bot_coin_qty
                    # Auto-sync when bot tracks materially MORE than the exchange
                    # holds (ratio < 0.9). Typical cause: partial fill at open
                    # before v11.8.4, manual UI close, or external interference.
                    # Mutating bot_pos.size_usdt is safe because pos_snapshot is
                    # a shallow copy of bot.positions — Position objects are
                    # shared. Subsequent reconciles see ratio ~1.0, no spam.
                    if ratio < 0.9:
                        adjusted_size = exch_coin_qty * bot_pos.entry_price
                        log.warning(
                            "RECONCILE: AUTO-SYNC %s bot=%.4f coins exch=%.4f "
                            "(ratio %.2f) — size_usdt %.2f → %.2f",
                            sym, bot_coin_qty, exch_coin_qty, ratio,
                            bot_pos.size_usdt, adjusted_size)
                        bot_pos.size_usdt = adjusted_size
                        send_telegram_fn(
                            f"🔧 Auto-sync {sym}: bot oversized "
                            f"({bot_coin_qty:.4f} → {exch_coin_qty:.4f} coins, "
                            f"size_usdt → ${adjusted_size:.0f})",
                            category="reconcile")
                    elif ratio < 0.95 or ratio > 1.05:
                        # Smaller mismatches OR the rare bot-undersized case
                        # (ratio > 1.05): keep alerting, don't auto-fix. The
                        # latter would mean the exchange has MORE than the bot
                        # tracks — could indicate a stale ghost or a manual
                        # add. Auto-syncing UP is dangerous; let the user see.
                        log.warning("RECONCILE: SIZE MISMATCH %s bot=%.4f coins exch=%.4f (ratio %.2f)",
                                    sym, bot_coin_qty, exch_coin_qty, ratio)
                        send_telegram_fn(
                            f"⚠️ Size mismatch {sym}: bot={bot_coin_qty:.4f} "
                            f"exch={exch_coin_qty:.4f} coins (ratio {ratio:.2f})",
                            category="reconcile", actionable=True)

        if not orphans and not ghosts:
            log.debug("Reconcile OK: %d positions match", len(bot_syms))
    except Exception as e:
        log.warning("Reconcile error: %s", e)
