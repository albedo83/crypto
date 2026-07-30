"""WALK-FORWARD — plafonner la FORCE du signal S5.

Prémisse (eda_entry_quality_floor2.py, 2026-07-30), consistante sur 28m/12m/6m :
S5 est « suivi de divergence sectorielle », et son P&L est INVERSEMENT lié à la
force de la divergence.

    quartile de strength      Q1 (faible)   Q2      Q3      Q4 (forte)
    net moyen 28m                  +187     -33     -21     -44
    net moyen 12m                  +247     -17     -77     -36
    net moyen  6m                  +148     -40     -37    -186

Seul Q1 est positif, sur les trois fenêtres. Or le moteur trie par force
DÉCROISSANTE : il priorise exactement les pires candidats S5.

Mécanique plausible : une divergence énorme = mouvement déjà étendu = entrée
tardive. Une divergence faible = on est tôt.

TEST : sauter les entrées S5 dont la force dépasse un seuil. Grille GROSSIÈRE
(pas d'optimum fin), fenêtres OOS glissantes de 6 mois NON chevauchantes
(offsets 0/6/12/18), critère strict 4/4 en P&L avec ΔDD non dégradé.

Anti-refit : les seuils testés sont des ronds arbitraires, PAS les bornes de
quartile mesurées ci-dessus. On cherche un PLATEAU, pas un pic.

Usage : python3 -m backtests.backtest_s5_strength_cap
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS
import alfred.signals as _alf_signals

START_CAP = 500.0
OFFSETS = [0, 6, 12, 18]          # 4 fenêtres OOS de 6 mois, non chevauchantes
CAPS = [2000, 2500, 3000, 3500, 4000, 5000, 6000]

_ORIG = _alf_signals.detect_token_signals


def _cap_filter(cap):
    def wrapped(*a, **k):
        return [s for s in _ORIG(*a, **k)
                if not (s["strategy"] == "S5" and s.get("strength", 0) > cap)]
    return wrapped


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
                     int(e.timestamp() * 1000), f"{s:%Y-%m}→{e:%Y-%m}"))
    print("Fenêtres OOS glissantes (6 mois, non chevauchantes) :")
    for n, _, _, lab in wins:
        print(f"  {n:8s} {lab}")

    def run(cap):
        _alf_signals.detect_token_signals = (_cap_filter(cap) if cap else _ORIG)
        br._alf_signals.detect_token_signals = _alf_signals.detect_token_signals
        out = {}
        for name, s_ms, e_ms, _lab in wins:
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=e_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            per = defaultdict(lambda: [0, 0.0])
            for t in r["trades"]:
                per[t["strat"]][0] += 1
                per[t["strat"]][1] += t.get("pnl", 0.0)
            out[name] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                         "n": r["n_trades"],
                         "n_s5": per["S5"][0], "pnl_s5": round(per["S5"][1], 2)}
        return out

    t0 = time.time()
    print("\n=== référence : aucun plafond")
    base = run(None)
    for n, _, _, _ in wins:
        b = base[n]
        print(f"  {n:8s} fin ${b['end']:>7.0f}  DD {b['dd']:>6.1f}%  "
              f"n={b['n']:>4d}  (S5 n={b['n_s5']:>3d} pnl {b['pnl_s5']:>+8.0f})")

    res = {}
    print("\n=== plafonds testés (Δ$ vs référence ; + = le plafond rapporte)")
    print(f"  {'cap':>6s}" + "".join(f"{n:>12s}" for n, _, _, _ in wins)
          + f"{'P&L +':>8s}" + "".join(f"{'ΔDD':>8s}" for _ in wins)
          + f"{'DD ok':>7s}")
    for cap in CAPS:
        r = run(cap)
        res[cap] = r
        d = {n: r[n]["end"] - base[n]["end"] for n, _, _, _ in wins}
        dd = {n: base[n]["dd"] - r[n]["dd"] for n, _, _, _ in wins}  # + = DD amélioré
        npos = sum(1 for v in d.values() if v > 0)
        nddok = sum(1 for v in dd.values() if v >= -2.0)   # tolérance 2pp
        print(f"  {cap:>6d}" + "".join(f"{d[n]:>+12.0f}" for n, _, _, _ in wins)
              + f"{npos:>5d}/4" + "".join(f"{dd[n]:>+8.1f}" for n, _, _, _ in wins)
              + f"{nddok:>5d}/4")
    _alf_signals.detect_token_signals = _ORIG

    print("\n=== trades S5 restants par plafond (contrôle : le filtre mord-il ?)")
    print(f"  {'cap':>6s}" + "".join(f"{n:>14s}" for n, _, _, _ in wins))
    print(f"  {'aucun':>6s}" + "".join(
        f"{base[n]['n_s5']:>6d} /{base[n]['pnl_s5']:>+7.0f}" for n, _, _, _ in wins))
    for cap in CAPS:
        print(f"  {cap:>6d}" + "".join(
            f"{res[cap][n]['n_s5']:>6d} /{res[cap][n]['pnl_s5']:>+7.0f}"
            for n, _, _, _ in wins))

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_strength_cap_wf.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "offsets": OFFSETS, "caps": CAPS,
                   "base": base, "results": {str(k): v for k, v in res.items()}},
                  f, indent=1, default=str)
    print(f"\n[{time.time()-t0:.0f}s]  Dump : {path}")
    print("\nCRITÈRE : 4/4 en P&L ET DD non dégradé de plus de 2pp, sur un"
          " PLATEAU\n           de seuils voisins. Un seul pic isolé = refit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
