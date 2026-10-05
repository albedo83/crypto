"""Fade de la prime mark/oracle HL (ticks 60 s, market.db, depuis 2026-06-10).

Écart = (mark/oracle − 1) en bps, moins sa médiane glissante 24 h PASSÉE (le
token a une prime habituelle, souvent négative). Si l'excès dépasse X bps, on
fade : short si le mark est trop haut, long s'il est trop bas. Rendement mesuré
sur le MARK (ce qu'on trade), sur H minutes, un trade à la fois par token.

Discipline : découverte 2026-06-10 → 08-15, validation 08-15 → fin.
Critère fixé AVANT : retenu si t ≥ 3 en découverte (clusterisé par jour) ET
moyenne nette > 0 avec t ≥ 1,5 en validation, à coût taker 13 bps.
Le brut est aussi rapporté (borne haute, cas d'exécution maker idéale).
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

DB = "file:/home/crypto/alfred/data/market.db?mode=ro"
SPLIT = datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp()
XS = (10, 15, 20, 30)
HS = (15, 60, 240)
COST = 13.0


def series(con, sym):
    df = pd.read_sql_query(
        "SELECT ts, mark_px, oracle_px FROM ticks WHERE symbol=? AND oracle_px>0 AND mark_px>0 ORDER BY ts",
        con, params=(sym,))
    df["ts"] = (df["ts"] // 60) * 60
    df = df.drop_duplicates("ts", keep="last").set_index("ts")
    full = np.arange(df.index[0], df.index[-1] + 60, 60)
    df = df.reindex(full)
    dev = (df["mark_px"] / df["oracle_px"] - 1) * 1e4
    base = dev.rolling(1440, min_periods=720).median().shift(1)
    return df["mark_px"].to_numpy(), (dev - base).to_numpy(), full


def trades(mark, exc, ts, X, H):
    out, nxt = [], 0
    hits = np.nonzero(np.abs(np.nan_to_num(exc)) > X)[0]
    for i in hits:
        if i < nxt or i + H >= len(mark):
            continue
        p0, p1 = mark[i], mark[i + H]
        if not (p0 > 0 and p1 > 0):
            continue
        d = -1 if exc[i] > 0 else 1
        gross = d * (p1 / p0 - 1) * 1e4
        out.append((int(ts[i] // 86400), gross, ts[i]))
        nxt = i + H
    return out


def stat(tr, cost):
    if len(tr) < 3:
        return len(tr), 0.0, 0.0
    by = {}
    for d, g, _ in tr:
        by.setdefault(d, []).append(g - cost)
    m = np.array([np.mean(v) for v in by.values()])
    t = m.mean() / (m.std(ddof=1) / math.sqrt(len(m))) if len(m) > 2 and m.std(ddof=1) > 0 else 0.0
    return len(tr), float(np.mean([g - cost for _, g, _ in tr])), float(t)


def main():
    con = sqlite3.connect(DB, uri=True)
    syms = [s for (s,) in con.execute("SELECT DISTINCT symbol FROM ticks") if s not in ("TON", "BTC", "ETH")]
    S = {s: series(con, s) for s in syms}
    print(f"{len(syms)} tokens — discovery jusqu'au 2026-08-15, validation ensuite")
    print(f"{'X bps':>6}{'H min':>7} | {'disc n':>7}{'brut':>7}{'net':>7}{'t':>6} | {'valid n':>7}{'brut':>7}{'net':>7}{'t':>6}")
    for X in XS:
        for H in HS:
            disc, val = [], []
            for s, (mark, exc, ts) in S.items():
                for tr in trades(mark, exc, ts, X, H):
                    (disc if tr[2] < SPLIT else val).append(tr)
            nd, gd, _ = stat(disc, 0.0)
            _, md, td = stat(disc, COST)
            nv, gv, _ = stat(val, 0.0)
            _, mv, tv = stat(val, COST)
            flag = "  ← retenu" if (td >= 3 and mv > 0 and tv >= 1.5) else ""
            print(f"{X:>6}{H:>7} | {nd:>7}{gd:>+7.1f}{md:>+7.1f}{td:>6.2f} | {nv:>7}{gv:>+7.1f}{mv:>+7.1f}{tv:>6.2f}{flag}")


if __name__ == "__main__":
    main()
