"""CONDITIONNEMENT À LA DISPERSION — diagnostic pur, zéro fit.

Question : le P&L d'Alfred dépend-il du régime de dispersion cross-sectionnelle
à l'heure d'entrée ?

Double usage assumé — dit ici en toutes lettres comme pour le spectre de
persistance : au-delà du diagnostic, cette étude est une **entrée de conception**
pour la Mission 2. Si la dispersion s'avère être une jauge, le choix de la
variable de conception aura été informé par l'historique : c'est un **méta-fit
assumé**, borné par le walk-forward que devra passer toute règle issue de M2.

Les trois mesures :
 1. net moyen (bps) par tercile de dispersion à l'entrée — bornes AFFICHÉES,
    par signal, par semestre, et creux vs référence séparément ;
 2. WR et effectif par tercile (un tercile peut sembler bon parce qu'on n'y
    trade presque pas — l'afficher plutôt que de le laisser deviner) ;
 3. corrélation dispersion ↔ `n_cands_at_open` : une seule information, ou deux ?

Livrable : docs/dispersion_conditioning.md · analysis/output/dispersion_conditioning.json

Usage : python3 -m backtests.dispersion_conditioning
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from collections import defaultdict
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
WINDOW_MONTHS = 28

# Fenêtres de creux FIGÉES — docs/dd_anatomy.md.
TROUGHS = [("A", "2024-08-03", "2024-11-06"), ("D", "2025-07-21", "2025-08-19")]

MIN_CELL = 20        # plancher d'effectif par cellule ; sous ce seuil : NON ÉMISE
CORR_THRESHOLD = 0.70   # seuil de la grille pour « une seule information »


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _semester(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return f"{d.year}-S{1 if d.month <= 6 else 2}"


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


def _spearman(a, b):
    return _pearson(_rank(a), _rank(b))


def cell(trades: list[dict]) -> dict | None:
    """Statistiques d'une cellule — None si sous le plancher d'effectif."""
    if len(trades) < MIN_CELL:
        return {"n": len(trades), "emitted": False}
    nets = [t["net"] for t in trades]
    return {"n": len(trades), "emitted": True,
            "net_mean": round(statistics.fmean(nets), 1),
            "net_median": round(statistics.median(nets), 1),
            "se": round(statistics.stdev(nets) / math.sqrt(len(nets)), 1),
            "wr": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1),
            "pnl": round(sum(t["pnl"] for t in trades), 2)}


def terciles(values: list[float]) -> tuple[float, float]:
    s = sorted(values)
    return s[len(s) // 3], s[2 * len(s) // 3]


def bucket(v: float, lo: float, hi: float) -> int:
    return 0 if v <= lo else (1 if v <= hi else 2)


def monotone(vals: list[float | None]) -> int:
    """+1 croissant strict, −1 décroissant strict, 0 sinon/incomplet."""
    if any(v is None for v in vals) or len(vals) != 3:
        return 0
    if vals[0] < vals[1] < vals[2]:
        return 1
    if vals[0] > vals[1] > vals[2]:
        return -1
    return 0


def analyse(trades: list[dict], key: str) -> dict:
    """Toute l'étude pour une variable de dispersion donnée."""
    vals = [t["disp"][key] for t in trades]
    require_series(f"dispersion {key} à l'entrée", vals, min_n=200)
    lo, hi = terciles(vals)
    for t in trades:
        t["_b"] = bucket(t["disp"][key], lo, hi)

    out = {"bounds": {"lo": lo, "hi": hi},
           "range": {"min": min(vals), "max": max(vals),
                     "median": statistics.median(vals)}}

    by_t = defaultdict(list)
    for t in trades:
        by_t[t["_b"]].append(t)
    out["global"] = {str(b): cell(by_t.get(b, [])) for b in range(3)}
    g = [out["global"][str(b)].get("net_mean") for b in range(3)]
    out["global_monotone"] = monotone(g)

    # par signal
    out["by_strat"] = {}
    for s in sorted({t["strat"] for t in trades}):
        sub = [t for t in trades if t["strat"] == s]
        c = {str(b): cell([t for t in sub if t["_b"] == b]) for b in range(3)}
        out["by_strat"][s] = {"cells": c,
                              "monotone": monotone([c[str(b)].get("net_mean")
                                                    for b in range(3)])}

    # par semestre — c'est ici que se joue la clause de stabilité
    out["by_semester"] = {}
    for sem in sorted({_semester(t["entry_t"]) for t in trades}):
        sub = [t for t in trades if _semester(t["entry_t"]) == sem]
        c = {str(b): cell([t for t in sub if t["_b"] == b]) for b in range(3)}
        out["by_semester"][sem] = {
            "n": len(sub), "cells": c,
            "monotone": monotone([c[str(b)].get("net_mean") for b in range(3)])}

    # creux vs référence
    def in_trough(t):
        d = _day(t["entry_t"])
        return any(a <= d <= b for _, a, b in TROUGHS)
    for lab, sel in (("creux", in_trough), ("reference", lambda t: not in_trough(t))):
        sub = [t for t in trades if sel(t)]
        c = {str(b): cell([t for t in sub if t["_b"] == b]) for b in range(3)}
        out.setdefault("regimes", {})[lab] = {
            "n": len(sub), "cells": c,
            "monotone": monotone([c[str(b)].get("net_mean") for b in range(3)])}

    # corrélations continues (complément à la lecture par tercile)
    nets = [t["net"] for t in trades]
    out["corr_disp_net"] = {
        "pearson": round(_pearson(vals, nets), 4),
        "spearman": round(_spearman(vals, nets), 4), "n": len(vals)}
    return out


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
    start_dt = end_dt - relativedelta(months=WINDOW_MONTHS)
    print(banner(P, data, extra={"étude": "conditionnement dispersion",
                                 "fenêtre": f"{start_dt:%Y-%m-%d}→{end_dt:%Y-%m-%d}",
                                 "plancher_cellule": MIN_CELL}), flush=True)

    r = run_window(features, data, sectors, dxy,
                   start_ts_ms=int(start_dt.timestamp() * 1000), end_ts_ms=end_ms,
                   start_capital=START_CAP, oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True,
                   margin_check=True, mfe_on_close=True,
                   realistic_trail_booking=True)
    print(f"→ {r['n_trades']} trades, fin ${r['end_capital']:,.0f}", flush=True)

    raw = r["trades"]
    require_column(raw, "net", label="net_bps des trades")
    require_column(raw, "entry_feats", label="features d'entrée")

    trades = []
    n_missing_disp = n_missing_cands = 0
    for t in raw:
        ef = t.get("entry_feats") or {}
        d24, d7 = ef.get("entry_disp_24h"), ef.get("entry_disp_7d")
        if d24 is None or d7 is None:
            n_missing_disp += 1
            continue
        if t.get("n_cands_at_open") is None:
            n_missing_cands += 1
        trades.append({"net": t["net"], "pnl": t["pnl"], "strat": t["strat"],
                       "coin": t["coin"], "dir": t["dir"],
                       "entry_t": t["entry_t"], "exit_t": t["exit_t"],
                       "n_cands": t.get("n_cands_at_open"),
                       "disp": {"disp_24h": float(d24), "disp_7d": float(d7)}})
    print(f"  exploitables : {len(trades)} "
          f"(sans dispersion : {n_missing_disp}, sans n_cands : {n_missing_cands})",
          flush=True)
    if not trades:
        raise MeasureError("aucun trade ne porte de dispersion d'entrée")

    res = {"window": {"start": start_dt.isoformat()[:10],
                      "end": end_dt.isoformat()[:10], "months": WINDOW_MONTHS},
           "n_trades": len(trades), "min_cell": MIN_CELL,
           "n_missing_disp": n_missing_disp, "n_missing_cands": n_missing_cands}

    for key in ("disp_24h", "disp_7d"):
        print(f"\n{'='*72}\n### {key}")
        a = analyse(trades, key)
        res[key] = a
        b = a["bounds"]
        print(f"  bornes de tercile : T1 ≤ {b['lo']:.0f} < T2 ≤ {b['hi']:.0f} < T3"
              f"   (min {a['range']['min']:.0f}, médiane "
              f"{a['range']['median']:.0f}, max {a['range']['max']:.0f})")
        print(f"  {'tercile':>8s} {'n':>5s} {'net moy':>9s} {'± se':>7s} "
              f"{'WR':>6s} {'P&L $':>10s}")
        for i in range(3):
            c = a["global"][str(i)]
            if not c["emitted"]:
                print(f"  {'T'+str(i+1):>8s} {c['n']:>5d}   NON ÉMISE (< {MIN_CELL})")
            else:
                print(f"  {'T'+str(i+1):>8s} {c['n']:>5d} {c['net_mean']:>+9.1f} "
                      f"{c['se']:>7.1f} {c['wr']:>5.1f}% {c['pnl']:>+10.0f}")
        print(f"  monotonie globale : {a['global_monotone']:+d}   "
              f"corr(disp, net) Pearson {a['corr_disp_net']['pearson']:+.4f} "
              f"Spearman {a['corr_disp_net']['spearman']:+.4f}")

        print("  par semestre (net moyen par tercile, monotonie) :")
        for sem, v in a["by_semester"].items():
            cells = []
            for i in range(3):
                c = v["cells"][str(i)]
                cells.append(f"{c['net_mean']:>+8.1f}({c['n']:>3d})"
                             if c["emitted"] else f"{'NON ÉMISE':>13s}")
            print(f"    {sem}  n={v['n']:>4d}  " + " ".join(cells)
                  + f"   mono {v['monotone']:+d}")

        print("  par signal :")
        for s, v in a["by_strat"].items():
            cells = []
            for i in range(3):
                c = v["cells"][str(i)]
                cells.append(f"{c['net_mean']:>+8.1f}({c['n']:>3d})"
                             if c["emitted"] else f"{'NON ÉMISE':>13s}")
            print(f"    {s:4s} " + " ".join(cells) + f"   mono {v['monotone']:+d}")

        print("  creux vs référence :")
        for lab, v in a["regimes"].items():
            cells = []
            for i in range(3):
                c = v["cells"][str(i)]
                cells.append(f"{c['net_mean']:>+8.1f}({c['n']:>3d})"
                             if c["emitted"] else f"{'NON ÉMISE':>13s}")
            print(f"    {lab:10s} n={v['n']:>4d}  " + " ".join(cells)
                  + f"   mono {v['monotone']:+d}")

    # ── mesure 3 : dispersion vs agitation du scan ────────────────────
    print(f"\n{'='*72}\n### dispersion ↔ agitation du scan (n_cands_at_open)")
    with_c = [t for t in trades if t["n_cands"] is not None]
    require_series("n_cands_at_open", [t["n_cands"] for t in with_c], min_n=200)

    # Au niveau du SCAN : les deux variables sont des propriétés du scan, pas
    # du trade. Les compter par trade sur-pondère les scans qui ont ouvert
    # plusieurs positions.
    per_scan = {}
    for t in with_c:
        per_scan[t["entry_t"]] = (t["disp"]["disp_24h"], t["disp"]["disp_7d"],
                                  t["n_cands"])
    scans = list(per_scan.values())
    m3 = {"n_scans": len(scans), "n_trades": len(with_c)}
    for i, key in ((0, "disp_24h"), (1, "disp_7d")):
        m3[key] = {
            "par_scan": {
                "pearson": round(_pearson([s[i] for s in scans],
                                          [s[2] for s in scans]), 4),
                "spearman": round(_spearman([s[i] for s in scans],
                                            [s[2] for s in scans]), 4)},
            "par_trade": {
                "pearson": round(_pearson([t["disp"][key] for t in with_c],
                                          [t["n_cands"] for t in with_c]), 4),
                "spearman": round(_spearman([t["disp"][key] for t in with_c],
                                            [t["n_cands"] for t in with_c]), 4)}}
        # par régime
        for lab, sel in (("creux", True), ("reference", False)):
            sub = [t for t in with_c
                   if (any(a <= _day(t["entry_t"]) <= b for _, a, b in TROUGHS))
                   == sel]
            ps = {}
            for t in sub:
                ps[t["entry_t"]] = (t["disp"][key], t["n_cands"])
            v = list(ps.values())
            m3[key][lab] = ({"n_scans": len(v),
                             "pearson": round(_pearson([x[0] for x in v],
                                                       [x[1] for x in v]), 4)}
                            if len(v) >= MIN_CELL else
                            {"n_scans": len(v), "emitted": False})
    res["disp_vs_agitation"] = m3
    print(f"  {len(scans)} scans distincts ({len(with_c)} trades)")
    for key in ("disp_24h", "disp_7d"):
        v = m3[key]
        print(f"  {key}  par scan  Pearson {v['par_scan']['pearson']:+.4f}  "
              f"Spearman {v['par_scan']['spearman']:+.4f}   |   "
              f"par trade Pearson {v['par_trade']['pearson']:+.4f}")
        for lab in ("creux", "reference"):
            c = v[lab]
            print(f"     {lab:10s} " + (f"n={c['n_scans']:>4d} scans  "
                  f"Pearson {c['pearson']:+.4f}" if c.get("pearson") is not None
                  else f"n={c['n_scans']} — NON ÉMISE"))

    res["fingerprint"] = fingerprint(P, data, extra={"étude": "dispersion_conditioning"})
    out = os.path.join(ROOT, "analysis", "output", "dispersion_conditioning.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
