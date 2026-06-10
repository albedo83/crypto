"""Export du funding horaire de market.db vers funding_history.db (backtests).

Même doctrine que export_candles : market.db = store canonique de la fenêtre
Alfred (table funding_hourly, sync horaire REST par le master), le deep
history pré-Alfred reste dans backtests/output/funding_history.db. Le merge
unionne par (symbol, ts) — INSERT OR IGNORE, l'existant n'est jamais réécrit.

Lecture SEULE de market.db (single-writer = le master). Si la table est vide
ou en retard (master pas redémarré depuis l'ajout du sync, ou Alfred down),
le fallback REST reste `python3 -m backtests.fetch_funding_history`.

Usage :
    python3 -m alfred.tools.export_history          # merge funding
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

MARKET_DB = os.path.join(_REPO, "alfred", "data", "market.db")
FUNDING_DB = os.path.join(_REPO, "backtests", "output", "funding_history.db")


def main() -> None:
    if not os.path.exists(MARKET_DB):
        print(f"market.db absent ({MARKET_DB}) — fallback : "
              "python3 -m backtests.fetch_funding_history")
        sys.exit(1)
    src = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    try:
        rows = src.execute(
            "SELECT symbol, ts, rate, premium FROM funding_hourly ORDER BY symbol, ts"
        ).fetchall()
    except sqlite3.OperationalError:
        print("table funding_hourly absente — le master n'a pas encore tourné "
              "avec le sync funding (restart requis). Fallback : "
              "python3 -m backtests.fetch_funding_history")
        sys.exit(1)
    finally:
        src.close()

    if not rows:
        print("funding_hourly vide — premier sync du master pas encore passé. "
              "Fallback : python3 -m backtests.fetch_funding_history")
        sys.exit(1)

    last_ts = max(r[1] for r in rows)
    age_h = (time.time() * 1000 - last_ts) / 3600_000
    dst = sqlite3.connect(FUNDING_DB)
    dst.execute("""CREATE TABLE IF NOT EXISTS funding (
        symbol TEXT NOT NULL, ts INTEGER NOT NULL,
        funding_rate REAL, premium REAL,
        PRIMARY KEY (symbol, ts))""")
    before = dst.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
    dst.executemany("INSERT OR IGNORE INTO funding VALUES (?,?,?,?)", rows)
    dst.commit()
    after = dst.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
    dst.close()
    print(f"funding_hourly : {len(rows)} rows lues (dernière il y a {age_h:.1f}h), "
          f"{after - before} nouvelles mergées → funding_history.db ({after} total)")
    if age_h > 3:
        print(f"⚠ market.db funding en retard de {age_h:.1f}h — compléter via "
              "python3 -m backtests.fetch_funding_history")


if __name__ == "__main__":
    main()
