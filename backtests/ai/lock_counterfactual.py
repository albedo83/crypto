"""Contrefactuel des LOCK de l'arbitre de SORTIE, reconstruit sur les bougies 4h.

Mesure grossière (n'applique que le stop catastrophe) : `ai_exit_scorecard.py`
rejoue la chaîne complète et fait autorité. Gardé parce qu'il est indépendant —
deux méthodes qui divergent signalent un défaut de l'une des deux.

    python3 -m backtests.ai.lock_counterfactual
"""

import sqlite3, datetime as dt

HOLD = {"S1":72.0,"S5":48.0,"S8":60.0,"S9":48.0,"S10":24.0}
bot = sqlite3.connect("alfred/data/bots/live/bot.db")
mkt = sqlite3.connect("alfred/data/market.db")

def path(sym, t0_ms, t1_ms):
    return mkt.execute("SELECT t,h,l,c FROM candles WHERE symbol=? AND interval='4h' "
                       "AND t>=? AND t<=? ORDER BY t", (sym, t0_ms, t1_ms)).fetchall()

rows = bot.execute("SELECT symbol,strategy,direction,entry_time,exit_time,entry_price,"
                   "exit_price,size_usdt,gross_bps,net_bps,pnl_usdt,mfe_bps "
                   "FROM trades WHERE reason='manual_stop_set' ORDER BY entry_time").fetchall()

print(f"  {'sym':7s} {'st':4s} {'dir':>5s} {'réel$':>8s} {'coupé à':>8s} "
      f"{'timeout':>8s} {'→ $':>8s} {'Δ$':>8s}  pire chemin")
tot_r = tot_c = 0.0
detail = []
for (sym, st, d, t0, t1, pe, px, sz, gb, nb, pnl, mfe) in rows:
    dirn = 1 if str(d).upper() in ("1", "LONG") else -1
    ts0 = dt.datetime.fromisoformat(t0).timestamp()
    ts1 = dt.datetime.fromisoformat(t1).timestamp()
    nat = ts0 + HOLD.get(st, 72.0) * 3600
    fee = gb - nb                                    # frais+funding réellement payés
    p = path(sym, int(ts1*1000), int(nat*1000) + 4*3600*1000)
    if not p:
        detail.append((sym, st, pnl, None, None, None)); tot_r += pnl; continue
    # dernière bougie <= timeout naturel
    cl = [r for r in p if r[0] <= int(nat*1000)]
    end = (cl[-1][3] if cl else p[0][3])
    g_cf = (end - pe) / pe * 1e4 * dirn
    pnl_cf = sz * (g_cf - fee) / 1e4
    # pire excursion entre la coupe et le timeout
    worst = min(((r[2] if dirn == 1 else -r[1]) - pe*dirn) / pe * 1e4 for r in (cl or p))
    STOP = -750.0 if st == "S8" else -1250.0
    stopped = worst < STOP
    if stopped:
        g_cf = STOP
        pnl_cf = sz * (g_cf - fee) / 1e4
    tot_r += pnl; tot_c += pnl_cf
    detail.append((sym, st, pnl, pnl_cf, worst, g_cf))
    print(f"  {sym:7s} {st:4s} {'LONG' if dirn>0 else 'SHORT':>5s} {pnl:>+8.2f} "
          f"{nb:>7.0f}b {g_cf-fee:>7.0f}b {pnl_cf:>+8.2f} {pnl_cf-pnl:>+8.2f}  {worst:>+7.0f} bps{'  ⟵ STOP' if stopped else ''}")

print()
print(f"  RÉEL (arbitre coupe)      : ${tot_r:+.2f}")
print(f"  CONTREFACTUEL (timeout)   : ${tot_c:+.2f}")
print(f"  VALEUR AJOUTÉE DE L'IA    : ${tot_r-tot_c:+.2f}")
w = [x for x in detail if x[3] is not None]
print(f"  n={len(w)} · l'arbitre a mieux fait sur {sum(1 for x in w if x[2]>x[3])}/{len(w)}")
