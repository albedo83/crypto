"""Téléchargement des archives publiques Binance USDⓈ-M futures (data.binance.vision).

Jeux : bougies 1 min (dont volume taker acheteur), métriques 5 min (OI, ratios
long/short des gros traders et du marché, ratio taker), profondeur de carnet
(bookDepth : volume cumulé à ±0,2…5 % du mid), funding. Période 2026-01-01 →
veille. Idempotent : saute les fichiers déjà présents. Zips conservés tels quels
dans backtests/output/binance/<jeu>/.

    python3 -m backtests.binance_fetch [--only klines,metrics,bookDepth,fundingRate]
"""
from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from backtests.backtest_genetic import load_3y_candles

BASE = "https://data.binance.vision/data/futures/um"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "binance")
START = date(2026, 1, 1)


def jobs(symbols, only):
    today = datetime.now(timezone.utc).date()
    last_full_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    months = []
    m = START
    while m <= last_full_month:
        months.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    days = [START + timedelta(days=i) for i in range((today - START).days)]
    tail_days = [d for d in days if d > (today.replace(day=1) - timedelta(days=1))]
    for s in symbols:
        if "klines" in only:
            for m in months:
                yield ("klines", f"monthly/klines/{s}/1m/{s}-1m-{m:%Y-%m}.zip")
            for d in tail_days:
                yield ("klines", f"daily/klines/{s}/1m/{s}-1m-{d:%Y-%m-%d}.zip")
        if "fundingRate" in only:
            for m in months:
                yield ("fundingRate", f"monthly/fundingRate/{s}/{s}-fundingRate-{m:%Y-%m}.zip")
        for ds in ("metrics", "bookDepth"):
            if ds in only:
                for d in days:
                    yield (ds, f"daily/{ds}/{s}/{s}-{ds}-{d:%Y-%m-%d}.zip")


def fetch(job):
    ds, rel = job
    dst = os.path.join(OUT, ds, os.path.basename(rel))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return "skip"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{BASE}/{rel}", timeout=60) as r:
                data = r.read()
            tmp = dst + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dst)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return f"404 {rel}"
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return f"FAIL {rel}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="klines,metrics,bookDepth,fundingRate")
    only = set(ap.parse_args().only.split(","))
    coins = sorted(load_3y_candles().keys())
    symbols = [f"{c}USDT" for c in coins]
    js = list(jobs(symbols, only))
    print(f"{len(symbols)} symboles, {len(js)} fichiers", flush=True)
    stats = {"ok": 0, "skip": 0, "404": 0, "FAIL": 0}
    missing = []
    with ThreadPoolExecutor(12) as ex:
        for i, res in enumerate(ex.map(fetch, js), 1):
            k = res.split()[0]
            stats[k] = stats.get(k, 0) + 1
            if k in ("404", "FAIL"):
                missing.append(res)
            if i % 500 == 0:
                print(f"  {i}/{len(js)} {stats}", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "missing.txt"), "w") as fh:
        fh.write("\n".join(missing))
    print("terminé", stats, f"— manquants listés dans {OUT}/missing.txt", flush=True)


if __name__ == "__main__":
    main()
