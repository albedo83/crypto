"""CLÔTURE DE BRANCHE — portage PERMANENT, la mesure qui tranche.

Contrairement à la clause d'épisodes de la Phase 1 — jugée mal formée, parce
qu'elle sélectionne les épisodes après coup — cette mesure n'a **aucun degré de
liberté ex-post** :

  - une seule entrée, une seule sortie, sur toute la fenêtre d'intersection ;
  - un **seul aller-retour** de frais, pas un par épisode ;
  - une direction **fixée a priori** : short HL perp / long venue B perp,
    choisie sur le fait structurel que le funding Hyperliquid est
    persistamment positif (78,7 % d'échantillons positifs, +1,455 bps/jour —
    `docs/mm_phase0_economics.md` § 3), et non sur le signe observé du résultat.

Un token dont le différentiel cumulé est négatif apparaît donc en perte : c'est
voulu, c'est le prix de ne pas choisir la direction après coup.

Rendement sur CAPITAL : la structure immobilise de la marge sur **deux** venues.
Deux hypothèses sont rendues, la favorable et la conservatrice, pour que le
verdict ne dépende pas de l'hypothèse retenue.

Usage : python3 -m backtests.basis.permanent_carry
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.basis.phase1_analysis import (  # noqa: E402
    hl_hourly, derive_intervals, build_diff, RT_BPS, CACHE, HL_FUNDING,
    TROUGHS, _d)
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENUE = "binance"

# ── hypothèses de capital, déclarées avant calcul ───────────────────────
# « notionnel/2 pour les deux venues, marges comprises » = 0,5 × notionnel de
# capital pour porter 1 × notionnel de chaque côté (levier effectif 4× par
# jambe). C'est l'hypothèse FAVORABLE. L'hypothèse CONSERVATRICE aligne la
# structure sur le levier d'Alfred (2× par jambe) : capital = 1 × notionnel.
CAPITAL_FRACTIONS = {"favorable (notionnel/2)": 0.5,
                     "conservatrice (notionnel×1)": 1.0}

# ── double benchmark, les deux régimes ──────────────────────────────────
HLP_12M_PCT = 16.51
HLP_9M_ANNUALISED_PCT = 0.13 * (12 / 9)     # +0,13 % sur 9 mois, annualisé


def main() -> int:
    inv = json.load(open(os.path.join(ROOT, "analysis", "output",
                                      "basis_phase0.json")))
    universe = [s for s in inv["universe"] if inv["usable"][s]["both"]]
    data = load_3y_candles()
    print(banner(P, data, extra={"étude": "portage permanent — clôture",
                                 "venue": VENUE, "AR": RT_BPS[VENUE]}),
          flush=True)
    print(f"  direction FIXÉE a priori : short HL perp / long {VENUE} perp")
    print(f"  un seul aller-retour de {RT_BPS[VENUE]:.0f} bps sur toute la fenêtre\n")

    ch = sqlite3.connect(HL_FUNDING)
    cb = sqlite3.connect(CACHE)
    rows, trough_rows = [], {t[0]: [] for t in TROUGHS}

    print(f"  {'token':7s} {'fenêtre':>23s} {'ans':>5s} {'cumulé':>10s} "
          f"{'net':>10s} {'%/an notionnel':>15s}")
    for s in universe:
        hl_ts, hl_r = hl_hourly(ch, s)
        r = cb.execute("SELECT ts, rate FROM funding_b WHERE venue=? AND "
                       "symbol=? ORDER BY ts", (VENUE, s)).fetchall()
        if len(r) < 100:
            continue
        b_ts = [x[0] for x in r]
        b_rate = [x[1] for x in r]
        iv, _ = derive_intervals(b_ts)
        d = build_diff(hl_ts, hl_r, b_ts, b_rate, iv)
        pts = d["points"]
        if not pts:
            continue
        require_series(f"diff {s}", [p["diff"] for p in pts], min_n=100)

        cum = sum(p["diff"] * p["iv_h"] for p in pts)
        hours = sum(p["iv_h"] for p in pts)
        years = hours / 8766.0
        net = cum - RT_BPS[VENUE]
        pct_notional_yr = net / 100.0 / years
        rows.append({"token": s, "window": d["window"], "years": round(years, 2),
                     "cum_bps": round(cum, 1), "net_bps": round(net, 1),
                     "pct_notional_per_year": round(pct_notional_yr, 3)})
        print(f"  {s:7s} {d['window'][0] + '→' + d['window'][1]:>23s} "
              f"{years:>5.2f} {cum:>+10.1f} {net:>+10.1f} "
              f"{pct_notional_yr:>+14.3f}%")

        # portage pendant les creux — un aller-retour par creux
        for name, t0, t1 in TROUGHS:
            sub = [p for p in pts if t0 <= _d(p["ts"]) <= t1]
            if len(sub) < 10:
                continue
            c = sum(p["diff"] * p["iv_h"] for p in sub)
            h = sum(p["iv_h"] for p in sub)
            trough_rows[name].append({
                "token": s, "cum_bps": round(c, 1),
                "net_bps": round(c - RT_BPS[VENUE], 1),
                "pct_notional_per_year": round(
                    (c - RT_BPS[VENUE]) / 100.0 / (h / 8766.0), 3)})

    require_series("rendements par token",
                   [x["pct_notional_per_year"] for x in rows], min_n=10)
    med_not = statistics.median(x["pct_notional_per_year"] for x in rows)
    n_pos = sum(1 for x in rows if x["net_bps"] > 0)

    print(f"\n  médiane : {med_not:+.3f} %/an de notionnel · "
          f"tokens à net > 0 : {n_pos}/{len(rows)}")

    print("\n═══ RENDEMENT SUR CAPITAL — deux hypothèses ═══")
    on_capital = {}
    for label, frac in CAPITAL_FRACTIONS.items():
        vals = [x["pct_notional_per_year"] / frac for x in rows]
        on_capital[label] = {
            "capital_fraction": frac,
            "median_pct_per_year": round(statistics.median(vals), 3),
            "best": round(max(vals), 3), "worst": round(min(vals), 3),
            "n_positive": sum(1 for v in vals if v > 0)}
        v = on_capital[label]
        print(f"  {label:30s} médiane {v['median_pct_per_year']:>+8.3f} %/an  "
              f"(min {v['worst']:+.2f} · max {v['best']:+.2f} · "
              f"positifs {v['n_positive']}/{len(vals)})")

    print("\n═══ PENDANT LES CREUX D'ALFRED ═══")
    tr = {}
    for name, t0, t1 in TROUGHS:
        v = trough_rows[name]
        if not v:
            tr[name] = {"emitted": False}
            continue
        m = statistics.median(x["pct_notional_per_year"] for x in v)
        tr[name] = {"emitted": True, "n_tokens": len(v),
                    "median_pct_notional_per_year": round(m, 3),
                    "median_net_bps": round(statistics.median(
                        x["net_bps"] for x in v), 1),
                    "n_positive": sum(1 for x in v if x["net_bps"] > 0)}
        print(f"  creux {name} {t0}→{t1}  n={len(v):2d}  "
              f"médiane {m:>+9.3f} %/an de notionnel  "
              f"net médian {tr[name]['median_net_bps']:>+8.1f} bps  "
              f"tokens positifs {tr[name]['n_positive']}/{len(v)}")

    res = {"venue": VENUE, "rt_bps": RT_BPS[VENUE], "tokens": rows,
           "median_pct_notional_per_year": round(med_not, 3),
           "n_positive": n_pos, "n_tokens": len(rows),
           "on_capital": on_capital, "troughs": tr,
           "benchmarks": {"hlp_12m_pct": HLP_12M_PCT,
                          "hlp_9m_annualised_pct": round(HLP_9M_ANNUALISED_PCT, 3)},
           "fingerprint": fingerprint(P, data, extra={"étude": "portage_permanent"})}
    out = os.path.join(ROOT, "analysis", "output", "basis_permanent_carry.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
