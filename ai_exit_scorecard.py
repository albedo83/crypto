"""Scorecard contrefactuel de l'arbitre IA de SORTIE (SENIOR) — la PREUVE.

Mesure si les interventions IA sur positions ouvertes (CUT / LOCK) créent ou
détruisent de la valeur vs la gestion RÈGLES-SEULES. Convention : delta = ia − rules
(>0 = l'IA ajoute). Contrefactuel par rejeu du noyau partagé `rules.evaluate_exit`.

- **CUT shadow** (position NON coupée → trade règles réel) :
  ia = PnL si coupé à la décision (`net_pnl` loggé) ; rules = PnL réel du trade.
- **CUT act** (position coupée, reason=ai_exit) :
  ia = PnL réel de la coupe ; rules = rejeu pures-règles depuis l'entrée (sans coupe).
- **LOCK act** (stop posé) :
  ia = PnL réel (avec stop) ; rules = rejeu sans stop.
- **LOCK shadow** (stop NON posé) :
  ia = rejeu AVEC le stop suggéré ; rules = PnL réel (sans stop).

Sortie : event AI_EXIT_SCORECARD + console (--report). Disjoncteur : ≥ cb_min
résolues ET delta_sum < cb_loss → drapeau exit_arbiter_tripped + alerte Telegram.

Usage :
    ./ai_exit_scorecard.py            # calcule + logge (+ disjoncteur)
    ./ai_exit_scorecard.py --report   # console seulement, pas d'écriture
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

import ai_exit_arbiter as aix
from ai_notify import send_telegram
from alfred import rules
from alfred.settings import DEFAULT_PARAMS

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SENIOR_DB = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live", "bot.db")
MARKET_DB = os.path.join(REPO_ROOT, "alfred", "data", "market.db")
COST_BPS = 9.0


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
            "SELECT ts, symbol, data FROM events WHERE event='ARBITER_EXIT_DECISION' "
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
           "pnl_usdt, size_usdt, entry_price FROM {} WHERE exit_time IS NOT NULL")
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


def load_btc_ret_by_t() -> dict:
    """{t_ms: ret_4h_bps} du BTC (open→close de chaque bougie 4h) — reconstruit
    btc_ret_4h_bps dans le rejeu, sinon btc_drop_cut ne peut jamais firer (M3)."""
    if not os.path.exists(MARKET_DB):
        return {}
    db = _conn(MARKET_DB)
    try:
        rows = db.execute(
            "SELECT t, o, c FROM candles WHERE symbol='BTC' AND interval='4h' "
            "AND closed=1").fetchall()
    finally:
        db.close()
    return {r["t"]: (r["c"] / r["o"] - 1) * 1e4 for r in rows if r["o"]}


def replay_rules(symbol, direction, strategy, entry_price, entry_ts_ms, size,
                 stop_bps, hold_hours, btc_z, manual_stop_usdt=None,
                 btc_ret_map=None) -> dict | None:
    """Rejeu pures-règles depuis l'entrée via `rules.evaluate_exit`. `hold_hours`
    doit être le hold CIBLE (pas l'âge à la décision — sinon le rejeu se tronque).
    Si `manual_stop_usdt` est fourni, le rejeu l'inclut (contrefactuel LOCK).
    `btc_ret_map` {t: ret_4h_bps} permet à btc_drop_cut de firer dans la baseline.
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
            mfe_at_h=mfe_at_h, extended=extended,
            manual_stop_usdt=manual_stop_usdt)
        m = rules.MarketCtx(
            price=c["c"], btc_z=btc_z,
            btc_ret_4h_bps=(btc_ret_map.get(c["t"]) if btc_ret_map else None),
            disp_24h=None)
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
    return None


def _iso16(entry_ts_ms) -> str:
    try:
        return datetime.fromtimestamp(int(entry_ts_ms) / 1000,
                                      tz=timezone.utc).isoformat()[:16]
    except Exception:
        return ""


def _dedup_decisions(decisions: list[dict]) -> list[dict]:
    """H2 : une décision représentative par (symbole, position, action) — la
    dernière ACTÉE si l'IA a agi, sinon la première (intention la plus précoce).
    Évite de compter N fois la même position (throttle horaire → N décisions)."""
    groups: dict = {}
    for d in decisions:
        if d.get("action") not in ("CUT", "LOCK"):
            continue
        k = (d["symbol"], _iso16(d.get("entry_ts_ms")), d.get("action"))
        if k not in groups:
            groups[k] = d
        elif bool(d.get("acted")):
            groups[k] = d   # une actée écrase → garde la dernière actée
    return list(groups.values())


def score() -> dict:
    trades = load_closed_trades(SENIOR_DB)
    btc_ret_map = load_btc_ret_by_t()                       # M3
    decisions = _dedup_decisions(load_decisions(SENIOR_DB))  # H2
    rows, pending = [], 0

    def _hold(d):   # H1 : hold CIBLE (pas l'âge à la décision)
        return (d.get("target_hold_h") or d.get("hold_hours")
                or DEFAULT_PARAMS.hold_hours_default)

    for d in decisions:
        sym = d["symbol"]
        action = d.get("action")
        acted = bool(d.get("acted"))
        tr = trades.get((sym, _iso16(d.get("entry_ts_ms"))))
        direction = 1 if d.get("dir") == "LONG" else -1
        ia_pnl = rules_pnl = None

        if action == "CUT":
            if not acted:
                # shadow : position non coupée → trade règles réel
                if tr is None:
                    pending += 1; continue
                rules_pnl = tr["pnl_usdt"] or 0.0
                ia_pnl = float(d.get("net_pnl", 0.0))   # PnL si coupé à la décision
            else:
                # act : coupée (reason ai_exit) → ia = réel ; rules = rejeu sans coupe
                if tr is None:
                    pending += 1; continue
                rep = replay_rules(sym, direction, d.get("strategy"),
                                   tr.get("entry_price"), d.get("entry_ts_ms"),
                                   tr.get("size_usdt", 0.0), d.get("stop_bps", 0.0),
                                   _hold(d), d.get("btc_z"), btc_ret_map=btc_ret_map)
                if rep is None:
                    pending += 1; continue
                ia_pnl = tr["pnl_usdt"] or 0.0
                rules_pnl = rep["pnl"]
        else:  # LOCK
            if tr is None:
                pending += 1; continue
            if acted:
                # stop posé → ia = réel (avec stop) ; rules = rejeu sans stop
                rep = replay_rules(sym, direction, d.get("strategy"),
                                   tr.get("entry_price"), d.get("entry_ts_ms"),
                                   tr.get("size_usdt", 0.0), d.get("stop_bps", 0.0),
                                   _hold(d), d.get("btc_z"), btc_ret_map=btc_ret_map)
                if rep is None:
                    pending += 1; continue
                ia_pnl = tr["pnl_usdt"] or 0.0
                rules_pnl = rep["pnl"]
            else:
                # M1 : ne scorer un LOCK-shadow que s'il était VALIDE mais
                # trip-suppressed (note="ok"), pas rejeté par la validation.
                if d.get("note") != "ok":
                    continue
                rep = replay_rules(sym, direction, d.get("strategy"),
                                   tr.get("entry_price"), d.get("entry_ts_ms"),
                                   tr.get("size_usdt", 0.0), d.get("stop_bps", 0.0),
                                   _hold(d), d.get("btc_z"),
                                   manual_stop_usdt=d.get("stop_usdt"),
                                   btc_ret_map=btc_ret_map)
                if rep is None:
                    pending += 1; continue
                ia_pnl = rep["pnl"]
                rules_pnl = tr["pnl_usdt"] or 0.0

        rows.append({
            "sym": sym, "strategy": d.get("strategy"), "dir": d.get("dir"),
            "action": action, "acted": acted,
            "rules_pnl": rules_pnl, "ia_pnl": ia_pnl, "delta": ia_pnl - rules_pnl})

    n = len(rows)
    delta_sum = round(sum(r["delta"] for r in rows), 2)
    cuts = [r for r in rows if r["action"] == "CUT"]
    locks = [r for r in rows if r["action"] == "LOCK"]
    return {
        "ts": int(time.time()),
        "n_resolved": n, "n_pending": pending,
        "rules_pnl_sum": round(sum(r["rules_pnl"] for r in rows), 2),
        "ia_pnl_sum": round(sum(r["ia_pnl"] for r in rows), 2),
        "delta_sum": delta_sum,
        "n_cut": len(cuts), "cut_delta": round(sum(r["delta"] for r in cuts), 2),
        "n_lock": len(locks), "lock_delta": round(sum(r["delta"] for r in locks), 2),
        "rows": rows,
    }


def format_tg(sc: dict, tripped: bool) -> str:
    if sc["n_resolved"] == 0:
        return (f"🧠 IA — Arbitre de SORTIE\nAucune décision résolue "
                f"({sc['n_pending']} en cours).")
    d = sc["delta_sum"]
    verdict = "ajoute ✅" if d > 0 else ("neutre" if d == 0 else "en retrait ⚠️")
    lines = ["🧠 IA — Arbitre de SORTIE — bilan",
             f"Δ IA vs règles: {d:+.2f}$ ({verdict})",
             f"{sc['n_resolved']} résolues ({sc['n_pending']} en cours)",
             f"CUT n={sc['n_cut']} Δ{sc['cut_delta']:+.2f}$ | "
             f"LOCK n={sc['n_lock']} Δ{sc['lock_delta']:+.2f}$"]
    if tripped:
        lines.append("🛑 DISJONCTEUR déclenché (arbitre sortie en observation)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="Console seulement, pas d'écriture ni disjoncteur")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    from supervisor import load_env
    load_env()

    sc = score()
    cfg = aix.config()

    print("=== Arbitre IA de SORTIE — scorecard contrefactuel (SENIOR) ===")
    print(f"Décisions résolues: {sc['n_resolved']}  (en cours: {sc['n_pending']})")
    print(f"P&L règles-seules : {sc['rules_pnl_sum']:+.2f}$")
    print(f"P&L décisions IA  : {sc['ia_pnl_sum']:+.2f}$")
    print(f"Δ IA vs règles    : {sc['delta_sum']:+.2f}$")
    print(f"  CUT  n={sc['n_cut']:<3} Δ={sc['cut_delta']:+.2f}$")
    print(f"  LOCK n={sc['n_lock']:<3} Δ={sc['lock_delta']:+.2f}$")
    by = defaultdict(lambda: [0, 0.0])
    for r in sc["rows"]:
        k = f"{r['action']} {r['strategy']} {r['dir']}"
        by[k][0] += 1; by[k][1] += r["delta"]
    if by:
        print("Détail (action stratégie dir) — n, Δ$:")
        for k, (cnt, dl) in sorted(by.items(), key=lambda kv: kv[1][1]):
            print(f"  {k:<18} n={cnt:<3} Δ={dl:+.2f}")

    if args.report:
        return 0

    payload = {k: v for k, v in sc.items() if k != "rows"}
    try:
        db = sqlite3.connect(SENIOR_DB, timeout=5)
        db.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                   (sc["ts"], "AI_EXIT_SCORECARD", None, json.dumps(payload, default=str)))
        db.commit(); db.close()
        print("[exit-scorecard] AI_EXIT_SCORECARD loggé")
    except Exception as e:
        print(f"[exit-scorecard] log failed: {e}", file=sys.stderr)

    if (sc["n_resolved"] >= cfg["cb_min"] and sc["delta_sum"] < cfg["cb_loss"]
            and not aix.is_tripped()):
        aix.trip("scorecard_negative",
                 {"delta_sum": sc["delta_sum"], "n": sc["n_resolved"]})
        msg = (f"🧠 IA — Arbitre SORTIE DISJONCTEUR\n"
               f"Δ {sc['delta_sum']:+.2f}$ sur {sc['n_resolved']} décisions "
               f"(< seuil {cfg['cb_loss']}$). Arbitre de sortie en observation "
               f"(CUT+LOCK n'agissent plus). Réarmer : supprimer {aix.TRIP_FILE}")
        send_telegram(msg, source="exit_arbiter_circuit_break")
        print("[exit-scorecard] DISJONCTEUR déclenché + drapeau écrit")

    # Disjoncteur SPÉCIFIQUE au CUT (revue 2026-07-04) : le CUT est passé en
    # act sur une preuve shadow n=1 — le breaker combiné (cb_min=20 sur
    # delta_sum LOCK+CUT) laisse des LOCKs positifs MASQUER un flux de CUTs
    # destructeurs. Gate dédiée, basse et précoce : les CUTs doivent prouver
    # leur valeur en marchant, pas en s'abritant derrière les LOCKs.
    cut_cb_min = int(os.environ.get("AI_EXIT_CUT_CB_MIN", "10"))
    cut_cb_loss = float(os.environ.get("AI_EXIT_CUT_CB_LOSS", "-15"))
    if (sc["n_cut"] >= cut_cb_min and sc["cut_delta"] < cut_cb_loss
            and not aix.is_tripped()):
        aix.trip("cut_scorecard_negative",
                 {"cut_delta": sc["cut_delta"], "n_cut": sc["n_cut"]})
        msg = (f"🧠 IA — DISJONCTEUR CUT\n"
               f"Δ CUT {sc['cut_delta']:+.2f}$ sur {sc['n_cut']} cuts "
               f"(< seuil {cut_cb_loss}$ dès n≥{cut_cb_min}). Arbitre de "
               f"sortie en observation. Réarmer : supprimer {aix.TRIP_FILE}")
        send_telegram(msg, source="exit_arbiter_cut_circuit_break")
        print("[exit-scorecard] DISJONCTEUR CUT déclenché + drapeau écrit")

    if args.telegram:
        if send_telegram(format_tg(sc, aix.is_tripped()), source="exit_arbiter_scorecard"):
            print("[exit-scorecard] récap Telegram envoyé")
    return 0


if __name__ == "__main__":
    sys.exit(main())
