"""Pré-estimation des entrées maker (post-only) sur les entrées réelles d'Alfred.

Pour chaque trade (bot.db) depuis le 2026-07-09 : un post-only posé au PRIX
D'ENTRÉE réel au moment de l'entrée aurait-il été touché dans les N minutes ?
Règle conservatrice : franchissement STRICT par le mark minute (ticks HL) —
LONG rempli si mark < prix, SHORT si mark > prix. Échantillonnage minute →
les extrêmes intra-minute sont ignorés (sous-estime les remplissages).

Scénarios, en $ réels du trade :
  skip     : non rempli → trade non pris ; rempli → +3 bps (taker 4,5 → maker 1,5)
  fallback : non rempli → taker au mark à t+N (glissement adverse payé)
La question décisive : P&L réel des trades NON remplis.

    python3 -m backtests.maker_entry_estimate [--bots paper,live]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime

import numpy as np

MKT = "file:/home/crypto/alfred/data/market.db?mode=ro"
SAVE_BPS = 3.0
NS = (5, 15, 30, 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bots", default="paper,live")
    bots = ap.parse_args().bots.split(",")
    mk = sqlite3.connect(MKT, uri=True)
    for b in bots:
        db = sqlite3.connect(f"file:/home/crypto/alfred/data/bots/{b}/bot.db?mode=ro", uri=True)
        rows = db.execute("SELECT symbol, direction, entry_time, entry_price, size_usdt, pnl_usdt, strategy "
                          "FROM trades WHERE entry_time >= '2026-07-09' ORDER BY entry_time").fetchall()
        recs = []
        for sym, d, et, ep, sz, pnl, strat in rows:
            d = 1 if str(d).upper() in ("LONG", "1") else -1
            t0 = int(datetime.fromisoformat(et).timestamp())
            path = mk.execute("SELECT ts, mark_px FROM ticks WHERE symbol=? AND ts>? AND ts<=? ORDER BY ts",
                              (sym, t0, t0 + max(NS) * 60 + 30)).fetchall()
            if not path or not ep:
                continue
            ts = np.array([p[0] for p in path]); px = np.array([p[1] for p in path])
            recs.append((d, ep, sz, pnl, strat, ts - t0, px))
        print(f"\n===== {b} : {len(recs)} entrées avec ticks — P&L total réel {sum(r[3] for r in recs):+.2f} $ =====")
        print(f"{'N min':>6}{'remplis':>9}{'P&L remplis':>13}{'P&L NON remplis':>17}{'Δ skip':>10}{'Δ fallback':>12}")
        for N in NS:
            fill_p = nofill_p = d_skip = d_fb = 0.0
            nf = 0
            for d, ep, sz, pnl, strat, dt, px in recs:
                w = px[dt <= N * 60]
                filled = len(w) > 0 and (np.any(w < ep) if d == 1 else np.any(w > ep))
                if filled:
                    nf += 1
                    fill_p += pnl
                    d_skip += sz * SAVE_BPS / 1e4
                    d_fb += sz * SAVE_BPS / 1e4
                else:
                    nofill_p += pnl
                    d_skip -= pnl
                    last = px[dt <= N * 60][-1] if np.any(dt <= N * 60) else ep
                    d_fb -= d * (last / ep - 1) * sz      # glissement adverse du repli
            n = len(recs)
            print(f"{N:>6}{nf:>5}/{n:<3}{fill_p:>+13.2f}{nofill_p:>+17.2f}{d_skip:>+10.2f}{d_fb:>+12.2f}")


if __name__ == "__main__":
    main()
