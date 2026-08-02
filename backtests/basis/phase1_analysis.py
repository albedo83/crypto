"""PHASE 1 — basis & différentiels de funding inter-venues. EXÉCUTION UNIQUE.

Applique **exactement** la grille de `docs/basis_feasibility.md`, committée avant
exécution (c37140f). Aucun paramètre réglable.

Deux économies mesurées séparément (amendement, point 2) :
  (a) ARBITRAGE — distribution du basis perp/spot vs seuil 2×AR. DESCRIPTIF.
  (b) CARRY     — épisodes de différentiel de funding de signe constant.
                  C'est (b) qui porte le verdict.

Normalisation (amendement, point 1) : tout en bps/heure, intervalles DÉRIVÉS de
l'espacement des horodatages, jamais du champ de métadonnées.

Usage : python3 -m backtests.basis.phase1_analysis
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(ROOT, "backtests", "output", "basis_cache.db")
HL_FUNDING = os.path.join(ROOT, "backtests", "output", "funding_history.db")

# ── LA GRILLE (docs/basis_feasibility.md) ───────────────────────────────
RT_BPS = {"binance": 19.0, "bybit": 20.0}     # § 3 — AR complet, 4 fills taker
BASIS_THRESHOLD_BPS = 58.0                     # § 2 — 2×AR HL perp × BN spot
MIN_EPISODE_H = 24.0                           # § 3 — persistance
MIN_TOKENS = 3                                 # § 3 — clause de verdict
MIN_COVERAGE = 0.95                            # § 3 — sinon NON ÉMISE
VERDICT_VENUE = "binance"                      # § 0 — figé avant exécution
CANONICAL_INTERVALS = (1.0, 2.0, 4.0, 8.0)

TROUGHS = [("A", "2024-08-03", "2024-11-06"), ("B", "2024-09-22", "2024-10-21"),
           ("C", "2024-08-03", "2024-09-01"), ("D", "2025-07-21", "2025-08-19")]


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def snap_interval(h: float) -> float | None:
    """Intervalle canonique le plus proche — None si irrésoluble (§ 7)."""
    if h <= 0 or h > 24:
        return None
    best = min(CANONICAL_INTERVALS, key=lambda c: abs(c - h))
    return best if abs(best - h) <= 0.5 * best else None


def derive_intervals(ts: list[int]) -> tuple[list[float | None], list[dict]]:
    """Intervalle en vigueur par enregistrement + segments de bascule."""
    iv: list[float | None] = [None] * len(ts)
    for i in range(1, len(ts)):
        iv[i] = snap_interval((ts[i] - ts[i - 1]) / 3_600_000)
    iv[0] = iv[1] if len(iv) > 1 else None
    segs, cur, start = [], iv[0], 0
    for i in range(1, len(iv)):
        if iv[i] != cur and iv[i] is not None:
            segs.append({"interval_h": cur, "from": _d(ts[start]),
                         "to": _d(ts[i - 1]), "n": i - start})
            cur, start = iv[i], i
    segs.append({"interval_h": cur, "from": _d(ts[start]),
                 "to": _d(ts[-1]), "n": len(ts) - start})
    # on ne garde que les segments significatifs (≥ 10 règlements) : un
    # règlement isolé mal espacé n'est pas une bascule d'intervalle.
    return iv, [s for s in segs if s["n"] >= 10]


def hl_hourly(con_hl, sym) -> tuple[list[int], list[float]]:
    rows = con_hl.execute("SELECT ts, funding_rate FROM funding WHERE symbol=? "
                          "ORDER BY ts", (sym,)).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def build_diff(hl_ts, hl_rate, b_ts, b_rate, b_iv) -> dict:
    """Différentiel bps/h sur la grille de règlement de la venue B.

    ⚠ La fenêtre d'étude est l'INTERSECTION des deux historiques. Le premier
    passage mesurait la couverture sur tout l'historique de la venue B, dont la
    partie antérieure au démarrage du funding Hyperliquid : 25 tokens sur 32
    sortaient alors « NON ÉMISE » pour cause de recouvrement partiel, pas pour
    cause de trou de données. C'est le run NUL du § 7 de la grille — la
    couverture doit qualifier la donnée DANS la fenêtre commune, pas la
    longueur relative des deux séries.
    """
    import bisect
    pts, uncovered, considered = [], 0, 0
    hl_lo, hl_hi = hl_ts[0], hl_ts[-1]
    for i in range(1, len(b_ts)):
        ivh = b_iv[i]
        if ivh is None:
            continue
        t0, t1 = b_ts[i - 1], b_ts[i]
        if t0 < hl_lo or t1 > hl_hi:
            continue                      # hors fenêtre commune
        considered += 1
        lo = bisect.bisect_right(hl_ts, t0)
        hi = bisect.bisect_right(hl_ts, t1)
        n_needed = max(1, round((t1 - t0) / 3_600_000))
        if hi - lo < MIN_COVERAGE * n_needed:
            uncovered += 1
            continue
        r_hl = statistics.fmean(hl_rate[lo:hi]) * 1e4          # bps/h
        r_b = b_rate[i] * 1e4 / ivh                            # bps/h
        pts.append({"ts": t1, "iv_h": ivh, "r_hl": r_hl, "r_b": r_b,
                    "diff": r_hl - r_b})
    return {"points": pts, "coverage": len(pts) / max(1, considered),
            "n_uncovered": uncovered, "n_considered": considered,
            "window": (_d(pts[0]["ts"]), _d(pts[-1]["ts"])) if pts else None}


def episodes(pts: list[dict], rt_bps: float) -> list[dict]:
    """Suites maximales de règlements consécutifs à `sign(diff)` constant."""
    out, run = [], []
    for p in pts:
        s = 1 if p["diff"] > 0 else (-1 if p["diff"] < 0 else 0)
        if run and s != run[0][1]:
            out.append(_close(run, rt_bps))
            run = []
        run.append((p, s))
    if run:
        out.append(_close(run, rt_bps))
    return out


def _close(run, rt_bps) -> dict:
    dur = sum(p["iv_h"] for p, _ in run)
    cum = sum(p["diff"] * p["iv_h"] for p, _ in run)
    return {"start": _d(run[0][0]["ts"]), "end": _d(run[-1][0]["ts"]),
            "start_ms": run[0][0]["ts"], "end_ms": run[-1][0]["ts"],
            "sign": run[0][1], "n_settlements": len(run),
            "duration_h": round(dur, 1), "cum_bps": round(cum, 2),
            "net_bps": round(abs(cum) - rt_bps, 2)}


def main() -> int:
    inv = json.load(open(os.path.join(ROOT, "analysis", "output",
                                      "basis_phase0.json")))
    universe = [s for s in inv["universe"] if inv["usable"][s]["both"]]
    data = load_3y_candles()
    print(banner(P, data, extra={"étude": "basis phase 1",
                                 "venue_verdict": VERDICT_VENUE,
                                 "AR": RT_BPS, "univers": len(universe)}),
          flush=True)

    con_hl = sqlite3.connect(HL_FUNDING)
    con_b = sqlite3.connect(CACHE)
    res = {"universe": universe, "grid": {
        "rt_bps": RT_BPS, "basis_threshold_bps": BASIS_THRESHOLD_BPS,
        "min_episode_h": MIN_EPISODE_H, "min_tokens": MIN_TOKENS,
        "min_coverage": MIN_COVERAGE, "verdict_venue": VERDICT_VENUE},
        "tokens": {}}

    # ── (b) CARRY ────────────────────────────────────────────────────
    print("\n═══ (b) CARRY — porte le verdict ═══")
    print(f"  {'token':7s} {'venue':8s} {'couv.':>7s} {'n épis':>7s} "
          f"{'≥24h':>6s} {'qualif':>7s} {'net méd':>9s} {'net agr':>10s} "
          f"{'diff moy bps/j':>15s}")
    for s in universe:
        hl_ts, hl_rate = hl_hourly(con_hl, s)
        res["tokens"][s] = {}
        for venue in ("binance", "bybit"):
            rows = con_b.execute(
                "SELECT ts, rate FROM funding_b WHERE venue=? AND symbol=? "
                "ORDER BY ts", (venue, s)).fetchall()
            if len(rows) < 100 or len(hl_ts) < 100:
                res["tokens"][s][venue] = {"emitted": False,
                                           "reason": "série trop courte"}
                continue
            b_ts = [r[0] for r in rows]
            b_rate = [r[1] for r in rows]
            b_iv, segs = derive_intervals(b_ts)
            d = build_diff(hl_ts, hl_rate, b_ts, b_rate, b_iv)
            if d["coverage"] < MIN_COVERAGE:
                res["tokens"][s][venue] = {
                    "emitted": False, "reason": "couverture < 95 %",
                    "coverage": round(d["coverage"], 4)}
                print(f"  {s:7s} {venue:8s} {d['coverage']*100:>6.1f}%   "
                      f"NON ÉMISE (couverture)")
                continue
            eps = episodes(d["points"], RT_BPS[venue])
            long_eps = [e for e in eps if e["duration_h"] >= MIN_EPISODE_H]
            qual = [e for e in long_eps if e["net_bps"] > 0]
            diffs = [p["diff"] for p in d["points"]]
            require_series(f"diff {s} {venue}", diffs, min_n=100)
            rec = {
                "emitted": True, "coverage": round(d["coverage"], 4),
                "window": d["window"], "n_considered": d["n_considered"],
                "n_settlements": len(d["points"]),
                "intervals": segs,
                "n_episodes": len(eps), "n_episodes_24h": len(long_eps),
                "n_qualifying": len(qual),
                "net_median": round(statistics.median(
                    [e["net_bps"] for e in qual]), 2) if qual else None,
                "net_sum": round(sum(e["net_bps"] for e in qual), 2) if qual else 0.0,
                "duration_median_h": round(statistics.median(
                    [e["duration_h"] for e in eps]), 1),
                "duration_max_h": round(max(e["duration_h"] for e in eps), 1),
                "diff_mean_bps_day": round(statistics.fmean(diffs) * 24, 4),
                "counts": bool(qual),
                "episodes": [e for e in qual],
            }
            res["tokens"][s][venue] = rec
            _nm = ("—" if rec["net_median"] is None
                   else f"{rec['net_median']:+.1f}")
            print(f"  {s:7s} {venue:8s} {rec['coverage']*100:>6.1f}% "
                  f"{rec['n_episodes']:>7d} {rec['n_episodes_24h']:>6d} "
                  f"{rec['n_qualifying']:>7d} {_nm:>9s} "
                  f"{rec['net_sum']:>+10.1f} {rec['diff_mean_bps_day']:>+15.4f}")

    # ── verdict ──────────────────────────────────────────────────────
    v = VERDICT_VENUE
    emitted = [s for s in universe if res["tokens"][s].get(v, {}).get("emitted")]
    counting = [s for s in emitted if res["tokens"][s][v]["counts"]]
    other = "bybit" if v == "binance" else "binance"
    counting_other = [s for s in universe
                      if res["tokens"][s].get(other, {}).get("emitted")
                      and res["tokens"][s][other]["counts"]]
    if len(emitted) < MIN_TOKENS:
        raise MeasureError(f"seulement {len(emitted)} tokens émettent une "
                           f"cellule — RUN NUL (§ 7)")
    verdict = "PHASE DE CONCEPTION" if len(counting) >= MIN_TOKENS else "BRANCHE CLOSE"
    res["verdict"] = {"venue": v, "n_emitted": len(emitted),
                      "n_counting": len(counting), "tokens_counting": counting,
                      "n_counting_other_venue": len(counting_other),
                      "tokens_counting_other": counting_other,
                      "verdict": verdict}
    print(f"\n  {v} : {len(counting)}/{len(emitted)} tokens comptent "
          f"(≥1 épisode ≥24 h avec net > 0)")
    print(f"  {other} (publié, hors verdict) : {len(counting_other)} tokens")
    print(f"  ⇒ VERDICT : {verdict}")

    # ── (a) ARBITRAGE — descriptif ───────────────────────────────────
    print("\n═══ (a) ARBITRAGE — basis perp/spot, DESCRIPTIF ═══")
    print(f"  {'token':7s} {'n 4h':>7s} {'médiane':>9s} {'p5':>8s} {'p95':>8s} "
          f"{'|b| p90':>9s} {'|b| p99':>9s} {'% > 58 bps':>11s}")
    basis = {}
    for s in universe:
        rows = con_b.execute("SELECT ts, close FROM spot_b WHERE venue='binance' "
                             "AND symbol=? ORDER BY ts", (s,)).fetchall()
        spot = {r[0]: r[1] for r in rows}
        hl = data.get(s) or []
        pairs = [((c["c"] / spot[c["t"]] - 1) * 1e4)
                 for c in hl if c["t"] in spot and spot[c["t"]] > 0]
        if len(pairs) < 200:
            basis[s] = {"emitted": False, "n": len(pairs)}
            continue
        a = sorted(pairs)
        ab = sorted(abs(x) for x in pairs)
        basis[s] = {
            "emitted": True, "n": len(pairs),
            "median": round(statistics.median(a), 2),
            "p5": round(a[int(.05 * len(a))], 2),
            "p95": round(a[int(.95 * len(a))], 2),
            "abs_p90": round(ab[int(.90 * len(ab))], 2),
            "abs_p99": round(ab[int(.99 * len(ab))], 2),
            "pct_above": round(sum(1 for x in ab if x > BASIS_THRESHOLD_BPS)
                               / len(ab) * 100, 3)}
        b = basis[s]
        print(f"  {s:7s} {b['n']:>7d} {b['median']:>+9.2f} {b['p5']:>+8.2f} "
              f"{b['p95']:>+8.2f} {b['abs_p90']:>9.2f} {b['abs_p99']:>9.2f} "
              f"{b['pct_above']:>10.3f}%")
    res["basis"] = basis

    # ── (3) croisement avec les creux d'Alfred — HORS verdict ────────
    print("\n═══ Croisement avec les creux d'Alfred — HORS VERDICT ═══")
    cross = {}
    for name, s0, s1 in TROUGHS:
        ins, outs, n_q_in = [], [], 0
        for s in emitted:
            r = res["tokens"][s][v]
            for e in r["episodes"]:
                if s0 <= e["start"] <= s1:
                    n_q_in += 1
        for s in emitted:
            rows = con_b.execute(
                "SELECT ts, rate FROM funding_b WHERE venue=? AND symbol=? "
                "ORDER BY ts", (v, s)).fetchall()
            hl_ts, hl_rate = hl_hourly(con_hl, s)
            b_ts = [x[0] for x in rows]
            b_rate = [x[1] for x in rows]
            b_iv, _ = derive_intervals(b_ts)
            d = build_diff(hl_ts, hl_rate, b_ts, b_rate, b_iv)
            for p in d["points"]:
                (ins if s0 <= _d(p["ts"]) <= s1 else outs).append(p["diff"])
        cross[name] = {
            "dates": f"{s0}→{s1}",
            "diff_in_bps_day": round(statistics.fmean(ins) * 24, 4) if ins else None,
            "diff_out_bps_day": round(statistics.fmean(outs) * 24, 4) if outs else None,
            "n_in": len(ins), "n_out": len(outs),
            "n_qualifying_starting_in": n_q_in}
        c = cross[name]
        print(f"  creux {name} {c['dates']}  dans "
              f"{c['diff_in_bps_day']:>+8.4f} bps/j (n={c['n_in']})  "
              f"hors {c['diff_out_bps_day']:>+8.4f} bps/j  "
              f"épisodes qualifiants démarrant dedans : {c['n_qualifying_starting_in']}")
    res["troughs"] = cross

    res["fingerprint"] = fingerprint(P, data, extra={"étude": "basis_phase1"})
    out = os.path.join(ROOT, "analysis", "output", "basis_phase1.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\n{'='*70}\nVERDICT : {verdict}\n{'='*70}\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
