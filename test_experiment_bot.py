import builtins
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from alfred.experiment_bot import ExperimentBot
from alfred.models import Position, SymbolState
from alfred import rules
from alfred.botinstance import BotInstance


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime.now(timezone.utc)
        self.master = SimpleNamespace(states={s: SymbolState(price=100, updated_at=self.now.timestamp(), funding=.001)
                                             for s in ('SOL', 'ARB', 'AVAX', 'LINK', 'DOGE')},
                                      snapshot=None, last_downtime=None)

    def bot(self, variant='control', **kwargs):
        b = ExperimentBot(self.master, self.tmp.name, variant, **kwargs)
        self.addCleanup(b.db.conn.close)
        return b

    def position(self, sym='SOL', strategy='S1', direction=1):
        return Position(sym, direction, strategy, 100, self.now, 100, '', self.now + timedelta(hours=48))

    def view(self, **kwargs):
        values = dict(strategy='S1', direction=1, entry_price=100, size_usdt=100,
                      stop_bps=0, mfe_bps=100, mae_bps=0, hours_held=48,
                      hours_to_timeout=0, mfe_at_h=48)
        values.update(kwargs)
        return rules.PosView(**values)

    def test_s1_once_only_and_other_strategies_unchanged(self):
        b = self.bot('s1'); m = rules.MarketCtx()
        self.assertEqual(b._evaluate_exit(self.view(), 100, m).extend_hours, 24)
        self.assertEqual(b._evaluate_exit(self.view(extended=True), 100, m).reason, 'timeout')
        self.assertEqual(b._evaluate_exit(self.view(), -1, m).reason, 'timeout')
        self.assertEqual(b._evaluate_exit(self.view(strategy='S5'), 100, m).reason, 'timeout')
        self.assertEqual(self.bot()._evaluate_exit(self.view(), 100, m).reason, 'timeout')

    def test_protection_precedes_extension(self):
        b = self.bot('s1')
        decision = b._evaluate_exit(self.view(manual_stop_usdt=2), 100, rules.MarketCtx())
        self.assertEqual(decision.reason, 'manual_stop_set')
        self.assertEqual(b.s1_extensions, 0)

    def test_tick_extension_persists_and_eventual_timeout(self):
        b = self.bot('s1')
        pos = self.position()
        pos.entry_time = self.now - timedelta(hours=48)
        pos.target_exit = self.now
        b.positions['SOL'] = pos
        self.master.states['SOL'].price = 101
        b.on_tick(self.now)
        self.assertEqual(b.positions['SOL'].target_exit, self.now+timedelta(hours=24))
        restored = self.bot('s1'); restored.load()
        self.assertTrue(restored.positions['SOL'].extended)
        later = self.now+timedelta(hours=24)
        self.master.states['SOL'].updated_at = later.timestamp()
        restored.on_tick(later)
        self.assertFalse(restored.positions)
        self.assertEqual(restored.trades[-1].reason, 'timeout')
        self.assertEqual(restored.s1_extensions, 1)

    def test_cap3_same_strategy_direction_only(self):
        b = self.bot('cap3')
        b.positions = {s: self.position(s) for s in ('SOL', 'ARB', 'AVAX')}
        sig = dict(symbol='LINK', strategy='S1', direction=1)
        with patch.object(BotInstance, '_entry_skip_reason', return_value=None):
            self.assertEqual(b._entry_skip_reason(sig, None, None, 500), 'experiment_strategy_direction_cap3')
            self.assertIsNone(b._entry_skip_reason(dict(sig, direction=-1), None, None, 500))
            self.assertIsNone(b._entry_skip_reason(dict(sig, strategy='S5'), None, None, 500))
        with patch.object(BotInstance, '_entry_skip_reason', return_value='max_positions'):
            self.assertEqual(b._entry_skip_reason(sig, None, None, 500), 'max_positions')

    def test_funding_previous_rate_no_future_and_gap(self):
        b = self.bot(); b.positions['SOL'] = self.position(); b._seed_funding()
        self.master.states['SOL'].funding = -.002
        self.master.states['SOL'].updated_at += 60
        b._accrue_funding(self.now.timestamp()+60)
        self.assertAlmostEqual(b._funding['SOL']['usd'], -100*.001/60)
        b._accrue_funding(self.now.timestamp()+120)
        self.assertAlmostEqual(b._funding['SOL']['usd'], 100*.001/60)
        b._accrue_funding(self.now.timestamp()+1000)
        self.assertEqual(b.funding_missing_seconds, 880)
        self.assertEqual(b.funding_gap_count, 1)

    def test_restart_checkpoints_extended_funding_cooldown_independent(self):
        b = self.bot('s1'); b.positions['SOL'] = self.position()
        b.positions['SOL'].extended = True
        b._last_trail_eval_4h = 14400
        b._last_entry_scan_4h_close = 28800
        b._cooldowns['ARB'] = self.now.timestamp()+3600
        b._accrue_funding(self.now.timestamp()+60)
        b._save_state()
        restored = self.bot('s1'); restored.load()
        self.assertTrue(restored.positions['SOL'].extended)
        self.assertEqual(restored._last_entry_scan_4h_close, 28800)
        self.assertEqual(restored._last_trail_eval_4h, 14400)
        self.assertIn('ARB', restored._cooldowns)
        self.assertEqual(restored._funding, b._funding)
        other = self.bot('cap3'); other.load(); other._total_pnl = 123
        self.assertEqual(restored._total_pnl, 0)
        self.assertFalse(other.positions)

    def test_no_network_ai_or_real_broker(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('ai_entry', 'ai_exit', 'hyperliquid')) or name == 'hl':
                raise AssertionError('forbidden import ' + name)
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded), patch('socket.socket', side_effect=AssertionError('network')):
            b = self.bot(param_overrides={'hard_stop_enabled': True, 'paper_slippage_bps': 0})
            b.on_tick(self.now)
            self.assertFalse(b.broker.is_live)
            self.assertFalse(b.p.hard_stop_enabled)
            self.assertEqual(b.p.paper_slippage_bps, 4)
            b.notifier.send('hello')
            sig = dict(symbol='SOL', strategy='S1', direction=1, z=2, strength=1, info='test')
            with patch.object(b, '_entry_skip_reason', return_value=None):
                self.assertEqual(b._rank_and_enter([sig], self.now, rules.MarketCtx()), 1)
            b.close_position('SOL', 100, self.now, 'offline_test')
        with self.assertRaises(ValueError):
            self.bot('live')

    def test_fills_costs_and_funding_swap_no_double_charge(self):
        b = self.bot(); f = b.broker.open('SOL', 1, 100, 100)
        self.assertAlmostEqual(f.avg_px, 100.02)
        pos = self.position(); pos.entry_price = f.avg_px
        b.positions['SOL'] = pos; b._seed_funding()
        self.master.states['SOL'].updated_at += 60
        b.close_position('SOL', 100, self.now+timedelta(seconds=60), 'test')
        trade = b.trades[-1]
        exit_px = 99.98
        expected = 100*((exit_px/f.avg_px-1)-9/10000) - 100*.001/60
        self.assertAlmostEqual(b._total_pnl, expected, places=6)
        self.assertEqual(trade.pnl_usdt, round(expected, 2))
        self.assertNotIn('SOL', b._funding)
        self.assertEqual(b.experiment_snapshot(self.now)['equity'], b._capital+b._total_pnl)

    def test_margin_and_net_marked_equity(self):
        b = self.bot(); b.positions['SOL'] = self.position()
        self.assertEqual(b._available_entry_margin(), 450)
        snap = b.experiment_snapshot(self.now)
        self.assertAlmostEqual(snap['equity'], 499.89)
        self.master.states['SOL'].updated_at -= 121
        self.assertIsNone(b.experiment_snapshot(self.now)['equity'])

    def test_rejected_fill_does_not_reserve_slot(self):
        b = self.bot(); signals = [dict(symbol=s, strategy='S1', direction=1, z=2, strength=1, info='test')
                                  for s in ('SOL', 'ARB')]
        real_open = b.broker.open
        def opening(sym, *args):
            if sym == 'SOL':
                raise RuntimeError('rejected fixture')
            return real_open(sym, *args)
        with patch.object(b, '_entry_skip_reason', return_value=None), patch.object(b.broker, 'open', side_effect=opening):
            self.assertEqual(b._rank_and_enter(signals, self.now, rules.MarketCtx()), 1)
        self.assertEqual(set(b.positions), {'ARB'})
        self.assertEqual(b._inflight_open, set())

    def test_restart_does_not_repeat_consumed_boundary(self):
        boundary = int(self.now.timestamp()) // 14400 * 14400
        self.master.last_price_fetch = self.now.timestamp()
        self.master.snapshot = SimpleNamespace(btc_z=0, cross_ctx={}, btc_f={})
        b = self.bot()
        b._last_entry_scan_4h_close = boundary
        b._save_state()
        restored = self.bot(); restored.load()
        with patch('alfred.botinstance.alerts.run_all'), patch.object(restored, '_rank_and_enter', side_effect=AssertionError('duplicate entry')):
            self.assertEqual(restored.on_scan(self.now), 0)

    def test_fail_closed_missing_funding_checkpoint(self):
        b = self.bot(); b.positions['SOL'] = self.position(); b._save_state()
        with open(b.state_file) as f: data=json.load(f)
        data['experiment']['funding'] = {}
        with open(b.state_file, 'w') as f: json.dump(data, f)
        with self.assertRaises(ValueError): b.load()

    def test_fail_closed_wrong_state_variant(self):
        b = self.bot(); b._save_state()
        with open(b.state_file) as f: data=json.load(f)
        data['experiment']['variant']='s1'
        with open(b.state_file,'w') as f: json.dump(data,f)
        with self.assertRaises(ValueError): b.load()

if __name__ == '__main__': unittest.main()
