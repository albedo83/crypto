import dataclasses
from datetime import datetime, timezone
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from alfred.botinstance import BotInstance
from alfred.settings import BotConfig
from alfred import rules
import ai_entry_arbiter as ai

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        states={s:SimpleNamespace(price=10.0,oi_history=[],impact_bid=0,impact_ask=0)
                for s in ['NEAR','AAVE','DOGE','ARB']}
        master=SimpleNamespace(states=states,snapshot=None)
        self.bot=BotInstance(BotConfig(id='live',label='test',mode='paper',capital_initial=500),master,self.tmp.name)
        self.addCleanup(self.bot.db.close)
        self.bot.notifier=Mock();self.bot._place_hard_stop=Mock()
        self.bot._save_state=Mock()
        self.bot._btc_z=0
        self.now=datetime.now(timezone.utc);self.market=rules.MarketCtx(btc_z=0)
        self.env=patch.dict(os.environ,{'AI_ARBITER_ENABLED':'1','AI_ARBITER_MODE':'shadow'})
        self.env.start();self.addCleanup(self.env.stop)
        self.trip=patch.object(ai,'is_tripped',return_value=False);self.trip.start();self.addCleanup(self.trip.stop)
        self.call=patch.object(ai,'arbitrate_safe',return_value={'verdicts':{},'meta':{}}).start()
        self.addCleanup(patch.stopall)
    def sig(self,symbol='NEAR',strategy='S5',direction=-1,z=3):
        return dict(symbol=symbol,strategy=strategy,direction=direction,z=z,strength=2,info='test')
    def test_blocked_book_avoids_api_and_preserves_no_orders(self):
        self.bot.p=dataclasses.replace(self.bot.p,max_positions=0)
        self.assertEqual(self.bot._rank_and_enter([self.sig()],self.now,self.market),0)
        self.call.assert_not_called();self.assertEqual(self.bot.positions,{})
    def test_cooldown_pause_oi_and_sector_gate_before_api(self):
        cases=['cooldown','pause','oi','sector']
        for reason in cases:
            with self.subTest(reason=reason):
                self.bot._cooldowns={};self.bot._paused_strats=set()
                self.bot.p=BotConfig(id='live',label='',mode='paper').params()
                direction=-1
                if reason=='cooldown':self.bot._cooldowns['NEAR']=self.now.timestamp()+3600
                if reason=='pause':self.bot._paused_strats={('S5','SHORT')}
                if reason=='oi':
                    direction=1;self.bot.p=dataclasses.replace(self.bot.p,oi_missing_policy='block')
                if reason=='sector':self.bot.p=dataclasses.replace(self.bot.p,max_per_sector=0)
                self.assertEqual(self.bot._rank_and_enter([self.sig(direction=direction)],self.now,self.market),0)
                self.call.assert_not_called()
    def test_mixed_batch_and_shadow_veto_does_not_change_fill(self):
        self.bot._paused_strats={('S1','SHORT')}
        self.call.return_value={'verdicts':{'NEAR':dict(decision='VETO',confidence=1,factor=0,reason='test',risk_flags=[])},'meta':{}}
        self.assertEqual(self.bot._rank_and_enter([self.sig('AAVE','S1'),self.sig()],self.now,self.market),1)
        batch=self.call.call_args.args[0]
        self.assertEqual([x['symbol'] for x in batch],['NEAR'])
        expected=rules.position_size('S5',-1,500,0,self.bot.p)
        self.assertAlmostEqual(self.bot.positions['NEAR'].size_usdt,expected)
    def test_slot_is_not_reserved_for_an_unfilled_candidate(self):
        self.bot.p=dataclasses.replace(self.bot.p,max_positions=1)
        real_open=self.bot.broker.open
        def opening(sym,*args):
            if sym=='NEAR':raise RuntimeError('insufficient margin')
            return real_open(sym,*args)
        self.bot.broker.open=opening
        sigs=[self.sig('NEAR',z=4),self.sig('AAVE',z=3)]
        self.assertEqual(self.bot._rank_and_enter(sigs,self.now,self.market),1)
        self.assertEqual([x['symbol'] for x in self.call.call_args.args[0]],['NEAR','AAVE'])
        self.assertEqual(set(self.bot.positions),{'AAVE'})
    def test_execution_rechecks_capacity_after_first_fill(self):
        self.bot.p=dataclasses.replace(self.bot.p,max_positions=1)
        self.assertEqual(self.bot._rank_and_enter([self.sig('NEAR',z=4),self.sig('AAVE')],self.now,self.market),1)
        self.assertEqual(set(self.bot.positions),{'NEAR'})
    def test_act_batch_unchanged_even_when_book_full(self):
        with patch.dict(os.environ,{'AI_ARBITER_MODE':'act'}):
            self.bot.p=dataclasses.replace(self.bot.p,max_positions=0)
            self.assertEqual(self.bot._rank_and_enter([self.sig()],self.now,self.market),0)
            self.call.assert_called_once()
    def test_disabled_ai_keeps_normal_entry(self):
        with patch.dict(os.environ,{'AI_ARBITER_ENABLED':'0'}):
            self.assertEqual(self.bot._rank_and_enter([self.sig()],self.now,self.market),1)
            self.call.assert_not_called()

    def test_skip_telemetry_is_a_count_not_an_estimated_dollar_gain(self):
        self.bot.p=dataclasses.replace(self.bot.p,max_positions=0)
        with patch.object(self.bot.db,'log_event',wraps=self.bot.db.log_event) as log:
            self.bot._rank_and_enter([self.sig(),self.sig('AAVE')],self.now,self.market)
            rows=[c.args[2] for c in log.call_args_list if c.args[0]=='ARBITER_ENTRY_PREFLIGHT']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['n_considered'],2)
        self.assertEqual(rows[0]['n_submitted'],0)
        self.assertEqual(rows[0]['skipped_by_reason'],{'max_positions':2})
        self.assertTrue(rows[0]['call_avoided'])

    def test_shadow_and_ai_off_have_identical_fills_across_portfolio_limits(self):
        import random
        rng=random.Random(1729)
        for trial in range(40):
            symbols=['NEAR','AAVE','DOGE','ARB']
            sigs=[self.sig(sym,direction=rng.choice([-1,1]),z=rng.random()*4) for sym in symbols]
            params=dataclasses.replace(self.bot.p,max_positions=rng.randrange(1,5),
                                       max_same_direction=rng.randrange(1,4),
                                       max_per_sector=rng.randrange(1,3))
            cooldown={rng.choice(symbols):self.now.timestamp()+3600} if trial%2 else {}
            results=[]
            for mode in ['off','shadow']:
                self.bot.positions={};self.bot._inflight_open=set()
                self.bot._arbiter_last={};self.bot._cooldowns=cooldown.copy();self.bot.p=params
                with patch.dict(os.environ,{'AI_ARBITER_ENABLED':'0' if mode=='off' else '1'}):
                    count=self.bot._rank_and_enter([dict(s) for s in sigs],self.now,self.market)
                results.append((count,[(sym,pos.direction,pos.size_usdt,pos.entry_price)
                                      for sym,pos in self.bot.positions.items()]))
            self.assertEqual(results[0],results[1],trial)

if __name__=='__main__':unittest.main()
