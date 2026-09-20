import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import ai_external_context as ext
import ai_entry_arbiter as entry
import ai_exit_arbiter as exit_ai

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now=ext.stamp('2026-09-20T12:00:00+00:00')
        self.url='https://example.org/official-announcement'
        self.evidence={'sources':{self.url:{'url':self.url,'cited_text':'announcement'}},'searches':1,'errors':[]}
        self.fact=dict(symbol='OP',url=self.url,claim='Annonce',published_at='2026-09-20T10:00:00+00:00',event_at='2026-09-21T12:00:00+00:00',category='unlock',source_type='primary')
    def valid(self,fact=None):return ext.validate_facts({'facts':[fact or self.fact]},self.evidence,['OP'],self.now)
    def test_cited_dated_fact_accepted_with_finite_expiry(self):
        facts,errors=self.valid();self.assertEqual(errors,[]);self.assertEqual(facts[0]['expires_at'],self.now+21600)
    def test_uncited_wrong_asset_secondary_dates_rejected(self):
        for change in ({'url':'https://invented.example/x'},{'symbol':'SNX'},{'source_type':'secondary'},{'published_at':None},{'published_at':'2026-09-21T10:00:00+00:00'},{'event_at':'2026-10-21T12:00:00+00:00'},{'published_at':'2026-09-20'},{'symbol':'MACRO'}):
            with self.subTest(change=change):self.assertEqual(self.valid(dict(self.fact,**change))[0],[])
    def test_real_citation_blocks_only(self):
        data={'stop_reason':'end_turn','content':[{'type':'text','text':'https://fake.example','citations':[{'type':'web_search_result_location','url':self.url,'cited_text':'fact'},{'type':'web_search_result_location','url':'javascript:bad'}]},{'type':'server_tool_use','name':'web_search'}]}
        evidence=ext.evidence_from_response(data);self.assertEqual(set(evidence['sources']),{self.url});self.assertEqual(evidence['searches'],1)
    def test_sdk_null_citations(self):
        r=ext.evidence_from_response({'stop_reason':'end_turn','content':[{'type':'text','text':'searching','citations':None}]})
        self.assertEqual(r['sources'],{})
    def test_quota_error_and_incomplete_response_visible(self):
        r=ext.evidence_from_response({'stop_reason':'pause_turn','content':[{'type':'web_search_tool_result','content':{'error_code':'max_uses_exceeded'}}]})
        self.assertEqual(r['errors'],['max_uses_exceeded','incomplete_search_response'])
    def test_unknown_evidence_neutralizes_both_arbiters(self):
        for phase,v in [('entry',{'decision':'VETO','factor':.5}),('exit',{'action':'CUT'})]:
            v['evidence_ids']=['made-up'];r=ext.ground_verdicts({'OP':v},{'facts':[]},phase,self.now)['OP']
            self.assertEqual(r.get('decision',r.get('action')),'GO' if phase=='entry' else 'HOLD')
    def test_evidence_cannot_cross_assets_or_outlive_expiry(self):
        facts,_=self.valid();v={'decision':'VETO','evidence_ids':[facts[0]['id']]}
        self.assertEqual(ext.ground_verdicts({'SNX':v},{'facts':facts},'entry',self.now)['SNX']['decision'],'GO')
        self.assertEqual(ext.ground_verdicts({'OP':v},{'facts':facts},'entry',self.now+21600)['OP']['decision'],'GO')
        self.assertEqual(ext.ground_verdicts({'OP':v},{'facts':facts},'entry',self.now)['OP']['decision'],'VETO')
    def test_missing_and_stale_cache(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'ALFRED_DATA_DIR':temp}):
            self.assertEqual(ext.read_cache(self.now)['status'],'unavailable')
            facts,_=self.valid();ext.atomic_write(ext.root()/'latest.json',{'version':ext.VERSION,'status':'ok','checked_at':self.now,'facts':facts})
            self.assertEqual(len(ext.read_cache(self.now)['facts']),1)
            self.assertEqual(ext.read_cache(self.now+21601)['facts'],[])
    def test_disabled_has_no_actionable_facts(self):
        facts,_=self.valid()
        with patch.object(ext,'read_cache',return_value={'status':'ok','facts':facts}),patch.dict(os.environ,{'AI_EXTERNAL_ENABLED':'0'}):
            self.assertEqual(ext.context_for(['OP'])['facts'],[])
    def test_no_context_skips_llm_and_records_neutral_verdict(self):
        for module,phase in ((entry,'entry'),(exit_ai,'exit')):
            with patch.object(ext,'context_for',return_value={'facts':[]}),patch.object(ext,'record_decision') as journal,patch.object(module,'_call_opus',side_effect=AssertionError('no API')):
                r=module.arbitrate([{'symbol':'OP','prior_decision':{'action':'CUT'}}],{})
                v=r['verdicts']['OP'];self.assertEqual(v.get('decision',v.get('action')),'GO' if phase=='entry' else 'HOLD')
                self.assertNotIn('prior_decision',journal.call_args.args[1][0])
    def test_unfounded_llm_veto_neutralized(self):
        facts,_=self.valid();facts[0]['observed_at']=0;facts[0]['expires_at']=9999999999
        with patch.object(ext,'context_for',return_value={'facts':facts}),patch.object(ext,'record_decision'),patch.object(entry,'_call_opus',return_value={'verdicts':{'OP':{'decision':'VETO','factor':.5,'evidence_ids':['invented']}},'meta':{}}):
            self.assertEqual(entry.arbitrate([{'symbol':'OP'}],{})['verdicts']['OP']['decision'],'GO')
    def test_journal_retains_evidence_identity(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'ALFRED_DATA_DIR':temp}):
            ext.record_decision('entry',[{'symbol':'OP'}],{'facts':[{'id':'a'}]},{'OP':{'evidence_ids':['a']}},'hash')
            r=json.loads((ext.root()/'decisions.jsonl').read_text());self.assertEqual(r['context']['facts'][0]['id'],'a');self.assertEqual(r['prompt_hash'],'hash')

if __name__=='__main__':unittest.main()
