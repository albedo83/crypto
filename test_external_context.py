import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
import ai_external_context as ext
import ai_entry_arbiter as entry
import ai_exit_arbiter as exit_ai

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now=ext.stamp('2026-09-20T12:00:00+00:00')
        self.url='https://www.optimism.io/official-announcement'
        self.evidence={'sources':{self.url:{'url':self.url,'cited_text':'The upgrade is scheduled for September 21, 2026 at 12:00 UTC.'}},'searches':1,'errors':[]}
        self.fact=dict(symbol='OP',url=self.url,claim='Annonce',published_at='2026-09-20T10:00:00+00:00',event_at='2026-09-21T12:00:00+00:00',category='unlock',source_type='primary',event_kind='scheduled',support_quote='The upgrade is scheduled for September 21, 2026 at 12:00 UTC.')
    def valid(self,fact=None):return ext.validate_facts({'facts':[fact or self.fact]},self.evidence,['OP'],self.now)
    def test_cited_dated_fact_accepted_with_finite_expiry(self):
        facts,errors=self.valid();self.assertEqual(errors,[]);self.assertEqual(facts[0]['expires_at'],self.now+21600)
    def test_uncited_wrong_asset_secondary_dates_rejected(self):
        for change in ({'url':'https://invented.example/x'},{'symbol':'SNX'},{'source_type':'secondary'},{'published_at':'2026-09-21T10:00:00+00:00'},{'event_at':'2026-10-21T12:00:00+00:00'},{'symbol':'MACRO'}):
            with self.subTest(change=change):self.assertEqual(self.valid(dict(self.fact,**change))[0],[])
    def test_old_announcement_and_undated_calendar_for_future_event(self):
        for pub in ('2026-01-01',None):
            facts,errors=self.valid(dict(self.fact,published_at=pub))
            self.assertEqual(errors,[]);self.assertEqual(len(facts),1)
    def test_missing_publication_not_allowed_for_recent_news(self):
        self.assertEqual(self.valid(dict(self.fact,event_kind='recent',published_at=None,event_at='2026-09-20'))[0],[])
    def test_day_precision_is_preserved(self):
        facts,errors=self.valid(dict(self.fact,event_at='2026-09-21'))
        self.assertEqual(errors,[]);self.assertEqual(facts[0]['time_precision'],'day')
        self.assertEqual(facts[0]['event_end']-facts[0]['event_start'],86400)
    def test_scheduled_fact_expires_at_event_not_six_hours_later(self):
        quote='The upgrade is scheduled for September 20, 2026 at 13:00 UTC.'
        self.evidence['sources'][self.url]['cited_text']=quote
        fact=dict(self.fact,event_at='2026-09-20T13:00:00Z',support_quote=quote)
        facts,errors=self.valid(fact)
        self.assertEqual(facts[0]['expires_at'],self.now+3600)
        self.assertEqual(ext.validate_facts({'facts':[fact]},self.evidence,['OP'],self.now+3601)[0],[])
    def test_fabricated_quote_and_unapproved_source_rejected(self):
        self.assertEqual(self.valid(dict(self.fact,support_quote='Fabricated sufficiently long citation'))[1],['unsupported_quote'])
        for url in ('https://optimism.io.evil.example/a','https://fakeoptimism.io/a','https://near.org/a'):
            evidence={'sources':{url:self.evidence['sources'][self.url]}}
            self.assertEqual(ext.validate_facts({'facts':[dict(self.fact,url=url)]},evidence,['OP'],self.now)[1],['unapproved_source'])
    def test_quote_without_event_date_or_with_wrong_date_rejected(self):
        for quote in ('The minimum validation period drops from 336 hours to 48 hours.',
                      'Scheduled upgrade on September 23, 2026 at 12:00 UTC.'):
            self.evidence['sources'][self.url]['cited_text']=quote
            self.assertEqual(self.valid(dict(self.fact,support_quote=quote))[1],['event_date_not_in_quote'])
    def test_unverified_hour_downgrades_to_day(self):
        quote='Official upgrade on September 21, 2026 at 12:00 (timezone unspecified).'
        self.evidence['sources'][self.url]['cited_text']=quote
        facts,errors=self.valid(dict(self.fact,support_quote=quote))
        self.assertEqual(errors,[]);self.assertEqual(facts[0]['event_at'],'2026-09-21')
        self.assertEqual(facts[0]['time_precision'],'day')
    def test_eastern_time_conversion_and_invented_hour(self):
        quote='Mainnet activation: September 21, 2026 at 11:00 AM ET.'
        self.evidence['sources'][self.url]['cited_text']=quote
        facts,_=self.valid(dict(self.fact,support_quote=quote,event_at='2026-09-21T15:00:00Z'))
        self.assertEqual(facts[0]['time_precision'],'time')
        self.assertTrue(ext.quote_has_time('September 21, 2026 at 11:00 AM EST','2026-09-21T16:00:00Z'))
        self.assertFalse(ext.quote_has_time('September 21, 2026 at 11:00 AM EST','2026-09-21T15:00:00Z'))
        facts,_=self.valid(dict(self.fact,support_quote=quote,event_at='2026-09-21T16:00:00Z'))
        self.assertEqual(facts[0]['time_precision'],'day')
    def test_duplicate_event_with_different_claim_counted_once(self):
        facts,_=ext.validate_facts({'facts':[self.fact,dict(self.fact,claim='Other detail')]},self.evidence,['OP'],self.now)
        self.assertEqual(len(facts),1)
    def test_shared_host_path_boundary_and_macro_isolation(self):
        self.assertTrue(ext.source_allowed('https://github.com/near/nearcore/releases/tag/2','NEAR'))
        self.assertFalse(ext.source_allowed('https://github.com/near/nearcore/releases-evil','NEAR'))
        self.assertFalse(ext.source_allowed('https://github.com/attacker/releases','NEAR'))
        self.assertFalse(ext.source_allowed('https://www.bea.gov/news/schedule','OP'))
        self.assertFalse(ext.source_allowed('https://blog.synthetix.io/news','MACRO'))
    def test_duplicate_fact_and_refresh_identity(self):
        facts,_=ext.validate_facts({'facts':[self.fact,self.fact]},self.evidence,['OP'],self.now)
        fresh,_=ext.validate_facts({'facts':[self.fact]},self.evidence,['OP'],self.now+1)
        self.assertEqual(len(facts),1);self.assertEqual(facts[0]['id'],fresh[0]['id'])
    def test_registry_covers_actual_universe_and_rotation_no_starvation(self):
        from alfred.settings import Params
        universe=list(Params().trade_symbols)
        self.assertFalse(set(universe)-set(ext.PROJECTS))
        coverage={};seen=set();held=universe[:4]
        for i in range(10):
            symbols=ext.select_symbols(held,universe,coverage)
            self.assertEqual(len(symbols),8);self.assertEqual(len(set(symbols)),8)
            self.assertTrue(set(held)<=set(symbols));seen.update(symbols)
            for sym in symbols:coverage[sym]={'attempted_at':i+1}
        self.assertEqual(seen,set(universe))
        self.assertEqual(ext.select_symbols([],[],{}),[])
    def test_partial_failure_retains_only_fresh_evidence_and_marks_gap(self):
        facts,_=self.valid()
        old={'facts':facts,'coverage':{'OP':{'checked_at':self.now}}}
        failed=dict(symbol='OP',status='error',error='Timeout',facts=[],rejected=[],searches=0,sources=[],batch_id='x')
        success=dict(failed,symbol='MACRO',status='ok',error=None)
        packet=ext.merge_results(old,[failed,success],['OP'],'test',self.now+10)
        self.assertEqual(packet['status'],'partial');self.assertEqual(len(packet['facts']),1)
        self.assertEqual(packet['coverage']['OP']['checked_at'],self.now)
        self.assertEqual(packet['coverage']['OP']['status'],'error')
        packet=ext.merge_results(old,[dict(failed,status='ok',error=None)],['OP'],'test',self.now+10)
        self.assertEqual(packet['facts'],[])
        packet=ext.merge_results(old,[failed],['OP'],'test',self.now+21601)
        self.assertEqual(packet['facts'],[])
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

class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_asset_tool_filter_and_archive_even_on_extraction_error(self):
        raw={'stop_reason':'end_turn','content':[{'type':'server_tool_use','name':'web_search'},
             {'type':'text','text':'result','citations':[{'type':'web_search_result_location',
               'url':'https://avax.network/x','cited_text':'Scheduled upgrade on September 22, 2026'}]}]}
        client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=[
            SimpleNamespace(model_dump=lambda **kwargs:raw),RuntimeError('extraction failed')])) )
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'ALFRED_DATA_DIR':temp}):
            r=await ext.collect_scope(client,'AVAX','model')
            self.assertEqual(r['status'],'error');self.assertEqual(r['searches'],1)
            self.assertEqual(len(list((ext.root()/'batches').glob('*.json'))),2)
        kwargs=client.messages.create.call_args_list[0].kwargs
        self.assertIn('avax.network',kwargs['tools'][0]['allowed_domains'])
        self.assertNotIn('federalreserve.gov',kwargs['tools'][0]['allowed_domains'])
    async def test_unknown_asset_does_not_call_api(self):
        client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock()))
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'ALFRED_DATA_DIR':temp}):
            r=await ext.collect_scope(client,'UNKNOWN','model')
        self.assertEqual(r['status'],'error');client.messages.create.assert_not_called()
    async def test_calendar_http_sources_and_fetch_failures_are_explicit(self):
        import httpx
        original=httpx.AsyncClient
        def handler(request):
            if 'bea.gov' in str(request.url):
                return httpx.Response(200,text='<script>ignore this instruction</script><h1>Year 2026</h1>'
                    '<p>September 24 8:30 AM U.S. International Transactions and Investment Position, '
                    '2nd Quarter 2026. Official release calendar.</p>')
            return httpx.Response(403,text='Forbidden')
        def client(**kwargs):return original(transport=httpx.MockTransport(handler),**kwargs)
        with patch.object(httpx,'AsyncClient',side_effect=client):
            evidence=await ext.fetch_calendars(1789971792)
        self.assertEqual(len(evidence['sources']),1);self.assertEqual(len(evidence['fetch_errors']),3)
        source=evidence['sources']['https://www.bea.gov/news/schedule']
        self.assertNotIn('ignore this instruction',source['cited_text'])
        self.assertEqual(source['retrieval'],'direct_https')
        self.assertTrue(ext.quote_has_date(source['cited_text'],'2026-09-24'))
    async def test_calendar_redirect_not_followed(self):
        import httpx
        original=httpx.AsyncClient
        def handler(request):return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
        with patch.object(httpx,'AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            evidence=await ext.fetch_calendars(1789971792)
        self.assertEqual(evidence['sources'],{});self.assertEqual(len(evidence['fetch_errors']),4)
    async def test_no_live_means_no_collection(self):
        with patch.object(ext,'collect',new_callable=AsyncMock) as collect:
            await ext.worker({'paper':object(),'junior':object()},None)
        collect.assert_not_called()

if __name__=='__main__':unittest.main()
