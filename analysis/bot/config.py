"""Bot configuration — constants, environment, sizing logic.

All tuneable parameters live here. Values are from exhaustive backtests
(see backtest_*.py files and CLAUDE.md for references).
"""

from __future__ import annotations

import logging
import os

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BOT] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("multisignal")

VERSION = "12.15.0"

# ── Environment (.env) ──────────────────────────────────────────────
# bot/ -> analysis/ -> project root
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _v = _v.strip().strip("'\"")
                os.environ.setdefault(_k.strip(), _v)

EXECUTION_MODE = os.environ.get("HL_MODE", "paper")
# Display label in login/dashboard. Defaults derive from EXECUTION_MODE
# (PAPER/LIVE). Override via env for named instances (e.g. Junior).
BOT_LABEL = os.environ.get("BOT_LABEL", "")
BOT_LABEL_COLOR = os.environ.get("BOT_LABEL_COLOR", "")
# v12.7.7: public dashboard URL for this bot instance, appended to actionable
# Telegram alerts (GIVEBACK, LOCK_FLOOR) so the user can tap straight from
# the notification. Empty = no URL appended (back-compat). Set per instance
# in start_bots.sh (live=https://echonym.fr/bot, junior=https://echonym.fr/junior).
BOT_PUBLIC_URL = os.environ.get("BOT_PUBLIC_URL", "").rstrip("/")
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")
HL_ACCOUNT_ADDRESS = os.environ.get("HL_ACCOUNT_ADDRESS", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
# Telegram category allowlist. "*" = all (default). Comma-separated category names
# filter which messages reach Telegram. Categories: trade, daily, reconcile,
# security, admin, system. Junior uses "trade,daily" to avoid noise.
TG_CATEGORIES = os.environ.get("TG_CATEGORIES", "*")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")
# AUTH_SALT adds entropy to the HMAC secret. A leaked session cookie no longer
# allows offline password brute-force without also leaking this salt. Keep it
# long (>=32 chars) and distinct from the password. Missing = empty salt (no
# effect; kept for backward compat with existing cookies).
AUTH_SALT = os.environ.get("AUTH_SALT", "")

# ── Symbols ─────────────────────────────────────────────────────────
TRADE_SYMBOLS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
    "TON",  # v11.9.0: universe expansion (4/4 walk-forward 12m/6m/3m/1m, +944pp sum, ΔDD 0)
    # v12.7.0: 6 curated tokens, walk-forward 2/2 pass on 6m+12m, avg DD +1.73pp.
    # L1-major: BCH/DOT/ADA. Privacy: XMR. DeFi: ENA/UNI. Source: universe_expansion_results.md.
    "BCH", "DOT", "ADA", "XMR", "ENA", "UNI",
]
REFERENCE = ["BTC", "ETH"]
ALL_SYMBOLS = TRADE_SYMBOLS + REFERENCE

# Tokens blacklisted from trading based on autopsy of worst losers
# (backtest_worst_losers.py, backtest_loser_filters.py). These 3 were net-negative
# on EVERY walk-forward window (28m/12m/6m/3m). Validated on the official
# backtest_rolling engine:
#   28m: +$49 687 (+91%) | 12m: +$5 704 (+63%) | 6m: +$1 077 (+34%) | 3m: +$207 (+18%)
# DD penalty -10pp on 28m (bigger capital swings on higher peak); DD improves or
# unchanged on all recent windows.
# Kept in TRADE_SYMBOLS to continue data collection for potential re-activation
# (market dispersion features still use them). Blacklist is enforced at the
# entry decision in trading.rank_and_enter(), logged as SKIP reason=blacklist.
TRADE_BLACKLIST: set[str] = {"SUI", "IMX", "LINK"}

# v12.2.0 — replaces the static v12.1.0 S5 SHORT token blacklist with a
# regime-aware modulator. The static blacklist could not catch trades like
# SEI 2026-05-08 (catastrophe-stop −$28 in BULL regime, but SEI was a
# historical SHORT winner). The adaptive approach scales ALL S5 SHORT
# entries by `1 + α × btc_z` — same mechanism as v11.10.0 macro modulator
# on S1/S8/S9, applied per-direction here. See ADAPTIVE_ALPHA_DIR below.
# Walk-forward strict: +10100pp sum on 28m, ΔDD +1.52pp avg, 4/4 PnL —
# better PnL AND smaller DD than the static blacklist (which gave +6558pp
# sum but ΔDD +5.07pp). The drift monitor remains in place to flag any
# token-level pattern that warrants further intervention.

# ── Sectors (for S5 divergence) ─────────────────────────────────────
# v12.7.0: 6 new tokens added across 4 sectors (L1, L1-major, Privacy, DeFi).
# L1-major and Privacy are new sectors created to absorb BTC-correlated blue
# chips without saturating the existing L1 sector. Walk-forward 2/2 pass on
# 6m+12m, avg DD degradation +1.73pp (under +2pp gate). See
# backtests/universe_expansion_results.md.
SECTORS = {
    "L1":       ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI", "TON"],
    "L1-major": ["BCH", "DOT", "ADA"],
    "Privacy":  ["XMR"],
    "DeFi":     ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO", "GMX", "UNI", "ENA"],
    "Gaming":   ["GALA", "IMX", "SAND"],
    "Infra":    ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":     ["DOGE", "WLD", "BLUR", "MINA"],
}
TOKEN_SECTOR: dict[str, str] = {}
for _sect, _toks in SECTORS.items():
    for _t in _toks:
        TOKEN_SECTOR[_t] = _sect

# ── Hold Periods (backtest_boost.py) ────────────────────────────────
HOLD_HOURS_DEFAULT = 72   # S1 — 3 days
HOLD_HOURS_S5 = 48        # sector divergences revert faster
HOLD_HOURS_S8 = 60        # 15 candles
HOLD_HOURS_S9 = 48        # best test performance
HOLD_HOURS_S10 = 24       # 6 candles

# ── S5 Sector Divergence ────────────────────────────────────────────
S5_DIV_THRESHOLD = 1000   # 10% divergence from sector
S5_VOL_Z_MIN = 1.0

# ── S8 Capitulation Flush (backtest_deep_s8.py) ────────────────────
S8_DRAWDOWN_THRESH = -4000   # -40% from 30d high
S8_VOL_Z_MIN = 1.0
S8_RET_24H_THRESH = -50      # still bleeding (< -0.5%)
S8_BTC_7D_THRESH = -300      # BTC 7d < -3% (z 5.2→6.99)

# ── S9 Fade Extreme (backtest_wild.py) ──────────────────────────────
S9_RET_THRESH = 2000         # ±20% in 24h
S9_ADAPTIVE_STOP = True      # bigger moves → tighter stops (+54% S9 P&L)

# ── S10 Squeeze Expansion (FROZEN — backtest_squeeze.py) ───────────
S10_SQUEEZE_WINDOW = 3       # 3 candles = 12h
S10_VOL_RATIO_MAX = 0.9
S10_BREAKOUT_PCT = 0.5
S10_REINT_CANDLES = 2
S10_CAPITAL_SHARE = 0.0      # no pocket — full capital (backtest: +48% P&L vs 15%)

# S10 walk-forward filters (backtest_s10_walkforward.py).
# Train 16m (2023-10→2025-02), test 12m (2025-02→2026-02 OOS).
# Test-window P&L +123% vs baseline, test DD improves by 8.7pp.
# Note: 28m in-sample DD worsens by ~8.7pp (lost S10-LONG diversification).
# Kill-switch: set ALLOW_LONGS=True and ALLOWED_TOKENS=set(ALL_SYMBOLS).
# S10 trailing stop (backtest_exits.py walk-forward, passes 4/4 windows).
# When MFE exceeds trigger, exit if unrealized drops below MFE - offset.
# S10 gives back 70% of MFE on average; this locks in gains on big winners.
S10_TRAILING_TRIGGER = 600   # activate trailing after +600 bps MFE
S10_TRAILING_OFFSET = 150    # exit at MFE - 150 bps

# ── S8 in-life regime-conditioned trail (v12.5.30, walk-forward 4/4 + null-shuffle z=10.52)
# Per-regime (activation_bps, offset_bps). Activates a MFE trail on open S8
# positions: when MFE >= activation AND unrealized <= MFE-offset, exit.
# Bucketed on bot._btc_z (rolling 30d/180d z-score of BTC ret_30d, v11.10.0).
# Kill-switch: empty the dict ({}) → trail never fires (graceful lookup).
S8_INLIFE_PARAMS = {
    "bear":    (1500, 100),  # btc_z < -threshold
    "neutral": (300,  300),  # |btc_z| <= threshold
    "bull":    (1500, 100),  # btc_z > +threshold
}
S8_INLIFE_Z_THRESHOLD = 0.5

# ── Proportional MFE trail (v12.11.0) — regime-conditioned on S9 ──────
# Locks a fraction of every bps of MFE above an arm threshold:
#   stop = arm + (mfe - arm) * lock_ratio
# Tight near the arm, more permissive as MFE grows. Mirrors v12.5.30 S8 in-life
# pattern but uses proportional offset instead of fixed.
# Per (strategy, regime) → (arm_bps, lock_ratio) or None (= disabled).
# Walk-forward strict 4/4 on backtests/backtest_prop_trail_regime_walkforward.py
# (S9 bull-only, sum ΔPnL +$1385 across 4 splits, avg ΔDD +4.16pp = DD improved).
# Discovery: backtests/optimize_prop_trail.py (offline trajectory-based optim,
# 280-config grid per bucket × 18 buckets).
# Kill-switch: set PROP_TRAIL_PARAMS = {} → trail never fires.
PROP_TRAIL_PARAMS: dict[str, dict[str, tuple[int, float] | None]] = {
    "S9": {
        "bear":    None,
        "neutral": None,
        "bull":    (100, 0.65),
    },
}
PROP_TRAIL_Z_THRESHOLD = 0.5

# ── S8 dead-in-water exit (v12.6.0, walk-forward 3/3-with-cuts + 1/4 null)
# At T+8h after entry, if a S8 LONG has never crossed even +0.5% MFE, the
# capitulation thesis is invalidated: pressure absorbing every bid. Cut the
# trade rather than waiting 52 more hours for the inevitable. Opposite-tail
# pair with S8_INLIFE_PARAMS (which fires at MFE >= 300/1500 bps, never
# overlaps). Kill-switch: set S8_DEAD_MFE_MAX_BPS = -99999 (rule never fires).
S8_DEAD_T_H = 8.0           # checkpoint: hours_held >= 8.0
S8_DEAD_MFE_MAX_BPS = 50.0  # MFE-so-far ceiling; <= => cut

S10_ALLOW_LONGS = False      # LONG fades were 45% WR, -$4.8k on 28m
S10_ALLOWED_TOKENS = {       # tokens with positive S10 P&L on train window
    "AAVE", "APT", "ARB", "BLUR", "COMP", "CRV", "INJ",
    "MINA", "OP", "PYTH", "SEI", "SNX", "WLD",
}

# ── DXY (S4 suspended, kept for dashboard display) ────────────────
DXY_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "output", "pairs_data", "macro_DXY.json"
)
# ── Leverage & Sizing ──────────────────────────────────────────────
# 2x optimal (3x = ruin from compounding losses)
LEVERAGE = 2.0

SIZE_PCT = 0.18        # base sizing (was 0.12, backtest: +138% P&L, DD -81%)
SIZE_BONUS = 0.03
# v12.13.9: hard cap on per-trade notional. Without this, S9/S10 (high
# STRAT_Z + SIGNAL_MULT=2.0 + modulator) can hit $1000+ notional on $800
# equity → single position consumes most of the available margin and
# subsequent entries at the same 4h boundary cascade-fail with HL's
# "Insufficient margin". Set to 0 to disable (legacy behavior).
MAX_NOTIONAL_PER_TRADE = 500.0
STRAT_Z = {"S1": 6.42, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}
LIQUIDITY_HAIRCUT = {"S8": 0.8}  # S8 fires during thin/stressed markets
# Per-signal multipliers (backtest_sizing.py cross-period sweep 3m/12m/24m).
# v11.9.2: S5 2.50 → 3.25 from backtest_partial_fills.py walk-forward 4/4
# (28m +2681pp / 12m +239pp / 6m +17pp / 3m +11pp, ΔDD avg −4.4pp). The bump
# compensates for the partial-fill rate observed on real S5 entries (~30%
# under-fill at the 1% slippage cap on thin-liquidity tokens). At 100% fills
# it would over-size by 30% — but combined with the natural partial-fill
# attrition, effective notional matches the optimal target.
SIGNAL_MULT = {"S1": 1.125, "S5": 3.25, "S8": 1.25, "S9": 2.00, "S10": 2.00}

# v12.13.5: abort partial fills below this notional. HL exchange minimum order
# is $10, so a fill smaller than that is intradable (cannot even be re-trimmed)
# AND pollutes the dashboard with a "ghost" mini-position whose PnL ceiling is
# < $1 even on a 5% move. The bot already tolerates partial fills (SIGNAL_MULT
# v11.9.2 calibrated against the ~30% natural attrition rate), but extreme
# partial fills (<5% of requested) are NOT what the calibration covers — they
# indicate margin saturation or book exhaustion, not natural slippage. Setting
# this threshold to exchange minimum keeps the calibration semantics intact
# (typical 30-90% partials still flow through) while killing the pathological
# tail (GMX $0.51 / NEAR $1.85 cases observed 2026-06-02/03).
MIN_FILL_ABORT_USDT = 10.0

# ── Adaptive macro modulator (v11.10.0) ─────────────────────────────
# Each scan, the bot computes a rolling z-score of BTC 30d return and
# scales selected strategies' sizing by `1 + α × btc_z` (clipped). This
# reflects the empirical regime-dependence of each signal's edge:
#   - S1 (BTC momentum LONG alts) wins more in bull → α positive
#   - S8 (capitulation flush LONG) wins more in bear → α negative
#   - S9 (extreme ±20% fade) wins more in bear/choppy → α negative
#   - S5 LONG and S10 excluded — sliding walk-forward OOS shows their α is
#     regime-unstable (worked on some past OOS slices, failed on others).
#   - S5 SHORT (v12.2.0): regime-stable when isolated. In bull, S5 SHORT
#     shorts outperformers that keep pumping → losses (e.g. SEI 2026-05-08
#     catastrophe at btc_z=+1.4). Adaptive α=-0.5 reduces S5 SHORT in bull,
#     amplifies in bear (mean-reversion regime). Validated: walk-forward
#     4/4 strict (28m +10100pp, 12m +298, 6m +82, 3m +38, ΔDD +1.52pp avg).
# Validation: backtest_adaptive_robustness.py (IS/OOS split, lookback
# sensitivity 15-90d, null shuffle 13× signal-vs-noise, rolling z-score
# without look-ahead, per-window α stability) + backtest_adaptive_
# walkforward.py (sliding 18m train / 6m test × 4 splits). All passed
# 4/4 strict + OOS confirmed for S1/S8/S9. Conservative α=±0.5 chosen
# (vs ±1.0 optimum) to limit overfit risk.
ADAPTIVE_ALPHA = {"S1": +0.5, "S8": -0.5, "S9": -0.5}
# Direction-specific overrides (precedence over ADAPTIVE_ALPHA when present).
# v12.5.8: both losing-SHORT directions tightened from -0.5 → -1.5 after
# walk-forward 4/4 strict validation (backtest_short_kill.py). At -1.5 the
# multiplier hits the 0.3 floor at btc_z=0.47 — effectively zero-sized in any
# meaningful bull regime, where these SHORTs structurally bleed. S5 LONG and
# S9 LONG unchanged (S9 LONG still uses ADAPTIVE_ALPHA["S9"]=-0.5).
# v12.13.0 — retiré suite à re-validation walk-forward post candle-sync fixes
# (v12.12.2). Le modulator directionnel ne tient plus le strict 4/4 sur le BT
# corrigé (split_1 et split_3 dégradés, DD avg pire de 3pp). Voir
# memory/project_s5_short_modulator_revalidation_2026_06.md. Kill-switch :
# laisser vide; ADAPTIVE_ALPHA général (v11.10.0) reste actif via get_adaptive_alpha.
ADAPTIVE_ALPHA_DIR: dict[tuple[str, int], float] = {}
MACRO_LOOKBACK_DAYS = 30        # BTC return horizon (= ret_30d)
MACRO_Z_WINDOW_DAYS = 180       # rolling 6m for mean/std of past ret_30d
MACRO_Z_CLIP = 2.5              # clip btc_z within ±2.5 (extreme outliers)
MACRO_MULT_MIN = 0.3            # final multiplier floor (safety)
MACRO_MULT_MAX = 2.5            # final multiplier ceiling

# ── Capital & Position Limits ───────────────────────────────────────
CAPITAL_USDT = float(os.environ.get("HL_CAPITAL", "1000"))
# Hard cap on Junior's capital (DCA refused above this). Applies only when
# BOT_LABEL == "JUNIOR"; Live and Paper are unaffected. Set to 0 to disable.
JUNIOR_CAPITAL_CAP = 500.0
MAX_POSITIONS = 6
MAX_SAME_DIRECTION = 4
MAX_PER_SECTOR = 2
# Slot reservation (backtest_slot_reservation.py: DD -32% vs -44%)
# v12.6.3: 2 → 3 after walk-forward 4/4 strict sweep on {2,3,4,5} — slots=3
# beats baseline on all 4 windows simultaneously with avg ΔDD = 0.00pp. The
# extra macro slot captures S1 LONG signals that were previously skipped on
# `max_macro` (250 such SKIPs in the live 51d audit window). slots=4-5 fail
# 4/4 strict (over-fills correlated positions in bull rallies, 6m/3m regress).
# Source: backtests/max_macro_sweep_results.md.
MAX_MACRO_SLOTS = 3
MAX_TOKEN_SLOTS = 4       # was 3, +157% P&L with 4
MACRO_STRATEGIES = {"S1"}

# ── Costs (round-trip total, applied once at close) ─────────────────
# Values calibrated from 80 live fills on Hyperliquid (v11.3.4, 2026-04-10):
#   - Taker fee measured at 4.50 bps per leg = 9.00 bps round-trip (current
#     volume tier, will decrease at higher tiers).
#   - Slippage is 0 for live mode: the bot uses the exact avgPx from each
#     order response, so slippage is already baked into gross_bps. Backtests
#     need to model slippage separately since they use candle closes.
#   - Funding drag measured at ~0.5 bps on average across trades (positions
#     held 24-72h × typical funding < 0.3 bps/8h). We keep 1 bps for safety.
TAKER_FEE_BPS = 9.0
SLIPPAGE_BPS = 0.0        # already in avgPx for live; backtest adds its own
FUNDING_DRAG_BPS = 1.0
COST_BPS = TAKER_FEE_BPS + SLIPPAGE_BPS + FUNDING_DRAG_BPS  # 10

# ── Stop Losses ─────────────────────────────────────────────────────
STOP_LOSS_BPS = -1250.0    # -12.5% price move (was -2500 leveraged)
STOP_LOSS_S8 = -750.0      # -7.5% price move (was -1500 leveraged)
# Early exit: only S9 benefits in compounding (S5/S8 tested, both lose value)
S9_EARLY_EXIT_BPS = -500.0    # -5% price move after 8h (was -1000 leveraged)
S9_EARLY_EXIT_HOURS = 8.0

# v12.15.0 — S9 early dead-in-water (mirror s8_dead_in_water for S9).
# At T+12h, if S9 position MFE has never crossed +150 bps, the fade thesis
# is unlikely to materialize within the 48h hold → cut. Walk-forward strict 4/4
# via backtests/walkforward_exit_c_mfe_velocity.py V3 (variant {"S9": (12.0, 150.0)}).
# Fires meaningfully only in deep bear (split_1 +39pp); no-op on splits 2/3/4.
# Kill-switch: set S9_EARLY_DEAD_MFE_MAX_BPS = -99999 (rule never fires).
S9_EARLY_DEAD_T_H = 12.0
S9_EARLY_DEAD_MFE_MAX_BPS = 150.0

# v12.15.0 — BTC drop cut: when BTC dumps -3%+ over last 4h candle AND a LONG
# position is in unrealized loss, the alt-BTC correlation makes the position
# very likely to deepen. Cut preemptively. Walk-forward strict 4/4 via
# backtests/walkforward_exit_d_btc_drop.py V3 (btc_4h<-300 + dir=LONG + ur<=0).
# Aggregate ΔPnL +56.58pp / ΔDD -4.91pp on 4 splits.
# Kill-switch: set BTC_DROP_CUT_RET_4H_BPS = -99999 (rule never fires).
BTC_DROP_CUT_RET_4H_BPS = -300.0
BTC_DROP_CUT_UR_MAX_BPS = 0.0

# Dead-timeout early exit: at T-LEAD hours before timeout, if the trade has
# never shown meaningful upside (MFE <= MFE_CAP), is deeply underwater
# (MAE <= MAE_FLOOR) AND is still pinned near its low (current <= MAE + SLACK),
# crystallize the loss now rather than waiting to close at MAE at timeout.
# Walk-forward validated 4/4 (28m +$49k, 12m +$1.4k, 6m +$46, 3m +$21) with DD
# unchanged, via backtests/backtest_early_exit_d.py (variant D2).
# v11.7.16: MAE_FLOOR tightened from -1000 → -800 (catches PENDLE/DYDX-style
# pinned S5 losers ~200 bps sooner). Walk-forward: +$9.5k on 28m, -$200/$100/$50
# on 12m/6m/3m (noise), DD unchanged. S5 PnL alone: +$6k on 28m, tiny gains on
# shorter windows. Not strict 4/4 pass but asymmetric risk/reward.
# v12.5.0: MAE_FLOOR tightened again from -800 → -500. Validated by
# `backtests/backtest_wr_autoclose.py` walk-forward 4/4 strict (sum ΔPnL
# +3244pp on 28m, +484 on 12m, +43 on 6m, +21 on 3m, ΔDD avg -0.82pp = DD
# improved). Catches dead-trades ~300bps earlier — most impact on S5 LONG
# trades that pin between -500 and -800 MAE while never showing pulse.
DEAD_TIMEOUT_LEAD_HOURS = 12.0
DEAD_TIMEOUT_MFE_CAP_BPS = 150.0
DEAD_TIMEOUT_MAE_FLOOR_BPS = -500.0
DEAD_TIMEOUT_SLACK_BPS = 300.0

# ── Trajectory cut (v12.7.1) — regime-conditioned mid-trade exit ──────
# Codifies the user's manual_close intuition: cut a position whose
# trajectory is in steep decline from MFE, currently pinned near MAE,
# meaningfully losing — AND we are in a bear macro regime where these
# patterns historically materialize into catastrophe_stop rather than
# rebound.
#
# Rule: exit if ALL of these hold:
#   - strategy in TRAJ_CUT_STRATEGIES
#   - hours_held - mfe_at_h >= TRAJ_CUT_TIME_SINCE_MFE_MIN_H
#   - unrealized_bps <= TRAJ_CUT_MIN_LOSS_BPS
#   - unrealized_bps - mae_bps <= TRAJ_CUT_AT_MAE_SLACK_BPS  (near MAE)
#   - (mfe_bps - unrealized_bps) / max(t_since_mfe, 1) >= TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H
#   - bot._btc_z < TRAJ_CUT_BTC_Z_THRESHOLD  (bear regime)
#
# Discovery: live April-May 2026 — user's manual_close on 6 trades saved
# 3 catastrophe_stops (+$28 vs counterfactual). Hypothesis "automate the
# pattern" tested 2026-05-20. v1 unconditioned (backtest_trajectory_cut.py)
# failed walk-forward 1/4 PnL — cut too many recoverable positions on
# recent windows. v2 regime-conditioned (backtest_trajectory_cut_v2.py)
# PASSES strict 4/4 on R1 (z < -0.5):
#   28m: +177 043 pp / 12m: +1 440 / 6m: +20 / 3m: +16  (ΔDD avg +2.15pp = DD AMÉLIORÉE)
# 36 fires over 28m, all in bear regime. S5 only (where the live
# catastrophes hit).
# Kill-switch: set TRAJ_CUT_STRATEGIES = set() — rule then no-ops.
TRAJ_CUT_STRATEGIES: set[str] = {"S5"}
TRAJ_CUT_BTC_Z_THRESHOLD = -0.5      # bear-strict gate
TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H = 100.0
TRAJ_CUT_TIME_SINCE_MFE_MIN_H = 4.0
TRAJ_CUT_AT_MAE_SLACK_BPS = 100.0
TRAJ_CUT_MIN_LOSS_BPS = -200.0

# ── Giveback alert (v12.7.2) — Telegram-only, NO trading action ───────
# Notifies the user when an open position is showing the "giveback through
# middle" pattern (had real upside, now in the red, sustained — the NEAR /
# WLD / GALA / LDO pattern from April-May 2026 where manual_close saved
# 3 catastrophe_stops out of 5 cuts).
#
# Mechanical exit on this pattern fails walk-forward across 4 R&Ds
# (backtest_s5_trailing, backtest_giveback, backtest_early_mfe_exit,
# backtest_s5_trail_bear) — the runners that need to keep running are
# statistically identical to the rollers at the moment of decision.
# But the USER's pattern recognition (live April-May 2026: +$28 net on 6
# manual_close) outperforms the mechanical baseline.
#
# Strategy: bot detects + alerts; user decides + acts. Hybrid alpha.
#
# Trigger: ALL of
#   - strategy in GIVEBACK_ALERT_STRATEGIES
#   - pos.mfe_bps >= GIVEBACK_ALERT_MFE_MIN_BPS    (had real upside)
#   - unrealized_bps <= GIVEBACK_ALERT_CUR_MAX_BPS (now in the red)
#   - hours_held - pos.mfe_at_h >= GIVEBACK_ALERT_TIME_SINCE_MFE_MIN_H
#
# Dedup: once per position (cleared automatically on close). Same pattern
# as the WR_ALERT mechanism (bot.py:_check_wr_alerts). No DB event spam,
# Telegram only. Kill-switch: empty GIVEBACK_ALERT_STRATEGIES.
GIVEBACK_ALERT_STRATEGIES: set[str] = {"S5"}
GIVEBACK_ALERT_MFE_MIN_BPS = 500.0
GIVEBACK_ALERT_CUR_MAX_BPS = -100.0
GIVEBACK_ALERT_TIME_SINCE_MFE_MIN_H = 4.0

# ── Lock-floor alert (v12.7.2) — Telegram-only, NO trading action ─────
# Notifies the user when an open position has accumulated SUBSTANTIAL
# unrealized profit and might warrant a proactive manual_stop_usdt to
# lock most of it. Pure suggestion — user decides + acts via 🎯 button
# or /api/manual_stop endpoint.
#
# Rationale: S5/S10 winners can give back significant gains (the
# "giveback through middle" pattern). The user has no automatic trailing
# (4 R&Ds rejected). manual_stop_usdt is a flat floor that pre-empts the
# giveback without trailing's runner-amputation issue. The bot can't
# decide where to set it (would be a trailing); but it CAN flag when the
# decision becomes worth making.
#
# Trigger: ALL of
#   - strategy in LOCK_FLOOR_ALERT_STRATEGIES
#   - hours_held >= LOCK_FLOOR_ALERT_MIN_HOLD_H (settled, not entry blip)
#   - manual_stop_usdt not already set (no duplicate suggestion)
#   - EITHER  unrealized_pnl >= LOCK_FLOOR_ALERT_MIN_USD  (substantial $)
#       OR    unrealized_bps >= LOCK_FLOOR_ALERT_MIN_BPS  (substantial %)
#
# Suggested floor in the message = round(max(0, current_pnl - $5), 2)
# (i.e., lock break-even on small profits, or "current - $5 buffer" on
# bigger profits). User can pick differently.
#
# Dedup: once per position. Kill-switch: empty STRATEGIES set.
LOCK_FLOOR_ALERT_STRATEGIES: set[str] = {"S5", "S10", "S8", "S9", "S1"}
LOCK_FLOOR_ALERT_MIN_USD = 20.0
LOCK_FLOOR_ALERT_MIN_BPS = 600.0
LOCK_FLOOR_ALERT_MIN_HOLD_H = 4.0
LOCK_FLOOR_ALERT_BUFFER_USD = 5.0   # suggested floor = current_pnl - this

# ── OI Gate (backtest_external_gates.py, backtest_oi_gate_validate.py) ──
# Skip LONG entries when token OI has fallen >10% in 24h: longs are unwinding,
# flow is still bearish, entering LONG is catching a falling knife. Helps S8
# (capitulation LONG) and S5 LONG most. Validated walk-forward 4/4 windows
# (28m/12m/6m/3m) with zero DD penalty. Sweet spot plateau 1000-1100 bps.
OI_LONG_GATE_BPS = 1000.0   # -10% OI in 24h blocks LONG entries
OI_GATE_MIN_HISTORY_HOURS = 23  # require at least 23h of OI history to activate

# v11.7.28 dispersion gate — skip mean-reversion entries during regime breakdowns.
# Cross-sectional std(ret_24h) across all tracked alts proxies "alts flying in
# all directions" (regime fragmentation). Above ~p98 of historical distribution,
# S5 (sector divergence) and S9 (extreme-move fade) catch falling knives —
# the mean-reversion thesis fails when the cross-sectional distribution is
# itself broken. S8 (capitulation flush) and S10 (squeeze fade) keep firing
# because their setup is tied to single-token mechanics, not cross-sectional
# stability. Walk-forward validated 4/4 (28m/12m/6m/3m) with zero DD penalty
# in `backtests/backtest_dispersion_filter.py`. Fires ~1.4% of 4h candles in
# the last 12 months → ~6 entries skipped per year.
# v12.8.0: RETIRED. 2×2 matrix (`backtests/discovery_bias_2x2.py`) shows the
# gate is now Pareto-dominated by traj_cut v12.7.1: traj_cut alone beats
# (traj_cut + disp_gate) by +345 005pp on 28m and +11 340pp on 12m, with DD
# also slightly better. 6m/3m are no-ops (gate dormant in current regime;
# live + paper events DB show 0 SKIP disp_gate over 52 days). Kept as
# kill-switch (DISP_GATE_BPS = 99999.0); to re-enable set back to 700.0.
DISP_GATE_BPS = 99999.0
DISP_GATE_STRATEGIES: frozenset[str] = frozenset({"S5", "S9"})

# v12.7.14 regime alert (observation-only Telegram, no trading impact).
# Fires when cross-sectional 7d dispersion is elevated AND the recent
# WR on the targeted (strategy, direction) bucket is degraded. Driven
# by 2026-05 EDA showing S5 LONG losers concentrated above disp_7d=700
# in current regime. The hard-gate and soft-haircut variants both
# failed walk-forward — alert remains the only viable response.
# Kill-switch: set REGIME_ALERT_DISP_7D_BPS = 99999.
REGIME_ALERT_DISP_7D_BPS = 700.0
REGIME_ALERT_WR_PCT = 35.0
REGIME_ALERT_LOOKBACK = 10
REGIME_ALERT_COOLDOWN_H = 24
REGIME_ALERT_STRATEGY = "S5"
REGIME_ALERT_DIRECTION = 1  # 1=LONG, -1=SHORT

# v11.7.32 runner extension — when an S9 position reaches its natural timeout
# while still showing strong upside (high MFE retained), extend the hold by
# RUNNER_EXT_HOURS to capture the continuation of the mean-reversion. Mirrors
# the dead_timeout logic but for winners. Walk-forward 4/4 with DD intact (-0.9pp
# avg) on `backtests/backtest_runner_extension.py`. Fires on subset of S9
# winners (~1-2× per month). Kill-switch: empty RUNNER_EXT_STRATEGIES.
RUNNER_EXT_STRATEGIES: frozenset[str] = frozenset({"S9"})
RUNNER_EXT_HOURS = 12              # extra hold time when condition met
RUNNER_EXT_MIN_MFE_BPS = 1200.0    # MFE must have reached this peak
RUNNER_EXT_MIN_CUR_TO_MFE = 0.3    # current unrealized must be >= 30% of MFE

# ── Timing ──────────────────────────────────────────────────────────
SCAN_INTERVAL = 3600
COOLDOWN_HOURS = 24

# ── Paths ───────────────────────────────────────────────────────────
# bot/ -> analysis/ for base paths
_analysis_dir = os.path.dirname(os.path.dirname(__file__))
OUTPUT_DIR = os.environ.get("HL_OUTPUT_DIR", os.path.join(_analysis_dir, "output"))
STATE_FILE = os.path.join(OUTPUT_DIR, "reversal_state.json")
TICKS_DB = os.path.join(OUTPUT_DIR, "reversal_ticks.db")
HTML_PATH = os.path.join(_analysis_dir, "reversal.html")
CHANGELOG_PATH = os.path.join(os.path.dirname(_analysis_dir), "CHANGELOG.md")
BACKTESTS_PATH = os.path.join(os.path.dirname(_analysis_dir), "docs", "backtests.md")
WEB_PORT = int(os.environ.get("WEB_PORT", "8097"))


def strat_size(strat_name: str, capital: float) -> float:
    """Compute position size: base% * z-weight * haircut * signal_mult.

    z-weight (z/4 clamped to [0.5, 2.0]) allocates more capital to
    statistically stronger signals. SIGNAL_MULT fine-tunes per-signal
    allocation (optimized via backtest_sizing_optimal.py grid search).
    """
    z = STRAT_Z.get(strat_name, 3.0)
    weight = max(0.5, min(2.0, z / 4.0))
    pct = SIZE_PCT + (SIZE_BONUS if z > 4.0 else 0)
    base = capital * pct
    haircut = LIQUIDITY_HAIRCUT.get(strat_name, 1.0)
    mult = SIGNAL_MULT.get(strat_name, 1.0)
    return round(max(10, base * weight * haircut * mult), 2)


def get_adaptive_alpha(strat: str, direction: int) -> float:
    """Lookup adaptive macro modulator α for (strategy, direction).

    Direction-specific entry in ADAPTIVE_ALPHA_DIR takes precedence over
    direction-agnostic entry in ADAPTIVE_ALPHA. Returns 0.0 if neither
    matches (= no modulator applied).
    """
    if (strat, direction) in ADAPTIVE_ALPHA_DIR:
        return ADAPTIVE_ALPHA_DIR[(strat, direction)]
    return ADAPTIVE_ALPHA.get(strat, 0.0)
