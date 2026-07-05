"""Arrondi des constantes baroques — mesure propre (revue 2026-07-05).

Séquence actée : arrondi AVANT vol-targeting, parce que l'arrondi préserve la
topologie de l'espace de paramètres → le re-MC 12m avant/après est
directement comparable = lecture propre de ce que portaient les décimales.

Set arrondi (curation honnête, dans la bande de bruit ±10 % du MC sauf S1) :
  signal_mult  S1 1.125→1.0 (−11 %), S5 3.25→3.0 (−7.7 %)
  strat_z      6.42→6.5, 3.67→3.5, 6.99→7.0, 8.71→8.5, 3.66→3.5
               (mesures descriptives utilisées comme poids — la 2e décimale
               est de la fausse précision d'estimation)
NON arrondis : size_pct 0.18, ratios 0.8/0.9/0.3 (grille simple), quarts
(S8 1.25), hard_stop_slippage 0.20 (borne CALCULÉE), seuils de sortie (déjà
ronds — le baroque vit côté entrée/sizing).

Mesures :
  1. Base vs arrondi sur les 4 fenêtres (Δ end, Δ DD) — comparé à la bande
     de bruit du MC joint.
  2. Re-MC 12m + 3m (200 draws, seed 42, mêmes boules ±10/20 %) centré sur
     l'arrondi → rang du nouveau centre + spread inter-fenêtres
     (métrique actée : spread p98−p30 = 68 pts aujourd'hui, cible < 30).

Usage : python3 -m backtests.round_constants_test
"""
import dataclasses as dc
import json
import os
import random
import sys
import time
from datetime import datetime

sys.path.insert(0, "/home/crypto")

from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.exit_joint_montecarlo import draw_params, SEED
from alfred.settings import DEFAULT_PARAMS

OUT = os.path.join(os.path.dirname(__file__), "output")

ROUNDED = dc.replace(
    DEFAULT_PARAMS,
    signal_mult={**DEFAULT_PARAMS.signal_mult, "S1": 1.0, "S5": 3.0},
    strat_z={"S1": 6.5, "S5": 3.5, "S8": 7.0, "S9": 8.5, "S10": 3.5},
)


def main():
    print("Loading data…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in (("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3))}

    def run(params, w):
        br._P = params
        try:
            r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                           start_capital=500.0, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True)
            return r["end_capital"], r.get("max_dd_pct", 0.0)
        finally:
            br._P = DEFAULT_PARAMS

    print("\n1) Base vs ARRONDI — 4 fenêtres")
    print(f"{'fen':<5} {'base $':>9} {'arrondi $':>10} {'Δ%':>7} | "
          f"{'DD base':>8} {'DD arr':>7}")
    res = {}
    for w in ("28m", "12m", "6m", "3m"):
        eb, db = run(DEFAULT_PARAMS, w)
        er, dr = run(ROUNDED, w)
        res[w] = {"base": eb, "rounded": er, "dd_base": db, "dd_rounded": dr}
        print(f"{w:<5} {eb:>9.0f} {er:>10.0f} {(er/eb-1)*100:>+6.1f}% | "
              f"{db:>7.1f}% {dr:>6.1f}%", flush=True)

    print("\n2) Re-MC centré sur ARRONDI (mêmes seeds/boules que le MC de base)")
    mc = {}
    t0 = time.time()
    for w, delta, n in (("12m", 0.10, 200), ("12m", 0.20, 200),
                        ("3m", 0.10, 200), ("3m", 0.20, 200)):
        rng = random.Random(SEED)
        ends = []
        for i in range(n):
            p = draw_params(rng, delta, base=ROUNDED)
            e, _ = run(p, w)
            ends.append(e)
            if (i + 1) % 50 == 0:
                print(f"  [{w} ±{int(delta*100)}%] {i+1}/{n} "
                      f"({time.time()-t0:.0f}s)", flush=True)
        b = res[w]["rounded"]
        se = sorted(ends)
        rank = sum(1 for e in ends if e < b) / n * 100
        mc[f"{w}_d{int(delta*100)}"] = {
            "rounded_base": round(b, 2), "rank_pct": round(rank, 1),
            "med": round(se[n // 2], 2),
            "p5": round(se[int(0.05 * n)], 2), "p95": round(se[int(0.95 * n)], 2)}
        r = mc[f"{w}_d{int(delta*100)}"]
        print(f"{w} ±{int(delta*100)}% (centre arrondi) : base={b:.0f} "
              f"rang=p{r['rank_pct']:.0f} | med={r['med']:.0f} "
              f"p5={r['p5']:.0f} p95={r['p95']:.0f}", flush=True)

    json.dump({"windows": res, "mc": mc},
              open(os.path.join(OUT, "round_constants.json"), "w"), indent=1)
    print(f"\n→ {os.path.join(OUT, 'round_constants.json')}")


if __name__ == "__main__":
    main()
