"""Independent scalar recheck of detected events, fills, costs and monthly coverage."""
from pathlib import Path
import sqlite3,json,bisect,math,collections
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--work-dir",type=Path,required=True)
root=parser.parse_args().work_dir.resolve();result=json.loads((root/'result.json').read_text());H=3600000
cdb=sqlite3.connect((root/'event_cache.db').as_uri()+'?mode=ro',uri=True)
odb=sqlite3.connect((root/'oi_history.db').as_uri()+'?mode=ro',uri=True)
fdb=sqlite3.connect((root/'funding_history.db').as_uri()+'?mode=ro',uri=True)
def quantile(xs,q):
 v=sorted(xs);at=(len(v)-1)*q;low=math.floor(at);high=math.ceil(at)
 return v[low]+(v[high]-v[low])*(at-low)
checked=0;valid_months=collections.Counter();history_missing=collections.Counter()
for sym in result['coverage']:
 rows=cdb.execute('select t,o,h,l,c,v from c1h where symbol=? order by t',(sym,)).fetchall();index={r[0]:i for i,r in enumerate(rows)}
 oi=odb.execute('select ts,oi from asset_ctx where symbol=? and oi is not null order by ts',(sym,)).fetchall();ots=[int(t*1000) for t,v in oi]
 rates=fdb.execute('select ts,funding_rate from funding where symbol=? order by ts',(sym,)).fetchall();fts=[r[0] for r in rates]
 def asof(t):
  j=bisect.bisect_right(ots,t)-1
  if j<0 or t-ots[j]>=H or oi[j][1]<=0:return None
  return oi[j][1]
 deltas=[]
 for t,*_ in rows:
  a=asof(t);b=asof(t+H);deltas.append(b-a if a is not None and b is not None else None)
 for e in [e for e in result['events'] if e['symbol']==sym]:
  i=index[e['signal_open_t']];prior=rows[i-720:i];d=deltas[i-720:i]
  assert len(prior)==720 and None not in d
  r=rows[i]
  assert r[4]<r[1] and deltas[i]<0 and deltas[i]<=quantile(d,.05)
  assert r[5]>=quantile([v[5] for v in prior],.95)
  assert (r[2]-r[3])/r[4]>=quantile([(v[2]-v[3])/v[4] for v in prior],.95)
  assert e['entry_t']==r[0]+2*H
  entry=rows[index[e['entry_t']]][1];exit=rows[index[e['exit_t']]][1]
  funding=rates[bisect.bisect_left(fts,e['entry_t']):bisect.bisect_left(fts,e['exit_t'])]
  assert len(funding)==e['hold_hours']
  net=(exit/entry-1)*10000-sum(x[1] for x in funding)*10000-25
  assert abs(net-e['net25_bps'])<1e-8
  checked+=1
 # Why some calendar months cannot be evaluated: causal rolling OI history.
 import datetime
 for i,r in enumerate(rows):
  month=datetime.datetime.fromtimestamp(r[0]/1000,datetime.timezone.utc).strftime('%Y-%m')
  if i<720 or None in deltas[max(0,i-720):i+1]:history_missing[month]+=1
  elif r[4]<r[1] and r[0]-rows[i-720][0]==720*H:valid_months[month]+=1
out={'event_horizon_rows_independently_verified':checked,'eligible_down_hours_by_month':dict(valid_months),'hours_without_full_rolling_oi_history':dict(history_missing),'all_checks_passed':True}
(root/'verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
