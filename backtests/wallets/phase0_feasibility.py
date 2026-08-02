"""PHASE 0 — faisabilité d'une étude de persistance de performance des wallets HL.

Lectures publiques uniquement : leaderboard `stats-data.hyperliquid.xyz` et
endpoint `info` d'Hyperliquid. **Aucune clé, aucun ordre, aucune donnée privée.**
Les adresses sont publiques par construction (registre on-chain + classement
public de la plateforme).

Ce script n'évalue AUCUNE performance et ne classe AUCUN wallet. Il répond à
quatre questions et s'arrête :

 1. quel panel est constituable, et à quel coût ?
 2. quelle profondeur d'historique est RÉELLEMENT récupérable par adresse ?
 3. les fills historiques sont-ils accessibles pour une adresse tierce ?
 4. quelles limites de débit l'API impose-t-elle ?

GATE : moins de 12 mois d'historique récupérable ⇒ NO-GO documenté.

Contrôle hérité : toute colonne temporelle dont min == max == date du run est
une mesure fausse (incident Binance `startTime=0`, projet basis).

Usage : python3 -m backtests.wallets.phase0_feasibility
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.fingerprint import fingerprint, git_rev  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

PANEL_SIZE = 250          # > 200 demandés
GATE_DAYS = 365.0         # 12 mois
PAUSE = 0.12
MIN_POINTS = 8            # sous ce nombre de points, cellule NON ÉMISE


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def post(payload, tries=3):
    for k in range(tries):
        t0 = time.perf_counter()
        try:
            r = urllib.request.Request(INFO, data=json.dumps(payload).encode(),
                                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.load(resp), (time.perf_counter() - t0) * 1000, None
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and k < tries - 1:
                time.sleep(2 ** k)
                continue
            return None, (time.perf_counter() - t0) * 1000, f"HTTP {e.code}"
        except Exception as e:
            if k < tries - 1:
                time.sleep(1)
                continue
            return None, (time.perf_counter() - t0) * 1000, str(e)[:70]
    return None, 0.0, "épuisé"


def leaderboard() -> list[dict]:
    with urllib.request.urlopen(LEADERBOARD, timeout=60) as r:
        return json.load(r)["leaderboardRows"]


def build_panel(rows: list[dict], n: int) -> list[dict]:
    """Panel réparti sur toute l'échelle de taille, pas seulement le sommet.

    Ne prendre que le top serait une sélection sur la performance passée — le
    biais même que la Phase 1 doit éviter. On échantillonne donc à pas régulier
    dans le classement par taille de compte, qui n'est pas la performance.
    """
    rows = sorted(rows, key=lambda r: -float(r["accountValue"]))
    step = max(1, len(rows) // n)
    return [rows[i] for i in range(0, len(rows), step)][:n]


def main() -> int:
    print(f"┌ étude wallets phase 0 · git {git_rev()} · "
          f"run {datetime.now(timezone.utc).isoformat()[:16]}Z")
    rows = leaderboard()
    avs = [float(r["accountValue"]) for r in rows]
    print(f"└ leaderboard : {len(rows)} adresses · accountValue "
          f"max ${max(avs):,.0f} · médiane ${statistics.median(avs):,.0f}\n",
          flush=True)

    panel = build_panel(rows, PANEL_SIZE)
    print(f"Panel : {len(panel)} adresses échantillonnées à pas régulier dans le "
          f"classement par TAILLE (pas par performance)", flush=True)

    # ── profondeur par adresse ────────────────────────────────────────
    recs, lat = [], []
    t_start = time.perf_counter()
    for i, r in enumerate(panel, 1):
        a = r["ethAddress"]
        p, ms, err = post({"type": "portfolio", "user": a})
        lat.append(ms)
        time.sleep(PAUSE)
        if err or not isinstance(p, list):
            recs.append({"addr": a, "emitted": False, "reason": err or "format"})
            continue
        D = dict(p)
        ah = D.get("allTime", {}).get("accountValueHistory") or []
        if len(ah) < MIN_POINTS:
            recs.append({"addr": a, "emitted": False,
                         "reason": f"{len(ah)} points < {MIN_POINTS}"})
            continue
        t0, t1 = int(ah[0][0]), int(ah[-1][0])
        steps = [(int(ah[k + 1][0]) - int(ah[k][0])) / 86400000
                 for k in range(len(ah) - 1)]
        recs.append({
            "addr": a, "emitted": True,
            "account_value": float(r["accountValue"]),
            "n_points": len(ah), "span_days": round((t1 - t0) / 86400000, 1),
            "first": _d(t0), "last": _d(t1),
            "step_median_days": round(statistics.median(steps), 3)})
        if i % 50 == 0:
            print(f"  … {i}/{len(panel)}", flush=True)
    elapsed = time.perf_counter() - t_start

    ok = [r for r in recs if r["emitted"]]
    if len(ok) < 50:
        raise MeasureError(f"seulement {len(ok)} adresses exploitables sur "
                           f"{len(panel)} — panel non constituable")

    spans = [r["span_days"] for r in ok]
    firsts = [r["first"] for r in ok]
    lasts = [r["last"] for r in ok]
    require_series("spans", spans, min_n=50)

    # ── CONTRÔLE HÉRITÉ : colonne temporelle dégénérée ────────────────
    degenerate = (min(firsts) == max(firsts)) or (len(set(firsts)) == 1)
    run_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_today = all(x == run_day for x in firsts)
    print(f"\n── Contrôle hérité (colonne temporelle) ──")
    print(f"  'first' : {len(set(firsts))} valeurs distinctes, "
          f"de {min(firsts)} à {max(firsts)}")
    print(f"  'last'  : {len(set(lasts))} valeurs distinctes, "
          f"de {min(lasts)} à {max(lasts)}")
    if degenerate or all_today:
        raise MeasureError("colonne temporelle dégénérée (min == max, ou tout "
                           "à la date du run) — mesure fausse, RUN NUL")
    print("  ✓ non dégénérée")

    # ── distribution des profondeurs ──────────────────────────────────
    spans.sort()
    n_gate = sum(1 for s in spans if s >= GATE_DAYS)
    print(f"\n── Profondeur récupérable ({len(ok)} adresses) ──")
    for q in (0, 10, 25, 50, 75, 90, 100):
        v = spans[min(len(spans) - 1, int(q / 100 * len(spans)))]
        print(f"  p{q:<3d} {v:>8.0f} jours")
    print(f"  ≥ {GATE_DAYS:.0f} jours : {n_gate}/{len(ok)} "
          f"({n_gate / len(ok) * 100:.1f} %)")

    # mur d'API ? les dates de début les plus anciennes
    oldest = sorted(set(firsts))[:12]
    print(f"\n  12 dates de début les plus anciennes du panel : {oldest}")
    c = Counter(firsts)
    print(f"  date de début la plus fréquente : {c.most_common(3)}")

    # granularité : le nombre de points est-il plafonné ?
    npts = sorted(r["n_points"] for r in ok)
    print(f"\n  points par adresse : min {npts[0]} · médiane "
          f"{npts[len(npts)//2]} · max {npts[-1]}")
    print(f"  pas médian : {statistics.median(r['step_median_days'] for r in ok):.2f} "
          f"jours (le pas s'élargit avec l'âge — le nombre de points est plafonné)")

    # ── fills historiques accessibles ? ───────────────────────────────
    print(f"\n── userFillsByTime sur adresse tierce ──")
    probe_addr = max(ok, key=lambda r: r["span_days"])["addr"]
    fills = []
    for lbl, st in (("2025-09", 1756684800000), ("2026-01", 1767225600000),
                    ("2026-05", 1777939200000), ("2026-07", 1782950400000)):
        r_, ms, err = post({"type": "userFillsByTime", "user": probe_addr,
                            "startTime": st, "endTime": st + 14 * 86400000})
        time.sleep(0.25)
        n = len(r_) if isinstance(r_, list) else 0
        fills.append({"window": lbl, "n": n, "err": err})
        print(f"  {lbl} +14 j : n={n:5d}  {err or ''}")
    fills_usable = any(f["n"] > 0 for f in fills)
    print(f"  ⇒ fills historiques tiers : "
          f"{'DISPONIBLES' if fills_usable else 'NON SERVIS'}")

    # ── débit ─────────────────────────────────────────────────────────
    lat.sort()
    rate = len(panel) / elapsed
    print(f"\n── Débit mesuré ──")
    print(f"  {len(panel)} appels en {elapsed:.0f} s → {rate:.1f}/s "
          f"(avec {PAUSE:.2f} s de pause volontaire)")
    print(f"  latence : médiane {lat[len(lat)//2]:.0f} ms · "
          f"p90 {lat[int(.9*len(lat))]:.0f} ms · aucune erreur 429")
    print(f"  coût d'un panel de {PANEL_SIZE} adresses : ~{elapsed:.0f} s")

    # ── GATE ──────────────────────────────────────────────────────────
    median_span = statistics.median(spans)
    gate_pass = median_span >= GATE_DAYS
    print(f"\n{'='*70}")
    print(f"GATE : profondeur médiane {median_span:.0f} j "
          f"{'≥' if gate_pass else '<'} {GATE_DAYS:.0f} j  ⇒  "
          f"{'GO' if gate_pass else 'NO-GO'}")
    print(f"{'='*70}")

    res = {"leaderboard_n": len(rows), "panel_n": len(panel),
           "emitted": len(ok), "gate_days": GATE_DAYS,
           "span_median_days": round(median_span, 1),
           "span_p10": spans[int(.1 * len(spans))],
           "span_p90": spans[int(.9 * len(spans))],
           "span_max": spans[-1],
           "n_above_gate": n_gate, "pct_above_gate": round(n_gate / len(ok) * 100, 1),
           "gate_pass": gate_pass,
           "oldest_starts": oldest,
           "points_min": npts[0], "points_median": npts[len(npts) // 2],
           "points_max": npts[-1],
           "step_median_days": round(statistics.median(
               r["step_median_days"] for r in ok), 3),
           "fills_probe": fills, "fills_usable": fills_usable,
           "rate_per_s": round(rate, 2),
           "latency_median_ms": round(lat[len(lat) // 2], 1),
           "latency_p90_ms": round(lat[int(.9 * len(lat))], 1),
           "panel_seconds": round(elapsed, 1),
           "records": recs,
           "fingerprint": {"git_rev": git_rev(),
                           "run_at": datetime.now(timezone.utc).isoformat()[:16]}}
    out = os.path.join(ROOT, "analysis", "output", "wallet_phase0.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"Dump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
