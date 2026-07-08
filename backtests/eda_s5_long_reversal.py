"""EDA — pourquoi les S5 LONG se retournent ? (2026-07-08)

Déclencheur : S5 LONG saigne en live (WR 62% mais −$73/14j, perdants
catastrophiques : DYDX −51, ADA −30…). L'utilisateur : « la cause est la
solution ». On cherche le séparateur à l'ENTRÉE entre un S5 LONG qui part en
catastrophe et un qui marche.

Hypothèse principale (S5 = suivre la divergence sectorielle + momentum aligné) :
le signal exige que le token MONTE déjà → S5 LONG achète du momentum, et un
momentum DÉJÀ ÉTIRÉ mean-reverte (on achète le sommet). Le séparateur serait
alors « à quel point le token a déjà couru » (ret_42h / ret_7d à l'entrée).

On teste plusieurs features à l'entrée, ventilées par issue.
Usage : python3 -m backtests.eda_s5_long_reversal
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from backtests.backtest_genetic import load_3y_candles, build_features

OUT = os.path.join(os.path.dirname(__file__), "output")
TRADES_F = os.path.join(OUT, "exit_ablation_base_trades.json")

# features candidates (dans build_features) : ret récents, vol, dispersion,
# force vs BTC/secteur — tout ce qui décrit l'état d'entrée.
FEATS = ["ret_42h", "ret_84h", "ret_180h", "vol_7d", "vol_ratio",
         "dispersion_7d", "alt_vs_btc_7d", "alt_vs_btc_30d", "consec_up",
         "range_pct", "drawdown"]


def main():
    data = load_3y_candles()
    features = build_features(data)   # {coin: [rows sorted by t]}
    # index par (coin, t) pour lookup
    idx = {}
    for coin, rows in features.items():
        for r in rows:
            idx[(coin, r["t"])] = r

    trades = json.load(open(TRADES_F))["28m"]
    s5l = [t for t in trades if t["strat"] == "S5" and t["dir"] == 1]
    # enrichir : trouver la row feature au t d'entrée (bougie du signal)
    enriched = []
    for t in s5l:
        # entry_t est le ms de l'entrée (open bougie suivante) → la feature du
        # signal est à la bougie PRÉCÉDENTE (close). On cherche la row la plus
        # proche ≤ entry_t.
        coin = t["coin"]
        rows = features.get(coin)
        if not rows:
            continue
        import bisect
        ts_list = [r["t"] for r in rows]
        i = bisect.bisect_right(ts_list, t["entry_t"]) - 1
        if i < 0:
            continue
        f = rows[i]
        enriched.append({**t, **{k: f.get(k) for k in FEATS}})

    print(f"S5 LONG : {len(s5l)} trades, {len(enriched)} enrichis\n")

    # buckets d'issue
    cata = [t for t in enriched if t["net"] <= -500]
    losers = [t for t in enriched if -500 < t["net"] <= 0]
    winners = [t for t in enriched if t["net"] > 0]
    print(f"  catastrophes (net≤−500) : {len(cata)}  ΣPnL={sum(t['pnl'] for t in cata):+.0f}$")
    print(f"  perdants modérés         : {len(losers)}  ΣPnL={sum(t['pnl'] for t in losers):+.0f}$")
    print(f"  gagnants                 : {len(winners)} ΣPnL={sum(t['pnl'] for t in winners):+.0f}$")
    print()

    def med(rows, k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return float(np.median(v)) if v else float("nan")

    print(f"  {'feature':<16} {'CATA(méd)':>11} {'gagnant(méd)':>13} {'écart':>9}  séparateur ?")
    seps = []
    for k in FEATS:
        mc, mw = med(cata, k), med(winners, k)
        if np.isnan(mc) or np.isnan(mw):
            continue
        # écart normalisé par l'IQR des gagnants (robuste)
        wv = sorted(r[k] for r in winners if r.get(k) is not None)
        iqr = (wv[int(len(wv)*0.75)] - wv[int(len(wv)*0.25)]) or 1.0
        gap = (mc - mw) / abs(iqr)
        flag = " ⭐ FORT" if abs(gap) >= 0.6 else (" ~" if abs(gap) >= 0.35 else "")
        seps.append((abs(gap), k, mc, mw, gap))
        print(f"  {k:<16} {mc:>11.1f} {mw:>13.1f} {gap:>+8.2f}σ{flag}")

    # le plus séparant : distribution par quartile de CE feature
    seps.sort(reverse=True)
    if seps:
        _, best, _, _, _ = seps[0]
        print(f"\n  === Meilleur séparateur : {best} — issue par quartile ===")
        allv = sorted(r[best] for r in enriched if r.get(best) is not None)
        n = len(allv)
        qs = [allv[int(n*q)] for q in (0.25, 0.5, 0.75)]
        for lo, hi, lbl in [(-1e9, qs[0], "Q1 bas"), (qs[0], qs[1], "Q2"),
                            (qs[1], qs[2], "Q3"), (qs[2], 1e9, "Q4 haut")]:
            b = [t for t in enriched if t.get(best) is not None and lo <= t[best] < hi]
            if not b:
                continue
            wr = sum(1 for t in b if t["net"] > 0)/len(b)*100
            cr = sum(1 for t in b if t["net"] <= -500)/len(b)*100
            pnl = sum(t["pnl"] for t in b)
            lo_s = f"{lo:.0f}" if lo > -1e8 else "−∞"
            hi_s = f"{hi:.0f}" if hi < 1e8 else "∞"
            print(f"    {lbl:<8} [{lo_s}..{hi_s}]  "
                  f"n={len(b):<3} WR={wr:4.0f}%  cata={cr:4.0f}%  ΣPnL={pnl:+7.0f}$")

    # Vue tail : les catastrophes sont-elles dans le HAUT de ret_180h (le plus
    # étiré sur 7j) ? (la médiane peut masquer un effet de queue)
    print("\n  === Effet de queue : ret_180h (course 7j déjà faite) par décile ===")
    rv = sorted((t["ret_180h"], t) for t in enriched if t.get("ret_180h") is not None)
    n = len(rv)
    for lo, hi, lbl in [(0.0, 0.5, "moitié basse 7j"), (0.5, 0.9, "haut 50-90%"),
                        (0.9, 1.0, "top 10% étiré")]:
        b = [t for _, t in rv[int(n*lo):int(n*hi)]]
        if not b:
            continue
        cr = sum(1 for t in b if t["net"] <= -500)/len(b)*100
        pnl = sum(t["pnl"] for t in b)
        print(f"    {lbl:<16} n={len(b):<3} cata={cr:4.0f}%  ΣPnL={pnl:+7.0f}$")


if __name__ == "__main__":
    main()
