"""Premise-EDA vol-targeting (2026-07-05) — histogramme AVANT hypothèse.

Hypothèse (revue) : la pile multiplicative actuelle (base% × z-weight ×
haircut × mult × modulateur) ignore la vol du token → un meme coin et un L1
au même notionnel portent des risques très différents. Le vol-targeting
(risque fixe en bps d'equity, taille ∝ 1/vol) égaliserait et virerait la
moitié des paramètres.

Premise à valider AVANT tout sweep :
  P1 (histogramme) : la vol 7j des tokens à l'heure de NOS entrées a-t-elle
     de la variance exploitable ? (leçon funding : feature épinglée = STOP)
  P2 : le sizing actuel est-il déjà anti-corrélé à la vol (auquel cas le
     chantier est cosmétique) ?
  P3 : le risque non-égalisé fait-il du dégât mesurable ? (taux de
     catastrophe_stop et |net| par tercile de vol)

Usage : python3 -m backtests.eda_vol_targeting
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from backtests.backtest_genetic import load_3y_candles

OUT = os.path.join(os.path.dirname(__file__), "output")
TRADES_F = os.path.join(OUT, "exit_ablation_base_trades.json")
VOL_LOOKBACK = 42          # 42 bougies 4h = 7 jours
HOLD_CANDLES = 12          # ~48h


def build_vol_series(data):
    """{coin: (ts_array, vol4h_array)} — std des log-returns 4h sur 7j, en bps."""
    out = {}
    for coin, candles in data.items():
        ts = np.array([c["t"] for c in candles], dtype=np.int64)
        close = np.array([c["c"] for c in candles], dtype=float)
        lr = np.diff(np.log(close))
        vol = np.full(len(ts), np.nan)
        for i in range(VOL_LOOKBACK, len(ts)):
            vol[i] = lr[i - VOL_LOOKBACK:i].std() * 1e4
        out[coin] = (ts, vol)
    return out


def vol_at(vols, coin, t_ms):
    if coin not in vols:
        return None
    ts, v = vols[coin]
    i = np.searchsorted(ts, t_ms, side="right") - 1
    if i < VOL_LOOKBACK or math.isnan(v[i]):
        return None
    return float(v[i])


def pctiles(a, label):
    a = sorted(a)
    n = len(a)
    q = lambda p: a[min(n - 1, int(p * n))]
    print(f"  {label:<34} n={n:<5} p10={q(.1):7.1f} p50={q(.5):7.1f} "
          f"p90={q(.9):7.1f}  ratio p90/p10 = {q(.9)/q(.1):4.1f}×")
    return q(.1), q(.5), q(.9)


def main():
    data = load_3y_candles()
    vols = build_vol_series(data)
    trades = json.load(open(TRADES_F))["28m"]

    rows = []
    for t in trades:
        v = vol_at(vols, t["coin"], t["entry_t"])
        if v is None or not t["net"]:
            continue
        size = abs(t["pnl"] * 1e4 / t["net"]) if t["net"] else 0.0
        rows.append({**t, "vol4h": v, "size": size,
                     "move48": v * math.sqrt(HOLD_CANDLES)})
    print(f"28m : {len(trades)} trades, {len(rows)} avec vol calculable\n")

    print("═ P1 — HISTOGRAMME : vol 4h (7j) des tokens à l'heure de NOS entrées (bps/bougie)")
    pctiles([r["vol4h"] for r in rows], "toutes entrées")
    for s in ("S1", "S5", "S8", "S9", "S10"):
        sel = [r["vol4h"] for r in rows if r["strat"] == s]
        if len(sel) >= 20:
            pctiles(sel, f"  {s}")
    lo, med, hi = np.percentile([r["vol4h"] for r in rows], [10, 50, 90])
    verdict1 = hi / lo
    print(f"  → variance exploitable : ratio p90/p10 = {verdict1:.1f}× "
          f"({'OK, la feature respire' if verdict1 >= 2 else 'ÉPINGLÉE — STOP'})")

    print("\n═ P2 — le sizing actuel compense-t-il déjà la vol ?")
    v = np.array([r["vol4h"] for r in rows])
    sz = np.array([r["size"] for r in rows])
    rv = np.argsort(np.argsort(v)).astype(float)
    rs = np.argsort(np.argsort(sz)).astype(float)
    rho_all = float(np.corrcoef(rv, rs)[0, 1])
    print(f"  Spearman(size, vol) toutes entrées : {rho_all:+.3f}")
    for s in ("S5", "S9", "S10", "S8"):
        sel = [r for r in rows if r["strat"] == s]
        if len(sel) < 30:
            continue
        v_ = np.array([r["vol4h"] for r in sel]); s_ = np.array([r["size"] for r in sel])
        rho = float(np.corrcoef(np.argsort(np.argsort(v_)).astype(float),
                                np.argsort(np.argsort(s_)).astype(float))[0, 1])
        print(f"    {s} (intra-strat) : {rho:+.3f}  (n={len(sel)})")
    print("  Risque-proxy = size × move48 attendu :")
    risk = [r["size"] * r["move48"] / 1e4 for r in rows]   # $ de move attendu
    pctiles(risk, "  $ d'excursion 48h attendue")

    print("\n═ P3 — le risque non-égalisé fait-il du dégât ?")
    terc = np.percentile(v, [33.3, 66.7])
    print(f"  Terciles de vol4h : < {terc[0]:.0f} / {terc[0]:.0f}-{terc[1]:.0f} / > {terc[1]:.0f} bps")
    for i, (lo_, hi_, lbl) in enumerate((
            (0, terc[0], "T1 calme"), (terc[0], terc[1], "T2 moyen"),
            (terc[1], 1e9, "T3 volatil"))):
        sel = [r for r in rows if lo_ <= r["vol4h"] < hi_]
        n = len(sel)
        cata = sum(1 for r in sel if r["reason"] == "catastrophe_stop") / n * 100
        wr = sum(1 for r in sel if r["net"] > 0) / n * 100
        net = sum(r["net"] for r in sel) / n
        pnl = sum(r["pnl"] for r in sel)
        worst = min(r["net"] for r in sel)
        print(f"    {lbl:<10} n={n:<4} WR={wr:5.1f}%  net={net:+7.1f} bps  "
              f"ΣPnL={pnl:+9.2f}$  cata_stop={cata:4.1f}%  pire={worst:+.0f}")


if __name__ == "__main__":
    main()
