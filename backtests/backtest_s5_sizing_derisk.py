"""S5 : réduire la MISE plutôt que retirer le signal.

Contexte (2026-07-30) : S5 a rapporté +$780 sur 4 semestres (2024-S1 → 2025-S2)
puis perdu $3065 sur 2026, avec un taux de réussite qui décroît continûment
(50 % → 41 % → 33 %). Le live confirme (S5 LONG 40 % de WR depuis le reset).

Le retrait est refusé : il gagne dans la seule fenêtre où S5 est cassé et perd
dans les trois où il fonctionnait (cf. backtest_s5_oos_and_decay.py).

Or S5 porte le coefficient de taille le PLUS ÉLEVÉ du bot — `signal_mult` :
S1 1.0, S8 1.25, S9 2.0, S10 2.0, **S5 3.0**. Calibré le 2026-07-05, soit vingt
jours AVANT la découverte du biais de booking des trails.

Réduire la mise n'enlève aucun trade, ne libère aucun slot, ne modifie pas les
cooldowns : le chemin de compounding est préservé, seule l'exposition change.
C'est le levier à plus faible dépendance au chemin disponible.

LECTURE HONNÊTE : 3 des 4 fenêtres OOS couvrent la période où S5 fonctionnait.
Une réduction y perdra mécaniquement. Le 4/4 strict n'est PAS le bon critère ici
— on le rapporte quand même, et on rapporte SÉPARÉMENT l'effet dans la fenêtre
cassée. Ce n'est pas du cherry-picking tant que les deux sont affichés.

Usage : python3 -m backtests.backtest_s5_sizing_derisk
"""

from __future__ import annotations

import dataclasses as dc
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

START_CAP = 500.0
MULTS = [3.0, 2.5, 2.0, 1.5, 1.0, 0.5]


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()

    wins = []
    for off in (0, 6, 12, 18):
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        wins.append((f"OOS-{off}", int(s.timestamp() * 1000),
                     int(e.timestamp() * 1000), f"{s:%Y-%m}→{e:%Y-%m}",
                     off == 0))
    # fenêtre « régime cassé » : 2026 complet, là où la rupture est établie
    s26 = datetime(2026, 1, 1).astimezone()
    wins.append(("2026", int(s26.timestamp() * 1000), end_ms, "2026-01→07", True))
    # fenêtre « régime sain » : tout ce qui précède 2026
    s24 = end_dt - relativedelta(months=28)
    wins.append(("pré-2026", int(s24.timestamp() * 1000),
                 int(s26.timestamp() * 1000), "2024-03→2026-01", False))

    print("Fenêtres :")
    for n, _, _, lab, broken in wins:
        print(f"  {n:9s} {lab:18s} {'[régime cassé]' if broken else ''}")

    def run(m):
        sm = dict(DEFAULT_PARAMS.signal_mult)
        sm["S5"] = m
        br._P = dc.replace(DEFAULT_PARAMS, signal_mult=sm)
        out = {}
        for name, a, b, _l, _br in wins:
            r = run_window(features, data, sectors, dxy, start_ts_ms=a, end_ts_ms=b,
                           start_capital=START_CAP, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            per = defaultdict(float)
            for t in r["trades"]:
                per[t["strat"]] += t.get("pnl", 0.0)
            out[name] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                         "n": r["n_trades"], "pnl_s5": round(per["S5"], 2)}
        return out

    t0 = time.time()
    res = {m: run(m) for m in MULTS}
    base = res[3.0]

    names = [n for n, _, _, _, _ in wins]
    print(f"\n=== capital final par coefficient de taille S5")
    print(f"  {'mult':>6s}" + "".join(f"{n:>11s}" for n in names))
    for m in MULTS:
        print(f"  {m:>6.1f}" + "".join(f"{res[m][n]['end']:>11.0f}" for n in names))

    print(f"\n=== Δ$ vs actuel (3.0)   [+ = la réduction rapporte]")
    print(f"  {'mult':>6s}" + "".join(f"{n:>11s}" for n in names)
          + f"{'OOS +':>8s}{'ΔDD OOS-0':>11s}")
    for m in MULTS:
        if m == 3.0:
            continue
        d = {n: res[m][n]["end"] - base[n]["end"] for n in names}
        oos = [n for n, _, _, _, _ in wins if n.startswith("OOS")]
        npos = sum(1 for n in oos if d[n] > 0)
        ddd = base["OOS-0"]["dd"] - res[m]["OOS-0"]["dd"]
        print(f"  {m:>6.1f}" + "".join(f"{d[n]:>+11.0f}" for n in names)
              + f"{npos:>5d}/4{ddd:>+11.1f}")

    print(f"\n=== drawdown max par coefficient")
    print(f"  {'mult':>6s}" + "".join(f"{n:>11s}" for n in names))
    for m in MULTS:
        print(f"  {m:>6.1f}" + "".join(f"{res[m][n]['dd']:>10.1f}%" for n in names))

    print(f"\n=== P&L de S5 seul, par coefficient (contrôle de linéarité)")
    print(f"  {'mult':>6s}" + "".join(f"{n:>11s}" for n in names))
    for m in MULTS:
        print(f"  {m:>6.1f}" + "".join(f"{res[m][n]['pnl_s5']:>+11.0f}" for n in names))

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_sizing_derisk.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "mults": MULTS,
                   "results": {str(k): v for k, v in res.items()}},
                  f, indent=1, default=str)
    print(f"\n[{time.time()-t0:.0f}s]  Dump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
