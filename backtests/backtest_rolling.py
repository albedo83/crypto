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

# Alfred shared core — single source of truth for every rule and parameter.
# The same rules/signals modules drive the live runtime; the historical
# bot-vs-backtest re-implementations are gone (Alfred phase 1).
import dataclasses as _dc

from alfred.settings import DEFAULT_PARAMS
from alfred import rules as _rules
from alfred import signals as _alf_signals

# Bot version label for the report header (the bot runtime still lives in
# analysis/bot/ until Alfred phase 5 — this is the only remaining import).
from analysis.bot.config import VERSION

_P = DEFAULT_PARAMS

# Module-level aliases: many research scripts import these names from
# backtest_rolling (and the report builder uses them). Values come from
# alfred Params — keep the names, change the source.
TOKEN_SECTOR = _P.token_sector()
SECTORS = _P.sectors
SIZE_PCT, SIZE_BONUS = _P.size_pct, _P.size_bonus
STRAT_Z = _P.strat_z
SIGNAL_MULT = _P.signal_mult
LIQUIDITY_HAIRCUT = _P.liquidity_haircut
LEVERAGE = _P.leverage
COST_BPS = _P.cost_bps
TAKER_FEE_BPS = _P.taker_fee_bps
FUNDING_DRAG_BPS = _P.funding_drag_bps
MAX_POSITIONS = _P.max_positions
MAX_SAME_DIRECTION = _P.max_same_direction
MAX_PER_SECTOR = _P.max_per_sector
MAX_MACRO_SLOTS = _P.max_macro_slots
MAX_TOKEN_SLOTS = _P.max_token_slots
MACRO_STRATEGIES = _P.macro_strategies
STOP_LOSS_BPS = _P.stop_loss_bps
STOP_LOSS_S8 = _P.stop_loss_s8
S9_EARLY_EXIT_BPS = _P.s9_early_exit_bps
S9_EARLY_EXIT_HOURS = _P.s9_early_exit_hours
HOLD_HOURS_DEFAULT = _P.hold_hours_for("S1")
HOLD_HOURS_S5 = _P.hold_hours_for("S5")
HOLD_HOURS_S8 = _P.hold_hours_for("S8")
HOLD_HOURS_S9 = _P.hold_hours_for("S9")
HOLD_HOURS_S10 = _P.hold_hours_for("S10")
S5_DIV_THRESHOLD = _P.s5_div_threshold
S5_VOL_Z_MIN = _P.s5_vol_z_min
S8_DRAWDOWN_THRESH = _P.s8_drawdown_thresh
S8_VOL_Z_MIN = _P.s8_vol_z_min
S8_RET_24H_THRESH = _P.s8_ret_24h_thresh
S8_BTC_7D_THRESH = _P.s8_btc_7d_thresh
S9_RET_THRESH = _P.s9_ret_thresh
S9_ADAPTIVE_STOP = _P.s9_adaptive_stop
S10_SQUEEZE_WINDOW = _P.s10_squeeze_window
S10_VOL_RATIO_MAX = _P.s10_vol_ratio_max
S10_BREAKOUT_PCT = _P.s10_breakout_pct
S10_REINT_CANDLES = _P.s10_reint_candles
S10_ALLOW_LONGS = _P.s10_allow_longs
S10_ALLOWED_TOKENS = _P.s10_allowed_tokens
S10_TRAILING_TRIGGER = _P.s10_trailing_trigger
S10_TRAILING_OFFSET = _P.s10_trailing_offset
OI_LONG_GATE_BPS = _P.oi_long_gate_bps
TRADE_BLACKLIST = _P.trade_blacklist
DISP_GATE_BPS = _P.disp_gate_bps
DISP_GATE_STRATEGIES = _P.disp_gate_strategies
RUNNER_EXT_STRATEGIES = _P.runner_ext_strategies
RUNNER_EXT_HOURS = _P.runner_ext_hours
RUNNER_EXT_MIN_MFE_BPS = _P.runner_ext_min_mfe_bps
RUNNER_EXT_MIN_CUR_TO_MFE = _P.runner_ext_min_cur_to_mfe
ADAPTIVE_ALPHA = _P.adaptive_alpha
MACRO_LOOKBACK_DAYS = _P.macro_lookback_days
MACRO_Z_WINDOW_DAYS = _P.macro_z_window_days
MACRO_Z_CLIP = _P.macro_z_clip
MACRO_MULT_MIN = _P.macro_mult_min
MACRO_MULT_MAX = _P.macro_mult_max
get_adaptive_alpha = _P.get_adaptive_alpha
S8_INLIFE_PARAMS = _P.s8_inlife_params
S8_INLIFE_Z_THRESHOLD = _P.s8_inlife_z_threshold
S8_DEAD_T_H = _P.s8_dead_t_h
S8_DEAD_MFE_MAX_BPS = _P.s8_dead_mfe_max_bps
TRAJ_CUT_STRATEGIES = _P.traj_cut_strategies
TRAJ_CUT_BTC_Z_THRESHOLD = _P.traj_cut_btc_z_threshold
TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H = _P.traj_cut_decline_rate_min_bps_per_h
TRAJ_CUT_TIME_SINCE_MFE_MIN_H = _P.traj_cut_time_since_mfe_min_h
TRAJ_CUT_AT_MAE_SLACK_BPS = _P.traj_cut_at_mae_slack_bps
TRAJ_CUT_MIN_LOSS_BPS = _P.traj_cut_min_loss_bps
S9_EARLY_DEAD_T_H = _P.s9_early_dead_t_h
S9_EARLY_DEAD_MFE_MAX_BPS = _P.s9_early_dead_mfe_max_bps
BTC_DROP_CUT_RET_4H_BPS = _P.btc_drop_cut_ret_4h_bps
BTC_DROP_CUT_UR_MAX_BPS = _P.btc_drop_cut_ur_max_bps
DEAD_TIMEOUT_LEAD_HOURS = _P.dead_timeout_lead_hours
DEAD_TIMEOUT_MFE_CAP_BPS = _P.dead_timeout_mfe_cap_bps
DEAD_TIMEOUT_MAE_FLOOR_BPS = _P.dead_timeout_mae_floor_bps
DEAD_TIMEOUT_SLACK_BPS = _P.dead_timeout_slack_bps
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

# Notional cap per trade (override via BACKTEST_MAX_NOTIONAL env var).
# Live HL orderbook depth on thin alts caps realistic trade size; without this
# cap the compound simulation reports ROIs that aren't reachable in production.
# Set to 0 (or negative) to disable.
BACKTEST_MAX_NOTIONAL = float(os.environ.get("BACKTEST_MAX_NOTIONAL", "20000"))


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


def detect_squeeze(candles, idx, vol_ratio, candle_scale: int = 1):
    """S10 squeeze detection — shared implementation (alfred.signals).

    Returns the fade direction (±1) or None, preserving this module's
    historical int return type for downstream callers.
    """
    res = _alf_signals.detect_squeeze_at(candles, idx, vol_ratio, _P,
                                         candle_scale=candle_scale)
    return res["direction"] if res else None


def strat_size(strat: str, capital: float) -> float:
    """Base size via the shared core (alfred.rules.base_size), plus the
    backtest-only notional cap (BACKTEST_MAX_NOTIONAL env var; models live
    orderbook depth). NOTE: this cap applies BEFORE the adaptive modulator,
    unlike the live bot's MAX_NOTIONAL_PER_TRADE which applies after —
    legacy semantics kept for iso-validation (see docs/alfred_divergences.md)."""
    return _rules.base_size(
        strat, capital, _P,
        cap=BACKTEST_MAX_NOTIONAL if BACKTEST_MAX_NOTIONAL > 0 else None)


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
               partial_profit: dict | None = None,
               giveback: dict | None = None,
               stop_override: dict | None = None,
               early_mae_exit: dict | None = None,
               interval_hours: int = 4,
               entry_align_hours: int = 0,
               smooth_mfe_hours: int = 0,
               funding_data: dict | None = None,
               apply_adaptive_modulator: bool = False,
               inlife_exit_extra=None,
               basket_haircut_fn=None,
               proportional_trail: dict | None = None,
               trajectory_dump_path: str | None = None,
               cooldown_hours: float = 24.0,
               cooldown_by_strat: dict | None = None,
               mid_trade_dump_path: str | None = None,
               mid_trade_checkpoints_h: tuple[int, ...] = (4, 8, 12, 24),
               max_notional_per_trade: float | None = None,
               margin_check: bool = False,
               margin_max_util: float = 0.95,
               early_dead_check: dict[str, tuple[float, float]] | None = None,
               btc_z_variant: str = "baseline",
               max_notional_fn=None,
               bear_derisk: tuple | None = None,
               opposite_cut: dict | None = None,
               take_profit: dict | None = None,
               prop_trail_override: dict | None = None,
               skip_log: list | None = None,
               opp_block_log: list | None = None,
               mfe_on_close: bool = False,
               reserve_highz_frac: float = 0.0,
               reserve_z_threshold: float = 5.0,
               entry_slip_bps_by_strat: dict | None = None,
               aligned: bool = False) -> dict:
    """Run the portfolio backtest on a time window.

    `interval_hours` (default 4) tells the engine how many hours each candle
    represents. Used to scale HOLD_CANDLES, S9_EARLY_EXIT_CANDLES, and the
    DEAD_TIMEOUT lead so the same TIME-based hold/exit semantics apply
    regardless of candle granularity. For a 1h backtest, pass interval_hours=1.

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

    inlife_exit_extra (optional): research hook for in-life exit rules. A
        callable that receives a per-position snapshot dict and returns
        (should_exit: bool, reason: str). Invoked in the exit chain just
        BEFORE the dead_timeout block so rollback rules can fire while MFE
        is still recent. Snapshot schema:
            symbol            (str)   pos["coin"]
            strat             (str)   pos["strat"]
            dir               (int)   +1 LONG / -1 SHORT
            hold_h            (float) hours since entry
            hold_max_h        (float) max hold in hours
            mfe_bps           (float) best unrealized in bps
            mae_bps           (float) worst unrealized in bps
            cur_bps           (float) current unrealized in bps
            time_since_mfe_h  (float) hours since last new MFE high
            btc_z             (float) BTC z-score at ts (0.0 if unavailable)
            ts_ms             (int)   current candle timestamp (ms)
            trade_id          (int)   unique id for per-position state caching
            time_in_pain_pct  (float) % of candles where close gave ur<0
            sector_div_delta  (float) sector_divergence(now) - at_entry, NaN
                                     if entry/now sector feature missing
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

    # v12.15.0 — Pre-compute BTC last-4h-candle return per ts for btc_drop_cut.
    # BTC isn't in `coins` (it's a REF_TOKEN), so we index it separately.
    btc_ret_4h_by_ts: dict[int, float] = {}
    if "BTC" in data:
        _btc_arr = data["BTC"]
        for i in range(1, len(_btc_arr)):
            _p_prev = _btc_arr[i - 1]["c"]
            _p_curr = _btc_arr[i]["c"]
            if _p_prev > 0 and _p_curr > 0:
                btc_ret_4h_by_ts[_btc_arr[i]["t"]] = (_p_curr / _p_prev - 1) * 1e4

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

    # EDA hook (feature_modulator_eda): precompute n_stress_global per ts —
    # mirrors signals.compute_cross_context's stress count (vol_z>1.5 AND
    # drawdown<-1500). Used to reconstruct entry_confluence_partial at open
    # time so the trade dataset can be analyzed for feature→outcome signal.
    n_stress_by_ts: dict[int, int] = {}
    for ts, fmap in feat_by_ts.items():
        n_stress_by_ts[ts] = sum(
            1 for f in fmap.values()
            if f.get("vol_z", 0) > 1.5 and f.get("drawdown", 0) < -1500
        )

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    # basket_haircut_eda: precompute per-coin candle returns (P&L correlation
    # source). Each coin gets a numpy array of (close[i]/close[i-1] - 1) aligned
    # by global ts. Used by the at-open effective_n backfill (3 windows: 7d /
    # 14d / 30d). Returns expressed in absolute units (not bps) — corrcoef is
    # scale-invariant anyway. Length matches candles_per_coin; index 0 → NaN.
    coin_rets: dict[str, np.ndarray] = {}
    for coin in coins:
        closes = np.array([c["c"] for c in data[coin]], dtype=float)
        if len(closes) < 2:
            coin_rets[coin] = np.array([], dtype=float)
            continue
        prev = closes[:-1]
        cur = closes[1:]
        # safe divide: 0 → 0
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(prev > 0, cur / np.where(prev > 0, prev, 1) - 1, 0.0)
        # prepend a 0 so r[i] corresponds to candle i (return from i-1 → i)
        coin_rets[coin] = np.concatenate([[0.0], r])

    # 3-window effective_n configuration (4h candles, 6 per day)
    EFFN_WINDOWS_D = (7, 14, 30)
    effn_lookbacks = {d: d * 6 for d in EFFN_WINDOWS_D}  # 42 / 84 / 180

    def _compute_effective_n(positions_dict: dict, ts: int) -> dict[int, float | None]:
        """Sign-adjusted effective_n (P&L correlation) for 3 windows.

        Mirrors features.compute_basket_correlation exactly:
          - signed pairwise corr: ρ_pnl[i,j] = dir_i * dir_j * ρ_price[i,j]
          - eff_n = n² / sum(signed_mat with diag=1), clamped to [1, n]
          - if sum ≤ 0: eff_n = n (fully over-hedged)
        Returns {window_days: value_or_None}. None when < 2 positions or
        insufficient candle history for that window.
        """
        out: dict[int, float | None] = {d: None for d in EFFN_WINDOWS_D}
        n = len(positions_dict)
        if n < 1:
            return out
        if n == 1:
            for d in EFFN_WINDOWS_D:
                out[d] = 1.0
            return out

        # Resolve each position's candle index at ts
        symbols: list[str] = []
        dirs: list[int] = []
        idxs: list[int] = []
        for coin, pos in positions_dict.items():
            ci_map = coin_by_ts.get(coin)
            if not ci_map or ts not in ci_map:
                continue
            ci = ci_map[ts]
            if ci < 1 or coin not in coin_rets or len(coin_rets[coin]) <= ci:
                continue
            symbols.append(coin)
            dirs.append(pos["dir"])
            idxs.append(ci)

        if len(symbols) < 2:
            # fall back to 1.0 / None for empty
            if len(symbols) == 1:
                for d in EFFN_WINDOWS_D:
                    out[d] = 1.0
            return out

        dirs_arr = np.array(dirs, dtype=float)
        n_eff = len(symbols)
        for d in EFFN_WINDOWS_D:
            lb = effn_lookbacks[d]
            # All positions must have lb candles of history available
            if min(idxs) < lb:
                continue
            # Build n × lb return matrix (last lb returns ending at ts)
            R = np.empty((n_eff, lb), dtype=float)
            ok = True
            for i, (sym, ci) in enumerate(zip(symbols, idxs)):
                row = coin_rets[sym][ci - lb + 1: ci + 1]
                if len(row) != lb or float(np.std(row)) == 0:
                    ok = False
                    break
                R[i] = row
            if not ok:
                continue
            corr_mat = np.corrcoef(R)
            if np.isnan(corr_mat).any():
                corr_mat = np.nan_to_num(corr_mat, nan=0.0)
            signed_mat = corr_mat * np.outer(dirs_arr, dirs_arr)
            np.fill_diagonal(signed_mat, 1.0)
            total = float(signed_mat.sum())
            if total <= 0:
                eff = float(n_eff)
            else:
                eff = max(1.0, min(float(n_eff), (n_eff * n_eff) / total))
            out[d] = round(eff, 3)
        return out

    # basket_haircut_eda: per-ts time series of open-basket state. Recorded
    # at start of each ts iteration BEFORE entries/exits fire so the value
    # reflects positions held over the prior 4h candle. Each entry is a dict
    # with {ts, n_positions, eff_n_7d, eff_n_14d, eff_n_30d, basket_unrealized,
    # capital}. Written to a JSONL file when basket_haircut_eda_dump is set.
    basket_timeseries: list[dict] = []
    basket_dump_path = os.environ.get("BASKET_HAIRCUT_EDA_DUMP", "")

    # v11.10.0 adaptive macro modulator (mirrors live bot exactly).
    # Precompute btc_z per ts so the modulator can be applied at entry time.
    # Only built when no custom size_fn (caller wants the canonical behavior).
    btc_z_map: dict[int, float] = {}
    cpd = max(1, 24 // max(1, interval_hours))  # candles per day
    n_lb = MACRO_LOOKBACK_DAYS * cpd
    n_zw = MACRO_Z_WINDOW_DAYS * cpd
    # Build btc_z_map when adaptive modulator, trajectory dump, regime-aware
    # proportional trail, S8 in-life trail, or trajectory cut is active.
    # Needed any time the engine reads btc_z at a tick.
    _need_btc_z = (
        apply_adaptive_modulator
        or trajectory_dump_path is not None
        or (proportional_trail is not None and "by_regime" in proportional_trail)
        or (S8_INLIFE_PARAMS and any(v != (99999, 0) for v in S8_INLIFE_PARAMS.values()))
        or TRAJ_CUT_STRATEGIES
    )
    if _need_btc_z and size_fn is None and len(btc_closes) >= n_lb + 30:
        # v12.18.0 — `btc_z_variant` controls the z-score formula:
        #   "baseline"        : ret_30d on 180d, mean+std (legacy)
        #   "robust"          : ret_30d on 180d, median+MAD
        #   "multi"           : 0.6 × (30d/180d, mean+std) + 0.4 × (7d/60d, mean+std)
        #   "robust_multi"    : 0.6 × (30d/180d, MAD) + 0.4 × (7d/60d, MAD)
        # v12.18.1 — new variants for whale-liquidation handling:
        #   "winsorize"       : cap per-candle returns at ±800 bps before
        #                       building the 30d return series — neutralizes
        #                       single-bar whale-liq spikes in the rolling
        #                       distribution without changing long-term sensitivity
        #   "adaptive_window" : when |z_30d| > 2.5, recompute with 14d lookback
        #                       for faster recovery — auto-heals from extreme regimes
        _use_robust = btc_z_variant in ("robust", "robust_multi")
        _use_multi = btc_z_variant in ("multi", "robust_multi")
        _use_winsorize = btc_z_variant == "winsorize"
        _use_adaptive = btc_z_variant == "adaptive_window"
        # Winsorize cap : 800 bps = 8% single-candle move
        _WINSOR_CAP = 0.08
        # Adaptive window threshold
        _ADAPTIVE_Z_THRESH = 2.5
        _ADAPTIVE_SHORT_LB_DAYS = 14
        # Short-horizon params for multi-variant
        _short_lb_days = 7
        _short_zw_days = 60
        _w_long = 0.6

        def _rolling_z(rets_hist: list, j: int, n_zw_local: int, robust: bool) -> float | None:
            # Aligned (divergence #10) : fenêtre de n_zw observations comme le
            # bot (le legacy en prenait n_zw+1 — off-by-one prouvé immatériel
            # par backtests/test_btc_z_parity.py, aligné ici).
            lo = (j - n_zw_local + 1) if aligned else (j - n_zw_local)
            past = rets_hist[max(0, lo):j + 1]
            if len(past) < 30:
                return None
            past_arr = np.array(past)
            if robust:
                center = float(np.median(past_arr))
                mad = float(np.median(np.abs(past_arr - center))) * 1.4826
                scale = mad or 1.0
            else:
                center = float(past_arr.mean())
                scale = float(past_arr.std()) or 1.0
            return (rets_hist[j] - center) / scale

        # v12.18.1 winsorize : build a sanitized close series where per-candle
        # returns are capped at ±_WINSOR_CAP, then compute ret_30d on that.
        if _use_winsorize:
            per_candle_rets = np.diff(btc_closes) / btc_closes[:-1]
            per_candle_rets = np.clip(per_candle_rets, -_WINSOR_CAP, _WINSOR_CAP)
            # Rebuild synthetic closes
            sanitized_closes = np.empty_like(btc_closes)
            sanitized_closes[0] = btc_closes[0]
            sanitized_closes[1:] = btc_closes[0] * np.cumprod(1.0 + per_candle_rets)
            closes_for_z = sanitized_closes
        else:
            closes_for_z = btc_closes

        # Long-horizon series
        rets_long: list[float] = []
        for i in range(n_lb, len(closes_for_z)):
            if closes_for_z[i - n_lb] > 0:
                rets_long.append(float(closes_for_z[i] / closes_for_z[i - n_lb] - 1))
            else:
                rets_long.append(0.0)

        # Short-horizon series (offset differs because n_lb differs)
        rets_short: list[float] = []
        n_lb_short = _short_lb_days * cpd
        n_zw_short = _short_zw_days * cpd
        if _use_multi:
            for i in range(n_lb_short, len(closes_for_z)):
                if closes_for_z[i - n_lb_short] > 0:
                    rets_short.append(float(closes_for_z[i] / closes_for_z[i - n_lb_short] - 1))
                else:
                    rets_short.append(0.0)

        # v12.18.1 adaptive_window : also build a 14d-lookback series to swap in
        rets_adaptive: list[float] = []
        n_lb_adapt = _ADAPTIVE_SHORT_LB_DAYS * cpd
        if _use_adaptive:
            for i in range(n_lb_adapt, len(closes_for_z)):
                if closes_for_z[i - n_lb_adapt] > 0:
                    rets_adaptive.append(float(closes_for_z[i] / closes_for_z[i - n_lb_adapt] - 1))
                else:
                    rets_adaptive.append(0.0)

        for j in range(len(rets_long)):
            ts_j = btc_candles[n_lb + j]["t"]
            z_long = _rolling_z(rets_long, j, n_zw, _use_robust)
            if z_long is None:
                continue
            # adaptive_window: if |z_long| extreme, swap to 14d lookback
            if _use_adaptive and abs(z_long) > _ADAPTIVE_Z_THRESH:
                j_adapt = (n_lb + j) - n_lb_adapt
                if 0 <= j_adapt < len(rets_adaptive):
                    z_adapt = _rolling_z(rets_adaptive, j_adapt, n_zw, _use_robust)
                    if z_adapt is not None:
                        btc_z_map[ts_j] = z_adapt
                        continue
                btc_z_map[ts_j] = z_long
                continue
            if not _use_multi:
                btc_z_map[ts_j] = z_long
                continue
            # Multi: blend long + short
            j_short = (n_lb + j) - n_lb_short
            if j_short < 0 or j_short >= len(rets_short):
                btc_z_map[ts_j] = z_long
                continue
            z_short = _rolling_z(rets_short, j_short, n_zw_short, _use_robust)
            if z_short is None:
                btc_z_map[ts_j] = z_long
            else:
                btc_z_map[ts_j] = _w_long * z_long + (1.0 - _w_long) * z_short

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
    # Counter for margin_check skips (margin saturation). Kept in a list so
    # the inner closure / loop can mutate it without `nonlocal`.
    n_margin_skip = [0]

    # mid_trade_profiling_eda: snapshot collector. Snapshots emitted at
    # specified hold-hour checkpoints (default 4/8/12/24h) for each position
    # still open at that checkpoint. Final outcome joined post-hoc by trade_id.
    # Disabled when mid_trade_dump_path is None — zero overhead in baseline.
    mid_trade_snapshots: list[dict] = []
    _next_trade_id = [0]  # mutable counter

    # Convert hour-based checkpoints to candle counts (held = candle index delta)
    mid_trade_checkpoints = tuple(int(h // interval_hours) for h in mid_trade_checkpoints_h)

    # Scale candle-count constants from the default 4h grid to whatever
    # `interval_hours` the caller is using. For 4h: factor 1 (no change).
    # For 1h: factor 4 (HOLD_HOURS_S5=48 → 48 candles instead of 12).
    _scale = 4 / interval_hours
    hold_candles = {k: int(v * _scale) for k, v in HOLD_CANDLES.items()}
    s9_early_exit_candles = int(S9_EARLY_EXIT_CANDLES * _scale)

    # Per-run Params: fold the runner_extension / early_exit_params hook
    # dicts into an alfred Params clone so the shared exit rules read them.
    # runner_extension=None disables the rule; early_exit_params=None
    # disables dead_timeout (lead = -inf never matches).
    if runner_extension is not None:
        _re_strats = runner_extension.get("strategies")
        _p_run = _dc.replace(
            _P,
            runner_ext_strategies=(frozenset(_re_strats) if _re_strats is not None
                                   else frozenset(STRAT_Z.keys())),
            runner_ext_hours=float(runner_extension.get("extra_candles", 6) * interval_hours),
            runner_ext_min_mfe_bps=float(runner_extension.get("min_mfe_bps", 500)),
            runner_ext_min_cur_to_mfe=float(runner_extension.get("min_cur_to_mfe", 0.5)),
        )
    elif not aligned:
        # legacy : runner_ext n'existe que via le hook R&D
        _p_run = _dc.replace(_P, runner_ext_strategies=frozenset())
    else:
        # aligné : les Params canoniques (live) s'appliquent tels quels —
        # bug de parité corrigé 2026-06-11 (l'audit d'ablation a montré des
        # contributions $0.00 : dead_timeout et runner_ext étaient strippés
        # du run officiel alors que le bot live les exécute).
        _p_run = _P
    if early_exit_params is not None:
        _p_run = _dc.replace(
            _p_run,
            dead_timeout_lead_hours=float(early_exit_params["exit_lead_candles"] * interval_hours),
            dead_timeout_mfe_cap_bps=float(early_exit_params["mfe_cap_bps"]),
            dead_timeout_mae_floor_bps=float(early_exit_params["mae_floor_bps"]),
            dead_timeout_slack_bps=float(early_exit_params["slack_bps"]),
        )
    elif not aligned:
        _p_run = _dc.replace(_p_run, dead_timeout_lead_hours=float("-inf"))
    # prop_trail R&D : merge un override de prop_trail_params (ex. ajouter S5).
    # Sinon défaut (S9 seul) → OFF pour tout le reste, zéro impact.
    if prop_trail_override is not None:
        _merged = dict(_p_run.prop_trail_params)
        _merged.update(prop_trail_override)
        _p_run = _dc.replace(_p_run, prop_trail_params=_merged)

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    # opposite_cut : signaux détectés au close de la bougie précédente sur
    # les tokens DÉTENUS (le flux candidats les skippe) — utilisés par les
    # exits de la bougie courante. Run officiel aligné : l'armement
    # opp_floor (v1.2.0) est piloté par Params ; le hook explicite reste
    # l'override R&D.
    if aligned and opposite_cut is None and _P.opp_floor_lock_ratio > 0:
        opposite_cut = {"mode": "floor",
                        "lock_ratio": _P.opp_floor_lock_ratio,
                        "min_gain_bps": _P.opp_floor_min_gain_bps,
                        "held_strats": None}
    _opp_sigs_prev: dict = {}

    for ts in sorted_ts:
        # basket_haircut_eda: snapshot pre-exit basket state for risk-side EDA.
        # Captures effective_n on 3 windows + unrealized basket P&L + capital
        # so we can later compute "did low eff_n precede a balance drawdown?"
        # Cheap: ≤6 positions × 3 corrcoef calls = few µs per ts.
        if True:
            effn_now = _compute_effective_n(positions, ts)
            basket_unreal = 0.0
            for _coin, _pos in positions.items():
                _ci_map = coin_by_ts.get(_coin)
                if _ci_map and ts in _ci_map:
                    _ci = _ci_map[ts]
                    _px = data[_coin][_ci]["c"]
                    if _px > 0 and _pos["entry"] > 0:
                        _bps = _pos["dir"] * (_px / _pos["entry"] - 1) * 1e4
                        basket_unreal += _pos["size"] * _bps / 1e4
            basket_timeseries.append({
                "ts": ts,
                "n_pos": len(positions),
                "eff_n_7d": effn_now[7],
                "eff_n_14d": effn_now[14],
                "eff_n_30d": effn_now[30],
                "basket_unreal": round(basket_unreal, 2),
                "capital": round(capital, 2),
            })

        # Market context for the shared exit rules. btc_z semantics mirror the
        # historical engine: empty btc_z_map → None (regime rules skip);
        # non-empty map with missing ts → 0.0 (neutral bucket).
        # Aligned (divergence #9) : missing ts → None (sémantique live —
        # règles régime sautées plutôt que bucket neutre).
        _mctx = _rules.MarketCtx(
            btc_z=(btc_z_map.get(ts) if aligned
                   else (btc_z_map.get(ts, 0.0) if btc_z_map else None)),
            btc_ret_4h_bps=btc_ret_4h_by_ts.get(ts),
            disp_24h=disp_by_ts.get(ts),
        )

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

            # Track MFE (best unrealized) and MAE (worst unrealized).
            # best_bps/worst_bps (intra-candle high/low) are kept for the
            # catastrophe-stop (resting order, realistic) and take_profit.
            best_bps, worst_bps = _rules.candle_excursions(
                pos["dir"], pos["entry"], candle["h"], candle["l"])
            # mfe_on_close: feed the MFE/MAE that the MARK-observed trailing
            # rules read (prop_trail, traj_cut, dead_timeout, s8_inlife, …) from
            # the close (mark proxy), NOT the wick high/low — removes the
            # backtest's intra-candle hindsight bias. Catastrophe-stop unaffected
            # (it reads worst_bps directly below).
            if mfe_on_close:
                _cur_mfe = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                # smooth_mfe_hours (config B′) : ne met à jour le MFE qu'aux
                # frontières N-h → les trailing-exits chevauchent les tendances
                # comme le BT 4h, sans sauter sur le bruit du mark horaire. Le MAE
                # reste à la granularité de la grille (cuts protègent vite). 0=off.
                _mfe_ok = (smooth_mfe_hours <= 0
                           or ts % (smooth_mfe_hours * 3600 * 1000) == 0)
                if _mfe_ok and _cur_mfe > pos.get("mfe", 0):
                    pos["mfe"] = _cur_mfe
                    pos["mfe_held"] = held
                if _cur_mfe < pos.get("mae", 0):
                    pos["mae"] = _cur_mfe
            else:
                if best_bps > pos.get("mfe", 0):
                    pos["mfe"] = best_bps
                    pos["mfe_held"] = held
                if worst_bps < pos.get("mae", 0):
                    pos["mae"] = worst_bps

            # mid_trade_profiling_eda: per-candle pain counter (count 4h candles
            # where close was below entry). Updated BEFORE checkpoint snapshot
            # so the value reflects the candle just closed.
            # s5_dead_t8h_walkforward: also enable when inlife_exit_extra is
            # active, so the hook can read time_in_pain_pct in its snapshot.
            if mid_trade_dump_path is not None or inlife_exit_extra is not None or trajectory_dump_path is not None:
                cur_bps_pain = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if cur_bps_pain < 0:
                    pos["pain_candles"] = pos.get("pain_candles", 0) + 1
            # Trajectory dump: record per-candle ur_bps + mfe + mae + btc_z
            if trajectory_dump_path is not None:
                pos["trajectory"].append({
                    "held": held,
                    "ur_bps": float(cur_bps_pain),
                    "mfe_bps": float(pos.get("mfe", 0.0)),
                    "mae_bps": float(pos.get("mae", 0.0)),
                    "btc_z": float(btc_z_map.get(ts, 0.0)) if btc_z_map else 0.0,
                })
            if mid_trade_dump_path is not None:
                # Snapshot when held matches one of the checkpoints exactly
                if held in mid_trade_checkpoints:
                    sf_now = sector_features.get((ts, coin))
                    sector_div_now = float(sf_now["divergence"]) if sf_now else float("nan")
                    sector_div_entry = pos.get("sector_div_at_entry", float("nan"))
                    pain_pct = (pos.get("pain_candles", 0) / held * 100.0) if held > 0 else 0.0
                    mid_trade_snapshots.append({
                        "trade_id": pos["trade_id"],
                        "symbol": coin,
                        "strat": pos["strat"],
                        "dir": pos["dir"],
                        "checkpoint_h": held * interval_hours,
                        "entry_t": pos["entry_t"],
                        "checkpoint_t": ts,
                        "current_ur_bps": float(cur_bps_pain),
                        "mfe_bps_to_date": float(pos.get("mfe", 0.0)),
                        "mae_bps_to_date": float(pos.get("mae", 0.0)),
                        "time_in_pain_pct": float(pain_pct),
                        "sector_div_at_entry": sector_div_entry,
                        "sector_div_now": sector_div_now,
                        "sector_div_delta": (sector_div_now - sector_div_entry
                                              if (sf_now and sector_div_entry == sector_div_entry)
                                              else float("nan")),
                    })

            # Optional partial profit-taking: when MFE crosses trigger and
            # not yet taken, exit `fraction` of the size at the current price.
            # Mirrors a live "scan & half-close" behavior. Fires once per
            # position. The remaining half continues with normal exit rules.
            if (partial_profit is not None
                    and pos["strat"] in partial_profit.get("strategies", set())
                    and not pos.get("partial_taken", False)
                    and pos.get("mfe", 0) >= partial_profit.get("trigger_bps", 1000)):
                fraction = partial_profit.get("fraction", 0.5)
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                partial_size = pos["size"] * fraction
                gross = cur_bps
                net = gross - COST
                partial_pnl = partial_size * net / 1e4
                if funding_data is not None:
                    partial_funding = compute_funding_cost(
                        funding_data, coin, pos["dir"],
                        pos["entry_t"], ts, partial_size)
                    partial_pnl -= partial_funding
                capital += partial_pnl
                peak_capital = max(peak_capital, capital)
                dd_p = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd_p)
                trades.append({
                    "pnl": partial_pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": "partial_profit", "size": partial_size,
                    "mfe_bps": pos.get("mfe", 0.0), "mae_bps": pos.get("mae", 0.0),
                    "conf_partial": pos.get("conf_partial"),
                    "session": pos.get("session"),
                    "entry_feats": pos.get("entry_feats"),
                    "effn_at_open_7d":  pos.get("effn_at_open_7d"),
                    "effn_at_open_14d": pos.get("effn_at_open_14d"),
                    "effn_at_open_30d": pos.get("effn_at_open_30d"),
                    "n_pos_at_open":    pos.get("n_pos_at_open"),
                    "trade_id":         pos.get("trade_id"),
                })
                pos["size"] = pos["size"] - partial_size
                pos["partial_taken"] = True

            # Shared-rules view of this position at this candle. cur_bps is
            # the close-based unrealized move all non-stop rules read.
            cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
            pv = _rules.PosView(
                strategy=pos["strat"], direction=pos["dir"],
                entry_price=pos["entry"], size_usdt=pos["size"],
                stop_bps=pos.get("stop", 0),
                mfe_bps=pos.get("mfe", 0.0), mae_bps=pos.get("mae", 0.0),
                hours_held=held * interval_hours,
                hours_to_timeout=(pos["hold"] - held) * interval_hours,
                mfe_at_h=pos.get("mfe_held", 0) * interval_hours,
                extended=pos.get("extended", False),
                opp_floor_bps=pos.get("opp_floor"),
            )

            # ── ALIGNED MODE (phase 6) ────────────────────────────────
            # One call to the canonical live exit chain (rules.evaluate_exit):
            # live rule order, synthetic trigger prices, prop_trail included,
            # stop-first via worst_bps. Replaces the whole legacy sequence
            # below — R&D hooks are NOT applied in aligned reference runs.
            # When aligned decides to HOLD, the sentinel keeps every legacy
            # rule (all guarded by `not exit_reason`) from firing; it is
            # cleared just before the close-booking block.
            _ALIGNED_HOLD = "__aligned_hold__"
            exit_reason = None
            exit_price = current
            # ── opposite_cut R&D : un signal de direction opposée est apparu
            # au close précédent sur ce token détenu, alors que la position
            # était gagnante → cut à l'OPEN de cette bougie (même timing
            # d'exécution qu'une entrée prise sur ce signal).
            if opposite_cut is not None:
                if opposite_cut.get("null_always"):
                    # NULL TEST : arme le plancher sans condition de signal
                    # (trail permanent) — si ça marche aussi, le signal
                    # opposé ne porte aucune information.
                    _ur_now = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    _o = ({"dirs": {-pos["dir"]}, "ur_bps": _ur_now}
                          if _ur_now >= opposite_cut["min_gain_bps"] else None)
                else:
                    _o = _opp_sigs_prev.get(coin)
                if (_o and (-pos["dir"]) in _o["dirs"]
                        and _o["ur_bps"] >= opposite_cut["min_gain_bps"]
                        and (opposite_cut.get("held_strats") is None
                             or pos["strat"] in opposite_cut["held_strats"])):
                    if opposite_cut.get("mode", "cut") == "floor":
                        # mode "floor" : le signal opposé ne coupe pas, il
                        # pose un plancher à lock_ratio × gain courant —
                        # rehaussé (cliquet) si le signal persiste plus haut.
                        _fl = opposite_cut["lock_ratio"] * _o["ur_bps"]
                        if _fl > pos.get("opp_floor", float("-inf")):
                            pos["opp_floor"] = _fl
                    else:
                        exit_reason = "opposite_cut"
                        exit_price = candle["o"] if candle["o"] > 0 else current
            # (déclenchement du plancher : canonique via rules.opp_floor_rule
            # dans evaluate_exit — une seule implémentation bot/BT.)
            # take_profit R&D : encaisse dès que la bougie touche +X bps
            # (prix synthétique du niveau, comme les stops côté défavorable).
            if take_profit is not None and not exit_reason:
                _tp = take_profit.get(pos["strat"], take_profit.get("ALL"))
                if _tp is not None and best_bps >= _tp:
                    exit_reason = "take_profit"
                    exit_price = pos["entry"] * (1 + pos["dir"] * _tp / 1e4)
            if aligned and not exit_reason:
                _dec = _rules.evaluate_exit(pv, cur_bps, _mctx, _p_run,
                                            worst_bps=worst_bps)
                if _dec and _dec.action == "extend":
                    pos["hold"] += int(_dec.extend_hours // interval_hours)
                    pos["extended"] = True
                    exit_reason = _ALIGNED_HOLD
                elif _dec:
                    exit_reason = _dec.reason
                    exit_price = (_dec.exit_price
                                  if _dec.exit_price is not None else current)
                else:
                    exit_reason = _ALIGNED_HOLD

            # Catastrophe stop (shared rule) — triggers on the candle's worst
            # excursion, books the synthetic stop price. `stop_override` lets
            # sweeps test alternate levels on one strategy.
            _stop_val = (stop_override.get(pos["strat"])
                         if stop_override else None)
            if not exit_reason:
                _dec = _rules.catastrophe_stop_rule(pv, worst_bps, _p_run,
                                                    stop_value=_stop_val)
                if _dec:
                    exit_reason = "stop"  # legacy label (canonical: catastrophe_stop)
                    exit_price = _dec.exit_price

            # Early-MAE exit (experimental): if MAE crosses an aggressive
            # threshold within the first N candles, exit immediately at that
            # threshold price. Mirrors a "fast crash detector" — different
            # hypothesis from WR auto-close (which uses historical WR estimate).
            if (early_mae_exit is not None and not exit_reason
                    and held <= early_mae_exit.get("max_candles", 2)
                    and pos["strat"] in early_mae_exit.get("strats", set())
                    and (early_mae_exit.get("dirs") is None
                         or pos["dir"] in early_mae_exit["dirs"])):
                thr = early_mae_exit["mae_threshold"]
                if pos.get("mae", 0) <= thr:
                    exit_reason = "early_mae_exit"
                    if pos["dir"] == 1:
                        exit_price = pos["entry"] * (1 + thr / 1e4)
                    else:
                        exit_price = pos["entry"] * (1 - thr / 1e4)

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
            # (aligned mode: runner ext + timeout already handled by
            # evaluate_exit — the sentinel/exit_reason guard skips this.)
            if not exit_reason:
                _dec = _rules.runner_ext_rule(pv, cur_bps, _p_run)
                if _dec:
                    pos["hold"] += int(_dec.extend_hours // interval_hours)
                    pos["extended"] = True
                    pv = _dc.replace(
                        pv, extended=True,
                        hours_to_timeout=(pos["hold"] - held) * interval_hours)

            if held >= pos["hold"]:
                exit_reason = exit_reason or "timeout"

            # S9 early exit (shared rule; legacy books the candle close)
            if not exit_reason:
                _dec = _rules.s9_early_rule(pv, cur_bps, _p_run, synthetic=False)
                if _dec:
                    exit_reason = _dec.reason

            # S10 trailing stop (shared rule; legacy books the candle close)
            if not exit_reason:
                _dec = _rules.s10_trail_rule(pv, cur_bps, _p_run, synthetic=False)
                if _dec:
                    exit_reason = _dec.reason

            # v12.6.0 — S8 dead-in-water (shared rule)
            if not exit_reason:
                _dec = _rules.s8_dead_rule(pv, _p_run)
                if _dec:
                    exit_reason = _dec.reason

            # EXIT-C R&D — early dead check generalized.
            # Mirror of s8_dead_in_water for other strats. early_dead_check
            # is a dict {strat: (T_check_h, mfe_cap_bps)}. Fires when held
            # reaches T_check AND mfe still below cap.
            if not exit_reason and early_dead_check is not None:
                edc = early_dead_check.get(pos["strat"])
                if edc is not None:
                    t_check_h, mfe_cap = edc
                    if (held * interval_hours >= t_check_h
                            and pos.get("mfe", 0) <= mfe_cap):
                        exit_reason = "early_dead_check"

            # v12.5.30 — S8 in-life MFE trail (shared rule; legacy books close)
            if not exit_reason:
                _dec = _rules.s8_inlife_rule(pv, cur_bps, _mctx, _p_run,
                                             synthetic=False)
                if _dec:
                    exit_reason = _dec.reason

            # Optional in-life exit (research hook — Families A/B/C from
            # docs/superpowers/specs/2026-05-14-inlife-exit-design.md).
            # Generic callable; sees a per-position snapshot and returns
            # (should_exit, reason). Inserted BEFORE dead_timeout so that
            # rollback rules can fire while MFE is still recent.
            if not exit_reason and inlife_exit_extra is not None:
                cur_bps_il = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                mfe_bps_il = pos.get("mfe", 0.0)
                mae_bps_il = pos.get("mae", 0.0)
                held_h_il = held * interval_hours
                mfe_peak_held = pos.get("mfe_held", held)
                time_since_mfe_h = (held - mfe_peak_held) * interval_hours
                # s5_dead_t8h_walkforward: enrich snap with checkpoint-style
                # features (time_in_pain_pct, sector_div_delta, trade_id) so
                # the hook can reproduce mid_trade_profiling_eda rules in-life.
                _pain_pct_il = (pos.get("pain_candles", 0) / held * 100.0
                                if held > 0 else 0.0)
                _sd_entry_il = pos.get("sector_div_at_entry", float("nan"))
                _sf_now_il = sector_features.get((ts, pos["coin"]))
                if _sf_now_il and _sd_entry_il == _sd_entry_il:
                    _sd_now_il = float(_sf_now_il["divergence"])
                    _sd_delta_il = _sd_now_il - _sd_entry_il
                else:
                    _sd_delta_il = float("nan")
                snap = {
                    "symbol": pos["coin"],
                    "strat":  pos["strat"],
                    "dir":    pos["dir"],
                    "hold_h": held_h_il,
                    "hold_max_h": pos["hold"] * interval_hours,
                    "mfe_bps": mfe_bps_il,
                    "mae_bps": mae_bps_il,
                    "cur_bps": cur_bps_il,
                    "time_since_mfe_h": time_since_mfe_h,
                    "btc_z":  btc_z_map.get(ts, 0.0) if btc_z_map else 0.0,
                    "ts_ms":  ts,
                    "trade_id": pos.get("trade_id"),
                    "time_in_pain_pct": float(_pain_pct_il),
                    "sector_div_delta": _sd_delta_il,
                }
                res = inlife_exit_extra(snap)
                if res and res[0]:
                    exit_reason = res[1] or "inlife_exit"

            # v12.7.1 — Trajectory cut (shared rule, regime-conditioned)
            if not exit_reason:
                _dec = _rules.traj_cut_rule(pv, cur_bps, _mctx, _p_run)
                if _dec:
                    exit_reason = _dec.reason

            # v12.15.0 — S9 early dead-in-water (shared rule)
            if not exit_reason:
                _dec = _rules.s9_early_dead_rule(pv, _p_run)
                if _dec:
                    exit_reason = _dec.reason

            # v12.15.0 — BTC drop cut (shared rule)
            if not exit_reason:
                _dec = _rules.btc_drop_cut_rule(pv, cur_bps, _mctx, _p_run)
                if _dec:
                    exit_reason = _dec.reason

            # v11.7.2 — Dead-timeout early exit (shared rule; hook params
            # folded into _p_run). Checked LAST, mirrors live bot order.
            if not exit_reason:
                _dec = _rules.dead_timeout_rule(pv, cur_bps, _p_run)
                if _dec:
                    exit_reason = _dec.reason
                    exit_price = current

            # Optional give-back exit: exit if MFE crossed `min_mfe_bps` AND
            # current is now ≤ `max_current_bps`. Asymmetric — only fires on
            # winners that turned negative, not on continuous trailing decay.
            if (not exit_reason and giveback is not None
                    and pos["strat"] in giveback.get("strategies", set())):
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if (pos.get("mfe", 0) >= giveback.get("min_mfe_bps", 100)
                        and cur_bps <= giveback.get("max_current_bps", 0)):
                    exit_reason = "giveback"

            # Optional extra-strategy trailing stop (sweep parameter)
            if (not exit_reason and trailing_extra is not None
                    and pos["strat"] == trailing_extra["strategy"]):
                mfe = pos.get("mfe", 0)
                if mfe >= trailing_extra["trigger_bps"]:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps <= mfe - trailing_extra["offset_bps"]:
                        exit_reason = f"{trailing_extra['strategy'].lower()}_trailing"

            # Optional proportional trailing stop: stop = arm + (mfe-arm) * lock_ratio
            # Two formats supported:
            #   1. Flat: {"strategy": "S9", "arm_bps": 800, "lock_ratio": 0.667}
            #   2. Regime-aware: {"strategy": "S9", "by_regime": {"bear": {"arm_bps": X,
            #         "lock_ratio": Y}, "neutral": {...}, "bull": None}, "z_threshold": 0.5}
            #      Regime is determined by btc_z at the current tick. `None` = disabled.
            if (not exit_reason and proportional_trail is not None
                    and pos["strat"] == proportional_trail["strategy"]):
                arm_lock = None
                if "by_regime" in proportional_trail:
                    z_th = proportional_trail.get("z_threshold", 0.5)
                    z = btc_z_map.get(ts, 0.0) if btc_z_map else 0.0
                    if z < -z_th:
                        regime_key = "bear"
                    elif z > z_th:
                        regime_key = "bull"
                    else:
                        regime_key = "neutral"
                    regime_cfg = proportional_trail["by_regime"].get(regime_key)
                    if regime_cfg is not None:
                        arm_lock = (regime_cfg["arm_bps"], regime_cfg["lock_ratio"])
                else:
                    arm_lock = (proportional_trail["arm_bps"], proportional_trail["lock_ratio"])
                if arm_lock is not None:
                    arm, lock = arm_lock
                    mfe = pos.get("mfe", 0)
                    if mfe >= arm:
                        stop_bps = arm + (mfe - arm) * lock
                        ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                        if ur_bps <= stop_bps:
                            exit_reason = f"{proportional_trail['strategy'].lower()}_prop_trail"

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

            # Aligned hold sentinel : evaluate_exit a décidé de tenir — la
            # position ne se ferme pas ce candle.
            if exit_reason == _ALIGNED_HOLD:
                exit_reason = None

            if exit_reason:
                # P&L via the shared core (v11.3.0 invariant: size = notional).
                # Real funding (v11.7.6): per-trade integral of hourly rates.
                funding_cost = (compute_funding_cost(
                    funding_data, coin, pos["dir"],
                    pos["entry_t"], ts, pos["size"])
                    if funding_data is not None else 0.0)
                gross, net, pnl = _rules.compute_trade_pnl(
                    pos["dir"], pos["entry"], exit_price, pos["size"],
                    COST, funding_usdt=funding_cost)
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                    "mfe_bps": pos.get("mfe", 0.0), "mae_bps": pos.get("mae", 0.0),
                    "mfe_held": pos.get("mfe_held", 0),
                    "conf_partial": pos.get("conf_partial"),
                    "session": pos.get("session"),
                    "entry_feats": pos.get("entry_feats"),
                    "effn_at_open_7d":  pos.get("effn_at_open_7d"),
                    "effn_at_open_14d": pos.get("effn_at_open_14d"),
                    "effn_at_open_30d": pos.get("effn_at_open_30d"),
                    "n_pos_at_open":    pos.get("n_pos_at_open"),
                    "trade_id":         pos.get("trade_id"),
                    "trajectory":       pos.get("trajectory"),
                })
                # Cooldown: per-strat override if cooldown_by_strat is set,
                # else global cooldown_hours. Set to 0 to disable.
                _cd_h = cooldown_by_strat.get(pos["strat"], cooldown_hours) \
                        if cooldown_by_strat is not None else cooldown_hours
                del positions[coin]
                if _cd_h > 0:
                    cooldown[coin] = ts + int(_cd_h * 3600 * 1000)

        # ── ENTRIES ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        # Entry-cadence gate (config B = live mirror): on a 1h grid, only open
        # positions on 4h boundaries (entries stay 4h, exits run hourly). 0 =
        # off (default) → parity with the 4h reference run.
        _entry_gate_open = (entry_align_hours <= 0
                            or ts % (entry_align_hours * 3600 * 1000) == 0)
        _btc_f = {"btc_30d": btc30, "btc_7d": btc7}
        for coin in coins:
            if not _entry_gate_open:
                break
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            # Shared detection (alfred.signals) — the same code the live bot
            # runs. Feature schema adapted (ret_6h → ret_24h); squeeze via the
            # shared indexed detector; S10 whitelist applied inside.
            sq = None
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq = _alf_signals.detect_squeeze_at(
                    data[coin], ci, f.get("vol_ratio", 2), _P,
                    candle_scale=int(_scale))
            sigs = _alf_signals.detect_token_signals(
                coin, _rules.adapt_bt_features(f), _btc_f,
                sector_features.get((ts, coin)), sq, "", {}, _P)
            for sig in sigs:
                cand = {
                    "coin": coin, "dir": sig["direction"],
                    "strat": sig["strategy"], "z": sig["z"],
                    "hold": int((sig["hold_hours"] // 4) * _scale),
                    # Legacy ranking quirk kept for iso-validation: the BT
                    # ranks S10 with flat strength 1000 (live ranks by
                    # squeeze tightness, divergence #8). Aligned mode uses
                    # the live force.
                    "strength": (sig["strength"] if (aligned or sig["strategy"] != "S10")
                                 else 1000),
                }
                if "stop_bps" in sig:
                    cand["stop"] = sig["stop_bps"]
                candidates.append(cand)

        # Optional extra candidates from a callback (used for new-signal sweeps).
        # Callback signature: fn(ts, coins, feat_by_ts, data, coin_by_ts, positions, cooldown) -> list[cand]
        if extra_candidate_fn is not None and _entry_gate_open:
            candidates.extend(extra_candidate_fn(ts, coins, feat_by_ts, data,
                                                  coin_by_ts, positions, cooldown))

        # opp_block_log R&D : recense les signaux RENTABLES bloqués par
        # already_in_position alors que la position détenue est de sens OPPOSÉ
        # (premise EDA du levier "autoriser LONG+SHORT même coin"). Détecte les
        # signaux sur les coins détenus et logge ceux de direction opposée, avec
        # de quoi reconstituer leur PnL (forward-walk dans le script EDA).
        # Actif uniquement quand opp_block_log est fourni → zéro impact runtime.
        if opp_block_log is not None:
            for _bcoin, _bpos in positions.items():
                _bf = feat_by_ts.get(ts, {}).get(_bcoin)
                _bci = coin_by_ts.get(_bcoin, {}).get(ts)
                if not _bf or _bci is None:
                    continue
                _bsq = _alf_signals.detect_squeeze_at(
                    data[_bcoin], _bci, _bf.get("vol_ratio", 2), _P,
                    candle_scale=int(_scale))
                _bsigs = _alf_signals.detect_token_signals(
                    _bcoin, _rules.adapt_bt_features(_bf), _btc_f,
                    sector_features.get((ts, _bcoin)), _bsq, "", {}, _P)
                for _bsig in _bsigs:
                    if _bsig["direction"] != _bpos["dir"]:
                        opp_block_log.append({
                            "ts": ts, "coin": _bcoin,
                            "dir": _bsig["direction"], "strat": _bsig["strategy"],
                            "z": _bsig["z"],
                            "hold_candles": int((_bsig["hold_hours"] // 4) * _scale),
                            "stop_bps": _bsig.get("stop_bps"),
                            "entry_idx": _bci + 1,
                            "held_dir": _bpos["dir"], "held_strat": _bpos["strat"],
                        })

        # opposite_cut R&D : signaux sur les tokens DÉTENUS (jamais calculés
        # par le flux candidats). Positions pré-entrées de ce ts uniquement —
        # une position qui s'ouvre à l'open suivant ne peut pas être cutée
        # par le signal qui l'a créée. Cooldown ignoré : le signal "apparaît"
        # dans le monde même si ce bot ne pourrait pas le trader.
        if opposite_cut is not None:
            _opp_sigs_prev = {}
            for _hcoin, _hpos in positions.items():
                _hf = feat_by_ts.get(ts, {}).get(_hcoin)
                _hci = coin_by_ts.get(_hcoin, {}).get(ts)
                if not _hf or _hci is None:
                    continue
                _hsq = _alf_signals.detect_squeeze_at(
                    data[_hcoin], _hci, _hf.get("vol_ratio", 2), _P,
                    candle_scale=int(_scale))
                _hsigs = _alf_signals.detect_token_signals(
                    _hcoin, _rules.adapt_bt_features(_hf), _btc_f,
                    sector_features.get((ts, _hcoin)), _hsq, "", {}, _P)
                if _hsigs:
                    _hpx = data[_hcoin][_hci]["c"]
                    _hur = (_hpos["dir"] * (_hpx / _hpos["entry"] - 1) * 1e4
                            if _hpx > 0 and _hpos["entry"] > 0 else 0.0)
                    _opp_sigs_prev[_hcoin] = {
                        "dirs": {s["direction"] for s in _hsigs},
                        "ur_bps": _hur}

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        _sector_counts: dict[str, int] = {}
        for _p_open in positions.values():
            _s_open = TOKEN_SECTOR.get(_p_open["coin"])
            if _s_open:
                _sector_counts[_s_open] = _sector_counts.get(_s_open, 0) + 1
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            if skip_fn is not None and skip_fn(coin, ts, cand["strat"], cand["dir"]):
                continue
            # Shared entry gates (alfred.rules) — blacklist, OI gate, disp
            # gate, position/direction/slot/sector caps. check_size_floor
            # stays off in legacy mode: the legacy BT enters sub-$10
            # post-modulator sizes the live exchange would reject
            # (divergence #7) — aligned mode enforces the live floor.
            _reason = _rules.entry_skip_reason(
                {"symbol": coin, "direction": cand["dir"],
                 "strategy": cand["strat"]},
                _rules.PortfolioCounters(
                    n_total=len(positions), n_longs=n_long, n_shorts=n_short,
                    n_macro=n_macro, n_token=n_token,
                    sector_counts=_sector_counts),
                _mctx, _P, capital, TOKEN_SECTOR,
                oi_delta_24h=(oi_delta_24h_pct(oi_data, coin, ts)
                              if oi_data is not None else None),
                check_size_floor=aligned)
            if _reason == "max_positions":
                if skip_log is not None:
                    skip_log.append((ts, cand["strat"], cand["dir"], "max_positions"))
                break
            if _reason:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector and block_opposite_sector:
                # Optional rule: block entries opposite to an existing
                # same-sector position
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
            # Hook R&D (2026-07-02, stress coûts par signal) : décalage
            # ADVERSE du prix d'entrée par stratégie (bps). Propage à tout
            # (P&L, MFE/MAE, distance au stop → stop-hit rate). None = no-op.
            if entry_slip_bps_by_strat:
                _esl = entry_slip_bps_by_strat.get(cand["strat"], 0.0)
                if _esl:
                    entry *= (1 + cand["dir"] * _esl / 1e4)

            if aligned:
                # ── ALIGNED (phase 6) : sizing canonique live ─────────
                # rules.position_size = base × modulateur, arrondi 2 déc.,
                # cap MAX_NOTIONAL post-modulateur, floor $10 (divergences
                # #5/#6/#7). Le btc_z vient de la map (fenêtre corrigée si
                # btc_z_variant le demande).
                _z = btc_z_map.get(ts) if (btc_z_map and apply_adaptive_modulator) else None
                if max_notional_fn is not None:
                    # R&D liquidity-aware cap : même ordre d'application que
                    # rules.position_size (cap EN DERNIER, post-arrondi) —
                    # on calcule sans cap puis on écrête via le hook.
                    size = _rules.position_size(
                        cand["strat"], cand["dir"], capital, _z,
                        _dc.replace(_P, max_notional_per_trade=0.0))
                    _cap = max_notional_fn(coin, ts, capital)
                    if 0 < _cap < size:
                        size = _cap
                else:
                    size = _rules.position_size(cand["strat"], cand["dir"],
                                                capital, _z, _P)
                # Levier C (R&D) : derisk global en bear profond. Réduit la
                # taille de TOUTES les entrées quand btc_z < seuil — overlay de
                # sizing, pas un filtre d'entrée (le trade est pris, plus petit).
                if bear_derisk is not None and _z is not None and _z < bear_derisk[0]:
                    size *= bear_derisk[1]
                if size < 10:
                    continue  # modulator_floor (live SKIP)
            else:
                size = strat_size(cand["strat"], capital)
            if size_multiplier is not None:
                size *= size_multiplier.get(cand["strat"], 1.0)
            # v11.7.28+ experimental: per-candidate size adjustment hook
            # Signature: size_fn(cand, feature_dict, n_positions) -> multiplier
            if size_fn is not None:
                size *= size_fn(cand, f, len(positions))
            elif not aligned and btc_z_map and apply_adaptive_modulator:
                # v11.10.0 + v12.2.0 adaptive modulator (shared rule). Legacy
                # quirk kept: the modulated size is NOT rounded here (live
                # rounds to 2 decimals) — see docs/alfred_divergences.md.
                _m = _rules.modulator_mult(cand["strat"], cand["dir"],
                                           btc_z_map.get(ts, 0.0), _P)
                if _m is not None:
                    size *= _m
            # basket_haircut_eda: multiplicative haircut from basket concentration.
            # Runs AFTER the adaptive modulator so it stacks on top, not in place
            # of it. Signature: basket_haircut_fn(cand, effn_dict, n_positions)
            # → multiplier. effn_dict = {7: v|None, 14: v|None, 30: v|None}.
            if basket_haircut_fn is not None:
                effn_basket = _compute_effective_n(positions, ts)
                size *= basket_haircut_fn(cand, effn_basket, len(positions))
            # v12.13.9 mirror: per-trade notional cap (matches trading.py:688)
            if max_notional_per_trade is not None and size > max_notional_per_trade:
                size = max_notional_per_trade
            # Margin saturation check: mirror HL "Insufficient margin" cascade
            # mode the live bot can hit when total open notional / LEVERAGE
            # approaches free balance. Off by default (legacy BT behavior).
            if margin_check:
                open_margin = sum(p["size"] / LEVERAGE for p in positions.values())
                new_margin = size / LEVERAGE
                margin_budget = capital * margin_max_util
                # R&D : réserver une fraction du budget de marge pour les
                # stratégies à forte espérance — un candidat low-z (S5/S10) ne
                # peut consommer que (1-frac) du budget, le reste reste libre
                # pour un futur S8/S9/S1. À $500 c'est la marge (pas le compteur
                # de slots) qui sature → on réserve la bonne ressource.
                if reserve_highz_frac and cand["z"] < reserve_z_threshold:
                    margin_budget *= (1.0 - reserve_highz_frac)
                if open_margin + new_margin > margin_budget:
                    n_margin_skip[0] += 1
                    if skip_log is not None:
                        skip_log.append((ts, cand["strat"], cand["dir"], "margin"))
                    continue
            # EDA hook (feature_modulator_eda): record entry features for
            # post-hoc analysis. Partial confluence = 4 of the 5 live components
            # (drops the OI component since the backtest has no live-grade
            # 1h-delta OI series). Mirrors analysis/bot/bot.py:258-266 exactly.
            entry_ts_ms = data[coin][idx_f + 1]["t"]
            conf_partial = int(sum([
                abs(f.get("drawdown", 0)) > 3000,
                f.get("vol_z", 0) > 1.5,
                abs(f.get("ret_6h", 0)) > 200,  # ret_6h on 4h candles = 24h
                n_stress_by_ts.get(ts, 0) >= 5,
            ]))
            _dt = datetime.utcfromtimestamp(entry_ts_ms // 1000)
            _h, _dow = _dt.hour, _dt.weekday()
            session = ("WE" if _dow >= 5 else
                       "Asia" if _h < 8 else
                       "EU"   if _h < 14 else
                       "US"   if _h < 21 else "Night")

            # EDA hook round 2: 10 additional continuous features at entry.
            # Mirrors analysis/bot/bot.py:245-253 for shock/clean/lead, plus
            # raw candle features and cross-sectional context. All derivable
            # from 4h candle data (no live-only inputs).
            _r24 = abs(f.get("ret_6h", 0))   # ret_6h on 4h = 24h return
            _dd  = abs(f.get("drawdown", 0))
            _entry_shock = (_r24 / _dd) if _dd > 100 else 0.0
            _rg = f.get("range_pct", 0)
            _entry_clean = (_rg / _r24) if _r24 > 50 else 0.0
            _sect_coin = TOKEN_SECTOR.get(coin)
            _peer_rets: list[float] = []
            if _sect_coin:
                for _p in SECTORS.get(_sect_coin, []):
                    if _p == coin:
                        continue
                    _pf = feat_by_ts.get(ts, {}).get(_p)
                    if _pf:
                        _peer_rets.append(abs(_pf.get("ret_42h", 0)))
            _peer_avg = float(np.mean(_peer_rets)) if _peer_rets else 0.0
            _entry_lead = (abs(f.get("ret_42h", 0)) / _peer_avg) if _peer_avg > 100 else 0.0
            # Cross-sectional context for this ts
            _ts_feats = feat_by_ts.get(ts, {})
            _all_r24 = [_pf.get("ret_6h", 0) for _pf in _ts_feats.values()]
            _all_r7d = [_pf.get("ret_42h", 0) for _pf in _ts_feats.values()]
            _disp_24h = float(np.std(_all_r24)) if _all_r24 else 0.0
            _disp_7d  = float(np.std(_all_r7d)) if _all_r7d else 0.0
            entry_feats = {
                "entry_shock":        float(_entry_shock),
                "entry_clean":        float(_entry_clean),
                "entry_lead":         float(_entry_lead),
                "entry_vol_z":        float(f.get("vol_z", 0)),
                "entry_range_pct":    float(_rg),
                "entry_disp_24h":     _disp_24h,
                "entry_disp_7d":      _disp_7d,
                "entry_n_stress":     int(n_stress_by_ts.get(ts, 0)),
                "entry_ret24h_abs":   float(_r24),
                "entry_drawdown_abs": float(_dd),
            }

            # basket_haircut_eda: effective_n of the existing basket BEFORE
            # adding this entry (3 windows). This is the value a haircut rule
            # would use to size the candidate.
            effn_at_open = _compute_effective_n(positions, ts)

            # mid_trade_profiling_eda: trade_id + entry-time sector divergence
            # for later joining with checkpoint snapshots and final outcome.
            _trade_id = _next_trade_id[0]
            _next_trade_id[0] += 1
            _sf_entry = sector_features.get((ts, coin))
            _sector_div_at_entry = float(_sf_entry["divergence"]) if _sf_entry else float("nan")

            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": entry_ts_ms,
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0, "mae": 0.0, "mfe_held": 0,
                "conf_partial": conf_partial, "session": session,
                "entry_feats": entry_feats,
                "effn_at_open_7d":  effn_at_open[7],
                "effn_at_open_14d": effn_at_open[14],
                "effn_at_open_30d": effn_at_open[30],
                "n_pos_at_open":    len(positions),  # excludes self
                "trade_id":         _trade_id,
                "sector_div_at_entry": _sector_div_at_entry,
                "pain_candles":     0,
                "trajectory":       [] if trajectory_dump_path is not None else None,
            }
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1
            if sym_sector:
                _sector_counts[sym_sector] = _sector_counts.get(sym_sector, 0) + 1

    # Close remaining positions at the last available candle (mark-to-market)
    for coin in list(positions.keys()):
        pos = positions[coin]
        last_ts = max(t for t in coin_by_ts[coin] if t <= end_ts_ms)
        last_idx = coin_by_ts[coin][last_ts]
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            funding_cost = (compute_funding_cost(
                funding_data, coin, pos["dir"],
                pos["entry_t"], last_ts, pos["size"])
                if funding_data is not None else 0.0)
            gross, net, pnl = _rules.compute_trade_pnl(
                pos["dir"], pos["entry"], exit_p, pos["size"],
                COST, funding_usdt=funding_cost)
            capital += pnl
            trades.append({
                "pnl": pnl, "net": net, "dir": pos["dir"],
                "strat": pos["strat"], "coin": coin,
                "entry_t": pos["entry_t"], "exit_t": last_ts,
                "reason": "mtm_final", "size": pos["size"],
                "mfe_bps": pos.get("mfe", 0.0), "mae_bps": pos.get("mae", 0.0),
                "conf_partial": pos.get("conf_partial"),
                "session": pos.get("session"),
                "entry_feats": pos.get("entry_feats"),
                "effn_at_open_7d":  pos.get("effn_at_open_7d"),
                "effn_at_open_14d": pos.get("effn_at_open_14d"),
                "effn_at_open_30d": pos.get("effn_at_open_30d"),
                "n_pos_at_open":    pos.get("n_pos_at_open"),
                "trade_id":         pos.get("trade_id"),
                "trajectory":       pos.get("trajectory"),
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

    # basket_haircut_eda: if dump path is set, write the per-ts basket time
    # series as JSONL so the risk-side EDA can replay equity curve + effective_n.
    if basket_dump_path and basket_timeseries:
        with open(basket_dump_path, "w") as _fh:
            for _row in basket_timeseries:
                _fh.write(json.dumps(_row) + "\n")

    # mid_trade_profiling_eda: join checkpoint snapshots with final outcomes
    # (joined by trade_id) and dump as JSONL. Each line is a single checkpoint
    # snapshot enriched with final_* fields.
    if mid_trade_dump_path and mid_trade_snapshots:
        trade_outcome = {}
        for t in trades:
            tid = t.get("trade_id")
            if tid is None:
                continue
            # net_bps from realized pnl: pnl / size * 1e4 gives the realized
            # net_bps per unit notional. Using "net" from trade is cleaner.
            trade_outcome[tid] = {
                "final_net_bps":     float(t["net"]),
                "final_pnl_usdt":    float(t["pnl"]),
                "final_winner":      bool(t["pnl"] > 0),
                "final_big_winner":  bool(t["net"] > 1500),
                "final_exit_reason": t["reason"],
                "final_mfe_bps":     float(t["mfe_bps"]),
                "final_mae_bps":     float(t["mae_bps"]),
            }
        with open(mid_trade_dump_path, "w") as _fh:
            for _snap in mid_trade_snapshots:
                tid = _snap["trade_id"]
                _out = trade_outcome.get(tid)
                if _out is None:
                    continue
                _snap.update(_out)
                _fh.write(json.dumps(_snap) + "\n")

    # Trajectory dump: write {trade_id: trajectory} JSON for offline analysis.
    # Only includes trades with a recorded trajectory (final closes, not partial).
    if trajectory_dump_path is not None:
        _trajectories = {
            str(t["trade_id"]): t.get("trajectory", [])
            for t in trades
            if t.get("trajectory") is not None
        }
        with open(trajectory_dump_path, "w") as _fh:
            json.dump(_trajectories, _fh)

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
        "basket_timeseries": basket_timeseries,
        "n_margin_skip": n_margin_skip[0],
    }


# ── Rolling runner & report writer ─────────────────────────────────────

# Bot reference dates — what would the strategy have produced from this
# date with current parameters? Direct comparison to live realized P&L.
# Update when a bot is reset/redeployed (date of first entry, OR date of
# soft/hard reset → it becomes the new fair-comparison baseline).
#
# Alfred (2026-06-10) : paper + live (SENIOR) démarrent à la migration Alfred le
# 2026-06-10 ; junior un jour après (2026-06-11). Les dates legacy pré-décommission
# sont caduques — aucun bot Alfred n'existait avant le 2026-06-10. Capitaux = equity
# au reset : paper $1000, live $680.58, junior $332.76 (cf. BOTS dans btlive_compare).
BOT_DEPLOYMENTS = [
    ("paper",  "2026-06-10"),
    ("live",   "2026-06-10"),
    ("junior", "2026-06-11"),
]


def rolling_windows(end_dt: datetime) -> list[tuple[str, datetime]]:
    """Return (label, start_dt) pairs for standard rolling windows + monthly
    starts + per-bot deployment-date anchors."""
    windows = [
        ("28 mois", end_dt - relativedelta(months=28)),
        ("12 mois", end_dt - relativedelta(months=12)),
        ("6 mois", end_dt - relativedelta(months=6)),
        ("3 mois", end_dt - relativedelta(months=3)),
        ("1 mois", end_dt - relativedelta(months=1)),
    ]
    # Monthly start points for the last 24 calendar months
    for i in range(24, 0, -1):
        month_start = (end_dt.replace(day=1) - relativedelta(months=i - 1))
        if month_start < end_dt:
            windows.append((f"depuis {month_start.strftime('%Y-%m-%d')}", month_start))
    # Per-bot deployment anchors (label kept "depuis YYYY-MM-DD" — the bot
    # identity is intentionally NOT included; backtests.md is exposed via
    # /api/changelog and stays anonymous).
    seen_dates: set[str] = {w[0].replace("depuis ", "") for w in windows
                             if w[0].startswith("depuis ")}
    for _, ds in BOT_DEPLOYMENTS:
        if ds in seen_dates:
            continue
        dt = datetime.fromisoformat(ds).replace(tzinfo=timezone.utc)
        if dt < end_dt:
            windows.append((f"depuis {ds}", dt))
            seen_dates.add(ds)
    return windows


def fmt_dollar(v: float) -> str:
    return f"${v:,.0f}".replace(",", " ")


def build_report(results: list[dict], end_dt: datetime, version: str,
                 capitals: list[float] | None = None,
                 aligned: bool = True) -> str:
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
        ("**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via "
         "`alfred/rules.py`, identique au bot live. Anciens chiffres : "
         "`docs/backtests_legacy_pre_phase6.md`."
         if aligned else
         "**Sémantique** : LEGACY (`BACKTEST_LEGACY_SEMANTICS=1`, archéologie "
         "uniquement — chiffres non comparables à la référence officielle)."),
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
        (
            f"**Notional cap** : ${BACKTEST_MAX_NOTIONAL:,.0f} par trade "
            "(override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). "
            "Modélise la profondeur d'orderbook HL : sans ce cap les ancres "
            "longues compoundent au-delà de la taille réellement exécutable."
            if BACKTEST_MAX_NOTIONAL > 0
            else "**Notional cap** : désactivé (BACKTEST_MAX_NOTIONAL=0)."
        ),
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
        "- Kill-switch (réactiver un token) : supprimer de `trade_blacklist` dans `alfred/settings.py`.",
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
        "- **Source** : candles 4h Hyperliquid, 34 tokens traded + BTC/ETH référence.",
        "- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector` "
        "(parité validée vs `alfred.features`, 800/800 tirages — `backtests/test_feature_parity.py`).",
        "- **Params & règles** : noyau ALFRED partagé bot/backtest — `alfred/settings.py` "
        "(`DEFAULT_PARAMS`) + `alfred/rules.py` (exits/sizing) + `alfred/signals.py`. "
        "Tout changement du bot est automatiquement reflété au prochain run.",
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
    # Convert 12h lead to candles (4h each) = 3 candles. Params from alfred.
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

    # BACKTEST_TRADE_DUMP env var: path to a JSON file where the full
    # trade-by-trade list of every window is dumped. Used for iso-result
    # validation when the engine internals change (Alfred phase 1). No
    # effect on the simulation itself.
    trade_dump_path = os.environ.get("BACKTEST_TRADE_DUMP", "")
    trade_dump: list[dict] = []

    # Phase 6 (actée 2026-06-10) : la sémantique ALIGNED (exécution live —
    # exits canoniques, prix synthétiques, prop_trail, sizing cap $500
    # post-modulateur, force S10 live, btc_z fenêtre bot) est désormais LA
    # référence officielle. Les chiffres legacy étaient inflatés ~34× sur
    # 28m (cap notionnel $20k pré-modulateur) — dossier complet dans
    # docs/alfred_phase6_preview.md. Anciens chiffres archivés dans
    # docs/backtests_legacy_pre_phase6.md.
    # Échappatoire d'archéologie : BACKTEST_LEGACY_SEMANTICS=1.
    aligned_run = os.environ.get("BACKTEST_LEGACY_SEMANTICS", "") != "1"
    print(f"Semantics: {'ALIGNED (live execution — phase 6)' if aligned_run else 'LEGACY (pre-phase 6)'}")

    results = []
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = latest_ts
        for cap in capitals:
            tag = f"  Running {label} (${cap:.0f}, {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')})..."
            print(tag)
            # En aligned, dead_timeout + runner_ext sont dans evaluate_exit —
            # les hooks legacy seraient redondants (et ignorés via sentinel).
            r = run_window(features, data, sector_features, dxy_data, start_ts, end_ts,
                           start_capital=cap,
                           oi_data=oi_data,
                           early_exit_params=None if aligned_run else early_exit_params,
                           runner_extension=None if aligned_run else runner_ext_cfg,
                           funding_data=funding_data,
                           apply_adaptive_modulator=True,
                           aligned=aligned_run,
                           margin_check=True,   # mime le plafond de marge HL (réaliste)
                           mfe_on_close=aligned_run)  # v1.4.0 : MFE sur le mark (close),
                           # pas les mèches high/low — le BT cessait de surévaluer les
                           # exits MFE (prop_trail/etc). Aligné sur le tracking du bot live.
            r["label"] = label
            r["start_date"] = start_dt.strftime("%Y-%m-%d")
            results.append(r)
            print(f"    → {r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%), "
                  f"{r['n_trades']} trades, DD {r['max_dd_pct']:.1f}%")
            if trade_dump_path:
                trade_dump.append({
                    "label": label,
                    "start_date": r["start_date"],
                    "capital": cap,
                    "end_capital": round(r["end_capital"], 6),
                    "max_dd_pct": round(r["max_dd_pct"], 6),
                    "trades": [
                        {
                            "coin": t["coin"], "strat": t["strat"],
                            "dir": t["dir"], "entry_t": t["entry_t"],
                            "exit_t": t["exit_t"], "reason": t["reason"],
                            "size": round(t["size"], 6),
                            "net": round(t["net"], 6),
                            "pnl": round(t["pnl"], 6),
                        }
                        for t in r["trades"]
                    ],
                })

    # Sort by (start_date asc, capital asc) so window groups stay consecutive
    results.sort(key=lambda x: (x["start_date"], x["start_capital"]))

    if trade_dump_path:
        with open(trade_dump_path, "w") as f:
            json.dump(trade_dump, f)
        print(f"Trade dump written to {trade_dump_path}")

    report = build_report(results, end_dt, VERSION, capitals=capitals,
                          aligned=aligned_run)
    os.makedirs(os.path.dirname(DOCS_PATH), exist_ok=True)
    with open(DOCS_PATH, "w") as f:
        f.write(report)
    print(f"\nReport written to {DOCS_PATH}")


if __name__ == "__main__":
    main()
