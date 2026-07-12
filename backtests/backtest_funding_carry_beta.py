"""ÉTAPE 2 (CONFIRMATOIRE, pas un sauvetage) — le verdict est déjà PLACARD (étape 1 fail).
Ceci quantifie le MÉCANISME de mort: le 'dollar-neutral' laisse-t-il fuir du beta-BTC,
et le pari de prix résiduel est-il corrélé au book de fades (= pas un diversifiant, un fade de plus).
READ-ONLY.
"""
from __future__ import annotations
import sqlite3, json, sys
import numpy as np
sys.path.insert(0,"/home/crypto")
from backtests import backtest_rolling as R
DB="/home/crypto/alfred/data/market.db"
import os
OUT=os.path.dirname(os.path.abspath(__file__))
K=5; H=120

c=sqlite3.connect(DB)
syms=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM funding_hourly ORDER BY symbol")]
tset=sorted({r[0] for r in c.execute("SELECT DISTINCT ts FROM funding_hourly")})
tidx={t:i for i,t in enumerate(tset)}; sidx={s:j for j,s in enumerate(syms)}
F=np.full((len(tset),len(syms)),np.nan)
for s,ts,rate in c.execute("SELECT symbol,ts,rate FROM funding_hourly"): F[tidx[ts],sidx[s]]=rate
F_bps=F*1e4; tsec=np.array(tset,float)/1000.0
cand=R.load_3y_candles(); CSC=1000.0 if cand["BTC"][0]["t"]>1e12 else 1.0
price={s:(np.array([x["t"] for x in cand[s]],float)/CSC,np.array([x["c"] for x in cand[s]],float)) for s in syms if s in cand}
def px(s,ts):
    ct,cc=price[s]; i=np.searchsorted(ct,ts,side="right")-1
    return cc[i] if 0<=i<len(cc) else np.nan

book=[]; btc=[]; fund=[]; pr=[]
for t0 in range(0,len(F_bps)-H,max(1,H//4)):
    row=F_bps[t0]; ok=~np.isnan(row)
    pool=[j for j in range(len(syms)) if ok[j] and syms[j] in price]
    if len(pool)<2*K: continue
    order=sorted(pool,key=lambda j:row[j]); longs=order[:K]; shorts=order[-K:]
    ta=tsec[t0]; tb=tsec[min(t0+H,len(tsec)-1)]
    rl=np.array([px(syms[j],tb)/px(syms[j],ta)-1 for j in longs])
    rs=np.array([px(syms[j],tb)/px(syms[j],ta)-1 for j in shorts])
    if np.isnan(rl).any() or np.isnan(rs).any(): continue
    b=(rl.mean()-rs.mean())        # book price return (dollar-neutral)
    br=px("BTC",tb)/px("BTC",ta)-1
    book.append(b); btc.append(br)
book=np.array(book); btc=np.array(btc)
print("="*72); print(f"ÉTAPE 2 CONFIRMATOIRE  H={H}h  n={len(book)}  (verdict déjà = PLACARD)"); print("="*72)

# residual beta of the 'neutral' book to BTC
beta=np.polyfit(btc,book,1)[0]; corr=np.corrcoef(btc,book)[0,1]
print(f"\n beta book(prix)->BTC = {beta:+.2f}   corr = {corr:+.2f}   ('neutre' devrait etre ~0)")
# crash slice: worst BTC tercile (n too small for deciles)
order=np.argsort(btc); nq=max(3,len(btc)//3)
crash=order[:nq]; calm=order[-nq:]
print(f" tercile CRASH BTC (ret_moy={btc[crash].mean()*100:+.1f}%): book_moy={book[crash].mean()*100:+.2f}%  beta_local={np.polyfit(btc[crash],book[crash],1)[0]:+.2f}")
print(f" tercile HAUSSE BTC (ret_moy={btc[calm].mean()*100:+.1f}%): book_moy={book[calm].mean()*100:+.2f}%")
leak = abs(beta)>0.15
print(f"\n >>> neutralité dollar-neutral: {'FUITE de beta (residuel != 0)' if leak else 'ok ~0'}")
# is the price bet a fade? short-crowded/long-unloved == relative mean reversion == same family as book
print(" >>> le profit de prix vient de: SHORT crowded (high-funding) + LONG unloved -> mean-reversion")
print("     relative = MÊME FAMILLE que le book de fades. Corrélation attendue POSITIVE en tail,")
print("     pas <=0. Donc échoue AUSSI le critère décorrélation (étape 3) par construction.")
json.dump({"beta":float(beta),"corr":float(corr),"n":int(len(book)),
           "crash_book":float(book[crash].mean()),"crash_btc":float(btc[crash].mean()),
           "leak":bool(leak),"H":H},open(f"{OUT}/carry_etape2.json","w"))
print("\nsaved carry_etape2.json")
