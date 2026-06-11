"""Pure entry/exit/sizing rules shared by the live runtime and the backtests.

No I/O, no clocks, no bot object. Every function takes plain values plus a
`settings.Params`. The runtime (botinstance) and the backtest engine both
call these — killing the historical bot-vs-backtest re-implementations.

Each exit rule is a standalone function returning ExitDecision | None, so
the two callers can compose them in their own chain order (the backtest
interleaves research hooks between rules; the live bot uses the canonical
`evaluate_exit` composition). The rule LOGIC lives here exactly once.

Known execution-semantics divergences between the legacy backtest and the
live bot (chain order, close-vs-synthetic exit pricing, sizing cap order)
are preserved through explicit knobs (`synthetic` flags, sizing composition
left to the caller) and documented in docs/alfred_divergences.md. Phase-1
keeps the legacy semantics for iso-validation; alignment is a separate,
explicit step (phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .settings import Params


# ── Views passed to the pure functions ───────────────────────────────


@dataclass(frozen=True)
class MarketCtx:
    """What the rules read from the market at decision time."""
    price: float = 0.0                # markPx (live) / candle close (backtest)
    btc_z: float | None = None        # None = regime rules fail-open (skip)
    btc_ret_4h_bps: float | None = None
    disp_24h: float | None = None     # None = disp gate skips its check


@dataclass(frozen=True)
class PosView:
    """Granularity-agnostic snapshot of an open position."""
    strategy: str
    direction: int                    # 1=LONG, -1=SHORT
    entry_price: float
    size_usdt: float
    stop_bps: float                   # per-position stop (S9 adaptive); 0 = default
    mfe_bps: float
    mae_bps: float
    hours_held: float
    hours_to_timeout: float           # (target_exit - now) in hours; <= 0 = expired
    mfe_at_h: float                   # hours_held when MFE was last updated
    extended: bool = False
    manual_stop_usdt: float | None = None
    opp_floor_bps: float | None = None   # plancher armé par signal opposé (v1.2.0)


@dataclass
class PortfolioCounters:
    n_total: int = 0
    n_longs: int = 0
    n_shorts: int = 0
    n_macro: int = 0
    n_token: int = 0
    sector_counts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
    action: str                       # "exit" | "extend"
    reason: str
    exit_price: float | None          # synthetic trigger price; None = current mark
    extend_hours: float = 0.0


# ── Excursion tracking ───────────────────────────────────────────────


def update_excursions(pos, unrealized_bps: float, hours_held: float) -> None:
    """Tick-based MAE/MFE update (live path). Mutates pos in place.

    pos needs attributes mae_bps, mfe_bps, mfe_at_h (models.Position fits).
    """
    if unrealized_bps < pos.mae_bps:
        pos.mae_bps = unrealized_bps
    if unrealized_bps > pos.mfe_bps:
        pos.mfe_bps = unrealized_bps
        pos.mfe_at_h = hours_held


def candle_excursions(direction: int, entry_price: float,
                      high: float, low: float) -> tuple[float, float]:
    """(best_bps, worst_bps) reached within one candle (backtest path)."""
    if direction == 1:
        best = (high / entry_price - 1) * 1e4
        worst = (low / entry_price - 1) * 1e4
    else:
        best = -(low / entry_price - 1) * 1e4
        worst = -(high / entry_price - 1) * 1e4
    return best, worst


# ── Individual exit rules ────────────────────────────────────────────


def effective_stop(pos: PosView, p: Params) -> float:
    """Catastrophe-stop level: S8 tighter, S9 adaptive (stored), else default."""
    if pos.strategy == "S8":
        return p.stop_loss_s8
    if pos.stop_bps != 0:
        return pos.stop_bps
    return p.stop_loss_bps


def _z_bucket(z: float, threshold: float) -> str:
    if z < -threshold:
        return "bear"
    if z > threshold:
        return "bull"
    return "neutral"


def _synth(pos: PosView, level_bps: float) -> float:
    """Synthetic exit price for a bps trigger level."""
    return pos.entry_price * (1 + pos.direction * level_bps / 1e4)


def runner_ext_rule(pos: PosView, ur: float, p: Params) -> ExitDecision | None:
    """v11.7.32 — at natural timeout, a winner still near its MFE peak gets
    one extra hold extension. Pre-empts the timeout exit."""
    if (pos.hours_to_timeout <= 0 and not pos.extended
            and pos.strategy in p.runner_ext_strategies
            and pos.mfe_bps >= p.runner_ext_min_mfe_bps
            and pos.mfe_bps > 0
            and ur / pos.mfe_bps >= p.runner_ext_min_cur_to_mfe):
        return ExitDecision("extend", "runner_ext", None, p.runner_ext_hours)
    return None


def catastrophe_stop_rule(pos: PosView, trigger_bps: float, p: Params,
                          stop_value: float | None = None) -> ExitDecision | None:
    """Hard stop. `trigger_bps` is the worst excursion since the last decision
    point (candle low/high in BT, current mark in live). Books the synthetic
    stop price. `stop_value` overrides the level (BT stop_override hook)."""
    stop = stop_value if stop_value is not None else effective_stop(pos, p)
    if trigger_bps < stop:
        return ExitDecision("exit", "catastrophe_stop", _synth(pos, stop))
    return None


def opp_floor_rule(pos: PosView, trigger_bps: float, p: Params) -> ExitDecision | None:
    """v1.2.0 — plancher armé par signal opposé : quand un signal de direction
    contraire est détecté sur un token détenu GAGNANT (armement au scan 4h,
    côté bot/BT), un plancher à `opp_floor_lock_ratio` × gain courant est posé
    (cliquet : jamais abaissé). Ici on ne fait que le déclencher — stop-first
    sur le worst de la période, prix synthétique du niveau.

    Validation 2026-06-11 : walk-forward strict 4/4 (+167/+389/+268/+137 $ à
    0.70 ; +681/+326/+392/+257 $ à 0.80), ΔDD +8pp, test nul destructeur
    (le même plancher SANS condition de signal détruit tout le P&L → c'est
    le signal qui porte l'information). Source : backtest_opposite_cut.py.
    """
    if pos.opp_floor_bps is None:
        return None
    if trigger_bps <= pos.opp_floor_bps:
        return ExitDecision("exit", "opp_floor", _synth(pos, pos.opp_floor_bps))
    return None


def opp_floor_level(ur_bps: float, p: Params) -> float | None:
    """Niveau de plancher à armer quand un signal opposé apparaît sur une
    position gagnante — None si l'armement ne s'applique pas (kill-switch :
    `opp_floor_lock_ratio` ≤ 0, ou gain insuffisant)."""
    if p.opp_floor_lock_ratio <= 0 or ur_bps < p.opp_floor_min_gain_bps:
        return None
    return p.opp_floor_lock_ratio * ur_bps


def manual_stop_rule(pos: PosView, ur: float, p: Params) -> ExitDecision | None:
    """v12.5.10/v12.5.29 — user-set dollar floor on NET pnl (live only)."""
    if pos.manual_stop_usdt is None or pos.size_usdt <= 0:
        return None
    if pos.size_usdt * (ur - p.cost_bps) / 1e4 <= pos.manual_stop_usdt:
        target_gross = pos.manual_stop_usdt / pos.size_usdt * 1e4 + p.cost_bps
        return ExitDecision("exit", "manual_stop_set", _synth(pos, target_gross))
    return None


def s9_early_rule(pos: PosView, ur: float, p: Params,
                  *, synthetic: bool = True) -> ExitDecision | None:
    """S9 early exit: not reverting after 8h and below -500 bps."""
    if (pos.strategy == "S9" and pos.hours_held >= p.s9_early_exit_hours
            and ur < p.s9_early_exit_bps):
        px = _synth(pos, p.s9_early_exit_bps) if synthetic else None
        return ExitDecision("exit", "s9_early_exit", px)
    return None


def s10_trail_rule(pos: PosView, ur: float, p: Params,
                   *, synthetic: bool = True) -> ExitDecision | None:
    """v11.4.0 — S10 trailing stop: lock gains once MFE crossed the trigger."""
    if pos.strategy == "S10" and pos.mfe_bps >= p.s10_trailing_trigger:
        trail = pos.mfe_bps - p.s10_trailing_offset
        if ur <= trail:
            px = _synth(pos, trail) if synthetic else None
            return ExitDecision("exit", "s10_trailing", px)
    return None


def s8_dead_rule(pos: PosView, p: Params) -> ExitDecision | None:
    """v12.6.0 — S8 LONG with no pulse by T+8h: thesis invalidated."""
    if (pos.strategy == "S8" and pos.direction == 1
            and pos.hours_held >= p.s8_dead_t_h
            and pos.mfe_bps <= p.s8_dead_mfe_max_bps):
        return ExitDecision("exit", "s8_dead_in_water", None)
    return None


def s8_inlife_rule(pos: PosView, ur: float, m: MarketCtx, p: Params,
                   *, synthetic: bool = True) -> ExitDecision | None:
    """v12.5.30 — S8 regime-conditioned MFE trail. Skips when btc_z is None."""
    if pos.strategy != "S8" or m.btc_z is None or not p.s8_inlife_params:
        return None
    cfg = p.s8_inlife_params.get(_z_bucket(m.btc_z, p.s8_inlife_z_threshold))
    if cfg is None:
        return None
    act, off = cfg
    if pos.mfe_bps >= act:
        trail = pos.mfe_bps - off
        if ur <= trail:
            px = _synth(pos, trail) if synthetic else None
            return ExitDecision("exit", "s8_inlife", px)
    return None


def prop_trail_rule(pos: PosView, ur: float, m: MarketCtx, p: Params) -> ExitDecision | None:
    """v12.11.0 — proportional trail: stop = arm + (mfe - arm) × lock_ratio."""
    if pos.strategy not in p.prop_trail_params or m.btc_z is None:
        return None
    cfg = p.prop_trail_params[pos.strategy].get(
        _z_bucket(m.btc_z, p.prop_trail_z_threshold))
    if cfg is None:
        return None
    arm_bps, lock_ratio = cfg
    if pos.mfe_bps >= arm_bps:
        stop_bps = arm_bps + (pos.mfe_bps - arm_bps) * lock_ratio
        if ur <= stop_bps:
            return ExitDecision("exit", "prop_trail", _synth(pos, stop_bps))
    return None


def traj_cut_rule(pos: PosView, ur: float, m: MarketCtx, p: Params) -> ExitDecision | None:
    """v12.7.1 — bear-regime trajectory cut (steep MFE→cur decline, pinned at MAE)."""
    if (pos.strategy in p.traj_cut_strategies
            and m.btc_z is not None
            and m.btc_z < p.traj_cut_btc_z_threshold
            and ur <= p.traj_cut_min_loss_bps
            and (ur - pos.mae_bps) <= p.traj_cut_at_mae_slack_bps):
        t_since_mfe = pos.hours_held - pos.mfe_at_h
        if t_since_mfe >= p.traj_cut_time_since_mfe_min_h:
            decline_rate = (pos.mfe_bps - ur) / max(t_since_mfe, 1.0)
            if decline_rate >= p.traj_cut_decline_rate_min_bps_per_h:
                return ExitDecision("exit", "traj_cut", None)
    return None


def s9_early_dead_rule(pos: PosView, p: Params) -> ExitDecision | None:
    """v12.15.0 — S9 with MFE never above the cap by T+12h: cut."""
    if (pos.strategy == "S9"
            and pos.hours_held >= p.s9_early_dead_t_h
            and pos.mfe_bps <= p.s9_early_dead_mfe_max_bps):
        return ExitDecision("exit", "s9_early_dead", None)
    return None


def btc_drop_cut_rule(pos: PosView, ur: float, m: MarketCtx, p: Params) -> ExitDecision | None:
    """v12.15.0 — LONG in loss while BTC dumps over its last 4h candle: cut."""
    if (pos.direction == 1
            and ur <= p.btc_drop_cut_ur_max_bps
            and m.btc_ret_4h_bps is not None
            and m.btc_ret_4h_bps <= p.btc_drop_cut_ret_4h_bps):
        return ExitDecision("exit", "btc_drop_cut", None)
    return None


def dead_timeout_rule(pos: PosView, ur: float, p: Params) -> ExitDecision | None:
    """v11.7.2 — near timeout, never showed upside, pinned at MAE: crystallize."""
    if (pos.hours_to_timeout <= p.dead_timeout_lead_hours
            and pos.mfe_bps <= p.dead_timeout_mfe_cap_bps
            and pos.mae_bps <= p.dead_timeout_mae_floor_bps
            and ur <= pos.mae_bps + p.dead_timeout_slack_bps):
        return ExitDecision("exit", "dead_timeout", None)
    return None


# ── Canonical (live) exit chain ──────────────────────────────────────


def evaluate_exit(pos: PosView, unrealized_bps: float, m: MarketCtx, p: Params,
                  *, worst_bps: float | None = None) -> ExitDecision | None:
    """Full canonical exit chain for one open position at one decision point.

    Mirrors analysis/bot/trading.py:check_exits order (v12.17.3), with one
    granularity-aware refinement: the catastrophe stop is evaluated on
    `worst_bps` when provided (candle low/high) so an intra-period stop
    touch beats the timeout tick — at 20s live granularity worst==current
    and the distinction vanishes.

    Returns None (hold), ExitDecision("extend", ...) for the runner
    extension (caller pushes target_exit and sets pos.extended), or
    ExitDecision("exit", ...).
    """
    ur = unrealized_bps
    d = runner_ext_rule(pos, ur, p)
    if d:
        return d
    d = catastrophe_stop_rule(pos, worst_bps if worst_bps is not None else ur, p)
    if d:
        return d
    d = opp_floor_rule(pos, worst_bps if worst_bps is not None else ur, p)
    if d:
        return d
    if pos.hours_to_timeout <= 0:
        return ExitDecision("exit", "timeout", None)
    d = manual_stop_rule(pos, ur, p)
    if d:
        return d
    d = s9_early_rule(pos, ur, p)
    if d:
        return d
    d = s10_trail_rule(pos, ur, p)
    if d:
        return d
    d = s8_dead_rule(pos, p)
    if d:
        return d
    d = s8_inlife_rule(pos, ur, m, p)
    if d:
        return d
    d = prop_trail_rule(pos, ur, m, p)
    if d:
        return d
    d = traj_cut_rule(pos, ur, m, p)
    if d:
        return d
    d = s9_early_dead_rule(pos, p)
    if d:
        return d
    d = btc_drop_cut_rule(pos, ur, m, p)
    if d:
        return d
    d = dead_timeout_rule(pos, ur, p)
    if d:
        return d
    return None


# ── Entry gates ──────────────────────────────────────────────────────


def entry_skip_reason(sig: dict, c: PortfolioCounters, m: MarketCtx, p: Params,
                      capital: float, token_sector: dict, *,
                      in_position: bool = False,
                      in_cooldown: bool = False,
                      paused: bool = False,
                      oi_delta_24h: float | None = None,
                      check_size_floor: bool = True) -> str | None:
    """Why `sig` would be skipped, or None ("would enter").

    Mirrors trading.signal_skip_reason / rank_and_enter check order. The
    gates are conjunctive so the outcome is order-independent; the order
    only fixes WHICH reason is reported.

    `oi_delta_24h`: pass features.oi_delta_24h_bps(...) (None = fail-open,
    matching the <23h-history behavior).
    `check_size_floor=False` reproduces the legacy backtest (which enters
    sub-$10 post-modulator sizes the live exchange would reject).
    """
    direction = sig["direction"]
    strategy = sig["strategy"]

    if in_position:
        return "already in position"
    if in_cooldown:
        return "cooldown"
    if (strategy in p.disp_gate_strategies and m.disp_24h is not None
            and m.disp_24h >= p.disp_gate_bps):
        return "disp_gate"
    if c.n_total >= p.max_positions:
        return "max_positions"
    if sig["symbol"] in p.trade_blacklist:
        return "blacklist"
    if paused:
        return "paused_strategy"
    if direction == 1 and c.n_longs >= p.max_same_direction:
        return "max_long"
    if direction == -1 and c.n_shorts >= p.max_same_direction:
        return "max_short"
    if strategy in p.macro_strategies and c.n_macro >= p.max_macro_slots:
        return "max_macro"
    if strategy not in p.macro_strategies and c.n_token >= p.max_token_slots:
        return "max_token"
    sym_sector = token_sector.get(sig["symbol"])
    if sym_sector and c.sector_counts.get(sym_sector, 0) >= p.max_per_sector:
        return "max_sector"
    if (direction == 1 and oi_delta_24h is not None
            and oi_delta_24h < -p.oi_long_gate_bps):
        return "oi_gate"
    if check_size_floor:
        if position_size(strategy, direction, capital, m.btc_z, p) < 10:
            return "modulator_floor"
    return None


# ── Sizing ───────────────────────────────────────────────────────────


def base_size(strategy: str, capital: float, p: Params,
              *, cap: float | None = None) -> float:
    """base% × z-weight × haircut × signal_mult, floored at $10.

    `cap` reproduces the legacy backtest's pre-modulator notional cap
    (BACKTEST_MAX_NOTIONAL); the live path caps after the modulator instead.
    """
    z = p.strat_z.get(strategy, 3.0)
    weight = max(0.5, min(2.0, z / 4.0))
    pct = p.size_pct + (p.size_bonus if z > 4.0 else 0)
    raw = max(10, capital * pct * weight
              * p.liquidity_haircut.get(strategy, 1.0)
              * p.signal_mult.get(strategy, 1.0))
    if cap is not None and cap > 0:
        raw = min(raw, cap)
    return round(raw, 2)


def modulator_mult(strategy: str, direction: int, btc_z: float | None,
                   p: Params) -> float | None:
    """Adaptive macro multiplier 1 + α × clip(btc_z), bounded. None = no-op."""
    alpha = p.get_adaptive_alpha(strategy, direction)
    if alpha == 0 or btc_z is None:
        return None
    z_clip = max(-p.macro_z_clip, min(p.macro_z_clip, btc_z))
    return max(p.macro_mult_min, min(p.macro_mult_max, 1.0 + alpha * z_clip))


def position_size(strategy: str, direction: int, capital: float,
                  btc_z: float | None, p: Params) -> float:
    """Canonical (live) sizing: base → modulator (rounded) → notional cap."""
    size = base_size(strategy, capital, p)
    mult = modulator_mult(strategy, direction, btc_z, p)
    if mult is not None:
        size = round(size * mult, 2)
    if 0 < p.max_notional_per_trade < size:
        size = p.max_notional_per_trade
    return size


# ── P&L ──────────────────────────────────────────────────────────────


def compute_trade_pnl(direction: int, entry_price: float, exit_price: float,
                      size_usdt: float, cost_bps: float,
                      funding_usdt: float = 0.0) -> tuple[float, float, float]:
    """(gross_bps, net_bps, pnl_usdt). size_usdt is the NOTIONAL — no extra
    leverage multiplier (v11.3.0 invariant). `funding_usdt` is the funding
    COST paid over the trade (positive = paid, subtracted from pnl)."""
    gross = direction * (exit_price / entry_price - 1) * 1e4
    net = gross - cost_bps
    pnl = size_usdt * net / 1e4 - funding_usdt
    return gross, net, pnl


# ── Backtest feature-schema adapter ──────────────────────────────────


def adapt_bt_features(f_bt: dict) -> dict:
    """Translate the vectorized backtest feature schema to the canonical bot
    schema consumed by signals.detect_token_signals.

    The backtest names returns by candle count on the 4h grid ("ret_6h" =
    6 candles = 24 hours); the bot names them by wall time. ret_42h is the
    same (42 candles = 7d) on both sides.
    """
    f = dict(f_bt)
    f["ret_24h"] = f_bt.get("ret_6h", 0)
    return f
