"""PHASE B — modulateur de taille par agitation du scan. EXÉCUTION UNIQUE.

Implémente **exactement** la spécification de `docs/scan_sizing_verdict.md`,
écrite et committée avant toute exécution (bf79a33, puis la 4ᵉ réserve).

Aucun paramètre réglable. Les constantes ci-dessous SONT la spec.

    mult(n) = 0,50                     si n ≤ 1
            = 0,50 + 0,25 × (n − 1)    si 1 < n < 5
            = 1,50                     si n ≥ 5

Le plafond proportionnel 0,3 × equity s'applique APRÈS modulation
(`cap_after_size_fn=True`), et le plancher $10 est re-vérifié sur la taille
finale.

Le run commence par une **preuve d'inertie en deux jambes** — sans elle, un
écart de moteur passerait pour un effet du modulateur :

  1. chemin par défaut (`size_fn=None`) contre les capitaux de référence
     produits AVANT la modification moteur (`scan_activity_premise.py`,
     commit 8c0de59) ;
  2. nouveau chemin (`cap_after_size_fn=True`) avec un multiplicateur
     constant à 1,0 — il doit rendre les mêmes chiffres.

Un écart sur l'une ou l'autre ⇒ RUN NUL, pas de verdict.

Usage : python3 -m backtests.backtest_scan_sizing
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
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_CAP = 1000.0
OFFSETS = [0, 6, 12, 18]
WINDOW_MONTHS = 28
INERTIA_TOL = 0.01          # $ — au-delà, le run est NUL

# ── LA SPEC (docs/scan_sizing_verdict.md § 2) ───────────────────────────
MULT_FLOOR, MULT_CEIL = 0.50, 1.50
ANCHOR_LO, ANCHOR_HI = 1, 5

TROUGHS = [
    ("A", "2024-08-03", "2024-11-06", "drawdown maximal"),
    ("B", "2024-09-22", "2024-10-21", "pire 30 j"),
    ("C", "2024-08-03", "2024-09-01", "2ᵉ pire 30 j"),
    ("D", "2025-07-21", "2025-08-19", "3ᵉ pire 30 j"),
]


def modulator(n_cands: int) -> float:
    """Rampe linéaire figée — aucune variante."""
    if n_cands <= ANCHOR_LO:
        return MULT_FLOOR
    if n_cands >= ANCHOR_HI:
        return MULT_CEIL
    step = (MULT_CEIL - MULT_FLOOR) / (ANCHOR_HI - ANCHOR_LO)
    return MULT_FLOOR + step * (n_cands - ANCHOR_LO)


def _size_fn(cand, f, n_positions):
    return modulator(int(cand.get("n_cands", 0)))


def _one(cand, f, n_positions):
    return 1.0


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


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
    print(banner(P, data, extra={
        "étude": "M2 phase B — modulateur d'agitation",
        "mult": f"{MULT_FLOOR}@{ANCHOR_LO} → {MULT_CEIL}@{ANCHOR_HI}"}), flush=True)
    print("  table du modulateur : " + "  ".join(
        f"n={n}→×{modulator(n):.2f}" for n in (1, 2, 3, 4, 5, 8)), flush=True)

    def run(s_dt, e_dt, *, fn=None, cap_after=False, audit=None):
        return run_window(features, data, sectors, dxy,
                          start_ts_ms=int(s_dt.timestamp() * 1000),
                          end_ts_ms=int(e_dt.timestamp() * 1000),
                          start_capital=START_CAP, oi_data=oi, funding_data=funding,
                          apply_adaptive_modulator=True, aligned=True,
                          margin_check=True, mfe_on_close=True,
                          realistic_trail_booking=True,
                          size_fn=fn, cap_after_size_fn=cap_after,
                          # INDISPENSABLE : sans ce drapeau, passer un size_fn
                          # laisse `btc_z_map` vide (backtest_rolling.py:575) et
                          # éteint SILENCIEUSEMENT le modulateur macro adaptatif,
                          # le trail proportionnel régime-conditionné, le trail
                          # S8 in-life et le traj_cut. La preuve d'inertie a
                          # attrapé exactement ça au premier run.
                          size_fn_keep_modulator=True,
                          size_audit=audit)

    wins = []
    for off in OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        wins.append((off, s, e))

    # ── PREUVE D'INERTIE ──────────────────────────────────────────────
    ref_path = os.path.join(ROOT, "analysis", "output", "scan_activity_premise.json")
    with open(ref_path) as f:
        ref = {w["offset"]: w["end_capital"]
               for w in json.load(f)["doctrine_windows"]}
    print(f"\n═══ PREUVE D'INERTIE (référence : {os.path.basename(ref_path)}, "
          f"produite avant la modification moteur) ═══")
    base, inertia_ok = {}, True
    for off, s, e in wins:
        r0 = run(s, e)                                   # jambe 1 : défaut
        r1 = run(s, e, fn=_one, cap_after=True)          # jambe 2 : nouveau chemin
        base[off] = r0
        d0 = abs(r0["end_capital"] - ref[off])
        d1 = abs(r1["end_capital"] - r0["end_capital"])
        ok = d0 <= INERTIA_TOL and d1 <= INERTIA_TOL
        inertia_ok &= ok
        print(f"  OOS-{off:<2d} réf ${ref[off]:>9,.2f} | défaut "
              f"${r0['end_capital']:>9,.2f} (Δ {d0:.4f}) | mult=1,0 "
              f"${r1['end_capital']:>9,.2f} (Δ {d1:.4f})  "
              f"{'✓' if ok else '✗ ÉCART'}")
    if not inertia_ok:
        raise MeasureError(
            "preuve d'inertie EN ÉCHEC — la modification moteur n'est pas "
            "neutre. RUN NUL, pas de verdict (docs/scan_sizing_verdict.md § 5).")
    print("  ✓ les deux jambes passent — la modification moteur est inerte")

    # ── LE TEST ───────────────────────────────────────────────────────
    print("\n═══ VERDICT — walk-forward glissant non chevauchant ═══")
    rows, audits = [], []
    for off, s, e in wins:
        aud = []
        r = run(s, e, fn=_size_fn, cap_after=True, audit=aud)
        b = base[off]
        d = r["end_capital"] - b["end_capital"]
        rows.append({
            "offset": off, "start": f"{s:%Y-%m-%d}", "end": f"{e:%Y-%m-%d}",
            "base_capital": round(b["end_capital"], 2),
            "mod_capital": round(r["end_capital"], 2),
            "delta_pnl": round(d, 2),
            "base_dd": round(b["max_dd_pct"], 2), "mod_dd": round(r["max_dd_pct"], 2),
            "base_n": b["n_trades"], "mod_n": r["n_trades"],
            "pass": d > 0})
        audits.append((off, aud))
        c = rows[-1]
        print(f"  OOS-{off:<2d} {c['start']}→{c['end']}  "
              f"base ${c['base_capital']:>9,.2f} → mod ${c['mod_capital']:>9,.2f}  "
              f"Δ {c['delta_pnl']:>+9,.2f}  "
              f"DD {c['base_dd']:>6.1f}%→{c['mod_dd']:>6.1f}%  "
              f"n {c['base_n']}→{c['mod_n']}  {'✓' if c['pass'] else '✗'}")

    n_pass = sum(1 for r in rows if r["pass"])
    verdict = "CANDIDAT À DÉPLOIEMENT" if n_pass == 4 else "REFUS"
    print(f"\n  → {n_pass}/4 en P&L  ⇒  **{verdict}**")

    # ── asymétrie du cap (4ᵉ réserve) ─────────────────────────────────
    print("\n═══ ASYMÉTRIE DU CAP — 4ᵉ réserve ═══")
    asym = {}
    for off, aud in audits:
        if not aud:
            asym[off] = {"emitted": False}
            continue
        boosted = [a for a in aud if a["n_cands"] > ANCHOR_LO]
        b_cap = [a for a in boosted if a["capped"]]
        pen = [a for a in aud if a["n_cands"] <= ANCHOR_LO]
        p_cap = [a for a in pen if a["capped"]]
        asym[off] = {
            "emitted": True, "n_entries": len(aud),
            "n_boosted": len(boosted), "n_boosted_capped": len(b_cap),
            "pct_boosted_capped": round(len(b_cap) / len(boosted) * 100, 1)
            if boosted else None,
            "n_penalised": len(pen), "n_penalised_capped": len(p_cap),
            "n_floored": sum(1 for a in aud if a["floored"]),
            "notional_lost_to_cap": round(
                sum(a["pre_cap"] - a["post_cap"] for a in b_cap), 0)}
        v = asym[off]
        print(f"  OOS-{off:<2d} {v['n_entries']:>4d} entrées | boostées "
              f"{v['n_boosted']:>4d} dont écrêtées {v['n_boosted_capped']:>4d} "
              f"({v['pct_boosted_capped']}%) | pénalisées {v['n_penalised']:>4d} "
              f"dont écrêtées {v['n_penalised_capped']:>3d} | "
              f"planchées {v['n_floored']:>3d} | "
              f"notionnel perdu au cap ${v['notional_lost_to_cap']:>10,.0f}")

    # ── creux : rapporté, ne participe pas au verdict ─────────────────
    print("\n═══ FENÊTRES DE CREUX — rapporté, HORS verdict ═══")
    long_b = run(end_dt - relativedelta(months=WINDOW_MONTHS), end_dt)
    long_m = run(end_dt - relativedelta(months=WINDOW_MONTHS), end_dt,
                 fn=_size_fn, cap_after=True)
    tr = []
    for name, s, e, why in TROUGHS:
        pb = sum(t["pnl"] for t in long_b["trades"] if s <= _day(t["exit_t"]) <= e)
        pm = sum(t["pnl"] for t in long_m["trades"] if s <= _day(t["exit_t"]) <= e)
        tr.append({"name": name, "dates": f"{s}→{e}", "why": why,
                   "base_pnl": round(pb, 2), "mod_pnl": round(pm, 2),
                   "delta": round(pm - pb, 2)})
        c = tr[-1]
        print(f"  creux {name} {c['dates']}  base ${c['base_pnl']:>+9,.0f}  "
              f"mod ${c['mod_pnl']:>+9,.0f}  Δ {c['delta']:>+9,.0f}")
    print(f"\n  28 mois : base ${long_b['end_capital']:>10,.0f} "
          f"(DD {long_b['max_dd_pct']:.1f}%)  →  mod "
          f"${long_m['end_capital']:>10,.0f} (DD {long_m['max_dd_pct']:.1f}%)  "
          f"Δ ${long_m['end_capital'] - long_b['end_capital']:>+10,.0f}")

    res = {"spec": {"floor": MULT_FLOOR, "ceil": MULT_CEIL,
                    "anchor_lo": ANCHOR_LO, "anchor_hi": ANCHOR_HI,
                    "table": {n: modulator(n) for n in range(1, 9)}},
           "inertia_ok": inertia_ok, "reference": ref,
           "windows": rows, "n_pass": n_pass, "verdict": verdict,
           "cap_asymmetry": asym, "troughs": tr,
           "long_28m": {"base_capital": round(long_b["end_capital"], 2),
                        "mod_capital": round(long_m["end_capital"], 2),
                        "base_dd": round(long_b["max_dd_pct"], 2),
                        "mod_dd": round(long_m["max_dd_pct"], 2),
                        "base_n": long_b["n_trades"], "mod_n": long_m["n_trades"]},
           "fingerprint": fingerprint(P, data, extra={"étude": "scan_sizing_phase_b"})}
    out = os.path.join(ROOT, "analysis", "output", "scan_sizing_verdict.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\n{'='*70}\nVERDICT : {verdict}  ({n_pass}/4)\n{'='*70}\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
