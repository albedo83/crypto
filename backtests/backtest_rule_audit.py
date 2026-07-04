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
    ("28m", 28),
    ("12m", 12),
    ("6m",  6),
    ("3m",  3),
]
START_CAP = 500.0

# 2026-07-04 (chantier ablation chaîne de sorties) : périmètre recentré sur
# les RÈGLES DE SORTIE actives — les ablations d'entrée (blacklist, OI gate,
# modulateur, cap notionnel, S10 whitelist) sortent de la liste, dead_timeout
# est déjà OFF depuis v1.4.0 (kill-switch settings). Fenêtres re-datées à la
# dernière bougie (plus de dates gravées), config canonique
# margin_check+mfe_on_close, Δ P&L ET ΔDD par fenêtre, dump JSON.
# (nom, version ship, overrides Params qui DÉSACTIVENT la règle)
ABLATIONS = [
    ("S10 trailing",           "v11.4.0",  {"s10_trailing_trigger": 1e9}),
    ("S9 early exit -500/8h",  "ancien",   {"s9_early_exit_bps": -1e9}),
    ("S8 in-life trail",       "v12.5.30", {"s8_inlife_params": {}}),
    ("S8 dead-in-water",       "v12.6.0",  {"s8_dead_mfe_max_bps": -99999.0}),
    ("traj cut S5",            "v12.7.1",  {"traj_cut_strategies": frozenset()}),
    ("runner ext S9",          "v11.7.32", {"runner_ext_strategies": frozenset()}),
    ("prop trail S9 bull",     "v12.11.0", {"prop_trail_params": {}}),
    ("S9 early dead 12h/150",  "v12.15.0", {"s9_early_dead_mfe_max_bps": -99999.0}),
    ("BTC drop cut LONG",      "v12.15.0", {"btc_drop_cut_ret_4h_bps": -1e9}),
    ("opp_floor 0.80",         "v1.2.0",   {"opp_floor_lock_ratio": 0.0}),
]


def main() -> int:
    import json as _json
    from dateutil.relativedelta import relativedelta
    from collections import Counter
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()

    def run_all(params, keep_trades=False):
        br._P = params
        out = {}
        for w, months in WINDOWS:
            s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True)
            out[w] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                      "n": r["n_trades"]}
            if keep_trades:
                out[w]["reasons"] = dict(Counter(
                    t.get("reason") for t in r["trades"]))
                out[w]["trades"] = [
                    {k: t.get(k) for k in ("coin", "strat", "dir", "reason",
                                           "net", "pnl", "entry_t", "exit_t")}
                    for t in r["trades"]]
        return out

    t0 = time.time()
    base = run_all(DEFAULT_PARAMS, keep_trades=True)
    print(f"\nStack complet ($500 départ, canonique margin+mfe_on_close) : " +
          " ".join(f"{w}=${base[w]['end']:.0f}" for w, _ in WINDOWS) +
          f"  [{time.time()-t0:.0f}s]\n")

    print(f"  {'règle retirée':26s} {'ship':9s}" +
          "".join(f"{w:>10s}" for w, _ in WINDOWS)
          + "".join(f"{'ΔDD ' + w:>9s}" for w, _ in WINDOWS) + "   verdict")
    results = []
    dump = {"generated": datetime.now().isoformat(),
            "windows_end": end_dt.isoformat(), "start_cap": START_CAP,
            "base": {w: {k: v for k, v in base[w].items() if k != "trades"}
                     for w, _ in WINDOWS},
            "ablations": {}}
    for name, ver, kw in ABLATIONS:
        abl = run_all(dc.replace(DEFAULT_PARAMS, **kw))
        # Δ$ = contribution de la règle (stack − ablation) : + = elle rapporte
        deltas = {w: base[w]["end"] - abl[w]["end"] for w, _ in WINDOWS}
        ddds = {w: base[w]["dd"] - abl[w]["dd"] for w, _ in WINDOWS}
        n_pos = sum(1 for v in deltas.values() if v > 1)
        n_nonpos = sum(1 for v in deltas.values() if v <= 1)
        if n_pos >= 3:
            verdict = "CONFIRMÉE"
        elif n_nonpos >= 3:
            verdict = "⚠ PANSEMENT?"   # apport ≤0 sur ≥3/4 — rapporté, pas jugé
        else:
            verdict = "neutre"
        cells = "".join(f"{deltas[w]:>+9.0f}$" for w, _ in WINDOWS)
        ddcells = "".join(f"{ddds[w]:>+8.1f}p" for w, _ in WINDOWS)
        print(f"  {name:26s} {ver:9s}{cells}{ddcells}  {verdict}")
        results.append((name, verdict))
        dump["ablations"][name] = {
            "ship": ver, "verdict": verdict,
            "delta_usd": {w: round(deltas[w], 2) for w, _ in WINDOWS},
            "delta_dd_pp": {w: round(ddds[w], 2) for w, _ in WINDOWS},
        }
    br._P = DEFAULT_PARAMS
    out_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "exit_ablation.json"), "w") as f:
        _json.dump(dump, f, indent=1, default=str)
    with open(os.path.join(out_dir, "exit_ablation_base_trades.json"), "w") as f:
        _json.dump({w: base[w]["trades"] for w, _ in WINDOWS}, f, default=str)
    n_bad = sum(1 for _, v in results if "PANSEMENT" in v)
    print(f"\n  {len(results)} règles de sortie auditées — {n_bad} candidates pansement.")
    print("  (Δ$ = contribution marginale ; ΔDD>0 = la règle améliore le DD. "
          "L'ablation DÉTECTE, ne juge pas — cf. runner_ext 5/7 vs strict 2/4.)")
    print(f"  Dumps : {out_dir}/exit_ablation.json + exit_ablation_base_trades.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
