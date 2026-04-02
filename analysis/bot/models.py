"""Data structures — SymbolState, Position, Trade."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SymbolState:
    price: float = 0.0
    updated_at: float = 0.0
    candles_4h: deque = field(default_factory=lambda: deque(maxlen=200))
    last_candle_ts: int = 0
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))  # ~5h @ 60s
    # OI + funding + premium: collected every 60s for observation/crowding score.
    # Not used for signal decisions yet — waiting for 50+ trades to analyze correlation.
    oi: float = 0.0
    funding: float = 0.0
    premium: float = 0.0
    oi_history: deque = field(default_factory=lambda: deque(maxlen=360))  # 6h @ 60s


@dataclass
class Position:
    symbol: str
    direction: int           # 1=LONG, -1=SHORT
    strategy: str            # S1, S5, S8, S9, S10
    entry_price: float
    entry_time: datetime
    size_usdt: float
    signal_info: str         # human-readable signal description
    target_exit: datetime
    mae_bps: float = 0.0    # Max Adverse Excursion (worst unrealized during trade)
    mfe_bps: float = 0.0    # Max Favorable Excursion (best unrealized during trade)
    trajectory: list = field(default_factory=list)  # [(hours_since_entry, unrealized_bps), ...] capped at 200
    stop_bps: float = 0.0       # per-position stop loss (0 = use default STOP_LOSS_BPS)
    # Structured entry context (for post-hoc OI analysis — populated at entry, copied to Trade at close)
    entry_oi_delta: float = 0.0      # OI delta 1h at entry (%)
    entry_crowding: int = 0          # crowding score at entry (0-100)
    entry_confluence: int = 0        # count of extreme features at entry (0-5)
    entry_session: str = ""          # Asia/EU/US/Night/WE


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
