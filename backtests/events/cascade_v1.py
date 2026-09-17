"""CASCADE-v1 fixed protocol; local read-only snapshots, no trading imports."""
from pathlib import Path
import collections,datetime,json,sqlite3,hashlib
import numpy as np
H=3600000

def asof(times, values, grid):
    idx=np.searchsorted(times,grid,side='right')-1
    safe=np.maximum(idx,0)
    ok=(idx>=0)&(grid-times[safe]<H)&(values[safe]>0)&np.isfinite(values[safe])
    return np.where(ok,values[safe],np.nan)

def trailing_threshold(values, q, lookback=720):
    result=np.full(len(values),np.nan)
    if len(values)>lookback:
        windows=np.lib.stride_tricks.sliding_window_view(values,lookback)[:-1]
        result[lookback:]=np.quantile(windows,q,axis=1)
    return result

def detect(c, oi_times, oi_values, lookback=720):
    t=c[:,0].astype(np.int64)
    delta=asof(oi_times,oi_values,t+H)-asof(oi_times,oi_values,t)
    amplitude=(c[:,2]-c[:,3])/c[:,4]
    thresholds=[trailing_threshold(x,q,lookback) for x,q in [(delta,.05),(c[:,5],.95),(amplitude,.95)]]
    valid=np.isfinite(delta)&np.all(np.isfinite(thresholds),axis=0)
    continuity=np.zeros(len(t),dtype=bool)
    continuity[lookback:]=(t[lookback:]-t[:-lookback]==lookback*H)
    valid &= continuity & (c[:,4]<c[:,1]) & np.all(np.isfinite(c),axis=1)
    raw=valid&(delta<0)&(delta<=thresholds[0])&(c[:,5]>=thresholds[1])&(amplitude>=thresholds[2])
    kept=[];last=-10**18
    for i in np.flatnonzero(raw):
        if t[i]-last>=24*H:kept.append(int(i));last=t[i]
    return valid,raw,kept

def outcome(c,i,hold,funding):
    # signal at i+1; wait one complete hour; enter at i+2.
    start=i+2;end=start+hold
    if end>=len(c):return None,'price_tail'
    t=int(c[start,0]);exit_t=int(c[end,0])
    if t!=int(c[i,0])+2*H or exit_t!=t+hold*H:return None,'price_gap'
    if not np.all(np.diff(c[i:end+1,0])==H):return None,'price_gap'
    rates=[funding.get(ts) for ts in range(t,exit_t,H)]
    if any(r is None or not np.isfinite(r) for r in rates):return None,'funding_gap'
    if c[start,1]<=0 or c[end,1]<=0:return None,'invalid_price'
    gross=(c[end,1]/c[start,1]-1)*10000
    funding_bps=sum(rates)*10000
    return dict(entry_t=t,exit_t=exit_t,gross_bps=float(gross),funding_bps=float(funding_bps),net13_bps=float(gross-funding_bps-13),net25_bps=float(gross-funding_bps-25)),None

def date(t):return datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).isoformat()

def aggregate(rows):
    days=collections.defaultdict(list);months=collections.defaultdict(list)
    for row in rows:days[date(row['entry_t'])[:10]].append(row)
    daily=[]
    for day,v in sorted(days.items()):
        r={'day':day,'tokens':len(v)}
        for key in ['net13_bps','net25_bps','excess_bps']:r[key]=float(np.mean([x[key] for x in v]))
        daily.append(r);months[day[:7]].append(r)
    if not daily:return {'n_events':0,'n_days':0,'months':{},'gate':False}
    result={'n_events':len(rows),'n_days':len(days),'months':{},'daily':daily}
    for key in ['net13_bps','net25_bps','excess_bps']:
        x=[r[key] for r in daily]
        result['mean_'+key]=float(np.mean(x));result['median_'+key]=float(np.median(x))
    for month,v in sorted(months.items()):
        result['months'][month]={'days':len(v),'mean_net25_bps':float(np.mean([r['net25_bps'] for r in v])),'mean_excess_bps':float(np.mean([r['excess_bps'] for r in v]))}
    positive=sum(v['mean_excess_bps']>0 for v in result['months'].values())
    result['gate_checks']={'at_least_30_days':len(days)>=30,'stress_mean_positive':result['mean_net25_bps']>0,'stress_median_positive':result['median_net25_bps']>0,'excess_positive':result['mean_excess_bps']>0,'two_thirds_months_excess_positive':positive*3>=len(months)*2}
    result['gate']=all(result['gate_checks'].values())
    return result

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir",type=Path,required=True)
    root=parser.parse_args().work_dir.resolve()
    databases=[sqlite3.connect((root/n).as_uri()+'?mode=ro',uri=True) for n in ['event_cache.db','oi_history.db','funding_history.db']]
    candles,oi,fund=databases
    for db in databases:db.execute('PRAGMA query_only=ON')
    # Exact universe in the archived 34-token phase0 cache; no selection by return.
    symbols=[r[0] for r in candles.execute('select distinct symbol from c1h order by symbol')]
    details=[];coverage={};missing=collections.Counter();baselines={}
    for sym in symbols:
        c=np.array(candles.execute('select t,o,h,l,c,v from c1h where symbol=? order by t',(sym,)).fetchall(),dtype=float)
        o=np.array(oi.execute('select ts,oi from asset_ctx where symbol=? and oi is not null order by ts',(sym,)).fetchall(),dtype=float)
        f=fund.execute('select ts,funding_rate from funding where symbol=? order by ts',(sym,)).fetchall()
        funding={};duplicates=set()
        for ts,rate in f:
            hour=int(ts)//H*H
            if hour in funding:duplicates.add(hour)
            funding[hour]=rate
        for hour in duplicates:funding.pop(hour)
        valid,raw,kept=detect(c,(o[:,0]*1000).astype(np.int64),o[:,1])
        coverage[sym]={'candles':len(c),'start':date(c[0,0]),'end':date(c[-1,0]),'valid_down_hours':int(valid.sum()),'raw':int(raw.sum()),'declustered':len(kept),'duplicate_funding_hours':len(duplicates)}
        controls=collections.defaultdict(list)
        for i in np.flatnonzero(valid&~raw):
            for hold in [4,24,72]:
                result,reason=outcome(c,int(i),hold,funding)
                if result:controls[(date(result['entry_t'])[:7],hold)].append(result['net25_bps'])
        for i in kept:
            for hold in [4,24,72]:
                result,reason=outcome(c,i,hold,funding)
                if not result:missing[(hold,reason)]+=1;continue
                month=date(result['entry_t'])[:7];control=controls[(month,hold)]
                if not control:missing[(hold,'no_matched_control')]+=1;continue
                result.update(symbol=sym,hold_hours=hold,signal_open_t=int(c[i,0]),control_n=len(control),control_mean_bps=float(np.mean(control)))
                result['excess_bps']=result['net25_bps']-result['control_mean_bps'];details.append(result)
        print(sym,coverage[sym]['raw'],len(kept),flush=True)
    output={'protocol_sha256':hashlib.sha256((root/'protocol.md').read_bytes()).hexdigest(),'snapshot_hashes':json.loads((root/'snapshot_hashes.json').read_text()),'primary_horizon_hours':24,'coverage':coverage,'missing':[{'hold_hours':h,'reason':r,'count':n} for (h,r),n in sorted(missing.items())],'summary':{str(h):aggregate([r for r in details if r['hold_hours']==h]) for h in [4,24,72]},'events':details}
    (root/'result.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({h:{k:v for k,v in s.items() if k!='daily'} for h,s in output['summary'].items()},indent=2))
    for db in databases:db.close()

if __name__=='__main__':main()
