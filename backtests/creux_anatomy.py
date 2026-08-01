"""ANATOMIE DES RÉGIMES DE CREUX — diagnostic pur, zéro fit.

Contexte (2026-08-01) : TREND-v0 rejeté. La clause C2 a montré que les creux
d'Alfred tuent AUSSI le suivi de tendance — les deux moteurs y perdent
ensemble. Hypothèse à tester : ce sont des régimes de **chop violent**, pas des
tendances.

Cette étude **caractérise** ces fenêtres. Aucune stratégie n'est simulée, aucun
paramètre n'est ajusté, aucune recommandation n'est produite.

Méthode. Chaque fenêtre de creux est comparée non pas à « la moyenne du reste »
— une fenêtre de 30 jours et une période de 24 mois ne sont pas comparables —
mais à la **distribution de toutes les fenêtres glissantes de MÊME LONGUEUR**
prises hors des creux. Chaque mesure est donc rendue avec son **percentile**
dans cette distribution de référence. C'est indispensable pour l'efficacité
directionnelle et le taux de retournement, qui décroissent mécaniquement avec
la longueur de la fenêtre.

Livrable : docs/creux_anatomy.md · analysis/output/creux_anatomy.json

Usage : python3 -m backtests.creux_anatomy
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import load_funding  # noqa: E402
from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.backtest_trend_v0 import daily_bars, atr_series  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_match, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY_MS = 86_400_000
PERIOD = ("2024-04-01", "2026-08-01")

# Fenêtres de creux — issues de docs/dd_anatomy.md, FIXÉES.
TROUGHS = [
    ("A", "2024-08-03", "2024-11-06", "drawdown maximal, −51,4 %"),
    ("B", "2024-09-22", "2024-10-21", "pire fenêtre 30 j, −29,5 %"),
    ("C", "2024-08-03", "2024-09-01", "2ᵉ pire 30 j, −26,2 %"),
    ("D", "2025-07-21", "2025-08-19", "3ᵉ pire 30 j, −25,2 %"),
]
# B et C sont incluses dans A : la baseline exclut l'union, soit A ∪ D.
EXCLUDE = [("2024-08-03", "2024-11-06"), ("2025-07-21", "2025-08-19")]

MIN_TOKEN_COVERAGE = 0.8    # part des jours de la fenêtre requise par token
MIN_TOKENS = 10             # sous ce seuil une mesure agrégée n'a pas de sens


def _ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


# ── mesures sur une fenêtre (indices [i0, i1) de la grille journalière) ──

def _slice(mat: np.ndarray, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    """Sous-matrice + masque des tokens suffisamment couverts."""
    sub = mat[i0:i1]
    n = i1 - i0
    ok = (~np.isnan(sub)).sum(axis=0) >= MIN_TOKEN_COVERAGE * n
    return sub[:, ok], ok


def efficiency_ratio(closes: np.ndarray, i0: int, i1: int) -> float | None:
    """|déplacement net| / longueur du chemin — Kaufman. 1 = tendance pure."""
    sub, ok = _slice(closes, i0, i1)
    if ok.sum() < MIN_TOKENS:
        return None
    out = []
    for j in range(sub.shape[1]):
        c = sub[:, j]
        c = c[~np.isnan(c)]
        if len(c) < 5:
            continue
        path = np.abs(np.diff(c)).sum()
        if path <= 0:
            continue
        out.append(abs(c[-1] - c[0]) / path)
    return float(np.median(out)) if len(out) >= MIN_TOKENS else None


def reversal_rate(rets: np.ndarray, i0: int, i1: int) -> float | None:
    """Part des jours où le rendement change de signe."""
    sub, ok = _slice(rets, i0, i1)
    if ok.sum() < MIN_TOKENS:
        return None
    out = []
    for j in range(sub.shape[1]):
        r = sub[:, j]
        r = r[~np.isnan(r)]
        r = r[r != 0]
        if len(r) < 5:
            continue
        out.append(np.mean(np.sign(r[1:]) != np.sign(r[:-1])))
    return float(np.median(out)) if len(out) >= MIN_TOKENS else None


def mean_pair_corr(rets: np.ndarray, i0: int, i1: int) -> float | None:
    """Corrélation moyenne des paires de tokens dans la fenêtre."""
    sub, ok = _slice(rets, i0, i1)
    if ok.sum() < MIN_TOKENS or sub.shape[0] < 10:
        return None
    df = np.where(np.isnan(sub), 0.0, sub)
    sd = df.std(axis=0)
    keep = sd > 0
    df = df[:, keep]
    if df.shape[1] < MIN_TOKENS:
        return None
    c = np.corrcoef(df, rowvar=False)
    iu = np.triu_indices_from(c, k=1)
    v = c[iu]
    v = v[~np.isnan(v)]
    return float(v.mean()) if len(v) else None


def mean_of(mat: np.ndarray, i0: int, i1: int) -> float | None:
    """Moyenne d'une matrice jour × token sur la fenêtre (NaN ignorés)."""
    sub, ok = _slice(mat, i0, i1)
    if ok.sum() < MIN_TOKENS:
        return None
    v = sub[~np.isnan(sub)]
    return float(v.mean()) if v.size else None


def dispersion(rets: np.ndarray, i0: int, i1: int) -> float | None:
    """Dispersion cross-sectionnelle : écart-type inter-tokens, moyenné."""
    sub, ok = _slice(rets, i0, i1)
    if ok.sum() < MIN_TOKENS:
        return None
    per_day = []
    for row in sub:
        r = row[~np.isnan(row)]
        if len(r) >= MIN_TOKENS:
            per_day.append(r.std())
    return float(np.mean(per_day)) if per_day else None


def btc_leadership(rets: np.ndarray, btc_r: np.ndarray,
                   i0: int, i1: int) -> dict | None:
    """Le creux est-il mené par BTC, ou par la dispersion des alts ?"""
    sub, ok = _slice(rets, i0, i1)
    b = btc_r[i0:i1]
    if ok.sum() < MIN_TOKENS or np.isnan(b).all():
        return None
    corrs = []
    for j in range(sub.shape[1]):
        a = sub[:, j]
        m = ~(np.isnan(a) | np.isnan(b))
        if m.sum() < 10 or a[m].std() == 0 or b[m].std() == 0:
            continue
        corrs.append(np.corrcoef(a[m], b[m])[0, 1])
    if len(corrs) < MIN_TOKENS:
        return None
    return {"corr_alt_btc": round(float(np.median(corrs)), 4),
            "r2_median": round(float(np.median(np.array(corrs) ** 2)), 4)}


METRICS = {
    "efficiency_ratio": ("efficacité directionnelle (médiane tokens)", "low_is_chop"),
    "reversal_rate": ("taux de retournement journalier", "high_is_chop"),
    "wick_frac": ("part de mèche dans la barre 4 h", "high_is_chop"),
    "atr_rel": ("ATR(14) relatif au prix", "high_is_vol"),
    "dispersion": ("dispersion cross-sectionnelle journalière", "high_is_vol"),
    "mean_pair_corr": ("corrélation moyenne intra-univers", "neutral"),
}


def compute_all(i0, i1, closes, rets, wick, atrrel, btc_r) -> dict:
    return {
        "efficiency_ratio": efficiency_ratio(closes, i0, i1),
        "reversal_rate": reversal_rate(rets, i0, i1),
        "wick_frac": mean_of(wick, i0, i1),
        "atr_rel": mean_of(atrrel, i0, i1),
        "dispersion": dispersion(rets, i0, i1),
        "mean_pair_corr": mean_pair_corr(rets, i0, i1),
    }


def pctile(value: float | None, dist: list[float]) -> float | None:
    """Percentile de `value` dans `dist` (0 = plus bas que tout le reste)."""
    if value is None or not dist:
        return None
    return round(sum(1 for d in dist if d < value) / len(dist) * 100, 1)


# ── funding ─────────────────────────────────────────────────────────────

def funding_stats(funding: dict, syms: list[str], t0: int, t1: int) -> dict | None:
    """Niveau, signe et extrêmes des taux HL sur la fenêtre.

    Convention HL : taux > 0 → les LONGs paient, les SHORTs reçoivent.
    « Le côté receveur » désigne donc les SHORTs quand le taux est positif.
    """
    rows = []
    per_sym = {}
    for s in syms:
        if s not in funding:
            continue
        ts, rate = funding[s]
        lo, hi = np.searchsorted(ts, t0, "left"), np.searchsorted(ts, t1, "right")
        if hi - lo < 24:
            continue
        r = rate[lo:hi]
        rows.append(r)
        per_sym[s] = float(r.mean())
    if len(rows) < MIN_TOKENS:
        return None
    allr = np.concatenate(rows)
    hours = (t1 - t0) / 3_600_000
    mean_rate = float(allr.mean())
    return {
        "n_symbols": len(per_sym), "n_samples": int(allr.size),
        "mean_rate_hourly": mean_rate,
        "mean_rate_bps_per_day": round(mean_rate * 24 * 1e4, 3),
        "median_rate_hourly": float(np.median(allr)),
        "p05_hourly": float(np.percentile(allr, 5)),
        "p95_hourly": float(np.percentile(allr, 95)),
        "frac_positive": round(float((allr > 0).mean()) * 100, 1),
        # ce qu'un SHORT de $1 000 aurait encaissé (positif) ou payé (négatif)
        "short_carry_usd_per_1000": round(mean_rate * hours * 1000, 2),
        "window_hours": round(hours, 1),
        "per_symbol_extremes": {
            "plus_payeur_pour_longs": max(per_sym, key=per_sym.get),
            "valeur": round(max(per_sym.values()) * 24 * 1e4, 3),
            "plus_payeur_pour_shorts": min(per_sym, key=per_sym.get),
            "valeur_min": round(min(per_sym.values()) * 24 * 1e4, 3)},
    }


# ── programme ───────────────────────────────────────────────────────────

def main() -> int:
    print("Chargement des données…", flush=True)
    data = load_3y_candles()
    funding = load_funding()

    universe = sorted(set(P.trade_symbols) & set(data))
    require_match("univers vs Params.trade_symbols", universe,
                  sorted(P.trade_symbols),
                  what_a="mesuré", what_b="Params.trade_symbols")
    print(banner(P, data, extra={"étude": "anatomie des creux",
                                "univers": len(universe),
                                "période": f"{PERIOD[0]}→{PERIOD[1]}"}), flush=True)

    # grille journalière commune
    t0, t1 = _ms(PERIOD[0]), _ms(PERIOD[1])
    bars = {s: daily_bars(data[s]) for s in universe + ["BTC"]}
    days = sorted({b["t"] for s in bars for b in bars[s] if t0 <= b["t"] <= t1})
    di = {d: i for i, d in enumerate(days)}
    n, m = len(days), len(universe)
    require_series("grille journalière", list(range(n)), min_n=400)

    closes = np.full((n, m), np.nan)
    wick = np.full((n, m), np.nan)
    atrrel = np.full((n, m), np.nan)
    for j, s in enumerate(universe):
        a = atr_series(bars[s], 14)
        for i, b in enumerate(bars[s]):
            k = di.get(b["t"])
            if k is None:
                continue
            closes[k, j] = b["c"]
            rng = b["h"] - b["l"]
            if rng > 0:
                wick[k, j] = (rng - abs(b["c"] - b["o"])) / rng
            if a[i] and b["c"] > 0:
                atrrel[k, j] = a[i] / b["c"]

    rets = np.full((n, m), np.nan)
    rets[1:] = closes[1:] / closes[:-1] - 1

    btc_c = np.full(n, np.nan)
    for b in bars["BTC"]:
        k = di.get(b["t"])
        if k is not None:
            btc_c[k] = b["c"]
    btc_r = np.full(n, np.nan)
    btc_r[1:] = btc_c[1:] / btc_c[:-1] - 1

    if np.isnan(closes).all():
        raise MeasureError("matrice de clôtures vide")
    cover = (~np.isnan(closes)).mean()
    print(f"  grille : {n} jours × {m} tokens, couverture {cover*100:.1f}%",
          flush=True)

    # masque baseline : jours hors de A ∪ D
    excl = np.zeros(n, dtype=bool)
    for s, e in EXCLUDE:
        a, b = _ms(s), _ms(e)
        for i, d in enumerate(days):
            if a <= d <= b:
                excl[i] = True
    print(f"  baseline : {int((~excl).sum())} jours hors creux "
          f"({int(excl.sum())} exclus)", flush=True)

    res = {"period": PERIOD, "n_days": n, "n_tokens": m,
           "baseline_days": int((~excl).sum()), "windows": {}}

    # distributions de référence, par longueur de fenêtre
    ref_cache: dict[int, dict] = {}

    def reference(L: int) -> dict:
        if L in ref_cache:
            return ref_cache[L]
        acc: dict = {k: [] for k in METRICS}
        acc["btc_corr"] = []
        for i in range(0, n - L + 1):
            if excl[i:i + L].any():
                continue
            vals = compute_all(i, i + L, closes, rets, wick, atrrel, btc_r)
            for k, v in vals.items():
                if v is not None:
                    acc[k].append(v)
            bl = btc_leadership(rets, btc_r, i, i + L)
            if bl:
                acc["btc_corr"].append(bl["corr_alt_btc"])
        ref_cache[L] = acc
        return acc

    for name, s, e, why in TROUGHS:
        i0, i1 = di[_ms(s)], di[_ms(e)] + 1
        L = i1 - i0
        vals = compute_all(i0, i1, closes, rets, wick, atrrel, btc_r)
        ref = reference(L)
        bl = btc_leadership(rets, btc_r, i0, i1)
        fnd = funding_stats(funding, universe, _ms(s), _ms(e) + DAY_MS)

        # BTC vs alts sur la fenêtre
        bs, be = btc_c[i0], btc_c[i1 - 1]
        btc_ret = (be / bs - 1) * 100 if bs > 0 else None
        alt_rets = []
        for j in range(m):
            c = closes[i0:i1, j]
            c = c[~np.isnan(c)]
            if len(c) >= L * MIN_TOKEN_COVERAGE:
                alt_rets.append((c[-1] / c[0] - 1) * 100)

        w = {
            "dates": f"{s} → {e}", "n_days": L, "why": why,
            "n_ref_windows": len(ref["efficiency_ratio"]),
            "metrics": {k: {"value": vals[k],
                            "pctile": pctile(vals[k], ref[k]),
                            "ref_median": (round(float(np.median(ref[k])), 5)
                                           if ref[k] else None)}
                        for k in METRICS},
            "btc": {"btc_ret_pct": round(btc_ret, 2) if btc_ret else None,
                    "alt_ret_median_pct": round(float(np.median(alt_rets)), 2)
                    if alt_rets else None,
                    "alt_ret_std_pct": round(float(np.std(alt_rets)), 2)
                    if alt_rets else None,
                    "corr_alt_btc": bl["corr_alt_btc"] if bl else None,
                    "corr_alt_btc_pctile": pctile(bl["corr_alt_btc"],
                                                  ref["btc_corr"]) if bl else None,
                    "r2_median": bl["r2_median"] if bl else None},
            "funding": fnd,
        }
        res["windows"][name] = w

        print(f"\n── {name}  {s}→{e}  ({L} j, {why})", flush=True)
        print(f"   réf : {w['n_ref_windows']} fenêtres glissantes hors creux")
        for k, (label, _) in METRICS.items():
            mv = w["metrics"][k]
            if mv["value"] is None:
                print(f"   {label:42s} —")
                continue
            print(f"   {label:42s} {mv['value']:>9.4f}  "
                  f"(réf {mv['ref_median']:>9.4f}, p{mv['pctile']:>5.1f})")
        print(f"   BTC {w['btc']['btc_ret_pct']:>+7.1f}%  alt médian "
              f"{w['btc']['alt_ret_median_pct']:>+7.1f}%  "
              f"corr alt-BTC {w['btc']['corr_alt_btc']:.3f} "
              f"(p{w['btc']['corr_alt_btc_pctile']})")
        if fnd:
            print(f"   funding {fnd['mean_rate_bps_per_day']:>+7.3f} bps/j  "
                  f"({fnd['frac_positive']:.0f}% d'échantillons positifs) → "
                  f"un SHORT de $1000 encaisse "
                  f"${fnd['short_carry_usd_per_1000']:+.2f} sur la fenêtre")

    # baseline agrégée, pour référence dans le rapport
    res["baseline_reference"] = {
        str(L): {k: {"median": round(float(np.median(v[k])), 5),
                     "p10": round(float(np.percentile(v[k], 10)), 5),
                     "p90": round(float(np.percentile(v[k], 90)), 5),
                     "n": len(v[k])}
                 for k in METRICS if v[k]}
        for L, v in ref_cache.items()}
    # funding de référence : toute la période hors creux, par blocs contigus
    res["baseline_funding"] = funding_stats(funding, universe, t0, t1)
    res["fingerprint"] = fingerprint(P, data, extra={"étude": "creux_anatomy"})

    out = os.path.join(ROOT, "analysis", "output", "creux_anatomy.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
