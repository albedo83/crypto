"""Network I/O — HTTP fetches (prices, candles) and Telegram alerts."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

from .config import (ALL_SYMBOLS, TG_BOT_TOKEN, TG_CHAT_ID, TG_CATEGORIES,
                     EXECUTION_MODE, BOT_LABEL)

_TG_ALLOWED: set[str] | None = (
    None if TG_CATEGORIES.strip() == "*"
    else {c.strip() for c in TG_CATEGORIES.split(",") if c.strip()}
)
# Prefix every Telegram message with the bot label so multiple instances
# sharing a Telegram chat stay distinguishable (e.g. Junior1 vs Junior2).
_TG_PREFIX = f"[{BOT_LABEL}] " if BOT_LABEL else ""

log = logging.getLogger("multisignal")


def http_fetch(url: str, payload: bytes | None = None, headers: dict | None = None,
               timeout: int = 15, retries: int = 3) -> bytes:
    """HTTP request with exponential backoff (1s, 2s, 4s). Returns response bytes."""
    hdrs = headers or {"Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            log.debug("http_fetch attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 ** attempt)  # 1s, 2s, 4s


def fetch_prices(states: dict) -> tuple[list | None, list | None]:
    """Fetch current prices from Hyperliquid metaAndAssetCtxs.

    Updates states dict in-place (price, updated_at, price_ticks, oi,
    oi_history, funding, premium). Returns (meta_universe, ctxs).
    """
    try:
        payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
        data = json.loads(http_fetch("https://api.hyperliquid.xyz/info", payload))

        meta = data[0]
        ctxs = data[1]
        if len(meta["universe"]) != len(ctxs):
            log.warning("API mismatch: %d universe vs %d ctxs", len(meta["universe"]), len(ctxs))
            return None, None
        now = time.time()

        for i, asset in enumerate(meta["universe"]):
            name = asset["name"]
            if name not in states:
                continue
            price = float(ctxs[i].get("markPx", 0))
            if price > 0:
                st = states[name]
                st.price = price
                st.updated_at = now
                st.price_ticks.append((now, price))
                # OI + funding (observation phase — not used for signals yet)
                oi = float(ctxs[i].get("openInterest") or 0)
                if oi > 0:
                    st.oi = oi
                    st.oi_history.append((now, oi))
                st.funding = float(ctxs[i].get("funding") or 0)
                st.premium = float(ctxs[i].get("premium") or 0)
        return meta["universe"], ctxs
    except Exception as e:
        log.warning("Price fetch error: %s", e)
        return None, None


def fetch_candles(symbol: str, states: dict) -> None:
    """Fetch 4h candles for one symbol (need 180+ for features). Updates states in-place."""
    try:
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - 45 * 86400 * 1000  # 45 days
        payload = json.dumps({"type": "candleSnapshot", "req": {
            "coin": symbol, "interval": "4h", "startTime": start_ts, "endTime": end_ts
        }}).encode()
        candles = json.loads(http_fetch("https://api.hyperliquid.xyz/info", payload))

        st = states[symbol]
        if not candles:
            return
        st.candles_4h.clear()
        for c in candles:
            st.candles_4h.append({
                "t": c["t"],
                "o": float(c["o"]),
                "c": float(c["c"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "v": float(c.get("v", 0)),
            })
        if candles:
            st.last_candle_ts = candles[-1]["t"]
    except Exception as e:
        log.warning("Candle fetch %s: %s", symbol, e)


def send_telegram(msg: str, category: str = "other") -> None:
    """Send alert via Telegram Bot API. Fire-and-forget in a daemon thread.

    `category` filters against TG_CATEGORIES env var (default "*" = all).
    Known categories: trade, daily, reconcile, security, admin, system.
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID or EXECUTION_MODE == "paper":
        return
    if _TG_ALLOWED is not None and category not in _TG_ALLOWED:
        return

    def _do_send():
        try:
            payload = json.dumps({"chat_id": TG_CHAT_ID, "text": _TG_PREFIX + msg}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            log.warning("Telegram error: %s", e)

    threading.Thread(target=_do_send, daemon=True).start()
