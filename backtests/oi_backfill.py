"""COMBLAGE DE LA SÉRIE OI DU BACKTEST — applique docs/oi_backfill_protocol.md.

Seuils committés avant mesure (4a3b2b8). Aucun paramètre réglable.

Source de remplacement : `market_snapshots` de alfred/data/market.db, l'amont S3
(`hyperliquid-archive/asset_ctxs/`) ne publiant plus après le 2026-06-29.

Le comblage est **purement additif** : aucun point historique n'est réécrit, et
chaque point ajouté porte `"src": "live"`.

Usage :
    python3 -m backtests.oi_backfill --dry-run   # valide, n'écrit rien
    python3 -m backtests.oi_backfill             # valide puis écrit
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import shutil
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = os.path.join(ROOT, "backtests", "output", "pairs_data")
MARKET_DB = os.path.join(ROOT, "alfred", "data", "market.db")

# ── LE PROTOCOLE (docs/oi_backfill_protocol.md § 2) ─────────────────────
# ── AMENDEMENT du 2026-08-02 (tir unique, § A1) ────────────────────────
# La validation se fait contre oi_history.db — MÊME amont S3 que les fichiers
# à combler, mais qui va jusqu'au 2026-06-29 : 19 jours de recouvrement et
# ~114 points par token, au lieu des 5 jours / 30 points du protocole initial.
# Appariement à ±5 min : les relevés live tombent à :03, la grille 4h à :00.
OI_HISTORY_DB = os.path.join(ROOT, "backtests", "output", "oi_history.db")
MATCH_TOL_MS = 5 * 60 * 1000
MIN_POINTS = 20            # § 2 — points comparables minimaux
MAX_MEDIAN_DEV = 0.010     # § 2 — écart relatif médian < 1,0 %
MAX_P95_DEV = 0.050        # § 2 — p95 < 5,0 %
MIN_PASS_RATE = 0.90       # § 2 — 90 % des tokens éligibles
EXCLUDED = {"TON"}         # § 2 — retrait de cote, pas un trou
STEP_MS = 4 * 3600 * 1000


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def live_oi(sym: str) -> list[tuple[int, float]]:
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts, oi FROM market_snapshots WHERE symbol=? "
                       "AND oi IS NOT NULL AND oi>0 ORDER BY ts", (sym,)).fetchall()
    con.close()
    return [(int(t) * 1000, float(o)) for t, o in rows]


def s3_hourly(sym: str) -> list[tuple[int, float]]:
    """Série OI de l'archive S3 (oi_history.db) — base de validation amendée."""
    con = sqlite3.connect(f"file:{OI_HISTORY_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts, oi FROM asset_ctx WHERE symbol=? AND oi>0 "
                       "ORDER BY ts", (sym,)).fetchall()
    con.close()
    return [(int(t) * 1000, float(o)) for t, o in rows]


def at_grid(pts: list[tuple[int, float]], g: int) -> float | None:
    """Valeur au point de grille g, à ±MATCH_TOL_MS près. Sinon None."""
    if not pts:
        return None
    ts = [p[0] for p in pts]
    i = bisect.bisect_left(ts, g)
    best, bd = None, None
    for j in (i - 1, i):
        if 0 <= j < len(ts):
            d = abs(ts[j] - g)
            if bd is None or d < bd:
                bd, best = d, pts[j][1]
    return best if (best is not None and bd <= MATCH_TOL_MS) else None


def resample_4h(pts: list[tuple[int, float]], t0: int, t1: int) -> list[tuple[int, float]]:
    """Grille 4 h, même tolérance d'appariement que la validation (±5 min).

    Une seule tolérance pour valider ET pour produire : en inventer une seconde
    reviendrait à valider une chose et à en écrire une autre.
    """
    if not pts:
        return []
    out = []
    g = (t0 // STEP_MS) * STEP_MS
    while g <= t1:
        v = at_grid(pts, g)
        if v is not None:
            out.append((g, v))
        g += STEP_MS
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    universe = sorted(set(P.trade_symbols))
    print(f"┌ comblage OI · {len(universe)} tokens · "
          f"exclus d'avance : {sorted(EXCLUDED)}")
    print(f"└ seuils : n≥{MIN_POINTS} · médiane<{MAX_MEDIAN_DEV*100:.1f}% · "
          f"p95<{MAX_P95_DEV*100:.1f}% · réussite globale ≥{MIN_PASS_RATE*100:.0f}%\n")

    results, eligible = {}, []
    print(f"  {'token':7s} {'n comp.':>8s} {'médiane':>9s} {'p95':>8s} "
          f"{'max':>8s} {'à ajouter':>10s}  verdict")
    for s in universe:
        path = os.path.join(PAIRS, f"{s}_oi_4h.json")
        rec = {"excluded": s in EXCLUDED}
        if rec["excluded"]:
            results[s] = {**rec, "pass": False, "reason": "exclu (retrait de cote)"}
            print(f"  {s:7s} {'—':>8s} {'—':>9s} {'—':>8s} {'—':>8s} "
                  f"{'—':>10s}  EXCLU")
            continue
        if not os.path.exists(path):
            results[s] = {**rec, "pass": False, "reason": "fichier absent"}
            continue
        old = json.load(open(path))
        if not old:
            results[s] = {**rec, "pass": False, "reason": "fichier vide"}
            continue
        old_ts = [int(r["t"]) for r in old]
        old_map = {int(r["t"]): float(r["oi"]) for r in old}
        last_old = max(old_ts)

        lv = live_oi(s)
        if not lv:
            results[s] = {**rec, "pass": False, "reason": "absent de market_snapshots"}
            print(f"  {s:7s} {'—':>8s} {'—':>9s} {'—':>8s} {'—':>8s} "
                  f"{'—':>10s}  NON COMBLÉ (absent du live)")
            continue
        grid = resample_4h(lv, min(p[0] for p in lv), max(p[0] for p in lv))
        gmap = dict(grid)

        # ── validation AMENDÉE : live contre oi_history.db (§ A1) ──────
        s3 = s3_hourly(s)
        devs = []
        if s3:
            lo = max(min(p[0] for p in lv), min(p[0] for p in s3))
            hi = min(max(p[0] for p in lv), max(p[0] for p in s3))
            g = ((lo + STEP_MS - 1) // STEP_MS) * STEP_MS
            while g <= hi:
                a, b = at_grid(s3, g), at_grid(lv, g)
                if a is not None and b is not None and a > 0:
                    devs.append(abs(b - a) / a)
                g += STEP_MS
        add = [(t, v) for t, v in grid if t > last_old]
        if len(devs) < MIN_POINTS:
            results[s] = {**rec, "pass": False, "n_overlap": len(devs),
                          "reason": f"{len(devs)} points < {MIN_POINTS}"}
            print(f"  {s:7s} {len(devs):>8d} {'—':>9s} {'—':>8s} {'—':>8s} "
                  f"{len(add):>10d}  NON COMBLÉ (recouvrement insuffisant)")
            continue
        require_series(f"écarts {s}", devs, min_n=MIN_POINTS, allow_constant=True)
        devs_s = sorted(devs)
        med = statistics.median(devs_s)
        p95 = devs_s[int(0.95 * len(devs_s))]
        ok = med < MAX_MEDIAN_DEV and p95 < MAX_P95_DEV
        results[s] = {**rec, "pass": ok, "n_overlap": len(devs),
                      "median_dev": round(med, 5), "p95_dev": round(p95, 5),
                      "max_dev": round(devs_s[-1], 5), "n_add": len(add),
                      "last_old": _d(last_old),
                      "first_add": _d(add[0][0]) if add else None,
                      "last_add": _d(add[-1][0]) if add else None}
        eligible.append(s)
        print(f"  {s:7s} {len(devs):>8d} {med*100:>8.3f}% {p95*100:>7.3f}% "
              f"{devs_s[-1]*100:>7.2f}% {len(add):>10d}  "
              f"{'✓' if ok else '✗ ÉCHEC'}")

    passed = [s for s in eligible if results[s]["pass"]]
    rate = len(passed) / max(len(eligible), 1)
    print(f"\n  éligibles {len(eligible)} · réussis {len(passed)} "
          f"({rate*100:.1f} %) · seuil global {MIN_PASS_RATE*100:.0f} %")
    failed = [s for s in eligible if not results[s]["pass"]]
    if failed:
        print(f"  tokens en échec, NON comblés : {failed}")

    if rate < MIN_PASS_RATE:
        print(f"\n  ⇒ taux global insuffisant — AUCUN fichier modifié (§ 2)")
        _dump(results, rate, [], args.dry_run)
        return 1

    if args.dry_run:
        print(f"\n  ⇒ --dry-run : validation OK, rien n'est écrit")
        _dump(results, rate, [], True)
        return 0

    # ── écriture, purement additive ───────────────────────────────────
    written = []
    for s in passed:
        path = os.path.join(PAIRS, f"{s}_oi_4h.json")
        old = json.load(open(path))
        last_old = max(int(r["t"]) for r in old)
        lv = live_oi(s)
        grid = resample_4h(lv, min(p[0] for p in lv), max(p[0] for p in lv))
        add = [{"t": t, "oi": round(v, 4), "src": "live"}
               for t, v in grid if t > last_old]
        if not add:
            continue
        shutil.copy2(path, path + ".pre_backfill")
        with open(path, "w") as f:
            json.dump(old + add, f)
        written.append({"symbol": s, "n_added": len(add),
                        "weld_at": _d(last_old),
                        "first_added": _d(add[0]["t"]),
                        "last_added": _d(add[-1]["t"])})
        print(f"  écrit {s:7s} +{len(add):4d} points · soudure {_d(last_old)}")
    print(f"\n  {len(written)} fichiers comblés · sauvegardes en *.pre_backfill")
    _dump(results, rate, written, False)
    return 0


def _dump(results, rate, written, dry):
    out = os.path.join(ROOT, "analysis", "output", "oi_backfill.json")
    with open(out, "w") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat()[:16],
                   "dry_run": dry, "pass_rate": round(rate, 4),
                   "thresholds": {"min_points": MIN_POINTS,
                                  "max_median_dev": MAX_MEDIAN_DEV,
                                  "max_p95_dev": MAX_P95_DEV,
                                  "min_pass_rate": MIN_PASS_RATE},
                   "excluded": sorted(EXCLUDED),
                   "per_token": results, "written": written}, f, indent=1,
                  default=str)
    print(f"  dump : {out}")


if __name__ == "__main__":
    sys.exit(main())
