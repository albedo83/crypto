"""Multi-Signal Bot v10.3.4 — Five strategies + DXY filter + 2x leverage + OI observation.

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

VERSION = "10.3.4"

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

# ── Hold Periods ─────────────────────────────────────────────────────
# Optimized via parameter sweep in backtest_boost.py.
# Shorter holds lose edge to costs; longer holds add drawdown without profit.
HOLD_HOURS_DEFAULT = 72   # 3 days (18 × 4h candles) for S1, S2, S4
HOLD_HOURS_S5 = 48        # 2 days — sector divergences revert faster

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

# ── S5 Sector Divergence Params ───────────────────────────────────────
# Token must lag/lead its sector by >10% AND have elevated volume.
# Volume filter prevents entry on illiquid drift.
S5_DIV_THRESHOLD = 1000   # 10% divergence from sector
S5_VOL_Z_MIN = 1.0        # minimum volume z-score to confirm

# S6 REMOVED — z=8.04 in isolation but LOSES in portfolio (-$627 to -$1,552)
# Standalone backtest was misleading (simpler backtester, no position limits)

# ── S8 Capitulation Flush Params ──────────────────────────────────────
# Catches market-wide liquidation cascades: token down >40% from 30d high,
# volume spiking (forced sells), still bleeding, AND BTC also weak.
# The 4th condition (BTC weak) raised z from 5.2 to 6.99 — see backtest_deep_s8.py.
S8_DRAWDOWN_THRESH = -4000   # -40% from 30d high
S8_VOL_Z_MIN = 1.0           # volume spike confirmation
S8_RET_24H_THRESH = -50      # still bleeding (24h return < -0.5%, 6 candles × 4h)
S8_BTC_7D_THRESH = -300      # BTC also weak (7d < -3%)
HOLD_HOURS_S8 = 60           # 60h hold (15 candles)

# ── DXY Filter (critical for S4) ─────────────────────────────────────
# S4 shorts only fire when the dollar is rising. Without DXY gating,
# S4 shorts in bull markets and loses. See CLAUDE.md "Gotchas" section.
DXY_CACHE = os.path.join(os.path.dirname(__file__), "output", "pairs_data", "macro_DXY.json")
DXY_BOOST_THRESHOLD = 100  # DXY 7d > +1% → S4 active

# ── Leverage & Sizing ────────────────────────────────────────────────
# 2x optimal from parameter sweep (3x = ruin from compounding losses).
LEVERAGE = 2.0

# Sizing: compounding base + bonus, z-weighted so higher-z signals get bigger bets.
# Formula in strat_size(): base * (z/4 clamped 0.5-2.0) * haircut.
SIZE_PCT = 0.12           # 12% of current capital per position (reduced from 15%)
SIZE_BONUS = 0.03         # bonus for high-z signals (z > 4)
STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67, "S8": 6.99}
LIQUIDITY_HAIRCUT = {"S8": 0.8}  # S8 fires during thin/stressed markets

# ── Capital & Position Limits ────────────────────────────────────────
CAPITAL_USDT = 1000.0
MAX_POSITIONS = 6
MAX_SAME_DIRECTION = 4    # prevents all-long or all-short concentration
MAX_PER_SECTOR = 2        # prevents overexposure to correlated tokens

# ── Costs (applied at exit, per leg, scaled by leverage) ─────────────
TAKER_FEE_BPS = 7.0
SLIPPAGE_BPS = 3.0
FUNDING_DRAG_BPS = 2.0
COST_BPS = TAKER_FEE_BPS + SLIPPAGE_BPS + FUNDING_DRAG_BPS  # 12 bps

# ── Stop Losses (leveraged bps — catastrophe guards, not profit-taking) ──
# Backtest showed "no stop" is best for profit, but these prevent tail risk.
STOP_LOSS_BPS = -2500.0        # default: -25% leveraged = -12.5% price move
STOP_LOSS_S8 = -1500.0         # S8 tighter: -15% leveraged = -7.5% price move

# ── Portfolio Kill-Switch ────────────────────────────────────────────
# Protects against regime change or systematic signal failure.
TOTAL_LOSS_CAP = -300.0        # auto-pause if cumulative P&L drops below this
LOSS_STREAK_THRESHOLD = 3      # reduce sizing after N consecutive losses
LOSS_STREAK_MULTIPLIER = 0.5   # halve position size during loss streak
LOSS_STREAK_COOLDOWN = 24 * 3600  # 24h before returning to normal sizing

# Timing
SCAN_INTERVAL = 3600      # check signals every hour (candles are 4h)
COOLDOWN_HOURS = 24       # 24h cooldown per symbol after exit

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRADES_CSV = os.path.join(OUTPUT_DIR, "reversal_trades.csv")
MARKET_CSV = os.path.join(OUTPUT_DIR, "reversal_market.csv")
STATE_FILE = os.path.join(OUTPUT_DIR, "reversal_state.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "reversal.html")
WEB_PORT = 8097


def strat_size(strat_name: str, capital: float) -> float:
    """Compute position size: base% * z-weight * haircut.

    z-weight (z/4 clamped to [0.5, 2.0]) allocates more capital to
    statistically stronger signals (S1/S8 get ~1.6x, S4 gets ~0.7x).
    Haircut reduces size for strategies that fire in illiquid conditions.
    """
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
    strategy: str            # S1, S2, S4
    entry_price: float
    entry_time: datetime
    size_usdt: float
    signal_info: str         # human-readable signal description
    target_exit: datetime
    mae_bps: float = 0.0    # Max Adverse Excursion (worst unrealized during trade)
    mfe_bps: float = 0.0    # Max Favorable Excursion (best unrealized during trade)
    trajectory: list = field(default_factory=list)  # [(hours_since_entry, unrealized_bps), ...] capped at 200


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


class MultiSignalBot:
    def __init__(self):
        self.states: dict[str, SymbolState] = {s: SymbolState() for s in ALL_SYMBOLS}
        self.positions: dict[str, Position] = {}
        self.trades: deque[Trade] = deque(maxlen=500)
        self.running = False
        self._paused = False
        # Feature cache: computed once per hourly scan, consumed by signals + dashboard.
        # Empty until first scan completes — dashboard may show stale data until then.
        self._feature_cache: dict[str, dict | None] = {}
        self._oi_summary: dict = {"falling": 0, "rising": 0}
        self._shutdown_event: asyncio.Event | None = None
        self.started_at: datetime | None = None
        # Cumulative stats (persisted across restarts via state file)
        self._total_pnl = 0.0
        self._wins = 0
        self._last_scan: float = 0
        self._last_price_fetch: float = 0
        self._cooldowns: dict[str, float] = {}  # symbol → earliest re-entry epoch
        self._degraded: list[str] = []           # active degradation tags (e.g. "DXY", "DXY_STALE")
        self._consecutive_losses = 0
        self._signal_first_seen: dict[str, float] = {}  # "S2:ARB" → epoch when first detected
        self._loss_streak_until: float = 0       # epoch when streak penalty expires

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
                    oi = float(ctxs[i].get("openInterest") or 0)
                    if oi > 0:
                        st.oi = oi
                        st.oi_history.append((now, oi))
                    st.funding = float(ctxs[i].get("funding") or 0)
                    st.premium = float(ctxs[i].get("premium") or 0)
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
        """Compute technical features for a single symbol from its 4h candles.

        All returns/drawdowns are in basis points (1 bps = 0.01%).
        Candle counts: 6 = 24h, 42 = 7d, 180 = 30d (at 4h per candle).
        """
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
        # 7-day return (42 candles × 4h) — used by S1, S2, S5
        if i >= 42 and closes[i - 42] > 0:
            f["ret_42h"] = (closes[i] / closes[i - 42] - 1) * 1e4
        else:
            return None

        # Volatility ratio: vol_7d / vol_30d — below 1.0 = compression (used by S4)
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

        # Return over 6 candles = 24 hours (needed for S8)
        if i >= 6 and closes[i - 6] > 0:
            f["ret_24h"] = (closes[i] / closes[i - 6] - 1) * 1e4
        else:
            f["ret_24h"] = 0

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
        """Compute OI delta as % change over 1h/4h from live 60s samples.
        Percentage (not absolute) normalizes across tokens with different OI levels.
        Observation only — not used for signal decisions yet."""
        st = self.states.get(symbol)
        if not st or len(st.oi_history) < 30:  # need ~30min of data for meaningful delta
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

    def _compute_crowding_score(self, symbol: str, oi_f: dict | None = None) -> int:
        """Score 0-100 measuring leverage stress / flush quality.

        Higher = more likely a genuine liquidation flush (good for S2/S8).
        Lower = simple price decline without deleveraging.
        Components: OI dropping (max 50) + negative funding (20) + vol spike (15) + negative premium (15).
        Observation only — not used for signal decisions yet.
        """
        score = 0
        if oi_f is None:
            oi_f = self._compute_oi_features(symbol)
        st = self.states.get(symbol)
        f = self._get_cached_features(symbol)

        # OI dropping = positions closing = deleveraging
        d1h = oi_f["oi_delta_1h"]
        if d1h < -1.0:
            score += 30
        if d1h < -3.0:
            score += 20

        # Funding very negative = shorts overcrowded, squeeze potential
        if st and st.funding < -0.00005:  # -0.005%
            score += 20

        # Volume spike = stress
        if f and f.get("vol_z", 0) > 1.5:
            score += 15

        # Premium negative = perp trading below oracle = forced selling
        if st and st.premium < -0.0005:  # -0.05%
            score += 15

        return min(100, score)

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
        """Fetch DXY 7-day return (bps) via Yahoo Finance with 3-tier fallback:
        1. Fresh cache (<6h) — normal operation
        2. Stale cache (6-48h) — S4 stays active, dashboard shows warning
        3. No data (>48h) — S4 disabled, returns 0.0
        """
        def _read_cache() -> tuple[float | None, float]:
            """Returns (dxy_bps or None, age_hours)."""
            if not os.path.exists(DXY_CACHE):
                return None, 999
            age_h = (time.time() - os.path.getmtime(DXY_CACHE)) / 3600
            try:
                with open(DXY_CACHE) as f:
                    daily = json.load(f)
                if len(daily) >= 10:
                    closes = [d["c"] for d in daily[-10:]]
                    if closes[-6] > 0:
                        return (closes[-1] / closes[-6] - 1) * 1e4, age_h
            except Exception:
                pass
            return None, age_h

        # 1. Try fresh cache (< 6h)
        cached, age_h = _read_cache()
        if cached is not None and age_h < 6:
            # Clear any degraded state
            for tag in ["DXY", "DXY_STALE"]:
                if tag in self._degraded:
                    self._degraded.remove(tag)
            return cached

        # 2. Try fresh fetch from Yahoo Finance
        try:
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
            if daily and len(daily) >= 6:
                os.makedirs(os.path.dirname(DXY_CACHE), exist_ok=True)
                with open(DXY_CACHE, "w") as f:
                    json.dump(daily, f)
                for tag in ["DXY", "DXY_STALE"]:
                    if tag in self._degraded:
                        self._degraded.remove(tag)
                return (daily[-1]["c"] / daily[-6]["c"] - 1) * 1e4
        except Exception as e:
            log.warning("DXY fetch failed: %s", e)

        # 3. Stale cache fallback (6h-48h)
        if cached is not None and age_h < 48:
            if "DXY_STALE" not in self._degraded:
                self._degraded.append("DXY_STALE")
            if "DXY" in self._degraded:
                self._degraded.remove("DXY")
            log.warning("DXY stale (%.0fh old) — using cached value", age_h)
            return cached

        # 4. No data at all — S4 disabled
        if "DXY" not in self._degraded:
            self._degraded.append("DXY")
        if "DXY_STALE" in self._degraded:
            self._degraded.remove("DXY_STALE")
        log.warning("DXY unavailable (cache >48h or missing) — S4 disabled")
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

        own_f = self._get_cached_features(symbol) or self._compute_features(symbol)
        if not own_f or "ret_42h" not in own_f:
            return None

        # Compute sector mean excluding self
        peers = SECTORS[sector]
        peer_rets = []
        for peer in peers:
            if peer == symbol:
                continue
            pf = self._get_cached_features(peer) or self._compute_features(peer)
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
        # OI summary (avoid recomputing per dashboard poll)
        falling = rising = 0
        for sym in TRADE_SYMBOLS:
            d = self._compute_oi_features(sym)["oi_delta_1h"]
            if d < -0.5:
                falling += 1
            elif d > 0.5:
                rising += 1
        self._oi_summary = {"falling": falling, "rising": rising}

    def _get_cached_features(self, symbol: str) -> dict | None:
        return self._feature_cache.get(symbol)

    # ── Signal Detection ─────────────────────────────────────────

    def _scan_signals(self) -> int:
        """Scan all 5 strategies across 28 tokens, rank by z-score, and open positions."""
        now = datetime.now(timezone.utc)
        signals = []

        # Compute global features
        btc_f = self._compute_btc_features()
        alt_index = self._compute_alt_index()
        dxy_7d = self._fetch_dxy()

        btc_30d = btc_f.get("btc_30d", 0)

        # Cross-sectional context (logged, not used for decisions)
        # Captures the "shape of the market" around each signal for future analysis
        stress_by_sector = defaultdict(int)
        n_stress_global = 0
        _all_ret24h = []
        _all_ret7d = []
        for _sym in TRADE_SYMBOLS:
            _f = self._get_cached_features(_sym)
            if not _f:
                continue
            if _f.get("vol_z", 0) > 1.5 and _f.get("drawdown", 0) < -1500:
                n_stress_global += 1
                _sect = TOKEN_SECTOR.get(_sym)
                if _sect:
                    stress_by_sector[_sect] += 1
            _all_ret24h.append(_f.get("ret_24h", 0))
            _all_ret7d.append(_f.get("ret_42h", 0))
        # Dispersion = how scattered the basket is (std of returns across all alts)
        disp_24h = round(float(np.std(_all_ret24h)), 0) if _all_ret24h else 0
        disp_7d = round(float(np.std(_all_ret7d)), 0) if _all_ret7d else 0

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

            # OI + crowding + stress context (observation — logged, not used for decisions)
            oi_f = self._compute_oi_features(sym)
            crowd = self._compute_crowding_score(sym, oi_f=oi_f)
            sym_sector = TOKEN_SECTOR.get(sym, "?")
            sect_stress = stress_by_sector.get(sym_sector, 0)
            # Shock ratio: how much of the 30d move happened in last 24h (0=slow drift, 1=flash crash)
            dd = abs(f.get("drawdown", 0))
            shock = round(abs(f.get("ret_24h", 0)) / dd, 2) if dd > 100 else 0
            # Move cleanliness: last candle range vs 24h move (high=hysteric, low=clean)
            r24 = abs(f.get("ret_24h", 0))
            clean = round(f.get("range_pct", 0) / r24, 2) if r24 > 50 else 0
            # Sector leadership: top token return / sector avg (>2 = concentrated leader, ~1 = unanimous)
            _sect = TOKEN_SECTOR.get(sym)
            _peers = [self._get_cached_features(p) for p in SECTORS.get(_sect, []) if p != sym] if _sect else []
            _peer_rets = [abs(pf.get("ret_42h", 0)) for pf in _peers if pf]
            _peer_avg = np.mean(_peer_rets) if _peer_rets else 0
            lead = round(abs(f.get("ret_42h", 0)) / _peer_avg, 1) if _peer_avg > 100 else 0
            # Confluence: how many features are simultaneously extreme (0-5)
            # Tests whether "too obvious" setups are better or already crowded
            conf = sum([
                abs(f.get("drawdown", 0)) > 3000,   # deep drawdown
                f.get("vol_z", 0) > 1.5,             # volume spike
                abs(f.get("ret_24h", 0)) > 200,      # big recent move
                n_stress_global >= 5,                 # broad panic
                oi_f["oi_delta_1h"] < -1.0,           # OI dropping
            ])
            # Time bucket: Asia(0-8) / EU(8-14) / US(14-21) / Night(21-24) + weekend
            _h = now.hour
            _session = "Asia" if _h < 8 else "EU" if _h < 14 else "US" if _h < 21 else "Night"
            if now.weekday() >= 5:
                _session = "WE"  # weekend
            oi_tag = f" OI1h={oi_f['oi_delta_1h']:+.1f}% CS={crowd} str={n_stress_global}/{sect_stress} disp={disp_24h:.0f}/{disp_7d:.0f} shk={shock:.2f} cln={clean:.1f} lead={lead:.1f} conf={conf} ses={_session}"

            # S1: BTC momentum spills over to alts — when BTC rallies >20%/30d,
            # altcoins follow with a lag. Rare but high-conviction (z=6.42).
            # Token ranking: alts already moving up get priority (backtest: +60% P&L vs random).
            # "Laggards first" was tested and performs WORSE — buy the wave, not the furniture.
            if btc_30d > 2000:
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S1",
                    "z": STRAT_Z["S1"],
                    "info": f"BTC 30d={btc_30d:+.0f}bps{oi_tag}",
                    "strength": max(f.get("ret_42h", 0), 0),  # momentum: alts already up first
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S2: Mean-reversion after alt crash — when the alt index drops >10%/7d,
            # the market is oversold and bounces. Classic "buy the dip" (z=4.00).
            if alt_index < -1000:
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S2",
                    "z": STRAT_Z["S2"],
                    "info": f"AltIdx={alt_index:+.0f}bps{oi_tag}",
                    "strength": abs(alt_index),
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S4: Volatility compression + rising dollar → SHORT. Quiet markets
            # with a strengthening USD precede alt drawdowns. DXY filter is critical:
            # without it, S4 shorts in bull markets and loses (see CLAUDE.md).
            if f["vol_ratio"] < 1.0 and f["range_pct"] < 200 and dxy_7d > DXY_BOOST_THRESHOLD:
                signals.append({
                    "symbol": sym, "direction": -1, "strategy": "S4",
                    "z": STRAT_Z["S4"],
                    "info": f"VolR={f['vol_ratio']:.2f} Rng={f['range_pct']:.0f} DXY={dxy_7d:+.0f}{oi_tag}",
                    "strength": (1.0 - f["vol_ratio"]) * 1000,
                    "hold_hours": HOLD_HOURS_DEFAULT,
                })

            # S5: Sector breakout — when a token diverges >10% from its sector peers
            # with high volume, FOLLOW the divergence (don't fade it). Backtested both
            # directions in backtest_sector.py: follow works, fade doesn't.
            sd = self._compute_sector_divergence(sym)
            if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
                direction = 1 if sd["divergence"] > 0 else -1
                signals.append({
                    "symbol": sym, "direction": direction, "strategy": "S5",
                    "z": STRAT_Z["S5"],
                    "info": f"{sd['sector']} div={sd['divergence']:+.0f} vz={sd['vol_z']:.1f}{oi_tag}",
                    "strength": abs(sd["divergence"]),
                    "hold_hours": HOLD_HOURS_S5,
                })

            # S8: Capitulation flush — buy when market-wide liquidation cascade is
            # underway. All 4 conditions must align: extreme drawdown, volume spike
            # (forced sells), still bleeding (not recovering yet), AND BTC weak.
            # Highest z-score of all signals (6.99), 70% win rate, rare (~1/month).
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                signals.append({
                    "symbol": sym, "direction": 1, "strategy": "S8",
                    "z": STRAT_Z["S8"],
                    "info": f"DD={f['drawdown']:.0f} vz={f['vol_z']:.1f} r6h={f['ret_24h']:.0f} BTC7d={btc_f.get('btc_7d',0):+.0f}{oi_tag}",
                    "strength": abs(f["drawdown"]),
                    "hold_hours": HOLD_HOURS_S8,
                })

            # S6 REMOVED — loses in portfolio despite z=8.04 in isolation

        # Track signal age + retest detection (observation only)
        now_ts = time.time()
        current_keys = set()
        for sig in signals:
            key = f"{sig['strategy']}:{sig['symbol']}"
            current_keys.add(key)
            prev = self._signal_first_seen.get(key)
            if prev is None:
                # Brand new signal
                self._signal_first_seen[key] = now_ts
                age_h = 0
                retest = 0
            elif prev < 0:
                # Was gone (negative = epoch when it disappeared), now back = retest
                self._signal_first_seen[key] = now_ts
                age_h = 0
                retest = 1
            else:
                # Still active
                age_h = (now_ts - prev) / 3600
                retest = 0
            sig["info"] += f" age={age_h:.0f}h rt={retest}"
        # Mark disappeared signals (keep for 7 days to detect retests)
        for k in list(self._signal_first_seen.keys()):
            if k not in current_keys:
                if self._signal_first_seen[k] > 0:
                    # Just disappeared — mark with negative timestamp
                    self._signal_first_seen[k] = -now_ts
                elif now_ts - abs(self._signal_first_seen[k]) > 7 * 86400:
                    # Gone for >7 days — prune
                    del self._signal_first_seen[k]

        # ── Signal Ranking & Position Entry ──────────────────────────
        # Priority: highest z-score first (strongest statistical edge), then
        # by signal strength within same z. This ensures S1/S8 get slots
        # before S4 when multiple signals fire simultaneously.
        signals.sort(key=lambda s: (s["z"], s["strength"]), reverse=True)

        n_longs = sum(1 for p in self.positions.values() if p.direction == 1)
        n_shorts = sum(1 for p in self.positions.values() if p.direction == -1)

        entries = 0
        seen_symbols = set()       # one entry per symbol per scan
        drift = self._compute_signal_drift()  # compute once, not per candidate
        for sig in signals:
            sym = sig["symbol"]
            side = "LONG" if sig["direction"] == 1 else "SHORT"

            if len(self.positions) >= MAX_POSITIONS:
                log.debug("SKIP %s %s %s: max_positions", sig["strategy"], side, sym)
                break

            if sym in seen_symbols:
                continue
            seen_symbols.add(sym)

            if sig["direction"] == 1 and n_longs >= MAX_SAME_DIRECTION:
                log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
                continue
            if sig["direction"] == -1 and n_shorts >= MAX_SAME_DIRECTION:
                log.debug("SKIP %s %s %s: max_direction", sig["strategy"], side, sym)
                continue

            # Sector concentration limit
            sym_sector = TOKEN_SECTOR.get(sym)
            if sym_sector:
                sector_count = sum(1 for p in self.positions.values() if TOKEN_SECTOR.get(p.symbol) == sym_sector)
                if sector_count >= MAX_PER_SECTOR:
                    log.debug("SKIP %s %s %s: max_sector (%s)", sig["strategy"], side, sym, sym_sector)
                    continue

            st = self.states[sym]
            hold_h = sig.get("hold_hours", HOLD_HOURS_DEFAULT)
            target_exit = now + timedelta(hours=hold_h)
            current_capital = CAPITAL_USDT + self._total_pnl
            size = strat_size(sig["strategy"], current_capital)
            # Loss streak penalty: protects against correlated losses
            # (e.g. flash crash hitting multiple positions simultaneously)
            if time.time() < self._loss_streak_until:
                size = round(size * LOSS_STREAK_MULTIPLIER, 2)

            # Signal health quarantine: protects against regime change making
            # a signal permanently unprofitable. If win rate on last 10 trades
            # drops below 20%, the signal is fully disabled; below 30%, sizing
            # is halved. Prevents silent degradation from draining capital.
            health = drift.get(sig["strategy"], {})
            if health.get("n", 0) >= 10:
                wr = health["win_rate"]
                if wr < 0.20:
                    log.critical("QUARANTINE: %s win rate %.0f%% on last %d trades — skipping",
                                 sig["strategy"], wr * 100, health["n"])
                    continue
                elif wr < 0.30:
                    size = round(size * 0.5, 2)
                    log.warning("DEGRADED: %s win rate %.0f%% — sizing halved",
                                sig["strategy"], wr * 100)

            # Capital exposure limit: max 90% of capital as margin
            used_margin = sum(p.size_usdt for p in self.positions.values())
            if used_margin + size > current_capital * 0.90:
                log.debug("SKIP %s %s %s: capital_exposure (%.0f+%.0f > %.0f)",
                          sig["strategy"], side, sym, used_margin, size, current_capital * 0.90)
                continue

            self.positions[sym] = Position(
                symbol=sym, direction=sig["direction"],
                strategy=sig["strategy"],
                entry_price=st.price, entry_time=now,
                size_usdt=size, signal_info=sig["info"],
                target_exit=target_exit,
                trajectory=[(0.0, 0.0)],  # t=0 anchor point
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
        """Close positions that hit timeout or stop loss. Returns count of exits."""
        now = datetime.now(timezone.utc)
        exits = 0

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            st = self.states.get(sym)
            if not st or st.price == 0:
                continue

            unrealized = pos.direction * (st.price / pos.entry_price - 1) * 1e4 * LEVERAGE

            # Track MAE/MFE + trajectory (updated every 60s via main loop)
            if unrealized < pos.mae_bps:
                pos.mae_bps = unrealized
            if unrealized > pos.mfe_bps:
                pos.mfe_bps = unrealized
            # Trajectory: record hourly snapshots (keep ~1 per hour to avoid bloat)
            hours_held = (now - pos.entry_time).total_seconds() / 3600
            last_h = pos.trajectory[-1][0] if pos.trajectory else -1
            if hours_held - last_h >= 0.95:  # ~1h interval
                if len(pos.trajectory) < 200:  # cap to prevent unbounded growth
                    pos.trajectory.append((round(hours_held, 1), round(unrealized, 1)))

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
        """Exit a position, record the trade, and update portfolio state."""
        pos = self.positions.pop(sym)
        hold_h = (now - pos.entry_time).total_seconds() / 3600
        # P&L calc: direction * price change * leverage, then subtract round-trip costs.
        # Costs scale with leverage because notional = size * leverage.
        gross_bps = pos.direction * (exit_price / pos.entry_price - 1) * 1e4 * LEVERAGE
        effective_cost = COST_BPS * LEVERAGE
        net_bps = gross_bps - effective_cost
        pnl = pos.size_usdt * net_bps / 1e4

        self._total_pnl += pnl
        if pnl > 0:
            self._wins += 1

        # Track consecutive losses — protects against correlated drawdowns.
        # After N losses in a row, halve sizing for 24h to limit damage.
        if pnl > 0:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            if self._consecutive_losses >= LOSS_STREAK_THRESHOLD:
                self._loss_streak_until = time.time() + LOSS_STREAK_COOLDOWN
                log.warning("Loss streak: %d consecutive losses — sizing reduced for 24h",
                             self._consecutive_losses)

        # Kill-switch: if total P&L breaches cap, stop all trading.
        # Protects against catastrophic regime change (e.g. all signals broken at once).
        if self._total_pnl <= TOTAL_LOSS_CAP:
            self._paused = True
            log.critical("KILL-SWITCH: P&L $%.2f below cap $%.0f — auto-paused",
                         self._total_pnl, TOTAL_LOSS_CAP)

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
            pnl_usdt=round(pnl, 2),
            mae_bps=round(pos.mae_bps, 1), mfe_bps=round(pos.mfe_bps, 1),
            reason=reason,
        )
        self.trades.append(trade)
        self._write_csv(trade)
        self._write_trajectory(sym, pos)

        n = len(self.trades)
        balance = CAPITAL_USDT + self._total_pnl
        wr = self._wins / n * 100 if n > 0 else 0
        arrow = "✓" if pnl > 0 else "✗"
        log.info("%s %s %s %s | %.0fh | %s | gross %+.1f | net %+.1f | $%+.2f | mae %+.0f | mfe %+.0f | bal $%.0f (#%d %.0f%%)",
                 arrow, pos.strategy, trade.direction, sym, hold_h, reason,
                 gross_bps, net_bps, pnl, pos.mae_bps, pos.mfe_bps, balance, n, wr)

    def _write_csv(self, t: Trade):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(TRADES_CSV)
        try:
            with open(TRADES_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if header:
                    w.writerow(["symbol", "direction", "strategy", "entry_time", "exit_time",
                               "entry_price", "exit_price", "hold_hours", "size_usdt",
                               "signal_info", "gross_bps", "net_bps", "pnl_usdt",
                               "mae_bps", "mfe_bps", "reason"])
                w.writerow([t.symbol, t.direction, t.strategy, t.entry_time, t.exit_time,
                           t.entry_price, t.exit_price, t.hold_hours, t.size_usdt,
                           t.signal_info, t.gross_bps, t.net_bps, t.pnl_usdt,
                           t.mae_bps, t.mfe_bps, t.reason])
        except Exception:
            log.exception("Trade CSV write failed — trade recorded in memory but not on disk")

    def _write_trajectory(self, sym: str, pos: Position):
        """Write hourly trajectory to CSV. One row per hour of the trade's life."""
        if not pos.trajectory:
            return
        traj_csv = os.path.join(OUTPUT_DIR, "reversal_trajectories.csv")
        header = not os.path.exists(traj_csv)
        try:
            with open(traj_csv, "a", newline="") as f:
                w = csv.writer(f)
                if header:
                    w.writerow(["symbol", "strategy", "entry_time", "hours", "unrealized_bps"])
                entry_t = pos.entry_time.isoformat(timespec="seconds")
                for hours, bps in pos.trajectory:
                    w.writerow([sym, pos.strategy, entry_t, hours, bps])
        except Exception:
            log.exception("Trajectory write failed")

    def _log_market_snapshot(self):
        """Append hourly snapshot of OI/funding/premium/crowding for all tokens to CSV.

        This data is lost on restart (rolling deques). Persisting it enables
        future analysis of OI delta vs trade quality without needing a database.
        """
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        header = not os.path.exists(MARKET_CSV)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with open(MARKET_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if header:
                    w.writerow(["timestamp", "symbol", "price", "oi", "oi_delta_1h_pct",
                                "funding_ppm", "premium_ppm", "crowding", "vol_z"])
                for sym in TRADE_SYMBOLS:
                    st = self.states.get(sym)
                    if not st or st.price == 0:
                        continue
                    oi_f = self._compute_oi_features(sym)
                    feat = self._get_cached_features(sym)
                    crowd = self._compute_crowding_score(sym, oi_f=oi_f)
                    w.writerow([ts, sym, round(st.price, 6), round(st.oi, 2),
                                oi_f["oi_delta_1h"], round(st.funding * 1e6, 2),
                                round(st.premium * 1e6, 2), crowd,
                                round(feat.get("vol_z", 0), 2) if feat else 0])
        except Exception:
            log.exception("Market snapshot write failed")

    # ── Persistence ─────────────────────────────────────────────

    def _save_state(self):
        """Atomically persist bot state (write to .tmp then os.replace)."""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = {
            "version": VERSION,
            "total_pnl": self._total_pnl, "wins": self._wins,
            "paused": self._paused,
            "consecutive_losses": self._consecutive_losses,
            "loss_streak_until": self._loss_streak_until,
            "cooldowns": {k: v for k, v in self._cooldowns.items() if v > time.time()},
            "signal_first_seen": self._signal_first_seen,
            "positions": [{
                "symbol": p.symbol, "direction": p.direction,
                "strategy": p.strategy,
                "entry_price": p.entry_price, "entry_time": p.entry_time.isoformat(),
                "size_usdt": p.size_usdt, "signal_info": p.signal_info,
                "target_exit": p.target_exit.isoformat(),
                "mae_bps": p.mae_bps, "mfe_bps": p.mfe_bps,
                "trajectory": p.trajectory,
            } for p in self.positions.values()],
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(orjson.dumps(data))
        os.replace(tmp, STATE_FILE)  # atomic on POSIX

    def _load_state(self):
        """Restore positions + P&L from disk. Keeps .loaded backup for debugging."""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "rb") as f:
                data = orjson.loads(f.read())
            self._total_pnl = data.get("total_pnl", 0)
            self._wins = data.get("wins", 0)
            self._paused = data.get("paused", False)
            self._consecutive_losses = data.get("consecutive_losses", 0)
            self._loss_streak_until = data.get("loss_streak_until", 0)
            self._cooldowns = data.get("cooldowns", {})
            self._signal_first_seen = data.get("signal_first_seen", {})
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
                    mae_bps=p.get("mae_bps", 0.0),
                    mfe_bps=p.get("mfe_bps", 0.0),
                    trajectory=p.get("trajectory", []),
                )
            if self.positions or self._total_pnl:
                log.info("Restored: %d positions, P&L $%.2f", len(self.positions), self._total_pnl)
            # Keep backup but don't remove original — next _save_state() overwrites it
            shutil.copy2(STATE_FILE, STATE_FILE + ".loaded")
        except Exception:
            log.exception("Load state failed")

    def _load_trades(self):
        """Reload trade history from CSV (needed for drift computation and dashboard)."""
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
                        mae_bps=float(row.get("mae_bps", 0)),
                        mfe_bps=float(row.get("mfe_bps", 0)),
                        reason=row["reason"],
                    ))
            if self.trades:
                log.info("Loaded %d historical trades", len(self.trades))
        except Exception:
            log.exception("Load trades failed")

    # ── API ─────────────────────────────────────────────────────

    def _compute_signal_drift(self) -> dict:
        """Rolling stats per signal on last 20 trades. Used by quarantine logic
        and exposed via /api/state for monitoring. Detects silent degradation."""
        by_strat: dict[str, list] = defaultdict(list)
        for t in self.trades:
            by_strat[t.strategy].append(t)
        result = {}
        for strat, trades in by_strat.items():
            recent = trades[-20:]
            if recent:
                result[strat] = {
                    "n": len(recent),
                    "win_rate": round(sum(1 for t in recent if t.pnl_usdt > 0) / len(recent), 2),
                    "avg_bps": round(sum(t.net_bps for t in recent) / len(recent), 1),
                    "total_pnl": round(sum(t.pnl_usdt for t in recent), 2),
                }
        return result

    def get_state(self) -> dict:
        """Build full dashboard state: balance, positions, signals, timing, drift."""
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
                "mae_bps": round(pos.mae_bps, 1),
                "mfe_bps": round(pos.mfe_bps, 1),
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
                           if (f := self._get_cached_features(sym) or self._compute_features(sym)) and
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
                    and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                s8_syms.append(sym)
        if s8_syms:
            active_signals.append(f"S8: {', '.join(s8_syms[:5])} capitulation flush")
        return {
            "version": VERSION, "strategy": "Multi-Signal (S1+S2+S4+S5+S8)",
            "paused": self._paused, "running": self.running,
            "degraded": list(self._degraded),
            "loss_streak": self._consecutive_losses,
            "kill_switch_active": self._total_pnl <= TOTAL_LOSS_CAP,
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
                "oi_falling": self._oi_summary["falling"],
                "oi_rising": self._oi_summary["rising"],
            },
            "params": {"hold_h": HOLD_HOURS_DEFAULT, "hold_s5_h": HOLD_HOURS_S5,
                       "cost_bps": COST_BPS, "stop_bps": STOP_LOSS_BPS,
                       "max_pos": MAX_POSITIONS},
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0,
            "last_price_s": time.time() - self._last_price_fetch if self._last_price_fetch else None,
            "last_scan_s": time.time() - self._last_scan if self._last_scan else None,
            "next_scan_s": max(0, SCAN_INTERVAL - (time.time() - self._last_scan)) if self._last_scan else 0,
            "scan_interval": SCAN_INTERVAL,
            "signal_drift": self._compute_signal_drift(),
        }

    def get_signals(self) -> dict:
        """All symbols with their current features and signal status."""
        btc_f = self._compute_btc_features()
        alt_idx = self._compute_alt_index()
        dxy_val = self._fetch_dxy()  # once, not 28 times
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
                    and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                triggered.append("S8:LONG")

            oi_f = self._compute_oi_features(sym)
            crowd = self._compute_crowding_score(sym, oi_f=oi_f)
            signals[sym] = {
                "price": st.price,
                "ret_7d_bps": round(f.get("ret_42h", 0), 1),  # ret_42h = 42 candles × 4h = 7 days
                "vol_ratio": round(f.get("vol_ratio", 0), 2),
                "range_bps": round(f.get("range_pct", 0), 0),
                "sector": TOKEN_SECTOR.get(sym, "?"),
                "sector_div": round(sd["divergence"], 0) if sd else 0,
                "oi_delta_1h": oi_f["oi_delta_1h"],
                "funding_bps": oi_f["funding_bps"],
                "crowding": crowd,
                "triggered": triggered,
                "in_position": sym in self.positions,
                "position_strategy": self.positions[sym].strategy if sym in self.positions else None,
            }
        return {"signals": signals, "btc_30d": round(btc_f.get("btc_30d", 0), 0),
                "alt_index": round(alt_idx, 0)}

    def get_trades_list(self, limit=50) -> list:
        """Return recent trades, newest first. Must convert deque to list first (no slicing)."""
        trades = list(self.trades)
        return [t.__dict__ for t in trades[-limit:][::-1]]

    def get_pnl_curve(self) -> list:
        """Cumulative P&L curve for the dashboard chart."""
        cum = 0.0
        pts = []
        for t in self.trades:
            cum += t.pnl_usdt
            pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                        "balance": round(CAPITAL_USDT + cum, 2)})
        return pts

    # ── Main Loop ───────────────────────────────────────────────

    async def main_loop(self):
        """Two cadences: prices every 60s (for stop checks), full scan every hour."""
        while self.running:
            try:
                now = time.time()

                # Always fetch prices (60s) — needed for stop loss checks between scans
                await asyncio.to_thread(self._fetch_prices)
                self._last_price_fetch = time.time()

                # Hourly: fetch candles + recompute features + scan signals + open/close
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
                    self._log_market_snapshot()

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
                    # Between scans: only check exits (stop losses can trigger any minute)
                    exits = self._check_exits()
                    if exits:
                        self._save_state()

            except Exception:
                log.exception("Loop error")

            await asyncio.sleep(60)


# ── FastAPI ──────────────────────────────────────────────────────────

bot = MultiSignalBot()
app = FastAPI()
_html_cache = None  # HTML cached in memory on first request — restart bot to pick up HTML changes


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
    bot._last_scan = 0  # force immediate scan on next loop iteration
    bot._save_state()   # persist unpaused state — crash after resume won't reload paused=True
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
    bot._consecutive_losses = 0
    bot._loss_streak_until = 0
    bot._cooldowns.clear()
    bot.trades.clear()
    bot._paused = False
    bot._degraded.clear()
    bot._feature_cache.clear()
    bot._oi_summary = {"falling": 0, "rising": 0}
    bot._last_scan = 0  # force immediate rescan
    if os.path.exists(TRADES_CSV):
        os.rename(TRADES_CSV, TRADES_CSV + f".bak.{int(time.time())}")
    bot._save_state()  # persist clean state — prevents reload of old data on restart
    log.info("RESET: capital $%.0f, all state cleared", CAPITAL_USDT)
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
        # State saved after event.wait() below, not here (signal handlers must avoid I/O)

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
             TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD, LOSS_STREAK_MULTIPLIER * 100, LOSS_STREAK_COOLDOWN // 3600)

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
