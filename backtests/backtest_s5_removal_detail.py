"""Retirer S5 : les AUTRES signaux en profitent-ils ?

Question utilisateur (2026-07-30) : « si S5 perd, pourquoi le garder, les autres
pourraient en bénéficier ». L'ablation globale dit que le retrait coûte sur 28m
et rapporte sur 6m/3m, mais elle ne dit pas POURQUOI. Ici on ouvre la boîte :
pour chaque signal restant, nombre de trades et P&L, avec S5 et sans S5.

Trois issues possibles :
  - les autres prennent PLUS de trades et gagnent PLUS -> les slots étaient
    bien la contrainte, l'intuition est juste ;
  - les autres prennent les MÊMES trades -> les slots n'étaient pas saturés,
    retirer S5 ne fait que soustraire son P&L (et l'écart vient du compounding) ;
  - les autres prennent plus de trades et gagnent MOINS -> S5 bloquait des
    entrées perdantes (cooldown/slot), effet protecteur non intentionnel.

Usage : python3 -m backtests.backtest_s5_removal_detail
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses as dc
import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
START_CAP = 500.0
OTHERS = ["S1", "S8", "S9", "S10"]


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()

    def run(params):
        br._P = params
        out = {}
        for w, months in WINDOWS:
            s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            per = defaultdict(lambda: [0, 0.0])
            for t in r["trades"]:
                k = per[t.get("strat")]
                k[0] += 1
                k[1] += t.get("pnl", 0.0)
            out[w] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                      "n": r["n_trades"],
                      "per": {k: {"n": v[0], "pnl": round(v[1], 2)}
                              for k, v in per.items()}}
        return out

    t0 = time.time()
    base = run(DEFAULT_PARAMS)
    nos5 = run(dc.replace(DEFAULT_PARAMS,
                          enabled_strategies=frozenset(OTHERS)))
    print(f"[{time.time()-t0:.0f}s]\n")

    for w, _ in WINDOWS:
        b, n = base[w], nos5[w]
        print(f"=== {w}   capital final : avec S5 ${b['end']:.0f}  →  "
              f"sans S5 ${n['end']:.0f}   ({n['end']-b['end']:+.0f})")
        print(f"  {'signal':7s} {'n avec':>7s} {'n sans':>7s} {'Δn':>6s}"
              f" {'P&L avec':>10s} {'P&L sans':>10s} {'ΔP&L':>10s}")
        d_others = 0.0
        for s in OTHERS:
            bs = b["per"].get(s, {"n": 0, "pnl": 0.0})
            ns = n["per"].get(s, {"n": 0, "pnl": 0.0})
            d_others += ns["pnl"] - bs["pnl"]
            print(f"  {s:7s} {bs['n']:>7d} {ns['n']:>7d} {ns['n']-bs['n']:>+6d}"
                  f" {bs['pnl']:>10.0f} {ns['pnl']:>10.0f}"
                  f" {ns['pnl']-bs['pnl']:>+10.0f}")
        s5 = b["per"].get("S5", {"n": 0, "pnl": 0.0})
        print(f"  {'S5':7s} {s5['n']:>7d} {0:>7d} {-s5['n']:>+6d}"
              f" {s5['pnl']:>10.0f} {0:>10.0f} {-s5['pnl']:>+10.0f}")
        print(f"  → S5 retiré rend {-s5['pnl']:+.0f} de son propre P&L, "
              f"et les autres font {d_others:+.0f}")
        print(f"    somme des effets P&L : {-s5['pnl']+d_others:+.0f}   "
              f"écart de capital final : {n['end']-b['end']:+.0f}"
              f"   (différence = compounding)\n")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_removal_detail.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "base": base, "sans_s5": nos5}, f, indent=1, default=str)
    print(f"Dump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
