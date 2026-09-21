"""Idempotent accounting of archived external-context API responses."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from ai_cost import cost_event


def record(response, ts, data_dir):
    if not response.get('id') or not response.get('usage'):
        return False
    db = Path(data_dir)/'bots/live/bot.db'
    if not db.exists():
        raise FileNotFoundError('Live accounting database missing')
    payload = cost_event('external', response.get('model'), response['usage'])
    payload['response_id'] = response['id']
    with closing(sqlite3.connect(db, timeout=10)) as c, c:
        c.execute('CREATE TABLE IF NOT EXISTS external_usage_receipts (response_id TEXT PRIMARY KEY)')
        inserted = c.execute('INSERT OR IGNORE INTO external_usage_receipts VALUES (?)', (response['id'],)).rowcount
        if inserted:
            c.execute('INSERT INTO events(ts,event,symbol,data) VALUES (?,?,?,?)',
                      (ts, 'AI_COST', None, json.dumps(payload)))
    return bool(inserted)


def backfill(data_dir):
    data_dir = Path(data_dir)
    count = 0
    def walk(value, ts):
        nonlocal count
        if isinstance(value, dict):
            ts = value.get('started_at', ts)
            if str(value.get('id', '')).startswith('msg_') and 'usage' in value:
                count += record(value, ts, data_dir)
                return
            for child in value.values():
                walk(child, ts)
        elif isinstance(value, list):
            for child in value:
                walk(child, ts)
    for path in sorted((data_dir/'external_context/batches').glob('*.json')):
        stamp = int(path.name.split('-')[0].split('.')[0])/1e9
        walk(json.loads(path.read_text()), stamp)
    return count
