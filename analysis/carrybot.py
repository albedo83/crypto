"""CarryBot — Funding rate carry trade, market-neutral.

Strategy: Long the symbol with lowest funding, short the one with highest.
Earn the funding differential every 8h. No directional risk.

Rebalances daily: picks the best pairs based on current funding + persistence.
Holds positions for 3 days (9 settlements) then re-evaluates.

Separate from LiveBot — runs on a different port, different capital.

Run:       python3 -m analysis.carrybot
Dashboard: http://0.0.0.0:8096
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import aiohttp
import numpy as np
import orjson
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CARRY] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("carrybot")

# ── Config ───────────────────────────────────────────────────────────
SYMBOLS = [
    "btcusdt", "ethusdt", "adausdt", "bnbusdt", "bchusdt", "trxusdt",
    "hypeusdt", "zrousdt", "aaveusdt", "linkusdt", "suiusdt",
    "avaxusdt", "xrpusdt", "xmrusdt", "xlmusdt", "tonusdt", "ltcusdt",
    "dogeusdt", "solusdt",
]

# Persistence data from study_11 — symbols that rarely flip funding sign
STABLE_SYMBOLS = {"XMRUSDT", "BNBUSDT", "ZROUSDT", "HYPEUSDT", "SOLUSDT"}

WS_STREAMS = []
for s in SYMBOLS:
    WS_STREAMS.extend([f"{s}@bookTicker", f"{s}@markPrice@1s"])
WS_URL = "wss://fstream.binance.com/stream?streams=" + "/".join(WS_STREAMS)

FUNDING_POLL_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
POLL_INTERVAL = 300         # check funding every 5 min
REBALANCE_HOURS = 72        # rebalance every 3 days
MIN_SPREAD_BPS = 1.5        # minimum spread to enter a pair
WEB_PORT = 8096

# ── Capital ──────────────────────────────────────────────────────────
CAPITAL_USDT = 1000.0
MAX_PAIRS = 3               # max 3 carry pairs simultaneously
MARGIN_PER_LEG_PCT = 15.0   # 15% of capital per leg → 30% per pair
COST_BPS_ENTRY = 4.0        # one-time maker roundtrip cost for both legs

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "carry_trades.csv")
SIGNALS_CSV = os.path.join(OUTPUT_DIR, "carry_signals.csv")
HTML_PATH = os.path.join(os.path.dirname(__file__), "carrybot.html")


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class SymbolData:
    mid_price: float = 0.0
    funding_rate: float = 0.0
    funding_bps: float = 0.0
    next_funding_ts: int = 0
    mark_price: float = 0.0
    index_price: float = 0.0
    basis_bps: float = 0.0
    last_update: float = 0.0


@dataclass
class CarryPair:
    long_sym: str
    short_sym: str
    long_entry_price: float
    short_entry_price: float
    entry_time: datetime
    long_margin: float
    short_margin: float
    entry_spread_bps: float
    settlements_earned: int = 0
    total_carry_bps: float = 0.0


@dataclass
class CarryTrade:
    long_sym: str
    short_sym: str
    entry_time: str
    exit_time: str
    hold_days: float
    settlements: int
    carry_earned_bps: float
    price_pnl_long_bps: float
    price_pnl_short_bps: float
    total_gross_bps: float
    cost_bps: float
    net_bps: float
    pnl_usdt: float
    reason: str


class CarryBot:
    def __init__(self):
        self.data: dict[str, SymbolData] = {s.upper(): SymbolData() for s in SYMBOLS}
        self.pairs: list[CarryPair] = []
        self.trades: list[CarryTrade] = []
        self.running = False
        self.ws_connected = False
        self.started_at: datetime | None = None
        self._total_pnl_usdt = 0.0
        self._wins = 0
        self._last_settlement_check: datetime | None = None

    # ── WebSocket ────────────────────────────────────────────────
    async def ws_loop(self):
        backoff = 3
        while self.running:
            try:
                log.info("Connecting to Binance WS...")
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    self.ws_connected = True
                    backoff = 3
                    log.info("WS connected (%d streams)", len(WS_STREAMS))
                    async for raw in ws:
                        if not self.running:
                            break
                        self._on_message(raw)
            except Exception as e:
                log.warning("WS error: %s — reconnecting in %ds", e, backoff)
            self.ws_connected = False
            if self.running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_message(self, raw):
        try:
            msg = orjson.loads(raw)
        except Exception:
            return
        stream = msg.get("stream", "")
        d = msg.get("data", {})
        if not d:
            return
        sym = d.get("s", "")
        st = self.data.get(sym)
        if not st:
            return

        if "@bookTicker" in stream:
            bid = float(d.get("b", 0))
            ask = float(d.get("a", 0))
            if bid > 0 and ask > 0:
                st.mid_price = (bid + ask) / 2
                st.last_update = time.time()
        elif "@markPrice" in stream:
            st.mark_price = float(d.get("p", 0))
            st.index_price = float(d.get("i", 0))
            st.funding_rate = float(d.get("r", 0))
            st.funding_bps = st.funding_rate * 1e4
            st.next_funding_ts = int(d.get("T", 0))
            if st.index_price > 0:
                st.basis_bps = (st.mark_price - st.index_price) / st.index_price * 1e4

    # ── Strategy loop ────────────────────────────────────────────
    async def strategy_loop(self):
        """Every 5 min: check settlements, rebalance if needed."""
        while self.running:
            await asyncio.sleep(POLL_INTERVAL)
            if not self.ws_connected:
                continue
            try:
                self._check_settlements()
                self._check_rebalance()
                self._log_signals()
            except Exception:
                log.exception("Strategy error")

    def _check_settlements(self):
        """Track funding earned by open pairs."""
        now = datetime.now(timezone.utc)

        # Detect if a settlement just happened (every 8h: 00, 08, 16 UTC)
        if self._last_settlement_check is None:
            self._last_settlement_check = now
            return

        last_h = self._last_settlement_check.hour
        curr_h = now.hour
        settlement_happened = False
        for sh in (0, 8, 16):
            if last_h < sh <= curr_h or (last_h > curr_h and (sh <= curr_h or last_h < sh)):
                settlement_happened = True
                break
        self._last_settlement_check = now

        if not settlement_happened or not self.pairs:
            return

        log.info("── Funding settlement detected ──")
        for pair in self.pairs:
            long_data = self.data.get(pair.long_sym)
            short_data = self.data.get(pair.short_sym)
            if not long_data or not short_data:
                continue

            # Carry earned this settlement:
            # Long position: if funding > 0, we PAY; if < 0, we EARN
            # Short position: if funding > 0, we EARN; if < 0, we PAY
            carry = short_data.funding_bps - long_data.funding_bps
            pair.total_carry_bps += carry
            pair.settlements_earned += 1

            log.info(
                "  %s/%s: carry %+.1f bps (total %+.1f bps, %d settlements)",
                pair.long_sym[:4], pair.short_sym[:4],
                carry, pair.total_carry_bps, pair.settlements_earned,
            )

    def _check_rebalance(self):
        """Close old pairs, open new ones if better opportunities exist."""
        now = datetime.now(timezone.utc)

        # Close pairs that have been held long enough
        for pair in list(self.pairs):
            held_hours = (now - pair.entry_time).total_seconds() / 3600
            if held_hours >= REBALANCE_HOURS:
                self._close_pair(pair, now, "rebalance")

        # Open new pairs if we have capacity
        if len(self.pairs) >= MAX_PAIRS:
            return

        # Rank symbols by funding rate
        rates = []
        for sym, st in self.data.items():
            if st.mid_price == 0 or st.funding_rate == 0:
                continue
            rates.append((sym, st.funding_bps, st.mid_price))

        if len(rates) < 4:
            return

        rates.sort(key=lambda x: x[1])
        longs = rates[:3]    # lowest funding → long
        shorts = rates[-3:]  # highest funding → short

        for long_sym, long_rate, long_price in longs:
            for short_sym, short_rate, short_price in shorts:
                if long_sym == short_sym:
                    continue
                if len(self.pairs) >= MAX_PAIRS:
                    return
                # Check if already in this pair
                if any(p.long_sym == long_sym and p.short_sym == short_sym for p in self.pairs):
                    continue

                spread = short_rate - long_rate
                if spread < MIN_SPREAD_BPS:
                    continue

                # Prefer pairs with stable funding (at least one leg stable)
                has_stable = long_sym in STABLE_SYMBOLS or short_sym in STABLE_SYMBOLS

                pnl = self._total_pnl_usdt
                current_capital = CAPITAL_USDT + pnl
                margin_per_leg = current_capital * MARGIN_PER_LEG_PCT / 100

                pair = CarryPair(
                    long_sym=long_sym, short_sym=short_sym,
                    long_entry_price=long_price, short_entry_price=short_price,
                    entry_time=now,
                    long_margin=margin_per_leg, short_margin=margin_per_leg,
                    entry_spread_bps=spread,
                )
                self.pairs.append(pair)
                stable_tag = " [STABLE]" if has_stable else ""
                log.info(
                    "→ OPEN CARRY: LONG %s (%+.1f bps) + SHORT %s (%+.1f bps) "
                    "= spread %.1f bps/8h | $%.0f/leg%s",
                    long_sym[:4], long_rate, short_sym[:4], short_rate,
                    spread, margin_per_leg, stable_tag,
                )

    def _close_pair(self, pair: CarryPair, now: datetime, reason: str):
        self.pairs.remove(pair)

        long_data = self.data.get(pair.long_sym)
        short_data = self.data.get(pair.short_sym)
        long_price = long_data.mid_price if long_data and long_data.mid_price > 0 else pair.long_entry_price
        short_price = short_data.mid_price if short_data and short_data.mid_price > 0 else pair.short_entry_price

        # Price P&L (might lose here — carry is supposed to compensate)
        long_pnl_bps = (long_price / pair.long_entry_price - 1) * 1e4
        short_pnl_bps = -(short_price / pair.short_entry_price - 1) * 1e4
        price_pnl = long_pnl_bps + short_pnl_bps

        total_gross = pair.total_carry_bps + price_pnl
        net = total_gross - COST_BPS_ENTRY
        hold_days = (now - pair.entry_time).total_seconds() / 86400

        total_margin = pair.long_margin + pair.short_margin
        pnl_usdt = total_margin * net / 1e4

        self._total_pnl_usdt += pnl_usdt
        if pnl_usdt > 0:
            self._wins += 1

        trade = CarryTrade(
            long_sym=pair.long_sym, short_sym=pair.short_sym,
            entry_time=pair.entry_time.isoformat(), exit_time=now.isoformat(),
            hold_days=round(hold_days, 1), settlements=pair.settlements_earned,
            carry_earned_bps=round(pair.total_carry_bps, 2),
            price_pnl_long_bps=round(long_pnl_bps, 2),
            price_pnl_short_bps=round(short_pnl_bps, 2),
            total_gross_bps=round(total_gross, 2),
            cost_bps=COST_BPS_ENTRY, net_bps=round(net, 2),
            pnl_usdt=round(pnl_usdt, 2), reason=reason,
        )
        self.trades.append(trade)
        self._write_trade_csv(trade)

        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl_usdt
        wr = self._wins / n * 100 if n > 0 else 0
        arrow = "+" if pnl_usdt > 0 else "-"
        log.info(
            "%s CLOSE %s/%s | %.1fd | %d settlements | carry %+.1f | price %+.1f | "
            "net %+.1f bps | %+.2f$ | balance $%.2f (#%d, win %.0f%%)",
            arrow, pair.long_sym[:4], pair.short_sym[:4],
            hold_days, pair.settlements_earned,
            pair.total_carry_bps, price_pnl,
            net, pnl_usdt, balance, n, wr,
        )

    def _write_trade_csv(self, t: CarryTrade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        with open(TRADES_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["long_sym", "short_sym", "entry_time", "exit_time",
                           "hold_days", "settlements", "carry_bps", "price_pnl_long",
                           "price_pnl_short", "total_gross", "cost_bps", "net_bps",
                           "pnl_usdt", "reason"])
            w.writerow([t.long_sym, t.short_sym, t.entry_time, t.exit_time,
                       t.hold_days, t.settlements, t.carry_earned_bps,
                       t.price_pnl_long_bps, t.price_pnl_short_bps,
                       t.total_gross_bps, t.cost_bps, t.net_bps,
                       t.pnl_usdt, t.reason])

    def _log_signals(self):
        """Log funding snapshot every 5 min."""
        now = datetime.now(timezone.utc).isoformat()
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(SIGNALS_CSV)
        try:
            with open(SIGNALS_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if header:
                    w.writerow(["timestamp", "symbol", "mid_price", "funding_bps",
                               "basis_bps", "in_pair_long", "in_pair_short"])
                for sym, st in sorted(self.data.items()):
                    if st.mid_price == 0:
                        continue
                    in_long = any(p.long_sym == sym for p in self.pairs)
                    in_short = any(p.short_sym == sym for p in self.pairs)
                    w.writerow([now, sym, st.mid_price, round(st.funding_bps, 3),
                               round(st.basis_bps, 2), in_long, in_short])
        except Exception:
            pass

    # ── API ──────────────────────────────────────────────────────
    def get_state(self) -> dict:
        now = datetime.now(timezone.utc)
        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl_usdt

        # Funding rates sorted
        rates = {}
        next_fund_min = None
        for sym, st in sorted(self.data.items(), key=lambda x: x[1].funding_bps):
            if st.mid_price == 0:
                continue
            rates[sym] = {
                "price": st.mid_price,
                "funding_bps": round(st.funding_bps, 2),
                "basis_bps": round(st.basis_bps, 2),
                "stable": sym in STABLE_SYMBOLS,
            }
            if st.next_funding_ts > 0 and next_fund_min is None:
                mins = max(0, (datetime.fromtimestamp(st.next_funding_ts/1000, tz=timezone.utc) - now).total_seconds() / 60)
                next_fund_min = round(mins, 0)

        # Open pairs
        pairs = []
        unrealized_usdt = 0.0
        for pair in self.pairs:
            ld = self.data.get(pair.long_sym)
            sd = self.data.get(pair.short_sym)
            lp = ld.mid_price if ld and ld.mid_price > 0 else pair.long_entry_price
            sp = sd.mid_price if sd and sd.mid_price > 0 else pair.short_entry_price
            long_pnl = (lp / pair.long_entry_price - 1) * 1e4
            short_pnl = -(sp / pair.short_entry_price - 1) * 1e4
            price_pnl = long_pnl + short_pnl
            total = pair.total_carry_bps + price_pnl
            margin = pair.long_margin + pair.short_margin
            pnl_usdt = margin * total / 1e4
            unrealized_usdt += pnl_usdt
            held_h = (now - pair.entry_time).total_seconds() / 3600

            pairs.append({
                "long": pair.long_sym, "short": pair.short_sym,
                "entry_spread": round(pair.entry_spread_bps, 1),
                "settlements": pair.settlements_earned,
                "carry_bps": round(pair.total_carry_bps, 1),
                "price_pnl_bps": round(price_pnl, 1),
                "total_bps": round(total, 1),
                "pnl_usdt": round(pnl_usdt, 2),
                "hold_hours": round(held_h, 1),
                "margin": round(margin, 0),
            })

        return {
            "running": self.running, "ws_connected": self.ws_connected,
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "next_funding_min": next_fund_min,
            "capital": CAPITAL_USDT,
            "balance": round(balance, 2),
            "pnl_usdt": round(self._total_pnl_usdt, 2),
            "unrealized_usdt": round(unrealized_usdt, 2),
            "total_trades": n,
            "win_rate": round(self._wins / n, 3) if n > 0 else 0,
            "open_pairs": len(self.pairs),
            "max_pairs": MAX_PAIRS,
            "pairs": pairs,
            "rates": rates,
        }

    def get_trades(self, limit=30) -> list:
        return [t.__dict__ for t in self.trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        cum = 0.0
        pts = []
        for t in self.trades:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "pnl": round(cum, 2),
                        "balance": round(CAPITAL_USDT + cum, 2)})
        return pts


# ── FastAPI ──────────────────────────────────────────────────────────
bot = CarryBot()
app = FastAPI()
_html_cache = None

@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        _html_cache = Path(HTML_PATH).read_text()
    return _html_cache

@app.get("/api/state")
async def api_state():
    return JSONResponse(bot.get_state())

@app.get("/api/trades")
async def api_trades(limit: int = 30):
    return JSONResponse(bot.get_trades(limit))

@app.get("/api/pnl")
async def api_pnl():
    return JSONResponse(bot.get_pnl_curve())


# ── Entry ────────────────────────────────────────────────────────────
async def main():
    bot.running = True
    bot.started_at = datetime.now(timezone.utc)
    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    log.info("CarryBot — Funding carry trade | Dashboard: http://0.0.0.0:%d", WEB_PORT)
    log.info("Capital: $%.0f | Max %d pairs | $%.0f/leg | Min spread %.1f bps",
             CAPITAL_USDT, MAX_PAIRS, CAPITAL_USDT * MARGIN_PER_LEG_PCT / 100, MIN_SPREAD_BPS)
    log.info("Rebalance every %dh | Stable symbols: %s", REBALANCE_HOURS, ", ".join(STABLE_SYMBOLS))
    await asyncio.gather(server.serve(), bot.ws_loop(), bot.strategy_loop())


def entry():
    loop = asyncio.new_event_loop()
    def stop(s, f):
        log.info("Shutting down...")
        bot.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        loop.run_until_complete(main())
    finally:
        now = datetime.now(timezone.utc)
        for pair in list(bot.pairs):
            bot._close_pair(pair, now, "shutdown")
        n = len(bot.trades)
        if n:
            balance = CAPITAL_USDT + bot._total_pnl_usdt
            log.info("FINAL: %d trades | balance $%.2f | win %.0f%%",
                     n, balance, bot._wins/n*100)
        loop.close()


if __name__ == "__main__":
    entry()
