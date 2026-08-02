"""Collecte des séries venue B — LECTURES PUBLIQUES, aucune clé, aucun ordre.

Met en cache dans `backtests/output/basis_cache.db` pour que le run de verdict
soit rejouable sans re-solliciter les API.

Trois séries par token :
  - funding perp Binance   (fapi/v1/fundingRate)
  - funding perp Bybit     (v5/market/funding/history)
  - klines spot 4 h Binance (api/v3/klines)

Usage : python3 -m backtests.basis.collect
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(ROOT, "backtests", "output", "basis_cache.db")
START_MS = int(datetime(2023, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
PAUSE = 0.10


def gj(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429, 503) and k < tries - 1:
                time.sleep(2 ** k + 1)
                continue
            return {"__error__": f"HTTP {e.code}"}
        except Exception as e:
            if k < tries - 1:
                time.sleep(1.5)
                continue
            return {"__error__": str(e)[:90]}
    return {"__error__": "épuisé"}


def db():
    con = sqlite3.connect(CACHE)
    con.execute("""CREATE TABLE IF NOT EXISTS funding_b(
        venue TEXT, symbol TEXT, ts INTEGER, rate REAL,
        PRIMARY KEY(venue, symbol, ts))""")
    con.execute("""CREATE TABLE IF NOT EXISTS spot_b(
        venue TEXT, symbol TEXT, ts INTEGER, close REAL,
        PRIMARY KEY(venue, symbol, ts))""")
    return con


def have(con, table, venue, sym):
    r = con.execute(f"SELECT COUNT(*), MAX(ts) FROM {table} "
                    f"WHERE venue=? AND symbol=?", (venue, sym)).fetchone()
    return r[0], r[1]


def binance_funding(con, sym):
    n, last = have(con, "funding_b", "binance", sym)
    cur = (last + 1) if last else START_MS
    added = 0
    while True:
        r = gj(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}USDT"
               f"&startTime={cur}&limit=1000")
        time.sleep(PAUSE)
        if not isinstance(r, list) or not r:
            break
        rows = [("binance", sym, int(x["fundingTime"]), float(x["fundingRate"]))
                for x in r]
        con.executemany("INSERT OR IGNORE INTO funding_b VALUES(?,?,?,?)", rows)
        con.commit()
        added += len(rows)
        nxt = max(x[2] for x in rows) + 1
        if nxt <= cur or len(r) < 1000:
            break
        cur = nxt
    return added


def bybit_funding(con, sym):
    n, last = have(con, "funding_b", "bybit", sym)
    end = int(time.time() * 1000)
    floor = (last + 1) if last else START_MS
    added = 0
    while end > floor:
        r = gj(f"https://api.bybit.com/v5/market/funding/history?category=linear"
               f"&symbol={sym}USDT&startTime={floor}&endTime={end}&limit=200")
        time.sleep(PAUSE)
        if not isinstance(r, dict) or r.get("retCode") != 0:
            break
        lst = r["result"]["list"]
        if not lst:
            break
        rows = [("bybit", sym, int(x["fundingRateTimestamp"]),
                 float(x["fundingRate"])) for x in lst]
        con.executemany("INSERT OR IGNORE INTO funding_b VALUES(?,?,?,?)", rows)
        con.commit()
        added += len(rows)
        oldest = min(x[2] for x in rows)
        if oldest >= end:
            break
        end = oldest - 1
        if len(lst) < 200:
            break
    return added


def binance_spot_4h(con, sym):
    n, last = have(con, "spot_b", "binance", sym)
    cur = (last + 1) if last else START_MS
    added = 0
    while True:
        r = gj(f"https://api.binance.com/api/v3/klines?symbol={sym}USDT"
               f"&interval=4h&startTime={cur}&limit=1000")
        time.sleep(PAUSE)
        if not isinstance(r, list) or not r:
            break
        rows = [("binance", sym, int(x[0]), float(x[4])) for x in r]
        con.executemany("INSERT OR IGNORE INTO spot_b VALUES(?,?,?,?)", rows)
        con.commit()
        added += len(rows)
        nxt = max(x[2] for x in rows) + 1
        if nxt <= cur or len(r) < 1000:
            break
        cur = nxt
    return added


def main() -> int:
    inv = json.load(open(os.path.join(ROOT, "analysis", "output",
                                      "basis_phase0.json")))
    universe = [s for s in inv["universe"] if inv["usable"][s]["both"]]
    con = db()
    print(f"Collecte pour {len(universe)} tokens (cache : {CACHE})", flush=True)
    for i, s in enumerate(universe, 1):
        a = binance_funding(con, s) if inv["binance"][s]["perp"] else 0
        b = bybit_funding(con, s) if inv["bybit"][s]["perp"] else 0
        c = binance_spot_4h(con, s) if inv["binance"][s]["spot"] else 0
        nb = have(con, "funding_b", "binance", s)[0]
        ny = have(con, "funding_b", "bybit", s)[0]
        ns = have(con, "spot_b", "binance", s)[0]
        print(f"  [{i:2d}/{len(universe)}] {s:7s} +{a:5d}/+{b:5d}/+{c:5d}  "
              f"→ BN fund {nb:6d} · BY fund {ny:6d} · BN spot4h {ns:6d}",
              flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
