"""Vague 1 — test 5 : comportement du prix autour des règlements de funding Binance.

Événements = horodatages RÉELS de règlement (fichiers fundingRate ; 8 h pour la
plupart des tokens, 4 h pour certains — précision de la grille, qui disait
0/8/16 h). Signal ex ante : taux du règlement PRÉCÉDENT ; si |taux| ≥ 1 bp,
position = −signe(taux). Bougies 1 min Binance. Coût 13 bps ; une position qui
traverse le règlement encaisse/paie le funding réel de ce règlement.
Découverte janvier → juin 2026, validation juillet → octobre.

    nice -n 19 python3 -m backtests.vague1_funding_hours
"""
from __future__ import annotations

import glob
import math
import os
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from alfred.settings import DEFAULT_PARAMS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "binance")
SPLIT = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
VARIANTS = [(-60, 0), (-60, 30), (-60, 60), (-30, 0), (-30, 30), (-30, 60), (0, 60), (0, 120)]
COST, THR = 13.0, 1e-4


def _csv(path, cols):
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        return pd.read_csv(fh, usecols=cols)


def closes(sym):
    parts = [_csv(p, ["open_time", "close"]) for p in sorted(glob.glob(f"{ROOT}/klines/{sym}-1m-*.zip"))]
    d = pd.concat(parts).drop_duplicates("open_time")
    return pd.Series(d["close"].to_numpy(), index=(d["open_time"].to_numpy() // 60000) * 60000).sort_index()


def fundings(sym):
    parts = [_csv(p, ["calc_time", "last_funding_rate"]) for p in sorted(glob.glob(f"{ROOT}/fundingRate/{sym}-fundingRate-*.zip"))]
    d = pd.concat(parts).sort_values("calc_time")
    return d["calc_time"].to_numpy(), d["last_funding_rate"].to_numpy()


def stat(tr):
    if len(tr) < 3:
        return len(tr), 0.0, 0.0, 0.0
    by = {}
    for day, g, n in tr:
        by.setdefault(day, []).append(n)
    m = np.array([np.mean(v) for v in by.values()])
    t = m.mean() / (m.std(ddof=1) / math.sqrt(len(m))) if len(m) > 2 and m.std(ddof=1) > 0 else 0.0
    return len(tr), float(np.mean([g for _, g, _ in tr])), float(np.mean([n for _, _, n in tr])), float(t)


def main():
    res = {v: ([], []) for v in VARIANTS}
    coins = sorted(DEFAULT_PARAMS.trade_symbols)
    n_ev = 0
    for coin in coins:
        sym = f"{coin}USDT"
        if not glob.glob(f"{ROOT}/fundingRate/{sym}-*.zip"):
            continue
        px = closes(sym)
        ft, fr = fundings(sym)
        idx = px.index.to_numpy()
        vals = px.to_numpy()

        def at(t):
            i = np.searchsorted(idx, t, side="right") - 1
            return vals[i] if i >= 0 and t - idx[i] <= 120000 else np.nan

        for i in range(1, len(ft)):
            prev, cur = fr[i - 1], fr[i]
            if abs(prev) < THR:
                continue
            T = (int(ft[i]) // 60000) * 60000
            d = -1 if prev > 0 else 1
            n_ev += 1
            for a, b in VARIANTS:
                p0, p1 = at(T + a * 60000), at(T + b * 60000)
                if not (p0 > 0 and p1 > 0):
                    continue
                gross = d * (p1 / p0 - 1) * 1e4
                fund = (-d * cur * 1e4) if (a < 0 < b or (a < 0 and b == 0 and False)) else 0.0
                net = gross + fund - COST
                (res[(a, b)][0] if T < SPLIT else res[(a, b)][1]).append((T // 86400000, gross + fund, net))
        print(f"  {coin}: ok", flush=True)
    print(f"\n{n_ev} règlements avec |taux précédent| ≥ 1 bp, {len(coins)} tokens")
    print(f"{'entrée→sortie (min)':<20}| {'disc n':>7}{'brut':>7}{'net':>7}{'t':>6} | {'valid n':>7}{'brut':>7}{'net':>7}{'t':>6}")
    for v, (dsc, vl) in res.items():
        a, b = stat(dsc), stat(vl)
        ok = a[3] >= 3 and b[2] > 0 and b[3] >= 1.5
        print(f"T{v[0]:+d} → T{v[1]:+d}".ljust(20) + f"| {a[0]:>7}{a[1]:>+7.1f}{a[2]:>+7.1f}{a[3]:>6.2f} | "
              f"{b[0]:>7}{b[1]:>+7.1f}{b[2]:>+7.1f}{b[3]:>6.2f}" + ("  ← VALIDÉ" if ok else ""))


if __name__ == "__main__":
    main()
