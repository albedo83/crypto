"""Tests 8A / 8B — le carnet d'ordres à l'échelle de la minute (grille : addendum
du 2026-10-19 dans docs/vague1_2026_10_grid.md).

8A : le déséquilibre du carnet à ±1 % prédit-il le rendement à 1/5/15/60 min ?
8B : utiliser ce déséquilibre pour choisir la minute d'entrée des trades du moteur.
Un token à la fois (RAM partagée avec Alfred) — lancer avec nice -n 19.
"""
from __future__ import annotations

import glob
import math
import os
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from alfred.settings import DEFAULT_PARAMS
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from backtests.backtest_sector import compute_sector_features

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "binance")
UTC = timezone.utc
Y26 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
SPLIT = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
HS = (1, 5, 15, 60)
W, COST = 30, 13.0
MIN = 60000


def _csv(path, **kw):
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        return pd.read_csv(fh, **kw)


def minute_series(sym):
    k = pd.concat([_csv(p, usecols=["open_time", "close"]) for p in
                   sorted(glob.glob(f"{ROOT}/klines/{sym}-1m-*.zip"))]).drop_duplicates("open_time")
    px = pd.Series(k["close"].to_numpy(), index=(k["open_time"].to_numpy() // MIN) * MIN).sort_index()
    parts = []
    for p in sorted(glob.glob(f"{ROOT}/bookDepth/{sym}-bookDepth-*.zip")):
        d = _csv(p)
        d = d[d["percentage"].isin([-1, 1])]
        ts_ = pd.to_datetime(d["timestamp"], utc=True)
        d["m"] = ((ts_ - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1) // MIN) * MIN
        w = d.pivot_table(index="m", columns="percentage", values="notional", aggfunc="last")
        if -1 in w and 1 in w:
            parts.append((w[-1] - w[1]) / (w[-1] + w[1]))
    imb = pd.concat(parts).groupby(level=0).last().sort_index()
    grid = np.arange(max(px.index[0], imb.index[0]), min(px.index[-1], imb.index[-1]) + MIN, MIN)
    return grid, px.reindex(grid).ffill(limit=2).to_numpy(), imb.reindex(grid).to_numpy()


def tstat(daily):
    m = np.array([s / n for s, n in daily.values() if n])
    if len(m) < 3 or m.std(ddof=1) == 0:
        return 0.0, float(m.mean()) if len(m) else 0.0
    return float(m.mean() / (m.std(ddof=1) / math.sqrt(len(m)))), float(m.mean())


def main():
    print("backtest 2026 (entrées du moteur)…", flush=True)
    data = load_3y_candles()
    f = build_features(data)
    end = max(c["t"] for c in data["BTC"])
    r = run_window(f, data, compute_sector_features(f, data), load_dxy(), Y26, end, start_capital=1000.0,
                   oi_data=load_oi(), funding_data=load_funding(), apply_adaptive_modulator=True,
                   aligned=True, margin_check=True, skip_fn=lambda c, t, s, d: s == "S5")
    entries = defaultdict(list)
    for t in r["trades"]:
        entries[t["coin"]].append((t["entry_t"], t["dir"]))
    print(f"  {sum(map(len, entries.values()))} entrées", flush=True)

    # 8A : sommes journalières {(h, période, quoi): {jour: [somme, n]}}
    A = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    B = {s: {"disc": [], "val": []} for s in (1, -1)}
    CTRL = {"disc": [], "val": []}
    for coin in sorted(DEFAULT_PARAMS.trade_symbols):
        sym = f"{coin}USDT"
        if not glob.glob(f"{ROOT}/bookDepth/{sym}-*.zip"):
            continue
        g, px, imb = minute_series(sym)
        disc = g < SPLIT
        ok = ~np.isnan(imb)
        lo, hi = np.nanquantile(imb[disc & ok], [0.1, 0.9])
        top, bot = ok & (imb >= hi), ok & (imb <= lo)
        day = g // 86400000
        for h in HS:
            fr = np.full(len(px), np.nan)
            fr[:-h] = (px[h:] / px[:-h] - 1) * 1e4
            for name, msk in (("top", top), ("bot", bot)):
                for i in np.nonzero(msk & ~np.isnan(fr))[0]:
                    per = "disc" if disc[i] else "val"
                    a = A[(h, per, name)][day[i]]
                    a[0] += fr[i]
                    a[1] += 1
        idx = {int(t): i for i, t in enumerate(g)}
        for t0, d in entries.get(coin, []):
            i0 = idx.get(int(t0))
            if i0 is None or i0 + W >= len(px) or not px[i0] > 0:
                continue
            per = "disc" if t0 < SPLIT else "val"
            for s in (1, -1):
                j = i0
                while j < i0 + W and not (np.isnan(imb[j]) or s * np.sign(imb[j]) * d >= 0):
                    j += 1
                B[s][per].append(-d * (px[j] / px[i0] - 1) * 1e4)
            CTRL[per].append(-d * (px[i0 + 15] / px[i0] - 1) * 1e4)
        print(f"  {coin}: ok", flush=True)

    print("\n=== 8A — déséquilibre ±1 % (déciles extrêmes) → rendement futur (bps, brut) ===")
    print(f"{'h':>4} | {'disc haut':>9}{'disc bas':>9}{'écart':>7}{'t':>6} | {'val haut':>9}{'val bas':>9}{'écart':>7}{'t':>6} | {'suivre net (val)':>17}")
    sign15 = 1
    for h in HS:
        res = {}
        for per in ("disc", "val"):
            th, mh = tstat(A[(h, per, "top")])
            tb, mb = tstat(A[(h, per, "bot")])
            days = set(A[(h, per, "top")]) & set(A[(h, per, "bot")])
            sp = {dd: [A[(h, per, "top")][dd][0] / A[(h, per, "top")][dd][1]
                       - A[(h, per, "bot")][dd][0] / A[(h, per, "bot")][dd][1], 1] for dd in days}
            tsp, msp = tstat(sp)
            res[per] = (mh, mb, msp, tsp)
        if h == 15:
            sign15 = 1 if res["disc"][2] >= 0 else -1
        net = res["val"][2] / 2 * (1 if res["val"][2] >= 0 else -1) - COST
        pred = "prédictif" if (np.sign(res["val"][2]) == np.sign(res["disc"][2]) and abs(res["val"][3]) >= 3) else "non"
        print(f"{h:>3}m | {res['disc'][0]:>+9.2f}{res['disc'][1]:>+9.2f}{res['disc'][2]:>+7.2f}{res['disc'][3]:>6.1f} | "
              f"{res['val'][0]:>+9.2f}{res['val'][1]:>+9.2f}{res['val'][2]:>+7.2f}{res['val'][3]:>6.1f} | "
              f"{net:>+8.2f} bp/trade  [{pred}]")

    print(f"\n=== 8B — choisir la minute d'entrée (sens de l'effet fixé par 8A découverte à 15 min : {sign15:+d}) ===")
    for per in ("disc", "val"):
        x, c = np.array(B[sign15][per]), np.array(CTRL[per])
        t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 else 0
        print(f"  {per:5}: n={len(x):4}  gain moyen {x.mean():+6.2f} bps (t {t:+.2f}, médiane {np.median(x):+.2f})"
              f" | témoin délai fixe 15 min {c.mean():+6.2f} bps")
    x, c = np.array(B[sign15]["val"]), np.array(CTRL["val"])
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 else 0
    ok = x.mean() >= 3 and t >= 2 and x.mean() > c.mean()
    print(f"  → 8B {'VALIDÉ' if ok else 'REJETÉ'} (critère : validation ≥ +3 bps, t ≥ 2, meilleur que le témoin)")


if __name__ == "__main__":
    main()
