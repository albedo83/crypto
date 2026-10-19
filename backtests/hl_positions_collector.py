"""Collecteur des positions publiques Hyperliquid (pistes « carte des liquidations »
et « indice des perdants », docs/vague1_2026_10_grid.md).

Lectures publiques uniquement (classement stats-data + endpoint info). Aucune clé.
Liste de 300 comptes FIGÉE au premier passage (wallets.json) :
  - 150 plus gros comptes actifs (volume du mois > 0), par valeur de compte ;
  - 150 perdants persistants actifs : P&L total < 0, P&L du mois < 0, volume de
    la semaine > 0, par volume du mois.
Chaque passage : clearinghouseState de chaque compte + allMids → SQLite.
Lent par construction (0,5 s entre requêtes) : l'IP est partagée avec Alfred.

    python3 -m backtests.hl_positions_collector     # cron */15
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
import time
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "hl_positions")
WALLETS = os.path.join(OUT, "wallets.json")
DB = os.path.join(OUT, "positions.db")
INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
N_BIG, N_LOSERS, PAUSE_S = 150, 150, 0.5


def _post(payload, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(INFO, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def _win(row, name):
    for w, v in row.get("windowPerformances", []):
        if w == name:
            return {k: float(x) for k, x in v.items()}
    return {"pnl": 0.0, "roi": 0.0, "vlm": 0.0}


def select_wallets():
    with urllib.request.urlopen(LEADERBOARD, timeout=120) as r:
        rows = json.loads(r.read())["leaderboardRows"]
    for x in rows:
        x["_av"] = float(x.get("accountValue") or 0)
        x["_m"], x["_w"], x["_all"] = _win(x, "month"), _win(x, "week"), _win(x, "allTime")
    big = sorted((x for x in rows if x["_m"]["vlm"] > 0), key=lambda x: -x["_av"])[:N_BIG]
    big_set = {x["ethAddress"] for x in big}
    losers = sorted((x for x in rows if x["ethAddress"] not in big_set and x["_all"]["pnl"] < 0
                     and x["_m"]["pnl"] < 0 and x["_w"]["vlm"] > 0),
                    key=lambda x: -x["_m"]["vlm"])[:N_LOSERS]
    sel = [{"address": x["ethAddress"], "group": g, "account_value": x["_av"],
            "pnl_all": x["_all"]["pnl"], "pnl_month": x["_m"]["pnl"], "vlm_month": x["_m"]["vlm"]}
           for g, xs in (("big", big), ("loser", losers)) for x in xs]
    meta = {"selected_at": int(time.time()), "leaderboard_rows": len(rows),
            "rule": "big: top 150 accountValue (vlm mois > 0) ; loser: pnl total < 0, pnl mois < 0, "
                    "vlm semaine > 0, top 150 par vlm mois", "wallets": sel}
    json.dump(meta, open(WALLETS, "w"), indent=1)
    return sel


def init_db(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS runs(ts INTEGER PRIMARY KEY, ok INTEGER, fail INTEGER, dur_s REAL);
    CREATE TABLE IF NOT EXISTS accounts(ts INTEGER, address TEXT, account_value REAL, margin_used REAL,
        ntl_pos REAL, PRIMARY KEY(ts, address));
    CREATE TABLE IF NOT EXISTS positions(ts INTEGER, address TEXT, coin TEXT, szi REAL, entry_px REAL,
        liq_px REAL, position_value REAL, unrealized_pnl REAL, leverage REAL, PRIMARY KEY(ts, address, coin));
    CREATE TABLE IF NOT EXISTS mids(ts INTEGER, coin TEXT, px REAL, PRIMARY KEY(ts, coin));
    """)


def main():
    os.makedirs(OUT, exist_ok=True)
    lock = open(os.path.join(OUT, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("passage précédent encore en cours — on saute")
        return 0
    wallets = json.load(open(WALLETS))["wallets"] if os.path.exists(WALLETS) else select_wallets()
    con = sqlite3.connect(DB, timeout=30)
    init_db(con)
    ts, t0, ok, fail = int(time.time()), time.time(), 0, 0
    mids = _post({"type": "allMids"}) or {}
    con.executemany("INSERT OR REPLACE INTO mids VALUES(?,?,?)",
                    [(ts, c, float(p)) for c, p in mids.items() if not c.startswith("@")])
    for w in wallets:
        st = _post({"type": "clearinghouseState", "user": w["address"]})
        time.sleep(PAUSE_S)
        if not st or "marginSummary" not in st:
            fail += 1
            continue
        ok += 1
        ms = st["marginSummary"]
        con.execute("INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?)",
                    (ts, w["address"], float(ms["accountValue"]), float(ms["totalMarginUsed"]),
                     float(ms["totalNtlPos"])))
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            con.execute("INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,?,?,?)",
                        (ts, w["address"], p["coin"], float(p["szi"]), float(p["entryPx"] or 0),
                         float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                         float(p["positionValue"]), float(p["unrealizedPnl"]),
                         float(p["leverage"]["value"]) if p.get("leverage") else None))
    con.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)", (ts, ok, fail, round(time.time() - t0, 1)))
    con.commit()
    con.close()
    print(f"{time.strftime('%m-%d %H:%M', time.gmtime(ts))} : {ok} comptes lus, {fail} échecs, "
          f"{time.time() - t0:.0f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
