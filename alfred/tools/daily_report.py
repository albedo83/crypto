"""Daily Alfred refacto digest — phase gates status via Telegram.

Runs the two gate checks and ships a compact summary :
  - phase 2 (observation) : check_observation criteria from market.db
  - phase 3 (parallel-run): compare_paper LOGIC divergence count

Cron (08:30 UTC, après le digest régime de 08:15) :
    30 8 * * * /home/crypto/.venv/bin/python3 -m alfred.tools.daily_report \
        >> /home/crypto/alfred/data/daily_report.log 2>&1

Telegram credentials: TG_BOT_TOKEN / TG_CHAT_ID from .env (master channel,
same as the legacy bots' main alerts). --dry-run prints without sending.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import time
from contextlib import redirect_stdout

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

MARKET_DB = os.path.join(_REPO, "alfred", "data", "market.db")
PAPER_DB = os.path.join(_REPO, "alfred", "data", "bots", "paper", "bot.db")

def dashboard_footer() -> str:
    """Liens cliquables vers la supervision + le dashboard de chaque bot.
    Base publique = nginx root_path /alfred ; override via ALFRED_PUBLIC_URL
    (.env). Telegram linkifie les URL nues automatiquement. Lu ici (pas à
    l'import) pour que l'override .env, chargé dans main(), soit pris."""
    base = os.environ.get("ALFRED_PUBLIC_URL",
                          "https://echonym.fr/alfred").rstrip("/")
    return (f"\n🔗 Supervision : {base}/master"
            f"\n   SENIOR {base}/bot/live/"
            f" · JUNIOR {base}/bot/junior/"
            f" · PAPER {base}/bot/paper/")


def _load_env():
    path = os.path.join(_REPO, ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def observation_summary() -> tuple[str, bool]:
    """(one-line summary, is_clean) from market.db evidence."""
    try:
        db = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
        now = int(time.time())
        first = db.execute("SELECT MIN(ts) FROM ticks").fetchone()[0] or now
        hours = (now - first) / 3600
        last_tick = db.execute("SELECT MAX(ts) FROM ticks").fetchone()[0] or 0
        tick_age = now - last_tick
        n_reconnect = db.execute(
            "SELECT COUNT(*) FROM events WHERE event='WS_RECONNECT'").fetchone()[0]
        n_audit_bad = 0
        for (data,) in db.execute(
                "SELECT data FROM events WHERE event='CANDLE_AUDIT'").fetchall():
            d = json.loads(data or "{}")
            if d.get("mismatches"):
                n_audit_bad += 1
        clean = tick_age < 300 and n_audit_bad == 0
        return (f"obs {hours:.0f}h | dernier tick {tick_age}s | "
                f"reconnects {n_reconnect} | audits KO {n_audit_bad}"), clean
    except Exception as e:
        return f"obs: erreur lecture market.db ({e})", False


def parallel_run_summary() -> tuple[str, bool]:
    """(one-line summary, gate_ok) by running compare_paper in-process."""
    try:
        from alfred.tools import compare_paper
        buf = io.StringIO()
        sys.argv = ["compare_paper", "--hours", "48"]
        with redirect_stdout(buf):
            rc = compare_paper.main()
        out = buf.getvalue()
        # Pull the counters line for the digest
        counters = next((l.strip() for l in out.splitlines()
                         if "entrées divergentes" in l), "")
        return (counters or "parallel-run: pas de données"), rc == 0
    except Exception as e:
        return f"parallel-run: erreur ({e})", False


def notional_cap_review() -> str:
    """Déclencheur de re-test du cap notionnel $500 (R&D 2026-06-11,
    `backtests/backtest_liquidity_cap.py`) : quand la balance SENIOR
    atteint ~2× le capital du reset ($680.58), le cap mord la majorité
    des entrées et le cap liquidity-aware doit être re-validé."""
    try:
        with open(os.path.join(_REPO, "alfred", "data", "bots", "live",
                               "state.json")) as fh:
            st = json.load(fh)
        balance = st.get("capital", 0) + st.get("total_pnl", 0)
        if balance >= 1400:
            return (f"\n📐 Balance SENIOR ${balance:.0f} ≥ $1400 — re-tester le "
                    f"cap notionnel (python3 -m backtests.backtest_liquidity_cap)")
    except Exception:
        pass
    return ""


def main() -> int:
    _load_env()
    dry = "--dry-run" in sys.argv

    obs_line, obs_ok = observation_summary()
    pr_line, pr_ok = parallel_run_summary()

    status = "✅" if (obs_ok and pr_ok) else "⚠️"
    msg = (f"{status} ALFRED refacto — digest quotidien\n"
           f"Phase 2 (observation) : {'✓' if obs_ok else '✗'} {obs_line}\n"
           f"Phase 3 (parallel-run): {'✓ gate OK' if pr_ok else '✗ LOGIC divergences'} — {pr_line}"
           + notional_cap_review()
           + dashboard_footer())

    if dry:
        print(msg)
        return 0

    # Synchronous send — Notifier.send is fire-and-forget in a daemon thread,
    # which a short-lived cron process would kill before the HTTP call lands.
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    sent = False
    if token and chat:
        import urllib.request
        payload = json.dumps({"chat_id": chat,
                              "text": "[ALFRED] " + msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                sent = bool(json.loads(resp.read()).get("ok"))
        except Exception as e:
            print(f"telegram error: {e}", file=sys.stderr)
    print(f"{time.strftime('%F %T')} sent={sent} obs_ok={obs_ok} pr_ok={pr_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
