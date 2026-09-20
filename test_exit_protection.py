import unittest
from backtests.audit_exit_protection import replay,ts
class Tests(unittest.TestCase):
    def trade(self,direction='LONG',exit_price=108):
        return dict(entry_time='2026-09-01T00:00:00+00:00',exit_time='2026-09-01T00:05:00+00:00',entry_price=100,size_usdt=100,exit_price=exit_price,direction=direction)
    def ticks(self,prices):return [(ts(self.trade()['entry_time'])+60*(i+1),p) for i,p in enumerate(prices)]
    def test_long_next_tick_not_threshold(self):
        r=replay(self.trade(),self.ticks([120,109,107,200]));self.assertTrue(r['triggered']);self.assertAlmostEqual(r['fill'],107*.9998);self.assertGreater(r['exit_ts'],r['trigger_ts']);self.assertLess(r['delta_price_usdt'],0)
    def test_short(self):
        r=replay(self.trade('SHORT',92),self.ticks([80,91,93,50]));self.assertTrue(r['triggered']);self.assertAlmostEqual(r['fill'],93*1.0002);self.assertLess(r['delta_price_usdt'],0)
    def test_no_future_peak(self):self.assertFalse(replay(self.trade(),self.ticks([105,102,120,121]))['triggered'])
    def test_gap_excluded(self):
        r=replay(self.trade(),self.ticks([120]));self.assertFalse(r['covered']);self.assertIsNone(r['delta_price_usdt'])
    def test_trigger_last_tick(self):
        r=replay(self.trade(),self.ticks([111,120,119,109]));self.assertFalse(r['triggered']);self.assertEqual(r['reason'],'original_exit_first')
    def test_unordered(self):
        with self.assertRaises(ValueError):replay(self.trade(),list(reversed(self.ticks([100,101,102,103]))))
    def test_rising(self):self.assertEqual(replay(self.trade(),self.ticks([105,111,119,121]))['delta_price_usdt'],0)
if __name__=='__main__':unittest.main()
