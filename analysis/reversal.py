"""Multi-Signal Bot v10.3.0 — Five strategies + DXY filter + 2x leverage + OI observation.

Strategies (all validated: train/test + Monte Carlo + portfolio + walk-forward):
  S1: btc_30d > +20% → LONG alts              (z=6.42, rare but powerful)
  S2: alt_index_7d < -10% → LONG              (z=4.00, buy alt crashes)
  S4: vol contraction + DXY rising → SHORT     (z=2.95, filtered by dollar)
  S5: sector divergence > 10% + volume → FOLLOW (z=3.67, sector breakout)
  S8: capitulation flush + BTC weak → LONG     (z=6.99, buy market-wide flushes)

Config:
  Leverage: 2x (optimal from parameter sweep)
  Hold: 72h (S1/S2/S4), 48h (S5), 60h (S8)
  Sizing: 12% base + 3% bonus (z>4), z-weighted, S8 haircut 0.8
  Stop: -25% leveraged (S1/S2/S4/S5), -15% (S8)
  Max 6 positions, max 4 same direction, max 2 per sector
  Kill-switch: auto-pause if P&L < -$300, sizing /2 after 3 losses
  DXY filter: S4 only active when dollar rising (+1%/7d)
  OI + funding: collected for observation, not used for signals yet

Run:       python3 -m analysis.reversal
Dashboard: http://0.0.0.0:8097
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import shutil
import signal
import time
import urllib.request
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import orjson
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BOT] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("multisignal")

VERSION = "10.3.0"

# ── Config ───────────────────────────────────────────────────────────

# 28 altcoins on Hyperliquid (from backtest universe)
TRADE_SYMBOLS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
]

REFERENCE = ["BTC", "ETH"]
ALL_SYMBOLS = TRADE_SYMBOLS + REFERENCE

# Strategy configs
HOLD_HOURS_DEFAULT = 72   # 3 days (18 × 4h candles) for S1, S2, S4
HOLD_HOURS_S5 = 48        # 2 days (12 × 4h candles) for S5 sector
CANDLES_NEEDED = 180 + 6  # 30 days warmup + margin

# Sectors (for S5 divergence)
SECTORS = {
    "L1":     ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":   ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO", "GMX"],
    "Gaming": ["GALA", "IMX", "SAND"],
    "Infra":  ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":   ["DOGE", "WLD", "BLUR", "MINA"],
}
TOKEN_SECTOR = {}
for _sect, _toks in SECTORS.items():
    for _t in _toks:
        TOKEN_SECTOR[_t] = _sect

# S5 params
S5_DIV_THRESHOLD = 1000   # 10% divergence from sector
S5_VOL_Z_MIN = 1.0        # minimum volume z-score to confirm

# S6 REMOVED — z=8.04 in isolation but LOSES in portfolio (-$627 to -$1,552)
# Standalone backtest was misleading (simpler backtester, no position limits)

# S8 params (capitulation flush + BTC weakness)
S8_DRAWDOWN_THRESH = -4000   # -40% from 30d high
S8_VOL_Z_MIN = 1.0           # volume spike confirmation
S8_RET_6H_THRESH = -50       # still bleeding (6h return < -0.5%)
S8_BTC_7D_THRESH = -300      # BTC also weak (7d < -3%)
HOLD_HOURS_S8 = 60           # 60h hold (15 candles)

# DXY filter for S4
DXY_CACHE = os.path.join(os.path.dirname(__file__), "output", "pairs_data", "macro_DXY.json")
DXY_BOOST_THRESHOLD = 100  # DXY 7d > +1% → S4 active

# Leverage: 2x (optimal from boost backtest — $1k→$17.7k, DD -54%)
LEVERAGE = 2.0

# Sizing: compounding 12% base + 3% bonus for high-z, z-weighted
SIZE_PCT = 0.12           # 12% of current capital per position (reduced from 15%)
SIZE_BONUS = 0.03         # bonus for high-z signals (z > 4)
STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67, "S8": 6.99}
LIQUIDITY_HAIRCUT = {"S8": 0.8}  # S8 operates in thin markets

# Capital
CAPITAL_USDT = 1000.0
MAX_POSITIONS = 6
MAX_SAME_DIRECTION = 4
MAX_PER_SECTOR = 2

# Costs
TAKER_FEE_BPS = 7.0
SLIPPAGE_BPS = 3.0
FUNDING_DRAG_BPS = 2.0
COST_BPS = TAKER_FEE_BPS + SLIPPAGE_BPS + FUNDING_DRAG_BPS  # 12 bps

# Stop loss per strategy (leveraged bps)
STOP_LOSS_BPS = -2500.0        # default catastrophe guard (-25% leveraged = -12.5% price)
STOP_LOSS_S8 = -1500.0         # S8 backtested with -1500 (-15% leveraged = -7.5% price)

# Portfolio kill-switch
DAILY_LOSS_CAP = -300.0        # auto-pause if total P&L drops below this
LOSS_STREAK_THRESHOLD = 3      # reduce sizing after N consecutive losses
LOSS_STREAK_MULTIPLIER = 0.5   # halve position size during loss streak
LOSS_STREAK_COOLDOWN = 24 * 3600  # 24h before returning to normal sizing

# Timing
SCAN_INTERVAL = 3600      # check signals every hour (candles are 4h)
COOLDOWN_HOURS = 24       # 24h cooldown per symbol after exit

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "reversal_trades.csv")
STATE_FILE = os.path.join(OUTPUT_DIR, "reversal_state.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "reversal.html")
WEB_PORT = 8097


def strat_size(strat_name: str, capital: float) -> float:
    """12% base + 3% bonus if z>4, z-weighted, with liquidity haircut."""
    z = STRAT_Z.get(strat_name, 3.0)
    weight = max(0.5, min(2.0, z / 4.0))
    pct = SIZE_PCT + (SIZE_BONUS if z > 4.0 else 0)
    base = capital * pct
    haircut = LIQUIDITY_HAIRCUT.get(strat_name, 1.0)
    return round(max(10, base * weight * haircut), 2)


# ── Data Structures ──────────────────────────────────────────────────

@dataclass
class SymbolState:
    price: float = 0.0
    updated_at: float = 0.0
    candles_4h: deque = field(default_factory=lambda: deque(maxlen=200))
    last_candle_ts: int = 0
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=300))
    # OI + funding tracking (collected every 60s, not used for signals yet — observation phase)
    oi: float = 0.0
    funding: float = 0.0
    oi_history: deque = field(default_factory=lambda: deque(maxlen=360))  # 6h @ 60s


@dataclass
class Position:
    symbol: str
    direction: int           # 1=LONG, -1=SHORT
    strategy: str            # S1, S2, S4
    entry_price: float
    entry_time: datetime
    size_usdt: float
    signal_info: str         # human-readable signal description
    target_exit: datetime


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
    reason: str


class MultiSignalBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s: SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        self.trades: deque[Trade] = deque(maxlen=500)
        self.running = False
        self._paused = False
        self._feature_cache: dict[str, dict | None] = {}  # symbol → features (refreshed each scan)
        self._shutdown_event: asyncio.Event | None = None
        self.started_at: datetime | None = None
        self._total_pnl = 0.0
        self._wins = 0
        self._last_scan: float = 0
        self._last_price_fetch: float = 0
        self._cooldowns: dict[str, float] = {}  # symbol → earliest re-entry epoch
        self._degraded: list[str] = []
        self._consecutive_losses = 0
        self._loss_streak_until: float = 0  # epoch when streak penalty expires

    # ── Price Data ──────────────────────────────────────────────

    def _fetch_prices(self):
        """Fetch current prices from Hyperliquid."""
        try:
            payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
            req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                         data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            meta = data[0]
            ctxs = data[1]
            if len(meta["universe"]) != len(ctxs):
                log.warning("API mismatch: %d universe vs %d ctxs", len(meta["universe"]), len(ctxs))
                return
            now = time.time()

            for i, asset in enumerate(meta["universe"]):
                name = asset["name"]
                if name not in self.states:
                    continue
                price = float(ctxs[i].get("markPx", 0))
                if price > 0:
                    st = self.states[name]
                    st.price = price
                    st.updated_at = now
                    st.price_ticks.append((now, price))
                    # Collect OI + funding (observation phase — not used for signals yet)
                    oi = float(ctxs[i].get("openInterest", 0))
                    if oi > 0:
                        st.oi = oi
                        st.oi_history.append((now, oi))
                    st.funding = float(ctxs[i].get("funding", 0))
        except Exception as e:
            log.warning("Price fetch error: %s", e)

    def _fetch_candles(self, symbol: str):
        """Fetch 4h candles for a symbol (need 180+ for features)."""
        try:
            end_ts = int(time.time() * 1000)
            start_ts = end_ts - 45 * 86400 * 1000  # 45 days
            payload = json.dumps({"type": "candleSnapshot", "req": {
                "coin": symbol, "interval": "4h", "startTime": start_ts, "endTime": end_ts
            }}).encode()
            req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                         data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                candles = json.loads(resp.read())

            st = self.states[symbol]
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

    # ── Feature Computation ─────────────────────────────────────

    def _compute_features(self, symbol: str) -> dict | None:
        """Compute features for a single symbol from its 4h candles."""
        st = self.states.get(symbol)
        if not st or len(st.candles_4h) < 50:
            return None

        candles = list(st.candles_4h)
        n = len(candles)
        i = n - 1  # latest candle

        closes = np.array([c["c"] for c in candles])
        highs = np.array([c["h"] for c in candles])
        lows = np.array([c["l"] for c in candles])

        f = {}
        # Return over 42 candles (7 days)
        if i >= 42 and closes[i - 42] > 0:
            f["ret_42h"] = (closes[i] / closes[i - 42] - 1) * 1e4
        else:
            return None

        # Volatility ratio
        if i >= 42:
            denom_7d = closes[max(0, i-42):i]
            if (denom_7d == 0).any():
                return None
            rets_7d = np.diff(closes[max(0, i-42):i+1]) / denom_7d
            f["vol_7d"] = float(np.std(rets_7d) * 1e4) if len(rets_7d) > 1 else 0
        else:
            f["vol_7d"] = 0

        if i >= 180:
            denom_30d = closes[i-180:i]
            if (denom_30d == 0).any():
                return None
            rets_30d = np.diff(closes[i-180:i+1]) / denom_30d
            f["vol_30d"] = float(np.std(rets_30d) * 1e4) if len(rets_30d) > 1 else 0
        elif i >= 42:
            f["vol_30d"] = f["vol_7d"]  # fallback
        else:
            f["vol_30d"] = 0

        f["vol_ratio"] = f["vol_7d"] / f["vol_30d"] if f["vol_30d"] > 0 else 1.0

        # Range of latest candle
        c = candles[i]
        f["range_pct"] = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0

        # Drawdown from 30d high (needed for S8)
        high_30d = float(np.max(highs[max(0, i-180):i+1]))
        f["drawdown"] = (closes[i] / high_30d - 1) * 1e4 if high_30d > 0 else 0

        # Return over 6 candles / 1 day (needed for S8)
        if i >= 6 and closes[i - 6] > 0:
            f["ret_6h"] = (closes[i] / closes[i - 6] - 1) * 1e4
        else:
            f["ret_6h"] = 0

        # Volume z-score (needed for S5, S8)
        volumes = np.array([c["v"] for c in candles])
        if i >= 42:
            vol_window = volumes[max(0, i-180):i]
            vol_mean = float(np.mean(vol_window)) if len(vol_window) > 0 else 0
            vol_std = float(np.std(vol_window)) if len(vol_window) > 1 else 0
            f["vol_z"] = (volumes[i] - vol_mean) / vol_std if vol_std > 0 else 0
        else:
            f["vol_z"] = 0

        return f

    def _compute_oi_features(self, symbol: str) -> dict:
        """Compute OI delta features from live 60s samples. Observation only — not used for signals."""
        st = self.states.get(symbol)
        if not st or len(st.oi_history) < 10:
            return {"oi_delta_1h": 0.0, "oi_delta_4h": 0.0, "funding_bps": 0.0}
        history = list(st.oi_history)
        now_oi = history[-1][1]
        # 1h delta (~60 samples)
        idx_1h = max(0, len(history) - 60)
        oi_1h = history[idx_1h][1]
        delta_1h = (now_oi / oi_1h - 1) * 100 if oi_1h > 0 else 0.0
        # 4h delta (~240 samples)
        idx_4h = max(0, len(history) - 240)
        oi_4h = history[idx_4h][1]
        delta_4h = (now_oi / oi_4h - 1) * 100 if oi_4h > 0 else 0.0
        # Funding in bps (hourly rate × 10000)
        funding_bps = st.funding * 1e4
        return {
            "oi_delta_1h": round(delta_1h, 2),
            "oi_delta_4h": round(delta_4h, 2),
            "funding_bps": round(funding_bps, 3),
        }

    def _compute_btc_features(self) -> dict:
        """Compute BTC-level features."""
        btc = self.states.get("BTC")
        if not btc or len(btc.candles_4h) < 50:
            return {}

        candles = list(btc.candles_4h)
        n = len(candles)
        closes = np.array([c["c"] for c in candles])

        f = {}
        if n >= 180 and closes[n - 180] > 0:
            f["btc_30d"] = (closes[-1] / closes[n - 180] - 1) * 1e4
        elif n >= 42 and closes[n - 42] > 0:
            f["btc_30d"] = (closes[-1] / closes[n - 42] - 1) * 1e4
        else:
            f["btc_30d"] = 0

        if n >= 42 and closes[n - 42] > 0:
            f["btc_7d"] = (closes[-1] / closes[n - 42] - 1) * 1e4
        else:
            f["btc_7d"] = 0

        return f

    def _fetch_dxy(self) -> float:
        """Fetch DXY 7-day return. Returns bps or 0 on failure."""
        try:
            # Try cached data first
            if os.path.exists(DXY_CACHE):
                age_h = (time.time() - os.path.getmtime(DXY_CACHE)) / 3600
                if age_h < 6:
                    with open(DXY_CACHE) as f:
                        daily = json.load(f)
                    if len(daily) >= 10:
                        closes = [d["c"] for d in daily[-10:]]
                        return (closes[-1] / closes[-6] - 1) * 1e4 if closes[-6] > 0 else 0

            # Fetch fresh from Yahoo Finance
            end_ts = int(time.time())
            start_ts = end_ts - 30 * 86400
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
                   f"?period1={start_ts}&period2={end_ts}&interval=1d")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read())

            result = raw["chart"]["result"][0]
            timestamps = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]

            daily = [{"t": ts * 1000, "c": c} for ts, c in zip(timestamps, closes) if c]
            if daily:
                os.makedirs(os.path.dirname(DXY_CACHE), exist_ok=True)
                with open(DXY_CACHE, "w") as f:
                    json.dump(daily, f)
                # 7d return (5 trading days)
                if len(daily) >= 6:
                    if "DXY" in self._degraded:
                        self._degraded.remove("DXY")
                    return (daily[-1]["c"] / daily[-6]["c"] - 1) * 1e4
        except Exception as e:
            log.warning("DXY unavailable — S4 disabled: %s", e)
            if "DXY" not in self._degraded:
                self._degraded.append("DXY")
        return 0.0

    def _compute_alt_index(self) -> float:
        """Compute alt-index: mean 7d return across all alts (uses cache)."""
        rets = []
        for sym in TRADE_SYMBOLS:
            f = self._get_cached_features(sym) or self._compute_features(sym)
            if f and "ret_42h" in f:
                rets.append(f["ret_42h"])
        return float(np.mean(rets)) if rets else 0

    def _compute_sector_divergence(self, symbol: str) -> dict | None:
        """Compute how much a token diverges from its sector peers.

        Returns {divergence, sector_mean, vol_z} or None.
        """
        sector = TOKEN_SECTOR.get(symbol)
        if not sector:
            return None

        own_f = self._compute_features(symbol)
        if not own_f or "ret_42h" not in own_f:
            return None

        # Compute sector mean excluding self
        peers = SECTORS[sector]
        peer_rets = []
        for peer in peers:
            if peer == symbol:
                continue
            pf = self._compute_features(peer)
            if pf and "ret_42h" in pf:
                peer_rets.append(pf["ret_42h"])

        if len(peer_rets) < 2:
            return None

        sector_mean = float(np.mean(peer_rets))
        divergence = own_f["ret_42h"] - sector_mean

        return {
            "divergence": divergence,
            "sector_mean": sector_mean,
            "token_ret": own_f["ret_42h"],
            "vol_z": own_f.get("vol_z", 0),
            "sector": sector,
        }

    def _refresh_feature_cache(self):
        """Recompute all features once per scan cycle."""
        self._feature_cache = {sym: self._compute_features(sym) for sym in TRADE_SYMBOLS}

    def _get_cached_features(self, symbol: str) -> dict | None:
        return self._feature_cache.get(symbol)

    # ── Signal Detection ─────────────────────────────────────────

    def _scan_signals(self) -> int:
        """Scan for signals from all strategies."""
        now = datetime.now(timezone.utc)
        signals = []

        # Compute global features
        btc_f = self._compute_btc_features()
        alt_index = self._compute_alt_index()
        dxy_7d = self._fetch_dxy()

        btc_30d = btc_f.get("btc_30d", 0)

        for sym in TRADE_SYMBOLS:
            if sym in self.positions:
                continue
            if sym in self._cooldowns and time.time() < self._cooldowns[sym]:
                continue

            f = self._get_cached_features(sym) or self._compute_features(sym)
            if not f:
                continue

            st = self.states.get(sym)
            if not st or st.price == 0:
                continue

            # S1: btc_30d > 2000 bps → LONG
            if btc_30d > 2000:
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S1",
                    "z": STRAT_Z["S1"],
                    "info": f"BTC 30d={btc_30d:+.0f}bps",
                    "strength": abs(btc_30d),
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S2: alt_index_7d < -1000 bps → LONG
            if alt_index < -1000:
                oi_f = self._compute_oi_features(sym)
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S2",
                    "z": STRAT_Z["S2"],
                    "info": f"AltIdx={alt_index:+.0f}bps OI1h={oi_f['oi_delta_1h']:+.1f}%",
                    "strength": abs(alt_index),
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S4: vol_ratio < 1.0 AND range_pct < 200 → SHORT
            # DXY filter: only active when dollar is rising (DXY 7d > +1%)
            # When DXY is falling, S4 is disabled (don't short in weak-dollar environment)
            if f["vol_ratio"] < 1.0 and f["range_pct"] < 200 and dxy_7d > DXY_BOOST_THRESHOLD:
                signals.append({
                    "symbol": sym, "direction": -1, "strategy": "S4",
                    "z": STRAT_Z["S4"],
                    "info": f"VolR={f['vol_ratio']:.2f} Rng={f['range_pct']:.0f} DXY={dxy_7d:+.0f}",
                    "strength": (1.0 - f["vol_ratio"]) * 1000,
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S5: sector divergence > 1000bps + volume z > 1.0 → FOLLOW
            sd = self._compute_sector_divergence(sym)
            if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
                direction = 1 if sd["divergence"] > 0 else -1
                signals.append({
                    "symbol": sym, "direction": direction, "strategy": "S5",
                    "z": STRAT_Z["S5"],
                    "info": f"{sd['sector']} div={sd['divergence']:+.0f} vz={sd['vol_z']:.1f}",
                    "strength": abs(sd["divergence"]),
                    "hold_hours": HOLD_HOURS_S5,
                })

            # S8: capitulation flush — drawdown < -40% + vol spike + bleeding + BTC weak → LONG
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_6h", 0) < S8_RET_6H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                oi_f = self._compute_oi_features(sym)
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S8",
                    "z": STRAT_Z["S8"],
                    "info": f"DD={f['drawdown']:.0f} vz={f['vol_z']:.1f} r6h={f['ret_6h']:.0f} BTC7d={btc_f.get('btc_7d',0):+.0f} OI1h={oi_f['oi_delta_1h']:+.1f}%",
                    "strength": abs(f["drawdown"]),
                    "hold_hours": HOLD_HOURS_S8,
                })

            # S6 REMOVED — loses in portfolio despite z=8.04 in isolation

        # Sort by z-score first (higher priority strategies), then strength
        signals.sort(key=lambda s: (s["z"], s["strength"]), reverse=True)

        n_longs = sum(1 for p in self.positions.values() if p.direction == 1)
        n_shorts = sum(1 for p in self.positions.values() if p.direction == -1)

        entries = 0
        seen_symbols = set()
        for sig in signals:
            if len(self.positions) >= MAX_POSITIONS:
                break

            sym = sig["symbol"]
            if sym in seen_symbols:
                continue
            seen_symbols.add(sym)

            if sig["direction"] == 1 and n_longs >= MAX_SAME_DIRECTION:
                continue
            if sig["direction"] == -1 and n_shorts >= MAX_SAME_DIRECTION:
                continue

            # Sector concentration limit
            sym_sector = TOKEN_SECTOR.get(sym)
            if sym_sector:
                sector_count = sum(1 for p in self.positions.values() if TOKEN_SECTOR.get(p.symbol) == sym_sector)
                if sector_count >= MAX_PER_SECTOR:
                    continue

            st = self.states[sym]
            hold_h = sig.get("hold_hours", HOLD_HOURS_DEFAULT)
            target_exit = now + timedelta(hours=hold_h)
            current_capital = CAPITAL_USDT + self._total_pnl
            size = strat_size(sig["strategy"], current_capital)
            # Loss streak penalty
            if time.time() < self._loss_streak_until:
                size = round(size * LOSS_STREAK_MULTIPLIER, 2)

            # Capital exposure limit: max 90% of capital as margin
            used_margin = sum(p.size_usdt for p in self.positions.values())
            if used_margin + size > current_capital * 0.90:
                continue

            self.positions[sym] = Position(
                symbol=sym, direction=sig["direction"],
                strategy=sig["strategy"],
                entry_price=st.price, entry_time=now,
                size_usdt=size, signal_info=sig["info"],
                target_exit=target_exit,
            )

            if sig["direction"] == 1:
                n_longs += 1
            else:
                n_shorts += 1
            entries += 1

            side = "LONG" if sig["direction"] == 1 else "SHORT"
            log.info("→ %s %s %s @ $%.4f | %s | $%.0f | exit ~%s | %d/%d pos",
                     sig["strategy"], side, sym, st.price, sig["info"],
                     size, target_exit.strftime("%m-%d %H:%M"),
                     len(self.positions), MAX_POSITIONS)

        return entries

    # ── Exit Logic ──────────────────────────────────────────────

    def _check_exits(self) -> int:
        now = datetime.now(timezone.utc)
        exits = 0

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            st = self.states.get(sym)
            if not st or st.price == 0:
                continue

            unrealized = pos.direction * (st.price / pos.entry_price - 1) * 1e4 * LEVERAGE

            # Per-strategy stop loss (S8 backtested with tighter stop)
            stop = STOP_LOSS_S8 if pos.strategy == "S8" else STOP_LOSS_BPS

            exit_reason = None
            if now >= pos.target_exit:
                exit_reason = "timeout"
            elif unrealized < stop:
                exit_reason = "catastrophe_stop"

            if exit_reason:
                self._close_position(sym, st.price, now, exit_reason)
                exits += 1

        return exits

    def _close_position(self, sym: str, exit_price: float, now: datetime, reason: str):
        pos = self.positions.pop(sym)
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4 * LEVERAGE
        effective_cost = COST_BPS * LEVERAGE  # fees/slippage scale with notional
        net_bps = gross_bps - effective_cost
        pnl = pos.size_usdt * net_bps / 1e4

        self._total_pnl += pnl
        if pnl > 0:
            self._wins += 1

        # Track consecutive losses for kill-switch
        if pnl > 0:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            if self._consecutive_losses >= LOSS_STREAK_THRESHOLD:
                self._loss_streak_until = time.time() + LOSS_STREAK_COOLDOWN
                log.warning("Loss streak: %d consecutive losses — sizing reduced for 24h",
                             self._consecutive_losses)

        # Daily loss cap
        if self._total_pnl <= DAILY_LOSS_CAP:
            self._paused = True
            log.critical("KILL-SWITCH: P&L $%.2f below cap $%.0f — auto-paused",
                         self._total_pnl, DAILY_LOSS_CAP)

        # Cooldown
        self._cooldowns[sym] = time.time() + COOLDOWN_HOURS * 3600

        trade = Trade(
            symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
            strategy=pos.strategy,
            entry_time=pos.entry_time.isoformat(), exit_time=now.isoformat(),
            entry_price=pos.entry_price, exit_price=exit_price,
            hold_hours=round(hold_h, 1), size_usdt=pos.size_usdt,
            signal_info=pos.signal_info,
            gross_bps=round(gross_bps, 1), net_bps=round(net_bps, 1),
            pnl_usdt=round(pnl, 2), reason=reason,
        )
        self.trades.append(trade)
        self._write_csv(trade)

        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl
        wr = self._wins / n * 100 if n > 0 else 0
        arrow = "✓" if pnl > 0 else "✗"
        log.info("%s %s %s %s | %.0fh | %s | gross %+.1f | net %+.1f | $%+.2f | bal $%.0f (#%d %.0f%%)",
                 arrow, pos.strategy, trade.direction, sym, hold_h, reason,
                 gross_bps, net_bps, pnl, balance, n, wr)

    def _write_csv(self, t: Trade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        with open(TRADES_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["symbol", "direction", "strategy", "entry_time", "exit_time",
                           "entry_price", "exit_price", "hold_hours", "size_usdt",
                           "signal_info", "gross_bps", "net_bps", "pnl_usdt", "reason"])
            w.writerow([t.symbol, t.direction, t.strategy, t.entry_time, t.exit_time,
                       t.entry_price, t.exit_price, t.hold_hours, t.size_usdt,
                       t.signal_info, t.gross_bps, t.net_bps, t.pnl_usdt, t.reason])

    # ── Persistence ─────────────────────────────────────────────

    def _save_state(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = {
            "version": VERSION,
            "total_pnl": self._total_pnl, "wins": self._wins,
            "cooldowns": {k: v for k, v in self._cooldowns.items() if v > time.time()},
            "positions": [{
                "symbol": p.symbol, "direction": p.direction,
                "strategy": p.strategy,
                "entry_price": p.entry_price, "entry_time": p.entry_time.isoformat(),
                "size_usdt": p.size_usdt, "signal_info": p.signal_info,
                "target_exit": p.target_exit.isoformat(),
            } for p in self.positions.values()],
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(orjson.dumps(data))
        os.replace(tmp, STATE_FILE)  # atomic on POSIX

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "rb") as f:
                data = orjson.loads(f.read())
            self._total_pnl = data.get("total_pnl", 0)
            self._wins = data.get("wins", 0)
            self._cooldowns = data.get("cooldowns", {})
            for p in data.get("positions", []):
                if p["symbol"] not in self.states:
                    log.warning("Skipping unknown symbol from state: %s", p["symbol"])
                    continue
                self.positions[p["symbol"]] = Position(
                    symbol=p["symbol"], direction=p["direction"],
                    strategy=p.get("strategy", "?"),
                    entry_price=p["entry_price"],
                    entry_time=datetime.fromisoformat(p["entry_time"]),
                    size_usdt=p["size_usdt"],
                    signal_info=p.get("signal_info", ""),
                    target_exit=datetime.fromisoformat(p["target_exit"]),
                )
            if self.positions or self._total_pnl:
                log.info("Restored: %d positions, P&L $%.2f", len(self.positions), self._total_pnl)
            # Keep backup but don't remove original — next _save_state() overwrites it
            shutil.copy2(STATE_FILE, STATE_FILE + ".loaded")
        except Exception:
            log.exception("Load state failed")

    def _load_trades(self):
        if not os.path.exists(TRADES_CSV):
            return
        try:
            with open(TRADES_CSV) as f:
                for row in csv.DictReader(f):
                    self.trades.append(Trade(
                        symbol=row["symbol"], direction=row["direction"],
                        strategy=row.get("strategy", "?"),
                        entry_time=row["entry_time"], exit_time=row["exit_time"],
                        entry_price=float(row["entry_price"]),
                        exit_price=float(row["exit_price"]),
                        hold_hours=float(row["hold_hours"]),
                        size_usdt=float(row["size_usdt"]),
                        signal_info=row.get("signal_info", ""),
                        gross_bps=float(row["gross_bps"]),
                        net_bps=float(row["net_bps"]),
                        pnl_usdt=float(row["pnl_usdt"]),
                        reason=row["reason"],
                    ))
            if self.trades:
                log.info("Loaded %d historical trades", len(self.trades))
        except Exception:
            log.exception("Load trades failed")

    # ── API ─────────────────────────────────────────────────────

    def get_state(self) -> dict:
        now = datetime.now(timezone.utc)
        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl

        btc_f = self._compute_btc_features()
        alt_idx = self._compute_alt_index()

        positions = []
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            price = st.price if st else pos.entry_price
            unreal = pos.direction * (price / pos.entry_price - 1) * 1e4 * LEVERAGE if pos.entry_price > 0 else 0
            pnl_u = pos.size_usdt * unreal / 1e4
            remaining_h = max(0, (pos.target_exit - now).total_seconds() / 3600)

            positions.append({
                "symbol": sym,
                "direction": "LONG" if pos.direction == 1 else "SHORT",
                "strategy": pos.strategy,
                "entry_price": pos.entry_price,
                "current_price": price,
                "size_usdt": pos.size_usdt,
                "signal_info": pos.signal_info,
                "unrealized_bps": round(unreal, 1),
                "pnl_usdt": round(pnl_u, 2),
                "hold_hours": round((now - pos.entry_time).total_seconds() / 3600, 1),
                "remaining_hours": round(remaining_h, 1),
            })

        # Active signals
        active_signals = []
        if btc_f.get("btc_30d", 0) > 2000:
            active_signals.append(f"S1: BTC 30d = {btc_f['btc_30d']:+.0f}bps → LONG")
        if alt_idx < -1000:
            active_signals.append(f"S2: Alt index = {alt_idx:+.0f}bps → LONG")
        # DXY status
        dxy_7d = self._fetch_dxy()
        dxy_active = dxy_7d > DXY_BOOST_THRESHOLD
        # S4 only when DXY rising
        if dxy_active:
            s4_count = sum(1 for sym in TRADE_SYMBOLS
                           if (f := self._compute_features(sym)) and
                           f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200)
            if s4_count > 0:
                active_signals.append(f"S4: {s4_count} quiet + DXY={dxy_7d:+.0f}bp → SHORT")
        else:
            active_signals.append(f"S4: OFF (DXY={dxy_7d:+.0f}bp, need >+{DXY_BOOST_THRESHOLD})")
        # S5 sector divergence
        s5_syms = []
        for sym in TRADE_SYMBOLS:
            sd = self._compute_sector_divergence(sym)
            if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
                d = "L" if sd["divergence"] > 0 else "S"
                s5_syms.append(f"{sym}({d})")
        if s5_syms:
            active_signals.append(f"S5: {', '.join(s5_syms[:5])} sector divergence")
        # S8 capitulation flush
        s8_syms = []
        for sym in TRADE_SYMBOLS:
            f = self._get_cached_features(sym) or self._compute_features(sym)
            if (f and f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_6h", 0) < S8_RET_6H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                s8_syms.append(sym)
        if s8_syms:
            active_signals.append(f"S8: {', '.join(s8_syms[:5])} capitulation flush")
        return {
            "version": VERSION, "strategy": "Multi-Signal (S1+S2+S4+S5+S8)",
            "paused": self._paused, "running": self.running,
            "degraded": list(self._degraded),
            "loss_streak": self._consecutive_losses,
            "kill_switch_active": self._total_pnl <= DAILY_LOSS_CAP,
            "balance": round(balance, 2),
            "total_pnl": round(self._total_pnl, 2),
            "total_trades": n,
            "win_rate": round(self._wins / n, 3) if n > 0 else 0,
            "n_positions": len(self.positions), "max_positions": MAX_POSITIONS,
            "positions": positions,
            "active_signals": active_signals,
            "market": {
                "btc_30d": round(btc_f.get("btc_30d", 0), 0),
                "alt_index_7d": round(alt_idx, 0),
                "dxy_7d": round(dxy_7d, 0),
                "oi_falling": sum(1 for s in TRADE_SYMBOLS if self._compute_oi_features(s)["oi_delta_1h"] < -0.5),
                "oi_rising": sum(1 for s in TRADE_SYMBOLS if self._compute_oi_features(s)["oi_delta_1h"] > 0.5),
            },
            "params": {"hold_h": HOLD_HOURS_DEFAULT, "hold_s5_h": HOLD_HOURS_S5,
                       "cost_bps": COST_BPS, "stop_bps": STOP_LOSS_BPS,
                       "max_pos": MAX_POSITIONS},
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "last_price_s": time.time() - self._last_price_fetch if self._last_price_fetch else None,
            "last_scan_s": time.time() - self._last_scan if self._last_scan else None,
            "next_scan_s": max(0, SCAN_INTERVAL - (time.time() - self._last_scan)) if self._last_scan else 0,
            "scan_interval": SCAN_INTERVAL,
        }

    def get_signals(self) -> dict:
        """All symbols with their current features and signal status."""
        btc_f = self._compute_btc_features()
        alt_idx = self._compute_alt_index()
        signals = {}

        for sym in TRADE_SYMBOLS:
            st = self.states.get(sym)
            f = self._get_cached_features(sym) or self._compute_features(sym)
            if not st or not f:
                continue

            triggered = []
            if btc_f.get("btc_30d", 0) > 2000:
                triggered.append("S1:LONG")
            if alt_idx < -1000:
                triggered.append("S2:LONG")
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200:
                dxy_val = self._fetch_dxy()
                if dxy_val > DXY_BOOST_THRESHOLD:
                    triggered.append("S4:SHORT")
                else:
                    triggered.append("S4:OFF(DXY)")
            sd = self._compute_sector_divergence(sym)
            if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
                d = "LONG" if sd["divergence"] > 0 else "SHORT"
                triggered.append(f"S5:{d}")
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_6h", 0) < S8_RET_6H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                triggered.append("S8:LONG")

            oi_f = self._compute_oi_features(sym)
            signals[sym] = {
                "price": st.price,
                "ret_7d_bps": round(f.get("ret_42h", 0), 1),
                "vol_ratio": round(f.get("vol_ratio", 0), 2),
                "range_bps": round(f.get("range_pct", 0), 0),
                "sector": TOKEN_SECTOR.get(sym, "?"),
                "sector_div": round(sd["divergence"], 0) if sd else 0,
                "oi_delta_1h": oi_f["oi_delta_1h"],
                "funding_bps": oi_f["funding_bps"],
                "triggered": triggered,
                "in_position": sym in self.positions,
                "position_strategy": self.positions[sym].strategy if sym in self.positions else None,
            }
        return {"signals": signals, "btc_30d": round(btc_f.get("btc_30d", 0), 0),
                "alt_index": round(alt_idx, 0)}

    def get_trades_list(self, limit=50) -> list:
        trades = list(self.trades)  # deque doesn't support slicing
        return [t.__dict__ for t in trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        cum = 0.0
        pts = []
        for t in self.trades:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                        "balance": round(CAPITAL_USDT + cum, 2)})
        return pts

    # ── Main Loop ───────────────────────────────────────────────

    async def main_loop(self):
        while self.running:
            try:
                now = time.time()

                await asyncio.to_thread(self._fetch_prices)
                self._last_price_fetch = time.time()

                if now - self._last_scan >= SCAN_INTERVAL:
                    log.info("Scanning signals...")
                    for sym in ALL_SYMBOLS:
                        await asyncio.to_thread(self._fetch_candles, sym)
                        await asyncio.sleep(0.2)

                    self._refresh_feature_cache()

                    exits = self._check_exits()
                    if exits:
                        self._save_state()

                    if not self._paused:
                        n_new = self._scan_signals()
                        if n_new:
                            log.info("Opened %d new positions", n_new)

                    self._last_scan = now
                    self._save_state()

                    # Log status
                    n = len(self.trades)
                    balance = CAPITAL_USDT + self._total_pnl
                    wr = self._wins / n * 100 if n > 0 else 0
                    btc_f = self._compute_btc_features()
                    alt_idx = self._compute_alt_index()
                    log.info("Status: %d pos | $%.0f | %d trades (%.0f%%) | BTC30d=%+.0f | AltIdx=%+.0f",
                             len(self.positions), balance, n, wr,
                             btc_f.get("btc_30d", 0), alt_idx)
                else:
                    exits = self._check_exits()
                    if exits:
                        self._save_state()

            except Exception:
                log.exception("Loop error")

            await asyncio.sleep(60)


# ── FastAPI ──────────────────────────────────────────────────────────

bot = MultiSignalBot()
app = FastAPI()
_html_cache = None


@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        if os.path.exists(HTML_PATH):
            _html_cache = Path(HTML_PATH).read_text().replace("{{VERSION}}", VERSION)
        else:
            _html_cache = f"""<html><body style="background:#0d1117;color:#e6edf3;font-family:monospace">
            <h1>Multi-Signal Bot v{VERSION}</h1>
            <pre id="s"></pre>
            <script>
            setInterval(()=>fetch('/api/state').then(r=>r.json()).then(d=>document.getElementById('s').textContent=JSON.stringify(d,null,2)),5000);
            </script></body></html>"""
    return _html_cache


@app.get("/api/state")
async def api_state():
    return JSONResponse(bot.get_state())

@app.get("/api/signals")
async def api_signals():
    return JSONResponse(bot.get_signals())

@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return JSONResponse(bot.get_trades_list(limit))

@app.get("/api/pnl")
async def api_pnl():
    return JSONResponse(bot.get_pnl_curve())

@app.post("/api/pause")
async def api_pause():
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.price > 0:
            bot._close_position(sym, st.price, now, "manual_stop")
    bot._paused = True
    bot._save_state()
    return JSONResponse({"status": "paused"})

@app.post("/api/resume")
async def api_resume():
    bot._paused = False
    bot._last_scan = 0
    return JSONResponse({"status": "resumed"})

@app.post("/api/reset")
async def api_reset():
    now = datetime.now(timezone.utc)
    for sym in list(bot.positions.keys()):
        st = bot.states.get(sym)
        if st and st.price > 0:
            bot._close_position(sym, st.price, now, "reset")
    bot._total_pnl = 0.0
    bot._wins = 0
    bot.trades.clear()
    bot._paused = False
    if os.path.exists(TRADES_CSV):
        os.rename(TRADES_CSV, TRADES_CSV + f".bak.{int(time.time())}")
    return JSONResponse({"status": "reset"})


# ── Main ─────────────────────────────────────────────────────────────

async def run():
    bot.running = True
    bot.started_at = datetime.now(timezone.utc)
    bot._shutdown_event = asyncio.Event()

    bot._load_trades()
    bot._load_state()

    def _sig(sig, frame):
        log.info("Shutdown signal")
        bot.running = False
        if bot._shutdown_event:
            bot._shutdown_event.set()
        # _save_state called after event.wait() in run(), not here (avoid I/O in signal handler)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("Multi-Signal Bot v%s | $%.0f capital | %dx leverage | %d symbols | port %d",
             VERSION, CAPITAL_USDT, LEVERAGE, len(TRADE_SYMBOLS), WEB_PORT)
    log.info("Sizing: %d%%+%d%% base, z-weighted | S1=$%.0f S2=$%.0f S4=$%.0f S5=$%.0f S8=$%.0f (at $%.0f)",
             SIZE_PCT * 100, SIZE_BONUS * 100,
             strat_size("S1", CAPITAL_USDT), strat_size("S2", CAPITAL_USDT),
             strat_size("S4", CAPITAL_USDT), strat_size("S5", CAPITAL_USDT),
             strat_size("S8", CAPITAL_USDT), CAPITAL_USDT)
    log.info("Hold: %dh (S5: %dh, S8: %dh) | Stop: %d bps (S8: %d) | Lev: %.0fx | Max: %d pos / %d dir / %d sect",
             HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8,
             STOP_LOSS_BPS, STOP_LOSS_S8, LEVERAGE, MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR)
    log.info("Kill-switch: loss cap $%.0f | streak threshold %d → %.0f%% sizing for %dh",
             DAILY_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER * 100, LOSS_STREAK_COOLDOWN // 3600)

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)

    tasks = [
        asyncio.create_task(bot.main_loop()),
        asyncio.create_task(server.serve()),
    ]

    await bot._shutdown_event.wait()
    bot.running = False
    bot._save_state()
    for t in tasks:
        t.cancel()
    log.info("Shutdown | P&L $%.2f | %d trades", bot._total_pnl, len(bot.trades))


if __name__ == "__main__":
    asyncio.run(run())
