from contextlib import closing
"""Event-driven review health. No API calls or trading side effects."""
import json
import math
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEARTBEAT_MAX_S = 600  # two-minute cron; five missed cycles
REQUEST_MAX_S = 300  # subprocess timeout 120s plus scheduling margin


def check_review_health(now=None, root=None):
    now = time.time() if now is None else now
    root = Path(root or ROOT)
    row = dict(dependance='revue de position', source='review', status='OK',
               age_h=None, max_age_h=None, checked_at=now,
               note='Sur événement ; absence d’appel ≠ panne. Détecteur et demandes surveillés.')
    def finish(status, message):
        return dict(row, status=status, message=message)
    try:
        heartbeat = float(json.loads((root/'alfred/data/attention_state.json').read_text())['last_scan'])
        if not math.isfinite(heartbeat) or heartbeat <= 0 or heartbeat > now + 60:
            return finish('INVALID', 'Horodatage du détecteur invalide')
        row['detector_age_s'] = round(now-heartbeat, 1)
        if now-heartbeat > HEARTBEAT_MAX_S:
            return finish('STALE', 'Détecteur sans passage depuis plus de 10 minutes')
        path = root/'alfred/data/bots/live/bot.db'
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db, db:
            events = db.execute("SELECT ts,event,data FROM events WHERE event IN "
                "('REVIEW_REQUEST','REVIEW_RESULT','POSITION_REVIEW','POSITION_REVIEW_ERROR') ORDER BY ts,rowid").fetchall()
        requests, results = {}, {}
        last_ok = last_error = None
        for ts, event, payload in events:
            data = json.loads(payload)
            if event == 'REVIEW_REQUEST':
                requests[data['request_id']] = ts
            elif event == 'REVIEW_RESULT':
                results[data['request_id']] = data
            elif event == 'POSITION_REVIEW':
                last_ok = ts
            else:
                last_error = ts
        row['last_success_ts'] = last_ok
        row['age_h'] = None if last_ok is None else round((now-last_ok)/3600, 2)
        pending = 0
        for request_id, ts in requests.items():
            result = results.get(request_id)
            if result is None:
                if now-ts > REQUEST_MAX_S:
                    return finish('BROKEN', 'Une demande de revue est restée sans résultat au-delà de 5 minutes')
                pending += 1
            elif result.get('status') == 'error':
                # A later success recovers a failed call, but never hides an
                # unresolved request: each request above retains its own ID.
                if last_ok is None or ts >= last_ok:
                    return finish('BROKEN', 'Échec de revue sans succès ultérieur')
            elif result.get('status') not in ('success', 'skipped'):
                return finish('INVALID', 'Résultat de revue inconnu')
        if last_error is not None and (last_ok is None or last_error > last_ok):
            return finish('BROKEN', 'Dernière revue en erreur, sans succès ultérieur')
        if pending:
            return finish('OK', 'Détecteur actif ; revue en cours (délai de 5 minutes non dépassé)')
        return finish('OK', 'Détecteur actif ; aucune demande de revue en attente')
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return finish('MISSING', 'Contrôle de revue indisponible : '+type(exc).__name__)
