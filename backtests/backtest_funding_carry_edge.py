"""ÉTAPE 1 — Edge net + juge de paix (funding carry, dollar-neutral).
Full P&L per rebalance = funding carry + PRICE drift of the dollar-neutral book - costs.
Decompose funding vs price: funding MUST dominate else it's a disguised directional bet.
Negative control: random legs (not funding-ranked), same hold/cost. Ranked must beat p95.
READ-ONLY. scratchpad only.
"""
from __future__ import annotations
import sqlite3, json, sys
import numpy as np
sys.path.insert(0,"/home/crypto")
from backtests import backtest_rolling as R

DB="/home/crypto/alfred/data/market.db"
import os
OUT=os.path.dirname(os.path.abspath(__file__))
RNG=np.random.default_rng(20260712)
RT_PAIR=18.0; SLIP_PAIR=8.0; COST=RT_PAIR+SLIP_PAIR
K=5

# ---- funding grid ----
c=sqlite3.connect(DB)
syms=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM funding_hourly ORDER BY symbol")]
tset=sorted({r[0] for r in c.execute("SELECT DISTINCT ts FROM funding_hourly")})
tidx={t:i for i,t in enumerate(tset)}; sidx={s:j for j,s in enumerate(syms)}
F=np.full((len(tset),len(syms)),np.nan)
for s,ts,rate in c.execute("SELECT symbol,ts,rate FROM funding_hourly"): F[tidx[ts],sidx[s]]=rate
F_bps=F*1e4
tsec=np.array(tset,float)/1000.0   # funding ts -> seconds

# ---- price closes from candles (4h), nearest-past lookup ----
cand=R.load_3y_candles()
# detect candle t unit using BTC
bt=cand["BTC"]; craw=bt[0]["t"]; CSC=1000.0 if craw>1e12 else 1.0
price={}
for s in syms:
    if s not in cand: continue
    a=cand[s]; price[s]=(np.array([x["t"] for x in a],float)/CSC, np.array([x["c"] for x in a],float))
usable=[s for s in syms if s in price]
def px(s,ts):
    ct,cc=price[s]; i=np.searchsorted(ct,ts,side="right")-1
    return cc[i] if 0<=i<len(cc) else np.nan

print("="*74)
print(f"ÉTAPE 1  funding+price  {len(usable)}/{len(syms)} symbols priced  cost/rebal={COST}bps  K={K}")
print("="*74)

def form(t0_i):
    """Return (funding_bps, price_bps, net_bps, longs, shorts) for a hold from index t0_i over H."""
    row=F_bps[t0_i]
    ok=~np.isnan(row); order=np.argsort(np.where(ok,row,np.inf))
    order=[j for j in order if ok[j] and syms[j] in price]
    longs=order[:K]; shorts=order[-K:]
    return longs,shorts,row

def run(H, ranked=True, rng_legs=None):
    fund=[]; pric=[]; net=[]
    step=max(1,H//4)
    for t0 in range(0,len(F_bps)-H,step):
        row=F_bps[t0]; ok=~np.isnan(row)
        pool=[j for j in range(len(syms)) if ok[j] and syms[j] in price]
        if len(pool)<2*K: continue
        if ranked:
            order=sorted(pool,key=lambda j:row[j])
            longs=order[:K]; shorts=order[-K:]
        else:
            pick=rng_legs.permutation(pool)[:2*K]; longs=pick[:K]; shorts=pick[K:2*K]
        # funding realized over hold (short collects +f, long collects -f)
        seg=F_bps[t0+1:t0+1+H]
        f_bps=(seg[:,shorts].mean(axis=1)-seg[:,longs].mean(axis=1)).sum()
        # price drift dollar-neutral: +ret(longs) -ret(shorts)
        t_a=tsec[t0]; t_b=tsec[min(t0+H,len(tsec)-1)]
        rl=np.array([px(syms[j],t_b)/px(syms[j],t_a)-1 for j in longs]);
        rs=np.array([px(syms[j],t_b)/px(syms[j],t_a)-1 for j in shorts])
        if np.isnan(rl).any() or np.isnan(rs).any(): continue
        p_bps=(rl.mean()-rs.mean())*1e4
        fund.append(f_bps); pric.append(p_bps); net.append(f_bps+p_bps-COST)
    return np.array(fund),np.array(pric),np.array(net)

def ci(x):
    idx=RNG.integers(0,len(x),size=(10000,len(x))); bm=x[idx].mean(axis=1)
    return np.percentile(bm,[2.5,97.5])

print("\n RANKED — décomposition funding vs prix, net de coût:")
print("  H(h)  n   funding_moy  prix_moy   NET_moy   IC95(net)        funding domine?")
res={}
for H in (48,72,120,168):
    f,p,nt=run(H,ranked=True)
    if len(nt)<5: continue
    lo,hi=ci(nt)
    dom = abs(f.mean())>abs(p.mean())
    res[H]={"n":int(len(nt)),"fund":float(f.mean()),"price":float(p.mean()),
            "net":float(nt.mean()),"lo":float(lo),"hi":float(hi),
            "fund_dom":bool(dom),"frac_pos":float((nt>0).mean())}
    print(f"  {H:4} {len(nt):3}   {f.mean():+8.1f}   {p.mean():+7.1f}  {nt.mean():+7.1f}   [{lo:+.1f},{hi:+.1f}]   {'OUI' if dom else 'NON (pari prix!)'}")

# ---- negative control: random legs ----
print("\n CONTRÔLE NÉGATIF — jambes aléatoires (100 tirages), même hold/coût:")
print("  H(h)   ranked_net   random_p50   random_p95   ranked>p95 random?")
ctrl={}
for H in (72,120,168):
    if H not in res: continue
    rmeans=[]
    for k in range(100):
        _,_,nt=run(H,ranked=False,rng_legs=np.random.default_rng(1000+k))
        if len(nt): rmeans.append(nt.mean())
    rmeans=np.array(rmeans); p50=np.percentile(rmeans,50); p95=np.percentile(rmeans,95)
    beats=res[H]["net"]>p95
    ctrl[H]={"ranked":res[H]["net"],"rand_p50":float(p50),"rand_p95":float(p95),"beats":bool(beats)}
    print(f"  {H:4}    {res[H]['net']:+7.1f}     {p50:+7.1f}     {p95:+7.1f}     {'OUI' if beats else 'NON -> pas mieux que hasard'}")

# ---- verdict etape 1 ----
best=max(res.items(),key=lambda kv:kv[1]["lo"]) if res else (None,{"lo":-1})
E1 = (best[1]["lo"]>0) and res.get(best[0],{}).get("fund_dom",False) and ctrl.get(best[0],{}).get("beats",False)
print("\n"+"="*74)
if best[0]:
    print(f"ÉTAPE 1 VERDICT: meilleur H={best[0]}h net={best[1]['net']:+.1f}bps IC=[{best[1]['lo']:+.1f},{best[1]['hi']:+.1f}]")
    print(f"  net IC-basse>0 ? {best[1]['lo']>0}   funding domine ? {best[1]['fund_dom']}   bat p95 random ? {ctrl.get(best[0],{}).get('beats')}")
print(f"  ÉTAPE 1: {'PASS' if E1 else 'FAIL'}")
print("="*74)
json.dump({"ranked":res,"control":ctrl,"E1":bool(E1),"K":K,"COST":COST},open(f"{OUT}/carry_etape1.json","w"))
print("saved carry_etape1.json")
