import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alfred.experiments import ExperimentManager, FrozenMarket
from alfred.models import SymbolState


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = datetime.now(timezone.utc)
        self.master = SimpleNamespace(states={'SOL': SymbolState(price=100, updated_at=self.now.timestamp())},
                                      snapshot=None, last_price_fetch=self.now.timestamp())
        self.bots = {name: SimpleNamespace(cfg=SimpleNamespace(overrides={}), _capital=500,
                                          _total_pnl=0, positions={}, label=name, _perf_track_start_ts=1)
                     for name in ('live', 'paper')}
        self.managers = []
        self.addCleanup(self.close_all)

    def close_all(self):
        for m in self.managers:
            for b in m.books:
                try: b.db.close()
                except Exception: pass

    def manager(self):
        with patch.dict(os.environ, {'ALFRED_EXPERIMENTS_ENABLED': '1'}):
            m = ExperimentManager(self.master, self.bots, self.tmp.name)
        self.managers.append(m)
        return m

    def test_new_cohort_preserves_previous_campaign(self):
        old = Path(self.tmp.name)/'experiments'/'shadow-v1'
        old.mkdir(parents=True)
        marker = old/'experiment.json'
        marker.write_text('{"old_campaign": true}')
        m = self.manager()
        self.assertEqual(m.VERSION, 'shadow-v2')
        self.assertEqual(m.status, 'running')
        self.assertEqual(marker.read_text(), '{"old_campaign": true}')
        self.assertNotEqual(m.root, old)

    def test_fresh_books_independent_and_official_registry_untouched(self):
        original = copy.deepcopy(self.bots)
        m = self.manager()
        self.assertEqual(m.status, 'running')
        self.assertEqual([b.id for b in m.books], ['exp_control', 'exp_s1', 'exp_cap3'])
        self.assertEqual(self.bots, original)
        self.assertEqual([r['equity'] for r in m.snapshot()['books']], [500, 500, 500])
        m.books[0]._capital = 400
        self.assertEqual(m.books[1]._capital, 500)
        self.assertEqual(self.bots, original)
        self.assertTrue(m.snapshot()['comparison_valid'])

    def test_roundtrip_checkpoints_and_counters(self):
        m = self.manager()
        m.books[1].s1_extensions = 2
        m.books[2].cohort_skips = 3
        m._save()
        resumed = self.manager()
        self.assertEqual(resumed.status, 'running')
        self.assertEqual(resumed.books[1].s1_extensions, 2)
        self.assertEqual(resumed.books[2].cohort_skips, 3)
        self.assertEqual(resumed.meta['started'], m.meta['started'])

    def test_checkpoint_corruption_fails_closed(self):
        m = self.manager()
        path = Path(m.books[0].state_file)
        path.write_text(path.read_text()+'\n')
        with self.assertLogs('alfred', level='ERROR'):
            resumed = self.manager()
        self.assertEqual(resumed.status, 'error')
        self.assertFalse(resumed.snapshot()['comparison_valid'])
        self.assertEqual(set(self.bots), {'live', 'paper'})

    def test_config_change_freezes_cohort(self):
        m = self.manager()
        before = m.path.read_bytes()
        self.bots['live'].cfg.overrides['max_macro_slots'] = 3
        with self.assertLogs('alfred', level='ERROR'): resumed = self.manager()
        self.assertEqual(resumed.status, 'config_changed')
        self.assertEqual(m.path.read_bytes(), before)
        self.assertEqual(resumed.books, [])

    def test_missing_manifest_with_existing_db_rejected(self):
        root = Path(self.tmp.name)/'experiments'/ExperimentManager.VERSION/'bots'/'exp_control'
        root.mkdir(parents=True); (root/'bot.db').touch()
        with self.assertLogs('alfred', level='ERROR'): m = self.manager()
        self.assertEqual(m.status, 'error')
        self.assertFalse(m.path.exists())

    def test_frozen_market_independent_from_master_and_shared_by_books(self):
        m = self.manager()
        before = m.view.states['SOL'].price
        self.master.states['SOL'].price = 123
        self.assertEqual(m.view.states['SOL'].price, before)
        self.assertTrue(all(b.states is m.view.states for b in m.books))
        m.tick()
        self.assertTrue(all(b.states['SOL'].price == 123 for b in m.books))
        self.assertEqual(self.master.states['SOL'].price, 123)
        result = m.snapshot(); result['books'].clear()
        self.assertEqual(len(m.snapshot()['books']), 3)

    def test_gap_invalidates_and_restart_detects_before_tick(self):
        m = self.manager()
        m.meta['last'] -= 121
        m.tick()
        self.assertFalse(m.snapshot()['comparison_valid'])
        self.assertEqual(m.meta['gaps'], 1)
        m.meta['last'] -= 121; m._save()
        resumed = self.manager()
        self.assertEqual(resumed.status, 'running')
        self.assertEqual(resumed.meta['gaps'], 2)
        self.assertFalse(resumed.snapshot()['comparison_valid'])

    def test_fail_isolated_and_does_not_resume_on_tick(self):
        m = self.manager()
        with patch.object(m.books[0], 'on_tick', side_effect=RuntimeError('fixture')), self.assertLogs('alfred', level='ERROR'):
            m.tick()
        self.assertEqual(m.status, 'error')
        with patch.object(m.books[0], 'on_tick', side_effect=AssertionError('must not retry')):
            m.tick()
        self.assertEqual(set(self.bots), {'live', 'paper'})

    def test_no_scan_repeat_and_readonly_payload(self):
        m = self.manager()
        boundary = m.books[0]._last_entry_scan_4h_close
        self.assertFalse(m.needs_scan(boundary))
        self.assertTrue(m.needs_scan(boundary+14400))
        m.scan()
        self.assertEqual(m.status, 'running')

    def test_delayed_reference_anchor_when_first_fresh_price_arrives(self):
        self.bots['live'].positions['SOL'] = SimpleNamespace(size_usdt=100, direction=1, entry_price=100)
        self.master.states['SOL'].updated_at = 0
        m = self.manager()
        self.assertIsNone(m.meta['anchors']['live']['equity'])
        self.master.states['SOL'].updated_at = self.now.timestamp()
        self.master.states['SOL'].price = 105
        m.tick()
        ref = next(r for r in m.snapshot()['references'] if r['id']=='live')
        self.assertEqual(ref['start_equity'], 505)
        self.assertEqual(ref['pnl_since_start'], 0)
        self.assertIn('started_at', ref)

    def test_market_stale_invalidates_even_flat_books(self):
        m = self.manager()
        m.meta['started'] -= 200
        self.master.last_price_fetch -= 200
        m.tick()
        self.assertFalse(m.snapshot()['comparison_valid'])
        self.assertEqual(m.meta['gaps'], 1)
        m.tick()
        self.assertEqual(m.meta['gaps'], 1)

    def test_state_serialization_failure_cannot_validate_old_checkpoint(self):
        m = self.manager()
        before = m.path.read_bytes()
        with patch('alfred.persistence.orjson.dumps', side_effect=RuntimeError('cannot serialize')), self.assertLogs('alfred', level='ERROR'):
            m.tick()
        self.assertEqual(m.status, 'error')
        self.assertEqual(m.path.read_bytes(), before)

    def test_sql_trade_write_failure_stops_experiment(self):
        from alfred.models import Position
        from datetime import timedelta
        m = self.manager(); b = m.books[0]
        b.positions['SOL'] = Position('SOL', 1, 'S1', 100, self.now, 100, '', self.now-timedelta(seconds=1))
        b._save_state()
        with patch('alfred.persistence.write_trade', side_effect=RuntimeError('disk failure')), self.assertLogs('alfred', level='ERROR'):
            m.tick()
        self.assertEqual(m.status, 'error')
        self.assertFalse(m.snapshot()['comparison_valid'])

    def test_reference_reset_marks_reference_only_unavailable(self):
        m = self.manager()
        self.bots['live']._capital += 10
        m.tick()
        reference = next(r for r in m.snapshot()['references'] if r['id']=='live')
        self.assertIsNone(reference['pnl_since_start'])
        self.assertTrue(m.snapshot()['comparison_valid'])

if __name__ == '__main__': unittest.main()
