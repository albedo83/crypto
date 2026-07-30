"""WALK-FORWARD — n'entrer que si le marché est agité (N candidats au scan).

Second effet consistant sorti de l'EDA (eda_entry_quality_floor2.py) :

    candidats au scan       1        2        3        4       5+
    net moyen 28m        +35      +99      -38     +185     +415
    net moyen 12m        +40     +129      -34     +135     +695
    net moyen  6m        -20     +110     -246      +60     +896

Les scans à 5+ candidats sont, de loin, les meilleurs — sur les trois fenêtres.
Les scans à 1 seul candidat sont au mieux nuls. Lecture mécanique : quand cinq
signaux de fade se déclenchent en même temps, c'est que le marché a réellement
bougé — le contexte où les stratégies de retour à la moyenne fonctionnent.

Attention au biais : le nombre de candidats est aussi un proxy de RÉGIME. Un
filtre là-dessus peut n'être qu'un pari sur la volatilité déguisé en règle
d'entrée, et il coupe énormément de trades (~48 % sont des scans à 1 candidat).

TEST : n'entrer que si le scan compte ≥ N candidats. Fenêtres OOS glissantes de
6 mois non chevauchantes, critère strict 4/4 + DD non dégradé.

Usage : python3 -m backtests.backtest_scan_activity_gate
"""

from __future__ import annotations

import json
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

START_CAP = 500.0
OFFSETS = [0, 6, 12, 18]
GATES = [2, 3, 4, 5]


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS

    wins = []
    for off in OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        wins.append((f"OOS-{off}", int(s.timestamp() * 1000),
                     int(e.timestamp() * 1000)))

    def run(gate):
        out = {}
        for name, s_ms, e_ms in wins:
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=e_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True,
                           min_scan_candidates=gate)
            out[name] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                         "n": r["n_trades"]}
        return out

    t0 = time.time()
    base = run(0)
    print("\n=== référence (aucun seuil)")
    for n, _, _ in wins:
        print(f"  {n:8s} fin ${base[n]['end']:>7.0f}  DD {base[n]['dd']:>6.1f}%"
              f"  n={base[n]['n']:>4d}")

    print("\n=== seuils (Δ$ vs référence ; + = le seuil rapporte)")
    print(f"  {'≥cand':>6s}" + "".join(f"{n:>12s}" for n, _, _ in wins)
          + f"{'P&L +':>8s}" + "".join(f"{'ΔDD':>8s}" for _ in wins)
          + f"{'trades restants':>18s}")
    res = {}
    for g in GATES:
        r = run(g)
        res[g] = r
        d = {n: r[n]["end"] - base[n]["end"] for n, _, _ in wins}
        dd = {n: base[n]["dd"] - r[n]["dd"] for n, _, _ in wins}
        npos = sum(1 for v in d.values() if v > 0)
        keep = sum(r[n]["n"] for n, _, _ in wins) / sum(base[n]["n"] for n, _, _ in wins)
        print(f"  {g:>6d}" + "".join(f"{d[n]:>+12.0f}" for n, _, _ in wins)
              + f"{npos:>5d}/4" + "".join(f"{dd[n]:>+8.1f}" for n, _, _ in wins)
              + f"{keep*100:>16.0f}%")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "scan_activity_gate_wf.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "base": base, "results": {str(k): v for k, v in res.items()}},
                  f, indent=1, default=str)
    print(f"\n[{time.time()-t0:.0f}s]  Dump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
