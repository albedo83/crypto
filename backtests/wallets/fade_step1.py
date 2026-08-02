"""FADE DES PERDANTS — étape 1. EXÉCUTION UNIQUE, in-data.

Applique **exactement** la grille de `docs/fade_feasibility.md`, committée avant
tout chiffre (fce59d0). Aucun paramètre réglable, aucune donnée nouvelle : le
panel vient du cache de la Phase 1, les facteurs de marché des bougies locales.

Usage : python3 -m backtests.wallets.fade_step1
"""

from __future__ import annotations

import bisect
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.fingerprint import git_rev  # noqa: E402
from backtests.wallets.phase1_persistence import (  # noqa: E402
    CACHE, MIN_AV_START, MIN_ACTIVE_WEEKS, db, series, quarter_metrics,
    spearman, _qnext)
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── LA GRILLE (docs/fade_feasibility.md) ────────────────────────────────
DECILE = 0.10             # § 1 — décile inférieur des perdants
MIN_CELL = 10             # § 2 — cellule sous-dotée
MIN_INTERVALS = 10        # § 3 — intervalles par compte
MIN_MATCHED_STAB = 30     # § 4 — comptes appariés pour la stabilité
FRICTION_MAX = 0.60       # § 5 C2
DIRECTIONAL_MIN = 0.40    # § 5 « substantielle »
T_STABLE = 2.0            # § 5 « stable »
SIGN_STABLE_MIN = 0.50    # § 5 « stable »
MIN_PAIRS = 3             # § 5 — sinon run NUL
STRATA = ["petit", "moyen", "gros", "agrégat"]


def _q(ms: int) -> str:
    d = datetime.fromtimestamp(int(ms) / 1000, timezone.utc)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def market_series():
    """BTC et indice alt équipondéré, sur la grille 4 h locale."""
    data = load_3y_candles()
    uni = sorted(set(P.trade_symbols) & set(data))
    btc = [(c["t"], c["c"]) for c in data["BTC"]]
    btc_ts = [x[0] for x in btc]
    btc_px = [x[1] for x in btc]
    # indice alt : moyenne des prix normalisés (équipondéré, rebalancé)
    by_ts = defaultdict(list)
    for s in uni:
        base = None
        for c in data[s]:
            if c["c"] <= 0:
                continue
            if base is None:
                base = c["c"]
            by_ts[c["t"]].append(c["c"] / base)
    alt_ts = sorted(by_ts)
    alt_px = [statistics.fmean(by_ts[t]) for t in alt_ts]
    require_series("indice alt", alt_px, min_n=1000)
    return (btc_ts, btc_px), (alt_ts, alt_px), len(uni)


def px_at(ts_arr, px_arr, t):
    i = bisect.bisect_right(ts_arr, t) - 1
    return px_arr[i] if i >= 0 else None


def ols3(y, x1, x2):
    """OLS y = a + b1·x1 + b2·x2 — équations normales 3×3."""
    n = len(y)
    if n < 5:
        return None
    sx1, sx2, sy = sum(x1), sum(x2), sum(y)
    s11 = sum(a * a for a in x1)
    s22 = sum(a * a for a in x2)
    s12 = sum(a * b for a, b in zip(x1, x2))
    s1y = sum(a * b for a, b in zip(x1, y))
    s2y = sum(a * b for a, b in zip(x2, y))
    A = [[n, sx1, sx2], [sx1, s11, s12], [sx2, s12, s22]]
    B = [sy, s1y, s2y]
    for i in range(3):                                  # Gauss-Jordan
        p = max(range(i, 3), key=lambda r: abs(A[r][i]))
        if abs(A[p][i]) < 1e-14:
            return None
        A[i], A[p] = A[p], A[i]
        B[i], B[p] = B[p], B[i]
        d = A[i][i]
        A[i] = [v / d for v in A[i]]
        B[i] /= d
        for r in range(3):
            if r == i:
                continue
            f = A[r][i]
            A[r] = [v - f * w for v, w in zip(A[r], A[i])]
            B[r] -= f * B[i]
    return B[0], B[1], B[2]


def main() -> int:
    print(f"┌ fade étape 1 · git {git_rev()} · "
          f"run {datetime.now(timezone.utc).isoformat()[:16]}Z")
    (btc_ts, btc_px), (alt_ts, alt_px), n_uni = market_series()
    print(f"└ facteurs : BTC + indice alt équipondéré sur {n_uni} tokens "
          f"(proxy — cf. grille § 3)\n", flush=True)

    con = db()
    addrs = [r[0] for r in con.execute("SELECT addr FROM portfolio")]
    per_addr, raw = {}, {}
    for a in addrs:
        pts = series(con, a)
        if len(pts) < 4:
            continue
        qm = quarter_metrics(pts)
        if qm:
            per_addr[a] = qm
            raw[a] = pts
    all_q = sorted({q for m in per_addr.values() for q in m})
    print(f"  comptes : {len(per_addr)} · trimestres {all_q[0]} → {all_q[-1]}")

    # ── panels roulants, identiques à la Phase 1 ──────────────────────
    panels = {}
    for q in all_q:
        m = [a for a, mm in per_addr.items()
             if (r := mm.get(q)) and r["av_start"] >= MIN_AV_START
             and r["active_weeks"] >= MIN_ACTIVE_WEEKS]
        if m:
            panels[q] = m
    strata_bounds = {}
    for q, mem in panels.items():
        avs = sorted(per_addr[a][q]["av_start"] for a in mem)
        strata_bounds[q] = (avs[len(avs) // 3], avs[2 * len(avs) // 3])

    def stratum(a, q):
        lo, hi = strata_bounds[q]
        v = per_addr[a][q]["av_start"]
        return "petit" if v <= lo else ("moyen" if v <= hi else "gros")

    def metric(a, q):
        r = per_addr[a].get(q)
        return None if not r or r["av_median"] <= 0 else r["pnl"] / r["av_median"]

    # ── décile inférieur des perdants de chaque panel T ───────────────
    deciles = {}
    for q in sorted(panels):
        losers = [(a, metric(a, q)) for a in panels[q]]
        losers = [(a, m) for a, m in losers if m is not None and m < 0]
        if len(losers) < MIN_CELL:
            continue
        losers.sort(key=lambda x: x[1])                  # plus mauvais d'abord
        k = max(MIN_CELL, int(len(losers) * DECILE))
        deciles[q] = [a for a, _ in losers[:k]]
    print(f"  déciles constitués : {len(deciles)} trimestres")

    # ═══ MESURE 1 — P&L absolu forward ═══════════════════════════════
    print(f"\n═══ MESURE 1 — rendement forward du décile (% de la valeur "
          f"au début de T+1) ═══")
    print(f"  {'paire':>14s} {'strate':>9s} {'n':>5s} {'moyenne':>10s} "
          f"{'IC 95 %':>24s} {'médiane':>10s}  ")
    m1, ci_contains_zero, n_pairs_seen = [], 0, 0
    for q in sorted(deciles):
        q2 = _qnext(q)
        if q2 not in all_q:
            continue
        n_pairs_seen += 1
        for s in STRATA:
            sel = [a for a in deciles[q]
                   if s == "agrégat" or stratum(a, q) == s]
            vals = []
            for a in sel:
                r2 = per_addr[a].get(q2)
                if not r2 or r2["av_start"] <= 0:
                    continue
                vals.append(r2["pnl"] / r2["av_start"] * 100)
            if len(vals) < MIN_CELL:
                continue
            mu = statistics.fmean(vals)
            se = statistics.stdev(vals) / math.sqrt(len(vals)) if len(vals) > 1 else 0
            lo, hi = mu - 1.96 * se, mu + 1.96 * se
            row = {"pair": f"{q}→{q2}", "stratum": s, "n": len(vals),
                   "mean_pct": round(mu, 3), "ci_lo": round(lo, 3),
                   "ci_hi": round(hi, 3), "median_pct": round(
                       statistics.median(vals), 3),
                   "ci_contains_zero": lo <= 0 <= hi}
            m1.append(row)
            if s == "agrégat" and row["ci_contains_zero"]:
                ci_contains_zero += 1
            print(f"  {row['pair']:>14s} {s:>9s} {len(vals):>5d} "
                  f"{mu:>+10.2f}% [{lo:>+8.2f} , {hi:>+8.2f}] "
                  f"{row['median_pct']:>+9.2f}%"
                  f"  {'◄ contient 0' if row['ci_contains_zero'] else ''}")
    agg = [r for r in m1 if r["stratum"] == "agrégat"]
    if len(agg) < MIN_PAIRS:
        raise MeasureError(f"seulement {len(agg)} paires émettent — RUN NUL (§ 7)")
    frac_zero = ci_contains_zero / len(agg)
    print(f"\n  agrégat : {len(agg)} paires · IC contenant 0 : "
          f"{ci_contains_zero} ({frac_zero*100:.0f} %)")
    c1 = frac_zero >= 0.5

    # ═══ MESURE 2 — décomposition friction / direction ═══════════════
    print(f"\n═══ MESURE 2 — décomposition de la perte ═══")
    per_acct = defaultdict(lambda: {"y": [], "x1": [], "x2": [], "strata": []})
    for q in sorted(deciles):
        q2 = _qnext(q)
        if q2 not in all_q:
            continue
        for a in deciles[q]:
            pts = [p for p in raw[a] if _q(p[0]) == q2]
            for i in range(1, len(pts)):
                t0, av0, c0 = pts[i - 1]
                t1, _, c1v = pts[i]
                if av0 <= 0:
                    continue
                b0, b1 = px_at(btc_ts, btc_px, t0), px_at(btc_ts, btc_px, t1)
                a0, a1 = px_at(alt_ts, alt_px, t0), px_at(alt_ts, alt_px, t1)
                if not (b0 and b1 and a0 and a1):
                    continue
                per_acct[(a, q2)]["y"].append((c1v - c0) / av0)
                per_acct[(a, q2)]["x1"].append(b1 / b0 - 1)
                per_acct[(a, q2)]["x2"].append(a1 / a0 - 1)
                per_acct[(a, q2)]["strata"].append(stratum(a, q))

    fits = {}
    for (a, q2), d in per_acct.items():
        if len(d["y"]) < MIN_INTERVALS:
            continue
        r = ols3(d["y"], d["x1"], d["x2"])
        if r is None:
            continue
        alpha, b1, b2 = r
        direc = b1 * statistics.fmean(d["x1"]) + b2 * statistics.fmean(d["x2"])
        denom = abs(alpha) + abs(direc)
        if denom <= 0:
            continue
        fits[(a, q2)] = {"alpha": alpha, "b_btc": b1, "b_alt": b2,
                         "direc": direc, "n": len(d["y"]),
                         "friction_share": abs(alpha) / denom,
                         "stratum": d["strata"][0]}
    print(f"  régressions retenues : {len(fits)} (≥ {MIN_INTERVALS} intervalles)")
    if not fits:
        raise MeasureError("aucune régression exploitable — RUN NUL")

    m2 = {}
    print(f"  {'strate':>9s} {'n':>5s} {'friction méd.':>14s} "
          f"{'direction méd.':>15s} {'α méd. (%/int.)':>17s} "
          f"{'β_btc méd.':>11s}")
    for s in STRATA:
        sub = [v for v in fits.values()
               if s == "agrégat" or v["stratum"] == s]
        if len(sub) < MIN_CELL:
            m2[s] = {"emitted": False, "n": len(sub)}
            print(f"  {s:>9s} {len(sub):>5d}   NON ÉMISE")
            continue
        fr = statistics.median(v["friction_share"] for v in sub)
        m2[s] = {"emitted": True, "n": len(sub),
                 "friction_median": round(fr, 4),
                 "directional_median": round(1 - fr, 4),
                 "alpha_median": round(statistics.median(
                     v["alpha"] for v in sub), 6),
                 "b_btc_median": round(statistics.median(
                     v["b_btc"] for v in sub), 4),
                 "b_alt_median": round(statistics.median(
                     v["b_alt"] for v in sub), 4),
                 "frac_alpha_negative": round(statistics.fmean(
                     [1.0 if v["alpha"] < 0 else 0.0 for v in sub]), 3)}
        print(f"  {s:>9s} {len(sub):>5d} {fr*100:>13.1f}% "
              f"{(1-fr)*100:>14.1f}% "
              f"{m2[s]['alpha_median']*100:>+16.3f}% "
              f"{m2[s]['b_btc_median']:>+11.3f}")
    fr_agg = m2["agrégat"]["friction_median"] if m2["agrégat"]["emitted"] else None
    c2 = (fr_agg is not None) and (fr_agg > FRICTION_MAX)

    # ═══ MESURE 3 — stabilité des β ══════════════════════════════════
    print(f"\n═══ MESURE 3 — stabilité de la part directionnelle ═══")
    by_acct = defaultdict(dict)
    for (a, q2), v in fits.items():
        by_acct[a][q2] = v
    pairs_b, sign_ok, sign_tot = [], 0, 0
    for a, qs in by_acct.items():
        ks = sorted(qs)
        for i in range(len(ks) - 1):
            if _qnext(ks[i]) != ks[i + 1]:
                continue
            pairs_b.append((qs[ks[i]]["b_btc"], qs[ks[i + 1]]["b_btc"]))
            sign_tot += 1
            if (qs[ks[i]]["b_btc"] > 0) == (qs[ks[i + 1]]["b_btc"] > 0):
                sign_ok += 1
    m3 = {"n_matched": len(pairs_b)}
    if len(pairs_b) < MIN_MATCHED_STAB:
        m3["emitted"] = False
        print(f"  {len(pairs_b)} comptes appariés < {MIN_MATCHED_STAB} — NON ÉMISE")
        c3_stable = False
    else:
        rho = spearman([x[0] for x in pairs_b], [x[1] for x in pairs_b])
        n = len(pairs_b)
        t = (rho * math.sqrt(n - 2) / math.sqrt(1 - rho ** 2)
             if rho is not None and abs(rho) < 1 else 0.0)
        sign_frac = sign_ok / max(sign_tot, 1)
        m3.update({"emitted": True, "rho_beta": round(rho, 4),
                   "t": round(t, 2), "sign_stable_frac": round(sign_frac, 3)})
        c3_stable = abs(t) >= T_STABLE and sign_frac > SIGN_STABLE_MIN
        print(f"  {n} comptes appariés · Spearman des β_btc = {rho:+.4f} "
              f"(t = {t:+.2f}) · signe stable {sign_frac*100:.1f} %")
        print(f"  ⇒ stable : {'OUI' if c3_stable else 'NON'}")

    # ═══ VERDICT ═════════════════════════════════════════════════════
    substantial = (fr_agg is not None) and ((1 - fr_agg) >= DIRECTIONAL_MIN)
    if c1:
        verdict, why = "BRANCHE CLOSE", "C1 — le P&L forward n'est pas négatif de façon fiable"
    elif c2:
        verdict, why = "BRANCHE CLOSE", "C2 — ils meurent de leurs frais, pas de leur direction"
    elif substantial and c3_stable:
        verdict, why = "ÉTAPE 2", "C3 — part directionnelle substantielle et stable"
    else:
        verdict, why = "BRANCHE CLOSE", ("repli conservateur — direction "
                                         "substantielle mais instable (§ 5)")
    print(f"\n{'='*70}\nVERDICT : {verdict}\n  motif : {why}\n{'='*70}")

    res = {"decile": DECILE, "n_quarters_decile": len(deciles),
           "measure1": m1, "n_pairs_agg": len(agg),
           "ci_contains_zero": ci_contains_zero,
           "frac_ci_zero": round(frac_zero, 3), "C1": c1,
           "measure2": m2, "friction_median_agg": fr_agg, "C2": c2,
           "substantial": substantial,
           "measure3": m3, "C3_stable": c3_stable,
           "verdict": verdict, "why": why,
           "n_universe_alt_index": n_uni,
           "fingerprint": {"git_rev": git_rev(),
                           "run_at": datetime.now(timezone.utc).isoformat()[:16]}}
    out = os.path.join(ROOT, "analysis", "output", "fade_step1.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"Dump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
