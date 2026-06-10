"""Parallel-run audit — Alfred paper bot vs legacy paper bot (:8097).

Compares the two bots' OPEN/CLOSE decisions over the run window:
  - entries: same (symbol, strategy, direction) at the same 4h boundary?
  - exits:   same reason at comparable times?
Timing differences (REST poll offsets, scan jitter) are expected and
classified separately from logic differences — only the latter block phase 4.

Usage:
    python3 -m alfred.tools.compare_paper [--hours 168]

Reads:
    legacy: analysis/output/reversal_ticks.db   (events table)
    alfred: alfred/data/bots/paper/bot.db       (events table)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEGACY_DB = os.path.join(_REPO, "analysis", "output", "reversal_ticks.db")
ALFRED_DB = os.path.join(_REPO, "alfred", "data", "bots", "paper", "bot.db")

# A decision belongs to the 4h period of its timestamp; entries are gated to
# boundaries on both sides so matching by period is exact.
PERIOD = 14400


def load_events(path: str, since_ts: int) -> list[dict]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT ts, event, symbol, data FROM events "
        "WHERE ts >= ? AND event IN ('OPEN','CLOSE') ORDER BY ts", (since_ts,)
    ).fetchall()
    out = []
    for ts, event, symbol, data in rows:
        d = json.loads(data or "{}")
        out.append({"ts": ts, "event": event, "symbol": symbol,
                    "strategy": d.get("strategy"), "dir": d.get("dir"),
                    "reason": d.get("reason"), "period": ts // PERIOD})
    return out


def key_open(e: dict) -> tuple:
    return (e["period"], e["symbol"], e["strategy"], e["dir"])


def main() -> int:
    hours = 168
    if "--hours" in sys.argv:
        hours = int(sys.argv[sys.argv.index("--hours") + 1])
    since = int(time.time()) - hours * 3600

    legacy = load_events(LEGACY_DB, since)
    alfred = load_events(ALFRED_DB, since)
    print(f"Fenêtre {hours}h — legacy: {len(legacy)} events, alfred: {len(alfred)} events")

    lo = {key_open(e) for e in legacy if e["event"] == "OPEN"}
    ao = {key_open(e) for e in alfred if e["event"] == "OPEN"}
    only_legacy = sorted(lo - ao)
    only_alfred = sorted(ao - lo)
    common = lo & ao
    print(f"\nENTRÉES — communes: {len(common)} | legacy-seulement: {len(only_legacy)} "
          f"| alfred-seulement: {len(only_alfred)}")
    for k in only_legacy:
        print(f"  ✗ legacy only: période {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]}")
    for k in only_alfred:
        print(f"  ✗ alfred only: période {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]}")

    # Exits: match by (symbol, strategy) and compare reasons + timing
    lc = [e for e in legacy if e["event"] == "CLOSE"]
    ac = [e for e in alfred if e["event"] == "CLOSE"]
    print(f"\nSORTIES — legacy: {len(lc)} | alfred: {len(ac)}")
    a_by_sym: dict[str, list] = {}
    for e in ac:
        a_by_sym.setdefault(e["symbol"], []).append(e)
    n_match = n_reason_diff = n_unmatched = 0
    for e in lc:
        cands = [x for x in a_by_sym.get(e["symbol"], [])
                 if x["strategy"] == e["strategy"]
                 and abs(x["ts"] - e["ts"]) <= 2 * 3600]
        if not cands:
            n_unmatched += 1
            print(f"  ? close legacy sans pendant alfred: {e['symbol']} {e['strategy']} "
                  f"{e['reason']} @ {time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))}")
            continue
        best = min(cands, key=lambda x: abs(x["ts"] - e["ts"]))
        if best["reason"] == e["reason"]:
            n_match += 1
        else:
            n_reason_diff += 1
            print(f"  ✗ raison divergente {e['symbol']} {e['strategy']}: "
                  f"legacy={e['reason']} vs alfred={best['reason']} "
                  f"(Δt={best['ts'] - e['ts']:+d}s)")
    print(f"  matches: {n_match} | raisons divergentes: {n_reason_diff} "
          f"| non appariés: {n_unmatched}")

    clean = not only_legacy and not only_alfred and n_reason_diff == 0
    print(f"\nVerdict: {'✓ ISO (écarts logiques: 0)' if clean else '✗ écarts à investiguer'}")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
