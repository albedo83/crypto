"""Vague 1 — test 7 : HLP comme destination du capital (allocation statique).

HLP : historique public `vaultDetails` (portfolio allTime, un point / ~14 j).
Rendement HLP d'un intervalle = Δ pnlHistory / accountValue du début (neutralise
dépôts et retraits). Moteur : backtest aligned continu (S1, S8, S9, S10) à
1 000 $, equity réalisée aux mêmes dates. Allocations 0 / 25 / 50 % dans HLP,
rééquilibrées à chaque intervalle. Mesure : rendement total / |DD max|, sur toute
la période commune et sur 2026. Le DD sur points espacés de 14 j sous-estime le
DD réel, également pour les deux séries.

    nice -n 19 python3 -m backtests.vague1_hlp
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from backtests.backtest_sector import compute_sector_features

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = timezone.utc
START = datetime(2024, 4, 19, tzinfo=UTC)
Y2026 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def main():
    d = json.load(open(os.path.join(HERE, "output", "hlp_vault_details.json")))
    p = dict(d["portfolio"])["allTime"]
    av = {int(t): float(v) for t, v in p["accountValueHistory"]}
    pn = {int(t): float(v) for t, v in p["pnlHistory"]}
    ts = sorted(t for t in av if t in pn and t >= int(START.timestamp() * 1000))

    data = load_3y_candles()
    f = build_features(data)
    end = max(c["t"] for c in data["BTC"])
    r = run_window(f, data, compute_sector_features(f, data), load_dxy(), int(START.timestamp() * 1000), end,
                   start_capital=1000.0, oi_data=load_oi(), funding_data=load_funding(),
                   apply_adaptive_modulator=True, aligned=True, margin_check=True,
                   skip_fn=lambda c, t, s, dr: s == "S5")
    ex = sorted((t["exit_t"], t["pnl"]) for t in r["trades"])
    eq_at = lambda T: 1000.0 + sum(x for t, x in ex if t <= T)
    ts = [t for t in ts if t <= end]
    hlp, alf = [], []
    for a, b in zip(ts, ts[1:]):
        hlp.append((pn[b] - pn[a]) / av[a] if av[a] > 0 else 0.0)
        ea, eb = eq_at(a), eq_at(b)
        alf.append(eb / ea - 1 if ea > 0 else 0.0)
    hlp, alf = np.array(hlp), np.array(alf)
    is26 = np.array([b >= Y2026 for b in ts[1:]])
    print(f"{len(hlp)} intervalles (~14 j) du {datetime.fromtimestamp(ts[0]/1000, UTC):%Y-%m-%d} "
          f"au {datetime.fromtimestamp(ts[-1]/1000, UTC):%Y-%m-%d} ; corrélation HLP/moteur "
          f"{np.corrcoef(hlp, alf)[0, 1]:+.2f}")

    def metric(x):
        eq = np.cumprod(1 + x)
        dd = (eq / np.maximum.accumulate(eq) - 1).min()
        return eq[-1] - 1, dd, (eq[-1] - 1) / abs(dd) if dd < 0 else float("inf")

    res = {}
    print(f"{'allocation HLP':<16}{'total':>9}{'DD':>8}{'ratio':>7}   {'2026 total':>11}{'DD':>8}{'ratio':>7}")
    for w in (0.0, 0.25, 0.5, 1.0):
        x = w * hlp + (1 - w) * alf
        a, b = metric(x), metric(x[is26])
        res[w] = (a, b)
        print(f"{w:>14.0%}  {a[0]:>+8.1%}{a[1]:>8.1%}{a[2]:>7.2f}   {b[0]:>+10.1%}{b[1]:>8.1%}{b[2]:>7.2f}")
    ok = any(res[w][0][2] > res[0.0][0][2] and res[w][1][2] > res[0.0][1][2] for w in (0.25, 0.5))
    print(f"→ {'VALIDÉ' if ok else 'REJETÉ'} (25 % ou 50 % doit battre le moteur seul sur toute la période ET sur 2026)")


if __name__ == "__main__":
    main()
