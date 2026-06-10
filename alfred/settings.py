"""Alfred parameters — every tuneable constant of the strategy core.

`Params` is a frozen dataclass whose defaults are the EXACT values of
analysis/bot/config.py at migration time (v12.17.3). One instance per bot:
the per-bot `overrides` block in bots.json is applied through
`Params.with_overrides`, and backtest sweeps use `dataclasses.replace`.

Pure data — no I/O, no env reads, no logging config. Runtime concerns
(.env, bots.json, paths) live in the runtime modules, not here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


def _d(**kw):
    """dict default helper for dataclass fields."""
    return field(default_factory=lambda: dict(kw))


@dataclass(frozen=True)
class Params:
    # ── Universe ─────────────────────────────────────────────────────
    trade_symbols: tuple[str, ...] = (
        "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
        "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
        "DOGE", "WLD", "BLUR", "LINK", "PYTH",
        "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
        "IMX", "SAND", "GALA", "MINA",
        "TON",
        "BCH", "DOT", "ADA", "XMR", "ENA", "UNI",
    )
    reference_symbols: tuple[str, ...] = ("BTC", "ETH")
    trade_blacklist: frozenset[str] = frozenset({"SUI", "IMX", "LINK"})
    sectors: dict = field(default_factory=lambda: {
        "L1":       ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI", "TON"],
        "L1-major": ["BCH", "DOT", "ADA"],
        "Privacy":  ["XMR"],
        "DeFi":     ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX",
                     "LDO", "GMX", "UNI", "ENA"],
        "Gaming":   ["GALA", "IMX", "SAND"],
        "Infra":    ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
        "Meme":     ["DOGE", "WLD", "BLUR", "MINA"],
    })

    # ── Hold periods (hours) ─────────────────────────────────────────
    hold_hours_default: float = 72.0          # S1
    hold_hours: dict = _d(S1=72.0, S5=48.0, S8=60.0, S9=48.0, S10=24.0)

    # ── Signal thresholds ────────────────────────────────────────────
    s1_btc_30d_min_bps: float = 2000.0        # BTC 30d > +20% fires S1
    s5_div_threshold: float = 1000.0
    s5_vol_z_min: float = 1.0
    s8_drawdown_thresh: float = -4000.0
    s8_vol_z_min: float = 1.0
    s8_ret_24h_thresh: float = -50.0
    s8_btc_7d_thresh: float = -300.0
    s9_ret_thresh: float = 2000.0
    s9_adaptive_stop: bool = True
    s10_squeeze_window: int = 3
    s10_vol_ratio_max: float = 0.9
    s10_breakout_pct: float = 0.5
    s10_reint_candles: int = 2
    s10_allow_longs: bool = False
    s10_allowed_tokens: frozenset[str] = frozenset({
        "AAVE", "APT", "ARB", "BLUR", "COMP", "CRV", "INJ",
        "MINA", "OP", "PYTH", "SEI", "SNX", "WLD",
    })

    # ── Leverage & sizing ────────────────────────────────────────────
    leverage: float = 2.0
    size_pct: float = 0.18
    size_bonus: float = 0.03
    max_notional_per_trade: float = 500.0     # 0 = disabled (v12.13.9)
    strat_z: dict = _d(S1=6.42, S5=3.67, S8=6.99, S9=8.71, S10=3.66)
    liquidity_haircut: dict = _d(S8=0.8)
    signal_mult: dict = _d(S1=1.125, S5=3.25, S8=1.25, S9=2.00, S10=2.00)
    min_fill_abort_usdt: float = 10.0

    # ── Adaptive macro modulator (v11.10.0 / v12.2.0) ───────────────
    adaptive_alpha: dict = _d(S1=+0.5, S8=-0.5, S9=-0.5)
    adaptive_alpha_dir: dict = field(default_factory=dict)  # {(strat, dir): α}
    macro_lookback_days: int = 30
    macro_z_window_days: int = 180
    macro_z_clip: float = 2.5
    macro_mult_min: float = 0.3
    macro_mult_max: float = 2.5

    # ── Position limits ──────────────────────────────────────────────
    max_positions: int = 6
    max_same_direction: int = 4
    max_per_sector: int = 2
    max_macro_slots: int = 3
    max_token_slots: int = 4
    macro_strategies: frozenset[str] = frozenset({"S1"})

    # ── Costs (round-trip, applied once at close) ────────────────────
    taker_fee_bps: float = 9.0
    slippage_bps: float = 0.0      # live: already in avgPx
    funding_drag_bps: float = 1.0  # flat estimate, swapped for real in live

    # ── Stops & exits ────────────────────────────────────────────────
    stop_loss_bps: float = -1250.0
    stop_loss_s8: float = -750.0
    s9_early_exit_bps: float = -500.0
    s9_early_exit_hours: float = 8.0
    s10_trailing_trigger: float = 600.0
    s10_trailing_offset: float = 150.0
    # v12.5.30 S8 in-life trail: regime → (activation_bps, offset_bps) | None
    s8_inlife_params: dict = _d(bear=(1500, 100), neutral=(300, 300), bull=(1500, 100))
    s8_inlife_z_threshold: float = 0.5
    # v12.11.0 proportional trail: strat → {regime: (arm_bps, lock_ratio) | None}
    prop_trail_params: dict = field(default_factory=lambda: {
        "S9": {"bear": None, "neutral": None, "bull": (100, 0.65)},
    })
    prop_trail_z_threshold: float = 0.5
    # v12.6.0 S8 dead-in-water
    s8_dead_t_h: float = 8.0
    s8_dead_mfe_max_bps: float = 50.0
    # v12.15.0 S9 early dead-in-water
    s9_early_dead_t_h: float = 12.0
    s9_early_dead_mfe_max_bps: float = 150.0
    # v12.15.0 BTC drop cut (LONG in loss + BTC 4h dump)
    btc_drop_cut_ret_4h_bps: float = -300.0
    btc_drop_cut_ur_max_bps: float = 0.0
    # v11.7.2 dead-timeout early exit
    dead_timeout_lead_hours: float = 12.0
    dead_timeout_mfe_cap_bps: float = 150.0
    dead_timeout_mae_floor_bps: float = -500.0
    dead_timeout_slack_bps: float = 300.0
    # v12.7.1 trajectory cut (regime-conditioned, S5)
    traj_cut_strategies: frozenset[str] = frozenset({"S5"})
    traj_cut_btc_z_threshold: float = -0.5
    traj_cut_decline_rate_min_bps_per_h: float = 100.0
    traj_cut_time_since_mfe_min_h: float = 4.0
    traj_cut_at_mae_slack_bps: float = 100.0
    traj_cut_min_loss_bps: float = -200.0
    # v11.7.32 runner extension (winners at timeout)
    runner_ext_strategies: frozenset[str] = frozenset({"S9"})
    runner_ext_hours: float = 12.0
    runner_ext_min_mfe_bps: float = 1200.0
    runner_ext_min_cur_to_mfe: float = 0.3

    # ── Entry gates ──────────────────────────────────────────────────
    oi_long_gate_bps: float = 1000.0
    oi_gate_min_history_hours: float = 23.0
    disp_gate_bps: float = 99999.0            # v12.8.0: retired (700 to re-enable)
    disp_gate_strategies: frozenset[str] = frozenset({"S5", "S9"})

    # ── Alerts (observation-only) ────────────────────────────────────
    giveback_alert_strategies: frozenset[str] = frozenset({"S5"})
    giveback_alert_mfe_min_bps: float = 500.0
    giveback_alert_cur_max_bps: float = -100.0
    giveback_alert_time_since_mfe_min_h: float = 4.0
    lock_floor_alert_strategies: frozenset[str] = frozenset({"S5", "S10", "S8", "S9", "S1"})
    lock_floor_alert_min_usd: float = 20.0
    lock_floor_alert_min_bps: float = 600.0
    lock_floor_alert_min_hold_h: float = 4.0
    lock_floor_alert_buffer_usd: float = 5.0
    regime_alert_disp_7d_bps: float = 700.0
    regime_alert_wr_pct: float = 35.0
    regime_alert_lookback: int = 10
    regime_alert_cooldown_h: float = 24.0
    regime_alert_strategy: str = "S5"
    regime_alert_direction: int = 1

    # ── Timing ───────────────────────────────────────────────────────
    cooldown_hours: float = 24.0

    # ── Paper engine (phase 7 corrections — shipped OFF) ─────────────
    paper_slippage_bps: float = 0.0           # 4.0 once phase 7 activates
    paper_funding_model: str = "flat"         # "flat" | "accrual"
    paper_gap_fills: bool = False             # book min(trigger, mark) on gaps

    # ── Derived helpers ──────────────────────────────────────────────

    @property
    def cost_bps(self) -> float:
        """Flat round-trip cost (taker + slippage + funding drag) = 10 bps."""
        return self.taker_fee_bps + self.slippage_bps + self.funding_drag_bps

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return self.trade_symbols + self.reference_symbols

    def token_sector(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for sect, toks in self.sectors.items():
            for t in toks:
                out[t] = sect
        return out

    def hold_hours_for(self, strategy: str) -> float:
        return self.hold_hours.get(strategy, self.hold_hours_default)

    def get_adaptive_alpha(self, strat: str, direction: int) -> float:
        """(strategy, direction) override wins over strategy-wide α; else 0."""
        if (strat, direction) in self.adaptive_alpha_dir:
            return self.adaptive_alpha_dir[(strat, direction)]
        return self.adaptive_alpha.get(strat, 0.0)

    def with_overrides(self, overrides: dict) -> "Params":
        """Apply a bots.json overrides block. Unknown keys are fatal."""
        valid = {f.name for f in dataclasses.fields(self)}
        unknown = set(overrides) - valid
        if unknown:
            raise ValueError(f"Unknown Params override(s): {sorted(unknown)}")
        return dataclasses.replace(self, **overrides)


DEFAULT_PARAMS = Params()
