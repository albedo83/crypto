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

VERSION = "11.4.9"

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
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")
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
]
REFERENCE = ["BTC", "ETH"]
ALL_SYMBOLS = TRADE_SYMBOLS + REFERENCE

# ── Sectors (for S5 divergence) ─────────────────────────────────────
SECTORS = {
    "L1":     ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":   ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO", "GMX"],
    "Gaming": ["GALA", "IMX", "SAND"],
    "Infra":  ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":   ["DOGE", "WLD", "BLUR", "MINA"],
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

S10_ALLOW_LONGS = False      # LONG fades were 45% WR, -$4.8k on 28m
S10_ALLOWED_TOKENS = {       # tokens with positive S10 P&L on train window
    "AAVE", "APT", "ARB", "BLUR", "COMP", "CRV", "INJ",
    "MINA", "OP", "PYTH", "SEI", "SNX", "WLD",
}

# ── DXY (S4 suspended, kept for dashboard display) ────────────────
DXY_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "output", "pairs_data", "macro_DXY.json"
)
DXY_BOOST_THRESHOLD = 100   # DXY 7d > +1% (unused while S4 suspended)

# ── Leverage & Sizing ──────────────────────────────────────────────
# 2x optimal (3x = ruin from compounding losses)
LEVERAGE = 2.0

SIZE_PCT = 0.18        # base sizing (was 0.12, backtest: +138% P&L, DD -81%)
SIZE_BONUS = 0.03
STRAT_Z = {"S1": 6.42, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}
LIQUIDITY_HAIRCUT = {"S8": 0.8}  # S8 fires during thin/stressed markets
# Per-signal multipliers (backtest_sizing.py cross-period sweep 3m/12m/24m)
# S5 2.50 and S9 2.00 stable across all periods; S10 2.00 conservative consensus
SIGNAL_MULT = {"S1": 1.125, "S5": 2.50, "S8": 1.25, "S9": 2.00, "S10": 2.00}

# ── Capital & Position Limits ───────────────────────────────────────
CAPITAL_USDT = float(os.environ.get("HL_CAPITAL", "1000"))
MAX_POSITIONS = 6
MAX_SAME_DIRECTION = 4
MAX_PER_SECTOR = 2
# Slot reservation (backtest_slot_reservation.py: DD -32% vs -44%)
MAX_MACRO_SLOTS = 2
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

# ── Portfolio Protections ──────────────────────────────────────────
# Kill-switch, loss streak, and quarantine DISABLED after backtest analysis:
# all three destroy compounding returns (-65% to -99% P&L impact).
# Per-trade stops + S9 early exit + position limits are sufficient.
TOTAL_LOSS_CAP = -999_999.0     # effectively disabled
LOSS_STREAK_THRESHOLD = 999     # effectively disabled
LOSS_STREAK_MULTIPLIER = 1.0    # no reduction
LOSS_STREAK_COOLDOWN = 0

# ── OI Gate (backtest_external_gates.py, backtest_oi_gate_validate.py) ──
# Skip LONG entries when token OI has fallen >10% in 24h: longs are unwinding,
# flow is still bearish, entering LONG is catching a falling knife. Helps S8
# (capitulation LONG) and S5 LONG most. Validated walk-forward 4/4 windows
# (28m/12m/6m/3m) with zero DD penalty. Sweet spot plateau 1000-1100 bps.
OI_LONG_GATE_BPS = 1000.0   # -10% OI in 24h blocks LONG entries
OI_GATE_MIN_HISTORY_HOURS = 23  # require at least 23h of OI history to activate

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
