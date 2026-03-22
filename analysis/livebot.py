"""LiveBot — Multi-altcoin swing strategy.  (version in VERSION constant)

17 symbols: Tier A + B altcoins ranked by OI/volume ratio.
Strategy: OI divergence + funding + BTC lead-lag.
Sessions: Asia (0-8h) + US (14-21h). European excluded.
Dynamic leverage: 1x→3x based on signal confluence.
Max 1 position per symbol. Concurrent positions across symbols.

Run:       python3 -m analysis.livebot
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BOT] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("livebot")

VERSION = "5.4.0"

# ── Config ───────────────────────────────────────────────────────────
# BTC/ETH = reference (lead-lag, not traded)
# Tier A+B altcoins = traded
REFERENCE_SYMBOLS = ["btcusdt", "ethusdt"]
TRADE_SYMBOLS_LIST = [
    # Tier A (score > 0.8)
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "HYPEUSDT",
    "ZROUSDT", "AAVEUSDT", "LINKUSDT", "SUIUSDT",
    # Tier B (score 0.75-0.8)
    "AVAXUSDT", "XRPUSDT", "XMRUSDT", "XLMUSDT", "TONUSDT", "LTCUSDT",
]
ALL_SYMBOLS = REFERENCE_SYMBOLS + [s.lower() for s in TRADE_SYMBOLS_LIST]
TRADE_SYMBOLS_SET = set(TRADE_SYMBOLS_LIST)

# Build WS streams — split into chunks of max 50 symbols (150 streams)
# Binance limit: 200 streams per connection
def _build_ws_urls():
    all_streams = []
    for s in ALL_SYMBOLS:
        all_streams.extend([f"{s}@bookTicker", f"{s}@aggTrade", f"{s}@markPrice@1s"])
    # Split into chunks
    chunk_size = 150  # 50 symbols × 3 streams
    urls = []
    for i in range(0, len(all_streams), chunk_size):
        chunk = all_streams[i:i+chunk_size]
        urls.append("wss://fstream.binance.com/stream?streams=" + "/".join(chunk))
    return urls

WS_URLS = _build_ws_urls()

OI_POLL_INTERVAL = 60       # poll OI every 60s
OI_REST_URL = "https://fapi.binance.com/fapi/v1/openInterest"
LS_RATIO_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
TOP_LS_URL = "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
SIGNAL_INTERVAL = 10        # compute signals every 10s
HOLD_MINUTES = 120          # hold 2 hours
BNB_FEE_DISCOUNT = True     # True = 25% discount on fees (hold BNB)
COST_BPS = 3.0 if BNB_FEE_DISCOUNT else 4.0  # maker roundtrip
SLIPPAGE_BPS = 1.0          # simulated slippage (entry + exit)
MAX_SPREAD_BPS = 3.0        # skip entry if spread > 3 bps
OI_LOOKBACK = 18            # 18 ticks × 10s = 180s = 3 OI poll cycles
STREAK_DISABLE = 3          # disable symbol after 3 consecutive losses
STREAK_COOLDOWN_H = 12      # re-enable after 12 hours
CORRELATION_MAX = 3          # if >3 symbols aligned, reduce confidence
WEB_PORT = 8095

# ── Capital management (Kelly-optimal) ───────────────────────────────
CAPITAL_USDT = 1000.0       # capital total simulé
MAX_POSITIONS = 4           # max 4 positions simultanées
BASE_RISK_PCT = 20.0        # base margin % (scaled by score)
MAX_RISK_PCT = 30.0         # cap margin % for strongest signals
MAX_RISK_TOTAL_PCT = 90.0   # jamais plus de 90% du capital exposé

# Sessions: different thresholds (Asia = best edge, US = decent, overnight = like Asia)
TRADE_SESSIONS = {
    "asian":    (0, 8),
    "us":       (14, 21),
    "overnight": (21, 24),
}
SESSION_CONFIG = {
    "asian":    {"min_score": 0.25, "lev_mult": 1.0},   # aggressive
    "us":       {"min_score": 0.35, "lev_mult": 0.8},   # conservative
    "overnight": {"min_score": 0.25, "lev_mult": 1.0},  # like Asia
}
# European (8-14) excluded — signal inverts

# Leverage tiers based on signal count (4 signals now)
LEVERAGE_MAP = {
    1: 1.0,   # 1 signal → 1x
    2: 1.5,   # 2 signals → 1.5x
    3: 2.5,   # 3 signals → 2.5x
    4: 3.0,   # 4 signals → 3x (max)
}
MAX_LEVERAGE = 3.0
MIN_HOLD_MINUTES = 10          # don't check reversal before 10 min
COOLDOWN_MINUTES = 30          # block re-entry after exit on same symbol

# Volatility filter: block entries when realized vol > threshold
VOL_WINDOW = 18                # 18 × 10s ticks = 3 min rolling window
VOL_MAX_BPS = 15.0             # max 3-min realized vol (bps std) to enter

# Trailing stop: protect profits
TRAIL_ACTIVATE_BPS = 25.0      # activate trailing after +25 bps peak
TRAIL_DRAWDOWN_BPS = 15.0      # exit if drops 15 bps from peak

# Funding grab: boost in last 30 min before settlement
FUNDING_GRAB_MINUTES = 30      # aggressive mode window before settlement

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "livebot_trades.csv")
SIGNALS_CSV = os.path.join(OUTPUT_DIR, "livebot_signals.csv")
POSITIONS_FILE = os.path.join(OUTPUT_DIR, "livebot_positions.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "livebot.html")

# ── Data structures ──────────────────────────────────────────────────

@dataclass
class SymbolState:
    mid_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    spread_bps: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    # Trade accumulators (reset each signal tick)
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    trade_count: int = 0
    # Mark/index from markPrice
    mark_price: float = 0.0
    index_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_ts: int = 0  # ms timestamp
    basis_bps: float = 0.0
    # OI (from REST)
    open_interest: float = 0.0
    prev_open_interest: float = 0.0
    oi_updated_at: float = 0.0
    # Long/Short ratio (from REST)
    crowd_long_pct: float = 0.5
    top_long_pct: float = 0.5
    smart_divergence: float = 0.0  # top_long - crowd_long
    # Tick counter (for dashboard activity)
    msg_count: int = 0
    last_trade_price: float = 0.0
    last_trade_side: str = ""
    volume_1s: float = 0.0
    # Price history for mini-charts (1 per second, last 5 min)
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))
    tick_ts: float = 0.0
    # Rolling buffers
    mids: deque = field(default_factory=lambda: deque(maxlen=720))  # 720×10s = 2h
    oi_history: deque = field(default_factory=lambda: deque(maxlen=60))  # 60 entries
    price_history: deque = field(default_factory=lambda: deque(maxlen=60))
    basis_history: deque = field(default_factory=lambda: deque(maxlen=60))
    funding_history: deque = field(default_factory=lambda: deque(maxlen=60))
    smart_div_history: deque = field(default_factory=lambda: deque(maxlen=60))


@dataclass
class Position:
    symbol: str
    direction: int
    entry_price: float
    entry_time: datetime
    leverage: float
    signals_detail: dict
    size_usdt: float = 0.0       # notional position size
    margin_usdt: float = 0.0     # capital locked (size / leverage)
    peak_bps: float = 0.0        # best unrealized P&L (for trailing stop)
    funding_paid: float = 0.0    # cumulative funding paid/received (USDT)
    last_funding_ts: int = 0     # last settlement timestamp processed


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
    signals: dict
    gross_bps: float
    net_bps: float
    leveraged_net_bps: float
    pnl_usdt: float
    reason: str
    session: str


class LiveBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s.upper(): SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        self._cooldowns: dict[str, datetime] = {}  # sym → earliest re-entry time
        self._streak_losses: dict[str, int] = {}   # sym → consecutive losses
        self._streak_disabled: dict[str, datetime] = {}  # sym → re-enable time
        self._paused = False
        self.trades: list[Trade] = []
        self.signals: dict[str, dict] = {}
        self.running = False
        self._shutdown_event: asyncio.Event | None = None
        self.ws_connected = False
        self.ws_count = 0  # number of active WS connections
        self.started_at: datetime | None = None
        self._total_gross = 0.0
        self._total_pnl_usdt = 0.0  # running accumulator (dollars)
        self._total_leveraged = 0.0
        self._wins = 0
        # Msg rate tracking (reset every signal tick)
        self._msg_window_count = 0
        self._msg_window_start = 0.0
        self._msg_rate = 0.0

    def _load_trades_csv(self):
        """Reload trade history from CSV to restore P&L state after restart."""
        if not os.path.exists(TRADES_CSV):
            return
        try:
            with open(TRADES_CSV, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trade = Trade(
                        symbol=row["symbol"],
                        direction=row["direction"],
                        entry_time=row["entry_time"],
                        exit_time=row["exit_time"],
                        entry_price=float(row["entry_price"]),
                        exit_price=float(row["exit_price"]),
                        hold_min=float(row["hold_min"]),
                        leverage=float(row["leverage"]),
                        size_usdt=float(row["size_usdt"]),
                        signals={},
                        gross_bps=float(row["gross_bps"]),
                        net_bps=float(row["net_bps"]),
                        leveraged_net_bps=float(row["leveraged_net_bps"]),
                        pnl_usdt=float(row["pnl_usdt"]),
                        reason=row["reason"],
                        session=row.get("session", "?"),
                    )
                    self.trades.append(trade)
                    self._total_gross += trade.gross_bps
                    self._total_pnl_usdt += trade.pnl_usdt
                    self._total_leveraged += trade.leveraged_net_bps
                    if trade.pnl_usdt > 0:
                        self._wins += 1
            n = len(self.trades)
            if n > 0:
                balance = CAPITAL_USDT + self._total_pnl_usdt
                log.info("Restored %d trades from CSV | balance $%.2f | win %.0f%%",
                         n, balance, self._wins / n * 100)
        except Exception:
            log.exception("Failed to load trades CSV")

    def _save_positions(self):
        """Save open positions to JSON for restart persistence."""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = []
        for sym, pos in self.positions.items():
            data.append({
                "symbol": pos.symbol, "direction": pos.direction,
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "leverage": pos.leverage, "signals_detail": pos.signals_detail,
                "size_usdt": pos.size_usdt, "margin_usdt": pos.margin_usdt,
                "peak_bps": pos.peak_bps, "funding_paid": pos.funding_paid,
                "last_funding_ts": pos.last_funding_ts,
            })
        with open(POSITIONS_FILE, "wb") as f:
            f.write(orjson.dumps(data))
        if data:
            log.info("Saved %d open positions to disk", len(data))

    def _load_positions(self):
        """Restore open positions from JSON after restart."""
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
                    leverage=p["leverage"], signals_detail=p.get("signals_detail", {}),
                    size_usdt=p["size_usdt"], margin_usdt=p["margin_usdt"],
                    peak_bps=p.get("peak_bps", 0.0),
                    funding_paid=p.get("funding_paid", 0.0),
                    last_funding_ts=p.get("last_funding_ts", 0),
                )
                self.positions[pos.symbol] = pos
            if self.positions:
                log.info("Restored %d open positions from disk", len(self.positions))
            os.remove(POSITIONS_FILE)  # clean up after load
        except Exception:
            log.exception("Failed to load positions")

    def _current_session(self) -> str | None:
        h = datetime.now(timezone.utc).hour
        for name, (start, end) in TRADE_SESSIONS.items():
            if start <= h < end:
                return name
        return None

    # ── WebSocket ────────────────────────────────────────────────
    async def ws_loop(self):
        """Launch one task per WS connection (for >50 symbols)."""
        tasks = [self._ws_connect(url, i) for i, url in enumerate(WS_URLS)]
        await asyncio.gather(*tasks)

    async def _ws_connect(self, url: str, idx: int):
        n_streams = url.count("@")
        backoff = 3
        while self.running:
            try:
                log.info("WS[%d] connecting (%d streams)...", idx, n_streams)
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws_count += 1
                    self.ws_connected = self.ws_count > 0
                    backoff = 3  # reset on success
                    connect_time = time.time()
                    log.info("WS[%d] connected (total %d)", idx, self.ws_count)
                    async for raw in ws:
                        if not self.running:
                            break
                        # Proactive rotation before Binance 24h limit
                        if time.time() - connect_time > 82800:  # 23h
                            log.info("WS[%d] proactive rotation (23h)", idx)
                            break
                        self._on_ws_message(raw)
            except Exception as e:
                log.warning("WS[%d] error: %s — reconnecting in %ds...", idx, e, backoff)
            self.ws_count = max(0, self.ws_count - 1)
            self.ws_connected = self.ws_count > 0
            if self.running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)  # exponential backoff, max 60s

    def _on_ws_message(self, raw):
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
        # Sample price every ~1s for mini-chart
        now = time.time()
        if now - st.tick_ts >= 1.0 and st.mid_price > 0:
            st.price_ticks.append((now, st.mid_price))
            st.tick_ts = now

    def _on_trade(self, d):
        sym = d.get("s", "")
        st = self.states.get(sym)
        if not st:
            return
        price = float(d.get("p", 0))
        notional = price * float(d.get("q", 0))
        is_sell = d.get("m", False)
        if is_sell:
            st.sell_notional += notional
        else:
            st.buy_notional += notional
        st.trade_count += 1
        st.msg_count += 1
        st.last_trade_price = price
        st.last_trade_side = "SELL" if is_sell else "BUY"
        st.volume_1s += notional

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

    # ── OI + L/S Ratio Polling ──────────────────────────────────
    async def oi_loop(self):
        async with aiohttp.ClientSession() as session:
            while self.running:
                # Poll OI for all symbols
                for sym in ALL_SYMBOLS:
                    if not self.running:
                        break
                    try:
                        url = f"{OI_REST_URL}?symbol={sym.upper()}"
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                st = self.states[sym.upper()]
                                st.prev_open_interest = st.open_interest
                                st.open_interest = float(data.get("openInterest", 0))
                                st.oi_updated_at = time.time()
                            elif resp.status == 429:
                                log.warning("OI rate limited — pausing 30s")
                                await asyncio.sleep(30)
                            elif resp.status == 418:
                                log.error("OI IP banned — pausing 120s")
                                await asyncio.sleep(120)
                    except Exception as e:
                        log.warning("OI poll error %s: %s", sym.upper(), e)
                    await asyncio.sleep(0.15)

                # Poll L/S ratios (global + top) for trade symbols
                for sym in TRADE_SYMBOLS_LIST:
                    if not self.running:
                        break
                    st = self.states.get(sym)
                    if not st:
                        continue
                    try:
                        # Global L/S ratio
                        async with session.get(LS_RATIO_URL, params={"symbol": sym, "period": "5m", "limit": 1}) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data:
                                    st.crowd_long_pct = float(data[-1].get("longAccount", 0.5))
                        await asyncio.sleep(0.15)
                        # Top trader L/S ratio
                        async with session.get(TOP_LS_URL, params={"symbol": sym, "period": "5m", "limit": 1}) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data:
                                    st.top_long_pct = float(data[-1].get("longAccount", 0.5))
                        # Smart divergence
                        st.smart_divergence = st.top_long_pct - st.crowd_long_pct
                    except Exception as e:
                        log.warning("L/S poll error %s: %s", sym, e)
                    await asyncio.sleep(0.15)

                await asyncio.sleep(max(1, OI_POLL_INTERVAL - len(ALL_SYMBOLS) * 0.3))

    # ── Signal computation ───────────────────────────────────────
    async def signal_loop(self):
        self._signal_log_counter = 0
        self._msg_window_start = time.time()
        while self.running:
            await asyncio.sleep(SIGNAL_INTERVAL)
            if not self.ws_connected:
                continue
            try:
                # Update msg rate
                now_t = time.time()
                elapsed = now_t - self._msg_window_start
                if elapsed > 0:
                    self._msg_rate = self._msg_window_count / elapsed
                self._msg_window_count = 0
                self._msg_window_start = now_t
                self._compute_signals()
                self._trading_logic()
                # Log signals to CSV every 60s (6 ticks × 10s)
                self._signal_log_counter += 1
                if self._signal_log_counter % 6 == 0:
                    self._write_signals_csv()
            except Exception:
                log.exception("Signal error")

    def _compute_signals(self):
        now = datetime.now(timezone.utc)
        session = self._current_session()

        # BTC return for lead-lag
        btc = self.states.get("BTCUSDT")
        btc_ret = 0.0
        if btc and len(btc.mids) >= 2:
            btc_ret = (btc.mids[-1] / btc.mids[-2] - 1) * 1e4

        for sym, st in self.states.items():
            if st.mid_price == 0:
                continue

            # Sample into buffers
            st.mids.append(st.mid_price)
            st.price_history.append(st.mid_price)
            st.basis_history.append(st.basis_bps)
            st.funding_history.append(st.funding_rate)
            if st.open_interest > 0:
                st.oi_history.append(st.open_interest)

            # Reset trade accumulators
            buy_n = st.buy_notional
            sell_n = st.sell_notional
            st.buy_notional = 0.0
            st.sell_notional = 0.0
            st.trade_count = 0
            st.volume_1s = 0.0

            if len(st.mids) < 6:
                continue

            # ── Signal 1: OI Divergence ──────────────────────
            oi_signal = 0.0
            oi_detail = "no_data"
            if len(st.oi_history) >= OI_LOOKBACK and len(st.price_history) >= OI_LOOKBACK:
                oi_now = st.oi_history[-1]
                oi_prev = st.oi_history[-OI_LOOKBACK]  # 180s = 3 OI poll cycles
                oi_change = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0

                price_now = st.price_history[-1]
                price_prev = st.price_history[-OI_LOOKBACK]
                price_change = (price_now / price_prev - 1) * 1e4

                # Graduated strength based on divergence magnitude
                strength = float(np.clip(
                    (min(abs(price_change), 20) / 20 + min(abs(oi_change), 0.3) / 0.3) / 2,
                    0.3, 1.0
                ))

                # Weak long: price up but OI down → fade (short)
                if price_change > 3 and oi_change < -0.03:
                    oi_signal = -strength
                    oi_detail = f"weak_long(p={price_change:+.0f},oi={oi_change:+.2f}%)"
                # Weak short: price down but OI up → fade (long)
                elif price_change < -3 and oi_change > 0.03:
                    oi_signal = +strength
                    oi_detail = f"weak_short(p={price_change:+.0f},oi={oi_change:+.2f}%)"
                else:
                    oi_detail = f"neutral(p={price_change:+.0f},oi={oi_change:+.2f}%)"

            # ── Signal 2: Funding proximity ──────────────────
            funding_signal = 0.0
            funding_detail = "no_settle"
            if st.next_funding_ts > 0:
                next_settle = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
                mins_to = (next_settle - now).total_seconds() / 60
                rate = st.funding_rate

                if 0 < mins_to < 120:
                    # High funding → longs will close → short
                    if rate > 0.0003:
                        funding_signal = -1.0 * min(1.0, (120 - mins_to) / 60)
                        funding_detail = f"high_fund({rate*1e4:.1f}bps,{mins_to:.0f}min)"
                    elif rate < -0.0003:
                        funding_signal = +1.0 * min(1.0, (120 - mins_to) / 60)
                        funding_detail = f"low_fund({rate*1e4:.1f}bps,{mins_to:.0f}min)"
                    else:
                        funding_detail = f"neutral({rate*1e4:.1f}bps,{mins_to:.0f}min)"
                else:
                    funding_detail = f"far({mins_to:.0f}min)"

            # ── Signal 3: BTC lead-lag (non-BTC only) ────────
            leadlag_signal = 0.0
            if sym != "BTCUSDT" and abs(btc_ret) > 2:
                leadlag_signal = float(np.clip(btc_ret / 10, -1, 1))

            # ── Signal 4: Smart money divergence ────────────
            smart_signal = 0.0
            smart_detail = "no_data"
            if sym in TRADE_SYMBOLS_SET:
                st.smart_div_history.append(st.smart_divergence)
            if len(st.smart_div_history) >= 30 and st.crowd_long_pct != 0.5:
                div_arr = np.array(st.smart_div_history)
                div_std = float(np.std(div_arr))
                if div_std > 0:
                    smart_z = float((div_arr[-1] - np.mean(div_arr)) / div_std)
                    smart_signal = float(np.clip(smart_z / 2, -1, 1))
                smart_detail = f"top={st.top_long_pct:.0%} crowd={st.crowd_long_pct:.0%} div={st.smart_divergence:+.3f}"

            # ── Composite score (4 signals) ─────────────────
            composite = (
                oi_signal * 0.35 +
                funding_signal * 0.20 +
                leadlag_signal * 0.15 +
                smart_signal * 0.30
            )

            # Count confirming signals
            active_signals = sum([
                abs(oi_signal) > 0.5,
                abs(funding_signal) > 0.3,
                abs(leadlag_signal) > 0.3,
                abs(smart_signal) > 0.3,
            ])
            leverage = LEVERAGE_MAP.get(min(active_signals, 4), 1.0)
            leverage = min(leverage, MAX_LEVERAGE)

            self.signals[sym] = {
                "composite": round(composite, 3),
                "oi_signal": round(oi_signal, 2),
                "oi_detail": oi_detail,
                "funding_signal": round(funding_signal, 2),
                "funding_detail": funding_detail,
                "leadlag_signal": round(leadlag_signal, 2),
                "smart_signal": round(smart_signal, 2),
                "smart_detail": smart_detail,
                "btc_ret": round(btc_ret, 1),
                "active_signals": active_signals,
                "leverage": leverage,
                "mid": st.mid_price,
                "spread_bps": round(st.spread_bps, 2),
                "basis_bps": round(st.basis_bps, 2),
                "funding_rate_bps": round(st.funding_rate * 1e4, 2),
                "oi": st.open_interest,
                "session": session or "excluded",
                "tradeable": session is not None and sym in TRADE_SYMBOLS_LIST and not self._paused,
            }

    def _margin_used(self) -> float:
        """Total margin currently locked in positions."""
        return sum(p.margin_usdt for p in self.positions.values())

    def _available_capital(self) -> float:
        """How much capital can still be allocated."""
        pnl = self._total_pnl_usdt
        current_capital = CAPITAL_USDT + pnl
        max_exposure = current_capital * MAX_RISK_TOTAL_PCT / 100
        return max(0, max_exposure - self._margin_used())

    def _trading_logic(self):
        now = datetime.now(timezone.utc)
        session = self._current_session()

        # ── Step 1: Check exits for existing positions ───────
        for sym in list(self.positions.keys()):
            sig = self.signals.get(sym)
            if not sig:
                continue
            mid = sig["mid"]
            if mid == 0:
                continue
            pos = self.positions[sym]
            comp = sig["composite"]
            held = (now - pos.entry_time).total_seconds() / 60

            # Simulate funding payment at settlement
            st = self.states.get(sym)
            if st and st.next_funding_ts > 0 and st.next_funding_ts != pos.last_funding_ts:
                settle = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
                # If settlement just passed (within last 60s), we owe/receive funding
                secs_since = (now - settle).total_seconds()
                if 0 < secs_since < 60:
                    # Long pays funding when rate > 0, short pays when rate < 0
                    funding_cost = pos.size_usdt * st.funding_rate * pos.direction
                    pos.funding_paid += funding_cost
                    pos.last_funding_ts = st.next_funding_ts
                    log.info("FUNDING %s %s: %+.4f$ (rate=%.1f bps, size=$%.0f)",
                             sym, "LONG" if pos.direction == 1 else "SHORT",
                             -funding_cost, st.funding_rate * 1e4, pos.size_usdt)

            unrealized = pos.direction * (mid / pos.entry_price - 1) * 1e4
            leveraged_unreal = unrealized * pos.leverage

            # Track peak on raw bps (consistent across leverage tiers)
            if unrealized > pos.peak_bps:
                pos.peak_bps = unrealized

            exit_reason = None
            if held >= HOLD_MINUTES:
                exit_reason = "timeout"
            elif held >= MIN_HOLD_MINUTES and (
                (pos.direction == 1 and comp < -0.3) or (pos.direction == -1 and comp > 0.3)
            ):
                exit_reason = "reversal"
            elif leveraged_unreal < -100:  # stop loss on leveraged bps
                exit_reason = "stop_loss"
            elif held >= MIN_HOLD_MINUTES and (
                pos.peak_bps >= TRAIL_ACTIVATE_BPS and
                unrealized < pos.peak_bps - TRAIL_DRAWDOWN_BPS
            ):
                exit_reason = "trail_stop"

            if exit_reason:
                net_pnl = self._close_position(sym, mid, now, exit_reason)
                self._cooldowns[sym] = now + timedelta(minutes=COOLDOWN_MINUTES)
                # Track win/loss streaks per symbol
                if net_pnl < 0:
                    self._streak_losses[sym] = self._streak_losses.get(sym, 0) + 1
                    if self._streak_losses[sym] >= STREAK_DISABLE:
                        self._streak_disabled[sym] = now + timedelta(hours=STREAK_COOLDOWN_H)
                        log.info("STREAK DISABLE %s: %d consecutive losses → disabled %dh",
                                 sym, self._streak_losses[sym], STREAK_COOLDOWN_H)
                else:
                    self._streak_losses[sym] = 0  # reset on win

        # ── Step 2: Collect & rank new entry candidates ──────
        if session is None or self._paused:
            return
        sess_cfg = SESSION_CONFIG.get(session, {"min_score": 0.35, "lev_mult": 0.8})
        min_score = sess_cfg["min_score"]

        # Funding grab: lower threshold in last 30 min before settlement
        funding_grab = False
        for st in self.states.values():
            if st.next_funding_ts > 0:
                mins_to = max(0, (datetime.fromtimestamp(
                    st.next_funding_ts / 1000, tz=timezone.utc) - now).total_seconds() / 60)
                if mins_to <= FUNDING_GRAB_MINUTES:
                    funding_grab = True
                break  # all symbols share same settlement time
        if funding_grab:
            min_score *= 0.8  # 20% more aggressive near settlement

        # Cross-symbol correlation: count aligned OI signals
        oi_long = oi_short = 0
        for sym2, sig2 in self.signals.items():
            if sym2 not in TRADE_SYMBOLS_SET:
                continue
            oi2 = sig2.get("oi_signal", 0)
            if oi2 > 0.3:
                oi_long += 1
            elif oi2 < -0.3:
                oi_short += 1
        macro_move = max(oi_long, oi_short) > CORRELATION_MAX

        candidates = []
        for sym in TRADE_SYMBOLS_LIST:
            if sym in self.positions:
                continue  # already in position
            # Anti-whipsaw: respect cooldown after exit
            if sym in self._cooldowns and now < self._cooldowns[sym]:
                continue
            # Streak: disable symbol after consecutive losses
            if sym in self._streak_disabled and now < self._streak_disabled[sym]:
                continue
            sig = self.signals.get(sym)
            if not sig:
                continue
            comp = sig["composite"]
            if abs(comp) < min_score:
                continue
            if sig["active_signals"] < 1:
                continue
            # Require OI divergence as primary signal
            if abs(sig["oi_signal"]) < 0.1:
                continue
            # Spread filter: skip if effective cost too high
            if sig["spread_bps"] > MAX_SPREAD_BPS:
                continue
            # Cross-symbol: reduce score if macro move detected
            effective_score = abs(comp)
            if macro_move:
                effective_score *= 0.6  # dampen confidence during correlated moves
                if effective_score < min_score:
                    continue
            # Volatility filter: skip if symbol too volatile
            st = self.states.get(sym)
            if st and len(st.mids) >= VOL_WINDOW:
                recent = list(st.mids)[-VOL_WINDOW:]
                returns = [(recent[i] / recent[i-1] - 1) * 1e4 for i in range(1, len(recent))]
                vol = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0
                if vol > VOL_MAX_BPS:
                    log.info("VOL SKIP %s: %.1f bps > %.1f limit", sym, vol, VOL_MAX_BPS)
                    continue
            candidates.append((sym, sig, effective_score))

        if not candidates:
            return

        # Sort by absolute score: strongest signal first
        candidates.sort(key=lambda x: x[2], reverse=True)

        # ── Step 3: Allocate capital to best candidates ──────
        slots_available = MAX_POSITIONS - len(self.positions)
        if slots_available <= 0:
            return

        pnl = self._total_pnl_usdt
        current_capital = CAPITAL_USDT + pnl
        remaining_capital = self._available_capital()
        lev_mult = sess_cfg["lev_mult"]

        for rank, (sym, sig, score) in enumerate(candidates[:slots_available], 1):
            mid = sig["mid"]
            if mid == 0:
                continue

            # Skip if price data is stale (no update in 30s)
            st = self.states.get(sym)
            if st and (time.time() - st.tick_ts) > 30:
                continue

            comp = sig["composite"]
            direction = 1 if comp > 0 else -1
            leverage = min(sig["leverage"] * lev_mult, MAX_LEVERAGE)

            # Proportional sizing: scale margin by score strength
            score_factor = min(score / 0.6, 1.0)  # score_factor: 0.3→0.5, 0.6+→1.0
            risk_pct = BASE_RISK_PCT + (MAX_RISK_PCT - BASE_RISK_PCT) * score_factor
            margin = min(current_capital * risk_pct / 100, remaining_capital)

            if margin < current_capital * BASE_RISK_PCT / 100 * 0.5:
                break  # not enough capital for a meaningful position
            size_usdt = margin * leverage
            remaining_capital -= margin  # decrement for next iteration

            pos = Position(
                symbol=sym, direction=direction,
                entry_price=mid, entry_time=now,
                leverage=leverage,
                size_usdt=size_usdt,
                margin_usdt=margin,
                signals_detail={
                    "oi": sig["oi_detail"],
                    "funding": sig["funding_detail"],
                    "btc_ret": sig["btc_ret"],
                    "composite": comp,
                    "rank": rank,
                },
            )
            self.positions[sym] = pos
            side = "LONG" if direction == 1 else "SHORT"
            log.info(
                "→ ENTER %s %s @ %.4f | $%.0f (%.0fx) | score=%+.2f [#%d/%d] | "
                "oi=%s | fund=%s | %d/%d slots",
                side, sym, mid, size_usdt, leverage, comp,
                rank, len(candidates),
                sig["oi_detail"][:25], sig["funding_detail"][:20],
                len(self.positions), MAX_POSITIONS,
            )

    def _close_position(self, sym: str, exit_price: float, now: datetime, reason: str) -> float:
        pos = self.positions.pop(sym)
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
        hold_min = (now - pos.entry_time).total_seconds() / 60

        # P&L in dollars: gross - fees - slippage - funding
        pnl_usdt = pos.size_usdt * (pos.direction * (exit_price / pos.entry_price - 1))
        fee_usdt = pos.size_usdt * COST_BPS / 1e4
        slip_usdt = pos.size_usdt * SLIPPAGE_BPS / 1e4
        funding_usdt = pos.funding_paid  # negative = we paid, positive = we received
        net_pnl_usdt = pnl_usdt - fee_usdt - slip_usdt - funding_usdt

        # Bps metrics (on margin, leverage-aware)
        total_cost_bps = COST_BPS + SLIPPAGE_BPS
        leveraged_gross_bps = gross_bps * pos.leverage
        leveraged_net_bps = leveraged_gross_bps - total_cost_bps

        self._total_gross += gross_bps
        self._total_pnl_usdt += net_pnl_usdt  # running accumulator (dollars)
        self._total_leveraged += leveraged_net_bps
        if net_pnl_usdt > 0:  # win = net positive after fees
            self._wins += 1

        session = self._current_session() or "?"

        trade = Trade(
            symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
            entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
            entry_price=pos.entry_price, exit_price=exit_price,
            hold_min=round(hold_min, 1), leverage=pos.leverage,
            size_usdt=round(pos.size_usdt, 2),
            signals=pos.signals_detail,
            gross_bps=round(gross_bps, 2),
            net_bps=round(gross_bps - COST_BPS, 2),  # unleveraged net (for chart)
            leveraged_net_bps=round(leveraged_net_bps, 2),
            pnl_usdt=round(net_pnl_usdt, 2),
            reason=reason, session=session,
        )
        self.trades.append(trade)
        self._write_csv(trade)

        n = len(self.trades)
        wr = self._wins / n * 100 if n > 0 else 0
        balance = CAPITAL_USDT + self._total_pnl_usdt
        arrow = "+" if net_pnl_usdt > 0 else "-"
        fund_str = f" fund=${funding_usdt:+.3f}" if abs(funding_usdt) > 0.001 else ""
        log.info(
            "%s EXIT %s %s | %.0fmin | $%.0f (%.0fx) | gross %+.1f bps | "
            "cost $%.3f (fee+slip)%s | %+.2f$ | balance $%.2f (#%d, win %.0f%%)",
            arrow, trade.direction, sym, hold_min,
            pos.size_usdt, pos.leverage, gross_bps,
            fee_usdt + slip_usdt, fund_str, net_pnl_usdt, balance, n, wr,
        )
        return net_pnl_usdt

    def _write_csv(self, t: Trade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        with open(TRADES_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["symbol", "direction", "entry_time", "exit_time",
                           "entry_price", "exit_price", "hold_min", "leverage",
                           "size_usdt", "gross_bps", "net_bps", "leveraged_net_bps",
                           "pnl_usdt", "reason", "session", "signals"])
            w.writerow([t.symbol, t.direction, t.entry_time, t.exit_time,
                       t.entry_price, t.exit_price, t.hold_min, t.leverage,
                       t.size_usdt, t.gross_bps, t.net_bps, t.leveraged_net_bps,
                       t.pnl_usdt, t.reason, t.session, str(t.signals)])

    def _write_signals_csv(self):
        """Log all signal values every 60s for post-analysis."""
        now = datetime.now(timezone.utc).isoformat()
        session = self._current_session() or "excluded"
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(SIGNALS_CSV)
        try:
            with open(SIGNALS_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if header:
                    w.writerow(["timestamp", "session", "symbol", "mid_price", "oi",
                               "oi_signal", "funding_signal", "leadlag_signal",
                               "smart_signal",
                               "composite", "active_signals", "leverage",
                               "spread_bps", "basis_bps", "funding_bps",
                               "tradeable", "in_position", "oi_detail", "smart_detail"])
                for sym, sig in self.signals.items():
                    w.writerow([
                        now, session, sym,
                        sig.get("mid", 0),
                        sig.get("oi", 0),
                        sig.get("oi_signal", 0),
                        sig.get("funding_signal", 0),
                        sig.get("leadlag_signal", 0),
                        sig.get("smart_signal", 0),
                        sig.get("composite", 0),
                        sig.get("active_signals", 0),
                        sig.get("leverage", 1),
                        sig.get("spread_bps", 0),
                        sig.get("basis_bps", 0),
                        sig.get("funding_rate_bps", 0),
                        sig.get("tradeable", False),
                        sym in self.positions,
                        sig.get("oi_detail", ""),
                        sig.get("smart_detail", ""),
                    ])
        except Exception:
            log.exception("Signal CSV write error")

    # ── API ──────────────────────────────────────────────────────
    def get_state(self) -> dict:
        n = len(self.trades)
        positions = []
        for sym, pos in self.positions.items():
            mid = self.signals.get(sym, {}).get("mid", pos.entry_price)
            unrealized = pos.direction * (mid / pos.entry_price - 1) * 1e4
            leveraged_bps = unrealized * pos.leverage
            pnl_usdt = pos.size_usdt * pos.direction * (mid / pos.entry_price - 1)
            positions.append({
                "symbol": sym,
                "direction": "LONG" if pos.direction == 1 else "SHORT",
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "size_usdt": round(pos.size_usdt, 2),
                "unrealized_bps": round(leveraged_bps, 2),  # leveraged bps on margin
                "pnl_usdt": round(pnl_usdt, 2),
                "leverage": pos.leverage,
                "hold_min": round((datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60, 1),
                "peak_bps": round(pos.peak_bps, 2),
                "trail_active": pos.peak_bps >= TRAIL_ACTIVATE_BPS,
                "funding_paid": round(pos.funding_paid, 4),
                "signals": pos.signals_detail,
            })
        now = datetime.now(timezone.utc)
        session = self._current_session()
        # Next funding (from first symbol that has data)
        next_fund_min = None
        avg_funding_bps = 0.0
        fund_count = 0
        for st in self.states.values():
            if st.next_funding_ts > 0:
                nf = datetime.fromtimestamp(st.next_funding_ts / 1000, tz=timezone.utc)
                mins = max(0, (nf - now).total_seconds() / 60)
                if next_fund_min is None or mins < next_fund_min:
                    next_fund_min = round(mins, 0)
            if st.funding_rate != 0:
                avg_funding_bps += st.funding_rate * 1e4
                fund_count += 1
        avg_funding_bps = round(avg_funding_bps / fund_count, 2) if fund_count > 0 else 0

        # Capital tracking
        n_positions = len(self.positions)
        total_pnl_usdt = self._total_pnl_usdt
        balance = CAPITAL_USDT + total_pnl_usdt
        margin_used = self._margin_used()
        # Unrealized P&L
        unrealized_usdt = 0.0
        for sym, pos in self.positions.items():
            mid = self.signals.get(sym, {}).get("mid", pos.entry_price)
            unrealized_usdt += pos.size_usdt * pos.direction * (mid / pos.entry_price - 1)

        return {
            "version": VERSION,
            "paused": self._paused,
            "running": self.running, "ws_connected": self.ws_connected,
            "ws_connections": self.ws_count,
            "n_symbols": len(TRADE_SYMBOLS_LIST),
            "n_positions": n_positions,
            "max_positions": MAX_POSITIONS,
            "capital_initial": CAPITAL_USDT,
            "balance": round(balance, 2),
            "total_pnl_usdt": round(total_pnl_usdt, 2),
            "unrealized_usdt": round(unrealized_usdt, 2),
            "margin_used": round(margin_used, 2),
            "available": round(self._available_capital(), 2),
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "session": session or "excluded",
            "session_tradeable": session is not None,
            "next_funding_min": next_fund_min,
            "avg_funding_bps": avg_funding_bps,
            "total_trades": n,
            "gross_pnl_bps": round(self._total_gross, 2),
            "net_pnl_usdt": round(self._total_pnl_usdt, 2),
            "leveraged_pnl_bps": round(self._total_leveraged, 2),
            "win_rate": round(self._wins / n, 3) if n > 0 else 0,
            "avg_leverage": round(np.mean([t.leverage for t in self.trades]), 1) if self.trades else 0,
            "positions": positions,
            "signals": self.signals,
        }

    def get_trades(self, limit=50) -> list:
        return [t.__dict__ for t in self.trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        cum_gross = cum_net = cum_lev = cum_usdt = 0.0
        pts = []
        for t in self.trades:
            cum_gross += t.gross_bps
            cum_net += t.net_bps
            cum_lev += t.leveraged_net_bps
            cum_usdt += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_gross": round(cum_gross, 2),
                        "cum_net": round(cum_net, 2), "cum_lev": round(cum_lev, 2),
                        "cum_usdt": round(cum_usdt, 2),
                        "balance": round(CAPITAL_USDT + cum_usdt, 2)})
        return pts

    def get_ticker(self) -> dict:
        """Live market data for dashboard activity feed."""
        now = time.time()
        tickers = {}
        for sym, st in self.states.items():
            if st.mid_price == 0:
                continue
            # Price change over last 60s from tick history
            change_1m = 0.0
            change_5m = 0.0
            if len(st.price_ticks) >= 2:
                current = st.price_ticks[-1][1]
                # Find price ~60s ago
                for ts, p in reversed(st.price_ticks):
                    if now - ts >= 60:
                        change_1m = (current / p - 1) * 1e4
                        break
                for ts, p in reversed(st.price_ticks):
                    if now - ts >= 300:
                        change_5m = (current / p - 1) * 1e4
                        break

            # Mini-chart data (last 5 min, 1 point/sec)
            chart = []
            for ts, p in st.price_ticks:
                chart.append({"t": round(ts), "p": p})

            imb = st.bid_qty / (st.bid_qty + st.ask_qty) if (st.bid_qty + st.ask_qty) > 0 else 0.5

            tickers[sym] = {
                "price": st.mid_price,
                "spread_bps": round(st.spread_bps, 2),
                "change_1m_bps": round(change_1m, 1),
                "change_5m_bps": round(change_5m, 1),
                "last_trade": st.last_trade_price,
                "last_side": st.last_trade_side,
                "imbalance": round(imb, 3),
                "basis_bps": round(st.basis_bps, 2),
                "funding_bps": round(st.funding_rate * 1e4, 2),
                "volume_1s": round(st.volume_1s, 0),
                "trades_count": st.trade_count,
                "chart": chart[-60:],  # last 60 points for sparkline
            }
        return {"tickers": tickers, "total_msgs_sec": round(self._msg_rate)}


# ── FastAPI ──────────────────────────────────────────────────────────
bot = LiveBot()
app = FastAPI()

_html_cache = None

@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        _html_cache = Path(HTML_PATH).read_text().replace("{{VERSION}}", VERSION)
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
    """Close all positions and pause trading."""
    now = datetime.now(timezone.utc)
    closed = 0
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.mid_price > 0:
            bot._close_position(sym, st.mid_price, now, "manual_stop")
            closed += 1
    bot._paused = True
    log.info("PAUSED by user — %d positions closed", closed)
    return JSONResponse({"ok": True, "closed": closed})

@app.post("/api/resume")
async def api_resume():
    """Resume trading."""
    bot._paused = False
    log.info("RESUMED by user")
    return JSONResponse({"ok": True})

@app.post("/api/reset")
async def api_reset():
    """Close positions, reset all counters, clear CSV."""
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.mid_price > 0:
            bot._close_position(sym, st.mid_price, now, "reset")
    bot.positions.clear()
    bot.trades.clear()
    bot._total_gross = 0.0
    bot._total_pnl_usdt = 0.0
    bot._total_leveraged = 0.0
    bot._wins = 0
    bot._cooldowns.clear()
    bot._streak_losses.clear()
    bot._streak_disabled.clear()
    bot._paused = False
    # Clear CSV
    if os.path.exists(TRADES_CSV):
        os.rename(TRADES_CSV, TRADES_CSV.replace(".csv", f"_reset_{now.strftime('%Y%m%d_%H%M%S')}.csv"))
    if os.path.exists(SIGNALS_CSV):
        os.rename(SIGNALS_CSV, SIGNALS_CSV.replace(".csv", f"_reset_{now.strftime('%Y%m%d_%H%M%S')}.csv"))
    log.info("RESET by user — all counters cleared")
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
    log.info("LiveBot v%s — Multi-altcoin swing | Dashboard: http://0.0.0.0:%d", VERSION, WEB_PORT)
    log.info("Trading %d symbols: %s", len(TRADE_SYMBOLS_LIST), ", ".join(TRADE_SYMBOLS_LIST))
    log.info("Reference: BTC, ETH | Sessions: Asia+US | Hold: %dmin | Cost: %.0fbps",
             HOLD_MINUTES, COST_BPS)
    log.info("WS connections: %d | Leverage: 1x→1.5x→2.5x→3x", len(WS_URLS))

    async def _watch_shutdown():
        await bot._shutdown_event.wait()
        bot.running = False
        server.should_exit = True

    await asyncio.gather(
        server.serve(), bot.ws_loop(), bot.oi_loop(),
        bot.signal_loop(), _watch_shutdown(),
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
        # Save open positions for restart (don't close them)
        bot._save_positions()
        n = len(bot.trades)
        np = len(bot.positions)
        if n or np:
            balance = CAPITAL_USDT + bot._total_pnl_usdt
            log.info("SHUTDOWN: %d trades | %d open positions saved | balance $%.2f",
                     n, np, balance)
        loop.close()

if __name__ == "__main__":
    entry()
