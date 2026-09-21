import tempfile
import unittest
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from alfred.botinstance import BotInstance
from alfred.settings import BotConfig

class CooldownBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.boundary=datetime(2026,7,22,16,tzinfo=timezone.utc).timestamp()
        self.master=SimpleNamespace(states={'BTC':SimpleNamespace(last_candle_ts=self.boundary*1000)},snapshot=SimpleNamespace(btc_z=0,cross_ctx={},btc_f={},feature_cache={}),last_price_fetch=self.boundary+187)
        self.bots=[];self.addCleanup(lambda:[b.db.close() for b in self.bots])
        for target in ('alerts.run_all','features.compute_basket_correlation','persistence.log_basket_snapshot','signals.track_signal_age'):
            p=patch('alfred.botinstance.'+target);p.start();self.addCleanup(p.stop)
        self.bot=self.make_bot()
        self.bot._last_entry_scan_4h_close=int(self.boundary)-14400
        self.bot._cooldowns={'PYTH':self.boundary+202}
    def make_bot(self):
        b=BotInstance(BotConfig(id='test',label='test',mode='paper',capital_initial=500),self.master,self.tmp.name)
        self.bots.append(b);b.p=replace(b.p,trade_symbols=('PYTH',))
        b.notifier=Mock();b._market_ctx=Mock();b._arm_opp_floors=Mock()
        b._compute_features=Mock(return_value=None);b._rank_and_enter=Mock(return_value=0)
        return b
    def scan(self,seconds,b=None):
        b=b or self.bot;t=self.boundary+seconds
        with patch('alfred.botinstance.time.time',return_value=t):
            return b.on_scan(datetime.fromtimestamp(t,timezone.utc))
    def test_wait_then_single_scan_after_actual_expiry(self):
        self.scan(187);self.bot._compute_features.assert_not_called();self.bot._rank_and_enter.assert_not_called()
        self.assertLess(self.bot._last_entry_scan_4h_close,self.boundary)
        self.scan(201);self.bot._rank_and_enter.assert_not_called()
        self.scan(202);self.bot._compute_features.assert_called_once_with('PYTH')
        self.bot._rank_and_enter.assert_called_once()
        self.scan(220);self.bot._rank_and_enter.assert_called_once()
    def test_wait_survives_restart_and_completed_scan_not_replayed(self):
        self.scan(187)
        restored=self.make_bot();restored.load()
        self.assertEqual(restored._cooldowns,self.bot._cooldowns)
        self.assertLess(restored._last_entry_scan_4h_close,self.boundary)
        self.scan(201,restored);restored._rank_and_enter.assert_not_called()
        self.scan(203,restored);restored._rank_and_enter.assert_called_once()
        completed=self.make_bot();completed.load();self.scan(240,completed)
        completed._rank_and_enter.assert_not_called()
    def test_long_cooldown_does_not_delay_or_allow_early_entry(self):
        self.bot._cooldowns={'PYTH':self.boundary+301};self.scan(187)
        self.bot._compute_features.assert_not_called()
        self.bot._rank_and_enter.assert_called_once_with([],unittest.mock.ANY,unittest.mock.ANY)
        self.assertEqual(self.bot._last_entry_scan_4h_close,self.boundary)
    def test_deadline_exact_and_late_scan(self):
        self.bot._cooldowns={'PYTH':self.boundary+300};self.scan(299)
        self.bot._rank_and_enter.assert_not_called();self.scan(300)
        self.bot._rank_and_enter.assert_called_once()
    def test_paused_or_braked_consumes_gate_without_wait(self):
        for attr,value in [('_paused',True),('_entries_halted_until',self.boundary+3600)]:
            with self.subTest(attr=attr):
                self.bot._last_entry_scan_4h_close=self.boundary-14400
                setattr(self.bot,attr,value);self.scan(187)
                self.assertEqual(self.bot._last_entry_scan_4h_close,self.boundary)
                self.bot._rank_and_enter.assert_not_called();setattr(self.bot,attr,False if attr=='_paused' else 0)
    def test_stale_prices_after_wait_do_not_consume_gate(self):
        self.scan(187);self.master.last_price_fetch=self.boundary-500;self.scan(203)
        self.bot._rank_and_enter.assert_not_called()
        self.assertLess(self.bot._last_entry_scan_4h_close,self.boundary)
        self.master.last_price_fetch=self.boundary+204;self.scan(204)
        self.bot._rank_and_enter.assert_called_once()
    def test_held_or_untraded_symbols_do_not_delay(self):
        self.bot._cooldowns={'NOT_TRADED':self.boundary+202};self.scan(187)
        self.bot._rank_and_enter.assert_called_once()
    def test_missing_candle_rechecks_before_entering(self):
        self.scan(187);self.master.states['BTC'].last_candle_ts=(self.boundary-14400)*1000
        self.scan(203);self.bot._rank_and_enter.assert_not_called()
        self.master.states['BTC'].last_candle_ts=self.boundary*1000;self.scan(204)
        self.bot._rank_and_enter.assert_called_once()

if __name__=='__main__':unittest.main()
