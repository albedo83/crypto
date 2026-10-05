"""Laboratoire de signaux 2026 — étude d'événements indépendante du moteur.

Question : quels signaux ont un rendement net positif en 2026 ?
Discipline (fixée avant toute mesure) :
  - DÉCOUVERTE : 2026-01-01 → 2026-07-01. Seule période utilisée pour classer.
  - VALIDATION : 2026-07-01 → fin des données. N'est lue qu'avec --holdout et
    seulement pour les finalistes figés dans backtests/signal_lab_2026_finalists.json
    (committé AVANT le run holdout).
  - Chaque trade : entrée à la clôture de la bougie 4h t, sortie à la clôture de
    t+h, coût 13 bps (taker + slippage, = backtest) + funding réel payé pendant la
    détention. Un seul trade par (signal, token) tant que le précédent n'est pas
    sorti (pas de chevauchement). Aucune donnée postérieure à la clôture de t.
  - t-stat clusterisé par jour (moyenne des trades de chaque jour).
  - Toutes les variantes testées sont comptées et rapportées.

Usage :
  python3 -m backtests.signal_lab_2026              # découverte
  python3 -m backtests.signal_lab_2026 --holdout    # validation des finalistes
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles
from backtests.backtest_rolling import load_oi, load_funding

COST_BPS = 13.0
BAR_MS = 4 * 3600 * 1000
DISC = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc))
HOLD_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
HORIZONS = (3, 6, 12)                       # 12 h, 24 h, 48 h
HERE = os.path.dirname(os.path.abspath(__file__))
FINALISTS = os.path.join(HERE, "signal_lab_2026_finalists.json")


# ── données alignées ─────────────────────────────────────────────────────
def load():
    data = load_3y_candles()
    ts = np.array([c["t"] for c in data["BTC"]], dtype=np.int64)
    idx = {t: i for i, t in enumerate(ts)}
    coins = sorted(k for k in data if k != "BTC")
    T, N = len(ts), len(coins)
    M = {k: np.full((N, T), np.nan) for k in "ohlcv"}
    for j, cn in enumerate(coins):
        for c in data[cn]:
            i = idx.get(c["t"])
            if i is not None:
                for k in "ohlcv":
                    M[k][j, i] = c[k]
    btc = np.array([c["c"] for c in data["BTC"]], dtype=float)
    close_ms = ts + BAR_MS
    # OI : dernière valeur connue à l'OUVERTURE de la bougie (1 bougie de retard, prudent)
    oi = load_oi()
    OI = np.full((N, T), np.nan)
    for j, cn in enumerate(coins):
        if cn in oi and oi[cn]:
            ot = np.array([x[0] for x in oi[cn]], dtype=np.int64)
            ov = np.array([x[1] for x in oi[cn]], dtype=float)
            pos = np.searchsorted(ot, ts, side="right") - 1
            ok = pos >= 0
            OI[j, ok] = ov[pos[ok]]
    # Funding : cumul des taux horaires → funding entre deux instants = différence
    fu = load_funding()
    FCUM = {}
    for j, cn in enumerate(coins):
        if cn in fu:
            ft, fr = np.asarray(fu[cn][0], dtype=np.int64), np.asarray(fu[cn][1], dtype=float)
            o = np.argsort(ft)
            FCUM[j] = (ft[o], np.concatenate([[0.0], np.cumsum(fr[o])]))
    return dict(ts=ts, close_ms=close_ms, coins=coins, btc=btc, OI=OI, FCUM=FCUM, **M)


def fund_between(D, j, t0_ms, t1_ms):
    """Somme des taux de funding horaires dans (t0, t1]. 0 si inconnu."""
    if j not in D["FCUM"]:
        return 0.0
    ft, cum = D["FCUM"][j]
    a = np.searchsorted(ft, t0_ms, side="right")
    b = np.searchsorted(ft, t1_ms, side="right")
    return float(cum[b] - cum[a])


def ret(x, L):
    out = np.full_like(x, np.nan)
    out[..., L:] = x[..., L:] / x[..., :-L] - 1.0
    return out


def roll_mean_std(x, W):
    """Moyenne/écart-type glissants sur les W valeurs PASSÉES (exclut t)."""
    out_m = np.full_like(x, np.nan)
    out_s = np.full_like(x, np.nan)
    for t in range(W, x.shape[-1]):
        win = x[..., t - W:t]
        out_m[..., t] = np.nanmean(win, axis=-1)
        out_s[..., t] = np.nanstd(win, axis=-1)
    return out_m, out_s


# ── signaux candidats : renvoient une matrice de direction (N, T) ∈ {-1,0,1} ──
def build_signals(D):
    c, h, l, v, btc = D["c"], D["h"], D["l"], D["v"], D["btc"]
    S = {}
    r1 = ret(c, 1)
    # F1 momentum / retournement temporel
    for L, ln in ((6, "1d"), (18, "3d"), (42, "7d")):
        rL = ret(c, L)
        for X in (0.05, 0.10, 0.20):
            S[f"F1_tsmom_{ln}_{int(X*100)}"] = np.where(rL > X, 1, np.where(rL < -X, -1, 0))
            S[f"F1_tsrev_{ln}_{int(X*100)}"] = np.where(rL > X, -1, np.where(rL < -X, 1, 0))
    # F2 classement cross-sectionnel (top/bottom 3)
    for L, ln in ((1, "4h"), (6, "1d"), (18, "3d"), (42, "7d")):
        rL = ret(c, L)
        rk = np.full_like(rL, np.nan)
        for t in range(rL.shape[1]):
            col = rL[:, t]
            ok = ~np.isnan(col)
            if ok.sum() >= 10:
                order = np.argsort(np.argsort(np.where(ok, col, np.inf)))
                rk[ok, t] = order[ok]
        n_ok = np.sum(~np.isnan(rL), axis=0)
        top = rk >= (n_ok - 3)
        bot = rk < 3
        for mode in ("mom", "rev"):
            sgn = 1 if mode == "mom" else -1
            S[f"F2_xs{mode}_{ln}_long"] = np.where(top if mode == "mom" else bot, 1, 0)
            S[f"F2_xs{mode}_{ln}_short"] = np.where(bot if mode == "mom" else top, -1, 0)
            _ = sgn
    # F3 rattrapage BTC (alts en retard)
    for L, ln in ((1, "4h"), (3, "12h"), (6, "1d")):
        bL = ret(btc, L)
        aL = ret(c, L)
        for X in (0.01, 0.02, 0.03):
            up = bL > X
            dn = bL < -X
            S[f"F3_btclead_all_{ln}_{int(X*100)}"] = np.where(up[None, :], 1, np.where(dn[None, :], -1, 0)) * np.ones_like(c)
            lag_up = up[None, :] & (aL < 0.5 * bL[None, :])
            lag_dn = dn[None, :] & (aL > 0.5 * bL[None, :])
            S[f"F3_btclead_lag_{ln}_{int(X*100)}"] = np.where(lag_up, 1, np.where(lag_dn, -1, 0))
    # F4 retournement / suivi du résiduel idiosyncratique (beta 30 j)
    br1 = ret(btc, 1)
    W = 180
    beta = np.full_like(c, np.nan)
    for t in range(W, c.shape[1]):
        x = br1[t - W:t]
        y = r1[:, t - W:t]
        ok = ~np.isnan(x)
        xv = x[ok] - x[ok].mean()
        var = (xv ** 2).sum()
        if var > 0:
            yv = y[:, ok] - np.nanmean(y[:, ok], axis=1, keepdims=True)
            beta[:, t] = np.nansum(yv * xv[None, :], axis=1) / var
    for L, ln in ((6, "1d"), (18, "3d")):
        res = ret(c, L) - beta * ret(btc, L)[None, :]
        for X in (0.05, 0.10, 0.15):
            S[f"F4_residrev_{ln}_{int(X*100)}"] = np.where(res > X, -1, np.where(res < -X, 1, 0))
            S[f"F4_residmom_{ln}_{int(X*100)}"] = np.where(res > X, 1, np.where(res < -X, -1, 0))
    # F6 choc de volume (z sur 30 j passés)
    vm, vs = roll_mean_std(v, 180)
    vz = (v - vm) / np.where(vs > 0, vs, np.nan)
    for k in (3, 5):
        sh = vz > k
        S[f"F6_volshock_follow_{k}"] = np.where(sh, np.sign(r1), 0)
        S[f"F6_volshock_fade_{k}"] = np.where(sh, -np.sign(r1), 0)
    # F7 cassure après compression
    for Nn in (18, 42):
        hh = np.full_like(c, np.nan)
        ll = np.full_like(c, np.nan)
        for t in range(Nn, c.shape[1]):
            hh[:, t] = np.nanmax(h[:, t - Nn:t], axis=1)
            ll[:, t] = np.nanmin(l[:, t - Nn:t], axis=1)
        width = (hh - ll) / c
        wm, ws = roll_mean_std(width, 180)
        tight = width < (wm - 0.8 * ws)
        S[f"F7_squeeze_break_{Nn}"] = np.where(tight & (c > hh), 1, np.where(tight & (c < ll), -1, 0))
    # F8 mèches
    rng = h - l
    atr_m, _ = roll_mean_std(rng, 42)
    lowwick = (np.minimum(D["o"], c) - l) / np.where(rng > 0, rng, np.nan)
    upwick = (h - np.maximum(D["o"], c)) / np.where(rng > 0, rng, np.nan)
    for m in (1.5, 2.5):
        big = rng > m * atr_m
        S[f"F8_wick_{m}"] = np.where(big & (lowwick > 0.6), 1, np.where(big & (upwick > 0.6), -1, 0))
    # F9 funding extrême (cumul 24 h à la clôture, z sur 30 j)
    F24 = np.full_like(c, np.nan)
    for j in range(c.shape[0]):
        if j in D["FCUM"]:
            for t in range(c.shape[1]):
                ce = D["close_ms"][t]
                F24[j, t] = fund_between(D, j, ce - 24 * 3600 * 1000, ce)
    fm, fs = roll_mean_std(F24, 180)
    fz = (F24 - fm) / np.where(fs > 0, fs, np.nan)
    for k in (1.5, 2.5):
        S[f"F9_fund_fade_{k}"] = np.where(fz > k, -1, np.where(fz < -k, 1, 0))
        S[f"F9_fund_follow_{k}"] = np.where(fz > k, 1, np.where(fz < -k, -1, 0))
    # F10 OI + prix (24 h)
    oi6 = ret(D["OI"], 6)
    p6 = ret(c, 6)
    for X in (0.10, 0.20):
        surge = oi6 > X
        S[f"F10_oiprice_follow_{int(X*100)}"] = np.where(surge & (p6 > 0.03), 1, np.where(surge & (p6 < -0.03), -1, 0))
        S[f"F10_oiprice_fade_{int(X*100)}"] = np.where(surge & (p6 > 0.03), -1, np.where(surge & (p6 < -0.03), 1, 0))
    # F12 écart à la moyenne mobile
    for Nn in (18, 42):
        sma = np.full_like(c, np.nan)
        for t in range(Nn, c.shape[1]):
            sma[:, t] = np.nanmean(c[:, t - Nn + 1:t + 1], axis=1)
        dev = c / sma - 1
        dm, ds = roll_mean_std(dev, 180)
        dz = (dev - dm) / np.where(ds > 0, ds, np.nan)
        S[f"F12_madev_fade_{Nn}"] = np.where(dz > 2, -1, np.where(dz < -2, 1, 0))
        S[f"F12_madev_follow_{Nn}"] = np.where(dz > 2, 1, np.where(dz < -2, -1, 0))
    return {k: np.nan_to_num(v_, nan=0).astype(np.int8) for k, v_ in S.items()}


# ── simulation des trades isolés ─────────────────────────────────────────
def trades_for(D, sig, H, t_lo, t_hi):
    c, cms = D["c"], D["close_ms"]
    out = []
    N, T = sig.shape
    for j in range(N):
        nxt = 0
        nz = np.nonzero(sig[j])[0]
        for t in nz:
            if t < nxt or t < t_lo or t >= t_hi or t + H >= T:
                continue
            p0, p1 = c[j, t], c[j, t + H]
            if not (p0 > 0 and p1 > 0):
                continue
            d = int(sig[j, t])
            fund = fund_between(D, j, cms[t], cms[t + H])
            net = d * (p1 / p0 - 1) * 1e4 - COST_BPS - d * fund * 1e4
            out.append((int(cms[t] // 86400000), net, t, j, d))
            nxt = t + H
    return out


def stats(tr):
    if not tr:
        return dict(n=0, mean=0.0, med=0.0, wr=0.0, t=0.0, days=0)
    net = np.array([x[1] for x in tr])
    byday = {}
    for dday, n_, *_ in tr:
        byday.setdefault(dday, []).append(n_)
    dm = np.array([np.mean(v) for v in byday.values()])
    tt = dm.mean() / (dm.std(ddof=1) / math.sqrt(len(dm))) if len(dm) > 2 and dm.std(ddof=1) > 0 else 0.0
    return dict(n=len(net), mean=float(net.mean()), med=float(np.median(net)),
                wr=float((net > 0).mean() * 100), t=float(tt), days=len(dm))


def t_index(D, dt):
    return int(np.searchsorted(D["close_ms"], int(dt.timestamp() * 1000)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    D = load()
    S = build_signals(D)
    T = len(D["ts"])
    d0, d1 = t_index(D, DISC[0]), t_index(D, DISC[1])
    q2 = t_index(D, datetime(2026, 4, 1, tzinfo=timezone.utc))
    h0 = t_index(D, HOLD_START)
    if not args.holdout:
        rows = []
        for name, sig in S.items():
            for H in HORIZONS:
                tr = trades_for(D, sig, H, d0, d1)
                s = stats(tr)
                s1 = stats([x for x in tr if x[2] < q2])
                s2 = stats([x for x in tr if x[2] >= q2])
                rows.append(dict(sig=name, H=H, **s, q1=s1["mean"], q2=s2["mean"], n1=s1["n"], n2=s2["n"]))
        rows.sort(key=lambda r: r["t"], reverse=True)
        print(f"Variantes testées : {len(rows)} ({len(S)} signaux × {len(HORIZONS)} horizons) — DÉCOUVERTE 2026-01-01 → 2026-07-01")
        print(f"{'signal':<30}{'h':>4}{'n':>6}{'jours':>6}{'moy bp':>8}{'méd':>7}{'WR%':>6}{'t':>6}{'T1 moy':>8}{'T2 moy':>8}")
        for r in rows[:40]:
            print(f"{r['sig']:<30}{r['H']*4:>3}h{r['n']:>6}{r['days']:>6}{r['mean']:>+8.0f}{r['med']:>+7.0f}{r['wr']:>6.0f}{r['t']:>6.2f}{r['q1']:>+8.0f}{r['q2']:>+8.0f}")
        with open(os.path.join(HERE, "signal_lab_2026_discovery.json"), "w") as fh:
            json.dump(rows, fh, indent=1)
        print("\n→ backtests/signal_lab_2026_discovery.json")
    else:
        fin = json.load(open(FINALISTS))["finalists"]
        print(f"VALIDATION {HOLD_START.date()} → fin — {len(fin)} finalistes figés")
        print(f"{'signal':<30}{'h':>4}{'n':>6}{'jours':>6}{'moy bp':>8}{'méd':>7}{'WR%':>6}{'t':>6}")
        for f_ in fin:
            tr = trades_for(D, S[f_["sig"]], f_["H"], h0, T)
            s = stats(tr)
            print(f"{f_['sig']:<30}{f_['H']*4:>3}h{s['n']:>6}{s['days']:>6}{s['mean']:>+8.0f}{s['med']:>+7.0f}{s['wr']:>6.0f}{s['t']:>6.2f}")


if __name__ == "__main__":
    main()
