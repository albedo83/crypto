"""Scorecard contrefactuel de l'arbitre IA (SENIOR) — la PREUVE.

Mesure en live si l'arbitrage IA crée ou détruit de la valeur, en comparant le
P&L réellement obtenu à celui qu'auraient donné les RÈGLES SEULES.

Deux régimes :
- **shadow** (le bot entre en pleine taille quoi qu'il arrive) : le trade réel EST
  le résultat règles-seules. Le contrefactuel IA se calcule SANS rejeu :
  veto hypothétique → 0 ; modulation → réel × facteur. delta = IA − règles.
- **act** : modulations → delta = réel − réel/facteur (trivial) ; vetos → pas de
  trade réel → REJEU du trade qui aurait eu lieu via le noyau partagé
  (rules.evaluate_exit), delta = 0 − pnl_rejoué.

Sortie : event AI_SCORECARD (live/bot.db) + console (--report). Disjoncteur :
si ≥ cb_min décisions résolues ET delta cumulé < cb_loss → écrit le drapeau
arbiter_tripped (l'arbitre dégrade en shadow) + alerte Telegram SENIOR.

Usage :
    ./ai_arbiter_scorecard.py            # calcule + logge AI_SCORECARD (+ disjoncteur)
    ./ai_arbiter_scorecard.py --report   # console seulement, pas d'écriture
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "/home/crypto")

import ai_entry_arbiter as aia
from ai_notify import send_telegram
from alfred import rules
from alfred.settings import DEFAULT_PARAMS

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SENIOR_DB = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live", "bot.db")
MARKET_DB = os.path.join(REPO_ROOT, "alfred", "data", "market.db")
COST_BPS = 9.0  # HL RT taker ~ floor (estimation pour le rejeu des vetos)


def _conn(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def load_decisions(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    db = _conn(db_path)
    try:
        rows = db.execute(
            "SELECT ts, symbol, data FROM events WHERE event='ARBITER_DECISION' "
            "ORDER BY ts").fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            continue
        d["_ts"] = r["ts"]
        d["symbol"] = r["symbol"]
        out.append(d)
    return out


def load_closed_trades(db_path: str) -> dict:
    """{(symbol, entry_time[:16]): trade}. Inclut trades_archive (v1.15.0) :
    les trades purgés par un reset restent appariables aux décisions IA."""
    if not os.path.exists(db_path):
        return {}
    db = _conn(db_path)
    sel = ("SELECT symbol, direction, strategy, entry_time, exit_time, "
           "pnl_usdt, size_usdt FROM {} WHERE exit_time IS NOT NULL")
    rows = []
    try:
        for table in ("trades_archive", "trades"):
            try:
                rows += db.execute(sel.format(table)).fetchall()
            except sqlite3.OperationalError:
                pass  # pas d'archive (aucun reset depuis v1.15.0)
    finally:
        db.close()
    out = {}
    for r in rows:
        key = (r["symbol"], (r["entry_time"] or "")[:16])
        out[key] = dict(r)
    return out


def load_candles(symbol: str, after_ts_ms: int, n: int = 24) -> list[dict]:
    if not os.path.exists(MARKET_DB):
        return []
    db = _conn(MARKET_DB)
    try:
        rows = db.execute(
            "SELECT t, o, h, l, c FROM candles WHERE symbol=? AND interval='4h' "
            "AND closed=1 AND t > ? ORDER BY t LIMIT ?",
            (symbol, after_ts_ms, n)).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


def replay_trade(symbol, direction, strategy, entry_price, entry_ts_ms, size,
                 stop_bps, hold_hours, btc_z, disp_24h) -> dict | None:
    """Rejeu d'un trade hypothétique via le noyau partagé (vetos en mode act).
    Retourne {pnl, reason} ou None si pas assez de bougies (non résolu)."""
    if not entry_price or not entry_ts_ms:
        return None
    p = DEFAULT_PARAMS
    after = load_candles(symbol, entry_ts_ms, n=max(2, int(hold_hours // 4) + 4))
    if not after:
        return None
    max_hold = int(hold_hours // 4) or 1
    mfe = mae = mfe_at_h = 0.0
    extended = False
    for i, c in enumerate(after):
        held = i + 1
        best, worst = rules.candle_excursions(direction, entry_price, c["h"], c["l"])
        if best > mfe:
            mfe, mfe_at_h = best, held * 4.0
        mae = min(mae, worst)
        cur_bps = direction * (c["c"] / entry_price - 1) * 1e4
        pv = rules.PosView(
            strategy=strategy, direction=direction, entry_price=entry_price,
            size_usdt=size, stop_bps=stop_bps, mfe_bps=mfe, mae_bps=mae,
            hours_held=held * 4.0, hours_to_timeout=(max_hold - held) * 4.0,
            mfe_at_h=mfe_at_h, extended=extended)
        m = rules.MarketCtx(price=c["c"], btc_z=btc_z, btc_ret_4h_bps=None,
                            disp_24h=disp_24h)
        dec = rules.evaluate_exit(pv, cur_bps, m, p, worst_bps=worst)
        if dec and dec.action == "extend":
            extended = True
            max_hold += int(dec.extend_hours // 4)
            continue
        if dec and dec.action == "exit":
            xp = dec.exit_price if dec.exit_price is not None else c["c"]
            _, _, pnl = rules.compute_trade_pnl(direction, entry_price, xp, size, COST_BPS)
            return {"pnl": pnl, "reason": dec.reason}
        if held >= max_hold:
            _, _, pnl = rules.compute_trade_pnl(direction, entry_price, c["c"], size, COST_BPS)
            return {"pnl": pnl, "reason": "timeout"}
    return None  # pas encore assez de bougies → non résolu


def score() -> dict:
    decisions = load_decisions(SENIOR_DB)
    trades = load_closed_trades(SENIOR_DB)
    rows = []          # décisions résolues
    pending = 0
    for d in decisions:
        sym = d["symbol"]
        et = (d.get("entry_time") or "")[:16]
        mode = d.get("mode", "shadow")
        decision = d.get("decision", "GO")
        hard_veto = bool(d.get("hard_veto"))
        factor = float(d.get("factor", 1.0))
        acted = bool(d.get("acted"))
        tr = trades.get((sym, et))

        rules_pnl = None      # P&L "règles seules"
        ia_pnl = None         # P&L "décision IA"
        if not acted:
            # shadow : le trade réel = règles seules (pleine taille)
            if tr is None:
                pending += 1
                continue
            rules_pnl = tr["pnl_usdt"] or 0.0
            ia_pnl = 0.0 if hard_veto else rules_pnl * factor
        else:
            if hard_veto:
                # act-veto : pas de trade réel → rejeu des règles seules via le
                # noyau partagé, depuis le prix/horodatage de référence loggés.
                rep = replay_trade(
                    sym,
                    1 if d.get("dir") == "LONG" else -1,
                    d.get("strategy"),
                    d.get("ref_price"),
                    d.get("entry_ts_ms"),
                    d.get("rules_size", 0.0),
                    d.get("stop_bps", 0.0),
                    d.get("hold_hours", DEFAULT_PARAMS.hold_hours_default),
                    d.get("btc_z"),
                    None)
                if rep is None:
                    pending += 1   # pas assez de bougies / prix manquant → non résolu
                    continue
                rules_pnl = rep["pnl"]
                ia_pnl = 0.0
            else:
                # act-modulé : trade réel = taille IA (rules×factor)
                if tr is None:
                    pending += 1
                    continue
                ia_pnl = tr["pnl_usdt"] or 0.0
                rules_pnl = ia_pnl / factor if factor else ia_pnl
        rows.append({
            "sym": sym, "strategy": d.get("strategy"), "dir": d.get("dir"),
            "mode": mode, "decision": decision, "hard_veto": hard_veto,
            "factor": factor, "rules_pnl": rules_pnl, "ia_pnl": ia_pnl,
            "delta": ia_pnl - rules_pnl})

    n = len(rows)
    delta_sum = round(sum(r["delta"] for r in rows), 2)
    rules_sum = round(sum(r["rules_pnl"] for r in rows), 2)
    ia_sum = round(sum(r["ia_pnl"] for r in rows), 2)
    vetoes = [r for r in rows if r["hard_veto"]]
    veto_useful = sum(1 for r in vetoes if r["rules_pnl"] < 0)
    return {
        "ts": int(time.time()),
        "n_resolved": n, "n_pending": pending,
        "rules_pnl_sum": rules_sum, "ia_pnl_sum": ia_sum, "delta_sum": delta_sum,
        "n_veto": len(vetoes),
        "veto_useful": veto_useful,
        "veto_useful_pct": round(100 * veto_useful / len(vetoes), 1) if vetoes else None,
        "rows": rows,
    }


def format_scorecard_tg(sc: dict, tripped: bool) -> str:
    """Récap Telegram condensé (canal SENIOR)."""
    d = sc["delta_sum"]
    head = "🎛️ Arbitre IA — bilan quotidien"
    if sc["n_resolved"] == 0:
        return (f"{head}\nAucune décision résolue encore "
                f"({sc['n_pending']} en cours). L'arbitre tourne, le scorecard "
                f"se remplira au fil des trades clos.")
    verdict = "IA ajoute de la valeur ✅" if d > 0 else ("IA neutre" if d == 0 else "IA en retrait ⚠️")
    lines = [head,
             f"Δ IA vs règles: {d:+.2f}$  ({verdict})",
             f"{sc['n_resolved']} décisions résolues ({sc['n_pending']} en cours)",
             f"P&L IA {sc['ia_pnl_sum']:+.2f}$ vs règles-seules {sc['rules_pnl_sum']:+.2f}$"]
    if sc["n_veto"]:
        lines.append(f"Vetos: {sc['n_veto']} dont {sc['veto_useful']} utiles "
                     f"({sc['veto_useful_pct']}%)")
    if tripped:
        lines.append("🛑 DISJONCTEUR déclenché (arbitre en shadow)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="Console seulement, pas d'écriture DB ni disjoncteur")
    ap.add_argument("--telegram", action="store_true",
                    help="Envoie aussi le récap sur Telegram (canal SENIOR)")
    args = ap.parse_args()
    aia  # touch import
    from supervisor import load_env
    load_env()

    sc = score()
    cfg = aia.config()

    print("=== Arbitre IA — scorecard contrefactuel (SENIOR) ===")
    print(f"Décisions résolues: {sc['n_resolved']}  (en cours: {sc['n_pending']})")
    print(f"P&L règles-seules : {sc['rules_pnl_sum']:+.2f}$")
    print(f"P&L décisions IA  : {sc['ia_pnl_sum']:+.2f}$")
    print(f"Δ IA vs règles    : {sc['delta_sum']:+.2f}$  "
          f"({'IA ajoute' if sc['delta_sum']>0 else 'IA détruit' if sc['delta_sum']<0 else 'neutre'})")
    if sc["n_veto"]:
        print(f"Vetos: {sc['n_veto']}  utiles (évité un perdant): "
              f"{sc['veto_useful']} ({sc['veto_useful_pct']}%)")
    by = defaultdict(lambda: [0, 0.0])
    for r in sc["rows"]:
        k = f"{r['strategy']} {r['dir']}"
        by[k][0] += 1
        by[k][1] += r["delta"]
    if by:
        print("Par (stratégie, direction) — n, Δ$:")
        for k, (cnt, dl) in sorted(by.items(), key=lambda kv: kv[1][1]):
            print(f"  {k:<12} n={cnt:<3} Δ={dl:+.2f}")

    if args.report:
        return 0

    # Log AI_SCORECARD (sans les rows détaillées)
    payload = {k: v for k, v in sc.items() if k != "rows"}
    try:
        db = sqlite3.connect(SENIOR_DB, timeout=5)
        db.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                   (sc["ts"], "AI_SCORECARD", None, json.dumps(payload, default=str)))
        db.commit(); db.close()
        print("[scorecard] AI_SCORECARD loggé")
    except Exception as e:
        print(f"[scorecard] log failed: {e}", file=sys.stderr)

    # Disjoncteur : assez de décisions + IA destructrice → trip + alerte
    if (sc["n_resolved"] >= cfg["cb_min"] and sc["delta_sum"] < cfg["cb_loss"]
            and not aia.is_tripped()):
        aia.trip("scorecard_negative",
                 {"delta_sum": sc["delta_sum"], "n": sc["n_resolved"]})
        msg = (f"🛑 Arbitre IA — DISJONCTEUR\n"
               f"Δ IA vs règles {sc['delta_sum']:+.2f}$ sur {sc['n_resolved']} décisions "
               f"(< seuil {cfg['cb_loss']}$). Arbitre dégradé en shadow (n'agit plus). "
               f"Réarmer : supprimer {aia.TRIP_FILE}")
        send_telegram(msg, source="arbiter_circuit_break")
        print("[scorecard] DISJONCTEUR déclenché + drapeau écrit")

    if args.telegram:
        if send_telegram(format_scorecard_tg(sc, aia.is_tripped()),
                         source="arbiter_scorecard"):
            print("[scorecard] récap Telegram envoyé")
    return 0


if __name__ == "__main__":
    sys.exit(main())
