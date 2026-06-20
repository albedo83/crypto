"""Envoi Telegram partagé pour les rapports IA — SENIOR uniquement.

Utilise le canal du bot live (TG_BOT_TOKEN / TG_CHAT_ID), pas celui de junior.
Kill-switch : AI_TG_ENABLED=0 dans .env. Texte brut (pas de parse_mode : le
contenu LLM contient routinièrement des underscores/astérisques qui cassent le
Markdown).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


def send_telegram(text: str) -> bool:
    """Envoi best-effort sur le canal SENIOR. Retourne True si ok:true."""
    if os.environ.get("AI_TG_ENABLED", "1") == "0":
        return False
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat:
        print("[ai_notify] TG_BOT_TOKEN/TG_CHAT_ID absents — pas d'envoi", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=10) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                return True
            print(f"[ai_notify] TG error: {body.get('description')}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[ai_notify] TG send failed: {e}", file=sys.stderr)
        return False
