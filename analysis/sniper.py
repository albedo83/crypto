"""Sniper Bot — Funding Sniper + Extreme Reversion strategy.

Two independent signals:
1. Funding Sniper: enter 1h before settlement when funding >3bps (fade the unwind)
2. Extreme Reversion: enter when price moved >150bps in 1h (fade the overshoot)

Sessions: Asia (0-8h) + Overnight (21-24h) only. US and Europe excluded.
Symbols: filtered altcoins (no TRX, BCH, TON — known losers).

Run:       python3 -m analysis.sniper
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

import aiohttp
import numpy as np
import orjson
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SNP] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sniper")

VERSION = "6.0.0"

# ── Config ───────────────────────────────────────────────────────────
REFERENCE_SYMBOLS = ["btcusdt", "ethusdt"]
TRADE_SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "ZROUSDT", "AAVEUSDT", "SUIUSDT",
    "AVAXUSDT", "XRPUSDT", "XMRUSDT", "XLMUSDT", "LTCUSDT",
    # Excluded (losers in backtest): TRXUSDT, BCHUSDT, TONUSDT, HYPEUSDT, LINKUSDT
]
ALL_SYMBOLS = REFERENCE_SYMBOLS + [s.lower() for s in TRADE_SYMBOLS]
TRADE_SET = set(TRADE_SYMBOLS)

# WS streams
def _build_ws_urls():
    streams = []
    for s in ALL_SYMBOLS:
        streams.extend([f"{s}@bookTicker", f"{s}@aggTrade", f"{s}@markPrice@1s"])
    return ["wss://fstream.binance.com/stream?streams=" + "/".join(streams)]

WS_URLS = _build_ws_urls()
WEB_PORT = 8095

# Sessions: only Asia + Overnight (US loses on funding sniper)
TRADE_SESSIONS = {"asian": (0, 8), "overnight": (21, 24)}

# Capital
CAPITAL_USDT = 1000.0
MAX_POSITIONS = 4
MAX_RISK_TOTAL_PCT = 90.0
HOLD_MINUTES = 120

# Costs
BNB_FEE_DISCOUNT = True
COST_BPS = 3.0 if BNB_FEE_DISCOUNT else 4.0
SLIPPAGE_BPS = 1.0

# ── Signal 1: Funding Sniper ─────────────────────────────────────────
FUNDING_THRESH_BPS = 3.0       # min |funding rate| to trigger
FUNDING_ENTRY_BEFORE = 60      # enter X min before settlement
FUNDING_MARGIN_PCT = 30.0      # high conviction → 30% margin
FUNDING_LEVERAGE = 2.0         # moderate leverage

# ── Signal 2: Extreme Reversion ──────────────────────────────────────
EXTREME_THRESH_BPS = 150.0     # min 1h move to trigger
EXTREME_LOOKBACK = 60          # 60 × 1-min ticks = 1h
EXTREME_MARGIN_PCT = 20.0      # standard margin
EXTREME_LEVERAGE = 1.5         # conservative leverage
EXTREME_COOLDOWN_MIN = 30      # don't re-enter same symbol for 30min

# ── Exit config ──────────────────────────────────────────────────────
STOP_LOSS_BPS = -50.0          # leveraged stop loss
TRAIL_ACTIVATE_BPS = 25.0      # raw bps to activate trailing
TRAIL_DRAWDOWN_BPS = 15.0      # raw bps drawdown from peak to exit
MIN_HOLD_MINUTES = 5           # min hold before reversal check

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "sniper_trades.csv")
SIGNALS_CSV = os.path.join(OUTPUT_DIR, "sniper_signals.csv")
POSITIONS_FILE = os.path.join(OUTPUT_DIR, "sniper_positions.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "sniper.html")


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class SymbolState:
    mid_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    spread_bps: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_ts: int = 0
    basis_bps: float = 0.0
    msg_count: int = 0
    last_trade_price: float = 0.0
    last_trade_side: str = ""
    tick_ts: float = 0.0
    # Price history for extreme reversion (1 per minute, last 2h)
    price_1m: deque = field(default_factory=lambda: deque(maxlen=120))
    price_1m_ts: float = 0.0
    # Price ticks for sparklines (1 per second, last 5 min)
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))


@dataclass
class Position:
    symbol: str
    direction: int           # 1=LONG, -1=SHORT
    entry_price: float
    entry_time: datetime
    leverage: float
    size_usdt: float
    margin_usdt: float
    signal_type: str         # "funding" or "extreme"
    detail: str
    peak_bps: float = 0.0
    funding_paid: float = 0.0
    last_funding_ts: int = 0


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    hold_min: float
    leverage: float
    size_usdt: float
    gross_bps: float
    net_bps: float
    leveraged_net_bps: float
    pnl_usdt: float
    reason: str
    signal_type: str
    session: str


class SniperBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s.upper(): SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        self._cooldowns: dict[str, datetime] = {}
        self._paused = False
        self.trades: list[Trade] = []
        self.running = False
        self._shutdown_event: asyncio.Event | None = None
        self.ws_connected = False
        self.ws_count = 0
        self.started_at: datetime | None = None
        self._total_pnl_usdt = 0.0
        self._total_gross = 0.0
        self._wins = 0
        self._msg_window_count = 0
        self._msg_window_start = 0.0
        self._msg_rate = 0.0

    def _current_session(self) -> str | None:
        h = datetime.now(timezone.utc).hour
        for name, (start, end) in TRADE_SESSIONS.items():
            if start <= h < end:
                return name
        return None

    # ── CSV Persistence ───────────────────────────────────────
    def _load_trades_csv(self):
        if not os.path.exists(TRADES_CSV):
            return
        try:
            with open(TRADES_CSV) as f:
                for row in csv.DictReader(f):
                    trade = Trade(
                        symbol=row["symbol"], direction=row["direction"],
                        entry_time=row["entry_time"], exit_time=row["exit_time"],
                        entry_price=float(row["entry_price"]), exit_price=float(row["exit_price"]),
                        hold_min=float(row["hold_min"]), leverage=float(row["leverage"]),
                        size_usdt=float(row["size_usdt"]), signals={},
                        gross_bps=float(row["gross_bps"]), net_bps=float(row["net_bps"]),
                        leveraged_net_bps=float(row["leveraged_net_bps"]),
                        pnl_usdt=float(row["pnl_usdt"]), reason=row["reason"],
                        signal_type=row.get("signal_type", "?"), session=row.get("session", "?"),
                    )
                    self.trades.append(trade)
                    self._total_gross += trade.gross_bps
                    self._total_pnl_usdt += trade.pnl_usdt
                    if trade.pnl_usdt > 0:
                        self._wins += 1
            n = len(self.trades)
            if n:
                balance = CAPITAL_USDT + self._total_pnl_usdt
                log.info("Restored %d trades | balance $%.2f | win %.0f%%", n, balance, self._wins / n * 100)
        except Exception:
            log.exception("Failed to load trades CSV")

    def _save_positions(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = []
        for pos in self.positions.values():
            data.append({
                "symbol": pos.symbol, "direction": pos.direction,
                "entry_price": pos.entry_price, "entry_time": pos.entry_time.isoformat(),
                "leverage": pos.leverage, "size_usdt": pos.size_usdt,
                "margin_usdt": pos.margin_usdt, "signal_type": pos.signal_type,
                "detail": pos.detail, "peak_bps": pos.peak_bps,
                "funding_paid": pos.funding_paid, "last_funding_ts": pos.last_funding_ts,
            })
        with open(POSITIONS_FILE, "wb") as f:
            f.write(orjson.dumps(data))
        if data:
            log.info("Saved %d positions to disk", len(data))

    def _load_positions(self):
        if not os.path.exists(POSITIONS_FILE):
            return
        try:
            with open(POSITIONS_FILE, "rb") as f:
                data = orjson.loads(f.read())
            for p in data:
                pos = Position(
                    symbol=p["symbol"], direction=p["direction"],
                    entry_price=p["entry_price"],
                    entry_time=datetime.fromisoformat(p["entry_time"]),
                    leverage=p["leverage"], size_usdt=p["size_usdt"],
                    margin_usdt=p["margin_usdt"], signal_type=p.get("signal_type", "?"),
                    detail=p.get("detail", ""), peak_bps=p.get("peak_bps", 0),
                    funding_paid=p.get("funding_paid", 0), last_funding_ts=p.get("last_funding_ts", 0),
                )
                self.positions[pos.symbol] = pos
            if self.positions:
                log.info("Restored %d positions from disk", len(self.positions))
            os.remove(POSITIONS_FILE)
        except Exception:
            log.exception("Failed to load positions")

    # ── WebSocket ─────────────────────────────────────────────
    async def ws_loop(self):
        tasks = [self._ws_connect(url, i) for i, url in enumerate(WS_URLS)]
        await asyncio.gather(*tasks)

    async def _ws_connect(self, url: str, idx: int):
        backoff = 3
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws_count += 1
                    self.ws_connected = True
                    backoff = 3
                    connect_time = time.time()
                    log.info("WS[%d] connected", idx)
                    async for raw in ws:
                        if not self.running:
                            break
                        if time.time() - connect_time > 82800:
                            log.info("WS[%d] rotation (23h)", idx)
                            break
                        self._on_msg(raw)
            except Exception as e:
                log.warning("WS[%d] error: %s — retry in %ds", idx, e, backoff)
            self.ws_count = max(0, self.ws_count - 1)
            self.ws_connected = self.ws_count > 0
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
        self._msg_window_count += 1
        if "@bookTicker" in stream:
            self._on_book(data)
        elif "@aggTrade" in stream:
            self._on_trade(data)
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
        st.bid_qty = float(d.get("B", 0))
        st.ask_qty = float(d.get("A", 0))
        if st.bid_price > 0 and ask > 0:
            st.mid_price = (st.bid_price + ask) / 2
            st.spread_bps = (ask - st.bid_price) / st.mid_price * 1e4
        st.msg_count += 1
        now = time.time()
        if now - st.tick_ts >= 1.0 and st.mid_price > 0:
            st.price_ticks.append((now, st.mid_price))
            st.tick_ts = now
        # 1-minute price sample for extreme reversion
        if now - st.price_1m_ts >= 60.0 and st.mid_price > 0:
            st.price_1m.append(st.mid_price)
            st.price_1m_ts = now

    def _on_trade(self, d):
        sym = d.get("s", "")
        st = self.states.get(sym)
        if not st:
            return
        st.last_trade_price = float(d.get("p", 0))
        st.last_trade_side = "SELL" if d.get("m", False) else "BUY"
        st.msg_count += 1

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

    # ── Signal Loop ───────────────────────────────────────────
    async def signal_loop(self):
        self._msg_window_start = time.time()
        while self.running:
            await asyncio.sleep(10)
            if not self.ws_connected:
                continue
            try:
                now_t = time.time()
                elapsed = now_t - self._msg_window_start
                if elapsed > 0:
                    self._msg_rate = self._msg_window_count / elapsed
                self._msg_window_count = 0
                self._msg_window_start = now_t
                self._check_exits()
                self._check_entries()
            except Exception:
                log.exception("Signal error")

    def _margin_used(self) -> float:
        return sum(p.margin_usdt for p in self.positions.values())

    def _available_capital(self) -> float:
        current = CAPITAL_USDT + self._total_pnl_usdt
        return max(0, current * MAX_RISK_TOTAL_PCT / 100 - self._margin_used())

    # ── Exits ─────────────────────────────────────────────────
    def _check_exits(self):
        now = datetime.now(timezone.utc)
        for sym in list(self.positions.keys()):
            st = self.states.get(sym)
            if not st or st.mid_price == 0:
                continue
            pos = self.positions[sym]
            mid = st.mid_price
            held = (now - pos.entry_time).total_seconds() / 60

            unrealized = pos.direction * (mid / pos.entry_price - 1) * 1e4
            leveraged = unrealized * pos.leverage

            if unrealized > pos.peak_bps:
                pos.peak_bps = unrealized

            # Funding simulation
            if st.next_funding_ts > 0 and st.next_funding_ts != pos.last_funding_ts:
                settle = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
                if 0 < (now - settle).total_seconds() < 60:
                    cost = pos.size_usdt * st.funding_rate * pos.direction
                    pos.funding_paid += cost
                    pos.last_funding_ts = st.next_funding_ts

            exit_reason = None
            if held >= HOLD_MINUTES:
                exit_reason = "timeout"
            elif leveraged < STOP_LOSS_BPS:
                exit_reason = "stop_loss"
            elif held >= MIN_HOLD_MINUTES and (
                pos.peak_bps >= TRAIL_ACTIVATE_BPS and
                unrealized < pos.peak_bps - TRAIL_DRAWDOWN_BPS
            ):
                exit_reason = "trail_stop"

            if exit_reason:
                self._close_position(sym, mid, now, exit_reason)
                self._cooldowns[sym] = now + timedelta(minutes=EXTREME_COOLDOWN_MIN)

    # ── Entries ────────────────────────────────────────────────
    def _check_entries(self):
        now = datetime.now(timezone.utc)
        session = self._current_session()
        if session is None or self._paused:
            return
        if len(self.positions) >= MAX_POSITIONS:
            return

        remaining = self._available_capital()
        current_capital = CAPITAL_USDT + self._total_pnl_usdt

        # ── Signal 1: Funding Sniper ──
        for sym in TRADE_SYMBOLS:
            if sym in self.positions or len(self.positions) >= MAX_POSITIONS:
                continue
            st = self.states.get(sym)
            if not st or st.mid_price == 0 or st.next_funding_ts == 0:
                continue

            rate_bps = st.funding_rate * 1e4
            if abs(rate_bps) < FUNDING_THRESH_BPS:
                continue

            settle = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
            mins_to = (settle - now).total_seconds() / 60
            if not (0 < mins_to <= FUNDING_ENTRY_BEFORE):
                continue

            direction = -1 if rate_bps > 0 else 1
            margin = min(current_capital * FUNDING_MARGIN_PCT / 100, remaining)
            if margin < 50:
                continue
            size = margin * FUNDING_LEVERAGE

            side = "LONG" if direction == 1 else "SHORT"
            detail = f"fund={rate_bps:+.1f}bps settle={mins_to:.0f}min"
            self.positions[sym] = Position(
                symbol=sym, direction=direction, entry_price=st.mid_price,
                entry_time=now, leverage=FUNDING_LEVERAGE,
                size_usdt=size, margin_usdt=margin,
                signal_type="funding", detail=detail,
            )
            remaining -= margin
            log.info("→ FUNDING %s %s @ %.4f | $%.0f (%.0fx) | %s | %d/%d",
                     side, sym, st.mid_price, size, FUNDING_LEVERAGE, detail,
                     len(self.positions), MAX_POSITIONS)

        # ── Signal 2: Extreme Reversion ──
        for sym in TRADE_SYMBOLS:
            if sym in self.positions or len(self.positions) >= MAX_POSITIONS:
                continue
            if sym in self._cooldowns and now < self._cooldowns[sym]:
                continue
            st = self.states.get(sym)
            if not st or st.mid_price == 0:
                continue
            if len(st.price_1m) < EXTREME_LOOKBACK:
                continue

            price_now = st.price_1m[-1]
            price_1h_ago = st.price_1m[-EXTREME_LOOKBACK]
            move_bps = (price_now / price_1h_ago - 1) * 1e4

            if abs(move_bps) < EXTREME_THRESH_BPS:
                continue

            # Fade the extreme move
            direction = -1 if move_bps > 0 else 1
            margin = min(current_capital * EXTREME_MARGIN_PCT / 100, remaining)
            if margin < 50:
                continue
            size = margin * EXTREME_LEVERAGE

            side = "LONG" if direction == 1 else "SHORT"
            detail = f"move={move_bps:+.0f}bps/1h"
            self.positions[sym] = Position(
                symbol=sym, direction=direction, entry_price=st.mid_price,
                entry_time=now, leverage=EXTREME_LEVERAGE,
                size_usdt=size, margin_usdt=margin,
                signal_type="extreme", detail=detail,
            )
            remaining -= margin
            log.info("→ EXTREME %s %s @ %.4f | $%.0f (%.0fx) | %s | %d/%d",
                     side, sym, st.mid_price, size, EXTREME_LEVERAGE, detail,
                     len(self.positions), MAX_POSITIONS)

    # ── Close Position ────────────────────────────────────────
    def _close_position(self, sym: str, exit_price: float, now: datetime, reason: str) -> float:
        pos = self.positions.pop(sym)
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
        hold_min = (now - pos.entry_time).total_seconds() / 60

        pnl_usdt = pos.size_usdt * pos.direction * (exit_price / pos.entry_price - 1)
        fee_usdt = pos.size_usdt * COST_BPS / 1e4
        slip_usdt = pos.size_usdt * SLIPPAGE_BPS / 1e4
        net_pnl = pnl_usdt - fee_usdt - slip_usdt - pos.funding_paid

        total_cost = COST_BPS + SLIPPAGE_BPS
        lev_gross = gross_bps * pos.leverage
        lev_net = lev_gross - total_cost

        self._total_gross += gross_bps
        self._total_pnl_usdt += net_pnl
        if net_pnl > 0:
            self._wins += 1

        trade = Trade(
            symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
            entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
            entry_price=pos.entry_price, exit_price=exit_price,
            hold_min=round(hold_min, 1), leverage=pos.leverage,
            size_usdt=round(pos.size_usdt, 2), gross_bps=round(gross_bps, 2),
            net_bps=round(gross_bps - total_cost, 2),
            leveraged_net_bps=round(lev_net, 2), pnl_usdt=round(net_pnl, 2),
            reason=reason, signal_type=pos.signal_type,
            session=self._current_session() or "?",
        )
        self.trades.append(trade)
        self._write_csv(trade)

        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl_usdt
        wr = self._wins / n * 100
        arrow = "+" if net_pnl > 0 else "-"
        fund_str = f" fund=${pos.funding_paid:+.3f}" if abs(pos.funding_paid) > 0.001 else ""
        log.info("%s %s %s %s | %.0fmin | $%.0f (%.0fx) | gross %+.1f | cost $%.3f%s | %+.2f$ | bal $%.2f (#%d %.0f%%)",
                 arrow, pos.signal_type.upper()[:4], trade.direction, sym, hold_min,
                 pos.size_usdt, pos.leverage, gross_bps,
                 fee_usdt + slip_usdt, fund_str, net_pnl, balance, n, wr)
        return net_pnl

    def _write_csv(self, t: Trade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        with open(TRADES_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["symbol", "direction", "entry_time", "exit_time",
                           "entry_price", "exit_price", "hold_min", "leverage",
                           "size_usdt", "gross_bps", "net_bps", "leveraged_net_bps",
                           "pnl_usdt", "reason", "signal_type", "session"])
            w.writerow([t.symbol, t.direction, t.entry_time, t.exit_time,
                       t.entry_price, t.exit_price, t.hold_min, t.leverage,
                       t.size_usdt, t.gross_bps, t.net_bps, t.leveraged_net_bps,
                       t.pnl_usdt, t.reason, t.signal_type, t.session])

    # ── API ───────────────────────────────────────────────────
    def get_state(self) -> dict:
        n = len(self.trades)
        now = datetime.now(timezone.utc)
        positions = []
        unrealized_total = 0.0
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            mid = st.mid_price if st else pos.entry_price
            unr = pos.direction * (mid / pos.entry_price - 1) * 1e4
            pnl_u = pos.size_usdt * pos.direction * (mid / pos.entry_price - 1)
            unrealized_total += pnl_u
            positions.append({
                "symbol": sym, "direction": "LONG" if pos.direction == 1 else "SHORT",
                "entry_price": pos.entry_price, "signal_type": pos.signal_type,
                "detail": pos.detail, "size_usdt": round(pos.size_usdt, 2),
                "leverage": pos.leverage,
                "unrealized_bps": round(unr * pos.leverage, 2),
                "pnl_usdt": round(pnl_u, 2),
                "peak_bps": round(pos.peak_bps, 2),
                "trail_active": pos.peak_bps >= TRAIL_ACTIVATE_BPS,
                "hold_min": round((now - pos.entry_time).total_seconds() / 60, 1),
            })

        # Next funding
        next_fund_min = None
        for st in self.states.values():
            if st.next_funding_ts > 0:
                mins = max(0, (datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc) - now).total_seconds() / 60)
                if next_fund_min is None or mins < next_fund_min:
                    next_fund_min = round(mins)
                break

        balance = CAPITAL_USDT + self._total_pnl_usdt
        return {
            "version": VERSION, "paused": self._paused,
            "running": self.running, "ws_connected": self.ws_connected,
            "n_symbols": len(TRADE_SYMBOLS),
            "n_positions": len(self.positions), "max_positions": MAX_POSITIONS,
            "balance": round(balance, 2),
            "total_pnl_usdt": round(self._total_pnl_usdt, 2),
            "unrealized_usdt": round(unrealized_total, 2),
            "net_pnl_usdt": round(self._total_pnl_usdt, 2),
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "session": self._current_session() or "excluded",
            "next_funding_min": next_fund_min,
            "total_trades": n,
            "win_rate": round(self._wins / n, 3) if n > 0 else 0,
            "positions": positions,
        }

    def get_trades(self, limit=50) -> list:
        return [t.__dict__ for t in self.trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        cum = 0.0
        pts = []
        for t in self.trades:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                        "balance": round(CAPITAL_USDT + cum, 2)})
        return pts

    def get_ticker(self) -> dict:
        tickers = {}
        for sym, st in self.states.items():
            if st.mid_price == 0:
                continue
            # 1h return from price_1m buffer
            ret_1h = 0.0
            if len(st.price_1m) >= 60:
                ret_1h = (st.price_1m[-1] / st.price_1m[-60] - 1) * 1e4

            chart = [{"t": round(ts), "p": p} for ts, p in st.price_ticks]
            imb = st.bid_qty / (st.bid_qty + st.ask_qty) if (st.bid_qty + st.ask_qty) > 0 else 0.5
            tickers[sym] = {
                "price": st.mid_price, "spread_bps": round(st.spread_bps, 2),
                "ret_1h_bps": round(ret_1h, 1),
                "funding_bps": round(st.funding_rate * 1e4, 2),
                "basis_bps": round(st.basis_bps, 2),
                "imbalance": round(imb, 3),
                "last_side": st.last_trade_side,
                "chart": chart[-60:],
            }
        return {"tickers": tickers, "total_msgs_sec": round(self._msg_rate)}


# ── FastAPI ──────────────────────────────────────────────────────────
bot = SniperBot()
app = FastAPI()
_html_cache = None

@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        # Reuse livebot HTML for now (TODO: sniper-specific dashboard)
        html_path = HTML_PATH if os.path.exists(HTML_PATH) else os.path.join(os.path.dirname(__file__), "livebot.html")
        _html_cache = Path(html_path).read_text().replace("{{VERSION}}", VERSION)
    return _html_cache

@app.get("/api/state")
async def api_state():
    return JSONResponse(bot.get_state())

@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return JSONResponse(bot.get_trades(limit))

@app.get("/api/pnl")
async def api_pnl():
    return JSONResponse(bot.get_pnl_curve())

@app.get("/api/ticker")
async def api_ticker():
    return JSONResponse(bot.get_ticker())

@app.post("/api/pause")
async def api_pause():
    now = datetime.now(timezone.utc)
    closed = 0
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.mid_price > 0:
            bot._close_position(sym, st.mid_price, now, "manual_stop")
            closed += 1
    bot._paused = True
    log.info("PAUSED — %d positions closed", closed)
    return JSONResponse({"ok": True, "closed": closed})

@app.post("/api/resume")
async def api_resume():
    bot._paused = False
    log.info("RESUMED")
    return JSONResponse({"ok": True})

@app.post("/api/reset")
async def api_reset():
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.mid_price > 0:
            bot._close_position(sym, st.mid_price, now, "reset")
    bot.positions.clear()
    bot.trades.clear()
    bot._total_gross = bot._total_pnl_usdt = 0.0
    bot._wins = 0
    bot._cooldowns.clear()
    bot._paused = False
    if os.path.exists(TRADES_CSV):
        os.rename(TRADES_CSV, TRADES_CSV.replace(".csv", f"_reset_{now.strftime('%Y%m%d_%H%M%S')}.csv"))
    log.info("RESET — all cleared")
    return JSONResponse({"ok": True})


# ── Entry point ──────────────────────────────────────────────────────
async def main():
    bot.running = True
    bot._shutdown_event = asyncio.Event()
    bot._load_trades_csv()
    bot._load_positions()
    bot.started_at = datetime.now(timezone.utc)
    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Sniper v%s | Dashboard: http://0.0.0.0:%d", VERSION, WEB_PORT)
    log.info("Symbols: %s", ", ".join(TRADE_SYMBOLS))
    log.info("Sessions: Asia (0-8h) + Overnight (21-24h)")
    log.info("Signals: Funding (>%.0fbps, entry-%dmin) + Extreme (>%.0fbps/1h)",
             FUNDING_THRESH_BPS, FUNDING_ENTRY_BEFORE, EXTREME_THRESH_BPS)

    async def _watch_shutdown():
        await bot._shutdown_event.wait()
        bot.running = False
        server.should_exit = True

    await asyncio.gather(
        server.serve(), bot.ws_loop(), bot.signal_loop(), _watch_shutdown(),
    )


def entry():
    loop = asyncio.new_event_loop()
    def stop(s, f):
        log.info("Shutting down...")
        if bot._shutdown_event:
            loop.call_soon_threadsafe(bot._shutdown_event.set)
        else:
            bot.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        loop.run_until_complete(main())
    finally:
        bot._save_positions()
        n = len(bot.trades)
        np_ = len(bot.positions)
        if n or np_:
            balance = CAPITAL_USDT + bot._total_pnl_usdt
            log.info("SHUTDOWN: %d trades | %d positions saved | balance $%.2f", n, np_, balance)
        loop.close()

if __name__ == "__main__":
    entry()
