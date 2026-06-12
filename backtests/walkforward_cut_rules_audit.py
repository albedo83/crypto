"""Walk-forward à dates de fin GLISSANTES sur les règles de coupe S9/S5.

Motivation (2026-06-12, user) : l'audit d'ablation (backtest_rule_audit) utilise
4 fenêtres qui finissent TOUTES à la même date (biais single-end-date, cf.
drop_mina_walkforward). Le user a repéré 2 règles de coupe douteuses :
  - s9_early_exit (-500/8h) → audit = NEUTRE
  - traj_cut S5            → audit = À RE-TESTER (négatif sur 4/4)
traj_cut avait pourtant PASSÉ un walk-forward strict à son ship (v12.7.1) →
hypothèse de décroissance (regime drift).

Ce test re-mesure baseline vs {−s9_early, −traj_cut, −both} sur N tranches OOS
de 6 mois à dates de fin DIFFÉRENTES (glissantes), pour distinguer un vrai effet
robuste d'un artefact de date de fin.

Lecture : ΔPnL = variant − baseline (POSITIF = retirer la règle RAPPORTE sur
cette tranche). Une règle est à retirer si le retrait gagne sur la majorité des
tranches ET ne dégrade pas le DD au-delà de la tolérance.

Usage : python3 -m backtests.walkforward_cut_rules_audit
"""

from __future__ import annotations

import dataclasses as dc
import os
import sys
import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

START_CAP = 500.0
OOS_MONTHS = 6
END_OFFSETS = [0, 3, 6, 9, 12, 15, 18]   # tranches OOS de 6m, end décalé

VARIANTS = [
    ("baseline",        {}),
    ("-s9_early",       {"s9_early_exit_bps": -1e9}),
    ("-traj_cut",       {"traj_cut_strategies": frozenset()}),
    ("-both",           {"s9_early_exit_bps": -1e9,
                         "traj_cut_strategies": frozenset()}),
]


def main() -> int:
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    # tranches OOS glissantes
    slices = []
    for off in END_OFFSETS:
        oos_end = end_dt - relativedelta(months=off)
        oos_start = oos_end - relativedelta(months=OOS_MONTHS)
        slices.append((off, int(oos_start.timestamp() * 1000),
                       int(oos_end.timestamp() * 1000),
                       oos_start.date().isoformat(), oos_end.date().isoformat()))

    def run(params, s_ms, e_ms):
        br._P = params
        r = run_window(features, data, sectors, dxy,
                       start_ts_ms=s_ms, end_ts_ms=e_ms,
                       start_capital=START_CAP,
                       oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True)
        return r["end_capital"], r["max_dd_pct"]

    t0 = time.time()
    # baseline par tranche
    base = {}
    for off, s_ms, e_ms, sd, ed in slices:
        base[off] = run(DEFAULT_PARAMS, s_ms, e_ms)

    print(f"\nWalk-forward dates de fin glissantes — OOS 6m, départ ${START_CAP:.0f}")
    print(f"(ΔPnL = variant − baseline ; + = RETIRER la règle rapporte)  [{time.time()-t0:.0f}s]\n")
    hdr = f"  {'tranche OOS':25s}{'baseline$':>11s}"
    for name, _ in VARIANTS[1:]:
        hdr += f"{name:>12s}"
    print(hdr)

    agg = {name: {"win": 0, "tot": 0.0, "dd": 0.0} for name, _ in VARIANTS[1:]}
    for off, s_ms, e_ms, sd, ed in slices:
        b_cap, b_dd = base[off]
        line = f"  {sd}→{ed} {b_cap:>10.0f}"
        for name, kw in VARIANTS[1:]:
            v_cap, v_dd = run(dc.replace(DEFAULT_PARAMS, **kw), s_ms, e_ms)
            d = v_cap - b_cap
            line += f"{d:>+11.0f}$"
            agg[name]["tot"] += d
            agg[name]["dd"] += (v_dd - b_dd)
            if d > 1:
                agg[name]["win"] += 1
        print(line)

    n = len(slices)
    print(f"\n  {'variant':14s}{'tranches gagnées':>18s}{'ΣΔPnL':>12s}{'ΔDD moy':>10s}   verdict")
    for name, _ in VARIANTS[1:]:
        a = agg[name]
        ddm = a["dd"] / n
        if a["win"] >= n - 1:
            verdict = "RETRAIT ROBUSTE"
        elif a["win"] >= n // 2 + 1:
            verdict = "retrait probable"
        elif a["win"] <= 1:
            verdict = "GARDER (règle utile)"
        else:
            verdict = "neutre/mitigé"
        print(f"  {name:14s}{a['win']:>10d}/{n:<7d}{a['tot']:>+11.0f}$"
              f"{ddm:>+8.2f}pp   {verdict}")
    print("\n  (ΔDD>0 = retirer la règle AMÉLIORE le DD ; <0 = retrait dégrade le DD.)")
    br._P = DEFAULT_PARAMS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
