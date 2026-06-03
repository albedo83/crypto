#!/usr/bin/env python3
"""Hedge conflict monitor — alerts when Live and Junior hold opposing positions.

Detects when both bots have a position on the same token in opposite directions
(LONG on one, SHORT on the other), which results in a partial portfolio hedge
that costs funding without net edge.

Run via cron every 5 minutes. Telegram alert sent ONCE per unique conflict pair
(deduplicated via state file using entry_time of both positions).

Both Live and Junior Telegram channels get the alert (so the user sees it
regardless of which app they have open).

Kill-switch: comment out the crontab line.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

LIVE_URL = "http://127.0.0.1:8098"
JUNIOR_URL = "http://127.0.0.1:8099"
STATE_FILE = Path("/home/crypto/analysis/output/hedge_monitor_state.json")
LOG_FILE = Path("/home/crypto/analysis/output/hedge_monitor.log")
ENV_FILE = Path("/home/crypto/.env")

logging.basicConfig(
    filename=str(LOG_FILE),
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hedge_monitor")


def load_env() -> dict:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch_state(base_url: str, user: str, pwd: str) -> dict | None:
    """POST /login then GET /api/state with session cookie. Returns None on error."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPRedirectHandler(),
    )
    try:
        body = urllib.parse.urlencode({"username": user, "password": pwd}).encode()
        login_req = urllib.request.Request(
            f"{base_url}/login", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        opener.open(login_req, timeout=5)
        state_req = urllib.request.Request(f"{base_url}/api/state")
        resp = opener.open(state_req, timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        log.warning("fetch %s failed: %s", base_url, e)
        return None


def send_telegram(token: str, chat_id: str, msg: str) -> bool:
    if not token or not chat_id:
        return False
    body = json.dumps({"chat_id": chat_id, "text": msg}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            parsed = json.loads(resp.read())
        if not parsed.get("ok"):
            log.warning("Telegram non-OK: %s", parsed.get("description"))
            return False
        return True
    except Exception as e:
        log.warning("Telegram error: %s", e)
        return False


def main() -> None:
    env = load_env()
    live = fetch_state(LIVE_URL, env.get("DASHBOARD_USER", ""), env.get("DASHBOARD_PASS", ""))
    junior = fetch_state(JUNIOR_URL, env.get("JUNIOR_USER", ""), env.get("JUNIOR_PASS", ""))
    if not live or not junior:
        log.info("at least one bot unreachable — skip")
        return

    live_pos = {p["symbol"]: p for p in live.get("positions", []) or []}
    junior_pos = {p["symbol"]: p for p in junior.get("positions", []) or []}

    conflicts = []
    for sym in set(live_pos) & set(junior_pos):
        L = live_pos[sym]
        J = junior_pos[sym]
        if L.get("direction") != J.get("direction"):
            conflicts.append((sym, L, J))

    prev_state = {}
    if STATE_FILE.exists():
        try:
            prev_state = json.loads(STATE_FILE.read_text())
        except Exception:
            prev_state = {}

    new_state = {}
    alerts_sent = 0
    for sym, L, J in conflicts:
        key = f"{sym}|{L.get('entry_time','')}|{J.get('entry_time','')}"
        new_state[key] = int(time.time())
        if key in prev_state:
            continue
        L_dir = L.get("direction", "?")
        J_dir = J.get("direction", "?")
        L_sz = float(L.get("size_usdt", 0))
        J_sz = float(J.get("size_usdt", 0))
        L_signed = L_sz if L_dir == "LONG" else -L_sz
        J_signed = J_sz if J_dir == "LONG" else -J_sz
        net = L_signed + J_signed
        net_dir = "LONG" if net > 0 else "SHORT" if net < 0 else "FLAT"
        msg = (
            f"⚠️ HEDGE CONFLICT {sym}\n"
            f"LIVE   {L.get('strategy','?'):>3} {L_dir:>5} ${L_sz:>5.0f} "
            f"ur={L.get('unrealized_bps',0):+.0f}bps\n"
            f"JUNIOR {J.get('strategy','?'):>3} {J_dir:>5} ${J_sz:>5.0f} "
            f"ur={J.get('unrealized_bps',0):+.0f}bps\n"
            f"Net exposure: {net_dir} ${abs(net):.0f}\n"
            f"Funding paye 2x. Close l'un des deux manuellement."
        )
        send_telegram(env.get("TG_BOT_TOKEN", ""), env.get("TG_CHAT_ID", ""), msg)
        send_telegram(env.get("JUNIOR_TG_BOT_TOKEN", ""), env.get("JUNIOR_TG_CHAT_ID", ""), msg)
        log.info("ALERT %s Live=%s%s Junior=%s%s", sym, L_dir, L_sz, J_dir, J_sz)
        alerts_sent += 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(new_state, indent=2))
    log.info("scan done: %d conflicts, %d new alerts", len(conflicts), alerts_sent)


if __name__ == "__main__":
    main()
