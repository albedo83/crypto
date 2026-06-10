"""Signal detection — S1/S5/S8/S9/S10 + squeeze + cross-context.

Taken from analysis/bot/signals.py (v12.17.3), parameterized by
`settings.Params` (no module-level config) so the same detection code is
called by BotInstances (each with its own Params) and by the backtests.

`detect_squeeze_at` is the indexed core (shared with the backtest engine,
which scans historical candles by index without slicing).
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .settings import Params


# ── S10 Squeeze Detection ──────────────────────────────────────────


def detect_squeeze_at(candles, idx: int, vol_ratio: float, p: Params,
                      candle_scale: int = 1) -> dict | None:
    """Detect squeeze -> false breakout -> reintegration ending at `idx`.

    `candle_scale` scales the time-based windows to non-4h grids (1 = 4h
    grid, 4 = 1h grid). Returns {"direction": ±1, "squeeze_range": float,
    "bo_dir": "UP"/"DOWN"} or None.
    """
    sq_window = p.s10_squeeze_window * candle_scale
    reint = p.s10_reint_candles * candle_scale
    if vol_ratio > p.s10_vol_ratio_max or idx < sq_window + reint + 2:
        return None

    # Try each valid breakout position (most recent first)
    for bo_offset in range(1, reint + 1):
        bo_idx = idx - bo_offset
        sq_start = bo_idx - sq_window
        if sq_start < 0:
            continue

        sq_candles = candles[sq_start:sq_start + sq_window]
        range_high = max(c["h"] for c in sq_candles)
        range_low = min(c["l"] for c in sq_candles)
        range_size = range_high - range_low
        if range_size <= 0 or range_low <= 0:
            continue

        bo = candles[bo_idx]
        threshold = range_size * p.s10_breakout_pct
        bo_above = bo["h"] > range_high + threshold
        bo_below = bo["l"] < range_low - threshold
        if not bo_above and not bo_below:
            continue
        if bo_above and bo_below:
            continue
        bo_dir = 1 if bo_above else -1

        # Reintegration: must close inside range within reint candles
        reintegrated = False
        ri_end = min(bo_idx + 1 + reint, idx + 1)
        for ri in range(bo_idx + 1, ri_end):
            if range_low <= candles[ri]["c"] <= range_high:
                reintegrated = True
                break

        if not reintegrated:
            continue

        return {
            "direction": -bo_dir,
            "squeeze_range": round(range_size / range_low * 100, 2),  # % of low
            "bo_dir": "UP" if bo_dir == 1 else "DOWN",
        }

    return None


def detect_squeeze(candles: list, vol_ratio: float, p: Params) -> dict | None:
    """Live-bot convenience wrapper: detect on the latest candle."""
    candles = list(candles)
    return detect_squeeze_at(candles, len(candles) - 1, vol_ratio, p)


# ── Cross-Sectional Context ────────────────────────────────────────


def compute_cross_context(feature_cache: dict, trade_symbols,
                          token_sector: dict) -> dict:
    """Market-wide stress and dispersion from the feature cache.

    Returns {"stress_by_sector": defaultdict, "n_stress_global": int,
             "disp_24h": float, "disp_7d": float}.
    """
    stress_by_sector: defaultdict[str, int] = defaultdict(int)
    n_stress_global = 0
    all_ret24h: list[float] = []
    all_ret7d: list[float] = []
    for _sym in trade_symbols:
        _f = feature_cache.get(_sym)
        if not _f:
            continue
        if _f.get("vol_z", 0) > 1.5 and _f.get("drawdown", 0) < -1500:
            n_stress_global += 1
            _sect = token_sector.get(_sym)
            if _sect:
                stress_by_sector[_sect] += 1
        all_ret24h.append(_f.get("ret_24h", 0))
        all_ret7d.append(_f.get("ret_42h", 0))
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
    p: Params,
) -> list[dict]:
    """Detect S1/S5/S8/S9/S10 for a single token.

    `features` uses the canonical bot schema (ret_24h, ret_42h, drawdown,
    vol_z, vol_ratio, range_pct). Backtest callers adapt their vectorized
    schema first (see rules.adapt_bt_features).
    """
    f = features
    signals: list[dict] = []

    btc_30d = btc_features.get("btc_30d", 0)

    # S1: BTC momentum spills over to alts. Ranking: alts already moving up
    # get priority (backtest: +60% P&L vs random; "laggards first" is worse).
    if btc_30d > p.s1_btc_30d_min_bps:
        signals.append({
            "symbol": sym, "direction": 1, "strategy": "S1",
            "z": p.strat_z["S1"],
            "info": f"BTC 30d={btc_30d:+.0f}bps{oi_tag}",
            "strength": max(f.get("ret_42h", 0), 0),
            "hold_hours": p.hold_hours_for("S1"), "ctx": entry_ctx,
        })

    # S5: Sector breakout — FOLLOW the divergence (fade tested, loses).
    if (sector_div and abs(sector_div["divergence"]) >= p.s5_div_threshold
            and sector_div["vol_z"] >= p.s5_vol_z_min):
        direction = 1 if sector_div["divergence"] > 0 else -1
        signals.append({
            "symbol": sym, "direction": direction, "strategy": "S5",
            "z": p.strat_z["S5"],
            "info": f"{sector_div['sector']} div={sector_div['divergence']:+.0f} vz={sector_div['vol_z']:.1f}{oi_tag}",
            "strength": abs(sector_div["divergence"]),
            "hold_hours": p.hold_hours_for("S5"), "ctx": entry_ctx,
        })

    # S8: Capitulation flush — all 4 conditions must align.
    if (f.get("drawdown", 0) < p.s8_drawdown_thresh
            and f.get("vol_z", 0) > p.s8_vol_z_min
            and f.get("ret_24h", 0) < p.s8_ret_24h_thresh
            and btc_features.get("btc_7d", 0) < p.s8_btc_7d_thresh):
        signals.append({
            "symbol": sym, "direction": 1, "strategy": "S8",
            "z": p.strat_z["S8"],
            "info": f"DD={f['drawdown']:.0f} vz={f['vol_z']:.1f} r24h={f['ret_24h']:.0f} BTC7d={btc_features.get('btc_7d', 0):+.0f}{oi_tag}",
            "strength": abs(f["drawdown"]),
            "hold_hours": p.hold_hours_for("S8"), "ctx": entry_ctx,
        })

    # S9: Fade extreme ±20%/24h move. Adaptive stop: bigger move → tighter stop.
    if abs(f.get("ret_24h", 0)) >= p.s9_ret_thresh:
        s9_dir = -1 if f["ret_24h"] > 0 else 1
        s9_stop = (max(p.stop_loss_bps, -500 - abs(f["ret_24h"]) / 8)
                   if p.s9_adaptive_stop else 0)
        signals.append({
            "symbol": sym, "direction": s9_dir, "strategy": "S9",
            "z": p.strat_z["S9"],
            "info": f"Fade r24h={f['ret_24h']:+.0f} stop={s9_stop:.0f}{oi_tag}",
            "strength": abs(f["ret_24h"]),
            "hold_hours": p.hold_hours_for("S9"), "ctx": entry_ctx,
            "stop_bps": s9_stop,
        })

    # S10: Squeeze expansion, mode B (fade the failed breakout) + walk-forward
    # filters: SHORT-only and token whitelist (v11.3.4).
    if squeeze_result:
        sq = squeeze_result
        direction = sq["direction"]
        wf_block = ((not p.s10_allow_longs and direction == 1)
                    or sym not in p.s10_allowed_tokens)
        if not wf_block:
            signals.append({
                "symbol": sym, "direction": direction, "strategy": "S10",
                "z": p.strat_z["S10"],
                "info": f"Squeeze bo={sq['bo_dir']} rng={sq['squeeze_range']:.1f}%{oi_tag}",
                "strength": 1000 / max(sq["squeeze_range"], 0.1),  # tighter = higher priority
                "hold_hours": p.hold_hours_for("S10"), "ctx": entry_ctx,
            })

    return signals


# ── S9-fast Observation ─────────────────────────────────────────────


def check_s9f_observation(price_ticks, current_price: float) -> dict | None:
    """±3% in 2h on 60s ticks. NOT traded — observation only."""
    if len(price_ticks) < 120:  # need 2h of ticks (120 x 60s)
        return None
    ticks = list(price_ticks)
    price_2h_ago = ticks[-120][1]
    if price_2h_ago <= 0:
        return None
    ret_2h = (current_price / price_2h_ago - 1) * 1e4
    if abs(ret_2h) >= 300:
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

    Mutates *signal_first_seen* and appends age/retest info to each signal's
    "info" string. Prunes entries gone for >7 days.
    """
    current_keys: set[str] = set()
    for sig in signals:
        key = f"{sig['strategy']}:{sig['symbol']}"
        current_keys.add(key)
        prev = signal_first_seen.get(key)
        if prev is None:
            signal_first_seen[key] = now_ts
            age_h = 0
            retest = 0
        elif prev < 0:
            # Was gone (negative = epoch when it disappeared), now back = retest
            signal_first_seen[key] = now_ts
            age_h = 0
            retest = 1
        else:
            age_h = (now_ts - prev) / 3600
            retest = 0
        sig["info"] += f" age={age_h:.0f}h rt={retest}"
    # Mark disappeared signals (keep for 7 days to detect retests)
    to_prune = []
    for k in list(signal_first_seen.keys()):
        if k not in current_keys:
            if signal_first_seen[k] > 0:
                signal_first_seen[k] = -now_ts
            elif now_ts - abs(signal_first_seen[k]) > 7 * 86400:
                to_prune.append(k)
    for k in to_prune:
        signal_first_seen.pop(k, None)
