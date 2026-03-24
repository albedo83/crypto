"""Carry Bot — Cash-and-Carry funding collection.

Strategy: Short perp on highest-funding symbols = collect funding every 8h.
Simulates delta-neutral (spot long + perp short) so price changes cancel.
XMR is the primary target (+1.62 bps/8h mean, 95% positive, +17.8%/year).

Run:       python3 -m analysis.carry
Dashboard: http://0.0.0.0:8095
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

import orjson
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CARRY] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("carry")

VERSION = "7.0.0"

# ── Config ───────────────────────────────────────────────────────────

# Symbols ranked by mean funding (1-year backtest)
# XMR: +1.62 bps/8h (95% pos), LTC: +0.42, AAVE: +0.41, BTC: +0.38, SUI: +0.35
CARRY_SYMBOLS = ["XMRUSDT", "LTCUSDT", "AAVEUSDT", "BTCUSDT", "SUIUSDT"]
ALL_WS_SYMBOLS = [s.lower() for s in CARRY_SYMBOLS]

CAPITAL_USDT = 1000.0
LEVERAGE = 3.0              # perp leverage — with cash-and-carry, effective notional = capital * leverage / (1 + leverage)
MAX_PAIRS = 3               # max simultaneous carry positions
REBALANCE_HOURS = 168       # 7 days between rebalances
MIN_FUNDING_BPS = 0.5       # minimum funding rate to hold position

# For delta-neutral: you need spot + perp margin
# With 3x perp leverage: perp_margin = notional/3, spot = notional
# Total capital per pair = notional * (1 + 1/leverage) = notional * 4/3
# So notional = capital / (1 + 1/leverage) / n_pairs
# Simplified: we track the PERP notional and funding income on that notional

# Costs
SPOT_FEE_BPS = 1.5          # spot buy (one-way, BNB discount 0.075%)
PERP_FEE_BPS = 1.5          # perp open (one-way, BNB discount 0.015%)
ENTRY_COST_BPS = SPOT_FEE_BPS + PERP_FEE_BPS  # 3 bps one-way entry
EXIT_COST_BPS = SPOT_FEE_BPS + PERP_FEE_BPS   # 3 bps one-way exit
ROUNDTRIP_COST_BPS = ENTRY_COST_BPS + EXIT_COST_BPS  # 6 bps total

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "carry_v7_trades.csv")
STATE_FILE = os.path.join(OUTPUT_DIR, "carry_v7_state.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "carry.html")

WEB_PORT = 8095

def _build_ws_url():
    streams = []
    for s in ALL_WS_SYMBOLS:
        streams.extend([f"{s}@bookTicker", f"{s}@markPrice@1s"])
    return "wss://fstream.binance.com/stream?streams=" + "/".join(streams)

WS_URL = _build_ws_url()


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class SymbolState:
    mid_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    spread_bps: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_ts: int = 0
    basis_bps: float = 0.0
    msg_count: int = 0
    tick_ts: float = 0.0
    # Funding history (last 30 settlements = 10 days)
    funding_history: deque = field(default_factory=lambda: deque(maxlen=30))
    # Price ticks for sparklines
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))


@dataclass
class CarryPosition:
    symbol: str
    entry_time: datetime
    entry_price: float
    notional_usdt: float       # perp notional (= spot notional, delta-neutral)
    margin_usdt: float         # perp margin = notional / leverage
    funding_collected: float = 0.0  # cumulative funding received in USD
    funding_count: int = 0     # number of settlements collected
    last_funding_ts: int = 0
    last_funding_rate: float = 0.0
    funding_payments: list = field(default_factory=list)  # history of each payment


@dataclass
class CarryTrade:
    """A completed carry trade (position opened then closed)."""
    symbol: str
    entry_time: str
    exit_time: str
    hold_hours: float
    notional_usdt: float
    funding_collected: float
    funding_count: int
    entry_cost: float
    exit_cost: float
    net_pnl: float
    reason: str
    avg_funding_bps: float


class CarryBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s: SymbolState() for s in CARRY_SYMBOLS}
        self.positions: dict[str, CarryPosition] = {}
        self.trades: list[CarryTrade] = []
        self.running = False
        self._paused = False
        self._shutdown_event: asyncio.Event | None = None
        self.ws_connected = False
        self.started_at: datetime | None = None
        self._total_pnl = 0.0
        self._total_funding = 0.0
        self._total_costs = 0.0
        self._msg_rate = 0.0
        self._msg_count = 0
        self._msg_start = 0.0
        self._last_rebalance: datetime | None = None
        self._pending_settlements: dict[str, bool] = {}  # track processed settlements

    # ── Notional Calculation ─────────────────────────────────────
    def _notional_per_pair(self) -> float:
        """Notional per pair for delta-neutral carry."""
        current_capital = CAPITAL_USDT + self._total_pnl
        # capital_per_pair = current_capital / n_pairs
        # For delta-neutral: need spot (notional) + perp margin (notional/leverage)
        # capital_per_pair = notional * (1 + 1/leverage)
        # notional = capital_per_pair / (1 + 1/leverage)
        n = max(1, min(MAX_PAIRS, len(CARRY_SYMBOLS)))
        capital_per = current_capital / n
        notional = capital_per / (1 + 1 / LEVERAGE)
        return notional

    # ── WebSocket ─────────────────────────────────────────────────
    async def ws_loop(self):
        backoff = 3
        while self.running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    self.ws_connected = True
                    backoff = 3
                    connect_time = time.time()
                    log.info("WS connected (%d streams)", len(ALL_WS_SYMBOLS) * 2)
                    async for raw in ws:
                        if not self.running:
                            break
                        if time.time() - connect_time > 82800:
                            log.info("WS rotation (23h)")
                            break
                        self._on_msg(raw)
            except Exception as e:
                log.warning("WS error: %s — retry in %ds", e, backoff)
            self.ws_connected = False
            if self.running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_msg(self, raw):
        try:
            msg = orjson.loads(raw)
        except Exception:
            return
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        if not data:
            return
        self._msg_count += 1
        if "@bookTicker" in stream:
            self._on_book(data)
        elif "@markPrice" in stream:
            self._on_mark(data)

    def _on_book(self, d):
        sym = d.get("s", "")
        st = self.states.get(sym)
        if not st:
            return
        st.bid_price = float(d.get("b", 0))
        ask = float(d.get("a", 0))
        st.ask_price = ask
        if st.bid_price > 0 and ask > 0:
            st.mid_price = (st.bid_price + ask) / 2
            st.spread_bps = (ask - st.bid_price) / st.mid_price * 1e4
        st.msg_count += 1
        now = time.time()
        if now - st.tick_ts >= 1.0 and st.mid_price > 0:
            st.price_ticks.append((now, st.mid_price))
            st.tick_ts = now

    def _on_mark(self, d):
        sym = d.get("s", "")
        st = self.states.get(sym)
        if not st:
            return
        st.mark_price = float(d.get("p", 0))
        st.index_price = float(d.get("i", 0))
        st.funding_rate = float(d.get("r", 0))
        st.next_funding_ts = int(d.get("T", 0))
        if st.index_price > 0:
            st.basis_bps = (st.mark_price - st.index_price) / st.index_price * 1e4

    # ── Main Loop ────────────────────────────────────────────────
    async def main_loop(self):
        self._msg_start = time.time()
        while self.running:
            await asyncio.sleep(10)
            if not self.ws_connected:
                continue
            try:
                now_t = time.time()
                elapsed = now_t - self._msg_start
                if elapsed > 0:
                    self._msg_rate = self._msg_count / elapsed
                self._msg_count = 0
                self._msg_start = now_t

                self._check_funding_settlements()
                if not self._paused:
                    self._check_rebalance()
            except Exception:
                log.exception("Loop error")

    def _check_funding_settlements(self):
        """Check if any settlement just happened, collect funding."""
        now = datetime.now(timezone.utc)
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            if not st or st.next_funding_ts == 0:
                continue

            settle_dt = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
            secs_since = (now - settle_dt).total_seconds()

            # Settlement just happened (within last 60s) and we haven't processed it
            if 0 < secs_since < 120 and st.next_funding_ts != pos.last_funding_ts:
                rate = st.funding_rate
                rate_bps = rate * 1e4

                # Cash-and-carry: we are SHORT perp
                # When funding > 0: shorts receive funding (we earn)
                # When funding < 0: shorts pay funding (we lose)
                pnl = pos.notional_usdt * rate  # positive rate = we earn as short
                pos.funding_collected += pnl
                pos.funding_count += 1
                pos.last_funding_ts = st.next_funding_ts
                pos.last_funding_rate = rate
                pos.funding_payments.append({
                    "time": now.isoformat(), "rate_bps": round(rate_bps, 3),
                    "pnl": round(pnl, 4),
                })

                self._total_funding += pnl
                self._total_pnl += pnl

                # Record in funding history
                st.funding_history.append({"time": now.isoformat(), "rate_bps": rate_bps})

                emoji = "+" if pnl > 0 else "-"
                log.info("%s FUNDING %s | rate %+.2f bps | $%+.4f | total $%+.2f | #%d",
                         emoji, sym, rate_bps, pnl, pos.funding_collected, pos.funding_count)

    def _check_rebalance(self):
        """Open initial positions or rebalance to best symbols."""
        now = datetime.now(timezone.utc)

        # Initial entry
        if not self.positions:
            self._enter_positions(now)
            return

        # Periodic rebalance
        if self._last_rebalance and (now - self._last_rebalance).total_seconds() < REBALANCE_HOURS * 3600:
            return

        # Check if any position should be swapped
        self._rebalance(now)

    def _rank_symbols(self) -> list[tuple[str, float]]:
        """Rank symbols by current funding rate."""
        ranked = []
        for sym in CARRY_SYMBOLS:
            st = self.states.get(sym)
            if not st or st.funding_rate == 0:
                continue
            rate_bps = st.funding_rate * 1e4
            ranked.append((sym, rate_bps))
        ranked.sort(key=lambda x: x[1], reverse=True)  # highest funding first (best for shorts)
        return ranked

    def _enter_positions(self, now: datetime):
        """Enter carry positions on best symbols."""
        ranked = self._rank_symbols()
        notional = self._notional_per_pair()

        entered = 0
        for sym, rate_bps in ranked:
            if entered >= MAX_PAIRS:
                break
            if rate_bps < MIN_FUNDING_BPS:
                continue
            st = self.states.get(sym)
            if not st or st.mid_price == 0:
                continue

            margin = notional / LEVERAGE
            entry_cost = notional * ENTRY_COST_BPS / 1e4
            self._total_costs += entry_cost
            self._total_pnl -= entry_cost

            self.positions[sym] = CarryPosition(
                symbol=sym, entry_time=now, entry_price=st.mid_price,
                notional_usdt=notional, margin_usdt=margin,
            )
            entered += 1
            log.info("→ ENTER %s | notional $%.0f | margin $%.0f | rate %+.2f bps | cost $%.2f",
                     sym, notional, margin, rate_bps, entry_cost)

        self._last_rebalance = now
        if entered:
            log.info("Entered %d positions | capital $%.2f", entered, CAPITAL_USDT + self._total_pnl)

    def _rebalance(self, now: datetime):
        """Close underperforming positions, open better ones."""
        ranked = self._rank_symbols()
        top_syms = set(s for s, r in ranked[:MAX_PAIRS] if r >= MIN_FUNDING_BPS)
        current_syms = set(self.positions.keys())

        to_close = current_syms - top_syms
        to_open = top_syms - current_syms

        if not to_close and not to_open:
            self._last_rebalance = now
            return

        # Close positions no longer in top
        for sym in to_close:
            self._close_position(sym, now, "rebalance")

        # Open new positions
        notional = self._notional_per_pair()
        for sym in to_open:
            if len(self.positions) >= MAX_PAIRS:
                break
            st = self.states.get(sym)
            if not st or st.mid_price == 0:
                continue
            rate_bps = st.funding_rate * 1e4
            margin = notional / LEVERAGE
            entry_cost = notional * ENTRY_COST_BPS / 1e4
            self._total_costs += entry_cost
            self._total_pnl -= entry_cost

            self.positions[sym] = CarryPosition(
                symbol=sym, entry_time=now, entry_price=st.mid_price,
                notional_usdt=notional, margin_usdt=margin,
            )
            log.info("→ REBAL ENTER %s | $%.0f | rate %+.2f bps", sym, notional, rate_bps)

        self._last_rebalance = now

    def _close_position(self, sym: str, now: datetime, reason: str):
        pos = self.positions.pop(sym)
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        exit_cost = pos.notional_usdt * EXIT_COST_BPS / 1e4
        self._total_costs += exit_cost
        self._total_pnl -= exit_cost

        net_pnl = pos.funding_collected - (pos.notional_usdt * ROUNDTRIP_COST_BPS / 1e4)
        avg_bps = (pos.funding_collected / pos.funding_count / pos.notional_usdt * 1e4
                    if pos.funding_count > 0 else 0)

        trade = CarryTrade(
            symbol=sym, entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
            hold_hours=round(hold_h, 1), notional_usdt=round(pos.notional_usdt, 2),
            funding_collected=round(pos.funding_collected, 4),
            funding_count=pos.funding_count,
            entry_cost=round(pos.notional_usdt * ENTRY_COST_BPS / 1e4, 4),
            exit_cost=round(exit_cost, 4),
            net_pnl=round(net_pnl, 4),
            reason=reason, avg_funding_bps=round(avg_bps, 3),
        )
        self.trades.append(trade)
        self._write_csv(trade)

        log.info("← CLOSE %s | %.0fh | %d settlements | funding $%+.4f | net $%+.4f | %s",
                 sym, hold_h, pos.funding_count, pos.funding_collected, net_pnl, reason)

    def _write_csv(self, t: CarryTrade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        with open(TRADES_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["symbol", "entry_time", "exit_time", "hold_hours",
                           "notional_usdt", "funding_collected", "funding_count",
                           "entry_cost", "exit_cost", "net_pnl", "reason", "avg_funding_bps"])
            w.writerow([t.symbol, t.entry_time, t.exit_time, t.hold_hours,
                       t.notional_usdt, t.funding_collected, t.funding_count,
                       t.entry_cost, t.exit_cost, t.net_pnl, t.reason, t.avg_funding_bps])

    # ── Persistence ──────────────────────────────────────────────
    def _save_state(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = {
            "total_pnl": self._total_pnl,
            "total_funding": self._total_funding,
            "total_costs": self._total_costs,
            "positions": [{
                "symbol": p.symbol, "entry_time": p.entry_time.isoformat(),
                "entry_price": p.entry_price, "notional_usdt": p.notional_usdt,
                "margin_usdt": p.margin_usdt, "funding_collected": p.funding_collected,
                "funding_count": p.funding_count, "last_funding_ts": p.last_funding_ts,
                "last_funding_rate": p.last_funding_rate,
            } for p in self.positions.values()],
        }
        with open(STATE_FILE, "wb") as f:
            f.write(orjson.dumps(data))

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "rb") as f:
                data = orjson.loads(f.read())
            self._total_pnl = data.get("total_pnl", 0)
            self._total_funding = data.get("total_funding", 0)
            self._total_costs = data.get("total_costs", 0)
            for p in data.get("positions", []):
                pos = CarryPosition(
                    symbol=p["symbol"],
                    entry_time=datetime.fromisoformat(p["entry_time"]),
                    entry_price=p["entry_price"],
                    notional_usdt=p["notional_usdt"],
                    margin_usdt=p["margin_usdt"],
                    funding_collected=p.get("funding_collected", 0),
                    funding_count=p.get("funding_count", 0),
                    last_funding_ts=p.get("last_funding_ts", 0),
                    last_funding_rate=p.get("last_funding_rate", 0),
                )
                self.positions[pos.symbol] = pos
            n_pos = len(self.positions)
            if n_pos or self._total_pnl != 0:
                log.info("Restored state: %d positions | P&L $%.2f | funding $%.2f",
                         n_pos, self._total_pnl, self._total_funding)
            os.rename(STATE_FILE, STATE_FILE + ".loaded")
        except Exception:
            log.exception("Failed to load state")

    def _load_trades_csv(self):
        if not os.path.exists(TRADES_CSV):
            return
        try:
            with open(TRADES_CSV) as f:
                for row in csv.DictReader(f):
                    trade = CarryTrade(
                        symbol=row["symbol"], entry_time=row["entry_time"],
                        exit_time=row["exit_time"], hold_hours=float(row["hold_hours"]),
                        notional_usdt=float(row["notional_usdt"]),
                        funding_collected=float(row["funding_collected"]),
                        funding_count=int(row["funding_count"]),
                        entry_cost=float(row["entry_cost"]),
                        exit_cost=float(row["exit_cost"]),
                        net_pnl=float(row["net_pnl"]),
                        reason=row["reason"],
                        avg_funding_bps=float(row.get("avg_funding_bps", 0)),
                    )
                    self.trades.append(trade)
            if self.trades:
                log.info("Loaded %d historical trades", len(self.trades))
        except Exception:
            log.exception("Failed to load trades")

    # ── API ──────────────────────────────────────────────────────
    def get_state(self) -> dict:
        now = datetime.now(timezone.utc)
        balance = CAPITAL_USDT + self._total_pnl

        # Next settlement
        next_fund_min = None
        for st in self.states.values():
            if st.next_funding_ts > 0:
                settle = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
                mins = max(0, (settle - now).total_seconds() / 60)
                next_fund_min = round(mins)
                break

        # Next rebalance
        next_rebal_h = None
        if self._last_rebalance:
            h_since = (now - self._last_rebalance).total_seconds() / 3600
            next_rebal_h = round(max(0, REBALANCE_HOURS - h_since), 1)

        positions = []
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            rate_bps = st.funding_rate * 1e4 if st else 0
            hold_h = (now - pos.entry_time).total_seconds() / 3600
            daily_rate = pos.funding_collected / max(1, hold_h / 24)

            positions.append({
                "symbol": sym,
                "notional": round(pos.notional_usdt, 2),
                "margin": round(pos.margin_usdt, 2),
                "current_rate_bps": round(rate_bps, 3),
                "funding_collected": round(pos.funding_collected, 4),
                "funding_count": pos.funding_count,
                "last_rate_bps": round(pos.last_funding_rate * 1e4, 3),
                "hold_hours": round(hold_h, 1),
                "daily_rate": round(daily_rate, 4),
                "entry_time": pos.entry_time.isoformat(),
            })

        return {
            "version": VERSION, "strategy": "Cash-and-Carry Funding",
            "paused": self._paused,
            "running": self.running, "ws_connected": self.ws_connected,
            "balance": round(balance, 2),
            "total_pnl": round(self._total_pnl, 2),
            "total_funding": round(self._total_funding, 4),
            "total_costs": round(self._total_costs, 4),
            "leverage": LEVERAGE,
            "n_positions": len(self.positions), "max_pairs": MAX_PAIRS,
            "positions": positions,
            "next_funding_min": next_fund_min,
            "next_rebalance_h": next_rebal_h,
            "total_trades": len(self.trades),
            "session": "24/7",  # carry runs all day
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "msg_rate": round(self._msg_rate),
        }

    def get_ticker(self) -> dict:
        tickers = {}
        for sym in CARRY_SYMBOLS:
            st = self.states.get(sym)
            if not st or st.mid_price == 0:
                continue
            chart = [{"t": round(ts), "p": p} for ts, p in st.price_ticks]
            # Annualized funding rate
            rate_bps = st.funding_rate * 1e4
            ann_pct = rate_bps * 3 * 365 / 100

            # Recent funding history
            recent_rates = [h["rate_bps"] for h in st.funding_history]
            mean_rate = sum(recent_rates) / len(recent_rates) if recent_rates else rate_bps

            tickers[sym] = {
                "price": st.mid_price,
                "spread_bps": round(st.spread_bps, 2),
                "funding_bps": round(rate_bps, 3),
                "ann_pct": round(ann_pct, 1),
                "mean_funding_bps": round(mean_rate, 3),
                "basis_bps": round(st.basis_bps, 2),
                "in_position": sym in self.positions,
                "chart": chart[-60:],
            }
        return {"tickers": tickers, "total_msgs_sec": round(self._msg_rate)}

    def get_trades(self, limit=50) -> list:
        return [t.__dict__ for t in self.trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        # Build from funding payments across all positions
        events = []
        for t in self.trades:
            events.append({"time": t.exit_time, "pnl": t.net_pnl, "type": "trade_close"})

        cum = 0.0
        pts = []
        for e in sorted(events, key=lambda x: x["time"]):
            cum += e["pnl"]
            pts.append({"time": e["time"], "cum_pnl": round(cum, 4),
                        "balance": round(CAPITAL_USDT + cum, 2)})

        # Add current unrealized
        if self.positions:
            pts.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "cum_pnl": round(self._total_pnl, 4),
                "balance": round(CAPITAL_USDT + self._total_pnl, 2),
            })
        return pts


# ── FastAPI ──────────────────────────────────────────────────────────

bot = CarryBot()
app = FastAPI()
_html_cache = None


@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        html_path = HTML_PATH
        if not os.path.exists(html_path):
            # Fallback: minimal page
            _html_cache = f"<html><body><h1>Carry Bot v{VERSION}</h1><pre id='s'></pre><script>setInterval(()=>fetch('/api/state').then(r=>r.json()).then(d=>document.getElementById('s').textContent=JSON.stringify(d,null,2)),5000)</script></body></html>"
        else:
            _html_cache = Path(html_path).read_text().replace("{{VERSION}}", VERSION)
    return _html_cache


@app.get("/api/state")
async def api_state():
    return JSONResponse(bot.get_state())


@app.get("/api/ticker")
async def api_ticker():
    return JSONResponse(bot.get_ticker())


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return JSONResponse(bot.get_trades(limit))


@app.get("/api/pnl")
async def api_pnl():
    return JSONResponse(bot.get_pnl_curve())


@app.post("/api/pause")
async def api_pause():
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        bot._close_position(sym, now, "manual_stop")
    bot._paused = True
    bot._save_state()
    log.info("PAUSED — all positions closed")
    return JSONResponse({"status": "paused"})


@app.post("/api/resume")
async def api_resume():
    bot._paused = False
    bot._last_rebalance = None  # force rebalance on resume
    log.info("RESUMED")
    return JSONResponse({"status": "resumed"})


@app.post("/api/reset")
async def api_reset():
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        bot._close_position(sym, now, "reset")
    bot._paused = False
    bot._total_pnl = 0.0
    bot._total_funding = 0.0
    bot._total_costs = 0.0
    bot.trades.clear()
    bot._last_rebalance = None
    # Clear CSV
    if os.path.exists(TRADES_CSV):
        os.rename(TRADES_CSV, TRADES_CSV + f".bak.{int(time.time())}")
    log.info("RESET — all data cleared")
    return JSONResponse({"status": "reset"})


# ── Main ─────────────────────────────────────────────────────────────

async def run():
    bot.running = True
    bot.started_at = datetime.now(timezone.utc)
    bot._shutdown_event = asyncio.Event()

    bot._load_trades_csv()
    bot._load_state()

    def _sig(sig, frame):
        log.info("Shutdown signal received")
        bot.running = False
        bot._save_state()
        if bot._shutdown_event:
            bot._shutdown_event.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("Carry Bot v%s starting | capital $%.0f | %dx leverage | %d symbols | port %d",
             VERSION, CAPITAL_USDT, LEVERAGE, len(CARRY_SYMBOLS), WEB_PORT)
    log.info("Symbols: %s", ", ".join(CARRY_SYMBOLS))

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)

    tasks = [
        asyncio.create_task(bot.ws_loop()),
        asyncio.create_task(bot.main_loop()),
        asyncio.create_task(server.serve()),
    ]

    await bot._shutdown_event.wait()
    bot.running = False
    bot._save_state()
    for t in tasks:
        t.cancel()
    log.info("Shutdown complete | P&L $%.2f | Funding $%.4f", bot._total_pnl, bot._total_funding)


if __name__ == "__main__":
    asyncio.run(run())
