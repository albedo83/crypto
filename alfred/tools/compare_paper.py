"""Parallel-run audit — Alfred paper bot vs legacy paper bot (:8097).

Compares the two bots' OPEN/CLOSE decisions over the run window and
AUTO-CLASSIFIES each divergence:

  STATE   — explained by inherited state differing between the two bots
            (cooldowns, open positions, slot caps, manual pauses, capital).
            Expected during the parallel-run: legacy carries history that
            fresh-booted Alfred doesn't have. Does NOT block phase 4.
  DATA    — explained by a market-data-path difference (oi_gate/disp_gate
            verdicts derived from each bot's own OI/dispersion history,
            which accumulates differently: legacy REST polls vs Alfred WS).
            Worth eyeballing but expected to converge as Alfred's history
            fills. Does NOT block phase 4 unless persistent after 48h.
  PREBOOT — the other bot wasn't running yet at that period. Ignored.
  CASCADE — a CLOSE whose OPEN was itself divergent (no position on the
            other side → nothing to close). Consequence, not a divergence.
  LOGIC   — none of the above: same inputs should have produced the same
            decision and didn't. ✗ BLOCKS PHASE 4.

Usage:
    python3 -m alfred.tools.compare_paper [--hours 168]

Exit code 0 = no LOGIC divergence; 1 otherwise.

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

# SKIP reasons explained by per-bot state (positions/cooldowns/slots/capital).
STATE_REASONS = {
    "cooldown", "already_in_position", "max_direction", "max_macro",
    "max_token", "max_positions", "max_sector", "paused_strategy",
    "modulator_floor",
}
# SKIP reasons derived from each bot's own accumulated market-data history.
DATA_REASONS = {"oi_gate", "disp_gate"}


def load_events(path: str, since_ts: int, kinds: tuple) -> list[dict]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    qmarks = ",".join("?" for _ in kinds)
    rows = db.execute(
        f"SELECT ts, event, symbol, data FROM events "
        f"WHERE ts >= ? AND event IN ({qmarks}) ORDER BY ts",
        (since_ts, *kinds),
    ).fetchall()
    out = []
    for ts, event, symbol, data in rows:
        d = json.loads(data or "{}")
        out.append({"ts": ts, "event": event, "symbol": symbol,
                    "strategy": d.get("strategy"), "dir": d.get("dir"),
                    "reason": d.get("reason"), "period": ts // PERIOD})
    return out


def first_event_ts(path: str) -> int:
    """Earliest event in the DB — proxy for the bot's boot time."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    row = db.execute("SELECT MIN(ts) FROM events").fetchone()
    return int(row[0]) if row and row[0] else 0


def key_open(e: dict) -> tuple:
    return (e["period"], e["symbol"], e["strategy"], e["dir"])


def classify_only(k: tuple, other_skips: list[dict], other_first_ts: int) -> tuple[str, str]:
    """Classify a one-side-only OPEN. Returns (category, detail)."""
    period, symbol, strategy, dirn = k
    period_start = period * PERIOD
    if period_start + PERIOD <= other_first_ts:
        return "PREBOOT", "other bot not running yet"
    # SKIP events carry the symbol; strategy/dir present on some reasons only.
    cands = [s for s in other_skips
             if s["period"] == period and s["symbol"] == symbol]
    for s in cands:
        r = s["reason"] or ""
        if r in STATE_REASONS:
            return "STATE", f"other side skipped: {r}"
        if r in DATA_REASONS:
            return "DATA", f"other side skipped: {r}"
    if cands:
        return "LOGIC", f"other side skipped with non-state reason: {cands[0]['reason']}"
    return "LOGIC", "no SKIP on other side — signal not even detected"


def main() -> int:
    hours = 168
    if "--hours" in sys.argv:
        hours = int(sys.argv[sys.argv.index("--hours") + 1])
    since = int(time.time()) - hours * 3600

    legacy = load_events(LEGACY_DB, since, ("OPEN", "CLOSE"))
    alfred = load_events(ALFRED_DB, since, ("OPEN", "CLOSE"))
    legacy_skips = load_events(LEGACY_DB, since, ("SKIP",))
    alfred_skips = load_events(ALFRED_DB, since, ("SKIP",))
    legacy_boot = first_event_ts(LEGACY_DB)
    alfred_boot = first_event_ts(ALFRED_DB)

    print(f"Fenêtre {hours}h — legacy: {len(legacy)} OPEN/CLOSE, {len(legacy_skips)} SKIP "
          f"| alfred: {len(alfred)} OPEN/CLOSE, {len(alfred_skips)} SKIP")
    print(f"Boot alfred (1er event): {time.strftime('%m-%d %H:%M', time.gmtime(alfred_boot))}")

    lo = {key_open(e) for e in legacy if e["event"] == "OPEN"}
    ao = {key_open(e) for e in alfred if e["event"] == "OPEN"}
    common = lo & ao
    n_logic = 0
    counts = {"STATE": 0, "DATA": 0, "PREBOOT": 0, "LOGIC": 0}
    divergent_opens: set[tuple] = set()  # (symbol, strategy) for cascade closes

    print(f"\nENTRÉES — communes: {len(common)} | legacy-seulement: {len(lo - ao)} "
          f"| alfred-seulement: {len(ao - lo)}")
    for k in sorted(lo - ao):
        cat, detail = classify_only(k, alfred_skips, alfred_boot)
        counts[cat] += 1
        divergent_opens.add((k[1], k[2]))
        mark = "✗" if cat == "LOGIC" else "·"
        print(f"  {mark} [{cat:7}] legacy only: {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]} — {detail}")
    for k in sorted(ao - lo):
        cat, detail = classify_only(k, legacy_skips, legacy_boot)
        counts[cat] += 1
        divergent_opens.add((k[1], k[2]))
        mark = "✗" if cat == "LOGIC" else "·"
        print(f"  {mark} [{cat:7}] alfred only: {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]} — {detail}")
    n_logic += counts["LOGIC"]

    # Exits: match by (symbol, strategy) and compare reasons + timing
    lc = [e for e in legacy if e["event"] == "CLOSE"]
    ac = [e for e in alfred if e["event"] == "CLOSE"]
    print(f"\nSORTIES — legacy: {len(lc)} | alfred: {len(ac)}")
    a_by_sym: dict[str, list] = {}
    for e in ac:
        a_by_sym.setdefault(e["symbol"], []).append(e)
    n_match = n_reason_diff = n_cascade = n_unmatched = 0
    for e in lc:
        cands = [x for x in a_by_sym.get(e["symbol"], [])
                 if x["strategy"] == e["strategy"]
                 and abs(x["ts"] - e["ts"]) <= 2 * 3600]
        if not cands:
            if (e["symbol"], e["strategy"]) in divergent_opens or e["ts"] < alfred_boot:
                n_cascade += 1
                print(f"  · [CASCADE] close legacy sans pendant alfred: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))} "
                      f"(OPEN divergent ou pré-boot)")
            else:
                n_unmatched += 1
                print(f"  ✗ [LOGIC  ] close legacy sans pendant alfred: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))}")
            continue
        best = min(cands, key=lambda x: abs(x["ts"] - e["ts"]))
        if best["reason"] == e["reason"]:
            n_match += 1
        else:
            n_reason_diff += 1
            print(f"  ✗ [LOGIC  ] raison divergente {e['symbol']} {e['strategy']}: "
                  f"legacy={e['reason']} vs alfred={best['reason']} "
                  f"(Δt={best['ts'] - e['ts']:+d}s)")
    n_logic += n_reason_diff + n_unmatched

    print(f"\n  closes: matches={n_match} | raisons divergentes={n_reason_diff} "
          f"| cascade={n_cascade} | non appariés (logic)={n_unmatched}")
    print(f"  entrées divergentes: STATE={counts['STATE']} DATA={counts['DATA']} "
          f"PREBOOT={counts['PREBOOT']} LOGIC={counts['LOGIC']}")

    clean = n_logic == 0
    print(f"\nVerdict: {'✓ gate phase 4 OK (écarts LOGIC: 0)' if clean else f'✗ {n_logic} écart(s) LOGIC à investiguer'}")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
