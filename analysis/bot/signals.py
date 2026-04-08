"""Signal detection logic — S1, S5, S8, S9, S10 + squeeze + S9-fast observation.

Extracted from reversal.py _detect_squeeze, _scan_signals, and signal-age tracking.
All thresholds and conditions are exact copies from the backtest-validated original.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

from .config import (
    TRADE_SYMBOLS, TOKEN_SECTOR, SECTORS, STRAT_Z,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP, STOP_LOSS_BPS,
    S10_SQUEEZE_WINDOW, S10_VOL_RATIO_MAX, S10_BREAKOUT_PCT, S10_REINT_CANDLES,
)

log = logging.getLogger("multisignal")


# ── S10 Squeeze Detection ──────────────────────────────────────────


def detect_squeeze(candles: list, vol_ratio: float) -> dict | None:
    """Detect squeeze -> false breakout -> reintegration (S10).

    Checks multiple offsets for the breakout candle (rc=2 support):
      offset=1: breakout at candles[-2], reint at candles[-1]
      offset=2: breakout at candles[-3], reint at candles[-2] or [-1]
    Returns {"direction": 1/-1, "squeeze_range": float, "bo_dir": "UP"/"DOWN"} or None.
    """
    if len(candles) < S10_SQUEEZE_WINDOW + S10_REINT_CANDLES + 2:
        return None

    if vol_ratio > S10_VOL_RATIO_MAX:
        return None

    # Try each valid breakout position (most recent first)
    for bo_offset in range(1, S10_REINT_CANDLES + 1):
        bo_idx = len(candles) - 1 - bo_offset
        sq_start = bo_idx - S10_SQUEEZE_WINDOW
        if sq_start < 0:
            continue

        sq_candles = candles[sq_start:sq_start + S10_SQUEEZE_WINDOW]
        range_high = max(c["h"] for c in sq_candles)
        range_low = min(c["l"] for c in sq_candles)
        range_size = range_high - range_low
        if range_size <= 0 or range_low <= 0:
            continue

        bo = candles[bo_idx]
        threshold = range_size * S10_BREAKOUT_PCT
        bo_above = bo["h"] > range_high + threshold
        bo_below = bo["l"] < range_low - threshold
        if not bo_above and not bo_below:
            continue
        if bo_above and bo_below:
            continue
        bo_dir = 1 if bo_above else -1

        # Check reintegration: must close inside range within rc candles
        reintegrated = False
        ri_end = min(bo_idx + 1 + S10_REINT_CANDLES, len(candles))
        for ri in range(bo_idx + 1, ri_end):
            if range_low <= candles[ri]["c"] <= range_high:
                reintegrated = True
                break

        if not reintegrated:
            continue

        return {
            "direction": -bo_dir,
            "squeeze_range": round(range_size / range_low * 100, 2),  # % of low (NOT bps like feature range_pct)
            "bo_dir": "UP" if bo_dir == 1 else "DOWN",
        }

    return None


# ── Cross-Sectional Context ────────────────────────────────────────


def compute_cross_context(feature_cache: dict) -> dict:
    """Compute market-wide stress and dispersion from feature cache.

    Returns {"stress_by_sector": defaultdict, "n_stress_global": int,
             "disp_24h": float, "disp_7d": float}.
    """
    stress_by_sector: defaultdict[str, int] = defaultdict(int)
    n_stress_global = 0
    all_ret24h: list[float] = []
    all_ret7d: list[float] = []
    for _sym in TRADE_SYMBOLS:
        _f = feature_cache.get(_sym)
        if not _f:
            continue
        if _f.get("vol_z", 0) > 1.5 and _f.get("drawdown", 0) < -1500:
            n_stress_global += 1
            _sect = TOKEN_SECTOR.get(_sym)
            if _sect:
                stress_by_sector[_sect] += 1
        all_ret24h.append(_f.get("ret_24h", 0))
        all_ret7d.append(_f.get("ret_42h", 0))
    # Dispersion = how scattered the basket is (std of returns across all alts)
    disp_24h = round(float(np.std(all_ret24h)), 0) if all_ret24h else 0
    disp_7d = round(float(np.std(all_ret7d)), 0) if all_ret7d else 0
    return {
        "stress_by_sector": stress_by_sector,
        "n_stress_global": n_stress_global,
        "disp_24h": disp_24h,
        "disp_7d": disp_7d,
    }


# ── Per-Token Signal Detection ─────────────────────────────────────


def detect_token_signals(
    sym: str,
    features: dict,
    btc_features: dict,
    sector_div: dict | None,
    squeeze_result: dict | None,
    oi_tag: str,
    entry_ctx: dict,
) -> list[dict]:
    """Detect S1/S5/S8/S9/S10 for a single token.

    Parameters
    ----------
    sym : token symbol (e.g. "SOL")
    features : token features dict (ret_24h, drawdown, vol_z, etc.)
    btc_features : BTC features dict (btc_30d, btc_7d)
    sector_div : sector divergence dict or None (from _compute_sector_divergence)
    squeeze_result : squeeze detection dict or None (from detect_squeeze)
    oi_tag : pre-built string with OI/crowding/stress context
    entry_ctx : structured context dict for trade logging
    """
    f = features
    signals: list[dict] = []

    btc_30d = btc_features.get("btc_30d", 0)

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
            "hold_hours": HOLD_HOURS_DEFAULT, "ctx": entry_ctx,
        })

    # S2 REMOVED — Alt crash mean-reversion (z=4.00) loses in portfolio.
    # S4 SUSPENDED — Vol compression + DXY SHORT. Code kept in original for reactivation.

    # S5: Sector breakout — when a token diverges >10% from its sector peers
    # with high volume, FOLLOW the divergence (don't fade it). Backtested both
    # directions in backtest_sector.py: follow works, fade doesn't.
    if sector_div and abs(sector_div["divergence"]) >= S5_DIV_THRESHOLD and sector_div["vol_z"] >= S5_VOL_Z_MIN:
        direction = 1 if sector_div["divergence"] > 0 else -1
        signals.append({
            "symbol": sym, "direction": direction, "strategy": "S5",
            "z": STRAT_Z["S5"],
            "info": f"{sector_div['sector']} div={sector_div['divergence']:+.0f} vz={sector_div['vol_z']:.1f}{oi_tag}",
            "strength": abs(sector_div["divergence"]),
            "hold_hours": HOLD_HOURS_S5, "ctx": entry_ctx,
        })

    # S8: Capitulation flush — buy when market-wide liquidation cascade is
    # underway. All 4 conditions must align: extreme drawdown, volume spike
    # (forced sells), still bleeding (not recovering yet), AND BTC weak.
    # Highest z-score of all signals (6.99), 70% win rate, rare (~1/month).
    if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
            and f.get("vol_z", 0) > S8_VOL_Z_MIN
            and f.get("ret_24h", 0) < S8_RET_24H_THRESH
            and btc_features.get("btc_7d", 0) < S8_BTC_7D_THRESH):
        signals.append({
            "symbol": sym, "direction": 1, "strategy": "S8",
            "z": STRAT_Z["S8"],
            "info": f"DD={f['drawdown']:.0f} vz={f['vol_z']:.1f} r24h={f['ret_24h']:.0f} BTC7d={btc_features.get('btc_7d', 0):+.0f}{oi_tag}",
            "strength": abs(f["drawdown"]),
            "hold_hours": HOLD_HOURS_S8, "ctx": entry_ctx,
        })

    # S9: Fade extreme move — when a token moves >20% in 24h, fade it.
    # Pumps are faded (short), dumps are bought (long). Mean reversion on
    # individual token extremes. z=8.71 (MC), strongest signal in the bot.
    # See backtest_wild.py.
    if abs(f.get("ret_24h", 0)) >= S9_RET_THRESH:
        s9_dir = -1 if f["ret_24h"] > 0 else 1
        # Adaptive stop: bigger moves get tighter stops (more confident in reversion)
        s9_stop = max(STOP_LOSS_BPS, -500 - abs(f["ret_24h"]) / 8) if S9_ADAPTIVE_STOP else 0
        signals.append({
            "symbol": sym, "direction": s9_dir, "strategy": "S9",
            "z": STRAT_Z["S9"],
            "info": f"Fade r24h={f['ret_24h']:+.0f} stop={s9_stop:.0f}{oi_tag}",
            "strength": abs(f["ret_24h"]),
            "hold_hours": HOLD_HOURS_S9, "ctx": entry_ctx,
            "stop_bps": s9_stop,
        })

    # S10: Squeeze expansion — compression + false breakout + reintegration.
    # Mode B: fade the failed breakout. Config frozen, do not re-optimize.
    if squeeze_result:
        sq = squeeze_result
        signals.append({
            "symbol": sym, "direction": sq["direction"], "strategy": "S10",
            "z": STRAT_Z["S10"],
            "info": f"Squeeze bo={sq['bo_dir']} rng={sq['squeeze_range']:.1f}%{oi_tag}",
            "strength": 1000 / max(sq["squeeze_range"], 0.1),  # tighter range = higher priority
            "hold_hours": HOLD_HOURS_S10, "ctx": entry_ctx,
        })

    return signals


# ── S9-fast Observation ─────────────────────────────────────────────


def check_s9f_observation(price_ticks, current_price: float) -> dict | None:
    """Check for S9-fast observation: +-3% in 2h on 60s price ticks.

    NOT traded — observation only. Returns {"dir": "SHORT"/"LONG", "ret_2h": int}
    if triggered, else None.
    """
    if len(price_ticks) < 120:  # need 2h of ticks (120 x 60s)
        return None
    ticks = list(price_ticks)
    price_2h_ago = ticks[-120][1]
    if price_2h_ago <= 0:
        return None
    ret_2h = (current_price / price_2h_ago - 1) * 1e4
    if abs(ret_2h) >= 300:  # +-3% in 2h
        s9f_dir = "SHORT" if ret_2h > 0 else "LONG"
        return {"dir": s9f_dir, "ret_2h": round(ret_2h)}
    return None


# ── Signal Age Tracking ─────────────────────────────────────────────


def track_signal_age(
    signals: list[dict],
    signal_first_seen: dict,
    now_ts: float,
) -> None:
    """Track signal age + retest detection (observation only).

    Mutates *signal_first_seen* dict and appends age/retest info to each
    signal's "info" string. Prunes entries gone for >7 days.
    """
    current_keys: set[str] = set()
    for sig in signals:
        key = f"{sig['strategy']}:{sig['symbol']}"
        current_keys.add(key)
        prev = signal_first_seen.get(key)
        if prev is None:
            # Brand new signal
            signal_first_seen[key] = now_ts
            age_h = 0
            retest = 0
        elif prev < 0:
            # Was gone (negative = epoch when it disappeared), now back = retest
            signal_first_seen[key] = now_ts
            age_h = 0
            retest = 1
        else:
            # Still active
            age_h = (now_ts - prev) / 3600
            retest = 0
        sig["info"] += f" age={age_h:.0f}h rt={retest}"
    # Mark disappeared signals (keep for 7 days to detect retests)
    to_prune = []
    for k in list(signal_first_seen.keys()):
        if k not in current_keys:
            if signal_first_seen[k] > 0:
                # Just disappeared — mark with negative timestamp
                signal_first_seen[k] = -now_ts
            elif now_ts - abs(signal_first_seen[k]) > 7 * 86400:
                # Gone for >7 days — prune
                to_prune.append(k)
    for k in to_prune:
        signal_first_seen.pop(k, None)
