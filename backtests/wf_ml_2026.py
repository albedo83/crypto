"""Apprentissage walk-forward 2026 — exécution de la grille docs/wf_ml_2026_grid.md.

Ne rien changer ici après le premier résultat sans ouvrir une nouvelle étude.

    nice -n 19 python3 -m backtests.wf_ml_2026            # run principal + témoins
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
HOURLY = os.path.join(HERE, "output", "binance", "hourly")
OUTDIR = os.path.join(HERE, "output", "wf_ml_2026")
START = pd.Timestamp("2026-01-01", tz="UTC")
COST = 13.0 / 1e4
H = 24
FAMILIES = {"prix": ["c", "h", "l", "o"], "flux": ["qv", "n", "tbq"], "oi": ["oi", "oiv"],
            "positionnement": ["tlsa", "tlsp", "gls", "tkr"], "carnet": ["b1", "a1", "b2", "a2", "b5", "a5"],
            "funding": ["fr"]}
SEED = 20261005


def load():
    raw = {os.path.basename(p)[:-4]: pd.read_pickle(p) for p in sorted(glob.glob(f"{HOURLY}/*.pkl"))}
    idx = pd.date_range(START - pd.Timedelta(days=10), max(d.index.max() for d in raw.values()), freq="1h")
    # ── contrôle de profondeur (grille) : couverture horaire depuis 2026-01-01 ──
    cov = {}
    for fam, cols in FAMILIES.items():
        vals = [raw[c].reindex(idx).loc[START:, col].notna().mean()
                for c in raw if c != "BTC" for col in cols if col in raw[c]]
        cov[fam] = float(np.mean(vals)) if vals else 0.0
    keep = {f for f, v in cov.items() if v >= 0.95}
    # ── décalage d'une barre : la barre ouverte à h n'est connue qu'à h+1 ──
    data = {c: d.reindex(idx).shift(1) for c, d in raw.items()}
    return data, idx, cov, keep


def features(data, idx, keep):
    btc = data["BTC"]["c"]
    rows = {}
    from alfred.settings import DEFAULT_PARAMS
    universe = set(DEFAULT_PARAMS.trade_symbols)      # univers réel d'Alfred (33)
    for coin, d in data.items():
        if coin not in universe:
            continue
        F = pd.DataFrame(index=idx)
        c = d["c"]
        r1 = c.pct_change()
        for L in (1, 4, 12, 24, 72, 168):
            F[f"ret_{L}"] = c.pct_change(L)
        F["vol_24"] = r1.rolling(24).std()
        F["vol_168"] = r1.rolling(168).std()
        F["vol_ratio"] = F["vol_24"] / F["vol_168"]
        hh, ll = d["h"].rolling(24).max(), d["l"].rolling(24).min()
        F["range_24"] = (hh - ll) / c
        F["pos_24"] = (c - ll) / (hh - ll)
        F["resid_24"] = F["ret_24"] - btc.pct_change(24)
        F["btc_4"], F["btc_24"] = btc.pct_change(4), btc.pct_change(24)
        F["btc_vol"] = btc.pct_change().rolling(24).std()
        if "flux" in keep:
            for L in (1, 4, 24):
                F[f"taker_imb_{L}"] = 2 * d["tbq"].rolling(L).sum() / d["qv"].rolling(L).sum() - 1
            q24 = d["qv"].rolling(24).sum()
            F["vol_z"] = (q24 - q24.rolling(168).mean()) / q24.rolling(168).std()
            avg = d["qv"] / d["n"]
            F["avg_trade_z"] = (avg - avg.rolling(168).mean()) / avg.rolling(168).std()
        if "oi" in keep:
            for L in (1, 4, 24):
                F[f"oi_chg_{L}"] = d["oi"].pct_change(L)
            F["oi_turn"] = d["oiv"] / d["qv"].rolling(24).sum()
        if "positionnement" in keep:
            for col in ("tlsa", "tlsp", "gls"):
                F[col] = d[col]
                F[f"{col}_chg24"] = d[col].pct_change(24)
            F["tkr_4"] = d["tkr"].rolling(4).mean()
        if "carnet" in keep:
            for k in ("1", "2", "5"):
                b, a = d[f"b{k}"], d[f"a{k}"]
                F[f"imb{k}"] = (b - a) / (b + a)
            F["imb1_chg24"] = F["imb1"] - F["imb1"].shift(24)
            F["depth_liq"] = (d["b2"] + d["a2"]) / d["qv"].rolling(24).sum()
        if "funding" in keep:
            F["fr"] = d["fr"]
        F["target"] = c.shift(-H) / c - 1           # c(t) = prix connu à t
        rows[coin] = F
    panel = pd.concat(rows, names=["coin", "ts"]).reset_index()
    panel = panel[panel["ts"].dt.hour % 4 == 0]          # décisions aux clôtures 4h
    panel = panel[panel["ts"] >= START + pd.Timedelta(days=7)]
    feats = [c for c in panel.columns if c not in ("coin", "ts", "target")]
    # rangs cross-sectionnels
    for col in ("ret_4", "ret_24", "resid_24") + (("imb1",) if "carnet" in keep else ()) + \
               (("oi_chg_24",) if "oi" in keep else ()) + (("tlsp_chg24",) if "positionnement" in keep else ()):
        panel[f"rk_{col}"] = panel.groupby("ts")[col].rank(pct=True)
    feats = [c for c in panel.columns if c not in ("coin", "ts", "target")]
    panel = panel.replace([np.inf, -np.inf], np.nan)
    return panel, feats


def fingerprint(feats, params, lo, hi):
    h = hashlib.sha256(open(__file__, "rb").read())
    h.update(json.dumps([feats, params, str(lo), str(hi)], sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def hl_funding_cost(fund, coin, t0, d):
    if coin not in fund:
        return 0.0
    ft, fr = fund[coin]
    a0, a1 = int(t0.timestamp() * 1000), int((t0 + pd.Timedelta(hours=H)).timestamp() * 1000)
    m = (ft > a0) & (ft <= a1)
    return d * float(fr[m].sum())


def run(panel, feats, model_name, shuffle, fund, manifest):
    rng = np.random.default_rng(SEED)
    ts_all = panel["ts"].sort_values().unique()
    first_monday = (pd.Timestamp(ts_all[0]) + pd.Timedelta(weeks=8)).normalize()
    first_monday += pd.Timedelta(days=(7 - first_monday.weekday()) % 7)
    last = pd.Timestamp(ts_all[-1])
    trades = []
    week = first_monday
    while week + pd.Timedelta(days=7) <= last + pd.Timedelta(hours=4):
        lo, hi = week - pd.Timedelta(weeks=8), week - pd.Timedelta(hours=H)   # purge + embargo 24 h
        tr = panel[(panel["ts"] >= lo) & (panel["ts"] < hi)].dropna(subset=["target"])
        te = panel[(panel["ts"] >= week) & (panel["ts"] < week + pd.Timedelta(days=7))].dropna(subset=["target"])
        if len(tr) < 1000 or te.empty:
            week += pd.Timedelta(days=7)
            continue
        y = tr["target"] - tr.groupby("ts")["target"].transform("mean")
        if shuffle:
            y = y.groupby(tr["ts"]).transform(lambda s: rng.permutation(s.to_numpy()))
        X, Xt = tr[feats], te[feats]
        if model_name == "hgb":
            params = dict(max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0,
                          min_samples_leaf=200, random_state=SEED)
            mdl = HistGradientBoostingRegressor(**params).fit(X, y)
            pred = mdl.predict(Xt)
        else:
            params = dict(alpha=10.0)
            med = X.median()
            sc = StandardScaler().fit(X.fillna(med))
            mdl = Ridge(**params).fit(sc.transform(X.fillna(med)), y)
            pred = mdl.predict(sc.transform(Xt.fillna(med)))
        manifest.append(dict(model=model_name, shuffle=shuffle, week=str(week.date()),
                             fp=fingerprint(feats, params, lo, hi), n_train=len(tr)))
        te = te.assign(pred=pred)
        held_until = {}
        for t, g in te.groupby("ts"):
            g = g.sort_values("pred")
            picks = [(r, -1) for r in g.head(3).itertuples() if r.pred < -COST] + \
                    [(r, 1) for r in g.tail(3).itertuples() if r.pred > COST]
            for r, d in picks:
                if held_until.get(r.coin, pd.Timestamp(0, tz="UTC")) > t:
                    continue
                pnl = d * r.target - COST - hl_funding_cost(fund, r.coin, t, d)
                trades.append((t, r.coin, d, pnl))
                held_until[r.coin] = t + pd.Timedelta(hours=H)
        week += pd.Timedelta(days=7)
    return pd.DataFrame(trades, columns=["ts", "coin", "dir", "ret"])


def weekly(tr, size_frac=1 / 6):
    if tr.empty:
        return pd.Series(dtype=float)
    return tr.set_index("ts")["ret"].mul(size_frac).resample("W-MON", label="left", closed="left").sum()


def tstat(s):
    s = s.dropna()
    return s.mean() / (s.std(ddof=1) / math.sqrt(len(s))) if len(s) > 2 and s.std(ddof=1) > 0 else 0.0


def alfred_weekly(start, end):
    from backtests.backtest_genetic import load_3y_candles, build_features
    from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
    from backtests.backtest_sector import compute_sector_features
    data = load_3y_candles()
    f = build_features(data)
    r = run_window(f, data, compute_sector_features(f, data), load_dxy(), int(start.timestamp() * 1000),
                   int(end.timestamp() * 1000), start_capital=1000.0, oi_data=load_oi(),
                   funding_data=load_funding(), apply_adaptive_modulator=True, aligned=True, margin_check=True)
    s = pd.Series([t["pnl"] / 1000.0 for t in r["trades"]],
                  index=pd.to_datetime([t["exit_t"] for t in r["trades"]], unit="ms", utc=True))
    return s.resample("W-MON", label="left", closed="left").sum()


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    data, idx, cov, keep = load()
    print("Couverture horaire depuis 2026-01-01 (seuil 95 %) :")
    for fam, v in cov.items():
        print(f"  {fam:<15}{v:>7.1%}  {'gardée' if fam in keep else 'RETIRÉE'}")
    panel, feats = features(data, idx, keep)
    print(f"{len(feats)} features, {len(panel)} échantillons (coin × décision 4h)")
    from backtests.backtest_rolling import load_funding
    fund = {k: (np.asarray(v[0]), np.asarray(v[1])) for k, v in load_funding().items()}
    manifest = []
    res = {}
    for name, shuf in (("hgb", False), ("ridge", False), ("hgb", True)):
        tr = run(panel, feats, name, shuf, fund, manifest)
        res[(name, shuf)] = tr
        w = weekly(tr)
        tr.to_csv(os.path.join(OUTDIR, f"trades_{name}{'_shuffled' if shuf else ''}.csv"), index=False)
        print(f"\n{name}{' [CIBLES MÉLANGÉES]' if shuf else ''} : {len(tr)} trades, "
              f"{len(w)} semaines, moy {w.mean()*100:+.2f} %/sem, t {tstat(w):+.2f}, "
              f"net moyen {tr['ret'].mean()*1e4 if len(tr) else 0:+.0f} bp/trade")
    json.dump(manifest, open(os.path.join(OUTDIR, "manifest.json"), "w"), indent=1)

    w = weekly(res[("hgb", False)])
    ws = weekly(res[("hgb", True)])
    wa = alfred_weekly(w.index.min(), w.index.max() + pd.Timedelta(days=7)).reindex(w.index).fillna(0)
    months = w.groupby(w.index.to_period("M")).sum()
    combo = 0.5 * w + 0.5 * wa
    sh = lambda s: s.mean() / s.std(ddof=1) if s.std(ddof=1) > 0 else 0.0
    c1 = w.mean() > 0 and tstat(w) >= 2
    c2 = (months > 0).mean() >= 0.6
    c3 = tstat(ws) < 1
    c4 = sh(combo) > sh(wa)
    print("\n=== Critères (grille) ===")
    print(f"1. modèle seul > 0 et t ≥ 2         : moy {w.mean()*100:+.2f} %/sem, t {tstat(w):+.2f} → {'OK' if c1 else 'ÉCHEC'}")
    print(f"2. ≥ 60 % de mois positifs          : {(months > 0).mean():.0%} ({len(months)} mois) → {'OK' if c2 else 'ÉCHEC'}")
    print(f"3. cibles mélangées t < 1           : t {tstat(ws):+.2f} → {'OK' if c3 else 'ÉCHEC — FUITE'}")
    print(f"4. Sharpe 50/50 > Alfred seul       : {sh(combo):.3f} vs {sh(wa):.3f} → {'OK' if c4 else 'ÉCHEC'}")
    print(f"   corrélation hebdo modèle/Alfred  : {np.corrcoef(w, wa)[0, 1]:+.2f}")
    print(f"\nVERDICT : {'RETENU' if all((c1, c2, c3, c4)) else 'REJETÉ'}")


if __name__ == "__main__":
    main()
