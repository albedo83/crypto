#!/usr/bin/env python3
"""Entry judge — observation-only LLM verdict on SENIOR's entries.

À chaque close 4h, le bot SENIOR fige le contexte de décision de chaque entrée
dans un event `ENTRY_CONTEXT` (alfred/data/bots/live/bot.db). Ce script lit les
contextes non encore jugés, demande à Claude de trancher (GO/VETO + raison), et
persiste un event `ENTRY_VERDICT` dans la même base.

OBSERVATION SEULEMENT — n'agit sur rien. Le trade a déjà été pris par le moteur
de règles (walk-forward validé). On logge le verdict pour mesurer ex-post si le
jugement de Claude corrèle avec l'issue (--report). En avançant depuis le
déploiement, les issues sont postérieures au cutoff training → vraie forward-
validation, sans look-ahead. Aucun gate live tant que les données n'ont pas
tranché (≥50 trades) + OK explicite.

Usage:
    ./entry_judge.py               # juge les entrées non jugées (API réelle)
    ./entry_judge.py --dry-run     # assemble les prompts, aucun appel API
    ./entry_judge.py --no-write    # appelle Claude, imprime, n'écrit pas en DB
    ./entry_judge.py --report      # analyse ex-post GO vs VETO (pas d'API)
    ./entry_judge.py --model MODEL # override ENTRY_JUDGE_MODEL

Design rules (comme supervisor.py):
- Aucun import de alfred.* — isolation totale du process de trading.
- Read/append-only sur la DB SENIOR (INSERT court, WAL, timeout=5s).
- Kill-switch: ENTRY_JUDGE_ENABLED=0 dans .env.
- Pas de Telegram. La remontée se fait côté admin (/master, event ENTRY_VERDICT).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from ai_doctrine import DOCTRINE_DIGEST

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
SENIOR_DB = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live", "bot.db")

MAX_PER_RUN = 24  # garde-fou (un scan 4h ouvre au plus max_positions entrées)

SYSTEM_PROMPT = """\
Tu juges la qualité d'une entrée de position qui vient d'être ouverte par un bot
de trading automatique sur Hyperliquid (altcoins perp, levier 2×, holds ~24-48h).

CADRE — IMPORTANT :
- Le trade A DÉJÀ ÉTÉ PRIS par un moteur de règles walk-forward validé (S1/S5/
  S8/S9/S10). Ton verdict n'exécute RIEN : il est loggé pour mesurer plus tard si
  ton jugement corrèle avec l'issue réelle. Tu n'as pas le résultat, il est dans
  le futur. Juge honnêtement, sans deviner l'issue.
- Le moteur a un edge prouvé en agrégat. Donc ton défaut est GO. Ne mets VETO que
  si tu vois une raison CONCRÈTE que CE setup précis est mauvais malgré la règle.

CE QUI JUSTIFIE UN VETO (exemples, non exhaustif) :
- Couteau qui tombe : fade (S9) ou mean-reversion (S5) à contre-courant d'une
  tendance directionnelle forte et alignée (btc_z extrême, momentum persistant).
- Désaccord régime : LONG en bear marqué (btc_z très négatif) ou SHORT en bull
  marqué, alors que la stratégie est régime-sensible.
- Crowding/structure extrême défavorable (funding, OI delta, dispersion) qui
  signale une poursuite plutôt qu'un retour.
- Setup mécaniquement marginal : gain brut attendu faible alors que le floor de
  frais Hyperliquid est ~9 bps aller-retour (taker) — un edge < ~50 bps est fragile.

CE QUI NE JUSTIFIE PAS UN VETO :
- Une simple intuition baissière/haussière macro sans élément du contexte fourni.
- Re-litiger la stratégie elle-même (elle est validée). Tu juges CE setup.

FORMAT DE SORTIE — réponds EXCLUSIVEMENT en JSON valide, rien avant, rien après :
{
  "decision": "GO" | "VETO",
  "confidence": <float 0.0-1.0>,
  "reason": "<=240 chars EN FRANÇAIS, factuel, ancré sur le contexte fourni>",
  "risk_flags": ["<tag court>", ...]  // 0-4 tags ex: "knife", "regime_mismatch",
                                      // "crowding", "thin_edge", "trend_align"
}

Tout le texte (reason) en français. Concision. Pas d'hallucination de chiffres :
n'utilise que les valeurs du contexte fourni.
"""


# ── .env loader (copie supervisor.py) ──────────────────────────────────


def load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


# ── DB helpers ──────────────────────────────────────────────────────────


def load_events(db_path: str, event: str) -> list[dict]:
    """Read all events of a kind, parsing the JSON `data` column."""
    if not os.path.exists(db_path):
        return []
    db = sqlite3.connect(db_path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT ts, symbol, data FROM events WHERE event=? ORDER BY ts",
            (event,),
        ).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            d = {}
        out.append({"ts": r["ts"], "symbol": r["symbol"], "data": d})
    return out


def unjudged_contexts(db_path: str) -> list[dict]:
    """ENTRY_CONTEXT events without a matching ENTRY_VERDICT (by symbol+entry_time)."""
    contexts = load_events(db_path, "ENTRY_CONTEXT")
    verdicts = load_events(db_path, "ENTRY_VERDICT")
    judged = {
        (v["data"].get("symbol") or v["symbol"], v["data"].get("entry_time"))
        for v in verdicts
    }
    out = []
    for c in contexts:
        key = (c["symbol"], c["data"].get("entry_time"))
        if key in judged:
            continue
        out.append(c)
    return out


def log_event(db_path: str, event: str, symbol: str | None, data: dict) -> None:
    """Persist an event (append-only, safe across processes in WAL)."""
    if not os.path.exists(db_path):
        print(f"[entry_judge] DB introuvable: {db_path}", file=sys.stderr)
        return
    try:
        db = sqlite3.connect(db_path, timeout=5)
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), event, symbol, json.dumps(data, default=str)),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[entry_judge] Event log failed: {e}", file=sys.stderr)


def load_doctrine() -> str:
    """Digest condensé stratégies/sorties (cache budget — pas le bot.md complet)."""
    return DOCTRINE_DIGEST


# ── Prompt + Claude ─────────────────────────────────────────────────────


def build_user_prompt(ctx: dict) -> str:
    sym = ctx["symbol"]
    payload = json.dumps(ctx["data"], indent=2, default=str, ensure_ascii=False)
    return (
        f"Entrée à juger — {sym}.\n\n"
        "Contexte exact que le bot a vu au moment de l'ouverture (figé) :\n\n"
        "```json\n" + payload + "\n```\n\n"
        "Rends ton verdict JSON."
    )


def call_claude(doctrine: str, user_prompt: str, model: str) -> dict:
    """Call Anthropic with the doctrine cached (ephemeral). Returns parsed JSON."""
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    system = [{"type": "text", "text": SYSTEM_PROMPT}]
    if doctrine:
        system.append({
            "type": "text",
            "text": "# Référence stratégies & sorties du bot\n\n" + doctrine,
            "cache_control": {"type": "ephemeral"},
        })
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw = "".join(parts).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError(f"Claude n'a pas renvoyé de JSON:\n---\n{raw}")
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON malformé: {e}\n---\n{match.group(0)}")

    # Normalisation défensive
    dec = str(verdict.get("decision", "")).upper()
    verdict["decision"] = "VETO" if dec == "VETO" else "GO"
    try:
        verdict["confidence"] = round(float(verdict.get("confidence", 0.0)), 3)
    except (TypeError, ValueError):
        verdict["confidence"] = 0.0
    if not isinstance(verdict.get("risk_flags"), list):
        verdict["risk_flags"] = []

    usage = getattr(resp, "usage", None)
    if usage:
        verdict["_usage"] = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        }
    verdict["_model"] = model
    return verdict


# ── Ex-post report (--report) ───────────────────────────────────────────


def fetch_trades(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    db = sqlite3.connect(db_path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT symbol, strategy, direction, entry_time, exit_time, "
            "pnl_usdt, size_usdt, mae_bps, mfe_bps, reason FROM trades "
            "WHERE exit_time IS NOT NULL ORDER BY entry_time"
        ).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "wr": 0.0, "sum": 0.0, "avg": 0.0,
                "avg_mfe": 0.0, "avg_mae": 0.0}
    pnl = [r["pnl_usdt"] or 0.0 for r in rows]
    wins = [p for p in pnl if p > 0]
    return {
        "n": n,
        "wr": len(wins) / n * 100,
        "sum": sum(pnl),
        "avg": sum(pnl) / n,
        "avg_mfe": sum(r.get("mfe_bps") or 0.0 for r in rows) / n,
        "avg_mae": sum(r.get("mae_bps") or 0.0 for r in rows) / n,
    }


def _match_key(symbol: str, entry_time: str) -> str:
    """Join key tolerant to sub-second formatting differences."""
    et = (entry_time or "")[:19]  # YYYY-MM-DDTHH:MM:SS
    return f"{symbol}|{et}"


def run_report(db_path: str) -> int:
    verdicts = load_events(db_path, "ENTRY_VERDICT")
    trades = fetch_trades(db_path)
    if not verdicts:
        print("[entry_judge] aucun ENTRY_VERDICT encore — rien à analyser.")
        return 0

    by_key = {}
    for v in verdicts:
        sym = v["data"].get("symbol") or v["symbol"]
        et = v["data"].get("entry_time")
        by_key[_match_key(sym, et)] = v["data"]

    go, veto, pending = [], [], 0
    for t in trades:
        v = by_key.get(_match_key(t["symbol"], t["entry_time"]))
        if not v:
            continue
        (go if v.get("decision") == "GO" else veto).append(t)
    judged_closed = len(go) + len(veto)
    pending = len(verdicts) - judged_closed  # verdicts dont le trade n'est pas (encore) clos

    sg, sv = _stats(go), _stats(veto)
    print("=== Entry judge — ex-post GO vs VETO (SENIOR) ===")
    print(f"Verdicts: {len(verdicts)} | trades clos jugés: {judged_closed} "
          f"| en cours/non appariés: {max(0, pending)}\n")
    hdr = f"{'bucket':<6} {'n':>4} {'WR%':>6} {'sumPnL$':>10} {'avgPnL$':>9} {'avgMFE':>8} {'avgMAE':>8}"
    print(hdr)
    print("-" * len(hdr))
    for name, s in (("GO", sg), ("VETO", sv)):
        print(f"{name:<6} {s['n']:>4} {s['wr']:>6.1f} {s['sum']:>10.2f} "
              f"{s['avg']:>9.2f} {s['avg_mfe']:>8.0f} {s['avg_mae']:>8.0f}")

    # Lecture : un VETO qui mord = bucket VETO nettement plus perdant que GO.
    if sv["n"] >= 5 and sg["n"] >= 5:
        edge = sg["avg"] - sv["avg"]
        print(f"\nSéparation avgPnL (GO - VETO): {edge:+.2f}$/trade "
              f"({'VETO discrimine' if edge > 0 else 'pas de signal'}).")
    else:
        print("\n(N insuffisant pour conclure — viser ≥50 trades clos jugés.)")

    # Top risk_flags des VETO + leur PnL
    if veto:
        flag_pnl = defaultdict(list)
        for t in veto:
            v = by_key.get(_match_key(t["symbol"], t["entry_time"])) or {}
            for fl in (v.get("risk_flags") or []):
                flag_pnl[fl].append(t["pnl_usdt"] or 0.0)
        if flag_pnl:
            print("\nrisk_flags VETO (n, sumPnL$):")
            for fl, ps in sorted(flag_pnl.items(), key=lambda kv: sum(kv[1])):
                print(f"  {fl:<18} n={len(ps):<3} sum={sum(ps):+.2f}")

    # Détail par stratégie/direction
    print("\nPar (stratégie, direction) :")
    grp = defaultdict(lambda: {"GO": [], "VETO": []})
    for bucket, rows in (("GO", go), ("VETO", veto)):
        for t in rows:
            grp[(t["strategy"], t["direction"])][bucket].append(t)
    for (strat, d), b in sorted(grp.items()):
        sg2, sv2 = _stats(b["GO"]), _stats(b["VETO"])
        print(f"  {strat:<4} {d:<5} | GO n={sg2['n']:<3} sum={sg2['sum']:+8.2f} "
              f"| VETO n={sv2['n']:<3} sum={sv2['sum']:+8.2f}")
    return 0


# ── Orchestration ──────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Assemble les prompts, aucun appel API ni écriture")
    parser.add_argument("--no-write", action="store_true",
                        help="Appelle Claude, imprime les verdicts, n'écrit pas en DB")
    parser.add_argument("--report", action="store_true",
                        help="Analyse ex-post GO vs VETO (pas d'API)")
    parser.add_argument("--model", default=None, help="Override ENTRY_JUDGE_MODEL")
    parser.add_argument("--limit", type=int, default=MAX_PER_RUN,
                        help=f"Max entrées jugées par run (défaut {MAX_PER_RUN})")
    args = parser.parse_args()

    load_env()

    if args.report:
        return run_report(SENIOR_DB)

    if os.environ.get("ENTRY_JUDGE_ENABLED", "1") == "0":
        print("[entry_judge] ENTRY_JUDGE_ENABLED=0 — sortie sans action")
        return 0

    pending = unjudged_contexts(SENIOR_DB)
    if not pending:
        print("[entry_judge] aucune entrée non jugée.")
        return 0
    if len(pending) > args.limit:
        print(f"[entry_judge] {len(pending)} entrées en attente, "
              f"plafonné à {args.limit} ce run (le reste au prochain run).")
        pending = pending[:args.limit]

    doctrine = load_doctrine()
    print(f"[entry_judge] {len(pending)} entrée(s) à juger | doctrine "
          f"{len(doctrine)} chars")

    if args.dry_run:
        for c in pending:
            print(f"\n=== {c['symbol']} @ {c['data'].get('entry_time')} ===")
            print(build_user_prompt(c)[:1200])
        print("\n[entry_judge] --dry-run: arrêt avant appel Claude")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[entry_judge] ANTHROPIC_API_KEY absent du .env", file=sys.stderr)
        return 1
    model = args.model or os.environ.get("ENTRY_JUDGE_MODEL", "claude-haiku-4-5")
    print(f"[entry_judge] modèle {model}")

    written = 0
    vetoes: list[dict] = []
    for c in pending:
        sym = c["symbol"]
        et = c["data"].get("entry_time")
        try:
            verdict = call_claude(doctrine, build_user_prompt(c), model)
        except Exception as e:
            print(f"[entry_judge] échec {sym} @ {et}: {e}", file=sys.stderr)
            continue
        verdict["symbol"] = sym
        verdict["entry_time"] = et
        verdict["strategy"] = c["data"].get("strategy")
        verdict["dir"] = c["data"].get("dir")
        print(f"  {sym:<6} {verdict.get('dir',''):<5} "
              f"{verdict['decision']:<4} conf={verdict['confidence']:.2f} "
              f"flags={verdict.get('risk_flags')} — {verdict.get('reason','')}")
        if not args.no_write:
            log_event(SENIOR_DB, "ENTRY_VERDICT", sym, verdict)
            written += 1
            if verdict["decision"] == "VETO":
                vetoes.append(verdict)

    if args.no_write:
        print("[entry_judge] --no-write: rien écrit en DB, pas d'envoi TG")
    else:
        print(f"[entry_judge] {written} verdict(s) écrit(s) dans {SENIOR_DB}")
        # Telegram SENIOR uniquement pour les VETO (les GO restent admin-only).
        if vetoes:
            lines = ["🚫 entry_judge — VETO SENIOR"]
            for v in vetoes:
                lines.append(f"{v['symbol']} {v.get('strategy', '')} {v.get('dir', '')} "
                             f"(conf {v.get('confidence')})")
                if v.get("reason"):
                    lines.append(f"  {v['reason']}")
            try:
                from ai_notify import send_telegram
                if send_telegram("\n".join(lines), source="verdict_veto"):
                    print(f"[entry_judge] Telegram SENIOR envoyé ({len(vetoes)} VETO)")
            except Exception as e:
                print(f"[entry_judge] Telegram échec: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
