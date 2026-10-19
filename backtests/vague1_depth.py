"""Test 8C — profondeur du carnet à ±2-5 % et stops catastrophe (grille : addendum 4
du 2026-10-19 dans docs/vague1_2026_10_grid.md).

Feature = moyenne des instantanés bookDepth des 60 min avant l'entrée (clôture de la
bougie signal). Un token à la fois (RAM partagée avec Alfred) — lancer avec nice -n 19.
"""
from __future__ import annotations

import glob
import math
import os
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from alfred.settings import DEFAULT_PARAMS
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from backtests.backtest_sector import compute_sector_features

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "binance")
UTC = timezone.utc
Y26 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
SPLIT = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
H4, MIN = 4 * 3600000, 60000
FEATS = ("F1_coussin", "F2_mur", "F3_epaisseur")


def _csv(path):
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        return pd.read_csv(fh, usecols=["timestamp", "percentage", "notional"])


def depth_at_entries(sym):
    """{entry_ts (clôture 4h): {pct: notionnel moyen des 60 min précédentes}}."""
    out = {}
    for p in sorted(glob.glob(f"{ROOT}/bookDepth/{sym}-bookDepth-*.zip")):
        d = _csv(p)
        ms = (pd.to_datetime(d["timestamp"], utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) \
            // pd.Timedelta(milliseconds=1)
        end = (ms // H4 + 1) * H4                       # clôture 4h suivante
        d = d[(end - ms) <= 60 * MIN].assign(end=end)
        for e, g in d.groupby("end"):
            out[int(e)] = g.groupby("percentage")["notional"].mean().to_dict()
    return out


def feats(book, d, med5):
    s = [book.get(-k * d) for k in range(1, 6)]        # LONG (d=1) : bids = pourcentages négatifs
    r5 = book.get(5 * d)
    if any(v is None or not v > 0 for v in s) or not r5 or not r5 > 0:
        return None
    tranches = [s[0]] + [s[k] - s[k - 1] for k in range(1, 5)]
    return {"F1_coussin": math.log(s[4] / r5), "F2_mur": max(tranches) / s[4],
            "F3_epaisseur": s[4] / med5 if med5 else None}


def run(f, data, sf, dxy, oi, fund, start, skip):
    end = max(c["t"] for c in data["BTC"])
    return run_window(f, data, sf, dxy, start, end, start_capital=1000.0, oi_data=oi, funding_data=fund,
                      apply_adaptive_modulator=True, aligned=True, margin_check=True, skip_fn=skip)


def summary(r):
    T = r["trades"]
    return (r.get("pnl_pct", sum(t["pnl"] for t in T) / 10.0), r.get("max_dd_pct"),
            sum(t["reason"] == "catastrophe_stop" for t in T), len(T))


def main():
    data = load_3y_candles()
    f = build_features(data)
    sf, dxy, oi, fund = compute_sector_features(f, data), load_dxy(), load_oi(), load_funding()
    base = lambda c, t, s, d: s == "S5"
    print("backtest 2026 (entrées du moteur)…", flush=True)
    T = run(f, data, sf, dxy, oi, fund, Y26, base)["trades"]

    books, med5 = {}, {}
    for coin in sorted(DEFAULT_PARAMS.trade_symbols):
        sym = f"{coin}USDT"
        if not glob.glob(f"{ROOT}/bookDepth/{sym}-*.zip"):
            continue
        books[coin] = depth_at_entries(sym)
        disc = [b for e, b in books[coin].items() if e < SPLIT]
        med5[coin] = {d: float(np.median([b[-5 * d] for b in disc if b.get(-5 * d)])) for d in (1, -1)}
        print(f"  {coin}: {len(books[coin])} clôtures 4h", flush=True)

    def fx(coin, entry_ts, d):
        b = books.get(coin, {}).get(int(entry_ts))
        return feats(b, d, med5[coin][d]) if b else None

    rows = []
    for t in T:
        x = fx(t["coin"], t["entry_t"], t["dir"])
        if x:
            rows.append({**x, "cat": t["reason"] == "catastrophe_stop", "disc": t["entry_t"] < SPLIT})
    df = pd.DataFrame(rows)
    D = df[df.disc]
    print(f"\n{len(T)} trades 2026, {len(df)} avec carnet ; découverte {len(D)} trades, {int(D.cat.sum())} stops")
    if D.cat.sum() < 20:
        print("→ SANS PUISSANCE (moins de 20 stops en découverte) : aucune règle")
        return

    best = None
    for name in FEATS:
        q = np.nanquantile(D[name], [0.2, 0.4, 0.6, 0.8])
        b = np.digitize(D[name], q)
        rates = [D.cat[b == i].mean() for i in range(5)]
        worst = 0 if rates[0] >= rates[4] else 4
        inq = b == worst
        tab = [[D.cat[inq].sum(), (~D.cat[inq]).sum()], [D.cat[~inq].sum(), (~D.cat[~inq]).sum()]]
        p = chi2_contingency(tab)[1]
        ratio = D.cat[inq].mean() / D.cat[~inq].mean() if D.cat[~inq].mean() else float("inf")
        print(f"  {name:13}: " + " · ".join(f"{x:.1%}" for x in rates) + f"  pire Q{worst + 1} ×{ratio:.2f}, p = {p:.2g}")
        if p < 0.01 / 3 and (best is None or ratio > best[1]):
            best = (name, ratio, worst, q[0] if worst == 0 else q[3])
    if best is None:
        print("→ REJETÉ : aucune feature sous le seuil de Bonferroni (0,0033)")
        return

    name, _, worst, thr = best
    print(f"retenue : {name}, pas d'entrée si {'≤' if worst == 0 else '≥'} {thr:.4g}")

    def rule(c, ts, s, d):
        if s == "S5":
            return True
        x = fx(c, ts + H4, d)
        v = x and x.get(name)
        return v is not None and (v <= thr if worst == 0 else v >= thr)

    ref, new = summary(run(f, data, sf, dxy, oi, fund, SPLIT, base)), summary(run(f, data, sf, dxy, oi, fund, SPLIT, rule))
    print(f"validation  réf : P&L {ref[0]:+.2f} %, DD {ref[1]}, {ref[2]} stops, {ref[3]} trades")
    print(f"validation règle: P&L {new[0]:+.2f} %, DD {new[1]}, {new[2]} stops, {new[3]} trades")
    ok = new[0] >= ref[0] and new[2] < ref[2] and new[1] >= ref[1] - 2
    print(f"→ {'VALIDÉ' if ok else 'REJETÉ'}")


if __name__ == "__main__":
    main()
