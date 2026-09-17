import unittest
import numpy as np
from backtests.events.cascade_v1 import H,asof,trailing_threshold,detect,outcome,aggregate
class Tests(unittest.TestCase):
 def test_threshold_uses_only_past(self):
  a=np.arange(20,dtype=float);x=trailing_threshold(a,.95,5)
  a[10:]=1e9;y=trailing_threshold(a,.95,5)
  np.testing.assert_equal(x[:11],y[:11])
 def test_asof_rejects_future_and_stale(self):
  x=asof(np.array([H,3*H]),np.array([100.,200.]),np.array([0,H,2*H,3*H]))
  self.assertTrue(np.isnan(x[0]));self.assertEqual(x[1],100);self.assertTrue(np.isnan(x[2]));self.assertEqual(x[3],200)
 def test_delayed_entry_exact_cost_funding_and_gaps(self):
  c=np.array([[i*H,100+i,102+i,99+i,101+i,1] for i in range(12)],dtype=float)
  funding={i*H:.0001 for i in range(12)}
  result,reason=outcome(c,0,4,funding)
  self.assertIsNone(reason);self.assertEqual(result['entry_t'],2*H)
  self.assertAlmostEqual(result['net25_bps'],(106/102-1)*10000-4-25)
  self.assertEqual(outcome(c,0,4,{})[1],'funding_gap')
  self.assertEqual(outcome(np.delete(c,3,axis=0),0,4,funding)[1],'price_gap')
 def test_future_prices_cannot_change_old_signals(self):
  rng=np.random.default_rng(17);n=100;t=np.arange(n)*H
  c=np.column_stack([t,np.ones(n)*100,100+rng.random(n)*10,90+rng.random(n)*10,95+rng.random(n)*10,rng.random(n)*100])
  oi=1000+np.cumsum(rng.normal(size=n+1));times=np.arange(n+1)*H
  before=detect(c,times,oi,10)
  c[70:,5]*=1000;oi[71:]*=10
  after=detect(c,times,oi,10)
  np.testing.assert_equal(before[1][:70],after[1][:70])
 def test_same_day_tokens_are_one_day(self):
  rows=[dict(entry_t=H,net13_bps=112,net25_bps=100,excess_bps=50),dict(entry_t=2*H,net13_bps=-88,net25_bps=-100,excess_bps=-50)]
  x=aggregate(rows);self.assertEqual(x['n_days'],1);self.assertEqual(x['mean_net25_bps'],0);self.assertFalse(x['gate'])
if __name__=='__main__':unittest.main()
