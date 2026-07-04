#!/usr/bin/env python3
"""Position review — regard advisory de Claude sur les positions OUVERTES de SENIOR.

Comportement « humain » : regarde chaque position ouverte (trajectoire, MAE/MFE,
temps tenu, régime) et dit si un stop manuel serait judicieux — ce que les
formules ne voient pas forcément. ADVISORY UNIQUEMENT : écrit un event
POSITION_REVIEW (snapshot) dans live/bot.db, affiché côté admin /master.
N'agit sur rien ; l'admin applique un stop via le bouton 🎯 du dashboard s'il
est d'accord (POST /bot/live/api/manual_stop/<sym>).

Usage:
    ./position_review.py            # API réelle + écriture DB
    ./position_review.py --dry-run  # contexte + prompt, aucun appel API
    ./position_review.py --no-write # API, stdout, pas d'écriture DB
    ./position_review.py --model M  # override POSITION_REVIEW_MODEL

Design rules (comme supervisor.py / entry_judge.py):
- Aucun import de alfred.* — isolation du process de trading. Réutilise le
  client HTTP authentifié de supervisor.py (lecture seule /bot/live/api/state).
- Kill-switch: POSITION_REVIEW_ENABLED=0 dans .env.
- Pas de Telegram. Remontée côté admin (event POSITION_REVIEW).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

from supervisor import BotClient, load_env  # client HTTP authentifié partagé
from ai_doctrine import DOCTRINE_DIGEST

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SENIOR_DB = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live", "bot.db")

SYSTEM_PROMPT = """\
Tu es l'œil humain sur les positions OUVERTES du bot de trading LIVE (SENIOR)
sur Hyperliquid (altcoins perp, levier 2×). Le bot a déjà ses sorties
automatiques (stop catastrophe, prop_trail, traj_cut, dead_timeout, timeout,
etc.). Ton rôle : repérer ce que les formules ne voient pas — une position dont
la trajectoire est « désespérée » (cassée, pinned au plus bas, momentum contre,
régime retourné) où un stop manuel anticipé serait judicieux, OU au contraire
confirmer qu'il faut laisser courir.

CADRE — IMPORTANT :
- ADVISORY uniquement. Ton avis n'exécute RIEN. L'admin décide et applique un
  stop manuel à la main s'il est d'accord. Sois utile, pas alarmiste.
- Le bot a un edge prouvé et l'asymétrie du compounding fait que couper un
  gagnant coûte plus cher que laisser passer un perdant. Donc HOLD par défaut.
  Ne propose STOP/TRIM que si la trajectoire le justifie concrètement.
- Ne JAMAIS citer un MAE/MFE comme une perte réalisée : ce sont des excursions
  d'une position encore vivante.

AVIS POSSIBLES :
- HOLD : laisser courir (rien à faire).
- WATCH : à surveiller, pas d'action immédiate.
- TRIM : envisager d'alléger / sécuriser une partie du gain.
- STOP : envisager un stop manuel maintenant (trajectoire cassée).

`suggested_stop_usdt` (seulement si TRIM/STOP, sinon null) : le niveau de P&L
latent EN $ auquel couper. Doit être STRICTEMENT inférieur au `pnl_usdt` actuel
et supérieur à la perte du stop catastrophe — sinon laisse null.

FORMAT — réponds EXCLUSIVEMENT en JSON valide, rien avant/après :
{
  "reviews": [
    {"symbol": "<TOKEN>", "advice": "HOLD|WATCH|TRIM|STOP",
     "suggested_stop_usdt": <number ou null>,
     "confidence": <float 0.0-1.0>,
     "reason": "<=200 chars FR, factuel, ancré sur la trajectoire fournie>"}
  ]
}

Une entrée par position fournie. Tout le texte en français. Concision. Pas
d'hallucination de chiffres : uniquement les valeurs du contexte fourni.
"""

import hashlib as _hl
PROMPT_HASH = _hl.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:10]


# Champs de position conservés pour le prompt (le reste = bruit/sparkline).
_POS_KEEP = [
    "symbol", "strategy", "direction", "entry_price", "current_price",
    "size_usdt", "unrealized_bps", "pnl_usdt", "hold_hours", "remaining_hours",
    "mae_bps", "mfe_bps", "stop_bps", "manual_stop_usdt", "opp_floor_bps",
    "prop_trail_active", "prop_trail_stop_bps", "win_prob", "signal_info",
]


def log_event(db_path: str, event: str, symbol: str | None, data: dict) -> None:
    if not os.path.exists(db_path):
        print(f"[position_review] DB introuvable: {db_path}", file=sys.stderr)
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
        print(f"[position_review] Event log failed: {e}", file=sys.stderr)


def load_doctrine() -> str:
    """Digest condensé stratégies/sorties (cache budget — pas le bot.md complet)."""
    return DOCTRINE_DIGEST


def fetch_state(user: str, password: str) -> dict | None:
    client = BotClient("live", user, password)
    return client.fetch("/api/state")


def compact_market(state: dict) -> dict:
    """Scalaires utiles du market snapshot (régime), sans les dumps per-symbol."""
    mkt = state.get("market") or {}
    out = {}
    for k, v in mkt.items():
        if isinstance(v, (int, float, str, bool)):
            out[k] = v
    # btc_z exposé directement par /api/state selon la version
    for k in ("btc_z_30d", "btc_z"):
        if k in state and isinstance(state[k], (int, float)):
            out[k] = state[k]
    return out


def build_user_prompt(positions: list[dict], market: dict) -> str:
    slim = [{k: p.get(k) for k in _POS_KEEP if k in p} for p in positions]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Revue des positions ouvertes — {now}.\n\n"
        "Régime / marché :\n```json\n"
        + json.dumps(market, indent=2, default=str, ensure_ascii=False)
        + "\n```\n\nPositions ouvertes (trajectoire figée à l'instant) :\n```json\n"
        + json.dumps(slim, indent=2, default=str, ensure_ascii=False)
        + "\n```\n\nRends ton JSON `reviews` (une entrée par position)."
    )


def call_claude(doctrine: str, user_prompt: str, model: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    # Un seul appel par run (toutes les positions en un prompt) → cache toujours
    # froid à 2h d'écart : on NE cache PAS le digest (éviterait le malus 1.25×).
    system = [{"type": "text", "text": SYSTEM_PROMPT}]
    if doctrine:
        system.append({
            "type": "text",
            "text": "# Référence stratégies & sorties du bot\n\n" + doctrine,
        })
    resp = client.messages.create(
        model=model, max_tokens=1024, system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw = "".join(parts).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError(f"Claude n'a pas renvoyé de JSON:\n---\n{raw}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON malformé: {e}\n---\n{match.group(0)}")
    usage = getattr(resp, "usage", None)
    data["_usage"] = ({
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
    } if usage else {})
    data["_model"] = model
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Contexte + prompt, aucun appel API ni écriture")
    parser.add_argument("--no-write", action="store_true",
                        help="Appelle Claude, imprime, n'écrit pas en DB")
    parser.add_argument("--model", default=None,
                        help="Override POSITION_REVIEW_MODEL")
    # Mode FOCUS (routeur d'attention, supervision v2 ph.1) : revue ciblée
    # sur un slice de positions avec le contexte du déclencheur dans le prompt.
    parser.add_argument("--focus-symbols", default=None,
                        help="CSV de symboles — ne revoir que ceux-là")
    parser.add_argument("--trigger-context", default=None,
                        help="Contexte du déclencheur (préfixé au prompt + loggé)")
    args = parser.parse_args()

    load_env()

    if os.environ.get("POSITION_REVIEW_ENABLED", "1") == "0":
        print("[position_review] POSITION_REVIEW_ENABLED=0 — sortie sans action")
        return 0

    user = os.environ.get("DASHBOARD_USER", "")
    password = os.environ.get("DASHBOARD_PASS", "")
    if not user or not password:
        print("[position_review] DASHBOARD_USER/PASS absents du .env", file=sys.stderr)
        return 1

    state = fetch_state(user, password)
    if not state:
        print("[position_review] /api/state injoignable", file=sys.stderr)
        return 1
    positions = state.get("positions") or []
    if args.focus_symbols:
        _want = {x.strip().upper() for x in args.focus_symbols.split(",") if x.strip()}
        positions = [p for p in positions if (p.get("symbol") or "").upper() in _want]
    if not positions:
        print("[position_review] aucune position (dans le focus) — rien à revoir.")
        return 0

    market = compact_market(state)
    doctrine = load_doctrine()
    user_prompt = build_user_prompt(positions, market)
    if args.trigger_context:
        user_prompt = (f"⚡ REVUE DÉCLENCHÉE PAR ÉVÉNEMENT (pas la revue "
                       f"périodique) : {args.trigger_context}\n"
                       f"Concentre ton jugement sur ce que cet événement "
                       f"change pour les positions ci-dessous.\n\n") + user_prompt
    print(f"[position_review] {len(positions)} position(s) | doctrine "
          f"{len(doctrine)} chars | prompt {len(user_prompt)} chars")

    if args.dry_run:
        print("\n=== USER PROMPT ===")
        print(user_prompt[:2500])
        print("\n[position_review] --dry-run: arrêt avant Claude")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY", ""):
        print("[position_review] ANTHROPIC_API_KEY absent du .env", file=sys.stderr)
        return 1
    model = args.model or os.environ.get("POSITION_REVIEW_MODEL", "claude-haiku-4-5")
    print(f"[position_review] modèle {model}")
    try:
        result = call_claude(doctrine, user_prompt, model)
    except Exception as e:
        print(f"[position_review] échec: {e}", file=sys.stderr)
        log_event(SENIOR_DB, "POSITION_REVIEW_ERROR", None, {"error": str(e)})
        return 1

    # Enrichit chaque review avec strategy/dir de la position correspondante
    by_sym = {p["symbol"]: p for p in positions}
    reviews = []
    for r in (result.get("reviews") or []):
        sym = r.get("symbol")
        p = by_sym.get(sym, {})
        reviews.append({
            "symbol": sym,
            "strategy": p.get("strategy"),
            "dir": p.get("direction"),
            "advice": str(r.get("advice", "HOLD")).upper(),
            "suggested_stop_usdt": r.get("suggested_stop_usdt"),
            "confidence": r.get("confidence"),
            "reason": r.get("reason", ""),
            "pnl_usdt": p.get("pnl_usdt"),
            "unrealized_bps": p.get("unrealized_bps"),
        })
        print(f"  {sym:<6} {reviews[-1]['advice']:<5} "
              f"conf={reviews[-1]['confidence']} — {reviews[-1]['reason']}")

    snapshot = {
        "prompt_hash": PROMPT_HASH,
        "model": model,
        "trigger": args.trigger_context,
        "focus": args.focus_symbols,
        "generated": datetime.now(timezone.utc).isoformat(),
        "n_positions": len(positions),
        "positions": reviews,
        "_usage": result.get("_usage"),
        "_model": result.get("_model"),
    }
    if args.no_write:
        print("[position_review] --no-write: rien écrit en DB, pas d'envoi TG")
    else:
        log_event(SENIOR_DB, "POSITION_REVIEW", None, snapshot)
        print(f"[position_review] POSITION_REVIEW loggé ({len(reviews)} avis)")
        # Telegram retiré (2026-07-01) — la revue de positions reste dans
        # l'historique (event POSITION_REVIEW / dashboard), pas d'envoi TG.
    return 0


if __name__ == "__main__":
    sys.exit(main())
