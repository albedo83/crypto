"""Ratio de taille live/paper sur les trades appariés — détecte toute décote
appliquée au live et pas au paper (les deux tournent le même code, même capital,
même reset ; seule la couche IA diffère).

Contrôle hebdomadaire depuis le retrait du 2026-08-22 (docs/ai_haircut_verdict.md
§ 8) : la médiane doit être ≥ 0,98 et la part de ratios < 0,95 sous 20 %.

    python3 -m backtests.ai.size_gap_live_vs_paper
"""

import sqlite3, datetime as dt
q = ("SELECT symbol,strategy,direction,entry_time,size_usdt,pnl_usdt,net_bps "
     "FROM trades ORDER BY entry_time")
L = sqlite3.connect("alfred/data/bots/live/bot.db").execute(q).fetchall()
P = sqlite3.connect("alfred/data/bots/paper/bot.db").execute(q).fetchall()
ts = lambda s: dt.datetime.fromisoformat(s).timestamp()
pidx = {}
for r in P:
    pidx.setdefault((r[0], r[1], r[2]), []).append(r)

print(f"  {'sym':7s} {'st':4s} {'dir':>5s} {'date':16s} {'live$':>8s} {'paper$':>8s} {'ratio':>7s}")
rows, miss = [], 0
for r in L:
    k = (r[0], r[1], r[2])
    cand = [p for p in pidx.get(k, []) if abs(ts(p[3]) - ts(r[3])) < 7200]
    if not cand:
        miss += 1; continue
    p = cand[0]
    rows.append((r, p))
    print(f"  {r[0]:7s} {r[1]:4s} {r[2]:>5s} {r[3][:16]:16s} "
          f"{r[4]:>8.2f} {p[4]:>8.2f} {r[4]/p[4]:>7.3f}")

print(f"\n  appariés {len(rows)} · live sans jumeau paper : {miss} (sur {len(L)})")
if rows:
    rt = [r[4]/p[4] for r, p in rows]
    rt.sort()
    print(f"  ratio taille live/paper — médiane {rt[len(rt)//2]:.3f} · "
          f"min {rt[0]:.3f} · max {rt[-1]:.3f} · sous 0.95 : {sum(1 for x in rt if x<0.95)}/{len(rt)}")
    sl = sum(r[4] for r, p in rows); sp = sum(p[4] for r, p in rows)
    print(f"  notionnel cumulé — live ${sl:.0f} vs paper ${sp:.0f}  ({sl/sp*100:.1f} %)")
