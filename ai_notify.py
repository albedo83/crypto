"""Envoi Telegram partagé pour les rapports IA — SENIOR uniquement.

Utilise le canal du bot live (TG_BOT_TOKEN / TG_CHAT_ID), pas celui de junior.
Kill-switch : AI_TG_ENABLED=0 dans .env. Texte brut (pas de parse_mode : le
contenu LLM contient routinièrement des underscores/astérisques qui cassent le
Markdown).

Chaque message effectivement envoyé est aussi journalisé dans la DB SENIOR
(event AI_TG) pour la section « Historique IA » du dashboard. Purge ultérieure
(pas de rétention automatique pour l'instant).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

SENIOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "alfred", "data", "bots", "live", "bot.db")

# Préfixe distinctif : tout message de la couche IA (superviseur, revue de
# positions, scorecard/disjoncteur arbitre, verdict) est marqué pour le
# distinguer d'un coup d'œil des messages mécaniques du bot (OPEN/CLOSE/alertes).
AI_PREFIX = "🧠 IA — "


def _log_history(text: str, source: str) -> None:
    """Journalise le message envoyé dans la DB SENIOR (event AI_TG)."""
    if not os.path.exists(SENIOR_DB):
        return
    try:
        db = sqlite3.connect(SENIOR_DB, timeout=5)
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), "AI_TG", None,
             json.dumps({"text": text, "source": source})))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[ai_notify] history log failed: {e}", file=sys.stderr)


def send_telegram(text: str, source: str = "") -> bool:
    """Envoi best-effort sur le canal SENIOR. Retourne True si ok:true.
    `source` étiquette le message dans l'historique (superviseur / revue / verdict).
    Journalise dans la DB SENIOR uniquement si l'envoi réussit.
    """
    if os.environ.get("AI_TG_ENABLED", "1") == "0":
        return False
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat:
        print("[ai_notify] TG_BOT_TOKEN/TG_CHAT_ID absents — pas d'envoi", file=sys.stderr)
        return False
    sent_text = text if text.startswith(AI_PREFIX) else AI_PREFIX + text
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": sent_text}).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=10) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                _log_history(text, source)
                return True
            print(f"[ai_notify] TG error: {body.get('description')}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[ai_notify] TG send failed: {e}", file=sys.stderr)
        return False
