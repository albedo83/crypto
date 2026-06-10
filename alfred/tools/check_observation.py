"""Phase-2 observation report — reads alfred/data/market.db and summarizes
the evidence for the go/no-go criteria:

  - tick coverage (rows/hour, gaps in the REST poll)
  - candle audits (CANDLE_AUDIT events: mismatches WS vs REST)
  - gap repairs and WS reconnects
  - trade_flow coverage
  - market_snapshots cadence

Usage:
    python3 -m alfred.tools.check_observation [path/to/market.db]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "market.db")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    now = int(time.time())

    first_ts, last_ts, n_ticks = db.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM ticks").fetchone()
    if not first_ts:
        print("Pas encore de ticks.")
        return 1
    hours = max((last_ts - first_ts) / 3600, 1e-9)
    print(f"Observation depuis {time.strftime('%Y-%m-%d %H:%M', time.localtime(first_ts))} "
          f"({hours:.1f}h)")
    print(f"Ticks: {n_ticks} rows ({n_ticks / hours:.0f}/h), "
          f"dernier il y a {now - last_ts}s")

    n_snap = db.execute("SELECT COUNT(DISTINCT ts) FROM market_snapshots").fetchone()[0]
    print(f"Market snapshots: {n_snap} heures couvertes")

    n_flow, flow_syms = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM trade_flow").fetchone()
    print(f"Trade flow: {n_flow} buckets sur {flow_syms} symboles")

    for ev in ("WS_RECONNECT", "CANDLE_GAP_REPAIR", "BACKFILL"):
        n = db.execute("SELECT COUNT(*) FROM events WHERE event=?", (ev,)).fetchone()[0]
        print(f"Events {ev}: {n}")

    audits = db.execute(
        "SELECT ts, data FROM events WHERE event='CANDLE_AUDIT' ORDER BY ts").fetchall()
    n_bad_audits = 0
    bad_details = []
    for ts, data in audits:
        rep = json.loads(data or "{}")
        bad = {k: v for k, v in rep.items()
               if v.get("mismatch") or v.get("missing_in_mem")
               or v.get("status", "").startswith("rest_error")}
        if bad:
            n_bad_audits += 1
            bad_details.append((ts, bad))
    print(f"Candle audits: {len(audits)} total, {n_bad_audits} avec écarts")
    for ts, bad in bad_details[-5:]:
        print(f"  - {time.strftime('%m-%d %H:%M', time.localtime(ts))}: {bad}")

    verdict = (len(audits) > 0 and n_bad_audits == 0
               and now - last_ts < 300)
    print(f"\nVerdict provisoire: {'✓ PROPRE' if verdict else '✗ À INVESTIGUER'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
