#!/usr/bin/env python3
"""Sonde heartbeat Alfred — attrape ce que le pgrep du watchdog laisse passer.

Leçon de l'incident 2026-07-02 : un process peut matcher pgrep tout en étant
un zombie (web morte, ou boucle data pendue). Cette sonde teste la VIE, pas
le PID : âge du dernier tick dans market.db (read-only) + réponse HTTP de la
web locale. Défaillance persistante sur 2 runs consécutifs (cron */2 → ≥2 min,
absorbe la fenêtre de boot ~3,5 min via le compteur) → alerte Telegram.
Re-alerte toutes les 30 min tant que ça persiste ; message de rétablissement.

AUCUNE action corrective automatique (règle maison : alerte seulement).
Zéro dépendance au code Alfred — stdlib pur, DB en lecture seule.

Usage : python3 -m analysis.alfred_heartbeat [--dry-run] [--max-age N]
Cron  : */2 * * * *
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.parse

ROOT = "/home/crypto"
MARKET_DB = os.path.join(ROOT, "alfred", "data", "market.db")
STATE = os.path.join(ROOT, "analysis", "output", "alfred_heartbeat_state.json")
WEB_URL = "http://127.0.0.1:8101/login"
MAX_TICK_AGE_S = 90
FAILS_BEFORE_ALERT = 2       # 2 runs consécutifs (≥2 min) — absorbe un boot
REALERT_S = 1800


def load_env():
    env = {}
    try:
        with open(os.path.join(ROOT, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def send_tg(text, dry):
    if dry:
        print(f"[dry] TG: {text}")
        return
    env = load_env()
    tok, chat = env.get("TG_BOT_TOKEN"), env.get("TG_CHAT_ID")
    if not tok or not chat:
        print("no TG creds", file=sys.stderr)
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
    except Exception as e:
        print(f"TG failed: {e}", file=sys.stderr)


def tick_age_s():
    db = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True, timeout=3)
    try:
        r = db.execute("SELECT MAX(ts) FROM ticks").fetchone()
        return time.time() - r[0] if r and r[0] else 1e9
    finally:
        db.close()


def web_alive():
    try:
        with urllib.request.urlopen(WEB_URL, timeout=5) as resp:
            return resp.status in (200, 302, 303, 307)
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-age", type=float, default=MAX_TICK_AGE_S)
    args = ap.parse_args()

    problems = []
    try:
        age = tick_age_s()
        if age > args.max_age:
            problems.append(f"ticks figés depuis {age:.0f}s (> {args.max_age:.0f}s)")
    except Exception as e:
        problems.append(f"market.db illisible ({e})")
    if not web_alive():
        problems.append("web :8101 morte")

    st = {}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE))
        except Exception:
            st = {}
    fails = st.get("fails", 0)
    last_alert = st.get("last_alert", 0)
    now = time.time()

    if problems:
        fails += 1
        alerted = st.get("alerted", False)
        if fails >= FAILS_BEFORE_ALERT and (now - last_alert) >= REALERT_S:
            send_tg("🫀 HEARTBEAT Alfred — zombie possible !\n"
                    + "\n".join(f"  • {p}" for p in problems)
                    + f"\n(persistant sur {fails} sondes ; le watchdog PID ne "
                      f"voit rien — vérifier/redémarrer manuellement)", args.dry_run)
            last_alert = now
            alerted = True
        st = {"fails": fails, "last_alert": last_alert, "alerted": alerted}
        print(f"{time.strftime('%F %T')} FAIL x{fails}: {'; '.join(problems)}")
    else:
        if st.get("alerted"):
            send_tg("✅ HEARTBEAT Alfred rétabli (ticks + web OK)", args.dry_run)
        st = {"fails": 0, "last_alert": 0, "alerted": False}
        print(f"{time.strftime('%F %T')} OK")

    if not args.dry_run:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        json.dump(st, open(tmp, "w"))
        os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
