import unittest
from backtests.audit_ai_shadow_replay import replay_exit
import test_exit_protection as fixture
class AIReplayTests(unittest.TestCase):
    def setUp(self):
        self.trade=fixture.Tests().trade();self.ticks=fixture.Tests().ticks([90,120,104,103]);self.start=self.ticks[0][0]-60
    def events(self,when,stop=5,action='LOCK'):
        return [dict(ts=self.start+when,data=dict(action=action,stop_usdt=stop,note='lock_shadow',lock_mode='shadow',cut_mode='shadow',acted=False,model='test',prompt_hash='v1'))]
    def test_lock_not_retroactive(self):
        r=replay_exit(self.trade,self.events(125),self.ticks);self.assertEqual(r['action'],'LOCK');self.assertEqual(r['exit_ts'],self.start+240)
    def test_lock_never_counts_each_repetition_as_trade(self):
        ev=self.events(125)+self.events(130);r=replay_exit(self.trade,ev,self.ticks);self.assertEqual(r['exit_ts'],self.start+240)
    def test_stop_cannot_be_lowered(self):
        r=replay_exit(self.trade,self.events(125,10)+self.events(130,0),self.ticks);self.assertEqual(r['action'],'LOCK')
    def test_cut_at_next_tick(self):
        r=replay_exit(self.trade,self.events(1,action='CUT'),self.ticks);self.assertEqual(r['exit_ts'],self.start+60)
    def test_cut_recovered_price_not_executed(self):
        r=replay_exit(self.trade,self.events(100,action='CUT'),self.ticks);self.assertEqual(r['action'],'unchanged')
    def test_gap_rejected(self):
        r=replay_exit(self.trade,self.events(1),self.ticks[2:]);self.assertEqual(r['status'],'missing_ticks')
    def test_acted_refused(self):
        e=self.events(1);e[0]['data']['acted']=True;self.assertEqual(replay_exit(self.trade,e,self.ticks)['status'],'acted_not_comparable')
if __name__=='__main__':unittest.main()
