"""AI-SHADOW-20260920-v1: read-only chronological exit replay and entry attribution.
Never writes breaker flags, changes the bot, contacts AI or places orders.
"""
import argparse,json,math,hashlib
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from backtests.audit_exit_protection import read_db,ts
VERSION='AI-SHADOW-20260920-v1'

def replay_exit(trade,events,ticks):
    start,end=ts(trade['entry_time']),ts(trade['exit_time'])
    entry,size,actual=(float(trade[k]) for k in ('entry_price','size_usdt','exit_price'))
    if not events or end<=start or min(entry,size,actual)<=0:raise ValueError('Invalid input')
    sign={'LONG':1,'SHORT':-1}[trade['direction']]
    events=sorted(events,key=lambda e:e['ts'])
    if any(e['data'].get('acted') for e in events):return dict(status='acted_not_comparable')
    if len({(e['data'].get('model'),e['data'].get('prompt_hash')) for e in events})!=1:return dict(status='mixed_prompts')
    beginning=events[0]['ts']
    if not start<=beginning<end:return dict(status='decision_outside_trade')
    ticks=[(t,p) for t,p in ticks if beginning<t<end]
    if any(not math.isfinite(p) or p<=0 for t,p in ticks):raise ValueError('Invalid price')
    timeline=[beginning]+[t for t,p in ticks]+[end]
    if any(b<=a for a,b in zip(timeline,timeline[1:])):raise ValueError('Unordered ticks')
    if not ticks or any(b-a>120 for a,b in zip(timeline,timeline[1:])):return dict(status='missing_ticks')
    stop=None;pending=None;i=0
    for t,price in ticks:
        if pending:
            fill=price*(1-sign*.0002)
            return dict(status='resolved',action=pending,exit_ts=t,fill=fill,delta_price_usdt=size*sign*(fill-actual)/entry)
        cut=False
        while i<len(events) and events[i]['ts']<t:
            e=events[i]['data'];i+=1
            if e.get('tripped'):continue
            if e.get('action')=='LOCK' and e.get('lock_mode')=='shadow' and e.get('note')=='lock_shadow':
                value=e.get('stop_usdt')
                if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Invalid stop')
                stop=value if stop is None else max(stop,value)
            if e.get('action')=='CUT' and e.get('cut_mode')=='shadow':cut=True
        # CUT executes at first tick after logged decision, and only if still losing.
        gross=sign*(price/entry-1)
        if cut and gross<0:
            fill=price*(1-sign*.0002)
            return dict(status='resolved',action='CUT',exit_ts=t,fill=fill,delta_price_usdt=size*sign*(fill-actual)/entry)
        # Mirrors current guard's 10bps cost proxy, excluding actual funding.
        if stop is not None and size*(gross-.001)<=stop:pending='LOCK'
    return dict(status='resolved',action='unchanged',delta_price_usdt=0.)

def audit(data_dir,since):
    db=read_db(data_dir/'bots/live/bot.db');market=read_db(data_dir/'market.db')
    entries=[];exits=[];inputs=[];costs=defaultdict(float)
    try:
        trades={}
        for r in db.execute('SELECT * FROM trades'):
            tr=dict(r);key=(tr['symbol'],int(ts(tr['entry_time'])*1000))
            if key in trades:raise ValueError('Ambiguous trade identity')
            trades[key]=tr
        grouped=defaultdict(list);entry_grouped=defaultdict(list)
        for r in db.execute("SELECT rowid,ts,event,symbol,data FROM events WHERE ts>=? AND event IN ('ARBITER_DECISION','ARBITER_EXIT_DECISION','AI_COST') ORDER BY ts,rowid",(since,)):
            d=json.loads(r['data']);key=(r['symbol'],d.get('entry_ts_ms'))
            if r['event']=='AI_COST':costs[d.get('source','unknown')]+=d.get('cost_usd',0);continue
            event=dict(ts=r['ts'],data=d)
            if r['event']=='ARBITER_DECISION':entry_grouped[key].append(event)
            else:grouped[key].append(event)
        for key,evs in entry_grouped.items():
            d=evs[0]['data'];tr=trades.get(key)
            row=dict(symbol=key[0],entry_ts_ms=key[1],decision=d.get('decision'),factor=d.get('factor'),prompt_hash=d.get('prompt_hash'),model=d.get('model'))
            if len(evs)!=1:row['status']='duplicate_decisions'
            elif d.get('mode')!='shadow' or d.get('acted'):row['status']='not_shadow'
            elif tr is None:row['status']='pending_or_unmatched'
            else:
                f=d['factor']
                if not isinstance(f,(int,float)) or not 0<=f<=1:raise ValueError('Invalid size factor')
                row.update(status='resolved',actual_pnl_usdt=tr['pnl_usdt'],delta_size_usdt=(f-1)*tr['pnl_usdt'])
            entries.append(row)
        for key,events in grouped.items():
            tr=trades.get(key)
            row=dict(symbol=key[0],entry_ts_ms=key[1],events=len(events),model=events[0]['data'].get('model'),prompt_hash=events[0]['data'].get('prompt_hash'))
            if tr is None:row['status']='pending_or_unmatched'
            else:
                # Refuse windows that omit earlier interventions for this position.
                earlier=db.execute("SELECT data FROM events WHERE event='ARBITER_EXIT_DECISION' AND symbol=? AND ts<?",(key[0],since)).fetchall()
                if any(json.loads(x[0]).get('entry_ts_ms')==key[1] for x in earlier):row['status']='left_censored'
                else:
                    ticks=[tuple(r) for r in market.execute('SELECT ts,mark_px FROM ticks WHERE symbol=? AND ts>? AND ts<? ORDER BY ts',(key[0],events[0]['ts'],ts(tr['exit_time'])))]
                    inputs.append(dict(trade=tr,events=events,ticks=ticks))
                    row.update(replay_exit(tr,events,ticks));row['actual_pnl_usdt']=tr['pnl_usdt']
            exits.append(row)
    finally:db.close();market.close()
    return dict(version=VERSION,checked_at=datetime.now(timezone.utc).isoformat(),since=since,entries=entries,exits=exits,api_costs_usd=dict(costs),inputs=inputs,input_sha256=hashlib.sha256(json.dumps(inputs,sort_keys=True,allow_nan=False).encode()).hexdigest(),limitations=['Entry proportional accounting, no replacement trades/portfolio effects.','Exit price-only attribution; differential funding omitted; 10bps guard cost proxy, 2bps exit slippage.','Full post-decision tick coverage required, maximum gap 120s; no interpolation.','Repeated exit decisions form one chronological trajectory per position, stops only tighten.','Open/unmatched positions and left-censored histories not scored.','Later recorded AI advice was generated from the actual unmodified book, not a counterfactual book.','GO agreement is not incremental value; HOLD quality not measured here.','No automatic promotion, no circuit-breaker writes.'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',type=Path,default=Path('alfred/data'));p.add_argument('--since',default='2026-09-17T00:00:00+00:00');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=audit(a.data_dir,ts(a.since));a.out.write_text(json.dumps(r,indent=2,allow_nan=False)+'\n');r.pop('inputs');print(json.dumps(r,indent=2))
