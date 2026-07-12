"""ADVERSARIAL VALIDATION — cross-sectional funding carry (dollar-neutral, HL perps).
ÉTAPE 0 — premise EDA. READ-ONLY, scratchpad only. Goal: TRY TO KILL IT cheaply.

Thesis to refute: rank names by funding, LONG bottom (low/neg funding, get paid to hold),
SHORT top (high funding, crowded), dollar-neutral -> harvest the funding SPREAD.

Kill conditions (either -> DEAD at gate 0):
  (a) funding rank NOT persistent -> the ex-ante ranking doesn't predict collected carry
  (b) accumulated spread over a realistic hold does NOT exceed ~2x taker RT (=18 bps/pair)

Data: funding_hourly (38 days, 36 symbols, hourly). HL funding charged hourly; rate = fraction/hr.
DOF of crowding definition: rank by raw hourly funding rate (K quintile legs). Counted.
"""
from __future__ import annotations
import sqlite3, json
import numpy as np

DB="/home/crypto/alfred/data/market.db"
import os
OUT=os.path.dirname(os.path.abspath(__file__))
RNG=np.random.default_rng(20260712)
RT_PAIR=18.0   # 2 legs x 9 bps taker RT, per unit book notional
SLIP_PAIR=8.0  # 2 legs x 4 bps slippage (bot's BACKTEST_SLIPPAGE_BPS), per rebalance

c=sqlite3.connect(DB)
syms=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM funding_hourly ORDER BY symbol")]
# build hourly grid: rows=timestamps, cols=symbols
tset=sorted({r[0] for r in c.execute("SELECT DISTINCT ts FROM funding_hourly")})
tidx={t:i for i,t in enumerate(tset)}
sidx={s:j for j,s in enumerate(syms)}
F=np.full((len(tset),len(syms)),np.nan)
for s,ts,rate in c.execute("SELECT symbol,ts,rate FROM funding_hourly"):
    F[tidx[ts],sidx[s]]=rate
F_bps=F*1e4   # hourly funding in bps
T,S=F.shape
# keep only fully-populated rows (all symbols present) for clean cross-sectional ranking
full=~np.isnan(F_bps).any(axis=1)
Ff=F_bps[full]
print("="*72)
print(f"FUNDING GRID  {T} hourly ts x {S} symbols   full rows={full.sum()} ({100*full.mean():.0f}%)")
print(f"  span ~{T/24:.0f} days.  RT_pair={RT_PAIR}bps  slip_pair={SLIP_PAIR}bps")
print("="*72)

# ---------- (A) cross-sectional spread amplitude (instantaneous) ----------
K=5   # quintile-ish legs: mean of top-K vs bottom-K of 36
def leg_spread(row):
    o=np.sort(row); return o[-K:].mean()-o[:K].mean()
spr=np.array([leg_spread(r) for r in Ff])   # bps/hour, ex-ante
print("\n(A) SPREAD INSTANTANÉ  top-%d moins bottom-%d funding (bps/heure)"%(K,K))
print(f"    median={np.median(spr):.3f}  mean={spr.mean():.3f}  p10={np.percentile(spr,10):.3f}  p90={np.percentile(spr,90):.3f}")
print(f"    -> gross carry si PARFAITEMENT persistant sur H heures = H x {spr.mean():.3f} bps")
for H in (24,48,72,120,168):
    print(f"       H={H:3}h ({H/24:.0f}j): ex-ante {H*spr.mean():6.1f} bps  vs cout {RT_PAIR+SLIP_PAIR:.0f} bps  -> {'>floor' if H*spr.mean()>RT_PAIR+SLIP_PAIR else 'SOUS floor'}")

# ---------- (B) rank persistence (Spearman autocorr of funding rank) ----------
def rank(a):
    r=np.empty_like(a,dtype=float); r[np.argsort(a)]=np.arange(len(a)); return r
Rk=np.array([rank(r) for r in Ff])
print("\n(B) PERSISTANCE DU RANG funding (Spearman entre rang_t et rang_{t+lag})")
print("    lag(h)  autocorr_rang   (1.0=fige, 0=aléatoire)")
persist={}
for lag in (1,4,8,24,48,72,120,168):
    if lag>=len(Rk): continue
    a=Rk[:-lag].ravel(); b=Rk[lag:].ravel()
    rho=np.corrcoef(a,b)[0,1]
    persist[lag]=float(rho)
    print(f"     {lag:4}   {rho:+.3f}")

# ---------- (C) realized carry vs ex-ante (the decisive test) ----------
# Form portfolio at t by ex-ante funding rank (long bottom-K, short top-K).
# Realized carry over next H = sum over h of (short legs funding - long legs funding) of the FIXED legs.
# If persistent: realized ~ ex-ante*H. If mean-reverting: realized << ex-ante*H.
print("\n(C) CARRY RÉALISÉ des jambes FIXES formées à t (moyenne sur toutes les formations)")
print("    H(h)   ex-ante(H*spr)   réalisé_moy   ratio réalisé/ex-ante   net(-cout)   IC95 réalisé")
realized_summary={}
for H in (24,48,72,120,168):
    exa=[]; rea=[]
    for t0 in range(0,len(Ff)-H, max(1,H//4)):   # step ~H/4 to reduce overlap
        row=Ff[t0]
        order=np.argsort(row)
        longs=order[:K]; shorts=order[-K:]   # long low funding, short high funding
        exa_t=(row[shorts].mean()-row[longs].mean())*H
        # realized: sum over hold of (short legs collect +funding, long legs collect -funding)
        seg=Ff[t0+1:t0+1+H]   # funding realized during hold (charged each hour)
        rea_t=(seg[:,shorts].mean(axis=1)-seg[:,longs].mean(axis=1)).sum()
        exa.append(exa_t); rea.append(rea_t)
    exa=np.array(exa); rea=np.array(rea)
    ratio=rea.mean()/exa.mean() if exa.mean()!=0 else float('nan')
    net=rea-(RT_PAIR+SLIP_PAIR)
    idx=RNG.integers(0,len(rea),size=(10000,len(rea))); bm=rea[idx].mean(axis=1)
    lo,hi=np.percentile(bm,[2.5,97.5])
    realized_summary[H]={"exante":float(exa.mean()),"realized":float(rea.mean()),
                         "ratio":float(ratio),"net":float(net.mean()),"lo":float(lo),"hi":float(hi),
                         "n":int(len(rea)),"frac_pos":float((net>0).mean())}
    print(f"    {H:4}    {exa.mean():8.1f}    {rea.mean():8.1f}     {ratio:+.2f}x            {net.mean():+7.1f}     [{lo:+.1f},{hi:+.1f}] n={len(rea)}")

# ---------- half-life of the formed spread ----------
print("\n    demi-vie du différentiel de funding des jambes formées:")
H=168; decay=[]
for t0 in range(0,len(Ff)-H,12):
    row=Ff[t0]; order=np.argsort(row); longs=order[:K]; shorts=order[-K:]
    seg=Ff[t0:t0+H]; d=seg[:,shorts].mean(axis=1)-seg[:,longs].mean(axis=1)
    if d[0]>0: decay.append(d/d[0])
decay=np.array(decay).mean(axis=0)
hl=next((h for h in range(len(decay)) if decay[h]<0.5),None)
print(f"     spread(0)=1.00  spread(8h)={decay[8]:.2f}  spread(24h)={decay[24]:.2f}  spread(72h)={decay[72]:.2f}")
print(f"     demi-vie ~ {hl} heures" if hl else "     >168h (très persistant) ou ne décroît pas monotone")

# ---------- verdict gate 0 ----------
best=max(realized_summary.items(), key=lambda kv: kv[1]["lo"])
GATE0_PASS = best[1]["lo"]>0   # some hold gives realized carry net-of-cost with lower-CI>0
print("\n"+"="*72)
print(f"GATE 0 VERDICT: meilleur hold H={best[0]}h  net réalisé={best[1]['net']:+.1f}bps  IC-basse(brut)={best[1]['lo']:+.1f}")
print(f"  net IC-basse >0 ? -> {'PASS (survit gate 0)' if GATE0_PASS else 'FAIL -> MORT (carry sous le floor / non persistant)'}")
print("="*72)

json.dump({"spread_inst":{"mean":float(spr.mean()),"median":float(np.median(spr))},
           "persist":persist,"realized":realized_summary,"decay":decay.tolist(),
           "halflife":hl,"gate0":bool(GATE0_PASS),"K":K,"n_full":int(full.sum()),
           "RT_pair":RT_PAIR,"slip_pair":SLIP_PAIR},
          open(f"{OUT}/carry_gate0.json","w"))
print("saved carry_gate0.json")
