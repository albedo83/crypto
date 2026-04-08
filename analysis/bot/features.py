"""Feature computation — technical indicators, OI, crowding, macro.

All pure functions: receive data as arguments, return computed values.
Extracted from the original reversal.py methods on the bot class.
"""

from __future__ import annotations

import json
import logging
import os
import time

import numpy as np

from .config import (TRADE_SYMBOLS, SECTORS, TOKEN_SECTOR, DXY_CACHE,
                     S5_DIV_THRESHOLD, S5_VOL_Z_MIN)
from .net import http_fetch

log = logging.getLogger("multisignal")


def compute_features(candles: list) -> dict | None:
    """Compute technical features for a single symbol from its 4h candles.

    All returns/drawdowns are in basis points (1 bps = 0.01%).
    Candle counts: 6 = 24h, 42 = 7d, 180 = 30d (at 4h per candle).
    """
    if len(candles) < 50:
        return None

    n = len(candles)
    i = n - 1  # latest candle

    closes = np.array([c["c"] for c in candles])
    highs = np.array([c["h"] for c in candles])

    f = {}
    # 7-day return (42 candles x 4h) -- used by S1, S5
    if i >= 42 and closes[i - 42] > 0:
        f["ret_42h"] = (closes[i] / closes[i - 42] - 1) * 1e4
    else:
        return None

    # Volatility ratio: vol_7d / vol_30d -- below 1.0 = compression (used by S10)
    if i >= 42:
        denom_7d = closes[max(0, i - 42):i]
        if (denom_7d == 0).any():
            return None
        rets_7d = np.diff(closes[max(0, i - 42):i + 1]) / denom_7d
        f["vol_7d"] = float(np.std(rets_7d) * 1e4) if len(rets_7d) > 1 else 0
    else:
        f["vol_7d"] = 0

    if i >= 180:
        denom_30d = closes[i - 180:i]
        if (denom_30d == 0).any():
            return None
        rets_30d = np.diff(closes[i - 180:i + 1]) / denom_30d
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
    high_30d = float(np.max(highs[max(0, i - 180):i + 1]))
    f["drawdown"] = (closes[i] / high_30d - 1) * 1e4 if high_30d > 0 else 0

    # Return over 6 candles = 24 hours (needed for S8)
    if i >= 6 and closes[i - 6] > 0:
        f["ret_24h"] = (closes[i] / closes[i - 6] - 1) * 1e4
    else:
        f["ret_24h"] = 0

    # Volume z-score (needed for S5, S8)
    volumes = np.array([c["v"] for c in candles])
    if i >= 42:
        vol_window = volumes[max(0, i - 180):i]
        vol_mean = float(np.mean(vol_window)) if len(vol_window) > 0 else 0
        vol_std = float(np.std(vol_window)) if len(vol_window) > 1 else 0
        f["vol_z"] = (volumes[i] - vol_mean) / vol_std if vol_std > 0 else 0
    else:
        f["vol_z"] = 0

    return f


def compute_oi_features(oi_history: list, funding: float = 0.0) -> dict:
    """Compute OI delta as % change over 1h/4h from live 60s samples.
    Percentage (not absolute) normalizes across tokens with different OI levels.
    Observation only -- not used for signal decisions yet."""
    if len(oi_history) < 30:  # need ~30min of data for meaningful delta
        return {"oi_delta_1h": 0.0, "oi_delta_4h": 0.0, "funding_bps": 0.0}
    history = list(oi_history)
    now_oi = history[-1][1]
    # 1h delta (~60 samples)
    idx_1h = max(0, len(history) - 60)
    oi_1h = history[idx_1h][1]
    delta_1h = (now_oi / oi_1h - 1) * 100 if oi_1h > 0 else 0.0
    # 4h delta (~240 samples)
    idx_4h = max(0, len(history) - 240)
    oi_4h = history[idx_4h][1]
    delta_4h = (now_oi / oi_4h - 1) * 100 if oi_4h > 0 else 0.0
    # Funding in bps (hourly rate x 10000)
    funding_bps = funding * 1e4
    return {
        "oi_delta_1h": round(delta_1h, 2),
        "oi_delta_4h": round(delta_4h, 2),
        "funding_bps": round(funding_bps, 3),
    }


def compute_crowding_score(funding: float, premium: float,
                           oi_delta_1h: float, vol_z: float | None) -> int:
    """Score 0-100 measuring leverage stress / flush quality.

    Higher = more likely a genuine liquidation flush (good for S8).
    Lower = simple price decline without deleveraging.
    Components: OI dropping (max 50) + negative funding (20) + vol spike (15) + negative premium (15).
    Observation only -- not used for signal decisions yet.
    """
    score = 0

    # OI dropping = positions closing = deleveraging
    if oi_delta_1h < -1.0:
        score += 30
    if oi_delta_1h < -3.0:
        score += 20

    # Funding very negative = shorts overcrowded, squeeze potential
    if funding < -0.00005:  # -0.005%
        score += 20

    # Volume spike = stress
    if vol_z is not None and vol_z > 1.5:
        score += 15

    # Premium negative = perp trading below oracle = forced selling
    if premium < -0.0005:  # -0.05%
        score += 15

    return min(100, score)


def compute_btc_features(btc_candles: list) -> dict:
    """Compute BTC-level features."""
    if len(btc_candles) < 50:
        return {}

    candles = list(btc_candles)
    n = len(candles)
    closes = np.array([c["c"] for c in candles])

    f = {}
    if n >= 180 and closes[n - 180] > 0:
        f["btc_30d"] = (closes[-1] / closes[n - 180] - 1) * 1e4
    else:
        f["btc_30d"] = 0  # need real 30d window -- 7d fallback would misfire S1

    if n >= 42 and closes[n - 42] > 0:
        f["btc_7d"] = (closes[-1] / closes[n - 42] - 1) * 1e4
    else:
        f["btc_7d"] = 0

    return f


def fetch_dxy(degraded: list, dxy_cache_path: str) -> float:
    """Fetch DXY 7-day return (bps) via Yahoo Finance with 3-tier fallback:
    1. Fresh cache (<6h) -- normal operation
    2. Stale cache (6-48h) -- S4 stays active, dashboard shows warning
    3. No data (>48h) -- S4 disabled, returns 0.0
    """
    def _read_cache() -> tuple[float | None, float]:
        """Returns (dxy_bps or None, age_hours)."""
        if not os.path.exists(dxy_cache_path):
            return None, 999
        age_h = (time.time() - os.path.getmtime(dxy_cache_path)) / 3600
        try:
            with open(dxy_cache_path) as fh:
                daily = json.load(fh)
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
            if tag in degraded:
                degraded.remove(tag)
        return cached

    # 2. Try fresh fetch from Yahoo Finance
    try:
        end_ts = int(time.time())
        start_ts = end_ts - 30 * 86400
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
               f"?period1={start_ts}&period2={end_ts}&interval=1d")
        raw = json.loads(http_fetch(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10))

        result = raw["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        daily = [{"t": ts * 1000, "c": c} for ts, c in zip(timestamps, closes) if c]
        if daily and len(daily) >= 6:
            os.makedirs(os.path.dirname(dxy_cache_path), exist_ok=True)
            with open(dxy_cache_path, "w") as fh:
                json.dump(daily, fh)
            for tag in ["DXY", "DXY_STALE"]:
                if tag in degraded:
                    degraded.remove(tag)
            return (daily[-1]["c"] / daily[-6]["c"] - 1) * 1e4
    except Exception as e:
        log.warning("DXY fetch failed: %s", e)

    # 3. Stale cache fallback (6h-48h)
    if cached is not None and age_h < 48:
        if "DXY_STALE" not in degraded:
            degraded.append("DXY_STALE")
        if "DXY" in degraded:
            degraded.remove("DXY")
        log.warning("DXY stale (%.0fh old) -- using cached value", age_h)
        return cached

    # 4. No data at all
    if "DXY" not in degraded:
        degraded.append("DXY")
    if "DXY_STALE" in degraded:
        degraded.remove("DXY_STALE")
    log.warning("DXY unavailable (cache >48h or missing)")
    return 0.0


def compute_alt_index(feature_cache: dict) -> float:
    """Compute alt-index: mean 7d return across all alts (uses cache)."""
    rets = []
    for sym in TRADE_SYMBOLS:
        f = feature_cache.get(sym)
        if f and "ret_42h" in f:
            rets.append(f["ret_42h"])
    return float(np.mean(rets)) if rets else 0


def compute_sector_divergence(symbol: str, feature_cache: dict) -> dict | None:
    """Compute how much a token diverges from its sector peers.

    Returns {divergence, sector_mean, token_ret, vol_z, sector} or None.
    """
    sector = TOKEN_SECTOR.get(symbol)
    if not sector:
        return None

    own_f = feature_cache.get(symbol)
    if not own_f or "ret_42h" not in own_f:
        return None

    # Compute sector mean excluding self
    peers = SECTORS[sector]
    peer_rets = []
    for peer in peers:
        if peer == symbol:
            continue
        pf = feature_cache.get(peer)
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
