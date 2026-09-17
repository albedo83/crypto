"""Read-only coverage audit. Counts observations; never estimates causal PNL."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def position_key(symbol, data):
    entry = data.get('entry_ts_ms')
    if not symbol or not isinstance(entry, (int, float)) or entry <= 0:
        return None
    return symbol, int(entry)


def summarize(events):
    groups = {}
    notes = Counter()
    all_positions = set()
    shadow_positions = set()
    missing_identity = 0
    for ts, symbol, data in events:
        key = position_key(symbol, data)
        if key is None:
            missing_identity += 1
        else:
            all_positions.add(key)
        action, note = data.get('action'), data.get('note')
        acted = data.get('acted') is True
        notes[(str(action), acted, str(note))] += 1
        group_key = (data.get('alfred_version'), data.get('prompt_hash'),
                     data.get('lock_mode'), data.get('cut_mode'))
        group = groups.setdefault(group_key, {'events': 0, 'positions': set(),
                                              'shadow_lock_events_excluded': 0})
        group['events'] += 1
        if key:
            group['positions'].add(key)
        # Matches the current scorecard's note filter, before dedup/trade joins.
        if action == 'LOCK' and not acted and note == 'lock_shadow':
            group['shadow_lock_events_excluded'] += 1
            if key:
                shadow_positions.add(key)
    return {
        'events': sum(notes.values()), 'distinct_positions': len(all_positions),
        'missing_position_identity': missing_identity,
        'shadow_lock_positions_excluded_by_note': len(shadow_positions),
        'notes': [{'action': a, 'acted': b, 'note': n, 'events': count}
                  for (a,b,n),count in sorted(notes.items())],
        'cohorts': [dict(alfred_version=k[0], prompt_hash=k[1], lock_mode=k[2],
                         cut_mode=k[3], events=v['events'],
                         distinct_positions=len(v['positions']),
                         shadow_lock_events_excluded=v['shadow_lock_events_excluded'])
                    for k,v in groups.items()],
        'causal_pnl_estimated': False,
        'limitations': [
            'Counts refer to logged decisions, not independent trades or measured edge.',
            'Positions may appear in several prompt cohorts; cohort counts are not additive.',
            'Current scorecard excludes note=lock_shadow and pools prompt versions.',
            'Current LOCK replay applies its stop from entry, before the decision timestamp.',
            '4h OHLC cannot establish tick-level stop ordering or execution prices.',
        ],
    }


def audit(db_path, since, until):
    path = Path(db_path).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute("SELECT ts,symbol,data FROM events WHERE "
                          "event='ARBITER_EXIT_DECISION' AND ts>=? AND ts<=? "
                          "ORDER BY ts,rowid", (since, until)).fetchall()
    events = [(ts,sym,json.loads(raw)) for ts,sym,raw in rows]
    result = summarize(events)
    result.update(source=str(path), since=since, until=until,
                  first_event_ts=rows[0][0] if rows else None,
                  last_event_ts=rows[-1][0] if rows else None)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', required=True)
    ap.add_argument('--since', required=True, help='UTC ISO timestamp')
    ap.add_argument('--until', help='UTC ISO timestamp; default now')
    args = ap.parse_args()
    def stamp(s):
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('Timezone required')
        return dt.timestamp()
    since = stamp(args.since)
    until = stamp(args.until) if args.until else datetime.now(timezone.utc).timestamp()
    if since > until:
        raise ValueError('since exceeds until')
    print(json.dumps(audit(args.db, since, until), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
