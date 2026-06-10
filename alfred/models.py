"""Data structures — SymbolState (shared market data), Position, Trade (per-bot).

Verbatim from analysis/bot/models.py (v12.17.3). SymbolState belongs to the
MarketDataMaster; Position/Trade belong to each BotInstance.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SymbolState:
    price: float = 0.0
    updated_at: float = 0.0
    # 1500 candles ≈ 250 days of 4h history (macro modulator needs 30d+180d on BTC).
    candles_4h: deque = field(default_factory=lambda: deque(maxlen=1500))
    last_candle_ts: int = 0
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))  # ~5h @ 60s
    oi: float = 0.0
    funding: float = 0.0
    premium: float = 0.0
    oi_history: deque = field(default_factory=lambda: deque(maxlen=1500))  # 25h @ 60s
    # impactPxs: price for $1M notional fill per side (book-depth proxy).
    impact_bid: float = 0.0
    impact_ask: float = 0.0


@dataclass
class Position:
    symbol: str
    direction: int           # 1=LONG, -1=SHORT
    strategy: str            # S1, S5, S8, S9, S10
    entry_price: float
    entry_time: datetime
    size_usdt: float
    signal_info: str
    target_exit: datetime
    mae_bps: float = 0.0
    mfe_bps: float = 0.0
    mfe_at_h: float = 0.0   # hours_held when mfe_bps was last updated (traj_cut)
    trajectory: list = field(default_factory=list)  # [(hours, unrealized_bps)] ≤200
    stop_bps: float = 0.0   # per-position stop (S9 adaptive); 0 = default
    entry_oi_delta: float = 0.0
    entry_crowding: int = 0
    entry_confluence: int = 0
    entry_session: str = ""
    extended: bool = False
    manual_stop_usdt: float | None = None


@dataclass
class Trade:
    symbol: str
    direction: str
    strategy: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    hold_hours: float
    size_usdt: float
    signal_info: str
    gross_bps: float
    net_bps: float
    pnl_usdt: float
    mae_bps: float
    mfe_bps: float
    reason: str
    entry_oi_delta: float = 0.0
    entry_crowding: int = 0
    entry_confluence: int = 0
    entry_session: str = ""
    funding_usdt: float = 0.0  # real funding paid (live only; 0 paper)
