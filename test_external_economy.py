import asyncio,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import ai_external_context as ext
import external_usage
from ai_cost import cost_from_usage

class EconomyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  db=self.root/'bots/live/bot.db';db.parent.mkdir(parents=True)
  c=sqlite3.connect(db);c.execute('CREATE TABLE events(ts REAL,event TEXT,symbol TEXT,data TEXT)');c.close()
  self.env=patch.dict('os.environ',{'ALFRED_DATA_DIR':str(self.root)});self.env.start();self.addCleanup(self.env.stop)
  self.raw={'id':'msg_fixture','model':ext.ECONOMY_MODEL,'usage':{'input_tokens':1000,'output_tokens':100,'server_tool_use':{'web_search_requests':1}}}
 def test_haiku_and_search_cost(self):
  self.assertAlmostEqual(cost_from_usage(self.raw['model'],self.raw['usage']),.0115)
  self.assertIn('haiku',ext.ECONOMY_MODEL)
 def test_record_and_backfill_idempotent_original_timestamp(self):
  folder=ext.root()/'batches';folder.mkdir(parents=True)
  for i in (1,2):(folder/(str(1700000000000000000+i)+'.json')).write_text(json.dumps({'started_at':1700000000,'search_response':self.raw,'extraction_response':self.raw}))
  self.assertEqual(external_usage.backfill(self.root),1)
  self.assertEqual(external_usage.backfill(self.root),0)
  c=sqlite3.connect(self.root/'bots/live/bot.db');rows=c.execute('SELECT ts,data FROM events').fetchall();c.close()
  self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],1700000000)
  self.assertEqual(json.loads(rows[0][1])['source'],'external')
 def test_missing_ledger_blocks_accounting(self):
  with self.assertRaises(FileNotFoundError):external_usage.record(self.raw,1,self.root/'missing')
 def test_recent_assets_not_recollected_even_when_empty(self):
  coverage={s:{'attempted_at':10000} for s in ('OP','AVAX','MACRO')}
  self.assertEqual(ext.due_symbols(['OP'],['OP','AVAX'],coverage,11000),[])
  self.assertEqual(ext.due_symbols(['OP'],['OP','AVAX'],coverage,32000),['OP','MACRO'])
  self.assertIn('AVAX',ext.due_symbols(['OP'],['OP','AVAX'],coverage,100000))
 def test_max_four_assets_plus_macro(self):
  self.assertEqual(len(ext.due_symbols([],list(ext.PROJECTS)[:10],{},100000)),5)
 def test_schedule_survives_reload(self):
  ext.atomic_write(ext.root()/'schedule.json',{'next_run':9999999999})
  self.assertEqual(ext.read_schedule()['next_run'],9999999999)
 def test_quota_blocks_until_provider_date(self):
  ext.block_provider('You have reached your specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.')
  self.assertEqual(ext.read_schedule()['blocked_until'],ext.stamp('2026-10-01T00:00:00Z'))
 def test_corrupt_schedule_does_not_default_to_spending(self):
  ext.root().mkdir();(ext.root()/'schedule.json').write_text('invalid')
  with self.assertRaises(ValueError):ext.read_schedule()
 def test_original_sources_bounded(self):
  original={'sources':{str(i):{'cited_text':'a'*9000} for i in range(12)},'searches':1}
  compact=ext.compact_evidence(original)
  self.assertLessEqual(sum(len(x['cited_text']) for x in compact['sources'].values()),18000)
  self.assertEqual(len(original['sources']['0']['cited_text']),9000)

class WorkerTests(unittest.IsolatedAsyncioTestCase):
 async def test_restart_uses_existing_deadline(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'ALFRED_DATA_DIR':temp}):
   ext.atomic_write(ext.root()/'schedule.json',{'next_run':time.time()+7200})
   shutdown=asyncio.Event()
   live=SimpleNamespace(p=SimpleNamespace(trade_symbols=['OP']))
   with patch.object(external_usage,'backfill'),patch.object(ext,'collect',new_callable=AsyncMock) as collect:
    task=asyncio.create_task(ext.worker({'live':live},shutdown))
    await asyncio.sleep(.01);shutdown.set();await task
    collect.assert_not_called()
 async def test_deadline_reserved_before_failed_collection(self):
  import threading
  with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'ALFRED_DATA_DIR':temp}):
   shutdown=asyncio.Event();live=SimpleNamespace(p=SimpleNamespace(trade_symbols=['OP']),_pos_lock=threading.Lock(),positions={})
   async def fail(symbols):
    self.assertGreater(ext.read_schedule()['next_run'],time.time())
    shutdown.set();raise RuntimeError('fixture')
   with patch.object(external_usage,'backfill'),patch.object(ext,'collect',side_effect=fail) as collect:
    await ext.worker({'live':live},shutdown)
    collect.assert_called_once()
 async def test_quota_stops_remaining_scope_calls(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'ALFRED_DATA_DIR':temp}):
   async def blocked(*args):
    ext.block_provider('usage limits')
    return dict(symbol='OP',status='error',facts=[],rejected=[],searches=0,sources=[],error='usage limits',batch_id='x')
   client=AsyncMock()
   with patch('anthropic.AsyncAnthropic',return_value=client),patch.object(ext,'collect_scope',side_effect=blocked) as scope:
    packet=await ext.collect(['OP','AVAX','MACRO'])
    self.assertEqual(scope.await_count,1);self.assertEqual(packet['error'],'provider_usage_limit')

if __name__=='__main__':unittest.main()
