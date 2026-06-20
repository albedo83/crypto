"""Re-validation de TOUTES les règles d'exit sous MFE réaliste (mark) vs MFE-wick.

Contexte : le BT track MFE/MAE sur les high/low de bougie (mèches) alors que le bot
live le track sur le mark horaire (cf. memory bt-mfe-wick-bias-2026-06). Les règles
pilotées par MFE (prop_trail, s8_inlife, s10_trailing, traj_cut, dead_timeout,
runner_ext, s8_dead, s9_early_dead) ont donc été validées sur un MFE optimiste.

Ce script ré-exécute l'audit d'ablation (`backtest_rule_audit.py`) sous DEUX modèles :
  - WICK     : MFE sur high/low (canonique actuel)
  - REALISTIC : MFE sur close (mfe_on_close=True, proxy mark ; catastrophe-stop
                inchangé)
sur les 4 fenêtres canoniques. Pour chaque règle : contribution marginale Δ$ =
(stack complet − sans la règle) dans CHAQUE modèle. On compare :
  - une règle Δ$>0 sur ≥3 fenêtres dans les DEUX modèles  → ROBUSTE (garder)
  - Δ$>0 en wick mais ≤0/neutre en réaliste                → FRAGILE (artefact mèche,
                                                              walk-forward dédié / retrait)
  - Δ$≤0 dans les deux                                      → à re-tester (ne rapporte pas)

Usage : python3 -m backtests.backtest_rule_audit_realistic
"""
from __future__ import annotations

import dataclasses as dc
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [
    ("28m", "2024-02-04"),
    ("12m", "2025-06-04"),
    ("6m",  "2025-12-04"),
    ("3m",  "2026-03-04"),
]
START_CAP = 500.0

# MFE = règle dont la condition lit mfe_bps/mae_bps (sensible au biais wick)
ABLATIONS = [
    ("blacklist SUI/IMX/LINK", False, {"trade_blacklist": frozenset()}),
    ("OI gate LONG",           False, {"oi_long_gate_bps": 1e9}),
    ("S10 SHORT+whitelist",    False,
     {"s10_allow_longs": True,
      "s10_allowed_tokens": frozenset(DEFAULT_PARAMS.trade_symbols)}),
    ("S10 trailing",           True,  {"s10_trailing_trigger": 1e9}),
    ("S9 early exit -500/8h",  False, {"s9_early_exit_bps": -1e9}),
    ("dead timeout",           True,  {"dead_timeout_mfe_cap_bps": -99999.0}),
    ("S8 in-life trail",       True,  {"s8_inlife_params": {}}),
    ("S8 dead-in-water",       True,  {"s8_dead_mfe_max_bps": -99999.0}),
    ("traj cut S5",            True,  {"traj_cut_strategies": frozenset()}),
    ("runner ext S9",          True,  {"runner_ext_strategies": frozenset()}),
    ("prop trail",             True,  {"prop_trail_params": {}}),
    ("S9 early dead 12h/150",  True,  {"s9_early_dead_mfe_max_bps": -99999.0}),
    ("BTC drop cut LONG",      False, {"btc_drop_cut_ret_4h_bps": -1e9}),
    ("modulateur macro",       False, {"adaptive_alpha": {}, "adaptive_alpha_dir": {}}),
    ("cap notionnel $500",     False, {"max_notional_per_trade": 0.0}),
    ("opp_floor 0.80",         True,  {"opp_floor_lock_ratio": 0.0}),
]


def main() -> int:
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    ms = lambda d_: int(datetime.fromisoformat(d_ + "T00:00:00+00:00").timestamp() * 1000)

    def run_all(params, mfe_on_close):
        br._P = params
        out = {}
        for w, start in WINDOWS:
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=ms(start), end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           mfe_on_close=mfe_on_close)
            out[w] = (r["end_capital"], r["max_dd_pct"])
        return out

    for model, moc in (("WICK (canonique)", False), ("REALISTIC (mark/close)", True)):
        t0 = time.time()
        base = run_all(DEFAULT_PARAMS, moc)
        print(f"\n========== MODÈLE {model} ==========", flush=True)
        print(f"Stack complet ($500) : " +
              " ".join(f"{w}=${base[w][0]:.0f}(DD{base[w][1]:.0f})" for w, _ in WINDOWS) +
              f"  [{time.time()-t0:.0f}s]")
        print(f"  {'règle retirée':24s}{'MFE?':5s}" +
              "".join(f"{w:>9s}" for w, _ in WINDOWS) + "  ΔDDmoy  verdict")
        for name, is_mfe, kw in ABLATIONS:
            abl = run_all(dc.replace(DEFAULT_PARAMS, **kw), moc)
            deltas = {w: base[w][0] - abl[w][0] for w, _ in WINDOWS}
            ddd = sum(base[w][1] - abl[w][1] for w, _ in WINDOWS) / len(WINDOWS)
            n_pos = sum(1 for v in deltas.values() if v > 1)
            n_neg = sum(1 for v in deltas.values() if v < -1)
            verdict = ("CONFIRMÉE" if n_pos >= 3 else
                       "⚠ RE-TESTER" if n_neg >= 3 else "neutre")
            cells = "".join(f"{deltas[w]:>+8.0f}$" for w, _ in WINDOWS)
            tag = "MFE" if is_mfe else " . "
            print(f"  {name:24s}{tag:5s}{cells}  {ddd:+5.1f}pp  {verdict}", flush=True)
    br._P = DEFAULT_PARAMS
    print("\nLecture : compare la ligne d'une règle MFE entre les 2 modèles.")
    print("  CONFIRMÉE dans les 2 = robuste ; CONFIRMÉE en wick mais neutre/RE-TESTER")
    print("  en réaliste = artefact de mèche → walk-forward dédié avant décision.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
