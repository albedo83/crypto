"""Purge des events opérationnels antérieurs à un reset clean-slate.

Après un reset (capital=equity, compteurs zéro, historique de trades effacé —
cf. api_reset + DCA), la table `events` conserve encore les logs OPÉRATIONNELS
d'avant le reset qui encombrent la timeline du dashboard. Ce tool les supprime
(SKIP, OPEN/CLOSE, S9F_OBS, HARD_STOP_*, WR_ALERT, EQUITY_BRAKE, LOCK_FLOOR…)
tout en CONSERVANT tous les events IA (scorecards, arbitres entrée/sortie,
revues, coût, prophecy, verdicts) — c'est le track record des scores.

Borne temporelle = `reset_ts` lu dans `bt_reset_anchor.json` du bot (posé au
clean-slate). Idempotent : un re-run est un no-op (les logs pré-reset sont déjà
partis). Read-only sur le bot vivant sauf le DELETE ciblé (verrou fichier SQLite
+ busy_timeout ; à lancer hors pic).

Appliqué le 2026-07-09 sur SENIOR (−1312) et PAPER (−1231) après le reset à
$518.34. Historique : voir CHANGELOG / mémoire project_live_capital.

Usage:
    python3 -m alfred.tools.purge_pre_reset_events --bot live [--dry-run]
    python3 -m alfred.tools.purge_pre_reset_events --bot paper
"""

import argparse
import json
import os
import sqlite3
import sys

# Events IA / scores à CONSERVER quel que soit leur âge (pré ou post reset).
KEEP = {
    "AI_SCORECARD", "AI_EXIT_SCORECARD", "POSITION_REVIEW", "POSITION_REVIEW_ERROR",
    "AI_TG", "ARBITER_DECISION", "ARBITER_EXIT_DECISION", "ARBITER_FAILOPEN",
    "ARBITER_EXIT_FAILOPEN", "AI_COST", "PROPHECY_SCORECARD", "STRATEGY_REVIEW",
    "OVERFIT_MONITOR", "ENTRY_VERDICT", "ATTENTION_TRIGGER", "BT_DIVERGENCE",
}

BOT_DIR = "/home/crypto/alfred/data/bots"


def reset_ts_for(bot: str) -> float:
    anchor = os.path.join(BOT_DIR, bot, "bt_reset_anchor.json")
    if not os.path.exists(anchor):
        sys.exit(f"Pas d'ancre de reset pour {bot} ({anchor}) — rien à borner.")
    return float(json.load(open(anchor))["reset_ts"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bot", required=True, help="id du bot (live, paper, …)")
    ap.add_argument("--dry-run", action="store_true", help="montre sans supprimer")
    args = ap.parse_args()

    reset = reset_ts_for(args.bot)
    db_path = os.path.join(BOT_DIR, args.bot, "bot.db")
    db = sqlite3.connect(db_path, timeout=30)
    ph = ",".join("?" * len(KEEP))

    rows = db.execute(
        f"SELECT event, COUNT(*) FROM events WHERE ts < ? AND event NOT IN ({ph}) "
        f"GROUP BY event ORDER BY COUNT(*) DESC", (reset, *KEEP)).fetchall()
    n = sum(c for _, c in rows)
    print(f"[{args.bot}] logs opérationnels pré-reset (ts < {reset:.0f}) : {n}")
    for e, c in rows:
        print(f"   {e:24s} {c}")

    if args.dry_run:
        print("   (dry-run : rien supprimé)")
        return
    if n == 0:
        print("   déjà propre, no-op.")
        return

    cur = db.execute(
        f"DELETE FROM events WHERE ts < ? AND event NOT IN ({ph})", (reset, *KEEP))
    db.commit()
    kept = db.execute(f"SELECT COUNT(*) FROM events WHERE event IN ({ph})", (*KEEP,)).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"   ✓ supprimés {cur.rowcount} · events IA conservés {kept} · total restant {total}")
    db.close()


if __name__ == "__main__":
    main()
