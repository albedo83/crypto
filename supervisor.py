#!/usr/bin/env python3
"""Supervisor — LLM-based daily review of the Hyperliquid bots.

Reads /api/state and related endpoints from each running bot, assembles a
structured context, asks Claude to analyze it, and ships the report via
Telegram. Observation + suggestions only — never writes anything to the
bot's config or state.

Usage:
    ./supervisor.py                # daily mode, real API + Telegram
    ./supervisor.py --dry-run      # fetch + print, no Claude call, no Telegram
    ./supervisor.py --no-telegram  # Claude call but print to stdout
    ./supervisor.py --model MODEL  # override SUPERVISOR_MODEL

Design rules:
- No import from analysis.bot.* — total runtime isolation from the live bot.
- Read-only: reads /api/state, /api/trades, /api/health, /api/pnl via HTTP.
- Kill-switch: set SUPERVISOR_ENABLED=0 in .env to disable without editing cron.
- Audit: every report is logged into analysis/output/reversal_ticks.db (events).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
LOG_DB = os.path.join(REPO_ROOT, "analysis", "output", "reversal_ticks.db")

STATIC_CONTEXT_FILES = [
    "CLAUDE.md",
    "docs/bot.md",
    "docs/backtests.md",
]

# Bots to supervise. Matches admin_config.json when present but stays
# self-contained so the supervisor works even if that file is missing.
#
# `notes` is a free-form status string passed to Claude so it can frame
# anomalies correctly. Use it to flag idle / disabled / test instances
# that would otherwise look suspicious (e.g. low trade count, 100% WR).
BOTS = [
    {"port": 8097, "label": "Paper", "mode": "paper",
     "notes": "primary paper instance, full production config"},
    {"port": 8098, "label": "Live",  "mode": "live",
     "notes": "real capital $300 (270 initial + 30 DCA), full production config"},
    {"port": 8099, "label": "Junior",  "mode": "paper",
     "notes": "DISABLED — running as paper placeholder, ignore P&L/trade counts"},
]

HTTP_TIMEOUT = 6  # seconds

SYSTEM_PROMPT = """\
Tu supervises un bot de trading automatique sur Hyperliquid DEX (paper + live).
Ton rôle: détecter dérives, régime shifts, anomalies. Pas micro-optimiser.

RÈGLES STRICTES (non négociables):
1. Ne JAMAIS recommander de réactiver TOTAL_LOSS_CAP, LOSS_STREAK_THRESHOLD,
   signal quarantine, ou exposure cap. Ces protections ont été explicitement
   désactivées en v11.3.0 après backtests montrant -65% à -99% de compounding
   destruction. Tu peux les mentionner seulement si tu cites 3+ métriques
   concrètes qui contredisent les backtests v11.3.0.
2. Ne pas paniquer sur 1 mauvaise journée. Les tendances comptent sur 7-30j.
3. Asymétrie du compounding: retirer des gagnants coûte plus cher que laisser
   passer quelques perdants. Être conservateur sur les suggestions restrictives.
4. Toujours citer les valeurs exactes des endpoints. Pas d'estimation.
5. Les filtres S10 v11.3.4 (SHORT-only + whitelist 13 tokens) sont régime-
   dépendants: si S10 bleeds 30j consécutifs, flip le kill-switch via
   S10_ALLOW_LONGS=True + S10_ALLOWED_TOKENS=set(ALL_SYMBOLS) dans config.py.

FORMAT DE SORTIE — réponds EXCLUSIVEMENT en JSON valide, rien avant, rien après:
{
  "health": "green" | "yellow" | "red",
  "summary": "<=500 chars, résumé exécutif EN FRANÇAIS",
  "bilan": {
    "days_live": <int>,
    "pnl_realized": <number>,
    "pnl_pct": <number>,
    "trades": <int>,
    "wr": <float 0-1>,
    "positions_open": <int>,
    "unrealized_bps_sum": <number>,
    "backtest_expected_pnl": <number>,
    "backtest_expected_pct": <number>,
    "vs_backtest_ratio": <float>,
    "regime_note": "<=150 chars — regime actuel vs attentes"
  },
  "key_metrics": {
    "live_pnl_24h": <number>,
    "live_balance": <number>,
    "live_drawdown_pct": <number>,
    "live_positions": <int>,
    "live_wr_recent": <float 0-1>
  },
  "anomalies": [
    {"severity": "info|warn|alert", "signal": "S5|S9|etc|global",
     "detail": "<=200 chars EN FRANÇAIS"}
  ],
  "suggestions": [
    {"action": "<=200 chars EN FRANÇAIS",
     "rationale": "<=300 chars EN FRANÇAIS",
     "urgency": "now|this_week|later"}
  ],
  "next_check": "daily" | "hourly"
}

**LANGUE : TOUT le contenu textuel doit être en français.** Les noms de champs
JSON (health, summary, anomalies, etc.) restent en anglais (convention), mais
les valeurs textuelles (summary, detail, action, rationale) sont obligatoirement
en français. Pas d'anglais dans les phrases — pas de "dry powder", "monitor",
"concern", "review", etc. Utilise "cash disponible", "surveiller", "préoccupation",
"examiner", etc. Termes techniques (bps, P&L, WR, drawdown, S5, S10) autorisés.

Max 5 anomalies, max 3 suggestions. Concision > exhaustivité. Si tout va
bien: health=green, anomalies=[], suggestions=[].
"""

# ── .env loader ────────────────────────────────────────────────────────


def load_env() -> None:
    """Load .env into os.environ without overwriting existing vars."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


# ── Bot HTTP client (auth + fetch) ─────────────────────────────────────


class BotClient:
    """Minimal HTTP client for the bot's authenticated endpoints.

    Uses /login with DASHBOARD_USER/DASHBOARD_PASS (form-encoded) to obtain
    the HMAC-signed session cookie, then reuses it for /api/* GETs.
    """

    def __init__(self, port: int, user: str, password: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.user = user
        self.password = password
        self.cookie: str | None = None

    def _login(self) -> bool:
        """POST /login, intercept the 303 redirect to capture the session cookie.

        Uses a custom opener that raises on any redirect so we can read
        Set-Cookie from the 303 response before urllib follows /paper/.
        """
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise urllib.error.HTTPError(
                    req.full_url, code, msg, headers, fp)

        opener = urllib.request.build_opener(_NoRedirect())
        data = urllib.parse.urlencode(
            {"username": self.user, "password": self.password}
        ).encode()
        req = urllib.request.Request(
            f"{self.base}/login",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            opener.open(req, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                for header in (e.headers.get_all("Set-Cookie") or []):
                    if header.startswith("session="):
                        self.cookie = header.split(";", 1)[0].split("=", 1)[1]
                        return True
            # 401 bad creds or 5xx: fall through to return False
        except Exception as e:
            print(f"[supervisor] login error {self.base}: {e}", file=sys.stderr)
        return False

    def fetch(self, path: str, _retry: bool = False) -> Any:
        """Authenticated GET. On 401, re-login and retry once (`_retry=True`
        prevents unbounded recursion if the bot keeps rejecting the cookie).
        """
        if not self.cookie:
            if not self._login():
                return None
        req = urllib.request.Request(
            f"{self.base}{path}",
            headers={"Cookie": f"session={self.cookie}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401 and not _retry:
                self.cookie = None
                if self._login():
                    return self.fetch(path, _retry=True)
            return None
        except Exception:
            return None


def fetch_bot_state(bot: dict, user: str, password: str) -> dict:
    """Fetch all relevant endpoints for one bot. Returns a dict with any
    keys that could be retrieved (the bot may be offline or auth broken)."""
    client = BotClient(bot["port"], user, password)
    out = {
        "label": bot["label"],
        "port": bot["port"],
        "mode": bot["mode"],
        "notes": bot.get("notes", ""),
        "online": False,
    }
    health = client.fetch("/api/health")
    if health is None:
        out["error"] = "Unreachable or unauthorized"
        return out
    out["online"] = True
    out["health"] = health
    for path, key in [
        ("/api/state", "state"),
        ("/api/trades", "trades"),
        ("/api/pnl", "pnl"),
    ]:
        data = client.fetch(path)
        if data is not None:
            out[key] = data
    return out


# ── Static context (docs) ──────────────────────────────────────────────


def load_static_context() -> str:
    """Read the docs that provide bot architecture & constraints context.

    This block is eligible for prompt caching — stable across runs for a
    given version, only changes when docs get updated.
    """
    chunks = []
    for rel in STATIC_CONTEXT_FILES:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            content = f.read()
        chunks.append(f"# ── {rel} ──\n\n{content}")
    return "\n\n".join(chunks)


def compress_bot_state(bot_data: dict) -> dict:
    """Reduce bot state payloads to what's useful for analysis.

    Drops noisy fields (per-symbol feature dumps, chart data) and keeps
    structured metrics, recent trades, and health signals.
    """
    compressed = {
        "label": bot_data["label"],
        "port": bot_data["port"],
        "mode": bot_data["mode"],
        "notes": bot_data.get("notes", ""),
        "online": bot_data["online"],
    }
    if not bot_data["online"]:
        compressed["error"] = bot_data.get("error", "unknown")
        return compressed

    state = bot_data.get("state") or {}
    compressed["health"] = bot_data.get("health")
    compressed["version"] = state.get("version")
    compressed["balance"] = state.get("balance")
    compressed["capital"] = state.get("capital")
    compressed["total_pnl"] = state.get("total_pnl")
    compressed["peak_balance"] = state.get("peak_balance")
    compressed["drawdown_pct"] = state.get("drawdown_pct")
    compressed["pnl_pct"] = state.get("pnl_pct")
    compressed["first_trade_date"] = state.get("first_trade_date")
    compressed["started_at"] = state.get("started_at")
    compressed["exchange_account"] = state.get("exchange_account")
    compressed["capital_utilization_pct"] = state.get("capital_utilization_pct")
    compressed["positions"] = state.get("positions")
    compressed["total_trades"] = state.get("total_trades")
    compressed["win_rate"] = state.get("win_rate")
    compressed["signal_drift"] = state.get("signal_drift")
    compressed["s10_health"] = state.get("s10_health")
    compressed["market"] = state.get("market")
    compressed["params"] = state.get("params")
    compressed["paused"] = state.get("paused")
    compressed["uptime_s"] = state.get("uptime_s")
    compressed["last_scan_s"] = state.get("last_scan_s")

    # Recent trades — keep last 30
    trades = bot_data.get("trades") or []
    if isinstance(trades, list):
        compressed["recent_trades"] = trades[-30:]

    # P&L curve — keep last 50 points (~50 days if daily close)
    pnl = bot_data.get("pnl") or []
    if isinstance(pnl, list):
        compressed["pnl_recent"] = pnl[-50:]

    return compressed


def build_user_prompt(bot_states: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"Rapport supervisor demandé {now}.\n\n"
        "État actuel des instances du bot. **Lis le champ `notes` de chaque "
        "entrée** : il indique le statut opérationnel (production vs "
        "disabled/test). Les bots marqués DISABLED doivent être exclus des "
        "métriques et ne pas générer d'anomalies.\n\n"
        "## Cible du rapport\n\n"
        "**Ce rapport est destiné au bot LIVE uniquement** (le seul avec "
        "du capital réel). Formate ta sortie en conséquence :\n\n"
        "- `summary` : parle du Live bot, pas de Paper/Bot2\n"
        "- `key_metrics` : métriques du Live uniquement "
        "(`live_pnl_24h`, `live_balance`, `live_drawdown_pct`, `live_positions`, "
        "`live_wr_recent`)\n"
        "- `anomalies` / `suggestions` : ciblent le comportement du Live\n\n"
        "**Paper bot** reste utile comme **baseline de comparaison** : "
        "si tu détectes une divergence Live vs Paper (ex: Live S5 WR 40% "
        "vs Paper S5 WR 70%), c'est une anomalie Live qui mérite d'être "
        "remontée. Mais ne liste pas les métriques Paper pour elles-mêmes. "
        "Utilise Paper comme référence contextuelle, pas comme sujet.\n\n"
        "## Section `bilan` — comparaison live vs backtest\n\n"
        "Remplis le champ `bilan` en **calculant** les comparaisons :\n\n"
        "1. `days_live` : jours depuis le premier trade (utilise `first_trade_date` "
        "du state)\n"
        "2. `pnl_realized` et `pnl_pct` : valeurs directes du state\n"
        "3. `unrealized_bps_sum` : somme des MFE actuels des positions ouvertes "
        "(optionnel, 0 si pas de positions)\n"
        "4. `backtest_expected_pnl` et `backtest_expected_pct` : le backtest 28m "
        "affiche +5000% soit ~+181%/an compounded ≈ **+9.1%/mois compounded**. "
        "Extrapole au prorata des `days_live` sur le capital initial Live "
        "(regarde `capital` dans state). Formule : "
        "`capital * ((1.091)^(days/30) - 1)`.\n"
        "5. `vs_backtest_ratio` : `pnl_realized / backtest_expected_pnl`. "
        "1.0 = aligné, <0.5 = sous-performance, >1.5 = surperformance.\n"
        "6. `regime_note` : **1 phrase** expliquant si l'écart vient du régime "
        "(ex: 'S10 idle 3j — pas de squeezes en vol élevée'), d'un drawdown, "
        "ou d'un bug. Context : `docs/backtests.md` montre que mars-avril 2026 "
        "est -16.3% (régime flat/bear) vs +93.5% sur 1m antérieur.\n\n"
        "Ne pas paniquer si `vs_backtest_ratio < 0.5` sur <30 jours : variance "
        "normale d'un petit échantillon. Signaler une anomalie seulement si "
        "<0.3 pendant 7+ jours consécutifs ou si drawdown > -20%.\n\n"
        "Analyse l'activité des 24-48h, détecte anomalies sur le Live, "
        "propose des suggestions concrètes.\n\n"
    )
    payload = json.dumps(bot_states, indent=2, default=str)
    return header + "```json\n" + payload + "\n```"


# ── Claude API ──────────────────────────────────────────────────────────


def call_claude(system_static: str, user_prompt: str, model: str) -> dict:
    """Call Anthropic messages API with prompt caching on the static block.

    Returns the parsed JSON report or raises on any error.
    """
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
            },
            {
                "type": "text",
                "text": system_static,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    # Extract text
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw = "".join(parts).strip()
    # Extract the outer JSON object even if Claude wraps it in code fences
    # or prepends/appends prose. Matches the first `{` and the last `}` —
    # survives trailing commentary, markdown fences, and "json" language tags.
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError(f"Claude did not return a JSON object:\n---\n{raw}")
    json_str = match.group(0)
    try:
        report = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned malformed JSON: {e}\n---\n{json_str}")

    # Attach API usage for cost tracking
    usage = getattr(resp, "usage", None)
    if usage:
        report["_usage"] = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        }
    report["_model"] = model
    return report


# ── Report formatting & output ──────────────────────────────────────────


EMOJI_HEALTH = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
EMOJI_SEVERITY = {"info": "ℹ️", "warn": "⚠️", "alert": "🚨"}
EMOJI_URGENCY = {"now": "🔥", "this_week": "📅", "later": "📝"}


def format_telegram(report: dict) -> str:
    """Plain-text Telegram message (no markdown).

    Markdown/HTML parse modes were tried but routinely choke on LLM-
    generated content that contains underscores (e.g. S10_ALLOW_LONGS),
    asterisks, or unbalanced formatting. Plain text renders cleanly via
    emoji + whitespace structure and never fails to parse.
    """
    lines = []
    hdr = EMOJI_HEALTH.get(report.get("health", ""), "⚪")
    lines.append(f"{hdr} SUPERVISOR DAILY")
    summary = (report.get("summary") or "").strip()
    if summary:
        lines.append("")
        lines.append(summary)

    bilan = report.get("bilan") or {}
    if bilan:
        lines.append("")
        lines.append("── Bilan ──")
        days = bilan.get("days_live", 0)
        pnl = bilan.get("pnl_realized", 0)
        pct = bilan.get("pnl_pct", 0)
        n = bilan.get("trades", 0)
        wr = bilan.get("wr", 0)
        pos = bilan.get("positions_open", 0)
        exp = bilan.get("backtest_expected_pnl", 0)
        exp_pct = bilan.get("backtest_expected_pct", 0)
        ratio = bilan.get("vs_backtest_ratio", 0)
        regime = bilan.get("regime_note", "")
        lines.append(f"  Jour {days} — P&L réalisé: ${pnl:+.2f} ({pct:+.1f}%)")
        lines.append(f"  {n} trades, WR {wr*100:.0f}%, {pos} positions ouvertes")
        lines.append(f"  Backtest attendu: ${exp:+.2f} ({exp_pct:+.1f}%) → ratio {ratio:.0%}")
        if regime:
            lines.append(f"  Régime: {regime}")

    km = report.get("key_metrics") or {}
    if km:
        lines.append("")
        for k, v in km.items():
            lines.append(f"  {k} = {v}")

    anomalies = report.get("anomalies") or []
    if anomalies:
        lines.append("")
        lines.append("── Anomalies ──")
        for a in anomalies[:5]:
            sev = EMOJI_SEVERITY.get(a.get("severity", ""), "•")
            sig = a.get("signal", "?")
            det = a.get("detail", "")
            lines.append(f"{sev} [{sig}] {det}")

    suggestions = report.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("── Suggestions ──")
        for s in suggestions[:3]:
            ur = EMOJI_URGENCY.get(s.get("urgency", ""), "•")
            act = s.get("action", "")
            rat = s.get("rationale", "")
            lines.append(f"{ur} {act}")
            if rat:
                lines.append(f"   → {rat}")

    usage = report.get("_usage") or {}
    if usage:
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        lines.append("")
        lines.append(
            f"[{in_tok}in / {out_tok}out / cache={cache_read}] "
            f"{report.get('_model', '?')}"
        )

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3990] + "\n…(truncated)"
    return msg


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    """Send a plain-text message. Parses the JSON body to detect silent
    failures (Telegram returns HTTP 200 with ok:false on parse errors)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text}
    ).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                return True
            print(f"[supervisor] Telegram API error: {body.get('description')}",
                  file=sys.stderr)
            return False
    except Exception as e:
        print(f"[supervisor] Telegram send failed: {e}", file=sys.stderr)
        return False


def log_event(db_path: str, event: str, data: dict) -> None:
    """Persist a supervisor event into the bot's events table."""
    if not os.path.exists(db_path):
        return
    try:
        db = sqlite3.connect(db_path, timeout=5)
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), event, None, json.dumps(data, default=str)),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[supervisor] Event log failed: {e}", file=sys.stderr)


# ── Orchestration ──────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + print context, no API call, no Telegram")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Call Claude but print report to stdout")
    parser.add_argument("--model", default=None,
                        help="Override SUPERVISOR_MODEL")
    args = parser.parse_args()

    load_env()

    if os.environ.get("SUPERVISOR_ENABLED", "1") == "0":
        print("[supervisor] SUPERVISOR_ENABLED=0 — exiting without action")
        return 0

    user = os.environ.get("DASHBOARD_USER", "")
    password = os.environ.get("DASHBOARD_PASS", "")
    if not user or not password:
        print("[supervisor] DASHBOARD_USER/DASHBOARD_PASS missing in .env", file=sys.stderr)
        return 1

    # 1. Fetch all bot states
    print(f"[supervisor] {datetime.now(timezone.utc).isoformat()} — fetching bot states")
    raw_states = []
    for bot in BOTS:
        data = fetch_bot_state(bot, user, password)
        raw_states.append(data)
        status = "ONLINE" if data.get("online") else "OFFLINE"
        print(f"  {bot['label']:<6} :{bot['port']} {status}")

    compressed = [compress_bot_state(s) for s in raw_states]

    # 2. Load static context
    static_ctx = load_static_context()
    print(f"[supervisor] static context: {len(static_ctx)} chars")

    # 3. Build user prompt
    user_prompt = build_user_prompt(compressed)
    print(f"[supervisor] user prompt: {len(user_prompt)} chars")

    if args.dry_run:
        print("\n=== STATIC CONTEXT (first 500 chars) ===")
        print(static_ctx[:500] + ("..." if len(static_ctx) > 500 else ""))
        print("\n=== USER PROMPT ===")
        print(user_prompt[:2000] + ("..." if len(user_prompt) > 2000 else ""))
        print("\n[supervisor] --dry-run: stopping before Claude call")
        return 0

    # 4. Call Claude
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[supervisor] ANTHROPIC_API_KEY missing in .env", file=sys.stderr)
        return 1

    model = args.model or os.environ.get("SUPERVISOR_MODEL", "claude-haiku-4-5")
    print(f"[supervisor] calling {model}...")
    try:
        report = call_claude(static_ctx, user_prompt, model)
    except Exception as e:
        err_msg = f"[supervisor] Claude call failed: {e}"
        print(err_msg, file=sys.stderr)
        # Best-effort telegram about the failure
        tg_token = os.environ.get("TG_BOT_TOKEN", "")
        tg_chat = os.environ.get("TG_CHAT_ID", "")
        if tg_token and tg_chat:
            send_telegram(f"🚨 Supervisor failed: {e}", tg_token, tg_chat)
        log_event(LOG_DB, "SUPERVISOR_ERROR", {"error": str(e)})
        return 1

    print(f"[supervisor] report health={report.get('health')}")

    # 5. Format and send
    msg = format_telegram(report)
    if args.no_telegram:
        print("\n=== REPORT ===")
        print(json.dumps(report, indent=2, default=str))
        print("\n=== TELEGRAM MESSAGE ===")
        print(msg)
    else:
        tg_token = os.environ.get("TG_BOT_TOKEN", "")
        tg_chat = os.environ.get("TG_CHAT_ID", "")
        if not tg_token or not tg_chat:
            print("[supervisor] TG_BOT_TOKEN/TG_CHAT_ID missing — printing report to stdout")
            print(msg)
        elif send_telegram(msg, tg_token, tg_chat):
            print("[supervisor] Telegram sent")
        else:
            print("[supervisor] Telegram failed — report on stdout:")
            print(msg)

    # 6. Log event
    log_event(LOG_DB, "SUPERVISOR_REPORT", report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
