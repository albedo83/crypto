"""Hyperliquid SDK layer — Alfred phase 4.

Port of analysis/bot/exchange.py (v12.17.4) restructured around HLAccount :
one instance per live bot (own signer key, own account), while the SDK
machinery (executor, timeout caps, 429 retry, spot_meta patch) stays
module-level and is shared by every account in the process.

Only imported by brokers.LiveBroker — paper-only deployments never touch
the SDK (lazy imports inside HLAccount.__init__).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTO

log = logging.getLogger("alfred")

# Bounded executor + per-call timeout cap. The SDK exposes no request
# timeouts on market_open/close — a hung HTTP call would otherwise stall a
# close with the _closing mutex held. Shared by all accounts: SDK calls are
# short; 6 workers absorb N≤8 bots without thread explosion.
_SDK_MAX_WORKERS = 6
_SDK_EXECUTOR = ThreadPoolExecutor(max_workers=_SDK_MAX_WORKERS,
                                   thread_name_prefix="hl-sdk")
_SDK_INFLIGHT: set = set()
_SDK_INFLIGHT_LOCK = threading.Lock()

# 429 retry budget — see legacy v12.16.6/v12.17.1 history: the 4h-close burst
# can sustain 15-20s. With the MarketDataMaster the candle burst is gone, but
# order writes can still race other IP tenants; keep the proven budget.
_RETRY_DELAYS_429 = (0.5, 1.5, 3.0, 5.0, 10.0)


def parse_exchange_close(fills: list, direction: int) -> dict | None:
    """Digest the exchange-side closing fills of a position (étape 0 du filet
    hard-stop). Pure function — testable without the SDK.

    `fills` = user_fills entries on ONE coin since entry. direction 1=LONG
    (closes sell, side "A"), -1=SHORT (closes buy, side "B"). Returns
    {exit_px (vwap), exit_ms, closed_sz, fees_open, fees_close, liquidated}
    or None when no closing fill is present (position genuinely unaccounted)."""
    want_dir = "Close Long" if direction == 1 else "Close Short"
    close_side = "A" if direction == 1 else "B"
    closes: list = []
    opens: list = []
    liquidated = False
    for f in fills:
        d = str(f.get("dir", ""))
        if "liquidat" in d.lower() or f.get("liquidation"):
            if f.get("side") == close_side:
                closes.append(f)
                liquidated = True
            continue
        if d == want_dir:
            closes.append(f)
        elif d.startswith("Open"):
            opens.append(f)
    if not closes:
        return None
    tot_sz = sum(float(f["sz"]) for f in closes)
    if tot_sz <= 0:
        return None
    vwap = sum(float(f["px"]) * float(f["sz"]) for f in closes) / tot_sz
    return {
        "exit_px": vwap,
        "exit_ms": max(int(f["time"]) for f in closes),
        "closed_sz": tot_sz,
        "fees_open": sum(float(f.get("fee", 0) or 0) for f in opens),
        "fees_close": sum(float(f.get("fee", 0) or 0) for f in closes),
        "liquidated": liquidated,
        # oids des ordres de fermeture — permet d'attribuer la fermeture au
        # trigger hard-stop du bot (reason exchange_stop) vs close manuel.
        "close_oids": {int(f["oid"]) for f in closes if f.get("oid") is not None},
    }


def _sdk_call(fn, *args, timeout: float = 20.0, **kwargs):
    """Run an SDK call with a timeout cap. Raises TimeoutError on expiry —
    the underlying thread keeps running (best-effort cancel); acceptable
    because reads are idempotent and writes are reconciled next tick."""
    fut = _SDK_EXECUTOR.submit(fn, *args, **kwargs)
    with _SDK_INFLIGHT_LOCK:
        _SDK_INFLIGHT.add(fut)
        inflight_n = len(_SDK_INFLIGHT)

    def _cleanup(f):
        with _SDK_INFLIGHT_LOCK:
            _SDK_INFLIGHT.discard(f)
    fut.add_done_callback(_cleanup)

    if inflight_n >= _SDK_MAX_WORKERS - 1:
        log.warning("SDK executor saturated: %d/%d in-flight — HL latency "
                    "stalling workers, next call may block at submit()",
                    inflight_n, _SDK_MAX_WORKERS)
    try:
        return fut.result(timeout=timeout)
    except _FTO:
        fut.cancel()
        raise TimeoutError(f"SDK call {fn.__name__} exceeded {timeout}s")


def _is_429(exc: Exception) -> bool:
    """Robust 429 detection: status_code attr, args scan, string fallback."""
    if getattr(exc, "status_code", None) == 429:
        return True
    for a in getattr(exc, "args", ()):
        if a == 429 or a == "429":
            return True
    try:
        return "429" in str(exc)
    except Exception:
        return False


def _sdk_retry(fn, *args, timeout: float = 20.0, **kwargs):
    """_sdk_call with bounded 429 retry (blocking sleeps — call sites must
    run in a thread, never on the event loop)."""
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


def _apply_sdk_patches() -> None:
    """Idempotent spot_meta sanitization (HL occasionally returns universe
    entries with out-of-range token indices → SDK Info.__init__ IndexError).
    We trade perps only; dropping the malformed spot entries is harmless."""
    global _SDK_PATCHES_APPLIED
    if _SDK_PATCHES_APPLIED:
        return
    try:
        from hyperliquid import info as _hl_info
    except ImportError:
        return

    _orig_init = _hl_info.Info.__init__

    def _patched_init(self, base_url=None, skip_ws=False, meta=None,
                      spot_meta=None, perp_dexs=None, timeout=None):
        if spot_meta is None:
            try:
                import requests
                api_url = base_url or "https://api.hyperliquid.xyz"
                r = requests.post(f"{api_url}/info",
                                  json={"type": "spotMeta"}, timeout=10)
                if r.status_code == 200:
                    spot_meta = r.json()
            except Exception:
                spot_meta = None
        if spot_meta and isinstance(spot_meta, dict) \
                and "universe" in spot_meta and "tokens" in spot_meta:
            ntok = len(spot_meta["tokens"])
            cleaned = dict(spot_meta)
            cleaned["universe"] = [
                u for u in spot_meta["universe"]
                if all(isinstance(t, int) and 0 <= t < ntok
                       for t in u.get("tokens", []))
            ]
            dropped = len(spot_meta["universe"]) - len(cleaned["universe"])
            if dropped:
                log.warning("SDK patch: dropped %d malformed spot_meta entries",
                            dropped)
            spot_meta = cleaned
        return _orig_init(self, base_url, skip_ws, meta, spot_meta,
                          perp_dexs, timeout)

    _hl_info.Info.__init__ = _patched_init
    _SDK_PATCHES_APPLIED = True
    log.info("Hyperliquid SDK patches applied (spot_meta sanitization)")


class HLAccount:
    """One live Hyperliquid account: signer key + (optional) master wallet.

    agent model : private_key signs, account_address holds the funds (the
    key must be authorized as an API agent of the master in the HL UI).
    direct model: account_address="" → the key IS the wallet.
    """

    def __init__(self, private_key: str, account_address: str,
                 trade_symbols: tuple, leverage: float,
                 min_fill_abort_usdt: float = 10.0):
        if not private_key:
            raise ValueError("live mode requires a private key")
        _apply_sdk_patches()
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info as HLInfo

        wallet = Account.from_key(private_key)
        if account_address:
            self.exchange = Exchange(wallet, account_address=account_address)
            self.address = account_address
        else:
            self.exchange = Exchange(wallet)
            self.address = wallet.address
        self.info = HLInfo(skip_ws=True)
        self.min_fill_abort_usdt = min_fill_abort_usdt

        self.sz_decimals: dict[str, int] = {}
        meta = self.info.meta()
        for asset in meta["universe"]:
            self.sz_decimals[asset["name"]] = asset["szDecimals"]
        missing = [s for s in trade_symbols if s not in self.sz_decimals]
        if missing:
            log.critical("Missing szDecimals for %s — fallback rounding", missing)

        for sym in trade_symbols:
            try:
                self.exchange.update_leverage(int(leverage), sym, is_cross=True)
            except Exception as e:
                log.warning("Leverage set failed for %s: %s", sym, e)

        signer = wallet.address[:10]
        if account_address:
            log.info("LIVE: signer %s… → trading on %s… (agent), %dx on %d symbols",
                     signer, self.address[:10], int(leverage), len(trade_symbols))
        else:
            log.info("LIVE: wallet %s…, %dx on %d symbols",
                     signer, int(leverage), len(trade_symbols))

    # ── Orders ────────────────────────────────────────────────────────

    def open_market(self, sym: str, is_buy: bool, size_usdt: float,
                    price: float) -> dict:
        """Market entry. Returns {"avgPx", "sz"} (real fill). Raises on
        failure/micro-fill (caller logs + skips the entry)."""
        if price <= 0:
            raise ValueError(f"Invalid price for {sym}: {price}")
        sz = round(size_usdt / price, self.sz_decimals.get(sym, 2))
        if sz <= 0 or sz * price < 10:
            raise ValueError(f"Order too small: {sz} {sym} = ${sz * price:.1f}")

        log.info("EXEC OPEN: %s %s sz=%s (~$%.0f)",
                 "BUY" if is_buy else "SELL", sym, sz, sz * price)
        result = _sdk_retry(self.exchange.market_open, sym, is_buy, sz,
                            slippage=0.01, timeout=20.0)
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            raise RuntimeError(f"Order returned empty statuses: {result}")
        first = statuses[0]
        if "error" in str(first).lower():
            raise RuntimeError(f"Order error: {first}")

        filled = first.get("filled") if isinstance(first, dict) else None
        if filled and "avgPx" in filled:
            try:
                avg_px = float(filled["avgPx"])
                actual_sz = float(filled.get("totalSz", sz))
            except (TypeError, ValueError):
                avg_px, actual_sz = 0.0, 0.0
            if avg_px > 0 and actual_sz > 0:
                return self._abort_if_micro_fill(sym, avg_px, actual_sz, size_usdt)
            log.warning("OPEN %s: invalid filled response — falling back to user_fills", sym)

        try:
            time.sleep(0.5)
            fills = _sdk_retry(self.info.user_fills_by_time, self.address,
                               int((time.time() - 30) * 1000), timeout=10.0)
            coin_fills = [f for f in fills if f.get("coin") == sym]
            if coin_fills:
                total_sz = sum(float(f["sz"]) for f in coin_fills)
                avg_px = (sum(float(f["px"]) * float(f["sz"]) for f in coin_fills)
                          / total_sz)
                return self._abort_if_micro_fill(sym, avg_px, total_sz, size_usdt)
        except Exception as e:
            log.warning("Fill lookup failed for %s: %s", sym, e)
        # Ne PAS fabriquer un fill au prix demandé : ça ancrerait un faux
        # entry_price (P&L/stops faussés à vie) et court-circuiterait le garde
        # micro-fill. On lève → le caller skip l'entrée ; si l'ordre a malgré
        # tout fillé, le reconcile horaire le détecte comme orphan (alerté).
        raise RuntimeError(
            f"{sym}: ordre soumis mais fill introuvable (réponse sans filled "
            f"+ user_fills KO) — entrée annulée, reconcile gérera tout fill réel")

    def _abort_if_micro_fill(self, sym: str, avg_px: float, actual_sz: float,
                             requested_usdt: float) -> dict:
        """Reject fills below min_fill_abort_usdt and reverse them (thin book
        / saturated margin edge case). If the reverse close itself fails,
        keep the mini-position in management rather than orphaning it."""
        filled_usdt = avg_px * actual_sz
        if filled_usdt >= self.min_fill_abort_usdt:
            return {"avgPx": avg_px, "sz": actual_sz}
        log.warning("MICRO FILL ABORT %s: $%.2f of $%.0f — closing",
                    sym, filled_usdt, requested_usdt)
        try:
            _sdk_retry(self.exchange.market_close, sym, slippage=0.02, timeout=20.0)
        except Exception as e:
            log.error("Micro-fill reverse close failed (%s) — keeping position", e)
            return {"avgPx": avg_px, "sz": actual_sz}
        raise ValueError(
            f"micro_fill_aborted: ${filled_usdt:.2f} of ${requested_usdt:.0f}")

    def close_market(self, sym: str) -> dict | None:
        """Market close. Returns {"avgPx", "sz"} or None (no fill info —
        caller books the trigger/mark). Raises on hard failure (caller keeps
        the position and retries next tick)."""
        log.info("EXEC CLOSE: %s", sym)
        result = _sdk_retry(self.exchange.market_close, sym, slippage=0.01,
                            timeout=20.0)
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            raise RuntimeError(f"Close returned empty statuses: {result}")
        first = statuses[0]
        if "error" in str(first).lower():
            raise RuntimeError(f"Close error: {first}")

        filled = first.get("filled") if isinstance(first, dict) else None
        if filled and "avgPx" in filled and "totalSz" in filled:
            try:
                avg_px = float(filled["avgPx"])
                total_sz = float(filled["totalSz"])
            except (TypeError, ValueError):
                avg_px, total_sz = 0.0, 0.0
            if avg_px > 0 and total_sz > 0:
                return {"avgPx": avg_px, "sz": total_sz}
            log.warning("CLOSE %s: invalid filled response — falling back", sym)

        try:
            time.sleep(0.5)
            fills = _sdk_retry(self.info.user_fills_by_time, self.address,
                               int((time.time() - 30) * 1000), timeout=10.0)
            coin_fills = [f for f in fills if f.get("coin") == sym]
            if coin_fills:
                total_sz = sum(float(f["sz"]) for f in coin_fills)
                avg_px = (sum(float(f["px"]) * float(f["sz"]) for f in coin_fills)
                          / total_sz)
                return {"avgPx": avg_px, "sz": total_sz}
        except Exception as e:
            log.warning("Fill lookup failed: %s", e)
        return None

    # ── Reads ────────────────────────────────────────────────────────

    def position_funding(self, coin: str, start_ms: int, end_ms: int) -> float:
        """Signed funding sum on `coin` over the window (negative = paid).
        Fail-open: 0.0 on any error."""
        try:
            hist = _sdk_retry(self.info.user_funding_history, self.address,
                              start_ms, end_ms, timeout=10.0)
            total = 0.0
            for ev in hist:
                if ev.get("delta", {}).get("coin") != coin:
                    continue
                t = int(ev.get("time", 0))
                if start_ms <= t <= end_ms:
                    total += float(ev["delta"].get("usdc", 0))
            return total
        except Exception as e:
            log.warning("Funding lookup failed for %s: %s", coin, e)
            return 0.0

    def _equity_parts(self) -> tuple:
        state = _sdk_retry(self.info.user_state, self.address, timeout=60.0)
        spot = _sdk_retry(self.info.spot_user_state, self.address, timeout=60.0)
        spot_usdc = spot_hold = 0.0
        for b in spot.get("balances", []):
            if b["coin"] == "USDC":
                spot_usdc = float(b["total"])
                spot_hold = float(b.get("hold", 0))
                break
        unrealized = 0.0
        for p in state.get("assetPositions", []):
            if abs(float(p["position"].get("szi", 0))) > 0:
                unrealized += float(p["position"].get("unrealizedPnl", 0))
        ms = state.get("marginSummary", {})
        account_value = float(ms.get("accountValue", 0))
        margin_used = float(ms.get("totalMarginUsed", 0))
        # Unified equity formula (v11.9.1): free spot + perps account value.
        equity = (spot_usdc - spot_hold) + account_value
        return state, equity, unrealized, margin_used

    def equity_only(self) -> dict | None:
        """Cheap equity refresh (2 SDK calls)."""
        try:
            _, equity, unrealized, margin_used = self._equity_parts()
            return {"equity": round(equity, 2),
                    "unrealized": round(unrealized, 2),
                    "margin_used": round(margin_used, 2),
                    "available": round(equity - margin_used, 2)}
        except Exception:
            log.exception("equity_only failed")
            return None

    def account_state(self, fees_start_ms: int | None = None) -> dict | None:
        """Full account state incl. fees/funding diagnostics (4 SDK calls)."""
        try:
            _, equity, unrealized, margin_used = self._equity_parts()
            end_ms = int(time.time() * 1000)
            default_start = int((time.time() - 90 * 86400) * 1000)
            start_ms = fees_start_ms if (fees_start_ms and fees_start_ms > 0) else default_start
            start_ms = max(start_ms, default_start - 365 * 86400 * 1000)
            try:
                fills = _sdk_retry(self.info.user_fills_by_time, self.address,
                                   start_ms, end_ms, timeout=15.0)
                taker_fees = sum(float(f.get("fee", 0)) for f in fills)
                closed_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
            except Exception:
                taker_fees, closed_pnl = 0.0, 0.0
            try:
                hist = _sdk_retry(self.info.user_funding_history, self.address,
                                  start_ms, end_ms, timeout=15.0)
                funding_paid = sum(float(f["delta"]["usdc"]) for f in hist)
            except Exception:
                funding_paid = 0.0
            return {"equity": round(equity, 2),
                    "unrealized": round(unrealized, 2),
                    "margin_used": round(margin_used, 2),
                    "available": round(equity - margin_used, 2),
                    "taker_fees": round(taker_fees, 2),
                    "funding_paid": round(funding_paid, 2),
                    "closed_pnl": round(closed_pnl, 2),
                    "fees_period_days": round((end_ms - start_ms) / 86400000, 1),
                    "fees_period_start_ms": start_ms}
        except Exception as e:
            log.warning("Account state fetch failed: %s", e)
            return None

    def exchange_positions(self) -> dict[str, dict]:
        """{coin: {"szi": float}} for non-zero exchange positions."""
        state = _sdk_retry(self.info.user_state, self.address, timeout=60.0)
        out: dict[str, dict] = {}
        for pos in state.get("assetPositions", []):
            p = pos["position"]
            sz = float(p.get("szi", 0))
            if abs(sz) > 0:
                out[p["coin"]] = {"szi": sz}
        return out

    def coin_fills_since(self, coin: str, start_ms: int) -> list[dict]:
        """All fills on `coin` since start_ms. Raises on API error (caller
        falls back). start padded −60s to always capture the entry fill."""
        fills = _sdk_retry(self.info.user_fills_by_time, self.address,
                           max(0, start_ms - 60_000), timeout=15.0)
        return [f for f in fills if f.get("coin") == coin]

    # ── Hard-stop triggers (filet exchange-side, v1.7.1) ─────────────

    def place_stop_order(self, sym: str, is_buy: bool, sz: float,
                         trigger_px: float, slippage: float = 0.05) -> int:
        """Resident reduce-only stop-market on HL. Returns the resting oid.
        Raises on any failure (caller alerts + retries at next reconcile).
        `is_buy`: True to close a SHORT, False to close a LONG."""
        # _slippage_price fait aussi l'arrondi de prix HL (5 sig figs,
        # 6−szDecimals) ; slippage=0 → arrondi pur pour le triggerPx.
        trig = self.exchange._slippage_price(sym, is_buy, 0.0, trigger_px)
        limit_px = self.exchange._slippage_price(sym, is_buy, slippage,
                                                 trigger_px)
        log.info("EXEC STOP: %s %s sz=%s trigger=%s",
                 "BUY" if is_buy else "SELL", sym, sz, trig)
        result = _sdk_retry(
            self.exchange.order, sym, is_buy, sz, limit_px,
            {"trigger": {"triggerPx": trig, "isMarket": True, "tpsl": "sl"}},
            reduce_only=True, timeout=20.0)
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            raise RuntimeError(f"Stop order returned empty statuses: {result}")
        first = statuses[0]
        if "error" in str(first).lower():
            raise RuntimeError(f"Stop order error: {first}")
        oid = (first.get("resting") or {}).get("oid") if isinstance(first, dict) else None
        if not oid:
            raise RuntimeError(f"Stop order: no resting oid in {first}")
        return int(oid)

    def cancel_order(self, sym: str, oid: int) -> bool:
        """Cancel one order. False (not raise) when already gone — the
        common benign race (order filled/canceled meanwhile)."""
        try:
            result = _sdk_retry(self.exchange.cancel, sym, oid, timeout=15.0)
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            return bool(statuses) and statuses[0] == "success"
        except Exception as e:
            log.warning("Cancel %s oid=%s failed: %s", sym, oid, e)
            return False

    def open_trigger_orders(self) -> list[dict]:
        """Resident reduce-only trigger orders on the account:
        [{coin, oid, trigger_px, side, sz}]. Raises on API error."""
        orders = _sdk_retry(self.info.frontend_open_orders, self.address,
                            timeout=15.0)
        out = []
        for o in orders or []:
            if o.get("isTrigger") and o.get("reduceOnly"):
                out.append({"coin": o.get("coin"), "oid": int(o.get("oid", 0)),
                            "trigger_px": float(o.get("triggerPx") or 0),
                            "side": o.get("side"),
                            "sz": float(o.get("sz") or 0)})
        return out

    def reconcile(self, bot_positions: dict, notify_fn, on_ghost=None) -> None:
        """Bot vs exchange positions: ghosts, orphans, direction/size
        mismatches. Auto-syncs size_usdt when the bot tracks materially MORE
        than the exchange holds (ratio < 0.9 in coin units)."""
        try:
            exch = self.exchange_positions()
        except Exception as e:
            log.warning("Reconcile fetch failed: %s", e)
            return
        bot_syms = set(bot_positions.keys())
        exch_syms = set(exch.keys())
        if exch_syms - bot_syms:
            log.warning("RECONCILE: orphans on exchange: %s", exch_syms - bot_syms)
            notify_fn(f"⚠️ Orphan on exchange (not in bot): {exch_syms - bot_syms}",
                      category="reconcile", actionable=True)
        ghosts = bot_syms - exch_syms
        if ghosts:
            log.warning("RECONCILE: ghosts in bot: %s", ghosts)
            if on_ghost is not None:
                # Étape 0 filet hard-stop : la position a été fermée côté
                # exchange (trigger/liquidation/close manuel) — booker le
                # trade réel au lieu de se contenter d'alerter.
                for sym in sorted(ghosts):
                    try:
                        on_ghost(sym)
                    except Exception as e:
                        log.exception("RECONCILE: ghost booking failed %s", sym)
                        notify_fn(f"⚠️ Ghost {sym}: booking failed ({e}) — "
                                  f"position gardée, retry au prochain reconcile",
                                  category="reconcile", actionable=True)
            else:
                notify_fn(f"⚠️ Ghost in bot (not on exchange): {ghosts}",
                          category="reconcile", actionable=True)
        for sym in bot_syms & exch_syms:
            bot_pos = bot_positions[sym]
            exch_dir = 1 if exch[sym]["szi"] > 0 else -1
            if exch_dir != bot_pos.direction:
                log.critical("RECONCILE: DIRECTION MISMATCH %s", sym)
                notify_fn(f"🚨 DIRECTION MISMATCH {sym}", category="reconcile",
                          actionable=True)
                continue
            if bot_pos.entry_price > 0:
                bot_qty = bot_pos.size_usdt / bot_pos.entry_price
                exch_qty = abs(exch[sym]["szi"])
                if bot_qty > 0:
                    ratio = exch_qty / bot_qty
                    if ratio < 0.9:
                        adjusted = exch_qty * bot_pos.entry_price
                        log.warning("RECONCILE AUTO-SYNC %s: size_usdt %.2f → %.2f",
                                    sym, bot_pos.size_usdt, adjusted)
                        bot_pos.size_usdt = adjusted
                        notify_fn(f"🔧 Auto-sync {sym}: size_usdt → ${adjusted:.0f}",
                                  category="reconcile")
                    elif ratio < 0.95 or ratio > 1.05:
                        notify_fn(f"⚠️ Size mismatch {sym}: bot={bot_qty:.4f} "
                                  f"exch={exch_qty:.4f} coins",
                                  category="reconcile", actionable=True)
