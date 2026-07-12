"""VALIDATION ADVERSE — overlay de hedge conditionnel anti-crash-corrélé.
ÉTAPE 0 — CARACTÉRISER L'ENNEMI. READ-ONLY, scratchpad only.

Question tueuse: le drawdown CORRÉLÉ (book perd ET BTC perd ensemble) est-il une part
DOMINANTE du risque du book ? Sinon un hedge de crash ne sert à rien -> MORT ICI.

Book = production backtest (aligned, modulateur, margin, mfe_on_close), fenêtre la plus
profonde (~28-34m de bougies 4h). Equity mark-to-market = capital + basket_unreal.
"""
from __future__ import annotations
import sys, json
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0,"/home/crypto")
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
import os
OUT=os.path.dirname(os.path.abspath(__file__))

data=load_3y_candles(); features=build_features(data)
sector_features=compute_sector_features(features,data); dxy=load_dxy()
oi=load_oi(); funding=load_funding()
first=min(c["t"] for c in data["BTC"]); last=max(c["t"] for c in data["BTC"])
prod=dict(start_capital=1000.0, oi_data=oi, funding_data=funding,
          apply_adaptive_modulator=True, aligned=True, margin_check=True, mfe_on_close=True)
print("Running full-window production backtest ...",flush=True)
res=run_window(features,data,sector_features,dxy,first,last,**prod)
bts=res["basket_timeseries"]; trades=res["trades"]
print(f"  {res['n_trades']} trades, pnl {res['pnl']:+.0f}, maxDD {res['max_dd_pct']:.1f}%, span "
      f"{datetime.utcfromtimestamp(first/1000).date()} -> {datetime.utcfromtimestamp(last/1000).date()}")

# ---- book equity curve (mark-to-market) ----
ts=np.array([b["ts"] for b in bts],dtype=np.int64)
cap=np.array([b["capital"] for b in bts],float)
unreal=np.array([b["basket_unreal"] for b in bts],float)
npos=np.array([b["n_pos"] for b in bts],float)
E=cap+unreal
# BTC close aligned to the same ts
bt={c["t"]:c["c"] for c in data["BTC"]}
btc=np.array([bt.get(t,np.nan) for t in ts],float)
ok=~np.isnan(btc); ts,cap,unreal,npos,E,btc=[a[ok] for a in (ts,cap,unreal,npos,E,btc)]
# reconstruct net/gross notional from open positions at each ts
tr=[(t["entry_t"],t["exit_t"],t["size"],t["dir"]) for t in trades]
gross=np.zeros(len(ts)); net=np.zeros(len(ts))
for e,x,sz,d in tr:
    m=(ts>=e)&(ts<x); gross[m]+=sz; net[m]+=sz*d

rb=np.diff(E)/E[:-1]                    # book 4h return
rk=np.diff(btc)/btc[:-1]               # btc 4h return
n=len(rb)
print(f"\nEquity pts={len(E)} (4h). Book net-notional/gross moyen = {np.nanmean(np.where(gross>0,net/gross,np.nan)):+.2f} (net-long?>0)")

# ---- overall coupling ----
beta=np.polyfit(rk,rb,1)[0]; corr=np.corrcoef(rk,rb)[0,1]
r2=corr**2
print("="*72); print("(A) COUPLAGE GLOBAL book vs BTC (rendements 4h)"); print("="*72)
print(f"  beta={beta:+.3f}  corr={corr:+.3f}  R2={r2:.3f}  (part systématique de la variance)")

# ---- BTC decile conditioning: where does the book bleed? ----
print("\n(B) RENDEMENT DU BOOK PAR DÉCILE DE RENDEMENT BTC (le tail corrélé)")
dec=np.clip((np.argsort(np.argsort(rk))*10//n),0,9)
print("  décile  BTC_moy%   book_moy%   book_sum($ approx via E)  #candles")
tail_book=0; tail_btc=0
for dd in range(10):
    m=dec==dd
    print(f"    {dd}     {rk[m].mean()*100:+6.2f}   {rb[m].mean()*100:+6.2f}      n={m.sum()}")
crash=dec==0  # worst BTC decile
print(f"  >>> décile-crash BTC (ret_moy {rk[crash].mean()*100:+.1f}%): book {rb[crash].mean()*100:+.2f}%/candle")

# ---- drawdown episode decomposition ----
peak=np.maximum.accumulate(E); dd_ser=E/peak-1.0
print("\n(C) ÉPISODES DE DRAWDOWN — corrélés vs idiosyncratiques")
# find contiguous underwater episodes
epis=[]; i=0
while i<len(dd_ser):
    if dd_ser[i]<-1e-6:
        j=i
        while j<len(dd_ser) and dd_ser[j]<-1e-6: j+=1
        seg=slice(i,j); trough=i+np.argmin(dd_ser[seg])
        pk=i-1 if i>0 else i
        depth=dd_ser[trough]
        btc_move=btc[trough]/btc[pk]-1 if btc[pk]>0 else 0.0
        dur_h=(ts[j-1]-ts[i])/3.6e6
        epis.append(dict(i=int(i),trough=int(trough),depth=float(depth),
                         btc_move=float(btc_move),dur_h=float(dur_h),
                         peak_E=float(E[pk]),trough_E=float(E[trough])))
        i=j
    else: i+=1
epis.sort(key=lambda e:e["depth"])   # deepest first (most negative)
BTC_CRASH_THRESH=-0.03   # episode counts as 'correlated' if BTC fell >3% during it
tot_depth=sum(-e["depth"] for e in epis)
corr_depth=sum(-e["depth"] for e in epis if e["btc_move"]<=BTC_CRASH_THRESH)
print(f"  {len(epis)} épisodes underwater. Seuil corrélé = BTC <= {BTC_CRASH_THRESH*100:.0f}% pendant l'épisode.")
print(f"  10 pires épisodes (profondeur, BTC concomitant, durée):")
print("    rang  depth%   BTC_pdt%   durée_h   type")
worst_dollar=0
for k,e in enumerate(epis[:10]):
    typ="CORRÉLÉ" if e["btc_move"]<=BTC_CRASH_THRESH else "idiosync"
    print(f"     {k+1:2}   {e['depth']*100:6.1f}   {e['btc_move']*100:+6.1f}    {e['dur_h']:6.0f}   {typ}")
frac_corr=corr_depth/tot_depth if tot_depth>0 else 0
# also: dollar drawdown weighting (depth * peak equity) since compounding
tot_d=sum(-e["depth"]*e["peak_E"] for e in epis)
corr_d=sum(-e["depth"]*e["peak_E"] for e in epis if e["btc_move"]<=BTC_CRASH_THRESH)
frac_corr_d=corr_d/tot_d if tot_d>0 else 0
# worst-5% book candles: how many coincide with BTC down?
thr=np.percentile(rb,5); worst=rb<=thr
coincide=(rk[worst]<0).mean()
print(f"\n  part du drawdown TOTAL venant d'épisodes corrélés: {frac_corr*100:.0f}% (profondeur) / {frac_corr_d*100:.0f}% ($ pondéré)")
print(f"  pires 5% candles du book -> {coincide*100:.0f}% coïncident avec BTC en baisse")
print(f"  pire épisode: {epis[0]['depth']*100:.1f}% ({'CORRÉLÉ' if epis[0]['btc_move']<=BTC_CRASH_THRESH else 'idiosync'}, BTC {epis[0]['btc_move']*100:+.1f}%)")

# --- threshold sensitivity (pre-empt 'you cherry-picked -3%') ---
print("\n(D) SENSIBILITÉ AU SEUIL 'corrélé' (part $-pondérée du DD venant d'épisodes corrélés)")
for th in (-0.01,-0.02,-0.03,-0.05):
    cd=sum(-e["depth"]*e["peak_E"] for e in epis if e["btc_move"]<=th)
    print(f"    BTC<= {th*100:+.0f}%: {100*cd/tot_d:.0f}% corrélé   (les 2 pires épisodes restent idiosync: BTC {epis[0]['btc_move']*100:+.0f}%, {epis[1]['btc_move']*100:+.0f}%)")
# --- hedge backfire: what a short-BTC overlay would ADD to the worst (idiosync) episodes ---
print("\n(E) CONTRE-FEU: P&L qu'un short-BTC (X% du brut) AJOUTERAIT aux 2 pires drawdowns")
for X in (0.30,0.50):
    print(f"    hedge {X*100:.0f}% brut: épisode1 (BTC {epis[0]['btc_move']*100:+.0f}%) -> {-X*epis[0]['btc_move']*100:+.1f}pp ; "
          f"épisode2 (BTC {epis[1]['btc_move']*100:+.0f}%) -> {-X*epis[1]['btc_move']*100:+.1f}pp  (aggrave)")
n_real_crash=sum(1 for e in epis if e["btc_move"]<=-0.03 and e["depth"]<=-0.05)
print(f"\n  # de vrais crashs corrélés matériels (DD<=-5% ET BTC<=-3%) sur {(last-first)/8.64e7:.0f}j = {n_real_crash}  (puissance quasi-nulle)")

DOMINANT = frac_corr_d>=0.5 or (r2>=0.25 and coincide>=0.6)
print("\n"+"="*72)
print(f"ÉTAPE 0 VERDICT: drawdown corrélé DOMINANT ? -> {'OUI (on continue étape 1)' if DOMINANT else 'NON -> MORT (le hedge de crash ne cible pas la vraie menace)'}")
print("="*72)

json.dump({"beta":float(beta),"corr":float(corr),"r2":float(r2),
           "crash_decile_book":float(rb[crash].mean()),"crash_decile_btc":float(rk[crash].mean()),
           "frac_corr_depth":float(frac_corr),"frac_corr_dollar":float(frac_corr_d),
           "worst5_coincide":float(coincide),"n_episodes":len(epis),
           "worst_episodes":epis[:10],"dominant":bool(DOMINANT),
           "span_days":float((last-first)/8.64e7),"maxDD":float(res["max_dd_pct"]),
           "n_trades":res["n_trades"]},open(f"{OUT}/hedge_etape0.json","w"))
print("saved hedge_etape0.json")
