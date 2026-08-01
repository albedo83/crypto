"""SPECTRE DE PERSISTANCE — diagnostic pur, zéro fit.

Contexte (2026-08-01) : `docs/creux_anatomy.md` a réfuté l'hypothèse du chop et
mis au jour la vraie signature des creux d'Alfred — persistance au jour le jour,
annulation à l'échelle de la fenêtre. Hypothèse de travail : l'ennemi vit à
l'échelle du **swing** (quelques jours à deux semaines), entre le hold 48 h
d'Alfred et le lookback 20 j de TREND-v0.

Cette étude **localise** la persistance sur l'axe des échelles. Elle ne simule
aucune stratégie et n'ajuste aucun paramètre.

Trois mesures, sur rendements **log** journaliers reconstruits des bougies 4 h :

 1. ratio de variance VR(k) sur BTC et sur l'indice équipondéré des alts ;
 2. distribution des VR(k) token par token (la dispersion compte autant que
    l'agrégat) ;
 3. autocorrélation de rang de Spearman entre les rendements k-jours des tokens
    sur deux périodes **adjacentes non chevauchantes** — le token relativement
    fort le reste-t-il ?

Chaque cellule (fenêtre × horizon) est soumise à un **plancher d'effectif**.
Sous le plancher, la cellule n'est pas émise : une fenêtre de 30 jours ne porte
pas un horizon de 20 jours, et l'afficher comme « non mesurable » vaut mieux que
de l'estimer quand même.

Livrable : docs/persistence_spectrum.md · analysis/output/persistence_spectrum.json

Usage : python3 -m backtests.persistence_spectrum
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.backtest_trend_v0 import daily_bars  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_match, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERIOD = ("2024-04-01", "2026-08-01")

# Fenêtres FIGÉES — docs/dd_anatomy.md. Aucun redécoupage, jamais.
TROUGHS = [
    ("A", "2024-08-03", "2024-11-06", "drawdown maximal, −51,4 %"),
    ("B", "2024-09-22", "2024-10-21", "pire fenêtre 30 j, −29,5 %"),
    ("C", "2024-08-03", "2024-09-01", "2ᵉ pire 30 j, −26,2 %"),
    ("D", "2025-07-21", "2025-08-19", "3ᵉ pire 30 j, −25,2 %"),
]
EXCLUDE = [("2024-08-03", "2024-11-06"), ("2025-07-21", "2025-08-19")]

VR_HORIZONS = [1, 3, 5, 10, 20]      # VR(1) ≡ 1 par construction
XS_HORIZONS = [3, 5, 10, 20]

# ── PLANCHERS D'EFFECTIF, fixés avant exécution ─────────────────────────
# VR(k) : au moins 4 blocs indépendants de k jours dans la fenêtre.
VR_MIN_BLOCKS = 4
# Spearman cross-sectionnel : au moins 5 paires de périodes adjacentes.
XS_MIN_PAIRS = 5
# Toute mesure cross-sectionnelle : au moins 10 tokens présents.
MIN_TOKENS = 10
MIN_TOKEN_COVERAGE = 0.8


def _ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


# ── mesures ─────────────────────────────────────────────────────────────

def variance_ratio(r1: np.ndarray, k: int) -> float | None:
    """VR(k) = Var(r_k) / (k · Var(r_1)) — Lo-MacKinlay, blocs chevauchants.

    Le chevauchement biaise l'estimateur en petit échantillon ; ce biais est
    **commun** à la fenêtre et à ses fenêtres de référence de même longueur, et
    la comparaison par percentile l'absorbe. Ce qui ne s'absorbe pas, c'est un
    effectif insuffisant : d'où le plancher VR_MIN_BLOCKS.
    """
    r = r1[~np.isnan(r1)]
    n = len(r)
    if k < 1 or n < VR_MIN_BLOCKS * k or n < 10:
        return None
    if k == 1:
        return 1.0
    v1 = r.var(ddof=1)
    if v1 <= 0:
        return None
    rk = np.convolve(r, np.ones(k), mode="valid")
    if len(rk) < 3:
        return None
    return float(rk.var(ddof=1) / (k * v1))


def _rank(v: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="mergesort")
    r = np.empty(len(v), dtype=float)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return r


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def xsec_rank_autocorr(logp: np.ndarray, i0: int, i1: int, k: int) -> dict | None:
    """Spearman entre le classement des rendements k-j de t et celui de t+1.

    Périodes **adjacentes et non chevauchantes** : [t, t+k) puis [t+k, t+2k).
    """
    n = i1 - i0
    n_pairs_max = n // k - 1
    if n_pairs_max < XS_MIN_PAIRS:
        return None
    rhos = []
    for p in range(n_pairs_max):
        a0, a1 = i0 + p * k, i0 + (p + 1) * k
        b1 = i0 + (p + 2) * k
        if b1 > i1:
            break
        ra = logp[a1 - 1] - logp[a0]           # rendement log période 1
        rb = logp[b1 - 1] - logp[a1]           # rendement log période 2
        m = ~(np.isnan(ra) | np.isnan(rb))
        if m.sum() < MIN_TOKENS:
            continue
        rho = _pearson(_rank(ra[m]), _rank(rb[m]))
        if rho is not None:
            rhos.append(rho)
    if len(rhos) < XS_MIN_PAIRS:
        return None
    a = np.array(rhos)
    # Erreur-type empirique de la moyenne des ρ : sans elle, « ρ = +0,009 »
    # se lit comme un petit effet alors que c'est un zéro bruité.
    se = float(a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else float("nan")
    return {"mean": float(a.mean()), "median": float(np.median(a)),
            "n_pairs": len(a), "se": se,
            "t_stat": float(a.mean() / se) if se > 0 else None,
            "frac_positive": round(float((a > 0).mean()) * 100, 1)}


def token_vr_distribution(rets: np.ndarray, i0: int, i1: int, k: int) -> dict | None:
    """Distribution des VR(k) sur l'univers — médiane et quartiles."""
    vals = []
    n = i1 - i0
    for j in range(rets.shape[1]):
        col = rets[i0:i1, j]
        if (~np.isnan(col)).sum() < MIN_TOKEN_COVERAGE * n:
            continue
        v = variance_ratio(col, k)
        if v is not None:
            vals.append(v)
    if len(vals) < MIN_TOKENS:
        return None
    a = np.array(vals)
    return {"median": float(np.median(a)), "q1": float(np.percentile(a, 25)),
            "q3": float(np.percentile(a, 75)), "n_tokens": len(vals),
            "frac_above_1": round(float((a > 1).mean()) * 100, 1)}


def pctile(value, dist: list) -> float | None:
    d = [x for x in dist if x is not None]
    if value is None or len(d) < 20:
        return None
    return round(sum(1 for x in d if x < value) / len(d) * 100, 1)


def _vr_pctile(value, dist: list, k: int) -> float | None:
    """VR(1) ≡ 1 par construction : son percentile n'a pas de sens."""
    return None if k == 1 else pctile(value, dist)


# ── programme ───────────────────────────────────────────────────────────

def main() -> int:
    print("Chargement des données…", flush=True)
    data = load_3y_candles()
    universe = sorted(set(P.trade_symbols) & set(data))
    require_match("univers vs Params.trade_symbols", universe,
                  sorted(P.trade_symbols),
                  what_a="mesuré", what_b="Params.trade_symbols")
    print(banner(P, data, extra={"étude": "spectre de persistance",
                                "univers": len(universe),
                                "VR_min_blocs": VR_MIN_BLOCKS,
                                "XS_min_paires": XS_MIN_PAIRS}), flush=True)

    t0, t1 = _ms(PERIOD[0]), _ms(PERIOD[1])
    bars = {s: daily_bars(data[s]) for s in set(universe) | {"BTC"}}
    days = sorted({b["t"] for s in bars for b in bars[s] if t0 <= b["t"] <= t1})
    di = {d: i for i, d in enumerate(days)}
    n, m = len(days), len(universe)
    require_series("grille journalière", list(range(n)), min_n=400)

    logp = np.full((n, m), np.nan)
    for j, s in enumerate(universe):
        for b in bars[s]:
            k = di.get(b["t"])
            if k is not None and b["c"] > 0:
                logp[k, j] = np.log(b["c"])
    rets = np.full((n, m), np.nan)
    rets[1:] = logp[1:] - logp[:-1]

    btc_lp = np.full(n, np.nan)
    for b in bars["BTC"]:
        k = di.get(b["t"])
        if k is not None and b["c"] > 0:
            btc_lp[k] = np.log(b["c"])
    btc_r = np.full(n, np.nan)
    btc_r[1:] = btc_lp[1:] - btc_lp[:-1]

    # indice équipondéré des alts : moyenne des rendements log du jour
    alt_r = np.full(n, np.nan)
    for i in range(n):
        row = rets[i][~np.isnan(rets[i])]
        if len(row) >= MIN_TOKENS:
            alt_r[i] = row.mean()

    print(f"  grille : {n} jours × {m} tokens · "
          f"BTC {int((~np.isnan(btc_r)).sum())} j · "
          f"indice alt {int((~np.isnan(alt_r)).sum())} j", flush=True)

    excl = np.zeros(n, dtype=bool)
    for s, e in EXCLUDE:
        a, b = _ms(s), _ms(e)
        for i, d in enumerate(days):
            if a <= d <= b:
                excl[i] = True
    print(f"  baseline : {int((~excl).sum())} jours hors creux", flush=True)

    def measure(i0: int, i1: int) -> dict:
        out = {"vr_btc": {}, "vr_alt": {}, "vr_tokens": {}, "xsec": {}}
        for k in VR_HORIZONS:
            out["vr_btc"][k] = variance_ratio(btc_r[i0:i1], k)
            out["vr_alt"][k] = variance_ratio(alt_r[i0:i1], k)
            out["vr_tokens"][k] = token_vr_distribution(rets, i0, i1, k)
        for k in XS_HORIZONS:
            out["xsec"][k] = xsec_rank_autocorr(logp, i0, i1, k)
        return out

    ref_cache: dict[int, dict] = {}

    def reference(L: int) -> dict:
        if L in ref_cache:
            return ref_cache[L]
        acc = {"vr_btc": {k: [] for k in VR_HORIZONS},
               "vr_alt": {k: [] for k in VR_HORIZONS},
               "vr_tok": {k: [] for k in VR_HORIZONS},
               "xsec": {k: [] for k in XS_HORIZONS}}
        for i in range(0, n - L + 1):
            if excl[i:i + L].any():
                continue
            mm = measure(i, i + L)
            for k in VR_HORIZONS:
                acc["vr_btc"][k].append(mm["vr_btc"][k])
                acc["vr_alt"][k].append(mm["vr_alt"][k])
                acc["vr_tok"][k].append(mm["vr_tokens"][k]["median"]
                                        if mm["vr_tokens"][k] else None)
            for k in XS_HORIZONS:
                acc["xsec"][k].append(mm["xsec"][k]["mean"]
                                      if mm["xsec"][k] else None)
        ref_cache[L] = acc
        return acc

    res = {"period": PERIOD, "n_days": n, "n_tokens": m,
           "floors": {"vr_min_blocks": VR_MIN_BLOCKS,
                      "xs_min_pairs": XS_MIN_PAIRS,
                      "min_tokens": MIN_TOKENS},
           "windows": {}}

    # ── référence globale : les 28 mois hors creux, segments contigus ──
    segs = []
    i = 0
    while i < n:
        if excl[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not excl[j + 1]:
            j += 1
        segs.append((i, j + 1))
        i = j + 1
    print(f"  segments contigus hors creux : "
          f"{[(days_len := b - a) for a, b in segs]}", flush=True)

    base = {"vr_btc": {}, "vr_alt": {}, "vr_tokens": {}, "xsec": {},
            "segments": [{"start": datetime.fromtimestamp(
                days[a] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(
                days[b - 1] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                "n_days": b - a} for a, b in segs]}
    for k in VR_HORIZONS:
        vb = [variance_ratio(btc_r[a:b], k) for a, b in segs]
        va = [variance_ratio(alt_r[a:b], k) for a, b in segs]
        vb, va = [x for x in vb if x], [x for x in va if x]
        base["vr_btc"][k] = round(float(np.mean(vb)), 4) if vb else None
        base["vr_alt"][k] = round(float(np.mean(va)), 4) if va else None
        # Mesure 2 : la distribution INTER-TOKENS, pas seulement sa médiane.
        # Les VR par token sont mis en commun sur les 3 segments hors creux.
        pool = []
        for a, b in segs:
            for j in range(m):
                col = rets[a:b, j]
                if (~np.isnan(col)).sum() < MIN_TOKEN_COVERAGE * (b - a):
                    continue
                v = variance_ratio(col, k)
                if v is not None:
                    pool.append(v)
        base["vr_tokens"][k] = ({
            "median": round(float(np.median(pool)), 4),
            "q1": round(float(np.percentile(pool, 25)), 4),
            "q3": round(float(np.percentile(pool, 75)), 4),
            "frac_above_1": round(float((np.array(pool) > 1).mean()) * 100, 1),
            "n": len(pool)} if len(pool) >= MIN_TOKENS else None)
    for k in XS_HORIZONS:
        rr = [xsec_rank_autocorr(logp, a, b, k) for a, b in segs]
        rr = [x for x in rr if x]
        if rr:
            tot = sum(x["n_pairs"] for x in rr)
            _mu = sum(x["mean"] * x["n_pairs"] for x in rr) / tot
            _se = (sum((x["se"] ** 2) * (x["n_pairs"] ** 2) for x in rr)
                   ** 0.5) / tot
            base["xsec"][k] = {
                "mean": round(_mu, 4), "se": round(_se, 4),
                "t_stat": round(_mu / _se, 2) if _se > 0 else None,
                "n_pairs": tot,
                "frac_positive": round(sum(x["frac_positive"] * x["n_pairs"]
                                           for x in rr) / tot, 1)}
        else:
            base["xsec"][k] = None
    res["baseline"] = base

    print("\n═══ RÉFÉRENCE — 28 mois hors creux "
          f"({sum(b - a for a, b in segs)} jours, "
          f"{len(segs)} segments) ═══")
    print("  VR(k)      " + "".join(f"{k:>10d}j" for k in VR_HORIZONS))
    for lab, key in (("BTC", "vr_btc"), ("indice alt", "vr_alt")):
        print(f"  {lab:<11s}" + "".join(
            f"{base[key][k]:>11.3f}" if base[key][k] else f"{'—':>11s}"
            for k in VR_HORIZONS))
    for lab, fld in (("méd. tokens", "median"), ("  Q1", "q1"), ("  Q3", "q3"),
                     ("  % VR>1", "frac_above_1")):
        print(f"  {lab:<11s}" + "".join(
            f"{base['vr_tokens'][k][fld]:>11.3f}" if base["vr_tokens"][k]
            else f"{'—':>11s}" for k in VR_HORIZONS))
    print("  Spearman cross-sectionnel (rendement k-j → k-j suivants)")
    for k in XS_HORIZONS:
        v = base["xsec"][k]
        print(f"    k={k:<3d} " + (f"ρ={v['mean']:+.4f} ± {v['se']:.4f}  "
                                   f"t={v['t_stat']:+.2f}  n={v['n_pairs']:>3d} paires"
                                   f"  {v['frac_positive']:.0f}% positives"
                                   if v else "— (sous le plancher)"))

    # ── fenêtres de creux ─────────────────────────────────────────────
    for name, s, e, why in TROUGHS:
        i0, i1 = di[_ms(s)], di[_ms(e)] + 1
        L = i1 - i0
        mm = measure(i0, i1)
        ref = reference(L)
        w = {"dates": f"{s} → {e}", "n_days": L, "why": why,
             "n_ref_windows": sum(1 for x in ref["vr_alt"][3] if x is not None),
             "vr_btc": {}, "vr_alt": {}, "vr_tokens": {}, "xsec": {}}
        for k in VR_HORIZONS:
            w["vr_btc"][k] = {"value": mm["vr_btc"][k],
                              "pctile": _vr_pctile(mm["vr_btc"][k], ref["vr_btc"][k], k)}
            w["vr_alt"][k] = {"value": mm["vr_alt"][k],
                              "pctile": _vr_pctile(mm["vr_alt"][k], ref["vr_alt"][k], k)}
            w["vr_tokens"][k] = mm["vr_tokens"][k]
            if mm["vr_tokens"][k]:
                w["vr_tokens"][k]["pctile"] = _vr_pctile(
                    mm["vr_tokens"][k]["median"], ref["vr_tok"][k], k)
        for k in XS_HORIZONS:
            w["xsec"][k] = mm["xsec"][k]
            if mm["xsec"][k]:
                w["xsec"][k]["pctile"] = pctile(mm["xsec"][k]["mean"],
                                                ref["xsec"][k])
        res["windows"][name] = w

        print(f"\n── {name}  {s}→{e}  ({L} j, {why})")
        print(f"   réf : {w['n_ref_windows']} fenêtres glissantes hors creux")
        print("   VR(k)      " + "".join(f"{k:>12d}j" for k in VR_HORIZONS))
        for lab, key in (("BTC", "vr_btc"), ("indice alt", "vr_alt")):
            cells = []
            for k in VR_HORIZONS:
                c = w[key][k]
                cells.append(f"{c['value']:.3f}(p{c['pctile']:.0f})"
                             if c["value"] is not None and c["pctile"] is not None
                             else (f"{c['value']:.3f}" if c["value"] is not None
                                   else "—"))
            print(f"   {lab:<11s}" + "".join(f"{c:>13s}" for c in cells))
        cells = []
        for k in VR_HORIZONS:
            t = w["vr_tokens"][k]
            cells.append(f"{t['median']:.3f}(p{t.get('pctile', float('nan')):.0f})"
                         if t and t.get("pctile") is not None
                         else (f"{t['median']:.3f}" if t else "—"))
        print(f"   {'méd. tokens':<11s}" + "".join(f"{c:>13s}" for c in cells))
        print("   Spearman cross-sectionnel")
        for k in XS_HORIZONS:
            v = w["xsec"][k]
            if not v:
                nmax = L // k - 1
                print(f"     k={k:<3d} — NON ÉMISE ({max(nmax,0)} paires possibles "
                      f"< plancher {XS_MIN_PAIRS})")
            else:
                pc = f"  p{v['pctile']:.0f}" if v.get("pctile") is not None else ""
                print(f"     k={k:<3d} ρ={v['mean']:+.4f} ± {v['se']:.4f}  "
                      f"t={v['t_stat']:+.2f}  n={v['n_pairs']} paires"
                      f"  {v['frac_positive']:.0f}% positives{pc}")

    res["fingerprint"] = fingerprint(P, data, extra={"étude": "persistence_spectrum"})
    out = os.path.join(ROOT, "analysis", "output", "persistence_spectrum.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
