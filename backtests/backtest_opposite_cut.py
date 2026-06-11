"""R&D — théorie utilisateur (2026-06-11, cas CRV SENIOR LONG × JUNIOR S9 SHORT) :
« si un signal contradictoire apparaît alors que la position est gagnante,
n'est-il pas plus intéressant de la couper ? »

Historique : l'inverse-exit sur signal opposé avait été testé pré-phase 6
(`backtest_exit_battery.py` → +$20k sur 28m mais perdant 12m/6m/3m, overfit).
Re-test ici en SÉMANTIQUE ALIGNÉE avec le hook `opposite_cut` de run_window :
signal de direction opposée détecté au close T sur un token détenu, position
gagnante (ur ≥ min_gain) → cut à l'open de T+1 (même timing d'exécution
qu'une entrée prise sur ce signal).

Grille : min_gain ∈ {0, +300, +800 bps} × held ∈ {S5 seul, toutes strats}.
Gate ship : ΔPnL ≥ 0 sur les 4 fenêtres ET ΔDD moyen ≥ −2pp. Résultats en $.

Usage : python3 -m backtests.backtest_opposite_cut
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

WINDOWS = [
    ("28m", "2024-02-04"),
    ("12m", "2025-06-04"),
    ("6m",  "2025-12-04"),
    ("3m",  "2026-03-04"),
]
START_CAP = 500.0

GRID = [
    ("cut_gain0_S5",    {"min_gain_bps": 0.0,   "held_strats": {"S5"}}),
    ("cut_gain300_S5",  {"min_gain_bps": 300.0, "held_strats": {"S5"}}),
    ("cut_gain800_S5",  {"min_gain_bps": 800.0, "held_strats": {"S5"}}),
    ("cut_gain0_ALL",   {"min_gain_bps": 0.0,   "held_strats": None}),
    ("cut_gain300_ALL", {"min_gain_bps": 300.0, "held_strats": None}),
    ("cut_gain800_ALL", {"min_gain_bps": 800.0, "held_strats": None}),
]


def main() -> int:
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    ms = lambda d: int(datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)

    results: dict[str, dict[str, dict]] = {}
    for name, oc in [("baseline", None)] + GRID:
        results[name] = {}
        for win, start in WINDOWS:
            t0 = time.time()
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=ms(start), end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True,
                           opposite_cut=oc, aligned=True)
            n_cuts = sum(1 for t in r["trades"] if t["reason"] == "opposite_cut")
            cut_pnl = sum(t["pnl"] for t in r["trades"] if t["reason"] == "opposite_cut")
            r["n_cuts"], r["cut_pnl"] = n_cuts, cut_pnl
            results[name][win] = r
            print(f"  {name:16s} {win:4s} → ${r['end_capital']:9.0f} "
                  f"(DD {r['max_dd_pct']:5.1f}%, {n_cuts} cuts) [{time.time()-t0:.0f}s]")

    base = results["baseline"]
    print("\n" + "=" * 100)
    hdr = f"  {'config':16s}" + "".join(f"{w:>17s}" for w, _ in WINDOWS) + "    4/4   ΔDD moy"
    print(hdr)
    print(f"  {'baseline ($)':16s}" + "".join(
        f"{base[w]['end_capital']:>14.0f}   " for w, _ in WINDOWS))
    for name, _ in GRID:
        r = results[name]
        ok, dds = True, []
        cells = ""
        for w, _w in WINDOWS:
            d = r[w]["end_capital"] - base[w]["end_capital"]
            dds.append(r[w]["max_dd_pct"] - base[w]["max_dd_pct"])
            if d < -0.01:
                ok = False
            cells += f"{d:>+13.0f}$   "
        avg_dd = sum(dds) / len(dds)
        gate = "PASS" if (ok and avg_dd >= -2.0) else "fail"
        print(f"  {name:16s}{cells} {gate:>5s}  {avg_dd:+5.2f}pp")
    print("\n  Δ en DOLLARS vs baseline (départ $500 par fenêtre) ; ΔDD>0 = DD améliorée")
    print("  cuts par fenêtre (config, fenêtre, n, P&L des trades coupés) :")
    for name, _ in GRID:
        cuts = "  ".join(f"{w}:{results[name][w]['n_cuts']}(${results[name][w]['cut_pnl']:+.0f})"
                         for w, _w in WINDOWS)
        print(f"    {name:16s} {cuts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
