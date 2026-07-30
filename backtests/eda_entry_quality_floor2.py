"""PRÉMISSE v2 — après réfutation de la v1, où est la vraie dimension de qualité ?

Ce que la v1 a établi (2026-07-30) :
  1. `entry_z` est une CONSTANTE PAR STRATÉGIE (S1 6.5, S5 3.5, S8 7.0, S9 8.5,
     S10 3.5 = `strat_z` des Params). Le tri `sort(key=(z, strength))` est donc
     un ORDRE DE PRIORITÉ ENTRE STRATÉGIES, pas un classement de qualité.
     Un « plancher de qualité sur z » est mécaniquement impossible : z ne porte
     AUCUNE information intra-signal.
  2. Le rang 0 est le PIRE (net moyen 59 / −8 / 56 bps selon fenêtre), les rangs
     suivants sont meilleurs — l'inverse de l'hypothèse de départ, et cohérent
     sur les 3 fenêtres.

Restent trois candidats pour expliquer le phénomène S5 (retirer un signal
perdant → les autres prennent plus de trades et gagnent moins) :

  A. `strength` — la seule variable INTRA-stratégie du classement. Discrimine-t-elle ?
  B. `n_cands_at_open` — nombre de candidats du scan. Le rang est confondu avec
     l'activité du marché : un scan à 4 candidats est un moment agité. Si c'est
     ça qui porte le signal, le levier n'est pas « filtrer » mais « trader quand
     ça bouge ».
  C. `n_pos_at_open` — occupation du portefeuille à l'entrée. C'est le VRAI
     mécanisme de blocage de S5 : une position S5 tient un slot 48h, pas un scan.
     Si les trades pris portefeuille plein sont pires, le levier devient
     « arrêter d'ajouter au-delà de N positions » — testable et jamais testé.

EDA descriptive uniquement. Aucun filtre appliqué.

Usage : python3 -m backtests.eda_entry_quality_floor2
"""

from __future__ import annotations

import json
import os
import statistics as stats
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6)]
START_CAP = 500.0
STRATS = ("S1", "S5", "S8", "S9", "S10")


def _agg(rows, key_fn, label, min_n=8):
    g = defaultdict(list)
    for t in rows:
        k = key_fn(t)
        if k is not None:
            g[k].append(t)
    print(f"    {label:20s} {'n':>6s} {'WR':>7s} {'net moy':>10s} "
          f"{'net med':>10s} {'P&L $':>9s}")
    out = {}
    for k in sorted(g, key=lambda x: str(x)):
        v = g[k]
        if len(v) < min_n:
            continue
        nets = [t["net"] for t in v]
        wr = sum(1 for t in v if t["pnl"] > 0) / len(v) * 100
        pnl = sum(t["pnl"] for t in v)
        print(f"    {str(k):20s} {len(v):>6d} {wr:>6.1f}% {stats.mean(nets):>10.1f} "
              f"{stats.median(nets):>10.1f} {pnl:>9.0f}")
        out[str(k)] = {"n": len(v), "wr": round(wr, 1),
                       "net_mean": round(stats.mean(nets), 1),
                       "pnl": round(pnl, 2)}
    return out


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

    dump = {"generated": datetime.now().isoformat(), "windows": {}}
    for w, months in WINDOWS:
        t0 = time.time()
        s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        r = run_window(features, data, sectors, dxy,
                       start_ts_ms=s_ms, end_ts_ms=end_ms,
                       start_capital=START_CAP, oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True,
                       realistic_trail_booking=True)
        T = [t for t in r["trades"] if t.get("entry_rank_all") is not None]
        print(f"\n{'='*78}\n### {w} — {len(T)} trades  (fin ${r['end_capital']:.0f})"
              f"  [{time.time()-t0:.0f}s]")
        d = {}

        print("\nA) `strength` par quartile, DANS chaque stratégie")
        d["strength"] = {}
        for s in STRATS:
            sub = [t for t in T if t["strat"] == s and t.get("entry_strength") is not None]
            vals = sorted(t["entry_strength"] for t in sub)
            if len(sub) < 40 or vals[0] == vals[-1]:
                print(f"  {s}: n={len(sub)}"
                      + (" — strength CONSTANTE, aucune information"
                         if sub and vals[0] == vals[-1] else " — trop peu"))
                continue
            cuts = [vals[int(len(vals) * q / 4)] for q in range(1, 4)]

            def q(t, _c=cuts):
                v = t["entry_strength"]
                for i, c in enumerate(_c):
                    if v < c:
                        return f"Q{i+1}"
                return "Q4"
            print(f"  {s} (n={len(sub)}, strength {vals[0]:.2f} → {vals[-1]:.2f})")
            d["strength"][s] = _agg(sub, q, f"{s} quartile")

        print("\nB) `n_cands_at_open` — agitation du scan")
        d["n_cands"] = _agg(T, lambda t: f"{min(t.get('n_cands_at_open') or 0, 5)} cand.",
                            "candidats au scan")

        print("\nC) `n_pos_at_open` — occupation du portefeuille à l'entrée")
        d["n_pos"] = _agg(T, lambda t: f"{t.get('n_pos_at_open')} pos. ouvertes",
                          "portefeuille")
        print("    (idem, PAR STRATÉGIE, pour séparer composition et effet propre)")
        d["n_pos_by_strat"] = {}
        for s in STRATS:
            sub = [t for t in T if t["strat"] == s]
            if len(sub) < 60:
                continue
            print(f"  — {s}")
            d["n_pos_by_strat"][s] = _agg(
                sub, lambda t: f"{min(t.get('n_pos_at_open') or 0, 5)}+ pos.",
                f"{s} portefeuille")

        print("\nD) composition par rang (explique l'effet de rang de la v1)")
        comp = defaultdict(Counter)
        for t in T:
            comp[min(t["entry_rank_all"], 3)][t["strat"]] += 1
        for k in sorted(comp):
            tot = sum(comp[k].values())
            txt = "  ".join(f"{s}={comp[k][s]*100//tot:2d}%" for s in STRATS)
            print(f"    rang {k}: n={tot:5d}   {txt}")
        d["composition"] = {str(k): dict(v) for k, v in comp.items()}

        dump["windows"][w] = d

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "entry_quality_floor_eda2.json")
    with open(path, "w") as f:
        json.dump(dump, f, indent=1, default=str)
    print(f"\nDump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
