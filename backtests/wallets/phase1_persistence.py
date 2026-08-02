"""PHASE 1 — persistance de performance des wallets HL. EXÉCUTION UNIQUE.

Applique **exactement** la grille de `docs/wallet_persistence.md` § P1, committée
avant tout chiffre (6d873cf). Aucun paramètre réglable.

Anti-lookahead strict : l'appartenance au panel se décide au SEUL trimestre T,
jamais sur l'historique complet, et jamais en exigeant la survie en T+1.

Lectures publiques uniquement. Aucune clé, aucun ordre.

Usage : python3 -m backtests.wallets.phase1_persistence
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.fingerprint import git_rev  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(ROOT, "backtests", "output", "wallet_cache.db")
INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

# ── LA GRILLE (docs/wallet_persistence.md § P1) ─────────────────────────
PANEL_SIZE = 1000          # § P1.7
MIN_AV_START = 500.0       # § P1.2 — valeur au début de T
MIN_ACTIVE_WEEKS = 1       # § P1.2 — semaines de P&L non nul dans T
MIN_MATCHED = 30           # § P1.3 — sous ce seuil, cellule NON ÉMISE
MIN_PAIRS = 3              # § P1.6 — sous ce seuil, SE non estimable
T_THRESHOLD = 2.0          # § P1.6
PAUSE = 0.12


def _q(ms: int) -> str:
    d = datetime.fromtimestamp(int(ms) / 1000, timezone.utc)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _qstart(q: str) -> int:
    y, k = int(q[:4]), int(q[-1])
    return int(datetime(y, 3 * (k - 1) + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _qnext(q: str) -> str:
    y, k = int(q[:4]), int(q[-1])
    return f"{y + 1}Q1" if k == 4 else f"{y}Q{k + 1}"


def post(payload, tries=3):
    for k in range(tries):
        try:
            r = urllib.request.Request(INFO, data=json.dumps(payload).encode(),
                                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.load(resp), None
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and k < tries - 1:
                time.sleep(2 ** k)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if k < tries - 1:
                time.sleep(1)
                continue
            return None, str(e)[:60]
    return None, "épuisé"


def db():
    con = sqlite3.connect(CACHE)
    con.execute("""CREATE TABLE IF NOT EXISTS portfolio(
        addr TEXT PRIMARY KEY, payload TEXT, fetched INTEGER)""")
    return con


def collect(con, addrs):
    have = {r[0] for r in con.execute("SELECT addr FROM portfolio")}
    todo = [a for a in addrs if a not in have]
    print(f"  {len(have)} en cache, {len(todo)} à récupérer", flush=True)
    for i, a in enumerate(todo, 1):
        p, err = post({"type": "portfolio", "user": a})
        time.sleep(PAUSE)
        if err or not isinstance(p, list):
            continue
        con.execute("INSERT OR REPLACE INTO portfolio VALUES(?,?,?)",
                    (a, json.dumps(p), int(time.time())))
        if i % 200 == 0:
            con.commit()
            print(f"    … {i}/{len(todo)}", flush=True)
    con.commit()


def series(con, addr):
    """(ts, accountValue, pnl_cumulé), premier point synthétique ÉCARTÉ."""
    r = con.execute("SELECT payload FROM portfolio WHERE addr=?", (addr,)).fetchone()
    if not r:
        return []
    D = dict(json.loads(r[0]))
    av = D.get("allTime", {}).get("accountValueHistory") or []
    pn = D.get("allTime", {}).get("pnlHistory") or []
    if len(av) != len(pn) or len(av) < 4:
        return []
    out = [(int(a[0]), float(a[1]), float(p[1])) for a, p in zip(av, pn)]
    # § P1.1 : le premier point est un remplissage synthétique (AV=0, pnl=0).
    if out and out[0][1] == 0.0 and out[0][2] == 0.0:
        out = out[1:]
    return out


def quarter_metrics(pts) -> dict:
    """Par trimestre : AV au premier point, AV médiane, pnl, semaines actives."""
    byq = defaultdict(list)
    for t, av, pn in pts:
        byq[_q(t)].append((t, av, pn))
    prev_pnl = {}
    qs = sorted(byq)
    out = {}
    for i, q in enumerate(qs):
        rows = sorted(byq[q])
        # base du cumul : dernier point AVANT T, sinon premier point de T
        base = None
        if i > 0:
            base = sorted(byq[qs[i - 1]])[-1][2]
        if base is None:
            base = rows[0][2]
        pnl = rows[-1][2] - base
        avs = [r[1] for r in rows if r[1] > 0]
        if not avs:
            continue
        cum = [r[2] for r in rows]
        active = sum(1 for k in range(1, len(cum)) if cum[k] != cum[k - 1])
        if i > 0 and cum and cum[0] != base:
            active += 1
        out[q] = {"av_start": rows[0][1], "av_median": statistics.median(avs),
                  "pnl": pnl, "n_points": len(rows), "active_weeks": active}
    del prev_pnl
    return out


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


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    dbb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or dbb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (da * dbb)


def main() -> int:
    print(f"┌ wallets phase 1 · git {git_rev()} · "
          f"run {datetime.now(timezone.utc).isoformat()[:16]}Z")
    with urllib.request.urlopen(LEADERBOARD, timeout=60) as r:
        rows = json.load(r)["leaderboardRows"]
    rows.sort(key=lambda x: -float(x["accountValue"]))
    step = max(1, len(rows) // PANEL_SIZE)
    panel = [rows[i]["ethAddress"] for i in range(0, len(rows), step)][:PANEL_SIZE]
    print(f"└ leaderboard {len(rows)} · panel {len(panel)} "
          f"(pas régulier dans le classement par TAILLE)\n", flush=True)

    con = db()
    collect(con, panel)

    # ── métriques par compte et par trimestre ─────────────────────────
    per_addr = {}
    for a in panel:
        pts = series(con, a)
        if len(pts) < 4:
            continue
        qm = quarter_metrics(pts)
        if qm:
            per_addr[a] = qm
    print(f"\n  comptes exploitables : {len(per_addr)}/{len(panel)}")

    all_q = sorted({q for m in per_addr.values() for q in m})
    print(f"  trimestres couverts : {all_q[0]} → {all_q[-1]} ({len(all_q)})")

    # ── panels roulants ───────────────────────────────────────────────
    panels = {}
    for q in all_q:
        members = []
        for a, m in per_addr.items():
            r = m.get(q)
            if not r:
                continue
            if r["av_start"] < MIN_AV_START:          # § P1.2
                continue
            if r["active_weeks"] < MIN_ACTIVE_WEEKS:  # § P1.2
                continue
            members.append(a)
        if members:
            panels[q] = members

    print(f"\n  {'trimestre':>9s} {'panel':>7s} {'terciles AV début':>34s}")
    strata_bounds = {}
    for q in sorted(panels):
        avs = sorted(per_addr[a][q]["av_start"] for a in panels[q])
        lo, hi = avs[len(avs) // 3], avs[2 * len(avs) // 3]
        strata_bounds[q] = (lo, hi)
        print(f"  {q:>9s} {len(panels[q]):>7d}   "
              f"${lo:>13,.0f} / ${hi:>13,.0f}")

    def stratum(a, q):
        lo, hi = strata_bounds[q]
        v = per_addr[a][q]["av_start"]
        return "petit" if v <= lo else ("moyen" if v <= hi else "gros")

    def metric(a, q):
        r = per_addr[a].get(q)
        if not r or r["av_median"] <= 0:
            return None
        return r["pnl"] / r["av_median"]

    # ── autocorrélation de rang par paire, groupe, strate ─────────────
    STRATA = ["petit", "moyen", "gros", "agrégat"]
    GROUPS = ["tous", "gagnants", "perdants"]
    cells = {(g, s): [] for g in GROUPS for s in STRATA}
    attrition = []
    pair_detail = []

    for q in sorted(panels):
        q2 = _qnext(q)
        if q2 not in all_q:
            continue
        members = panels[q]
        # § P1.2 : AUCUNE condition d'appartenance au panel de T+1.
        matched, lost = [], 0
        for a in members:
            m1, m2 = metric(a, q), metric(a, q2)
            if m1 is None:
                continue
            if m2 is None:
                lost += 1
                continue
            matched.append((a, m1, m2))
        attrition.append({"pair": f"{q}→{q2}", "panel": len(members),
                          "matched": len(matched), "lost": lost,
                          "attrition_pct": round(lost / max(len(members), 1) * 100, 1)})
        for g in GROUPS:
            sel = (matched if g == "tous"
                   else [x for x in matched if x[1] > 0] if g == "gagnants"
                   else [x for x in matched if x[1] < 0])
            for s in STRATA:
                sub = sel if s == "agrégat" else [x for x in sel
                                                  if stratum(x[0], q) == s]
                if len(sub) < MIN_MATCHED:
                    continue
                rho = spearman([x[1] for x in sub], [x[2] for x in sub])
                if rho is None:
                    continue
                cells[(g, s)].append(rho)
                pair_detail.append({"pair": f"{q}→{q2}", "group": g,
                                    "stratum": s, "n": len(sub),
                                    "rho": round(rho, 4)})

    print(f"\n  {'paire':>14s} {'panel':>6s} {'appariés':>9s} {'perdus':>7s} "
          f"{'attrition':>10s}")
    for r in attrition:
        print(f"  {r['pair']:>14s} {r['panel']:>6d} {r['matched']:>9d} "
              f"{r['lost']:>7d} {r['attrition_pct']:>9.1f}%")

    # ── statistique de verdict : mise en commun ───────────────────────
    print(f"\n═══ VERDICT — rho mis en commun par cellule ═══")
    print(f"  {'groupe':>10s} {'strate':>9s} {'paires':>7s} {'rho poolé':>11s} "
           f"{'SE':>8s} {'t':>8s}  ")
    pooled, n_sig = {}, 0
    for g in GROUPS:
        for s in STRATA:
            v = cells[(g, s)]
            if len(v) < MIN_PAIRS:
                pooled[f"{g}|{s}"] = {"emitted": False, "n_pairs": len(v)}
                print(f"  {g:>10s} {s:>9s} {len(v):>7d}   NON ÉMISE "
                      f"(< {MIN_PAIRS} paires)")
                continue
            mu = statistics.fmean(v)
            se = statistics.stdev(v) / math.sqrt(len(v))
            t = mu / se if se > 0 else 0.0
            sig = abs(t) >= T_THRESHOLD
            n_sig += sig
            pooled[f"{g}|{s}"] = {"emitted": True, "n_pairs": len(v),
                                  "rho_pooled": round(mu, 4), "se": round(se, 4),
                                  "t": round(t, 2), "significant": sig,
                                  "rhos": [round(x, 4) for x in v]}
            print(f"  {g:>10s} {s:>9s} {len(v):>7d} {mu:>+11.4f} {se:>8.4f} "
                  f"{t:>+8.2f}  {'◄ |t| ≥ 2' if sig else ''}")

    emitted = [k for k, v in pooled.items() if v["emitted"]]
    if not emitted:
        raise MeasureError("aucune cellule émise — RUN NUL (§ P1.9)")
    expected_fp = len(emitted) * 0.05
    verdict = "BRANCHE CLOSE" if n_sig == 0 else "PHASE DE CONCEPTION"
    print(f"\n  cellules émises : {len(emitted)} · à |t| ≥ 2 : **{n_sig}** · "
          f"attendu sous l'hypothèse nulle : {expected_fp:.1f}")
    print(f"  ⇒ VERDICT : {verdict}")

    res = {"panel_size": len(panel), "usable": len(per_addr),
           "quarters": all_q, "grid": {
               "min_av_start": MIN_AV_START, "min_active_weeks": MIN_ACTIVE_WEEKS,
               "min_matched": MIN_MATCHED, "min_pairs": MIN_PAIRS,
               "t_threshold": T_THRESHOLD},
           "panels": {q: len(v) for q, v in panels.items()},
           "strata_bounds": {q: [round(a, 2), round(b, 2)]
                             for q, (a, b) in strata_bounds.items()},
           "attrition": attrition, "pair_detail": pair_detail,
           "pooled": pooled, "n_significant": n_sig,
           "n_cells_emitted": len(emitted),
           "expected_false_positives": round(expected_fp, 2),
           "verdict": verdict,
           "fingerprint": {"git_rev": git_rev(),
                           "run_at": datetime.now(timezone.utc).isoformat()[:16]}}
    out = os.path.join(ROOT, "analysis", "output", "wallet_phase1.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\n{'='*70}\nVERDICT : {verdict}\n{'='*70}\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
