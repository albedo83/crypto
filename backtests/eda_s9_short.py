"""Premise-EDA — S9 SHORT paie-t-il sa place ? (2026-07-07)

Déclencheur : le trade BLUR catastrophique a fait remonter que S9 SHORT est
net-négatif dans le BT 28m (−$56, 37 % de catastrophe_stop). Question :
la perte est-elle RÉGIME-SÉPARABLE (rescuable par un gate) ou STRUCTURELLE
(gagnants/perdants entrelacés, S9 SHORT juste marginal) ?

Doctrine : histogramme AVANT hypothèse + premise-gate AVANT sweep.
  - Le modulateur dé-amplifie DÉJÀ S9 SHORT en bull (alpha=−0.5). Donc les
    chiffres ci-dessous sont APRÈS dé-amp bull. Si S9 SHORT perd encore en
    bull → le modulateur n'est pas assez agressif OU c'est structurel.
  - Prior fort (vol-targeting réfuté) : la vol est le carburant — ne pas
    espérer que la magnitude du move sépare (le gros move fadé PAIE).

Critère PASS pré-enregistré (écrit AVANT lecture) : un séparateur (régime
btc_z, ou autre) isole un bucket clairement net-négatif (n≥25) avec les
autres net-positifs ET le SIGNE cohérent avec le design (bull perd / bear
gagne pour un fade SHORT) → un gate/dé-amp renforcé a une prémisse. SINON
(perte étalée sur tous les buckets, ou signe inversé, ou porté par la queue
catastrophe indissociable des gagnants) → STRUCTUREL, pas de sweep, décision
keep/cut/deamp séparée et argumentée.

Contrôles : S9 LONG (l'autre côté) + S5 SHORT (fade frère) pour situer.

Usage : python3 -m backtests.eda_s9_short
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from backtests.backtest_genetic import load_3y_candles

OUT = os.path.join(os.path.dirname(__file__), "output")
TRADES_F = os.path.join(OUT, "exit_ablation_base_trades.json")
LB_D, ZW_D, CLIP = 30, 180, 2.5
CPD = 6                       # bougies 4h/jour
N_LB, N_ZW = LB_D * CPD, ZW_D * CPD


def build_btc_z(data):
    """{ts_ms: btc_z} baseline (ret_30d sur fenêtre 180j, mean+std, clip 2.5)."""
    btc = sorted(data["BTC"], key=lambda c: c["t"])
    ts = np.array([c["t"] for c in btc], dtype=np.int64)
    close = np.array([c["c"] for c in btc], dtype=float)
    ret30 = np.full(len(ts), np.nan)
    for j in range(N_LB, len(ts)):
        ret30[j] = close[j] / close[j - N_LB] - 1.0
    out = {}
    for j in range(N_LB, len(ts)):
        lo = j - N_ZW + 1                      # aligned (divergence #10)
        past = ret30[max(0, lo):j + 1]
        past = past[~np.isnan(past)]
        if len(past) < 30:
            continue
        c, s = past.mean(), past.std()
        if s <= 0:
            continue
        z = (ret30[j] - c) / s
        out[int(ts[j])] = float(np.clip(z, -CLIP, CLIP))
    return out


def btc_z_at(zmap, ts_sorted, zarr, entry_ms):
    i = np.searchsorted(ts_sorted, entry_ms, side="right") - 1
    return zarr[i] if i >= 0 else None


def bucket_stats(trades, label):
    n = len(trades)
    if n == 0:
        print(f"    {label:<22} n=0"); return
    wr = sum(1 for t in trades if t["net"] > 0) / n * 100
    net = sum(t["net"] for t in trades) / n
    med = float(np.median([t["net"] for t in trades]))
    pnl = sum(t["pnl"] for t in trades)
    cata = sum(1 for t in trades if t["reason"] == "catastrophe_stop") / n * 100
    print(f"    {label:<22} n={n:<4} WR={wr:5.1f}%  net_moy={net:+7.0f}  "
          f"net_med={med:+7.0f}  ΣPnL={pnl:+8.1f}$  cata={cata:4.0f}%")


def main():
    data = load_3y_candles()
    zmap = build_btc_z(data)
    ts_sorted = np.array(sorted(zmap.keys()), dtype=np.int64)
    zarr = np.array([zmap[t] for t in ts_sorted])

    # VALIDATION contre l'ancre connue : BLUR 07-07 04:00 UTC → btc_z=+0.534 live
    anchor_ms = 1783396800 * 1000    # 2026-07-07 04:00:00 UTC
    z_anchor = btc_z_at(zmap, ts_sorted, zarr, anchor_ms)
    print(f"VALIDATION btc_z @ 07-07 04:00 : {z_anchor:+.3f}  "
          f"(live loggé = +0.534 → {'OK ✓' if z_anchor and abs(z_anchor-0.534)<0.15 else 'DIVERGE ⚠'})")
    print()

    trades = json.load(open(TRADES_F))["28m"]
    for t in trades:
        t["btc_z"] = btc_z_at(zmap, ts_sorted, zarr, t["entry_t"])

    def subset(strat, d):
        return [t for t in trades if t["strat"] == strat and t["dir"] == d
                and t["btc_z"] is not None]

    s9s = subset("S9", -1)
    print(f"═ S9 SHORT — population {len(s9s)} (btc_z calculable)")
    print(f"  Agrégat :"); bucket_stats(s9s, "TOUS")

    # HISTOGRAMME : distribution de btc_z aux entrées S9 SHORT
    zvals = sorted(t["btc_z"] for t in s9s)
    q = lambda p: zvals[min(len(zvals) - 1, int(p * len(zvals)))]
    print(f"\n  Histogramme btc_z aux entrées S9 SHORT :")
    print(f"    p10={q(.1):+.2f}  p25={q(.25):+.2f}  p50={q(.5):+.2f}  "
          f"p75={q(.75):+.2f}  p90={q(.9):+.2f}")
    print(f"    part en bull (z>0.5)={sum(1 for z in zvals if z>0.5)/len(zvals)*100:.0f}%  "
          f"neutre={sum(1 for z in zvals if -0.5<=z<=0.5)/len(zvals)*100:.0f}%  "
          f"bear (z<−0.5)={sum(1 for z in zvals if z<-0.5)/len(zvals)*100:.0f}%")

    # STRATIFICATION par régime btc_z (le séparateur de la prémisse)
    print(f"\n  Par régime btc_z (le design veut : bull PERD, bear GAGNE) :")
    bucket_stats([t for t in s9s if t["btc_z"] < -0.5], "bear (z<−0.5)")
    bucket_stats([t for t in s9s if -0.5 <= t["btc_z"] <= 0.5], "neutre")
    bucket_stats([t for t in s9s if t["btc_z"] > 0.5], "bull (z>0.5)")

    # La perte est-elle portée par la QUEUE catastrophe (indissociable) ?
    print(f"\n  Décomposition mean vs median (queue catastrophe ?) :")
    nets = sorted(t["net"] for t in s9s)
    print(f"    net moyen={np.mean(nets):+.0f}  net médian={np.median(nets):+.0f}  "
          f"→ {'queue négative tire la moyenne' if np.median(nets) > np.mean(nets) else 'perte broad (médiane ≤ moyenne)'}")
    winners = [t for t in s9s if t["net"] > 0]
    losers = [t for t in s9s if t["net"] <= 0]
    print(f"    gagnants n={len(winners)} ΣPnL={sum(t['pnl'] for t in winners):+.0f}$  |  "
          f"perdants n={len(losers)} ΣPnL={sum(t['pnl'] for t in losers):+.0f}$")

    # CONTRÔLES
    print(f"\n═ CONTRÔLES")
    bucket_stats(subset("S9", 1), "S9 LONG")
    bucket_stats(subset("S5", -1), "S5 SHORT")
    bucket_stats(subset("S10", -1), "S10 SHORT")


if __name__ == "__main__":
    main()
