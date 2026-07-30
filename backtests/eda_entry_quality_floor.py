"""PRÉMISSE — le trade marginal est-il structurellement pire que le trade de tête ?

Origine (2026-07-30) : en retirant S5 du stack, les quatre autres signaux prennent
+153 trades et perdent $7215. Hypothèse : le bot remplit ses slots quoi qu'il
arrive, et les candidats qu'il accepte en bas de classement détruisent de la
valeur. Si c'est vrai, un PLANCHER DE QUALITÉ transversal (ne rien prendre plutôt
que prendre un mauvais candidat) devrait payer.

Cette EDA ne teste AUCUN filtre — elle vérifie seulement que la prémisse tient,
avant de brûler du compute en walk-forward (doctrine premise-gate).

Sorties :
  1. P&L / WR / net_bps par RANG d'entrée (0 = meilleur candidat du scan)
  2. idem par rang PARMI LES RETENUS
  3. histogramme du z d'entrée + P&L par décile de z, GLOBAL et PAR STRATÉGIE
     (le z n'est pas comparable entre stratégies : c'est le point critique
      d'un plancher « transversal »)
  4. le même découpage restreint aux scans SATURÉS (là où le plancher mordrait)

Usage : python3 -m backtests.eda_entry_quality_floor
"""

from __future__ import annotations

import json
import os
import statistics as stats
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

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6)]
START_CAP = 500.0


def _agg(rows, key_fn, label, min_n=10):
    """Agrège par clé et imprime n / WR / net_bps moyen / P&L."""
    g = defaultdict(list)
    for t in rows:
        k = key_fn(t)
        if k is not None:
            g[k].append(t)
    print(f"  {label:22s} {'n':>6s} {'WR':>7s} {'net moy':>10s} "
          f"{'net med':>10s} {'P&L $':>10s}")
    out = {}
    for k in sorted(g):
        v = g[k]
        if len(v) < min_n:
            continue
        nets = [t["net"] for t in v]
        wr = sum(1 for t in v if t["pnl"] > 0) / len(v) * 100
        pnl = sum(t["pnl"] for t in v)
        print(f"  {str(k):22s} {len(v):>6d} {wr:>6.1f}% {stats.mean(nets):>10.1f} "
              f"{stats.median(nets):>10.1f} {pnl:>10.0f}")
        out[str(k)] = {"n": len(v), "wr": round(wr, 1),
                       "net_mean": round(stats.mean(nets), 1),
                       "net_median": round(stats.median(nets), 1),
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
                       start_capital=START_CAP,
                       oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True,
                       realistic_trail_booking=True)
        T = [t for t in r["trades"] if t.get("entry_rank_all") is not None]
        print(f"\n{'='*78}\n### {w} — {len(T)} trades instrumentés "
              f"(capital final ${r['end_capital']:.0f}) [{time.time()-t0:.0f}s]")

        d = {}
        print("\n1) par RANG dans la liste des candidats du scan")
        d["by_rank_all"] = _agg(T, lambda t: min(t["entry_rank_all"], 6), "rang (6=6+)")

        print("\n2) par rang PARMI LES RETENUS de ce scan")
        d["by_rank_taken"] = _agg(T, lambda t: min(t["entry_rank_taken"], 4),
                                  "rang retenu (4=4+)")

        print("\n3) par décile de z d'entrée — TOUTES stratégies confondues")
        zs = sorted(t["entry_z"] for t in T)
        cuts = [zs[int(len(zs) * q / 10)] for q in range(1, 10)]

        def zdec(t):
            z = t["entry_z"]
            for i, c in enumerate(cuts):
                if z < c:
                    return f"D{i+1} (<{c:.2f})"
            return f"D10 (>={cuts[-1]:.2f})"
        d["by_z_decile"] = _agg(T, zdec, "décile z")

        print("\n4) par décile de z — PAR STRATÉGIE (le z n'est pas comparable)")
        d["by_strat_z"] = {}
        for s in ("S1", "S5", "S8", "S9", "S10"):
            sub = [t for t in T if t["strat"] == s]
            if len(sub) < 40:
                print(f"  {s}: n={len(sub)} — trop peu, ignoré")
                continue
            szs = sorted(t["entry_z"] for t in sub)
            scuts = [szs[int(len(szs) * q / 4)] for q in range(1, 4)]

            def zq(t, _c=scuts):
                z = t["entry_z"]
                for i, c in enumerate(_c):
                    if z < c:
                        return f"Q{i+1}"
                return "Q4"
            print(f"  — {s} (n={len(sub)}, z de {szs[0]:.2f} à {szs[-1]:.2f})")
            d["by_strat_z"][s] = _agg(sub, zq, f"{s} quartile z", min_n=5)

        print("\n5) scans SATURÉS uniquement (≥3 candidats) — là où un plancher mord")
        sat = [t for t in T if (t.get("n_cands_at_open") or 0) >= 3]
        print(f"  n={len(sat)} / {len(T)}")
        d["saturated_by_rank"] = _agg(sat, lambda t: min(t["entry_rank_all"], 6),
                                      "rang (saturés)")

        dump["windows"][w] = d

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "entry_quality_floor_eda.json")
    with open(path, "w") as f:
        json.dump(dump, f, indent=1, default=str)
    print(f"\nDump : {path}")
    print("\nLECTURE : la prémisse tient si net_bps DÉCROÎT avec le rang et")
    print("          CROÎT avec le z, de façon cohérente sur les 3 fenêtres.")
    print("          Un effet visible sur une seule fenêtre = régime-local, STOP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
