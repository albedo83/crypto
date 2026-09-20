"""EXIT-PROTECT-20260920-v1. Read-only matched-entry replay, not portfolio PNL."""
import gzip
import argparse, hashlib, json, math, sqlite3
from datetime import datetime, timezone
from pathlib import Path
VERSION='EXIT-PROTECT-20260920-v1'

def ts(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('Timezone required')
    return dt.timestamp()

def replay(trade,ticks):
    start,end=ts(trade['entry_time']),ts(trade['exit_time'])
    entry,size,actual=(float(trade[k]) for k in ('entry_price','size_usdt','exit_price'))
    if trade['direction'] not in ('LONG','SHORT') or end<=start: raise ValueError('Invalid chronology/direction')
    if any(not math.isfinite(v) or v<=0 for v in (entry,size,actual)): raise ValueError('Invalid trade')
    direction=1 if trade['direction']=='LONG' else -1
    ticks=[(float(t),float(p)) for t,p in ticks if start<t<end]
    if any(not math.isfinite(p) or p<=0 or not math.isfinite(t) for t,p in ticks): raise ValueError('Invalid tick')
    times=[start]+[t for t,p in ticks]+[end]
    if any(b<=a for a,b in zip(times,times[1:])): raise ValueError('Unordered ticks')
    gap=max(b-a for a,b in zip(times,times[1:]))
    if not ticks or gap>120: return dict(covered=False,max_gap_s=gap,triggered=False,delta_price_usdt=None)
    peak=0.; pending=False
    for t,price in ticks:
        if pending:
            fill=price*(1-direction*2/10000)
            return dict(covered=True,max_gap_s=gap,triggered=True,trigger_ts=trigger_ts,exit_ts=t,fill=fill,delta_price_usdt=size*direction*(fill-actual)/entry,peak_before_trigger_bps=peak)
        pnl=direction*(price/entry-1)*10000
        peak=max(peak,pnl)
        if peak>=1000 and pnl<=peak*.5: pending=True;trigger_ts=t
    return dict(covered=True,max_gap_s=gap,triggered=False,delta_price_usdt=0.,reason='original_exit_first' if pending else 'no_trigger')

def read_db(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c

def run(data_dir, snapshot_out=None):
    bot=read_db(data_dir/'bots/live/bot.db');market=read_db(data_dir/'market.db')
    inputs=[];rows=[];digest=hashlib.sha256();split=ts('2026-09-17T00:00:00+00:00')
    try:
        trades=[dict(r) for r in bot.execute("SELECT * FROM trades WHERE strategy IN ('S1','S5') ORDER BY entry_time,id")]
        for trade in trades:
            start,end=ts(trade['entry_time']),ts(trade['exit_time'])
            ticks=[tuple(r) for r in market.execute('SELECT ts,mark_px FROM ticks WHERE symbol=? AND ts>? AND ts<? ORDER BY ts',(trade['symbol'],start,end))]
            inputs.append([trade,ticks])
            digest.update(json.dumps([trade,ticks],sort_keys=True,allow_nan=False).encode())
            result=replay(trade,ticks)
            result.update({k:trade[k] for k in ('id','symbol','strategy','direction','entry_time','exit_time','pnl_usdt')})
            result['cohort']='prior' if end<split else ('recent' if start>=split else 'straddling')
            rows.append(result)
    finally: bot.close();market.close()
    if snapshot_out is not None:
        with gzip.open(snapshot_out, "wt") as f: json.dump(inputs,f,allow_nan=False)
    summary={}
    for cohort in ('prior','recent','straddling','all'):
        group=[r for r in rows if cohort=='all' or r['cohort']==cohort]
        covered=[r for r in group if r['covered']];hit=[r for r in covered if r['triggered']]
        summary[cohort]=dict(trades=len(group),covered=len(covered),excluded=len(group)-len(covered),triggered=len(hit),improved=sum(r['delta_price_usdt']>0 for r in hit),worsened=sum(r['delta_price_usdt']<0 for r in hit),delta_price_usdt=sum(r['delta_price_usdt'] for r in covered))
    return dict(version=VERSION,checked_at=datetime.now(timezone.utc).isoformat(),rule=dict(strategies=['S1','S5'],arm_bps=1000,retain_fraction=.5,max_gap_s=120,exit_slippage_bps=2),split='2026-09-17T00:00:00+00:00',input_sha256=digest.hexdigest(),limitations=['Historical data already inspected: no independent validation.','Same actual entries/sizes; no replacement trades or portfolio sizing/margin.','Price-only delta, unchanged exit fee assumed; funding difference omitted.','Sampled ticks, next-tick fill; no liquidity model.','Crossing-split trades separated, open trades excluded.','Any gap >120s excludes entire trade; no interpolation.'],summary=summary,trades=rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',type=Path,default=Path('alfred/data'));p.add_argument('--out',type=Path,required=True);p.add_argument('--snapshot-out',type=Path);a=p.parse_args()
    result=run(a.data_dir,a.snapshot_out);a.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');print(json.dumps(result['summary'],indent=2))
