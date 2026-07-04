#!/usr/bin/env python3
"""Prophecy scorecard — note les verdicts de position_review à la clôture.

position_review prophétise toutes les 2h (HOLD/WATCH/STOP/TRIM + confiance)
depuis des semaines et personne ne vérifie si ses condamnés meurent. Ce cron
apparie chaque verdict à l'issue réelle de la position (trades) et mesure :
matrice verdict × issue, précision par verdict, Brier score, calibration.

Sémantique de notation (documentée, fixe) :
  - STOP / TRIM  → prédit que la position finit PERDANTE (pnl_usdt < 0).
  - HOLD         → prédit qu'elle finit GAGNANTE (pnl ≥ 0).
  - WATCH        → abstention (ni compté juste ni faux — reporté à part).
  - P(perte) = confidence si STOP/TRIM, 1 − confidence si HOLD → Brier.
  - Un verdict est apparié au trade dont [entry_time, exit_time] contient
    l'horodatage de la revue (même symbole) ; dernier verdict AVANT clôture
    = celui noté (les intermédiaires : reportés en « révisions »).

Observation PURE — deux issues rentables : calibré → input gratuit de
l'arbitre de sortie ; pas calibré → on débranche 12 appels/jour d'astrologie.

ÉPISTÉMOLOGIE (revue 2026-07-04) :
  - Les verdicts antérieurs au 2026-07-04 sont RÉTRO-NOTÉS (extraits des
    events POSITION_REVIEW historiques, mêmes champs advice/confidence).
    Indicatif, pas probant — le forward PROPRE commence au FORWARD_START.
    Le rapport ventile rétro / forward.
  - La précision d'un verdict ne vaut que contre le TAUX DE BASE (imprimé) :
    si 60 % des positions perdent de toute façon, un WATCH à 78 % murmure.
    Le vrai juge est le Brier vs climatologie.
  - ⚠️ GOODHART (phase 2) : le jour où « doomed » nourrit l'arbitre et que le
    CUT exécute les condamnés, la prophétie devient auto-réalisatrice — le
    prophète tue ses patients et son score s'améliore. À la promotion :
    flag `verdict_consumed` dans les events, et ne noter en forward QUE les
    positions où l'arbitre n'a PAS agi.

Usage : python3 prophecy_scorecard.py [--report]   (cron quotidien)
Event : PROPHECY_SCORECARD dans live/bot.db.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "alfred", "data", "bots", "live", "bot.db")

FORWARD_START_TS = 1783200000   # 2026-07-04 ~16h UTC : début du forward propre
PREDICT_LOSS = {"STOP", "TRIM"}
PREDICT_WIN = {"HOLD"}


def parse_ts(iso):
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="console seule")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    # verdicts : (ts, symbol, advice, confidence, prompt_hash, model)
    verdicts = []
    for r in db.execute("SELECT ts, data FROM events WHERE event='POSITION_REVIEW' ORDER BY ts"):
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        for p in d.get("positions") or []:
            if p.get("advice"):
                verdicts.append({
                    "ts": r["ts"], "symbol": p["symbol"],
                    "advice": str(p["advice"]).upper(),
                    "conf": float(p.get("confidence") or 0.5),
                    "hash": d.get("prompt_hash"), "model": d.get("_model") or d.get("model")})

    # trades clos : fenêtres d'appariement
    trades = [dict(r) for r in db.execute(
        "SELECT symbol, entry_time, exit_time, pnl_usdt, reason FROM trades "
        "WHERE exit_time IS NOT NULL")]
    for t in trades:
        t["t0"], t["t1"] = parse_ts(t["entry_time"]), parse_ts(t["exit_time"])

    # dernier verdict par (trade) ; les précédents = révisions
    scored, revisions = [], 0
    for t in trades:
        vs = [v for v in verdicts
              if v["symbol"] == t["symbol"] and t["t0"] <= v["ts"] <= t["t1"]]
        if not vs:
            continue
        vs.sort(key=lambda v: v["ts"])
        revisions += sum(1 for a, b in zip(vs, vs[1:]) if a["advice"] != b["advice"])
        v = vs[-1]
        loss = (t["pnl_usdt"] or 0) < 0
        if v["advice"] in PREDICT_LOSS:
            correct, p_loss, counted = loss, v["conf"], True
        elif v["advice"] in PREDICT_WIN:
            correct, p_loss, counted = (not loss), 1 - v["conf"], True
        else:   # WATCH
            correct, p_loss, counted = None, None, False
        scored.append({"symbol": t["symbol"], "advice": v["advice"],
                       "conf": v["conf"], "loss": loss, "correct": correct,
                       "p_loss": p_loss, "counted": counted,
                       "pnl": t["pnl_usdt"], "reason": t["reason"]})

    n_open_verdicts = len({(v["symbol"], v["ts"]) for v in verdicts})
    n_retro = sum(1 for v in verdicts if v["ts"] < FORWARD_START_TS)
    base_loss_all = (sum(1 for s_ in scored if s_["loss"]) / len(scored)) if scored else 0
    print(f"=== PROPHECY SCORECARD — position_review noté à la clôture ===")
    print(f"{len(verdicts)} verdicts émis ({n_retro} RÉTRO-notés pré-2026-07-04, "
          f"indicatifs ; le forward propre = le reste), {len(scored)} appariés, "
          f"{revisions} révisions d'avis en cours de vie")
    print(f"Taux de base : {base_loss_all*100:.0f}% des positions notées finissent "
          f"perdantes — toute précision se juge contre CE chiffre.")

    # matrice verdict × issue
    mat = defaultdict(lambda: [0, 0])   # advice → [n_loss, n_win]
    for s in scored:
        mat[s["advice"]][0 if s["loss"] else 1] += 1
    print(f"\n{'verdict':<8}{'n':>4}{'→ perte':>9}{'→ gain':>8}{'précision':>11}")
    for adv, (nl, nw) in sorted(mat.items()):
        n = nl + nw
        if adv in PREDICT_LOSS:
            acc = nl / n * 100 if n else 0
        elif adv in PREDICT_WIN:
            acc = nw / n * 100 if n else 0
        else:
            acc = float("nan")
        acc_s = f"{acc:.0f}%" if acc == acc else "(abstient)"
        print(f"{adv:<8}{n:>4}{nl:>9}{nw:>8}{acc_s:>11}")

    counted = [s for s in scored if s["counted"]]
    if counted:
        brier = sum((s["p_loss"] - (1.0 if s["loss"] else 0.0)) ** 2
                    for s in counted) / len(counted)
        acc = sum(1 for s in counted if s["correct"]) / len(counted)
        base_loss = sum(1 for s in scored if s["loss"]) / len(scored)
        brier_base = base_loss * (1 - base_loss)   # prédicteur constant
        print(f"\nPrécision globale (hors WATCH) : {acc*100:.0f}% (n={len(counted)})")
        print(f"Brier : {brier:.3f} vs base (taux constant) {brier_base:.3f} "
              f"→ {'BAT la base' if brier < brier_base else 'ne bat PAS la base'}")
        # calibration grossière par tercile de confiance
        buckets = defaultdict(lambda: [0, 0])
        for s in counted:
            b = "haute ≥0.7" if s["conf"] >= 0.7 else ("basse <0.55" if s["conf"] < 0.55 else "moy")
            buckets[b][0] += 1
            buckets[b][1] += 1 if s["correct"] else 0
        print("Calibration (confiance → précision) :")
        for b, (n, ok) in sorted(buckets.items()):
            print(f"  {b:<12} n={n:<4} précision {ok/n*100:.0f}%")

    if args.report or not counted:
        return 0
    payload = {"ts": int(time.time()), "n_verdicts": len(verdicts),
               "n_scored": len(scored), "n_counted": len(counted),
               "revisions": revisions,
               "accuracy": round(sum(1 for s in counted if s["correct"]) / len(counted), 3),
               "brier": round(brier, 4), "brier_base": round(brier_base, 4),
               "matrix": {k: v for k, v in mat.items()}}
    try:
        wdb = sqlite3.connect(DB, timeout=5)
        wdb.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                    (payload["ts"], "PROPHECY_SCORECARD", None,
                     json.dumps(payload, default=str)))
        wdb.commit(); wdb.close()
        print("[prophecy] PROPHECY_SCORECARD loggé")
    except Exception as e:
        print(f"[prophecy] log failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
