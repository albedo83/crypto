"""Isolated prospective portfolios; never included in the official bot registry."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from contextlib import nullcontext
from collections import deque
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import features
from .experiment_bot import ExperimentBot
from .market import closed_candles

log = logging.getLogger('alfred')


class FrozenMarket:
    def __init__(self):
        self.states = {}
        self.snapshot = None
        self.last_price_fetch = 0
        self.last_downtime = 0
        self._degraded = False

    def capture(self, master):
        captured = {}
        for sym, state in tuple(master.states.items()):
            clone = copy.copy(state)
            for key, value in vars(state).items():
                if isinstance(value, deque):
                    setattr(clone, key, deque(tuple(value), maxlen=value.maxlen))
            captured[sym] = clone
        states = copy.deepcopy(captured)
        snapshot = copy.deepcopy(master.snapshot)
        self.states.clear()
        self.states.update(states)
        self.snapshot = snapshot
        self.last_price_fetch = master.last_price_fetch
        self.last_downtime = getattr(master, 'last_downtime', 0)
        self._degraded = getattr(master, '_degraded', False)

    def _compute_features_for(self, sym):
        st = self.states.get(sym)
        candles = closed_candles(st.candles_4h) if st else []
        return features.compute_features(candles) if len(candles) >= 50 else None

    def _oi_features(self, sym):
        st = self.states[sym]
        return features.compute_oi_features(list(st.oi_history), st.funding)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class ExperimentManager:
    VERSION = 'shadow-v1'
    LABELS = {'control': 'Témoin', 's1': 'S1 gagnant : +24 h', 'cap3': 'Maximum 3 par stratégie et sens'}

    def __init__(self, master, bots, data_dir):
        self.master, self.official = master, bots
        self.root = Path(data_dir) / 'experiments' / self.VERSION
        self.path = self.root / 'experiment.json'
        self.view = FrozenMarket()
        self.books = []
        self.lock = threading.RLock()
        self.meta = {}
        self._payload = {'enabled': False, 'version': self.VERSION, 'status': 'disabled',
                         'books': [], 'references': [], 'warnings': [], 'comparison_valid': False}
        self.status = 'disabled'
        if os.environ.get('ALFRED_EXPERIMENTS_ENABLED', '1') != '1' or 'live' not in bots:
            return
        try:
            overrides = dict(bots['live'].cfg.overrides)
            digest = hashlib.sha256(json.dumps(overrides, sort_keys=True).encode())
            for name in ('experiments.py', 'experiment_bot.py', 'botinstance.py', 'rules.py',
                         'signals.py', 'features.py', 'settings.py', 'brokers.py', 'persistence.py'):
                digest.update(Path(__file__).with_name(name).read_bytes())
            fingerprint = digest.hexdigest()
            existing = self.path.exists()
            if existing:
                self.meta = json.loads(self.path.read_text())
                if self.meta['fingerprint'] != fingerprint:
                    self.status = 'config_changed'
                    raise ValueError('Configuration/code modifié : cohorte gelée, nouvelle campagne requise')
            elif self.root.exists() and (any(self.root.rglob('state.json')) or any(self.root.rglob('bot.db'))):
                raise ValueError('États existants sans manifeste : reprise refusée')
            self.view.capture(master)
            now = datetime.now(timezone.utc)
            if not existing:
                self.meta = {'fingerprint': fingerprint, 'started': now.timestamp(),
                             'last': now.timestamp(), 'gaps': 0, 'valid': True,
                             'stats': {}, 'anchors': {}, 'checkpoints': {}}
            if existing and now.timestamp() - self.meta['last'] > 120:
                self.meta['gaps'] += 1
                self.meta['valid'] = False
                self.meta['last'] = now.timestamp()
            for variant in self.LABELS:
                if existing:
                    folder = self.root / 'bots' / ('exp_' + variant)
                    if not (folder / 'state.json').is_file() or not (folder / 'bot.db').is_file():
                        raise ValueError('Fichiers de campagne manquants : ' + variant)
                book = ExperimentBot(self.view, str(self.root), variant, param_overrides=overrides)
                self.books.append(book)
                if existing:
                    checkpoint = self.meta['checkpoints'][book.id]
                    if self._checkpoint(book) != checkpoint:
                        raise ValueError('Checkpoint incomplet : ' + book.id)
                    book.load()
                else:
                    book.load()
                    book._last_entry_scan_4h_close = int(now.timestamp()) // 14400 * 14400
            self.status = 'running'
            if not existing:
                for key in ('live', 'paper'):
                    if key in bots:
                        b = bots[key]
                        self.meta['anchors'][key] = {'equity': self._reference_equity(b),
                            'capital': b._capital, 'reset': getattr(b, '_perf_track_start_ts', None), 'started': now.timestamp()}
            self._refresh(now)
            self._save()
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc):
        log.exception('Experiments stopped; official bots unaffected')
        if self.status != 'config_changed':
            self.status = 'error'
        self._payload.update(enabled=True, status=self.status, comparison_valid=False,
                              warnings=['Expériences suspendues : ' + str(exc)])

    def _checkpoint(self, book):
        return {'sha256': hashlib.sha256(Path(book.state_file).read_bytes()).hexdigest(),
                'trades': book.db.conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}

    def _save(self):
        for book in self.books:
            book._save_state()
        self.meta['checkpoints'] = {b.id: self._checkpoint(b) for b in self.books}
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as f:
            json.dump(self.meta, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.path)

    def _reference_equity(self, bot):
        with getattr(bot, '_pos_lock', nullcontext()):
            value = bot._capital + bot._total_pnl
            positions = copy.deepcopy(bot.positions)
        now = datetime.now(timezone.utc).timestamp()
        for sym, pos in positions.items():
            st = self.view.states.get(sym)
            if not st or not math.isfinite(st.price) or st.price <= 0 or st.updated_at <= 0 or now - st.updated_at > 120:
                return None
            value += pos.size_usdt * pos.direction * (st.price / pos.entry_price - 1)
        return value

    def _refresh(self, now):
        ts = now.timestamp()
        rows, warnings = [], []
        stale_market = ts - self.view.last_price_fetch > 120
        if stale_market:
            warnings.append('Prix globaux périmés : aucune validation de la comparaison.')
            if ts - self.meta['started'] > 120:
                self.meta['valid'] = False
                if not self.meta.get('market_stale_counted', False):
                    self.meta['gaps'] += 1
                    self.meta['market_stale_counted'] = True
        if not stale_market:
            self.meta['market_stale_counted'] = False
        self.meta['market_stale'] = stale_market
        for b in self.books:
            row = b.experiment_snapshot(now)
            row.update(id=b.id, variant=b.variant, label=self.LABELS[b.variant], initial_capital=500)
            stat = self.meta['stats'].setdefault(b.id, {'peak': 500, 'dd': 0, 'series': []})
            eq = row['equity']
            if eq is not None:
                stat['peak'] = max(stat['peak'], eq)
                stat['dd'] = max(stat['dd'], (stat['peak'] - eq) / stat['peak'] * 100)
                if not stat['series'] or ts - stat['series'][-1][0] >= 300:
                    stat['series'].append([int(ts), eq])
                    stat['series'] = stat['series'][-12000:]
            row.update(pnl=eq - 500 if eq is not None else None,
                       drawdown_pct=stat['dd'], series=stat['series'])
            warnings.extend(b.id + ': ' + w for w in row['warnings'])
            if row['warnings']:
                self.meta['valid'] = False
            rows.append(row)
        control = rows[0]['equity'] if rows else None
        for row in rows:
            row['delta_vs_control'] = row['equity'] - control if row['equity'] is not None and control is not None else None
        refs = []
        for key, anchor in self.meta['anchors'].items():
            b = self.official[key]
            eq = self._reference_equity(b)
            valid = b._capital == anchor['capital'] and getattr(b, '_perf_track_start_ts', None) == anchor['reset']
            if anchor['equity'] is None and valid and eq is not None:
                anchor['equity'] = eq
                anchor['started'] = ts
            start = anchor['equity']
            pnl = eq - start if valid and eq is not None and start is not None else None
            delayed = anchor.get('started', self.meta['started']) - self.meta['started'] > 1
            note = 'Comptabilité interne, latent brut ; positions et capital initiaux différents.'
            if delayed:
                note += ' Référence ancrée au premier prix frais : ' + _iso(anchor['started'])
            refs.append({'id': key, 'label': b.label, 'equity': eq, 'start_equity': start,
                         'started_at': _iso(anchor.get('started', self.meta['started'])),
                         'pnl_since_start': pnl, 'return_pct': pnl / start * 100 if pnl is not None and start else None,
                         'note': note if valid else 'Référence réinitialisée : comparaison indisponible.'})
        if self.meta['gaps']:
            warnings.append('Lacunes de collecte : comparaison non validée, aucune reconstitution rétrospective.')
        start = self.meta['started']
        self._payload = {'enabled': True, 'version': self.VERSION, 'status': self.status,
            'started_at': _iso(start), 'review_at': _iso(start + 28 * 86400),
            'observation_days': (ts - start) / 86400, 'review_due': ts >= start + 28 * 86400,
            'warnings': warnings, 'gap_count': self.meta['gaps'], 'last_observation_at': _iso(self.meta['last']),
            'books': rows, 'references': refs, 'comparison_valid': self.meta['valid'] and not stale_market,
            'cost_model': {'description': 'Virtuel : frais 9 bps + glissement 4 bps aller-retour ; funding estimé au taux précédent. Liquidité illimitée, sans liquidation simulée. Hors abonnement assistant. Revue à 28 jours, aucune promotion automatique en Live.'}}

    def _step(self, scan):
        with self.lock:
            if self.status != 'running':
                return
            try:
                self.view.capture(self.master)
                now = datetime.now(timezone.utc)
                if now.timestamp() - self.meta['last'] > 120:
                    self.meta['gaps'] += 1
                    self.meta['valid'] = False
                self.meta['last'] = now.timestamp()
                for b in self.books:
                    if scan:
                        b.on_scan(now)
                    else:
                        b.on_tick(now)
                self._refresh(now)
                self._save()
            except Exception as exc:
                self._fail(exc)

    def tick(self):
        self._step(False)

    def scan(self):
        self._step(True)

    def needs_scan(self, last_4h_close):
        with self.lock:
            return self.status == 'running' and any(b._last_entry_scan_4h_close < last_4h_close for b in self.books)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self._payload)

    def close(self):
        with self.lock:
            if self.status == 'running':
                try:
                    self._save()
                except Exception as exc:
                    self._fail(exc)
            for b in self.books:
                b.db.close()
