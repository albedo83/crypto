"""Impact du correctif de parité des secteurs — comparatif AVANT / APRÈS.

Contexte (2026-07-30) : `backtests/backtest_sector.py` portait une carte des
secteurs codée en dur et figée depuis v11.0.0 — 5 secteurs au lieu de 7, MKR
fantôme, et 8 tokens tradés en production sans secteur (ADA, BCH, DOT, ENA, GMX,
TON, UNI, XMR). Comme `compute_sector_features` est l'UNIQUE source de la
divergence que consomme S5, ces tokens ne pouvaient émettre aucun signal S5 en
backtest, alors que le live en tradait : 7 des 19 trades S5 depuis le reset.

Le correctif dérive les secteurs des Params de production. Ce script mesure
exactement ce que ça déplace, en rejouant les deux cartes dans le même process.

Contrôles attendus :
  - l'arm AVANT doit reproduire les chiffres publiés (28m $13553, 12m $2271,
    6m $800, 3m $562) — sinon le comparatif ne vaut rien ;
  - l'arm APRÈS doit faire apparaître des trades S5 sur les tokens orphelins
    (sauf XMR : secteur Privacy à 1 membre, donc 0 pair — la règle « ≥ 2 pairs »
    existe des DEUX côtés, cf. alfred/features.py:363, c'est le comportement
    correct et non un reste de bug).

Usage : python3 -m backtests.backtest_sector_parity_impact
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
import backtests.backtest_sector as bs
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
START_CAP = 500.0
# Contrôle de non-régression de l'arm AVANT. Ces valeurs ont été obtenues en
# exécutant le module ORIGINAL (avant correctif, via git stash) sur le jeu de
# données du 2026-07-30 16:10 UTC. Elles sont donc DÉPENDANTES DES DONNÉES : les
# fichiers de bougies sont rafraîchis toutes les 4h (cron :10), et les fenêtres
# glissent avec la dernière bougie. Un écart ici après un refresh de données
# n'est pas un bug — le vrai contrôle est le design à deux arms dans le MÊME
# process sur les MÊMES données.
# (Pour mémoire, docs/backtests.md du 2026-07-25 annonçait 13553/2271/800/562 :
#  l'écart avec les valeurs ci-dessous est entièrement dû au refresh de données.)
REFERENCE = {"28m": 13209, "12m": 2180, "6m": 872, "3m": 652}

# La carte périmée, telle qu'elle était avant le correctif (v11.0.0).
LEGACY_SECTORS = {
    "L1":     ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":   ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO"],
    "Gaming": ["GALA", "IMX", "SAND"],
    "Infra":  ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":   ["DOGE", "WLD", "BLUR", "MINA"],
}
ORPHANS = ["ADA", "BCH", "DOT", "ENA", "GMX", "TON", "UNI", "XMR"]


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS

    def run(label, sectors_map):
        """Recalcule les features sectorielles pour CETTE carte, puis rejoue."""
        bs.SECTORS = {k: list(v) for k, v in sectors_map.items()}
        bs.TOKEN_SECTOR = {t: s for s, ts in sectors_map.items() for t in ts}
        sectors = bs.compute_sector_features(features, data)
        out, t0 = {}, time.time()
        for w, months in WINDOWS:
            s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=end_ms,
                           start_capital=START_CAP, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            per = defaultdict(lambda: [0, 0.0, 0])
            s5_coins = Counter()
            for t in r["trades"]:
                k = per[t["strat"]]
                k[0] += 1
                k[1] += t.get("pnl", 0.0)
                k[2] += 1 if t.get("pnl", 0) > 0 else 0
                if t["strat"] == "S5":
                    s5_coins[t["coin"]] += 1
            out[w] = {
                "end": r["end_capital"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                "per_strat": {k: {"n": v[0], "pnl": round(v[1], 2),
                                  "wr": round(v[2] / v[0] * 100, 1)}
                              for k, v in per.items()},
                "s5_coins": dict(s5_coins),
            }
        print(f"  {label:24s} " + " ".join(
            f"{w}=${out[w]['end']:>8.0f}" for w, _ in WINDOWS)
            + f"   [{time.time()-t0:.0f}s]", flush=True)
        return out

    print("\n=== deux arms, même process, features sectorielles recalculées à chaque fois")
    before = run("AVANT (carte périmée)", LEGACY_SECTORS)
    after = run("APRÈS (Params prod)", DEFAULT_PARAMS.sectors)

    print("\n--- contrôle de non-régression de l'arm AVANT")
    ok = True
    for w, _ in WINDOWS:
        got, exp = before[w]["end"], REFERENCE[w]
        d = abs(got - exp) / exp * 100
        flag = "✓" if d < 1.0 else "✗ ÉCART"
        if d >= 1.0:
            ok = False
        print(f"  {w:4s} attendu ${exp:>7d}  obtenu ${got:>8.0f}  ({d:.2f}%) {flag}")
    if not ok:
        print("  ⚠ l'arm AVANT ne reproduit pas les chiffres publiés — comparatif "
              "non fiable, ne pas conclure")

    print("\n--- capital final et drawdown")
    print(f"  {'fenêtre':9s}{'AVANT':>12s}{'APRÈS':>12s}{'Δ$':>11s}{'Δ%':>9s}"
          f"{'DD av':>9s}{'DD ap':>9s}")
    for w, _ in WINDOWS:
        b, a = before[w], after[w]
        d = a["end"] - b["end"]
        print(f"  {w:9s}{b['end']:>12.0f}{a['end']:>12.0f}{d:>+11.0f}"
              f"{d/b['end']*100:>+8.1f}%{b['dd']:>8.1f}%{a['dd']:>8.1f}%")

    print("\n--- par signal (n / WR / P&L)")
    for w, _ in WINDOWS:
        print(f"\n  {w} :")
        print(f"    {'strat':6s}{'n av':>7s}{'n ap':>7s}{'WR av':>8s}{'WR ap':>8s}"
              f"{'P&L av':>11s}{'P&L ap':>11s}")
        for s in ("S1", "S5", "S8", "S9", "S10"):
            b = before[w]["per_strat"].get(s, {"n": 0, "wr": 0, "pnl": 0})
            a = after[w]["per_strat"].get(s, {"n": 0, "wr": 0, "pnl": 0})
            print(f"    {s:6s}{b['n']:>7d}{a['n']:>7d}{b['wr']:>7.1f}%{a['wr']:>7.1f}%"
                  f"{b['pnl']:>+11.0f}{a['pnl']:>+11.0f}")

    print("\n--- contrôle : les tokens orphelins tradent-ils S5 maintenant ?")
    b28, a28 = before["28m"]["s5_coins"], after["28m"]["s5_coins"]
    for t in ORPHANS:
        note = ""
        if t == "XMR":
            note = "  (attendu 0 : secteur Privacy à 1 membre → 0 pair)"
        print(f"  {t:5s} avant={b28.get(t, 0):>4d}  après={a28.get(t, 0):>4d}{note}")
    nouveaux = sum(a28.get(t, 0) for t in ORPHANS)
    print(f"  → {nouveaux} trades S5 sur les tokens auparavant invisibles (28m)")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "sector_parity_impact.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "reference": REFERENCE, "before": before, "after": after},
                  f, indent=1, default=str)
    print(f"\nDump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
