"""Agrège les archives Binance (backtests/output/binance/) en séries HORAIRES par token.

Un token à la fois, un fichier journalier à la fois (3 Go de RAM partagés avec
Alfred en production — lancer avec `nice -n 19`). Sortie :
backtests/output/binance/hourly/<COIN>.pkl, index = début d'heure UTC.

Colonnes :
  o h l c            prix (bougies 1 min agrégées)
  qv n tbq           volume quote, nombre de trades, volume quote acheteur taker
  oi oiv             open interest (contrats, valeur) — dernière valeur de l'heure
  tlsa tlsp gls tkr  ratios long/short gros traders (comptes, positions), global,
                     ratio taker — moyennes de l'heure
  b1 a1 b2 a2 b5 a5  notionnel cumulé du carnet à −/+1, 2, 5 % du mid — moyenne
  fr                 dernier taux de funding Binance connu
"""
from __future__ import annotations

import glob
import os
import sys
import zipfile

import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "binance")
OUT = os.path.join(ROOT, "hourly")


def _read(path, **kw):
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as fh:
            return pd.read_csv(fh, **kw)


def klines(sym):
    parts = []
    for p in sorted(glob.glob(f"{ROOT}/klines/{sym}-1m-*.zip")):
        d = _read(p, usecols=["open_time", "open", "high", "low", "close",
                              "quote_volume", "count", "taker_buy_quote_volume"])
        d.index = pd.to_datetime(d.pop("open_time"), unit="ms", utc=True)
        g = d.resample("1h")
        parts.append(pd.DataFrame({
            "o": g["open"].first(), "h": g["high"].max(), "l": g["low"].min(),
            "c": g["close"].last(), "qv": g["quote_volume"].sum(),
            "n": g["count"].sum(), "tbq": g["taker_buy_quote_volume"].sum()}))
    return pd.concat(parts) if parts else None


def metrics(sym):
    parts = []
    for p in sorted(glob.glob(f"{ROOT}/metrics/{sym}-metrics-*.zip")):
        d = _read(p)
        d.index = pd.to_datetime(d.pop("create_time"), utc=True)
        g = d.resample("1h")
        parts.append(pd.DataFrame({
            "oi": g["sum_open_interest"].last(), "oiv": g["sum_open_interest_value"].last(),
            "tlsa": g["count_toptrader_long_short_ratio"].mean(),
            "tlsp": g["sum_toptrader_long_short_ratio"].mean(),
            "gls": g["count_long_short_ratio"].mean(),
            "tkr": g["sum_taker_long_short_vol_ratio"].mean()}))
    return pd.concat(parts) if parts else None


def book(sym):
    parts = []
    for p in sorted(glob.glob(f"{ROOT}/bookDepth/{sym}-bookDepth-*.zip")):
        d = _read(p)
        d = d[d["percentage"].isin([-5, -2, -1, 1, 2, 5])]
        d["ts"] = pd.to_datetime(d["timestamp"], utc=True).dt.floor("1h")
        w = d.pivot_table(index="ts", columns="percentage", values="notional", aggfunc="mean")
        w = w.rename(columns={-1: "b1", 1: "a1", -2: "b2", 2: "a2", -5: "b5", 5: "a5"})
        parts.append(w)
    return pd.concat(parts) if parts else None


def funding(sym):
    parts = []
    for p in sorted(glob.glob(f"{ROOT}/fundingRate/{sym}-fundingRate-*.zip")):
        d = _read(p)
        d.index = pd.DatetimeIndex(pd.to_datetime(d.pop("calc_time"), unit="ms", utc=True)).floor("1h")
        parts.append(d[["last_funding_rate"]].rename(columns={"last_funding_rate": "fr"}))
    return pd.concat(parts) if parts else None


def build(coin):
    sym = f"{coin}USDT"
    k = klines(sym)
    if k is None or k.empty:
        return f"{coin}: pas de bougies"
    frames = [k]
    for f in (metrics, book, funding):
        x = f(sym)
        if x is not None and not x.empty:
            frames.append(x[~x.index.duplicated(keep="last")])
    df = pd.concat(frames, axis=1).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if "fr" in df:
        df["fr"] = df["fr"].ffill()
    os.makedirs(OUT, exist_ok=True)
    df.to_pickle(os.path.join(OUT, f"{coin}.pkl"))
    cov = {c: f"{df[c].notna().mean():.0%}" for c in ("c", "oi", "tlsa", "b1", "fr") if c in df}
    return f"{coin}: {len(df)} h {df.index[0]:%m-%d}→{df.index[-1]:%m-%d} couverture {cov}"


if __name__ == "__main__":
    coins = sys.argv[1:] or sorted({os.path.basename(p).split("USDT")[0]
                                    for p in glob.glob(f"{ROOT}/klines/*.zip")})
    for c in coins:
        print(build(c), flush=True)
