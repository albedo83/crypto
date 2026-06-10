"""Telegram notifier — per-instance config (token/chat/categories/label),
replacing the module-global send_telegram of analysis/bot/net.py.

Each BotInstance gets its own Notifier (from bots.json); the master gets one
for system alerts. A Notifier without token/chat_id is silently inert, which
is how paper bots disable Telegram (config, not execution-mode checks).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request

log = logging.getLogger("alfred")


class Notifier:
    def __init__(self, token: str = "", chat_id: str = "",
                 categories: str = "*", label: str = "",
                 public_url: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.label = label
        self.public_url = public_url.rstrip("/")
        self._allowed: set[str] | None = (
            None if categories.strip() == "*"
            else {c.strip() for c in categories.split(",") if c.strip()})
        self._prefix = f"[{label}] " if label else ""
        # "📊 Dashboard" button: attached to daily digests and actionable alerts.
        self._button = (
            {"inline_keyboard": [[{"text": "📊 Dashboard",
                                   "url": f"{self.public_url}/"}]]}
            if self.public_url else None)
        self._button_categories = {"daily"}

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, msg: str, category: str = "other",
             actionable: bool = False) -> None:
        """Fire-and-forget in a daemon thread. Category-filtered."""
        if not self.enabled:
            return
        if self._allowed is not None and category not in self._allowed:
            return

        def _do_send():
            try:
                body_dict = {"chat_id": self.chat_id, "text": self._prefix + msg}
                if self._button is not None and (
                        actionable or category in self._button_categories):
                    body_dict["reply_markup"] = self._button
                payload = json.dumps(body_dict).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    data=payload, headers={"Content-Type": "application/json"})
                # Read the body and verify ok:true — Telegram returns HTTP 200
                # even on rejected messages (rate-limit, bad chat_id).
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = resp.read()
                try:
                    parsed = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    log.warning("Telegram non-JSON body (first 200B): %r", body[:200])
                    return
                if not parsed.get("ok"):
                    log.warning("Telegram non-OK: %s",
                                parsed.get("description") or parsed)
            except Exception as e:
                log.warning("Telegram error: %s", e)

        threading.Thread(target=_do_send, daemon=True).start()
