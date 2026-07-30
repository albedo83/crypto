"""S5 : retrait sur fenêtres OOS GLISSANTES + dégradation dans le temps.

Deux manques du premier passage (2026-07-30), relevés par l'utilisateur :

1. Le retrait de S5 n'a été testé que sur des fenêtres EMBOÎTÉES (28m/12m/6m/3m,
   même date de fin, chacune contenant les suivantes). Le verdict « 2/4 » est
   donc dominé par la fenêtre 28m, qui contient les trois autres. Le protocole
   correct — utilisé pour les autres hypothèses de la campagne — est 4 fenêtres
   OOS de 6 mois GLISSANTES et NON CHEVAUCHANTES.

2. Personne n'a regardé si S5 se DÉGRADE. Un signal positif tôt et négatif tard
   affiche une somme négative tout en ayant fait grossir le capital au bon
   moment — ce qui expliquerait entièrement le paradoxe « il perd mais le
   retirer coûte ». On mesure donc son net_bps (neutre en taille) par semestre.

Usage : python3 -m backtests.backtest_s5_oos_and_decay
"""

from __future__ import annotations

import json
import os
import statistics as stats
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses as dc
import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS
import alfred.signals as _alf_signals

START_CAP = 500.0
OFFSETS = [0, 6, 12, 18]
OTHERS = frozenset({"S1", "S8", "S9", "S10"})
_ORIG = _alf_signals.detect_token_signals


def _drop(dirs):
    def wrapped(*a, **k):
        return [s for s in _ORIG(*a, **k)
                if not (s["strategy"] == "S5" and s["direction"] in dirs)]
    return wrapped


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS

    wins = []
    for off in OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        wins.append((f"OOS-{off}", int(s.timestamp() * 1000),
                     int(e.timestamp() * 1000), f"{s:%Y-%m}→{e:%Y-%m}"))

    def run(label, dirs=None, keep=False):
        _alf_signals.detect_token_signals = (_drop(dirs) if dirs else _ORIG)
        br._alf_signals.detect_token_signals = _alf_signals.detect_token_signals
        out = {}
        for name, a, b, _l in wins:
            r = run_window(features, data, sectors, dxy, start_ts_ms=a, end_ts_ms=b,
                           start_capital=START_CAP, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            out[name] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                         "n": r["n_trades"]}
            if keep:
                out[name]["trades"] = r["trades"]
        return out

    print("\n" + "=" * 78)
    print("PARTIE 1 — retrait de S5 sur 4 fenêtres OOS GLISSANTES non chevauchantes")
    for n, _, _, lab in wins:
        print(f"  {n:8s} {lab}")
    t0 = time.time()
    base = run("base")
    variants = {
        "sans S5 entier": {1, -1},
        "sans S5 LONG":   {1},
        "sans S5 SHORT":  {-1},
    }
    print(f"\n  {'':18s}" + "".join(f"{n:>12s}" for n, _, _, _ in wins))
    print(f"  {'référence':18s}"
          + "".join(f"{base[n]['end']:>12.0f}" for n, _, _, _ in wins))
    res = {}
    print(f"\n  {'Δ$ (+ = retrait gagnant)':18s}"
          + "".join(f"{n:>12s}" for n, _, _, _ in wins)
          + f"{'P&L +':>8s}" + "".join(f"{'ΔDD':>8s}" for _ in wins) + f"{'DD ok':>7s}")
    for k, dirs in variants.items():
        r = run(k, dirs)
        res[k] = r
        d = {n: r[n]["end"] - base[n]["end"] for n, _, _, _ in wins}
        dd = {n: base[n]["dd"] - r[n]["dd"] for n, _, _, _ in wins}
        npos = sum(1 for v in d.values() if v > 0)
        nddok = sum(1 for v in dd.values() if v >= -2.0)
        print(f"  {k:18s}" + "".join(f"{d[n]:>+12.0f}" for n, _, _, _ in wins)
              + f"{npos:>5d}/4" + "".join(f"{dd[n]:>+8.1f}" for n, _, _, _ in wins)
              + f"{nddok:>5d}/4")
    _alf_signals.detect_token_signals = _ORIG

    print("\n" + "=" * 78)
    print("PARTIE 2 — S5 se dégrade-t-il ? net_bps par semestre (neutre en taille)")
    s28 = end_dt - relativedelta(months=28)
    r = run_window(features, data, sectors, dxy,
                   start_ts_ms=int(s28.timestamp() * 1000), end_ts_ms=end_ms,
                   start_capital=START_CAP, oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True,
                   margin_check=True, mfe_on_close=True,
                   realistic_trail_booking=True)
    per = defaultdict(lambda: defaultdict(list))
    for t in r["trades"]:
        dt_ = datetime.fromtimestamp(t["entry_t"] / 1000, timezone.utc)
        sem = f"{dt_.year}-S{1 if dt_.month <= 6 else 2}"
        per[t["strat"]][sem].append(t)
    sems = sorted({s for v in per.values() for s in v})
    print(f"\n  {'semestre':10s}" + "".join(f"{s:>22s}" for s in ("S5", "autres")))
    print(f"  {'':10s}" + "".join(f"{'n':>6s}{'WR':>7s}{'net moy':>9s}"
                                  for _ in range(2)))
    decay = {}
    for sem in sems:
        s5 = per["S5"].get(sem, [])
        oth = [t for k, v in per.items() if k != "S5" for t in v.get(sem, [])]
        def fmt(v):
            if len(v) < 5:
                return f"{len(v):>6d}{'—':>7s}{'—':>9s}"
            wr = sum(1 for t in v if t["pnl"] > 0) / len(v) * 100
            return f"{len(v):>6d}{wr:>6.1f}%{stats.mean(t['net'] for t in v):>9.1f}"
        print(f"  {sem:10s}{fmt(s5)}{fmt(oth)}")
        decay[sem] = {
            "s5_n": len(s5),
            "s5_net": round(stats.mean([t["net"] for t in s5]), 1) if len(s5) >= 5 else None,
            "s5_pnl": round(sum(t["pnl"] for t in s5), 2),
            "others_n": len(oth),
            "others_net": round(stats.mean([t["net"] for t in oth]), 1) if len(oth) >= 5 else None,
        }

    print("\n  P&L $ de S5 par semestre (là où il a fait grossir ou fondre le capital)")
    cum = 0.0
    for sem in sems:
        v = decay[sem]["s5_pnl"]
        cum += v
        print(f"  {sem:10s} {v:>+10.0f}   cumulé {cum:>+10.0f}")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_oos_and_decay.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "base": base, "variants": res, "decay": decay},
                  f, indent=1, default=str)
    print(f"\n[{time.time()-t0:.0f}s]  Dump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
