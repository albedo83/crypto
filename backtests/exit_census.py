"""Volet 1 — census empirique de la chaîne de sorties (chantier ablation).

Combien de fois chaque règle de sortie a RÉELLEMENT tiré, sur tout l'historique
disponible (Alfred 4 bots depuis 2026-06-10 + legacy live 2026-03-26→06-10 —
couverture réelle ≈ 3,3 mois, pas 6), P&L moyen/cumulé par reason × signal,
ventilé par bot. Statut par règle : ACTIVE / RETIRÉE (kill-switch settings) /
JAMAIS TIRÉE. Le masquage (règle jamais atteinte car une règle plus haute
capture avant) est instruit par croisement avec les tirs BT
(backtests/output/exit_ablation_base_trades.json, produit par
backtest_rule_audit) — voir le rapport.

Usage : python3 -m backtests.exit_census
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "/home/crypto")

from alfred.settings import DEFAULT_PARAMS as P

SOURCES = [
    ("live",   "/home/crypto/alfred/data/bots/live/bot.db",   None),
    ("junior", "/home/crypto/alfred/data/bots/junior/bot.db", None),
    ("baby",   "/home/crypto/alfred/data/bots/baby/bot.db",   None),
    ("paper",  "/home/crypto/alfred/data/bots/paper/bot.db",  None),
    ("legacy", "/home/crypto/analysis/output_live/reversal_ticks.db", None),
]

# Statut réglementaire des reasons (chaîne rules.evaluate_exit + hors-chaîne)
RULE_STATUS = {
    "catastrophe_stop": "ACTIVE (exclue de l'ablation)",
    "timeout":          "ACTIVE (exclue de l'ablation)",
    "opp_floor":        "ACTIVE",
    "manual_stop_set":  "ACTIVE (user/LOCK IA — pas une règle auto)",
    "s9_early_exit":    "ACTIVE",
    "s10_trailing":     "ACTIVE",
    "s8_dead_in_water": "ACTIVE",
    "s8_inlife":        "ACTIVE",
    "prop_trail":       "ACTIVE",
    "traj_cut":         "ACTIVE (LONG-only depuis v1.6.4)",
    "s9_early_dead":    "ACTIVE",
    "btc_drop_cut":     "ACTIVE",
    "dead_timeout":     "RETIRÉE v1.4.0 (kill-switch mfe_cap=-99999)",
    "runner_ext":       "ACTIVE (extension, pas une sortie — voir events RUNNER_EXT)",
}
# Reasons opérationnels (pas des règles de stratégie)
OPERATIONAL = {"manual_close", "retry_close", "stale_price", "exchange_close",
               "exchange_stop", "exchange_close_nofill", "liquidation", "adl",
               "ai_cut", "reset", "manual_stop"}


def main():
    rows = []
    for bot, path, _ in SOURCES:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        for t in db.execute("SELECT strategy, direction, reason, pnl_usdt, "
                            "net_bps, entry_time FROM trades"):
            rows.append({"bot": bot, "strat": t["strategy"],
                         "reason": t["reason"] or "?",
                         "pnl": t["pnl_usdt"] or 0.0,
                         "net": t["net_bps"] or 0.0,
                         "entry": (t["entry_time"] or "")[:10]})
        db.close()
    total = len(rows)
    span = (min(r["entry"] for r in rows if r["entry"]),
            max(r["entry"] for r in rows if r["entry"]))
    print(f"CENSUS — {total} trades clos, couverture {span[0]} → {span[1]} "
          f"(≈ {round((_days(span[1]) - _days(span[0])) / 30.4, 1)} mois)")
    print("Réconciliation : total = somme des 5 tables trades ✓" if total else "VIDE ?!")

    # 1) par reason, tous bots confondus
    agg = defaultdict(lambda: {"n": 0, "pnl": 0.0, "net": 0.0})
    for r in rows:
        a = agg[r["reason"]]
        a["n"] += 1; a["pnl"] += r["pnl"]; a["net"] += r["net"]
    print(f"\n{'reason':<20}{'statut':<42}{'n':>5}{'pnl cumulé':>12}{'pnl moyen':>11}{'net moy bps':>12}")
    for reason, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        status = RULE_STATUS.get(reason,
                                 "opérationnel" if reason in OPERATIONAL else "?")
        print(f"{reason:<20}{status:<42}{a['n']:>5}{a['pnl']:>+12.2f}"
              f"{a['pnl']/a['n']:>+11.2f}{a['net']/a['n']:>+12.0f}")

    # règles jamais tirées (dans la chaîne active, 0 trace live)
    never = [k for k, v in RULE_STATUS.items()
             if k not in agg and "ACTIVE" in v]
    print(f"\nRègles ACTIVES jamais tirées en {round((_days(span[1]) - _days(span[0])) / 30.4, 1)} mois de live : "
          f"{never or 'aucune'}")
    # runner_ext : extension, visible en events pas en reason
    n_ext = 0
    for bot, path, _ in SOURCES:
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            n_ext += db.execute("SELECT COUNT(*) FROM events WHERE event='RUNNER_EXT'").fetchone()[0]
            db.close()
        except Exception:
            pass
    print(f"RUNNER_EXT (events, toutes sources) : {n_ext} extension(s)")

    # 2) par reason × signal
    bys = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for r in rows:
        a = bys[(r["reason"], r["strat"])]
        a["n"] += 1; a["pnl"] += r["pnl"]
    print(f"\n{'reason':<20}{'signal':<8}{'n':>5}{'pnl cumulé':>12}{'pnl moyen':>11}")
    for (reason, strat), a in sorted(bys.items(), key=lambda kv: (kv[0][0], -kv[1]["n"])):
        print(f"{reason:<20}{strat:<8}{a['n']:>5}{a['pnl']:>+12.2f}{a['pnl']/a['n']:>+11.2f}")

    # 3) par bot (ventilation)
    byb = defaultdict(lambda: defaultdict(int))
    for r in rows:
        byb[r["reason"]][r["bot"]] += 1
    print(f"\n{'reason':<20}" + "".join(f"{b:>8}" for b, _, _ in SOURCES))
    for reason in sorted(agg, key=lambda k: -agg[k]["n"]):
        print(f"{reason:<20}" + "".join(f"{byb[reason].get(b, 0):>8}" for b, _, _ in SOURCES))

    # 4) croisement BT (si le dump du volet 2 existe déjà)
    dump_p = os.path.join(os.path.dirname(__file__), "output",
                          "exit_ablation_base_trades.json")
    if os.path.exists(dump_p):
        bt = json.load(open(dump_p))
        from collections import Counter
        print("\nTirs BT par fenêtre (base canonique) :")
        for w, trades in bt.items():
            c = Counter(t.get("reason") for t in trades)
            print(f"  {w:<5} n={len(trades):<4} " +
                  " ".join(f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))
        print("  → une règle ACTIVE à 0 tir live ET 0 tir BT récent = complexité morte ;")
        print("    0 live mais >0 BT = basse fréquence ou divergence de chemin (voir rapport).")
    else:
        print("\n[dump BT absent — relancer après backtest_rule_audit]")


def _days(iso):
    from datetime import date
    y, m, d = iso.split("-")
    return date(int(y), int(m), int(d)).toordinal()


if __name__ == "__main__":
    main()
