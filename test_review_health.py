from contextlib import closing
import json,sqlite3,tempfile,unittest,subprocess,time
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from review_health import check_review_health
from alfred import attention

class ReviewHealthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.now=time.time()
  self.db=self.root/'alfred/data/bots/live/bot.db';self.db.parent.mkdir(parents=True)
  self.state=self.root/'alfred/data/attention_state.json';self.heartbeat(self.now-30)
  with closing(sqlite3.connect(self.db)) as c, c:c.execute('CREATE TABLE events(ts REAL,event TEXT,symbol TEXT,data TEXT)')
 def heartbeat(self,t):self.state.write_text(json.dumps({'last_scan':t}))
 def event(self,event,t=None,**data):
  with closing(sqlite3.connect(self.db)) as c, c:c.execute('INSERT INTO events VALUES(?,?,NULL,?)',(t if t is not None else self.now,event,json.dumps(data)))
 def status(self):return check_review_health(self.now,self.root)['status']
 def test_idle_old_or_no_review_is_healthy(self):
  self.assertEqual(self.status(),'OK');self.event('POSITION_REVIEW',self.now-86400);self.assertEqual(self.status(),'OK')
 def test_dead_detector_not_hidden_by_recent_success(self):
  self.event('POSITION_REVIEW');self.heartbeat(self.now-601);self.assertEqual(self.status(),'STALE')
 def test_bad_missing_heartbeat_and_database(self):
  self.heartbeat(self.now+100);self.assertEqual(self.status(),'INVALID')
  self.state.write_text('{}');self.assertEqual(self.status(),'MISSING')
  self.heartbeat(self.now);self.db.unlink();self.assertEqual(self.status(),'MISSING')
 def test_pending_then_timeout_not_hidden_by_unrelated_success(self):
  self.event('REVIEW_REQUEST',self.now-200,request_id='a');self.assertEqual(self.status(),'OK')
  self.event('REVIEW_REQUEST',self.now-301,request_id='b');self.event('POSITION_REVIEW');self.assertEqual(self.status(),'BROKEN')
 def test_results_and_skips_resolve_exact_requests(self):
  for i,status in enumerate(('success','skipped')):
   self.event('REVIEW_REQUEST',self.now-600,request_id=str(i));self.event('REVIEW_RESULT',request_id=str(i),status=status)
  self.assertEqual(self.status(),'OK')
 def test_failure_and_recovery(self):
  self.event('REVIEW_REQUEST',self.now-30,request_id='a');self.event('REVIEW_RESULT',request_id='a',status='error');self.assertEqual(self.status(),'BROKEN')
  self.event('POSITION_REVIEW');self.assertEqual(self.status(),'OK')
 def test_legacy_error(self):
  self.event('POSITION_REVIEW',self.now-100);self.event('POSITION_REVIEW_ERROR',self.now-50);self.assertEqual(self.status(),'BROKEN')
 def call(self,run):
  with patch.object(attention,'LIVE_DB',str(self.db)),patch.object(attention.subprocess,'run',side_effect=run),patch.object(attention,'env',side_effect=lambda k,d='':d):
   attention.llm_review({},'near_stop','fixture',['SEI'])
  with closing(sqlite3.connect(self.db)) as c, c:return [(e,json.loads(d)) for e,d in c.execute("SELECT event,data FROM events WHERE event LIKE 'REVIEW_%' ORDER BY rowid")]
 def test_wrapper_success_requires_matching_persisted_review(self):
  def run(*a,**k):
   self.event('POSITION_REVIEW',time.time(),request_id=a[0][a[0].index('--request-id')+1])
   return SimpleNamespace(returncode=0,stdout='POSITION_REVIEW loggé')
  rows=self.call(run);self.assertEqual(rows[-1][1]['status'],'success');self.assertEqual(rows[0][1]['request_id'],rows[-1][1]['request_id'])
 def test_other_request_cannot_prove_success(self):
  self.event('POSITION_REVIEW',time.time(),request_id='other',trigger='[near_stop] fixture')
  rows=self.call(lambda *a,**k:SimpleNamespace(returncode=0,stdout='POSITION_REVIEW loggé'))
  self.assertEqual(rows[-1][1]['status'],'error')
 def test_zero_exit_without_review_is_error(self):
  rows=self.call(lambda *a,**k:SimpleNamespace(returncode=0,stdout=''))
  self.assertEqual(rows[-1][1]['status'],'error')
 def test_empty_focus_is_explicit_skip(self):
  rows=self.call(lambda *a,**k:SimpleNamespace(returncode=0,stdout='aucune position (dans le focus)'))
  self.assertEqual(rows[-1][1]['status'],'skipped')
 def test_timeout_records_error(self):
  def run(*a,**k):raise subprocess.TimeoutExpired('test',120)
  self.assertEqual(self.call(run)[-1][1]['status'],'error')
 def test_cap_does_not_create_pending_request(self):
  with patch.object(attention,'env',side_effect=lambda k,d='': '0' if k=='ATTENTION_LLM_CAP' else d),patch.object(attention,'log_event'),patch.object(attention,'review_event') as trace,patch.object(attention.subprocess,'run') as run:
   attention.llm_review({},'near_stop','fixture',['SEI']);trace.assert_not_called();run.assert_not_called()

if __name__=='__main__':unittest.main()
