#!/usr/bin/env python3
"""Regime / pause-worthy alert — periodic Telegram nudge for the user.

Runs hourly via cron. Reads `/api/state` from the live bot, computes the
"Dépause LONGs" score (mirror of the dashboard widget v12.15.3), and
sends a short Telegram message **only** when there's a transition worth
acting on:

  - Score crosses 2 upward         → "prépare la dépause"
  - Score reaches 3 (vert)         → "feu vert pour dépauser S5 LONG"
  - Score drops below 2 from 2+    → "régime se dégrade, considère repauser"
  - Regime label flips to RALLY/BULL / from FLUSH→… etc.
  - BTC level crosses $63/64/65/66k upward (psychological levels)
  - Daily digest at 08:15 UTC regardless (proves the script is running)

State persisted in `analysis/output/regime_alert_state.json` (last_score,
last_regime, last_btc_bucket, last_digest_date). Stateless wrt the bot —
zero coupling with `analysis/bot/*`, only stdlib + `urllib` HTTP.

Usage:
    python3 -m analysis.regime_alert                # normal run
    python3 -m analysis.regime_alert --dry-run      # print to stdout, no TG, no state write
    python3 -m analysis.regime_alert --force-digest # ignore digest-once-per-day rule
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────
ROOT = "/home/crypto"
ENV_FILE = os.path.join(ROOT, ".env")
STATE_FILE = os.path.join(ROOT, "analysis", "output", "regime_alert_state.json")
DB_PATH = os.path.join(ROOT, "analysis", "output_live", "reversal_ticks.db")
BOT_HOST = "http://127.0.0.1:8098"
HTTP_TIMEOUT = 10
DIGEST_HOUR_UTC = 8  # send a forced digest at 08:xx UTC

# Fallback thresholds — used if /api/state doesn't expose them. The
# canonical values live in the bot itself (web.py exposes them via
# unpause_thresholds) and the dashboard widget reads from there too.
# v12.17.0: dynamic ; previously hardcoded constants that drifted from
# the dashboard widget (v12.16.7 fix manually re-aligned them).
THRESHOLD_BTC_Z = -1.0
THRESHOLD_DISP_7D = 900.0
THRESHOLD_STRESS = 6

# BTC psychological levels to alert on upward crossing — derived
# dynamically from the current price in main() so the script stays
# relevant whatever the market regime. v12.17.0.
BTC_LEVEL_STEP = 1_000  # $1k bucket
BTC_LEVEL_STEPS_UP = 3  # alert on next 3 round numbers above current price


# ── env + IO ─────────────────────────────────────────────────────────
def load_env(path: str) -> dict:
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def make_authed_opener(env: dict):
    """v12.17.0: single opener + single login for the whole run.

    Caller passes this opener to every subsequent fetch_* — avoids
    re-hitting /login per call and respects the 10-attempts/5min/IP rate
    limit. Raises on auth failure so the caller can short-circuit.
    """
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    user = env.get("DASHBOARD_USER", "admin")
    pwd = env.get("DASHBOARD_PASS", "")
    data = urllib.parse.urlencode({"username": user, "password": pwd}).encode()
    opener.open(f"{BOT_HOST}/login", data=data, timeout=HTTP_TIMEOUT)
    return opener


def fetch_bot_state(opener) -> dict:
    with opener.open(f"{BOT_HOST}/api/state", timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def fetch_btc_price(opener) -> float | None:
    """Pull the last BTC price from the bot's chart endpoint.
    v12.17.0: reuses the opener from make_authed_opener instead of re-logging in.
    """
    try:
        with opener.open(f"{BOT_HOST}/api/chart/BTC?hours=2", timeout=HTTP_TIMEOUT) as r:
            d = json.loads(r.read())
        pts = d.get("points", [])
        return float(pts[-1]["price"]) if pts else None
    except Exception as e:
        print(f"fetch_btc_price failed: {e}", file=sys.stderr)
        return None


def load_prev_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def send_telegram(text: str, env: dict) -> bool:
    token = env.get("TG_BOT_TOKEN")
    chat = env.get("TG_CHAT_ID")
    if not token or not chat:
        print(f"[no TG] {text}")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=HTTP_TIMEOUT) as r:
            body = json.loads(r.read())
            return bool(body.get("ok"))
    except Exception as e:
        print(f"TG send failed: {e}", file=sys.stderr)
        return False


def log_event_db(payload: dict) -> None:
    """Persist a REGIME_ALERT event for audit. Silent on failure."""
    if not os.path.exists(DB_PATH):
        return
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), "REGIME_ALERT_DIGEST", "", json.dumps(payload)),
        )
        db.commit()
        db.close()
    except Exception:
        pass


# ── Score logic (mirror dashboard) ───────────────────────────────────
def compute_score(s: dict) -> tuple[int, dict]:
    bz = s.get("btc_z_30d")
    d7 = s.get("cross_disp_7d")
    strs = s.get("regime_stress")
    ok_bz = bz is not None and bz > THRESHOLD_BTC_Z
    ok_d7 = d7 is not None and d7 < THRESHOLD_DISP_7D
    ok_strs = strs is not None and strs <= THRESHOLD_STRESS
    return sum([ok_bz, ok_d7, ok_strs]), {
        "btc_z": bz, "disp_7d": d7, "stress": strs,
        "ok_bz": ok_bz, "ok_d7": ok_d7, "ok_strs": ok_strs,
    }


def crossed_btc_level(prev_price: float | None, cur_price: float | None) -> int | None:
    """Return the highest BTC level (≥ $1k bucket) just crossed upward, or None.

    v12.17.0: levels derived from the current price instead of hardcoded.
    Considers the next BTC_LEVEL_STEPS_UP round-number buckets above the
    lower of (prev, cur) — so works at any BTC price band.
    """
    if (prev_price is None or cur_price is None
            or not (isinstance(prev_price, (int, float)) and isinstance(cur_price, (int, float)))):
        return None
    if prev_price != prev_price or cur_price != cur_price:  # NaN check
        return None
    base = int(min(prev_price, cur_price) // BTC_LEVEL_STEP) * BTC_LEVEL_STEP
    for k in range(1, BTC_LEVEL_STEPS_UP + 1):
        lvl = base + k * BTC_LEVEL_STEP
        if prev_price < lvl <= cur_price:
            return lvl
    return None


# ── Message formatting ───────────────────────────────────────────────
def format_message(s: dict, score: int, prev_score: int | None,
                    breakdown: dict, action: str, header: str) -> str:
    bz = breakdown["btc_z"]
    d7 = breakdown["disp_7d"]
    strs = breakdown["stress"]
    regime = s.get("regime", "?")
    paused = s.get("paused_strategies") or []
    paused_longs = [p for p in paused if p[1] == "LONG"]
    paused_str = ", ".join(p[0] for p in paused_longs) if paused_longs else "aucune"

    score_emoji = ["🔴", "🟠", "🟡", "🟢"][min(score, 3)]
    arrow = ""
    if prev_score is not None and prev_score != score:
        arrow = f" ({prev_score}/3 → {score}/3)"

    bz_str = f"{bz:+.2f}" if bz is not None else "?"
    d7_str = f"{d7:.0f}" if d7 is not None else "?"
    strs_str = f"{strs}/10" if strs is not None else "?"

    lines = [
        f"{score_emoji} {header}",
        f"Régime: {regime} | btc_z={bz_str} | disp_7d={d7_str} | stress={strs_str}",
        f"Score Dépause LONGs: {score}/3{arrow}",
        f"Pausés: {paused_str}",
    ]
    if action:
        lines.append("")
        lines.append(f"→ {action}")
    return "\n".join(lines)


# ── Main decision tree ──────────────────────────────────────────────
def decide_action(score: int, prev_score: int | None,
                   regime: str, prev_regime: str | None,
                   paused_longs: set,
                   btc_level_crossed: int | None) -> tuple[bool, str, str]:
    """Returns (should_notify, action_text, header)."""
    # Score transitions
    if prev_score is not None and prev_score < 2 and score >= 2:
        return True, "Prépare la dépause S5 LONG (score 2/3 atteint). Surveille le passage à 3/3 avant de pull the trigger.", "Régime: opportunité"
    if prev_score is not None and prev_score < 3 and score == 3:
        return True, "Feu vert — tu peux dépauser S5 LONG (puis S9 LONG, puis S8 LONG si la tendance se confirme).", "Régime: feu vert"
    if prev_score is not None and prev_score >= 2 and score <= 1:
        active = {"S5", "S9", "S8"} - {p for p in paused_longs}
        if active:
            return True, f"Régime se dégrade. Considère repauser les LONG actifs: {', '.join(sorted(active))}.", "Régime: dégradation"
        return True, "Régime se dégrade — pauses LONG déjà en place, surveille.", "Régime: dégradation"

    # Regime label flip
    if prev_regime is not None and regime != prev_regime:
        if regime in ("BULL", "RALLY"):
            return True, f"Régime → {regime}. Évalue dépauser un LONG si score ≥ 2/3.", "Régime: changement"
        if regime in ("BEAR", "STRESSED"):
            return True, f"Régime → {regime}. Surveille la dégradation continue.", "Régime: changement"

    # BTC level crossed
    if btc_level_crossed:
        return True, f"BTC franchit ${btc_level_crossed//1000}k. Si score ≥ 2/3, dépause envisageable.", f"BTC ${btc_level_crossed//1000}k"

    return False, "", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print to stdout, no TG send, no state write")
    ap.add_argument("--force-digest", action="store_true",
                    help="send digest even if already sent today")
    args = ap.parse_args()

    env = load_env(ENV_FILE)
    # v12.17.0: single login, reused for both fetches.
    try:
        opener = make_authed_opener(env)
    except Exception as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 1
    try:
        state = fetch_bot_state(opener)
    except Exception as e:
        print(f"fetch_bot_state failed: {e}", file=sys.stderr)
        return 1

    # v12.17.0: prefer server-supplied thresholds if exposed (single source
    # of truth between dashboard widget and this script). Fall back to the
    # constants above if the bot version is older.
    global THRESHOLD_BTC_Z, THRESHOLD_DISP_7D, THRESHOLD_STRESS
    th = state.get("unpause_thresholds") or {}
    THRESHOLD_BTC_Z = th.get("btc_z", THRESHOLD_BTC_Z)
    THRESHOLD_DISP_7D = th.get("disp_7d", THRESHOLD_DISP_7D)
    THRESHOLD_STRESS = th.get("stress", THRESHOLD_STRESS)

    score, breakdown = compute_score(state)
    regime = state.get("regime", "?")
    paused = state.get("paused_strategies") or []
    paused_longs = {p[0] for p in paused if p[1] == "LONG"}

    btc_price = fetch_btc_price(opener)

    prev = load_prev_state()
    prev_score = prev.get("score")
    prev_regime = prev.get("regime")
    prev_btc = prev.get("last_btc_price")

    btc_level_crossed = crossed_btc_level(prev_btc, btc_price)

    notify, action, header = decide_action(
        score, prev_score, regime, prev_regime, paused_longs, btc_level_crossed)

    # Daily digest at DIGEST_HOUR_UTC, once per day
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    is_digest_hour = (now.hour == DIGEST_HOUR_UTC)
    digest_already_sent = (prev.get("last_digest_date") == today_str)
    is_digest = is_digest_hour and (not digest_already_sent or args.force_digest)

    if is_digest and not notify:
        notify = True
        if score == 3:
            action = "Score 3/3 — dépause S5 LONG possible si tu n'es pas déjà fait."
        elif score >= 2:
            action = "Score 2/3 — prépare la dépause S5 LONG, attends le 3/3."
        elif score == 0:
            action = "Score 0/3 — laisser les LONGs pausés."
        else:
            action = "Score 1/3 — laisser les LONGs pausés, surveiller convergence."
        header = "Digest quotidien"

    if notify:
        msg = format_message(state, score, prev_score, breakdown, action, header)
        if args.dry_run:
            print("--- DRY RUN ---")
            print(msg)
        else:
            sent = send_telegram(msg, env)
            log_event_db({
                "score": score, "prev_score": prev_score,
                "regime": regime, "prev_regime": prev_regime,
                "btc_price": btc_price, "btc_level_crossed": btc_level_crossed,
                "action": action, "tg_sent": sent,
            })
            print(f"sent: {sent} | {action}")

    if not args.dry_run:
        new_prev = dict(prev)
        new_prev["score"] = score
        new_prev["regime"] = regime
        new_prev["last_btc_price"] = btc_price
        new_prev["last_run_iso"] = now.isoformat()
        if is_digest:
            new_prev["last_digest_date"] = today_str
        save_state(new_prev)

    return 0


if __name__ == "__main__":
    sys.exit(main())
