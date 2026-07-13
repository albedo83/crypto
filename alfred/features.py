"""Feature computation — technical indicators, OI, crowding, macro.

Pure functions: data in, values out. Taken from analysis/bot/features.py
(v12.17.3) with two changes:
  - fetch_dxy (network I/O) moved out — it belongs to the MarketDataMaster.
  - universe-dependent helpers take the symbol/sector lists as arguments
    instead of reading module-level config (per-bot universes).
"""

from __future__ import annotations

import numpy as np


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

    # Volatility ratio: vol_7d / vol_30d -- below 1.0 = compression (S10)
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

    Returns None if history is too short (< ~23h) — caller treats it as
    "gate inactive" (fail-open). Avoids false readings right after a restart.
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
    target_ts = now_ts - 24 * 3600
    oi_then = None
    for t, oi in history:
        if t >= target_ts:
            # v1.15.0 : après un trou d'échantillonnage REST, le premier
            # sample >= cible peut dater d'il y a 2h — comparer un delta 2h
            # au seuil 24h sous-bloque (un unwind réel passe le gate). Si le
            # sample trouvé est à plus de 4h de la cible, les données ne
            # peuvent pas répondre à la question 24h → None (fail-open,
            # même sémantique que l'historique court).
            if t - target_ts > 4 * 3600:
                return None
            oi_then = oi
            break
    if oi_then is None or oi_then <= 0:
        return None
    return (oi_now / oi_then - 1) * 1e4


def compute_oi_features(oi_history: list, funding: float = 0.0) -> dict:
    """OI delta as % change over 1h/4h from live 60s samples. Observation only."""
    if len(oi_history) < 30:
        return {"oi_delta_1h": 0.0, "oi_delta_4h": 0.0, "funding_bps": 0.0}
    history = list(oi_history)
    now_oi = history[-1][1]
    idx_1h = max(0, len(history) - 60)
    oi_1h = history[idx_1h][1]
    delta_1h = (now_oi / oi_1h - 1) * 100 if oi_1h > 0 else 0.0
    idx_4h = max(0, len(history) - 240)
    oi_4h = history[idx_4h][1]
    delta_4h = (now_oi / oi_4h - 1) * 100 if oi_4h > 0 else 0.0
    funding_bps = funding * 1e4
    return {
        "oi_delta_1h": round(delta_1h, 2),
        "oi_delta_4h": round(delta_4h, 2),
        "funding_bps": round(funding_bps, 3),
    }


def compute_crowding_score(funding: float, premium: float,
                           oi_delta_1h: float, vol_z: float | None) -> int:
    """Score 0-100 measuring leverage stress / flush quality. Observation only."""
    score = 0
    if oi_delta_1h < -1.0:
        score += 30
    if oi_delta_1h < -3.0:
        score += 20
    if funding < -0.00005:  # -0.005%
        score += 20
    if vol_z is not None and vol_z > 1.5:
        score += 15
    if premium < -0.0005:  # -0.05%
        score += 15
    return min(100, score)


def compute_btc_features(btc_candles: list) -> dict:
    """Compute BTC-level features (btc_30d, btc_7d in bps)."""
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
                  candles_per_day: int = 6,
                  robust: bool = False) -> float | None:
    """Rolling z-score of BTC's `lookback_days` return. No look-ahead.

    robust=True uses median + MAD×1.4826 instead of mean + std (insensitive
    to single whale-liquidation outliers). Returns None if history short.
    """
    n_lb = lookback_days * candles_per_day
    n_z = z_window_days * candles_per_day
    if len(btc_candles) < n_lb + 30:
        return None
    closes = np.array([c["c"] for c in btc_candles])
    rets = []
    start_i = max(n_lb, len(btc_candles) - n_z)
    for i in range(start_i, len(btc_candles)):
        if closes[i - n_lb] > 0:
            rets.append(closes[i] / closes[i - n_lb] - 1)
    if len(rets) < 30:
        return None
    rets_arr = np.array(rets)
    current_ret = rets_arr[-1]
    if robust:
        center = float(np.median(rets_arr))
        mad = float(np.median(np.abs(rets_arr - center))) * 1.4826
        scale = mad or 1.0
    else:
        center = float(rets_arr.mean())
        scale = float(rets_arr.std()) or 1.0
    return (current_ret - center) / scale


def compute_btc_z_multi(btc_candles: list,
                        weight_long: float = 0.6,
                        long_lookback_days: int = 30,
                        long_z_window_days: int = 180,
                        short_lookback_days: int = 7,
                        short_z_window_days: int = 60,
                        candles_per_day: int = 6,
                        robust: bool = False) -> float | None:
    """Multi-horizon BTC z-score: weighted blend of 30d/180d and 7d/60d."""
    z_long = compute_btc_z(btc_candles, long_lookback_days, long_z_window_days,
                           candles_per_day, robust=robust)
    z_short = compute_btc_z(btc_candles, short_lookback_days, short_z_window_days,
                            candles_per_day, robust=robust)
    if z_long is None and z_short is None:
        return None
    if z_long is None:
        return z_short
    if z_short is None:
        return z_long
    return weight_long * z_long + (1.0 - weight_long) * z_short


def compute_basket_correlation(positions: dict, states: dict,
                               lookback_days: int = 30,
                               candles_per_day: int = 6) -> dict | None:
    """Basket-level correlation metrics over open positions. Observation only."""
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

    signed_btc = []
    for r in rets_list:
        if float(np.std(r)) == 0:
            signed_btc.append(0.0)
            continue
        c = float(np.corrcoef(r, btc_rets)[0, 1])
        signed_btc.append(c)
    signed_btc_arr = np.array(signed_btc) * dirs_arr
    mean_corr_to_btc = float(signed_btc_arr.mean())

    R = np.array(rets_list)
    corr_mat = np.corrcoef(R)
    if np.isnan(corr_mat).any():
        corr_mat = np.nan_to_num(corr_mat, nan=0.0)
    signed_mat = corr_mat * np.outer(dirs_arr, dirs_arr)

    off_diag = signed_mat.copy()
    np.fill_diagonal(off_diag, -np.inf)
    max_pairwise = float(off_diag.max())

    signed_mat_clean = signed_mat.copy()
    np.fill_diagonal(signed_mat_clean, 1.0)
    total = float(signed_mat_clean.sum())
    if total <= 0:
        effective_n = float(n)
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
    """Order book imbalance at the side WE will hit as taker. Observation only."""
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


def compute_alt_index(feature_cache: dict, trade_symbols) -> float:
    """Alt-index: mean 7d return across the tracked alts (uses cache)."""
    rets = []
    for sym in trade_symbols:
        f = feature_cache.get(sym)
        if f and "ret_42h" in f:
            rets.append(f["ret_42h"])
    return float(np.mean(rets)) if rets else 0


def compute_sector_divergence(symbol: str, feature_cache: dict,
                              sectors: dict, token_sector: dict) -> dict | None:
    """How much a token diverges from its sector peers (S5 input).

    Returns {divergence, sector_mean, token_ret, vol_z, sector} or None.
    """
    sector = token_sector.get(symbol)
    if not sector:
        return None

    own_f = feature_cache.get(symbol)
    if not own_f or "ret_42h" not in own_f:
        return None

    peers = sectors[sector]
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
