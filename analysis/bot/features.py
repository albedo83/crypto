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


def oi_delta_24h_bps(oi_history) -> float | None:
    """OI delta 24h in bps, looking up the oldest sample >= 23h back.

    Returns None if history is too short (< ~23h) — in that case the caller
    should treat it as "gate inactive" (fail-open). Avoids false readings
    right after a restart.
    """
    if not oi_history:
        return None
    history = list(oi_history)
    if len(history) < 2:
        return None
    now_ts, oi_now = history[-1]
    if oi_now <= 0:
        return None
    oldest_ts = history[0][0]
    if now_ts - oldest_ts < 23 * 3600:
        return None  # not enough history yet
    # Find sample closest to 24h ago (history is sorted by ts ascending)
    target_ts = now_ts - 24 * 3600
    oi_then = None
    for t, oi in history:
        if t >= target_ts:
            oi_then = oi
            break
    if oi_then is None or oi_then <= 0:
        return None
    return (oi_now / oi_then - 1) * 1e4


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


def compute_btc_z(btc_candles: list, lookback_days: int = 30,
                  z_window_days: int = 180,
                  candles_per_day: int = 6) -> float | None:
    """Rolling z-score of BTC's `lookback_days` return.

    Computes ret_30d at each candle in the past `z_window_days`, then returns
    the z-score of the latest ret_30d against that rolling distribution.
    No look-ahead: only past data is used. Returns None if history insufficient.

    candles_per_day=6 for 4h candles (24/4). For other granularities, adjust.
    """
    n_lb = lookback_days * candles_per_day
    n_z = z_window_days * candles_per_day
    if len(btc_candles) < n_lb + 30:  # need at least lb + minimal z window
        return None
    closes = np.array([c["c"] for c in btc_candles])
    # Build the trailing window of ret_lb values
    rets = []
    start_i = max(n_lb, len(btc_candles) - n_z)
    for i in range(start_i, len(btc_candles)):
        if closes[i - n_lb] > 0:
            rets.append(closes[i] / closes[i - n_lb] - 1)
    if len(rets) < 30:
        return None
    rets_arr = np.array(rets)
    mean = float(rets_arr.mean())
    std = float(rets_arr.std()) or 1.0
    current_ret = rets_arr[-1]
    return (current_ret - mean) / std


def compute_basket_correlation(positions: dict, states: dict,
                                lookback_days: int = 30,
                                candles_per_day: int = 6) -> dict | None:
    """Basket-level correlation metrics over currently open positions.

    Observation-only (no trading decisions). Computes:
      - mean_corr_to_btc: signed BTC exposure of the basket. +1 = fully long-BTC,
        -1 = fully short-BTC, 0 = hedged. mean over positions of
        direction_i * corr(alt_i, BTC) on the rolling lookback window.
      - max_pairwise_corr: maximum signed pairwise correlation between any two
        positions (sign-adjusted by directions). High positive = strongest "same
        trade" pair in the basket. Negative = best hedge pair.
      - effective_n: effective number of independent positions, equal-weighted.
        Equals n if all pairwise correlations are 0; equals 1 if all signed
        correlations are 1. Clamped to [1, n_positions].

    Returns None if < 2 positions, insufficient candle history, or zero-variance.
    """
    if len(positions) < 2:
        return None
    btc_st = states.get("BTC")
    n_candles = lookback_days * candles_per_day
    if not btc_st or len(btc_st.candles_4h) < n_candles + 1:
        return None
    btc_closes = np.array([c["c"] for c in list(btc_st.candles_4h)[-(n_candles + 1):]])
    if (btc_closes[:-1] == 0).any():
        return None
    btc_rets = np.diff(btc_closes) / btc_closes[:-1]
    btc_std = float(np.std(btc_rets))
    if btc_std == 0:
        return None

    dirs = []
    rets_list = []
    for sym, pos in positions.items():
        st = states.get(sym)
        if not st or len(st.candles_4h) < n_candles + 1:
            return None
        closes = np.array([c["c"] for c in list(st.candles_4h)[-(n_candles + 1):]])
        if (closes[:-1] == 0).any():
            return None
        rets = np.diff(closes) / closes[:-1]
        if len(rets) != len(btc_rets):
            return None
        dirs.append(pos.direction)
        rets_list.append(rets)

    n = len(dirs)
    if n < 2:
        return None
    dirs_arr = np.array(dirs, dtype=float)

    # Per-position correlation to BTC, sign-adjusted
    signed_btc = []
    for r in rets_list:
        if float(np.std(r)) == 0:
            signed_btc.append(0.0)
            continue
        c = float(np.corrcoef(r, btc_rets)[0, 1])
        signed_btc.append(c)
    signed_btc_arr = np.array(signed_btc) * dirs_arr
    mean_corr_to_btc = float(signed_btc_arr.mean())

    # Pairwise signed correlation matrix
    R = np.array(rets_list)
    corr_mat = np.corrcoef(R)
    # corrcoef can produce NaN for zero-variance rows — replace with 0
    if np.isnan(corr_mat).any():
        corr_mat = np.nan_to_num(corr_mat, nan=0.0)
    signed_mat = corr_mat * np.outer(dirs_arr, dirs_arr)

    # Max off-diagonal signed corr (worst same-trade pair)
    off_diag = signed_mat.copy()
    np.fill_diagonal(off_diag, -np.inf)
    max_pairwise = float(off_diag.max())

    # Effective n with sign-adjusted matrix (diagonal = 1)
    signed_mat_clean = signed_mat.copy()
    np.fill_diagonal(signed_mat_clean, 1.0)
    total = float(signed_mat_clean.sum())
    if total <= 0:
        effective_n = float(n)  # fully over-hedged → cap at n
    else:
        effective_n = max(1.0, min(float(n), (n * n) / total))

    return {
        "n_positions": n,
        "mean_corr_to_btc": round(mean_corr_to_btc, 3),
        "max_pairwise_corr": round(max_pairwise, 3),
        "effective_n": round(effective_n, 2),
    }


def compute_entry_side_imbalance(direction: int, mark: float,
                                  impact_bid: float, impact_ask: float
                                  ) -> dict | None:
    """Order book imbalance at the side WE will hit as taker.

    Observation-only. Returns {esi, spread_bps, skew} or None if the book is
    degenerate (zero/non-monotonic prices).

    Semantics:
      - impactPxs[0] = impact_bid = price to SELL $1M (lower than mark)
      - impactPxs[1] = impact_ask = price to BUY $1M (higher than mark)
      - skew = (mark - impact_bid) / (impact_ask - impact_bid) ∈ [0, 1]:
          0 = mark stuck at impact_bid (thin ask side, more buying pressure)
          1 = mark stuck at impact_ask (thin bid side, more selling pressure)
      - esi = the fraction of the spread WE'd cross as taker:
          LONG  → buy at ask → unfavorable when ask side is thin →
                  esi = (impact_ask - mark) / (impact_ask - impact_bid) = 1 - skew
          SHORT → sell at bid → unfavorable when bid side is thin →
                  esi = (mark - impact_bid) / (impact_ask - impact_bid) = skew

    Backtest retrospective on 75 live trades showed défavorable bucket
    (esi > 0.6) underperforms favorable bucket (esi < 0.3) by ~17 bps in
    slippage and ~$3.30 in avg PnL. Logging on OPEN events for forward
    validation; no trading gate at this point.
    """
    if mark <= 0 or impact_bid <= 0 or impact_ask <= impact_bid:
        return None
    spread = impact_ask - impact_bid
    raw_skew = (mark - impact_bid) / spread
    skew = max(0.0, min(1.0, raw_skew))
    esi = (1.0 - skew) if direction == 1 else skew
    return {
        "esi": round(esi, 3),
        "skew": round(skew, 3),
        "spread_bps": round(spread / mark * 1e4, 1),
    }


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
