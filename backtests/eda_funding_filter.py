"""Premise-EDA — funding à l'entrée comme filtre de crowding S5/S9 (2026-07-05).

Hypothèse (revue) : entrer un fade (S5/S9) quand MON côté paie déjà la prime
de funding = se mettre dans la file du squeeze. Feature :
    crowding_bps_h = −dir × funding_rate × 1e4   (bps/heure)
positif = mon côté est le côté crowdé (je paierais la prime).
NB signe : funding positif = les longs paient. Pour un SHORT, funding négatif
= les shorts paient → crowding = −(−1)×(rate<0)×1e4 > 0. Cohérent.

Critère PASS pré-enregistré (avant lecture des chiffres) : relation monotone
sur les quartiles OU bucket toxique (net moyen < 0) avec n ≥ 30 sur 28m,
signe cohérent sur le sous-échantillon 12m. Sinon STOP (pas de sweep).

Usage : python3 -m backtests.eda_funding_filter
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from backtests.backtest_rolling import load_funding

OUT = os.path.join(os.path.dirname(__file__), "output")
TRADES_F = os.path.join(OUT, "exit_ablation_base_trades.json")
MS_12M = 365 * 86400_000


def crowding_at(fd, coin, direction, entry_ms, lookback_h):
    """crowding bps/h au moment de l'entrée. None si pas de données."""
    if coin not in fd:
        return None
    ts, rates = fd[coin]
    hi = np.searchsorted(ts, entry_ms)
    lo = np.searchsorted(ts, entry_ms - lookback_h * 3_600_000) if lookback_h else hi - 1
    if hi <= 0 or hi <= lo:
        return None
    avg = float(rates[max(lo, 0):hi].mean())
    return -direction * avg * 1e4


def q_table(rows, key, label):
    """Quartiles de crowding → n, WR, net moyen, PnL total."""
    vals = sorted(r[key] for r in rows)
    n = len(vals)
    if n < 20:
        print(f"    {label}: n={n} < 20 — trop mince"); return None
    qs = [vals[int(n * q)] for q in (0.25, 0.5, 0.75)]
    buckets = [[], [], [], []]
    for r in rows:
        v = r[key]
        b = 0 if v < qs[0] else 1 if v < qs[1] else 2 if v < qs[2] else 3
        buckets[b].append(r)
    print(f"    {label} (n={n}, bornes quartiles bps/h : "
          f"{qs[0]:+.3f} / {qs[1]:+.3f} / {qs[2]:+.3f})")
    out = []
    for i, b in enumerate(buckets):
        wr = sum(1 for r in b if r["net"] > 0) / len(b) * 100 if b else 0
        net = sum(r["net"] for r in b) / len(b) if b else 0
        pnl = sum(r["pnl"] for r in b)
        rng = ("Q1 (anti-crowdé)", "Q2", "Q3", "Q4 (crowdé)")[i]
        print(f"      {rng:<17} n={len(b):<4} WR={wr:5.1f}%  "
              f"net={net:+7.1f} bps  ΣPnL={pnl:+9.2f}$")
        out.append({"n": len(b), "wr": wr, "net": net, "pnl": pnl})
    return out


def main():
    fd = load_funding()
    trades = json.load(open(TRADES_F))
    t28 = trades["28m"]
    t_max = max(t["entry_t"] for t in t28)

    rows = []
    miss = 0
    for t in t28:
        c_inst = crowding_at(fd, t["coin"], t["dir"], t["entry_t"], 0)
        c_24h = crowding_at(fd, t["coin"], t["dir"], t["entry_t"], 24)
        if c_inst is None or c_24h is None:
            miss += 1; continue
        rows.append({**t, "c_inst": c_inst, "c24": c_24h})
    print(f"28m : {len(t28)} trades, {len(rows)} avec funding, {miss} sans "
          f"(réconciliation : {len(rows)}+{miss}={len(t28)})")

    for strat in ("S5", "S9", "S8", "S10"):
        for d, dl in ((1, "LONG"), (-1, "SHORT")):
            sel = [r for r in rows if r["strat"] == strat and r["dir"] == d]
            if not sel:
                continue
            tag = "CIBLE" if strat in ("S5", "S9") else "contrôle"
            print(f"\n  {strat} {dl} [{tag}] :")
            q_table(sel, "c24", "crowding moy. 24h")
            q_table(sel, "c_inst", "crowding instantané")

    # Stabilité temporelle : sous-échantillon 12 derniers mois, cible seulement
    print("\n══ Sous-échantillon 12m (stabilité du signe) ══")
    cut = t_max - MS_12M
    for strat in ("S5", "S9"):
        for d, dl in ((1, "LONG"), (-1, "SHORT")):
            sel = [r for r in rows
                   if r["strat"] == strat and r["dir"] == d and r["entry_t"] >= cut]
            if len(sel) >= 20:
                print(f"\n  {strat} {dl} (12m) :")
                q_table(sel, "c24", "crowding moy. 24h")

    # Vue continue : corrélation de rang crowding ↔ net (cible, 28m)
    print("\n══ Corrélation de rang (Spearman approx.) crowding_24h ↔ net ══")
    for strat in ("S5", "S9"):
        for d, dl in ((1, "LONG"), (-1, "SHORT")):
            sel = [r for r in rows if r["strat"] == strat and r["dir"] == d]
            if len(sel) < 20:
                continue
            c = np.array([r["c24"] for r in sel])
            g = np.array([r["net"] for r in sel])
            rc = np.argsort(np.argsort(c)).astype(float)
            rg = np.argsort(np.argsort(g)).astype(float)
            rho = float(np.corrcoef(rc, rg)[0, 1])
            print(f"  {strat} {dl:<5} n={len(sel):<4} rho={rho:+.3f}")


if __name__ == "__main__":
    main()
