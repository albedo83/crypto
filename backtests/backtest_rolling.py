"""Rolling backtest — runs the bot's current config on multiple start dates
ending on the most recent candle, and writes a summary to docs/backtests.md.

Goal: answer the question "what would the bot have returned if I had started
it with $1000 X months ago, using the CURRENT parameters, until yesterday?".

This file is the source of truth for forward-looking expectations. Re-run it
any time the bot rules or parameters change.

Usage:
    python3 -m backtests.backtest_rolling
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

# Bot config — single source of truth. Import from the live bot so this
# backtest automatically reflects any rule or parameter change.
from analysis.bot.config import (
    SIZE_PCT, SIZE_BONUS, STRAT_Z, SIGNAL_MULT, LIQUIDITY_HAIRCUT,
    LEVERAGE, COST_BPS, TAKER_FEE_BPS, FUNDING_DRAG_BPS,
    MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8, S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP, VERSION,
    S10_SQUEEZE_WINDOW, S10_VOL_RATIO_MAX, S10_BREAKOUT_PCT, S10_REINT_CANDLES,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
    OI_LONG_GATE_BPS, TRADE_BLACKLIST,
    DISP_GATE_BPS, DISP_GATE_STRATEGIES,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)
from bisect import bisect_right

# Data + feature builders reused as-is from the existing backtest infrastructure
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
DOCS_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "backtests.md")


def load_oi():
    """Load OI per coin → sorted list of (ts, oi)."""
    d = {}
    for coin in TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_oi_4h.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        pts = [(int(r["t"]), float(r["oi"])) for r in raw]
        pts.sort()
        d[coin] = pts
    return d


def oi_delta_24h_pct(oi_data, coin, ts_ms):
    """OI delta over 24h in bps (6 4h-candles). None if insufficient history."""
    pts = oi_data.get(coin)
    if not pts:
        return None
    times = [p[0] for p in pts]
    i = bisect_right(times, ts_ms) - 1
    if i < 6:
        return None
    oi_now = pts[i][1]
    oi_then = pts[i - 6][1]
    if oi_then <= 0:
        return None
    return (oi_now / oi_then - 1) * 1e4


def load_funding():
    """Load hourly funding rate per coin from backtests/output/funding_history.db.

    Returns dict[coin] → (ts_array, rate_array) sorted by ts_ms. Rate is the
    hourly funding rate (fraction, e.g. 0.0001 = 0.01% per hour).

    Falls back to empty dict if DB missing — backtest keeps working with 0 funding.
    """
    db_path = os.path.join(os.path.dirname(__file__), "output", "funding_history.db")
    if not os.path.exists(db_path):
        return {}
    import sqlite3
    con = sqlite3.connect(db_path)
    result = {}
    for coin in TOKENS:
        rows = con.execute(
            "SELECT ts, funding_rate FROM funding WHERE symbol = ? ORDER BY ts",
            (coin,)
        ).fetchall()
        if rows:
            ts_arr = np.array([r[0] for r in rows], dtype=np.int64)
            rate_arr = np.array([r[1] for r in rows], dtype=np.float64)
            result[coin] = (ts_arr, rate_arr)
    con.close()
    return result


def compute_funding_cost(funding_data, coin, direction, entry_ts_ms, exit_ts_ms, notional):
    """Sum hourly funding payments on `notional` between entry and exit.

    Hyperliquid charges funding every hour at the `fundingRate` of that hour.
    The `fundingHistory` API returns rate samples at 8h intervals for historical
    data — we interpret each sample as the hourly rate constant for the
    surrounding 8h block, then integrate over the trade's hours of exposure.

    Convention: HL charges LONGs when rate > 0, pays them when rate < 0 (SHORTs
    are inverse). Per hour: cost = direction × rate × notional, where direction
    is +1 for LONG (+ means money out of account) and -1 for SHORT.

    Returns USDC cost (positive = we paid). 0 if no data or zero-length hold.
    """
    if coin not in funding_data:
        return 0.0
    ts_arr, rate_arr = funding_data[coin]
    lo = np.searchsorted(ts_arr, entry_ts_ms, side="left")
    hi = np.searchsorted(ts_arr, exit_ts_ms, side="right")
    if lo >= hi:
        return 0.0
    hold_hours = max((exit_ts_ms - entry_ts_ms) / 3_600_000, 0.0)
    if hold_hours <= 0:
        return 0.0
    avg_rate = rate_arr[lo:hi].mean()
    return direction * avg_rate * hold_hours * notional

# Hold periods converted to 4h candle counts
HOLD_CANDLES = {
    "S1": HOLD_HOURS_DEFAULT // 4,
    "S5": HOLD_HOURS_S5 // 4,
    "S8": HOLD_HOURS_S8 // 4,
    "S9": HOLD_HOURS_S9 // 4,
    "S10": HOLD_HOURS_S10 // 4,
}

# S9 early exit threshold in 4h candles
S9_EARLY_EXIT_CANDLES = int(S9_EARLY_EXIT_HOURS // 4)

# Cost per round-trip in the backtest.
#
# Live bot uses COST_BPS from config which assumes avgPx-based gross_bps (no
# slippage to add). The backtest uses candle closes (midprice) so it needs an
# extra slippage estimate on top. Realistic taker slippage on the traded
# universe: 3-5 bps round-trip on majors, 8-15 bps on thin tokens. Use 4 bps
# as a blended average — re-calibrate if position sizes exceed $5k on thin
# tokens (see docs/backtests.md).
BACKTEST_SLIPPAGE_BPS = 4.0
# Per-trade: drop the flat FUNDING_DRAG_BPS baked into COST_BPS — the backtest
# now computes real funding cost per trade from historical funding rates (v11.7.6).
COST = TAKER_FEE_BPS + BACKTEST_SLIPPAGE_BPS  # applied once at close


# ── Data loading ───────────────────────────────────────────────────────

def load_dxy():
    path = os.path.join(DATA_DIR, "macro_DXY.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        daily = json.load(f)
    closes = [d["c"] for d in daily]
    result = {}
    for i in range(5, len(daily)):
        if closes[i - 5] > 0:
            result[daily[i]["t"]] = (closes[i] / closes[i - 5] - 1) * 1e4
    return result


def detect_squeeze(candles, idx, vol_ratio):
    if vol_ratio > S10_VOL_RATIO_MAX or idx < S10_SQUEEZE_WINDOW + S10_REINT_CANDLES + 2:
        return None
    for bo_offset in range(1, S10_REINT_CANDLES + 1):
        bo_idx = idx - bo_offset
        sq_start = bo_idx - S10_SQUEEZE_WINDOW
        if sq_start < 0:
            continue
        sq = candles[sq_start:sq_start + S10_SQUEEZE_WINDOW]
        rh = max(c["h"] for c in sq)
        rl = min(c["l"] for c in sq)
        rs = rh - rl
        if rs <= 0 or rl <= 0:
            continue
        bo = candles[bo_idx]
        th = rs * S10_BREAKOUT_PCT
        above = bo["h"] > rh + th
        below = bo["l"] < rl - th
        if not above and not below:
            continue
        if above and below:
            continue
        bo_dir = 1 if above else -1
        ri_end = min(bo_idx + 1 + S10_REINT_CANDLES, idx + 1)
        for ri in range(bo_idx + 1, ri_end):
            if rl <= candles[ri]["c"] <= rh:
                return -bo_dir
    return None


def strat_size(strat: str, capital: float) -> float:
    """Match analysis.bot.config.strat_size() exactly."""
    z = STRAT_Z.get(strat, 3.0)
    w = max(0.5, min(2.0, z / 4.0))
    pct = SIZE_PCT + (SIZE_BONUS if z > 4.0 else 0)
    haircut = LIQUIDITY_HAIRCUT.get(strat, 1.0)
    mult = SIGNAL_MULT.get(strat, 1.0)
    return round(max(10, capital * pct * w * haircut * mult), 2)


# ── Backtest engine ────────────────────────────────────────────────────

def run_window(features, data, sector_features, dxy_data,
               start_ts_ms: int, end_ts_ms: int, start_capital: float = 1000.0,
               skip_fn=None, oi_data: dict | None = None,
               early_exit_params: dict | None = None,
               trailing_extra: dict | None = None,
               reversal_exit: dict | None = None,
               early_mfe_exit: dict | None = None,
               extra_candidate_fn=None,
               block_opposite_sector: bool = False,
               size_multiplier: dict | None = None,
               size_fn=None,
               btc_corr_exit: dict | None = None,
               runner_extension: dict | None = None,
               funding_data: dict | None = None) -> dict:
    """Run the portfolio backtest on a time window.

    P&L math matches the live bot (v11.3.0+): size_usdt is the notional, so
    pnl = notional × (exit/entry - 1). No extra leverage multiplier.

    Mirrors current bot filters:
    - v11.4.9 OI gate LONG: skip LONG entries when Δ(OI,24h) < -OI_LONG_GATE_BPS
    - v11.4.10 TRADE_BLACKLIST: skip any entry on blacklisted tokens

    early_exit_params (optional, option D sweep): dict with
        exit_lead_candles: trigger check at held >= hold - this
        mfe_cap_bps:       MFE must be <= this (bps) — no upside revealed
        mae_floor_bps:     MAE must be <= this (bps) — trade is deeply under
        slack_bps:         current_bps must be <= MAE + slack — still near low

    trailing_extra (optional): adds a trailing-stop rule to a non-S10 strategy
        dict with keys: strategy (e.g. "S5"), trigger_bps, offset_bps.
        When MFE >= trigger_bps and current drops to MFE - offset_bps, exit.
    """
    coins = [c for c in TOKENS if c in features and c in data]
    macro_strats = set(MACRO_STRATEGIES)

    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    # v11.7.28 dispersion gate — precompute cross-sectional std(ret_6h) per ts.
    # Mirrors the live bot's signals.compute_cross_context "disp_24h" exactly:
    # ret_6h on 4h candles = 24h return.
    disp_by_ts: dict[int, float] = {}
    if DISP_GATE_BPS < 99000:  # kill-switch: disable when set very high
        for ts, fmap in feat_by_ts.items():
            rets = [f.get("ret_6h", 0) for f in fmap.values() if "ret_6h" in f]
            if len(rets) > 4:
                disp_by_ts[ts] = float(np.std(rets))

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    def btc_ret(ts: int, lookback: int) -> float:
        if ts not in btc_by_ts:
            return 0.0
        i = btc_by_ts[ts]
        if i < lookback or btc_closes[i - lookback] <= 0:
            return 0.0
        return (btc_closes[i] / btc_closes[i - lookback] - 1) * 1e4

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0.0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        # ── EXITS ──
        for coin in list(positions.keys()):
            pos = positions[coin]
            if ts not in coin_by_ts.get(coin, {}):
                continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0:
                continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0:
                continue

            # Track MFE (best unrealized) and MAE (worst unrealized)
            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
                worst_bps = (candle["l"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
                worst_bps = -(candle["h"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0):
                pos["mfe"] = best_bps
            if worst_bps < pos.get("mae", 0):
                pos["mae"] = worst_bps

            # Per-strategy stop in price-move bps (not leveraged)
            if pos["strat"] == "S8":
                stop = STOP_LOSS_S8
            elif pos.get("stop", 0) != 0:
                stop = pos["stop"]
            else:
                stop = STOP_LOSS_BPS

            exit_reason = None
            exit_price = current
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - stop / 1e4)

            # Runner extension: at the natural timeout, if the position is
            # still showing strong upside (high MFE retained), extend hold by
            # `extra_candles`. Idea: catch winners still riding momentum at
            # end of normal hold. Only fires once (uses pos["extended"]).
            # NOTE: order mirrors `trading.check_exits` in the live bot —
            # runner extension first (pre-timeout), then timeout, then stop /
            # S9 early / S10 trailing, dead_timeout last. With the current
            # thresholds runner_ext (mfe ≥ 1200) and dead_timeout (mfe ≤ 150)
            # are mutually exclusive, but keeping the order aligned protects
            # against silent divergences if thresholds change.
            if (held >= pos["hold"] and not pos.get("extended", False)
                    and runner_extension is not None):
                strats = runner_extension.get("strategies")
                if strats is None or pos["strat"] in strats:
                    cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    mfe_bps = pos.get("mfe", 0)
                    min_mfe = runner_extension.get("min_mfe_bps", 500)
                    min_ratio = runner_extension.get("min_cur_to_mfe", 0.5)
                    if mfe_bps >= min_mfe and cur_bps / mfe_bps >= min_ratio:
                        pos["hold"] += runner_extension.get("extra_candles", 6)
                        pos["extended"] = True

            if held >= pos["hold"]:
                exit_reason = exit_reason or "timeout"

            # S9 early exit: cut if not reverting after S9_EARLY_EXIT_HOURS
            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"

            # S10 trailing stop: lock gains when MFE exceeds trigger
            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps <= mfe - S10_TRAILING_OFFSET:
                        exit_reason = "s10_trailing"

            # Dead-timeout early exit (option D): if trade is close to timeout,
            # has never shown meaningful MFE, and is still pinned near its MAE,
            # crystallize the loss now instead of waiting for timeout at MAE.
            # Mirrors live bot order — checked LAST, after timeout / stop /
            # s9_early / s10_trailing. (Was placed earlier in the chain in
            # earlier versions; relocated here for bot-vs-backtest parity.)
            if (not exit_reason and early_exit_params is not None
                    and held >= pos["hold"] - early_exit_params["exit_lead_candles"]):
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                mfe = pos.get("mfe", 0.0)
                mae = pos.get("mae", 0.0)
                if (mfe <= early_exit_params["mfe_cap_bps"]
                        and mae <= early_exit_params["mae_floor_bps"]
                        and cur_bps <= mae + early_exit_params["slack_bps"]):
                    exit_reason = "dead_timeout"
                    exit_price = current

            # Optional extra-strategy trailing stop (sweep parameter)
            if (not exit_reason and trailing_extra is not None
                    and pos["strat"] == trailing_extra["strategy"]):
                mfe = pos.get("mfe", 0)
                if mfe >= trailing_extra["trigger_bps"]:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps <= mfe - trailing_extra["offset_bps"]:
                        exit_reason = f"{trailing_extra['strategy'].lower()}_trailing"

            # Optional early-MFE-absence exit (sweep parameter): if after N
            # candles the trade has never shown meaningful upside (MFE < min),
            # exit. Different from D2 (which fires at T-K before timeout) and
            # from MAE-cry-uncle (which uses MAE only). Targets the pattern
            # where big losers never cross MFE +303 in live data.
            if (not exit_reason and early_mfe_exit is not None
                    and held == early_mfe_exit["check_after_candles"]):
                strat_ok = (early_mfe_exit.get("strategies") is None
                            or pos["strat"] in early_mfe_exit["strategies"])
                if strat_ok and pos.get("mfe", 0) < early_mfe_exit["mfe_min_bps"]:
                    exit_reason = "early_mfe_absence"

            # Optional BTC-correlation exit (sweep parameter): cut a position
            # when BTC moves >= threshold_bps against the trade direction
            # within `lookback_h` hours of entry. Captures the "alts follow
            # BTC" effect: when BTC dumps mid-LONG-hold (or rallies mid-SHORT),
            # the position is mathematically going deeper underwater.
            #   threshold_bps: BTC adverse-move threshold (positive value)
            #   lookback_h:    max hold-hours during which the rule fires
            #                  (None = active for full hold)
            #   apply_long / apply_short: direction scope
            if (not exit_reason and btc_corr_exit is not None):
                d_apply = ((pos["dir"] == 1 and btc_corr_exit.get("apply_long"))
                           or (pos["dir"] == -1 and btc_corr_exit.get("apply_short")))
                lb_h = btc_corr_exit.get("lookback_h")
                held_h = held * 4  # candles to hours (4h candles)
                if d_apply and (lb_h is None or held_h <= lb_h):
                    e_idx = btc_by_ts.get(pos["entry_t"])
                    c_idx = btc_by_ts.get(ts)
                    if e_idx is not None and c_idx is not None:
                        btc_r = (btc_closes[c_idx] / btc_closes[e_idx] - 1) * 1e4
                        adverse = ((pos["dir"] == 1 and btc_r <= -btc_corr_exit["threshold_bps"])
                                   or (pos["dir"] == -1 and btc_r >= btc_corr_exit["threshold_bps"]))
                        if adverse:
                            exit_reason = "btc_corr_exit"

            # Optional reversal-momentum exit (sweep parameter): exit if the
            # price has moved hard against us in the last N candles AND we're
            # currently in profit. Doesn't rely on entry signal erosion — uses
            # the price tape itself as a reversal detector.
            if (not exit_reason and reversal_exit is not None
                    and held >= reversal_exit["lookback_candles"]):
                strat_ok = (reversal_exit.get("strategies") is None
                            or pos["strat"] in reversal_exit["strategies"])
                if strat_ok:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps >= reversal_exit["min_gain_bps"]:
                        n = reversal_exit["lookback_candles"]
                        prev_close = data[coin][ci - n]["c"]
                        if prev_close > 0:
                            adverse_bps = pos["dir"] * (current / prev_close - 1) * 1e4
                            if adverse_bps <= -reversal_exit["adverse_bps"]:
                                exit_reason = "reversal_momentum"

            if exit_reason:
                # P&L math matches trading.py close_position (v11.3.0+)
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                # Real funding (v11.7.6): subtract sum of hourly funding payments
                if funding_data is not None:
                    funding_cost = compute_funding_cost(
                        funding_data, coin, pos["dir"],
                        pos["entry_t"], ts, pos["size"])
                    pnl -= funding_cost
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── ENTRIES ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            ret_24h = f.get("ret_6h", 0)  # 6 candles of 4h = 24h

            if btc30 > 2000:
                candidates.append({
                    "coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                    "strength": max(f.get("ret_42h", 0), 0),
                })

            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({
                    "coin": coin, "dir": 1 if sf["divergence"] > 0 else -1, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                    "strength": abs(sf["divergence"]),
                })

            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                candidates.append({
                    "coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                    "strength": abs(f["drawdown"]),
                })

            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                candidates.append({
                    "coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                    "strength": abs(ret_24h), "stop": s9_stop,
                })

            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    s10_block = ((not S10_ALLOW_LONGS and sq_dir == 1)
                                 or coin not in S10_ALLOWED_TOKENS)
                    if not s10_block:
                        candidates.append({
                            "coin": coin, "dir": sq_dir, "strat": "S10",
                            "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                            "strength": 1000,
                        })

        # Optional extra candidates from a callback (used for new-signal sweeps).
        # Callback signature: fn(ts, coins, feat_by_ts, data, coin_by_ts, positions, cooldown) -> list[cand]
        if extra_candidate_fn is not None:
            candidates.extend(extra_candidate_fn(ts, coins, feat_by_ts, data,
                                                  coin_by_ts, positions, cooldown))

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            # v11.4.10 blacklist
            if coin in TRADE_BLACKLIST:
                continue
            # v11.4.9 OI gate LONG
            if cand["dir"] == 1 and oi_data is not None:
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                if oi_d is not None and oi_d < -OI_LONG_GATE_BPS:
                    continue
            # v11.7.28 dispersion gate — skip mean-reversion in regime breakdowns
            if cand["strat"] in DISP_GATE_STRATEGIES:
                d = disp_by_ts.get(ts)
                if d is not None and d >= DISP_GATE_BPS:
                    continue
            if skip_fn is not None and skip_fn(coin, ts, cand["strat"], cand["dir"]):
                continue
            if len(positions) >= MAX_POSITIONS:
                break
            if cand["dir"] == 1 and n_long >= MAX_SAME_DIRECTION:
                continue
            if cand["dir"] == -1 and n_short >= MAX_SAME_DIRECTION:
                continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO_SLOTS:
                continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN_SLOTS:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_PER_SECTOR:
                    continue
                # Optional rule: block entries opposite to an existing same-sector position
                if block_opposite_sector:
                    same_sec_dirs = [p["dir"] for p in positions.values()
                                     if TOKEN_SECTOR.get(p["coin"]) == sym_sector]
                    if same_sec_dirs and any(d != cand["dir"] for d in same_sec_dirs):
                        continue

            f = feat_by_ts.get(ts, {}).get(coin)
            idx_f = f.get("_idx") if f else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            size = strat_size(cand["strat"], capital)
            if size_multiplier is not None:
                size *= size_multiplier.get(cand["strat"], 1.0)
            # v11.7.28+ experimental: per-candidate size adjustment hook
            # Signature: size_fn(cand, feature_dict, n_positions) -> multiplier
            if size_fn is not None:
                size *= size_fn(cand, f, len(positions))
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0, "mae": 0.0,
            }
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1

    # Close remaining positions at the last available candle (mark-to-market)
    for coin in list(positions.keys()):
        pos = positions[coin]
        last_ts = max(t for t in coin_by_ts[coin] if t <= end_ts_ms)
        last_idx = coin_by_ts[coin][last_ts]
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            if funding_data is not None:
                funding_cost = compute_funding_cost(
                    funding_data, coin, pos["dir"],
                    pos["entry_t"], last_ts, pos["size"])
                pnl -= funding_cost
            capital += pnl
            trades.append({
                "pnl": pnl, "net": net, "dir": pos["dir"],
                "strat": pos["strat"], "coin": coin,
                "entry_t": pos["entry_t"], "exit_t": last_ts,
                "reason": "mtm_final", "size": pos["size"],
            })

    # Summary stats
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    by_strat: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1

    best_strat = max(by_strat.items(), key=lambda kv: kv[1]["pnl"])[0] if by_strat else "-"

    return {
        "start_capital": start_capital,
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "by_strat": {k: {
            "n": v["n"],
            "pnl": round(v["pnl"], 2),
            "wr": round(v["wins"] / v["n"] * 100, 0) if v["n"] else 0,
        } for k, v in by_strat.items()},
        "best_strat": best_strat,
        "trades": trades,
    }


# ── Rolling runner & report writer ─────────────────────────────────────

def rolling_windows(end_dt: datetime) -> list[tuple[str, datetime]]:
    """Return (label, start_dt) pairs for standard rolling windows + monthly starts."""
    windows = [
        ("28 mois", end_dt - relativedelta(months=28)),
        ("12 mois", end_dt - relativedelta(months=12)),
        ("6 mois", end_dt - relativedelta(months=6)),
        ("3 mois", end_dt - relativedelta(months=3)),
        ("1 mois", end_dt - relativedelta(months=1)),
    ]
    # Monthly start points for the last 6 calendar months
    for i in range(6, 0, -1):
        month_start = (end_dt.replace(day=1) - relativedelta(months=i - 1))
        if month_start < end_dt:
            windows.append((f"depuis {month_start.strftime('%Y-%m-%d')}", month_start))
    return windows


def fmt_dollar(v: float) -> str:
    return f"${v:,.0f}".replace(",", " ")


def build_report(results: list[dict], end_dt: datetime, version: str,
                 capitals: list[float] | None = None) -> str:
    capitals = capitals or [1000.0]
    multi = len(capitals) > 1
    cap_phrase = (" / ".join(f"${int(c):,}".replace(",", " ") for c in capitals)
                  if multi else f"${int(capitals[0]):,}".replace(",", " "))
    lines = [
        f"# Rolling backtests",
        "",
        f"**Générée le** : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Bot version** : v{version}",
        f"**Données jusqu'à** : {end_dt.strftime('%Y-%m-%d')}",
        f"**Capitaux testés** : {cap_phrase}",
        "",
        "Chaque ligne répond à la question : *si j'avais lancé le bot avec "
        f"{cap_phrase} au début de cette fenêtre jusqu'à la date des données, avec "
        "les paramètres actuels du bot, combien aurais-je fini ?*",
        "",
        "P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le "
        "notionnel, pas de multiplication par le levier).",
        "",
        f"**Coûts backtest** : {COST:.0f} bps round-trip = {COST_BPS:.0f} bps "
        f"(taker {TAKER_FEE_BPS:.0f} + funding {FUNDING_DRAG_BPS:.0f}, "
        f"calibrés depuis les fills live) + {BACKTEST_SLIPPAGE_BPS:.0f} bps "
        "de slippage moyen que le backtest doit modéliser puisqu'il utilise "
        "les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique "
        f"que {COST_BPS:.0f} bps car le slippage est déjà dans l'avgPx.",
        "",
        "Ce fichier est **régénéré automatiquement** par "
        "`python3 -m backtests.backtest_rolling`. Relancer après tout changement "
        "de règles ou de paramètres du bot.",
        "",
        f"## Filtres actifs (v{version})",
        "",
        f"**S10 filters** (v11.3.4)",
        f"- `S10_ALLOW_LONGS = {S10_ALLOW_LONGS}` → "
        f"{'SHORT fades seulement' if not S10_ALLOW_LONGS else 'LONG+SHORT'} "
        "(LONG fades perdaient $4.8k sur 28m, 45% WR — *fade panic = fail*)",
        f"- `S10_ALLOWED_TOKENS` (whitelist de {len(S10_ALLOWED_TOKENS)} tokens) : "
        f"{', '.join(sorted(S10_ALLOWED_TOKENS))}",
        "",
        "Dérivés de `backtest_s10_walkforward.py` (train 2023-10→2025-02, "
        "test 2025-02→2026-02 OOS). Impact OOS : P&L +123% vs baseline, DD −8.7pp.",
        "",
        f"**OI gate LONG** (v11.4.9) — `OI_LONG_GATE_BPS = {OI_LONG_GATE_BPS:.0f}`",
        "- Skip LONG entries quand `Δ(OI, 24h) < -10%`. Longs qui se débouclent = "
        "flow baissier encore actif = LONG catche un couteau qui tombe.",
        "- Validé walk-forward 4/4 : +$2 498 / +$816 / +$380 / +$252 sur 28m/12m/6m/3m, "
        "zéro impact DD. Helper : `features.oi_delta_24h_bps()`.",
        "- Source : `backtests/backtest_external_gates.py`, `backtests/backtest_oi_gate_validate.py`.",
        "",
        f"**Trade blacklist** (v11.4.10) — `TRADE_BLACKLIST = {{{', '.join(sorted(TRADE_BLACKLIST))}}}`",
        "- Tokens net-négatifs sur les 4 fenêtres walk-forward : SUI (−$5 311 28m, "
        "−$1 045 12m, −$336 6m, −$98 3m), IMX (−$2 952 / −$566 / −$156 / −$53), "
        "LINK (−$2 415 / −$387 / −$185 / −$75).",
        "- Validé sur `backtest_rolling` : +91% sur 28m (+$49 687), +63% 12m, +34% 6m, +18% 3m.",
        "- DD 28m dégradée de ~10pp (swings absolus plus grands sur un capital plus haut), "
        "DD améliorée ou inchangée sur toutes les fenêtres récentes.",
        "- Source : `backtests/backtest_worst_losers.py`, `backtests/backtest_loser_filters.py`.",
        "- Kill-switch (réactiver un token) : supprimer de `TRADE_BLACKLIST` dans `analysis/bot/config.py`.",
        "",
        "## Résumé par fenêtre",
        "",
    ]
    if multi:
        lines += [
            "| Fenêtre | Start | Capital | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            pnl_sign = "+" if r["pnl"] >= 0 else ""
            cap = r.get("start_capital", capitals[0])
            lines.append(
                f"| {r['label']} | {r['start_date']} | "
                f"{fmt_dollar(cap)} | "
                f"{fmt_dollar(r['end_capital'])} | "
                f"{pnl_sign}{fmt_dollar(r['pnl'])} | "
                f"{pnl_sign}{r['pnl_pct']:.1f}% | "
                f"{r['max_dd_pct']:.1f}% | "
                f"{r['n_trades']} | "
                f"{r['win_rate']:.0f}% | "
                f"{r['best_strat']} |"
            )
    else:
        lines += [
            "| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            pnl_sign = "+" if r["pnl"] >= 0 else ""
            lines.append(
                f"| {r['label']} | {r['start_date']} | "
                f"{fmt_dollar(r['end_capital'])} | "
                f"{pnl_sign}{fmt_dollar(r['pnl'])} | "
                f"{pnl_sign}{r['pnl_pct']:.1f}% | "
                f"{r['max_dd_pct']:.1f}% | "
                f"{r['n_trades']} | "
                f"{r['win_rate']:.0f}% | "
                f"{r['best_strat']} |"
            )

    # Per-strategy breakdown on the longest window (using the largest capital
    # if multiple, since absolute P&L is more meaningful at the higher base).
    if results:
        breakdown_cap = max(capitals)
        breakdown_candidates = [r for r in results
                                if r.get("start_capital") == breakdown_cap]
        longest = breakdown_candidates[0] if breakdown_candidates else results[0]
        cap_str = fmt_dollar(longest.get("start_capital", capitals[0]))
        lines += [
            "",
            f"## Breakdown par stratégie sur la fenêtre la plus longue ({longest['label']}, capital {cap_str})",
            "",
            "| Stratégie | Trades | Win Rate | P&L |",
            "|---|---|---|---|",
        ]
        for s, d in sorted(longest["by_strat"].items()):
            pnl_sign = "+" if d["pnl"] >= 0 else ""
            lines.append(f"| {s} | {d['n']} | {d['wr']:.0f}% | {pnl_sign}{fmt_dollar(d['pnl'])} |")

    lines += [
        "",
        "## Méthodologie",
        "",
        "- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.",
        "- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.",
        "- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, "
        "`SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement "
        "reflété au prochain run.",
        "- **Entry timing** : open de la bougie suivante (no look-ahead).",
        "- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. "
        "S9 early exit si unrealized < "
        f"{S9_EARLY_EXIT_BPS:.0f} bps après {S9_EARLY_EXIT_HOURS:.0f}h.",
        "- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.",
        "- **Costs** : "
        f"{COST:.0f} bps par trade round-trip ({TAKER_FEE_BPS:.0f} taker + "
        f"{FUNDING_DRAG_BPS:.0f} funding + {BACKTEST_SLIPPAGE_BPS:.0f} slippage "
        "backtest). Pas de multiplication par le levier.",
        "",
        "## Limites",
        "",
        "- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. "
        "Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne "
        "sont pas disponibles dans l'historique → cette dimension est absente du backtest.",
        "- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique "
        f"un coût fixe de {COST_BPS:.0f} bps.",
        "- Pas de modélisation des funding rates variables — on utilise le coût moyen.",
        "- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, "
        "S1 rarement. Prendre les résultats avec précaution.",
    ]
    return "\n".join(lines) + "\n"


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    print(f"Loaded {len(data)} coins, {sum(len(f) for f in features.values())} feature points")

    print("Computing sector features...")
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    print(f"Loaded OI for {len(oi_data)} coins (for v11.4.9 OI gate)")
    funding_data = load_funding()
    print(f"Loaded funding history for {len(funding_data)} coins (v11.7.6 real funding cost)")

    # Determine end_ts as the latest available candle
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.isoformat()}")

    windows = rolling_windows(end_dt)
    # Default-activate D2 (v11.7.2 dead-timeout exit) to mirror live bot behavior.
    # Convert 12h lead to candles (4h each) = 3 candles. Other params come from config.
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    )
    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    print(f"D2 dead-timeout exit active: {early_exit_params}")

    # v11.7.32 runner extension — mirror production config so backtest reflects
    # the live bot's behavior. Empty RUNNER_EXT_STRATEGIES disables the rule.
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)
    if runner_ext_cfg:
        print(f"Runner extension active: {runner_ext_cfg}")

    # BACKTEST_CAPITALS env var: comma-separated list of starting capitals.
    # Default "1000" (single-capital, legacy behavior). E.g. "500,1000" runs
    # each window with both capitals and produces a side-by-side table.
    cap_env = os.environ.get("BACKTEST_CAPITALS", "1000")
    capitals = [float(c.strip()) for c in cap_env.split(",") if c.strip()]
    if not capitals:
        capitals = [1000.0]
    print(f"Capitals: {capitals}")

    results = []
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = latest_ts
        for cap in capitals:
            tag = f"  Running {label} (${cap:.0f}, {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')})..."
            print(tag)
            r = run_window(features, data, sector_features, dxy_data, start_ts, end_ts,
                           start_capital=cap,
                           oi_data=oi_data, early_exit_params=early_exit_params,
                           runner_extension=runner_ext_cfg,
                           funding_data=funding_data)
            r["label"] = label
            r["start_date"] = start_dt.strftime("%Y-%m-%d")
            results.append(r)
            print(f"    → {r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%), "
                  f"{r['n_trades']} trades, DD {r['max_dd_pct']:.1f}%")

    # Sort by (start_date asc, capital asc) so window groups stay consecutive
    results.sort(key=lambda x: (x["start_date"], x["start_capital"]))

    report = build_report(results, end_dt, VERSION, capitals=capitals)
    os.makedirs(os.path.dirname(DOCS_PATH), exist_ok=True)
    with open(DOCS_PATH, "w") as f:
        f.write(report)
    print(f"\nReport written to {DOCS_PATH}")


if __name__ == "__main__":
    main()
