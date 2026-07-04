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
    # Phase 6 (2026-06-10) : MKR retiré — Hyperliquid ne sert plus aucune
    # candle depuis le 2025-09-05 (rebranding MakerDAO→SKY) ; aucun signal
    # possible depuis 9 mois (fail-safe silencieux). Impact chiffré dans
    # docs/alfred_phase6_preview.md (-93pp sur 28m, no-op 6m/3m).
    trade_symbols: tuple[str, ...] = (
        "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
        "AAVE", "COMP", "SNX", "PENDLE", "DYDX",
        "DOGE", "WLD", "BLUR", "LINK", "PYTH",
        "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
        "IMX", "SAND", "GALA", "MINA",
        "TON",
        "BCH", "DOT", "ADA", "XMR", "ENA", "UNI",
    )
    reference_symbols: tuple[str, ...] = ("BTC", "ETH")
    trade_blacklist: frozenset[str] = frozenset()  # vidée 2026-06-30 : overfit de sélection décru (walk-forward glissant : retrait gagne 6/7 OOS). Re-blacklister = ré-ajouter des tokens ici.
    sectors: dict = field(default_factory=lambda: {
        "L1":       ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI", "TON"],
        "L1-major": ["BCH", "DOT", "ADA"],
        "Privacy":  ["XMR"],
        "DeFi":     ["AAVE", "CRV", "SNX", "PENDLE", "COMP", "DYDX",
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
    # Filet hard-stop exchange-side (v1.7.1) : trigger reduce-only résident
    # sur HL à effective_stop − buffer. Couvre les downtimes du process ; la
    # chaîne 20s reste l'exécuteur primaire (buffer 200 = p99.99 des
    # excursions 60s [194] et > max overshoot soft observé [162]). PAS une
    # règle : rules.py/backtest inchangés. Kill-switch : enabled=False.
    # Trails sur close 4h (v1.8.0, chantier 2026-07-04) : les règles TRAIL
    # (opp_floor, s10_trail, s8_inlife, prop_trail) ne sont évaluées qu'à la
    # première tick suivant chaque clôture 4h, sur un MFE échantillonné à ces
    # clôtures — la sémantique EXACTE de leur validation walk-forward (le BT
    # évalue 1×/bougie 4h sur closes). Le tick 20s bruité gonflait le pic
    # (+36-45 bps médian mesuré) et déclenchait les croisements trop tôt →
    # gagnants réels +236/306 bps vs +562 BT à WR égal. Validation : A′ 7/7
    # (cadence 4h > 1h en BT, DD meilleur), contrefactuel réel n=98 Δ+98 bps
    # IC95[+4,+217]. Coupe-pertes/stops/filet JAMAIS gatés (restent au tick).
    # Kill-switch : False = comportement tick historique.
    trail_eval_4h_close: bool = True
    # Staleness gate (v1.8.2) : aucune décision de sortie SOFT si le dernier
    # tick du symbole date de plus de N secondes — une sortie sur prix figé
    # (GAP_REPAIR, WS mort) est une décision au mauvais prix. Le filet
    # exchange-side couvre la catastrophe pendant le trou. 0 = désactivé.
    exit_stale_max_s: float = 180.0

    hard_stop_enabled: bool = False        # armé par bot via overrides
    hard_stop_buffer_bps: float = 200.0
    # ⚠️ 0.20 N'EST PAS une faute de frappe — ne pas « nettoyer » à 0.05.
    # C'est la borne de PERMISSION du stop-market IoC (pas une cible) :
    # l'IoC fille toujours aux meilleurs prix du book, la borne ne fait
    # qu'autoriser de filler à travers un trou. À 0.05, un gap atomique
    # au-delà de −18.8 % du prix d'entrée annulait l'IoC → position nue
    # pendant un downtime jusqu'à la liquidation (−40/−47 % pleine charge,
    # mm=1/(2×maxLev)). À 0.20 le fill est permis jusqu'à
    # 1−(1−0.145)×0.80 ≈ −31.6 % : couvre la tranche −18.8→−31.6 qui
    # rendait le filet inutile pile dans son cas d'usage. 0.30 couvrirait
    # le pire book (−40.1 vs liq −40.2) mais jamais le book standard
    # (−47.5) — 0.20 = genou de la courbe. Analyse : 2026-07-02.
    hard_stop_slippage: float = 0.20       # borne limit du stop-market
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
        # S5 : verrou proportionnel tous régimes (arm 200, lock 0.65). Capture le
        # MFE que les gagnants S5 rendaient (WR 47→75, book ×2.6 en BT 28m, DD
        # amélioré sur tous les tests). Validé aligned strict 4/4 (6/9 plateau) +
        # OOS DD 4/4 / PnL 3/4. Kill-switch : retirer cette entrée.
        "S5": {"bear": (200, 0.65), "neutral": (200, 0.65), "bull": (200, 0.65)},
    })
    prop_trail_z_threshold: float = 0.5
    # v12.6.0 S8 dead-in-water
    s8_dead_t_h: float = 8.0
    s8_dead_mfe_max_bps: float = 50.0
    # v12.15.0 S9 early dead-in-water
    s9_early_dead_t_h: float = 12.0
    s9_early_dead_mfe_max_bps: float = 150.0
    # v12.15.0 BTC drop cut (LONG in loss + BTC 4h dump)
    # v12.15.0 EXIT-D — RETIRÉE v1.9.0 (décision utilisateur 2026-07-04,
    # rapport exit_chain_ablation_report.md) : contribution −178/−290/−825/
    # +209 $ sur 28m/12m/6m/3m, DD DÉGRADÉ 2 fenêtres, 13/26 coupes
    # récupéraient au timeout sans elle (+69 $ direct/28m), 31 % redondante
    # avec catastrophe_stop, 14 tirs live −59 $. La défense « assurance
    # anti-krach » est réfutée : 10 bougies-krach sur la fenêtre où elle
    # perd le plus. Réactiver = remettre -300.0.
    btc_drop_cut_ret_4h_bps: float = -1e9
    btc_drop_cut_ur_max_bps: float = 0.0
    # v1.2.0 opp_floor : signal opposé détecté sur token détenu gagnant →
    # plancher cliquet à lock_ratio × gain courant (armement au scan 4h,
    # déclenchement rules.opp_floor_rule). Walk-forward strict 4/4 + test
    # nul destructeur (backtest_opposite_cut.py, 2026-06-11).
    # Kill-switch : opp_floor_lock_ratio = 0.0
    opp_floor_lock_ratio: float = 0.80
    opp_floor_min_gain_bps: float = 300.0
    # v11.7.2 dead-timeout early exit — RETIRÉE v1.4.0 (kill-switch cap=-99999).
    # Le cap 150 bps avait été calibré sur le MFE du backtest, qui lit les mèches
    # de bougie (high/low) alors que le bot live track le MFE sur le mark. Sous MFE
    # réaliste, bien plus de trades tombent sous le cap → la règle coupait des
    # positions récupérables. Walk-forward dédié dates glissantes (mfe_on_close) :
    # retrait gagnant 6/7 tranches, ΣΔPnL +$798, DD meilleur. Réactiver = remettre 150.
    # Cf. memory bt-mfe-wick-bias-2026-06.
    dead_timeout_lead_hours: float = 12.0
    dead_timeout_mfe_cap_bps: float = -99999.0
    dead_timeout_mae_floor_bps: float = -500.0
    dead_timeout_slack_bps: float = 300.0
    # v12.7.1 trajectory cut (regime-conditioned, S5)
    traj_cut_strategies: frozenset[str] = frozenset({"S5"})
    traj_cut_btc_z_threshold: float = -0.5
    traj_cut_decline_rate_min_bps_per_h: float = 100.0
    traj_cut_time_since_mfe_min_h: float = 4.0
    traj_cut_at_mae_slack_bps: float = 100.0
    traj_cut_min_loss_bps: float = -200.0
    traj_cut_long_only: bool = True   # v1.6.4 : ne jamais couper un SHORT (mean-revert)
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
    regime_alert_disp_7d_bps: float = 99999.0  # désactivé 2026-06-30 (kill-switch ≥99000) : l'alerte 🌪️ par-bot était commune aux 4 bots ; le nudge régime reste sur le canal SENIOR via le cron regime_alert.py. Ré-activer = remettre 700.0.
    regime_alert_wr_pct: float = 35.0
    regime_alert_lookback: int = 10
    regime_alert_cooldown_h: float = 24.0
    regime_alert_strategy: str = "S5"
    regime_alert_direction: int = 1

    # ── Timing ───────────────────────────────────────────────────────
    cooldown_hours: float = 24.0
    scan_interval: float = 3600.0

    # ── Per-bot strategy enablement (bots.json overrides) ────────────
    enabled_strategies: frozenset[str] = frozenset({"S1", "S5", "S8", "S9", "S10"})

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


# ── Per-bot configuration (bots.json) ─────────────────────────────────

MAX_BOTS = 8


@dataclass(frozen=True)
class BotConfig:
    """One bots.json entry. Secrets are NEVER stored here — only the names
    of the .env variables holding them (resolved at broker init)."""
    id: str
    label: str
    mode: str                      # "paper" | "live"
    color: str = ""
    private_key_env: str = ""      # env var name holding the signer key
    account_address: str = ""      # master wallet (agent model); "" = key IS wallet
    agent_expiry: str = ""         # ISO date expiration agent wallet ; "" = clé directe (pas d'expiration)
    capital_initial: float = 1000.0
    capital_cap: float = 0.0       # 0 = no cap (replaces JUNIOR_CAPITAL_CAP)
    tg_token_env: str = ""
    tg_chat_id_env: str = ""
    tg_categories: str = "*"
    public_url: str = ""
    enabled: bool = True
    start_paused: bool = False
    overrides: dict = field(default_factory=dict)

    def params(self, base: Params = DEFAULT_PARAMS) -> Params:
        ov = dict(self.overrides)
        # JSON can't carry sets/tuples — coerce the common list-valued keys.
        if "enabled_strategies" in ov:
            ov["enabled_strategies"] = frozenset(ov["enabled_strategies"])
        if "trade_blacklist" in ov:
            ov["trade_blacklist"] = frozenset(ov["trade_blacklist"])
        if "trade_symbols" in ov:
            ov["trade_symbols"] = tuple(ov["trade_symbols"])
        return base.with_overrides(ov)


def parse_bots_config(raw: dict) -> list[BotConfig]:
    """Validation pure d'une config bots (dict déjà parsé). Fatal sur :
    clé d'override inconnue, id dupliqué, > MAX_BOTS, env de signer partagée
    entre bots live (nonce safety). Utilisée par load_bots_config (boot) et
    par la validation HTTP de /master (dry-run sans toucher au disque)."""
    bots: list[BotConfig] = []
    for b in raw.get("bots", []):
        tg = b.get("telegram", {}) or {}
        cfg = BotConfig(
            id=b["id"], label=b.get("label", b["id"].upper()),
            mode=b.get("mode", "paper"), color=b.get("color", ""),
            private_key_env=b.get("private_key_env", ""),
            account_address=b.get("account_address", ""),
            agent_expiry=b.get("agent_expiry", ""),
            capital_initial=float(b.get("capital_initial", 1000.0)),
            capital_cap=float(b.get("capital_cap", 0.0)),
            tg_token_env=tg.get("token_env", ""),
            tg_chat_id_env=tg.get("chat_id_env", ""),
            tg_categories=tg.get("categories", "*"),
            public_url=b.get("public_url", ""),
            enabled=bool(b.get("enabled", True)),
            start_paused=bool(b.get("start_paused", False)),
            overrides=b.get("overrides", {}) or {},
        )
        if cfg.mode not in ("paper", "live"):
            raise ValueError(f"bot {cfg.id}: mode must be paper|live")
        cfg.params()  # validates override keys (raises on unknown)
        bots.append(cfg)
    enabled = [b for b in bots if b.enabled]
    ids = [b.id for b in enabled]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate bot ids")
    if len(enabled) > MAX_BOTS:
        raise ValueError(f"{len(enabled)} enabled bots > MAX_BOTS={MAX_BOTS}")
    signers = [b.private_key_env for b in enabled if b.mode == "live" and b.private_key_env]
    if len(set(signers)) != len(signers):
        raise ValueError("two live bots share the same private_key_env — "
                         "same signer = nonce conflicts, refusing to start")
    return enabled


def load_bots_config(path: str) -> list[BotConfig]:
    """Parse bots.json (lecture fichier + parse_bots_config)."""
    import json as _json
    with open(path) as fh:
        raw = _json.load(fh)
    return parse_bots_config(raw)
