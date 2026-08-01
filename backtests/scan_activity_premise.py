"""PHASE A0 — re-vérification de la prémisse « agitation du scan » sur base propre.

Descriptif pur. Aucun modulateur n'est simulé, aucun paramètre n'est ajusté.

LA PRÉMISSE À VÉRIFIER, telle que publiée en juillet 2026
(`backtests/backtest_scan_activity_gate.py`, EDA `eda_entry_quality_floor2.py`) :

    candidats au scan       1        2        3        4       5+
    net moyen 28m        +35      +99      -38     +185     +415
    net moyen 12m        +40     +129      -34     +135     +695
    net moyen  6m        -20     +110     -246      +60     +896

Sens de l'effet : **le net CROÎT avec le nombre de candidats**.

Ces chiffres portent deux défauts connus, tous deux corrigés depuis :
  - ils ont été calculés sur la carte sectorielle périmée (avant v1.17.1),
    donc sur une population de trades amputée de 8 tokens ;
  - les fenêtres 28m/12m/6m sont EMBOÎTÉES, pas glissantes — la fenêtre courte
    est incluse dans la longue, donc les « trois fenêtres » n'en font qu'une.

D'où cette re-vérification, sur base propre et sur les fenêtres de doctrine.

═══ TEST DE SIGNE, FIXÉ AVANT EXÉCUTION ═══
Une fenêtre présente « un effet de même sens que l'EDA de juillet » si LES DEUX
mesures suivantes sont strictement positives :

  1. Spearman(n_cands, net) sur les trades de la fenêtre — mesure continue,
     insensible au découpage en buckets ;
  2. net moyen(bucket ≥5) − net moyen(bucket 1) — la comparaison extrême, qui
     est celle sur laquelle la prémisse de juillet avait été lue.

Exiger les deux évite qu'un artefact de bucketisation ou une queue isolée porte
seule le verdict. Aucune des deux n'est ajustable après coup.

Livrable : docs/scan_activity_premise.md · analysis/output/scan_activity_premise.json

Usage : python3 -m backtests.scan_activity_premise
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br  # noqa: E402
from backtests.backtest_rolling import (  # noqa: E402
    run_window, load_oi, load_funding, load_dxy)
from backtests.backtest_genetic import load_3y_candles, build_features  # noqa: E402
from backtests.backtest_sector import compute_sector_features  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import (  # noqa: E402
    MeasureError, require_column, require_series)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_CAP = 1000.0
OFFSETS = [0, 6, 12, 18]        # fenêtres de doctrine, glissantes non chevauchantes
WINDOW_MONTHS = 28
MIN_CELL = 20                   # sous ce seuil : cellule NON ÉMISE

BUCKETS = [("1", 1, 1), ("2", 2, 2), ("3-4", 3, 4), ("≥5", 5, 10**9)]

# Fenêtres de creux FIGÉES — docs/dd_anatomy.md.
TROUGHS = [
    ("A", "2024-08-03", "2024-11-06", "drawdown maximal"),
    ("B", "2024-09-22", "2024-10-21", "pire 30 j"),
    ("C", "2024-08-03", "2024-09-01", "2ᵉ pire 30 j"),
    ("D", "2025-07-21", "2025-08-19", "3ᵉ pire 30 j"),
]


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (da * db)


def _spearman(a, b):
    return _pearson(_rank(a), _rank(b))


def cell(ts: list[dict]) -> dict:
    if len(ts) < MIN_CELL:
        return {"n": len(ts), "emitted": False}
    nets = [t["net"] for t in ts]
    return {"n": len(ts), "emitted": True,
            "net_mean": round(statistics.fmean(nets), 1),
            "se": round(statistics.stdev(nets) / math.sqrt(len(nets)), 1),
            "wr": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1),
            "pnl": round(sum(t["pnl"] for t in ts), 2)}


def profile(trades: list[dict], label: str) -> dict:
    """Buckets + les deux mesures du test de signe."""
    cells = {}
    for name, lo, hi in BUCKETS:
        cells[name] = cell([t for t in trades if lo <= t["nc"] <= hi])
    ncs = [t["nc"] for t in trades]
    nets = [t["net"] for t in trades]
    rho = _spearman(ncs, nets) if len(trades) >= MIN_CELL else None
    hi_c, lo_c = cells["≥5"], cells["1"]
    diff = (hi_c["net_mean"] - lo_c["net_mean"]
            if hi_c["emitted"] and lo_c["emitted"] else None)
    same_sense = (rho is not None and rho > 0 and diff is not None and diff > 0)
    return {"label": label, "n": len(trades), "cells": cells,
            "spearman": round(rho, 4) if rho is not None else None,
            "diff_hi_lo": round(diff, 1) if diff is not None else None,
            "same_sense": same_sense,
            "nc_distribution": {name: sum(1 for t in trades if lo <= t["nc"] <= hi)
                                for name, lo, hi in BUCKETS}}


def _prep(raw: list[dict]) -> list[dict]:
    require_column(raw, "net", label="net_bps")
    require_column(raw, "n_cands_at_open", label="agitation du scan")
    out = [{"net": t["net"], "pnl": t["pnl"], "strat": t["strat"],
            "entry_t": t["entry_t"], "nc": t["n_cands_at_open"]}
           for t in raw if t.get("n_cands_at_open") is not None]
    require_series("n_cands_at_open", [t["nc"] for t in out], min_n=50)
    return out


def _show(p: dict) -> None:
    cells = []
    for name, _, _ in BUCKETS:
        c = p["cells"][name]
        cells.append(f"{c['net_mean']:>+8.0f}({c['n']:>3d})" if c["emitted"]
                     else f"{'NON ÉMISE':>13s}")
    sp = f"{p['spearman']:+.4f}" if p["spearman"] is not None else "—"
    df = f"{p['diff_hi_lo']:+.0f}" if p["diff_hi_lo"] is not None else "—"
    print(f"  {p['label']:<26s} n={p['n']:>4d}  " + " ".join(cells)
          + f"   ρ={sp:>8s}  Δ(≥5−1)={df:>7s}  "
          + ("MÊME SENS" if p["same_sense"] else "non"))


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Chargement des données…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    br._P = P

    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    print(banner(P, data, extra={"étude": "prémisse agitation du scan (A0)",
                                 "buckets": [b[0] for b in BUCKETS],
                                 "plancher_cellule": MIN_CELL}), flush=True)

    def run(s_dt, e_dt):
        return run_window(features, data, sectors, dxy,
                          start_ts_ms=int(s_dt.timestamp() * 1000),
                          end_ts_ms=int(e_dt.timestamp() * 1000),
                          start_capital=START_CAP, oi_data=oi, funding_data=funding,
                          apply_adaptive_modulator=True, aligned=True,
                          margin_check=True, mfe_on_close=True,
                          realistic_trail_booking=True)

    res = {"buckets": [b[0] for b in BUCKETS], "min_cell": MIN_CELL,
           "july_premise": {"sense": "net croissant avec n_cands",
                            "source": "backtest_scan_activity_gate.py",
                            "caveats": ["carte sectorielle périmée",
                                        "fenêtres emboîtées, non glissantes"]}}

    # ── les 4 fenêtres de doctrine, exécutées INDÉPENDAMMENT ─────────
    print("\n═══ FENÊTRES DE DOCTRINE (glissantes, non chevauchantes) ═══")
    wins = []
    for off in OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        r = run(s, e)
        p = profile(_prep(r["trades"]), f"OOS-{off} {s:%Y-%m}→{e:%Y-%m}")
        p["end_capital"] = round(r["end_capital"], 2)
        p["offset"] = off
        wins.append(p)
        _show(p)
    res["doctrine_windows"] = wins
    n_same = sum(1 for w in wins if w["same_sense"])
    res["n_same_sense"] = n_same
    res["premise_confirmed"] = n_same >= 3
    print(f"\n  → effet de même sens sur {n_same}/4 fenêtres  ⇒  prémisse "
          f"{'CONFIRMÉE' if n_same >= 3 else 'NON CONFIRMÉE'}")

    # ── référence longue + fenêtres de creux ─────────────────────────
    print("\n═══ RÉFÉRENCE 28 MOIS ET FENÊTRES DE CREUX ═══")
    long_r = run(end_dt - relativedelta(months=WINDOW_MONTHS), end_dt)
    lt = _prep(long_r["trades"])
    ref = profile(lt, f"28 mois (référence)")
    _show(ref)
    res["reference_28m"] = ref

    troughs = []
    for name, s, e, why in TROUGHS:
        sub = [t for t in lt if s <= _day(t["entry_t"]) <= e]
        p = profile(sub, f"creux {name} ({why})")
        p["dates"] = f"{s}→{e}"
        troughs.append(p)
        _show(p)
    res["troughs"] = troughs

    # distribution de l'agitation — combien de trades chaque bucket porte
    print("\n  répartition des trades par bucket (28 mois) :")
    tot = len(lt)
    for name, lo, hi in BUCKETS:
        k = sum(1 for t in lt if lo <= t["nc"] <= hi)
        print(f"    {name:>4s} : {k:>4d} trades ({k/tot*100:>5.1f} %)")

    res["fingerprint"] = fingerprint(P, data, extra={"étude": "scan_activity_premise"})
    out = os.path.join(ROOT, "analysis", "output", "scan_activity_premise.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
