"""Audit d'ablation du stack de règles actives — re-validation périodique.

Motivation (2026-06-11, demande utilisateur) : les validations individuelles
des règles shippées datent de leur ship (souvent pré-phase-6, sémantique
legacy). Cet audit re-mesure la contribution MARGINALE de chaque règle dans
le stack actuel : règle désactivée (kill-switch) vs stack complet, 4 fenêtres
canoniques, sémantique alignée.

Lecture (Δ$ = stack_complet − sans_la_règle ; positif = la règle rapporte) :
  CONFIRMÉE   — la retirer coûte sur ≥3 fenêtres
  NEUTRE      — bruit (|Δ| faible des deux côtés)
  À RE-TESTER — la retirer RAPPORTE sur ≥3 fenêtres → walk-forward dédié
                avant toute décision (l'ablation 1-à-1 est path-dépendante,
                ce n'est PAS une preuve suffisante de retrait).

Usage : python3 -m backtests.backtest_rule_audit
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

# (nom, version ship, overrides Params qui DÉSACTIVENT la règle)
ABLATIONS = [
    ("blacklist SUI/IMX/LINK", "v11.4.10", {"trade_blacklist": frozenset()}),
    ("OI gate LONG",           "v11.4.9",  {"oi_long_gate_bps": 1e9}),
    ("S10 SHORT-only+whitelist", "v11.3.4",
     {"s10_allow_longs": True,
      "s10_allowed_tokens": frozenset(DEFAULT_PARAMS.trade_symbols)}),
    ("S10 trailing",           "v11.4.0",  {"s10_trailing_trigger": 1e9}),
    ("S9 early exit -500/8h",  "ancien",   {"s9_early_exit_bps": -1e9}),
    ("dead timeout",           "v11.7.2",  {"dead_timeout_mfe_cap_bps": -99999.0}),
    ("S8 in-life trail",       "v12.5.30", {"s8_inlife_params": {}}),
    ("S8 dead-in-water",       "v12.6.0",  {"s8_dead_mfe_max_bps": -99999.0}),
    ("traj cut S5",            "v12.7.1",  {"traj_cut_strategies": frozenset()}),
    ("runner ext S9",          "v11.7.32", {"runner_ext_strategies": frozenset()}),
    ("prop trail S9 bull",     "v12.11.0", {"prop_trail_params": {}}),
    ("S9 early dead 12h/150",  "v12.15.0", {"s9_early_dead_mfe_max_bps": -99999.0}),
    ("BTC drop cut LONG",      "v12.15.0", {"btc_drop_cut_ret_4h_bps": -1e9}),
    ("modulateur macro",       "v11.10.0", {"adaptive_alpha": {},
                                            "adaptive_alpha_dir": {}}),
    ("cap notionnel $500",     "v12.13.9", {"max_notional_per_trade": 0.0}),
    ("opp_floor 0.80",         "v1.2.0",   {"opp_floor_lock_ratio": 0.0}),
]


def main() -> int:
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    ms = lambda d_: int(datetime.fromisoformat(d_ + "T00:00:00+00:00").timestamp() * 1000)

    def run_all(params):
        br._P = params
        out = {}
        for w, start in WINDOWS:
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=ms(start), end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True)
            out[w] = (r["end_capital"], r["max_dd_pct"])
        return out

    t0 = time.time()
    base = run_all(DEFAULT_PARAMS)
    print(f"\nStack complet ($500 départ) : " +
          " ".join(f"{w}=${base[w][0]:.0f}" for w, _ in WINDOWS) +
          f"  [{time.time()-t0:.0f}s]\n")

    print(f"  {'règle retirée':26s} {'ship':9s}" +
          "".join(f"{w:>10s}" for w, _ in WINDOWS) + "   ΔDD moy   verdict")
    results = []
    for name, ver, kw in ABLATIONS:
        t0 = time.time()
        abl = run_all(dc.replace(DEFAULT_PARAMS, **kw))
        # Δ$ = contribution de la règle (stack − ablation) : + = elle rapporte
        deltas = {w: base[w][0] - abl[w][0] for w, _ in WINDOWS}
        ddd = sum(base[w][1] - abl[w][1] for w, _ in WINDOWS) / len(WINDOWS)
        n_pos = sum(1 for v in deltas.values() if v > 1)
        n_neg = sum(1 for v in deltas.values() if v < -1)
        if n_pos >= 3:
            verdict = "CONFIRMÉE"
        elif n_neg >= 3:
            verdict = "⚠ À RE-TESTER"
        else:
            verdict = "neutre"
        cells = "".join(f"{deltas[w]:>+9.0f}$" for w, _ in WINDOWS)
        print(f"  {name:26s} {ver:9s}{cells}   {ddd:+6.2f}pp  {verdict}")
        results.append((name, verdict))
    br._P = DEFAULT_PARAMS
    n_bad = sum(1 for _, v in results if "RE-TESTER" in v)
    print(f"\n  {len(results)} règles auditées — {n_bad} à re-tester.")
    print("  (Δ$ = contribution marginale dans le stack actuel ; ΔDD>0 = la règle améliore le DD.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
