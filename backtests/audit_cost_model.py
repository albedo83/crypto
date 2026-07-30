"""AUDIT DU MODÈLE DE COÛTS — l'« assumé » tient-il sur les fills réels ?

Motivation (2026-07-30) : la décision S5 compare un stack à 637 trades à un stack
qui en a ~100-150 de moins. Une erreur du modèle de coûts **par trade** ne biaise
pas le P&L uniformément — elle biaise **contre le signal le plus actif**, donc
exactement le verdict qu'on s'apprête à rendre. Et les marges sont fines : le
retrait se jouait à +$148 par fenêtre, le sizing à −$341.

Le backtest facture par trade (aller-retour) :
    TAKER 9 bps + BACKTEST_SLIPPAGE_BPS 4 bps + intégrale de funding horaire
Le bot facture :
    TAKER 9 bps + funding plat 1 bps, remplacé à la clôture par le funding RÉEL
    (le slippage est déjà dans l'avgPx des fills)

Les 4 bps de slippage du backtest sont donc une MODÉLISATION de ce que le bot
subit implicitement. Ce script la confronte aux fills réels de SENIOR depuis le
reset du 2026-07-09 :

  slippage réel = écart entre le prix de fill et le prix que le backtest aurait
  booké (la clôture de la bougie 4h de référence), compté ADVERSE à l'entrée
  comme à la sortie.

Lecture :
  écart sub-bps          → « assumé » devient « vérifié », on n'en reparle plus
  écart de plusieurs bps → à corriger AVANT les re-runs, pas après

Lecture seule. Usage : python3 -m backtests.audit_cost_model
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics as st
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOT_DB = "/home/crypto/alfred/data/bots/live/bot.db"
MARKET_DB = "/home/crypto/alfred/data/market.db"
RESET = "2026-07-09"
TAKER_BPS = 9.0
BT_SLIPPAGE_BPS = 4.0      # backtests/backtest_rolling.py:249
BOT_FLAT_FUNDING_BPS = 1.0


def _mark_at(cur, symbol, ts_sec, tol=180):
    """Mark price au tick le plus proche de ts_sec (±tol s).

    C'est LA bonne référence pour mesurer une qualité d'EXÉCUTION : le prix que
    le bot voyait au moment d'envoyer l'ordre. La méthode historique
    (`measure_live_slippage.py`) comparait à la clôture de la bougie 4h en
    cours — un prix POSTÉRIEUR à la décision : elle mesure donc surtout la
    dérive intra-bougie (écart-type ~258 bps), ce qui exige des centaines de
    trades pour que le bruit s'annule. À n=31 elle n'a aucune puissance
    (IC95 mesuré : [−47, +135] bps).
    """
    r = cur.execute(
        "SELECT ts, mark_px FROM ticks WHERE symbol=? AND ts BETWEEN ? AND ? "
        "ORDER BY ABS(ts-?) LIMIT 1",
        (symbol, ts_sec - tol, ts_sec + tol, ts_sec)).fetchone()
    return r[1] if r and r[1] and r[1] > 0 else None


def main() -> int:
    tr = sqlite3.connect(BOT_DB)
    mk = sqlite3.connect(MARKET_DB).cursor()
    rows = list(tr.execute(
        "SELECT symbol,strategy,direction,entry_time,exit_time,entry_price,"
        "exit_price,size_usdt,gross_bps,net_bps,pnl_usdt,funding_usdt,reason "
        "FROM trades WHERE entry_time>=? AND exit_time IS NOT NULL "
        "ORDER BY entry_time", (RESET,)))
    print(f"SENIOR, {len(rows)} trades clos depuis le reset {RESET}\n")

    recs = []
    for (sym, strat, d, et, xt, epx, xpx, size, gb, nb, pnl, fund, reason) in rows:
        di = 1 if d == "LONG" else -1
        e_s = int(datetime.fromisoformat(et).timestamp())
        x_s = int(datetime.fromisoformat(xt).timestamp())
        e_ref = _mark_at(mk, sym, e_s)
        x_ref = _mark_at(mk, sym, x_s)
        if not e_ref or not x_ref or epx <= 0 or xpx <= 0:
            continue
        # ADVERSE > 0 : on a payé plus cher que la référence à l'achat,
        # ou vendu moins cher qu'elle à la vente.
        slip_in = di * (epx / e_ref - 1) * 1e4
        slip_out = -di * (xpx / x_ref - 1) * 1e4
        fund_bps = (fund or 0.0) / size * 1e4 if size else 0.0
        recs.append({"sym": sym, "strat": strat, "dir": d, "size": size,
                     "slip_in": slip_in, "slip_out": slip_out,
                     "slip_rt": slip_in + slip_out,
                     "fund_bps": fund_bps, "reason": reason,
                     "cost_ledger_bps": (gb - nb)})

    if not recs:
        print("Aucun trade exploitable.")
        return 1

    rt = [r["slip_rt"] for r in recs]
    n = len(rt)
    mean, med = st.mean(rt), st.median(rt)
    sd = st.stdev(rt) if n > 1 else 0.0
    se = sd / n ** 0.5 if n else 0.0

    print("=== SLIPPAGE D'EXÉCUTION aller-retour (adverse > 0), vs mark à l'ordre")
    print(f"  n={n}   moyenne {mean:+.2f} bps   médiane {med:+.2f}   "
          f"écart-type {sd:.1f}")
    print(f"  IC95 de la moyenne : [{mean-1.96*se:+.2f} , {mean+1.96*se:+.2f}] bps")
    print(f"  entrée seule  : moy {st.mean(r['slip_in'] for r in recs):+.2f} bps")
    print(f"  sortie seule  : moy {st.mean(r['slip_out'] for r in recs):+.2f} bps")
    print(f"\n  modèle backtest : {BT_SLIPPAGE_BPS:.1f} bps  →  "
          f"écart modèle − réel = {BT_SLIPPAGE_BPS - mean:+.2f} bps par trade")

    print("\n=== par stratégie (le biais dépend de l'activité)")
    print(f"  {'strat':6s}{'n':>5s}{'slip RT moy':>14s}{'funding moy':>14s}")
    by = {}
    for r in recs:
        by.setdefault(r["strat"], []).append(r)
    for s in sorted(by, key=lambda k: -len(by[k])):
        v = by[s]
        print(f"  {s:6s}{len(v):>5d}{st.mean(x['slip_rt'] for x in v):>13.2f} "
              f"{st.mean(x['fund_bps'] for x in v):>13.2f}")

    print("\n=== FUNDING réel encaissé/payé (bps du notionnel, >0 = payé)")
    fb = [r["fund_bps"] for r in recs]
    print(f"  moyenne {st.mean(fb):+.2f} bps   médiane {st.median(fb):+.2f}   "
          f"min {min(fb):+.1f}   max {max(fb):+.1f}")
    print(f"  modèle plat du bot : {BOT_FLAT_FUNDING_BPS:.1f} bps "
          f"(remplacé par le réel à la clôture — le backtest, lui, intègre "
          f"les taux horaires)")

    print("\n=== IMPACT sur la décision S5")
    ecart = BT_SLIPPAGE_BPS - mean
    for delta_n in (100, 150):
        print(f"  un stack de {delta_n} trades de plus est pénalisé de "
              f"{abs(ecart)*delta_n/1e4*100:.2f} % de notionnel cumulé "
              f"({'sur' if ecart > 0 else 'sous'}-facturé par le backtest)")

    lo, hi = mean - 1.96 * se, mean + 1.96 * se
    if lo <= BT_SLIPPAGE_BPS <= hi:
        verdict = (
            f"NON RÉFUTÉ — le modèle ({BT_SLIPPAGE_BPS:.1f} bps) est DANS l'IC95 "
            f"[{lo:+.2f}, {hi:+.2f}]. L'estimation ponctuelle ({mean:+.2f}) est "
            f"plus haute, donc le backtest sous-facture probablement et FAVORISE "
            f"les configurations qui tradent le plus — mais re-caler sur n={n} "
            f"serait du refit, et refit dans le sens de la conclusion attendue.\n"
            f"    → traiter en SENSIBILITÉ : rejouer les études décisionnelles à "
            f"{BT_SLIPPAGE_BPS:.0f} ET à {mean:.0f} bps. Verdict identique aux "
            f"deux = l'incertitude de coût n'est pas décisive. Verdict qui "
            f"bascule = le dire, ne pas choisir.")
    else:
        verdict = (
            f"RÉFUTÉ — le modèle ({BT_SLIPPAGE_BPS:.1f} bps) est HORS de l'IC95 "
            f"[{lo:+.2f}, {hi:+.2f}]. À corriger avant toute étude décisionnelle.")
    print(f"\n>>> {verdict}")

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "analysis", "output", "cost_model_audit.json")
    with open(out, "w") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(),
                   "n": n, "slip_rt_mean": mean, "slip_rt_median": med,
                   "slip_rt_sd": sd, "bt_model_bps": BT_SLIPPAGE_BPS,
                   "ecart_model_minus_real": ecart,
                   "funding_mean_bps": st.mean(fb),
                   "trades": recs}, f, indent=1, default=str)
    print(f"Dump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
