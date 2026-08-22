"""Ce que la décote de l'arbitre d'entrée a coûté ou rapporté, en dollars.

Pour chaque trade décoté : P&L réel vs P&L au notionnel plein (celui du paper, à
bps identiques). Sépare l'effet sur gagnants et sur perdants — c'est cette
asymétrie qui a motivé le retrait du 2026-08-22.

    python3 -m backtests.ai.haircut_pnl
"""

import sqlite3, datetime as dt
q = ("SELECT symbol,strategy,direction,entry_time,size_usdt,pnl_usdt,net_bps,reason "
     "FROM trades ORDER BY entry_time")
L = sqlite3.connect("alfred/data/bots/live/bot.db").execute(q).fetchall()
P = sqlite3.connect("alfred/data/bots/paper/bot.db").execute(q).fetchall()
ts = lambda s: dt.datetime.fromisoformat(s).timestamp()
pidx = {}
for r in P:
    pidx.setdefault((r[0], r[1], r[2]), []).append(r)

cut, full = [], []
for r in L:
    c = [p for p in pidx.get((r[0], r[1], r[2]), []) if abs(ts(p[3]) - ts(r[3])) < 7200]
    if not c:
        continue
    (cut if r[4] / c[0][4] < 0.95 else full).append((r, c[0]))

print(f"  {'sym':7s} {'st':4s} {'dir':>5s} {'ratio':>6s} {'P&L réel':>9s} "
      f"{'à taille pleine':>15s} {'manque à gagner':>16s}")
tot = 0.0
for r, p in sorted(cut, key=lambda x: x[0][3]):
    k = r[4] / p[4]
    full_pnl = r[5] / k                      # même bps, notionnel plein
    d = full_pnl - r[5]
    tot += d
    print(f"  {r[0]:7s} {r[1]:4s} {r[2]:>5s} {k:>6.3f} {r[5]:>+9.2f} "
          f"{full_pnl:>+15.2f} {d:>+16.2f}")
print(f"\n  {len(cut)} trades décotés · {len(full)} à taille pleine")
print(f"  EFFET NET DE LA DÉCOTE : ${-tot:+.2f}")
print(f"    (négatif = la décote a coûté ; positif = elle a protégé)")
w = [(r, p) for r, p in cut if r[5] > 0]
l = [(r, p) for r, p in cut if r[5] <= 0]
print(f"  dont sur gagnants n={len(w)} : ${-sum(r[5]/(r[4]/p[4])-r[5] for r,p in w):+.2f}")
print(f"       sur perdants n={len(l)} : ${-sum(r[5]/(r[4]/p[4])-r[5] for r,p in l):+.2f}")
