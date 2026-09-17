import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from audit_ai_exit_coverage import audit, summarize

class CoverageTests(unittest.TestCase):
    def test_repeated_decisions_and_prompt_changes_are_not_independent_trades(self):
        d = dict(action='LOCK', acted=False, note='lock_shadow', entry_ts_ms=1000,
                 prompt_hash='old', alfred_version='1', lock_mode='shadow')
        result = summarize([(2,'BTC',d), (3,'BTC',d),
                            (4,'BTC',dict(d,prompt_hash='new'))])
        self.assertEqual(result['events'],3)
        self.assertEqual(result['shadow_lock_positions_excluded_by_note'],1)
        self.assertEqual(len(result['cohorts']),2)
        self.assertFalse(result['causal_pnl_estimated'])

    def test_invalid_or_acted_locks_are_not_shadow_exclusions(self):
        base = dict(action='LOCK',entry_ts_ms=1000)
        result = summarize([(2,'BTC',dict(base,acted=True,note='ok')),
                            (3,'BTC',dict(base,acted=False,note='invalid')),
                            (4,'ETH',dict(action='LOCK',acted=False,note='lock_shadow'))])
        self.assertEqual(result['shadow_lock_positions_excluded_by_note'],0)
        self.assertEqual(result['missing_position_identity'],1)

    def test_database_bounds_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bot.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE events(ts INTEGER,event TEXT,symbol TEXT,data TEXT)')
                for ts in [1,2,3]:
                    db.execute('INSERT INTO events VALUES(?,?,?,?)',
                               (ts,'ARBITER_EXIT_DECISION','BTC',json.dumps(dict(action='CUT'))))
            before=path.read_bytes()
            self.assertEqual(audit(path,2,2)['events'],1)
            self.assertEqual(path.read_bytes(),before)
            with self.assertRaises(FileNotFoundError):
                audit(Path(td)/'absent.db',0,10)
            self.assertFalse((Path(td)/'absent.db').exists())

if __name__ == '__main__':
    unittest.main()
