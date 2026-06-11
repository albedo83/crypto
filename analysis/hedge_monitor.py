#!/usr/bin/env python3
"""Hedge conflict monitor — alerte quand deux bots LIVE tiennent des positions
opposées sur le même token (hedge partiel : funding payé 2× sans edge net).

Depuis 2026-06-11 les bots vivent dans Alfred (:8101) : UN login admin + UN
appel /api/admin suffisent — la détection couvre N'IMPORTE quelle paire de
bots live (SENIOR×JUNIOR aujourd'hui, extensible sans modification), le
paper est exclu.

Cron toutes les 5 minutes. Alerte Telegram UNE fois par paire de conflit
unique (dedup via state file sur les entry_time des deux positions), envoyée
sur les deux canaux (principal + Junior).

Kill-switch : commenter la ligne crontab.
"""
from __future__ import annotations

import http.cookiejar
import itertools
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

ALFRED_URL = "http://127.0.0.1:8101"
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


def fetch_bots(user: str, pwd: str) -> list | None:
    """POST /login (Alfred, rôle admin) puis GET /api/admin. None si erreur."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPRedirectHandler(),
    )
    try:
        body = urllib.parse.urlencode({"username": user, "password": pwd}).encode()
        login_req = urllib.request.Request(
            f"{ALFRED_URL}/login", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        opener.open(login_req, timeout=5)
        resp = opener.open(urllib.request.Request(f"{ALFRED_URL}/api/admin"),
                           timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        log.warning("fetch Alfred failed: %s", e)
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
    bots = fetch_bots(env.get("DASHBOARD_USER", ""), env.get("DASHBOARD_PASS", ""))
    if not bots:
        log.info("Alfred unreachable — skip")
        return

    live_bots = [b for b in bots
                 if b.get("mode") == "live" and b.get("online")]
    if len(live_bots) < 2:
        log.info("moins de 2 bots live en ligne — rien à comparer")
        return

    conflicts = []   # (sym, (label_a, pos_a), (label_b, pos_b))
    for a, b in itertools.combinations(live_bots, 2):
        pos_a = {p["symbol"]: p for p in a.get("positions", []) or []}
        pos_b = {p["symbol"]: p for p in b.get("positions", []) or []}
        for sym in set(pos_a) & set(pos_b):
            if pos_a[sym].get("direction") != pos_b[sym].get("direction"):
                conflicts.append((sym, (a["label"], pos_a[sym]),
                                  (b["label"], pos_b[sym])))

    prev_state = {}
    if STATE_FILE.exists():
        try:
            prev_state = json.loads(STATE_FILE.read_text())
        except Exception:
            prev_state = {}

    new_state = {}
    alerts_sent = 0
    for sym, (la, A), (lb, B) in conflicts:
        key = f"{sym}|{la}:{A.get('entry_time','')}|{lb}:{B.get('entry_time','')}"
        new_state[key] = int(time.time())
        if key in prev_state:
            continue
        a_dir, b_dir = A.get("direction", "?"), B.get("direction", "?")
        a_sz, b_sz = float(A.get("size_usdt", 0)), float(B.get("size_usdt", 0))
        net = (a_sz if a_dir == "LONG" else -a_sz) + (b_sz if b_dir == "LONG" else -b_sz)
        net_dir = "LONG" if net > 0 else "SHORT" if net < 0 else "FLAT"
        msg = (
            f"⚠️ HEDGE CONFLICT {sym}\n"
            f"{la:<8} {A.get('strategy','?'):>3} {a_dir:>5} ${a_sz:>5.0f} "
            f"ur={A.get('unrealized_bps',0):+.0f}bps\n"
            f"{lb:<8} {B.get('strategy','?'):>3} {b_dir:>5} ${b_sz:>5.0f} "
            f"ur={B.get('unrealized_bps',0):+.0f}bps\n"
            f"Net exposure: {net_dir} ${abs(net):.0f}\n"
            f"Funding paye 2x. Close l'un des deux manuellement."
        )
        send_telegram(env.get("TG_BOT_TOKEN", ""), env.get("TG_CHAT_ID", ""), msg)
        send_telegram(env.get("JUNIOR_TG_BOT_TOKEN", ""), env.get("JUNIOR_TG_CHAT_ID", ""), msg)
        log.info("ALERT %s %s=%s$%.0f %s=%s$%.0f", sym, la, a_dir, a_sz, lb, b_dir, b_sz)
        alerts_sent += 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(new_state, indent=2))
    log.info("scan done: %d bots live, %d conflicts, %d new alerts",
             len(live_bots), len(conflicts), alerts_sent)


if __name__ == "__main__":
    main()
