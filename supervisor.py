#!/usr/bin/env python3
"""Supervisor — LLM-based daily review of the Hyperliquid LIVE bot (SENIOR).

Reads /api/state and related endpoints, assembles a structured context, asks
Claude for a SYNTHETIC analysis and logs it as a SUPERVISOR_REPORT event in
alfred/data/market.db. The admin page /master surfaces it (no Telegram).
Observation + suggestions only — never writes to the bot's config or state.

Usage:
    ./supervisor.py                # daily mode, real API + DB log
    ./supervisor.py --dry-run      # fetch + print, no Claude call, no DB write
    ./supervisor.py --no-write     # Claude call but print to stdout, no DB write
    ./supervisor.py --model MODEL  # override SUPERVISOR_MODEL

Design rules:
- No import from analysis.bot.* / alfred.* — total runtime isolation.
- Read-only on the bot: /api/state, /api/trades, /api/health, /api/pnl via HTTP.
- Kill-switch: set SUPERVISOR_ENABLED=0 in .env to disable without editing cron.
- Output: SUPERVISOR_REPORT event in alfred/data/market.db (INSERT court
  inter-process, sûr en WAL, timeout=5s) — lu et affiché par l'admin /master.
- Cible les bots Alfred (:8101/bot/<id>) ; le rapport ne couvre que SENIOR (live).
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
LOG_DB = os.path.join(REPO_ROOT, "alfred", "data", "market.db")
SENIOR_DB = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live", "bot.db")
SENIOR_DASHBOARD_URL = os.environ.get(
    "SENIOR_DASHBOARD_URL", "https://echonym.fr/alfred/bot/live/")

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
# Depuis 2026-06-11 les bots vivent dans Alfred (:8101, un process, un bot
# par préfixe /bot/<id>). SENIOR = cible principale du rapport.
ALFRED_HOST = "http://127.0.0.1:8101"
BOTS = [
    {"id": "live", "label": "SENIOR", "mode": "live",
     "notes": "bot perso de l'admin, capital réel $680.58 au reset du "
              "2026-06-10 (migration Alfred, remise à zéro de l'historique)"},
    {"id": "junior", "label": "JUNIOR", "mode": "live",
     "notes": "bot piloté par un testeur non-admin, capital réel $332.76 au "
              "reset du 2026-06-11, capital_cap $500 — faible historique, "
              "ne pas sur-interpréter les stats"},
    {"id": "paper", "label": "PAPER-ALFRED", "mode": "paper",
     "notes": "simulation $1000, baseline de comparaison du live"},
]

HTTP_TIMEOUT = 6  # seconds

SYSTEM_PROMPT = """\
Tu supervises le bot de trading LIVE (SENIOR) sur Hyperliquid (altcoins perp).
Rôle: détecter dérives, shifts de régime, anomalies. Pas de micro-optimisation.
Sortie SYNTHÉTIQUE, scannable d'un coup d'œil (affichée dans une console admin).

GARDE-FOUS (non négociables):
- Ne JAMAIS recommander de réactiver TOTAL_LOSS_CAP, LOSS_STREAK, signal
  quarantine, exposure cap (désactivés v11.3.0, -65% à -99% compounding).
- Ne pas paniquer sur 1 mauvaise journée (tendances sur 7-30j). Asymétrie du
  compounding: retirer des gagnants coûte plus cher que laisser passer des perdants.
- Chiffres EXACTS uniquement (endpoints API, CLAUDE.md, docs/backtests.md,
  docs/bot.md). Zéro hallucination — si tu n'as pas le chiffre, ne le cite pas.
- `last_scan_s` = secondes depuis le dernier scan (SCAN_INTERVAL=3600), normal
  entre 0 et ~3700. Anomalie seulement si > 5400.
- Positions OUVERTES ≠ trades CLOS: leurs MAE/MFE évoluent, ce ne sont pas des
  résultats. Ne cite pas un MAE de position ouverte comme une perte.
- REGISTRE ANTI-REPRISE: la section "teste et rejete" de docs/bot.md liste ~40
  hypothèses déjà rejetées en walk-forward (filtre régime BTC sur S5, OI delta à
  l'entrée, trailing/breakeven/ATR stops, MAE cry-uncle, sizing adaptatif WR,
  blacklist étendue, token rotation, pause selon régime, réduction sizing S9,
  vol_z min, etc.). Ne JAMAIS les re-suggérer, même reformulées. Si convaincu,
  cite 3+ métriques et dis "re-tester variant X" plutôt que "implémenter X".

FORMAT — réponds EXCLUSIVEMENT en JSON valide, rien avant, rien après:
{
  "health": "green" | "yellow" | "red",
  "summary": "<=280 chars, FR, l'essentiel",
  "bilan": {
    "days_live": <int>,
    "pnl_pct": <number>,
    "backtest_expected_pct": <number>,
    "vs_backtest_ratio": <float>,
    "regime_note": "<=120 chars FR — régime actuel vs attentes"
  },
  "positions": [
    {"symbol": "<TOKEN>",
     "etat": "<=160 chars FR concis — direction + stratégie, P&L latent $ et %, "
             "durée, situation vs stop, tendance. Termes techniques OK (MFE/MAE/bps/div)"}
  ],
  "points": [
    {"severity": "info|warn|alert", "text": "<=180 chars FR",
     "action": "<=160 chars FR, ou null si rien à faire"}
  ],
  "next_check": "daily" | "hourly"
}

`positions` : UNE entrée par position ouverte du state (TOUTES). État concis,
termes techniques OK (MFE/MAE/bps/div), pas besoin de les traduire.

`points` fusionne anomalies ET suggestions, du plus urgent au moins (max 4).
Tout le texte des valeurs en français (noms de champs en anglais). Pas d'anglais
dans les phrases ("surveiller" pas "monitor", etc.). Termes techniques (bps, P&L,
WR, drawdown, S5) autorisés. Concision > exhaustivité. Si tout va bien:
health=green, points=[].
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

    def __init__(self, bot_id: str, user: str, password: str) -> None:
        self.host = ALFRED_HOST                  # login à la racine d'Alfred
        self.base = f"{ALFRED_HOST}/bot/{bot_id}"  # APIs sous /bot/<id>/
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
            f"{self.host}/login",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            opener.open(req, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                for header in (e.headers.get_all("Set-Cookie") or []):
                    if header.startswith("alfred_session="):
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
            headers={"Cookie": f"alfred_session={self.cookie}"},
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
    client = BotClient(bot["id"], user, password)
    out = {
        "label": bot["label"],
        "id": bot["id"],
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


def parse_backtest_for(bot_label: str, capital_int: int) -> dict:
    """Extract two reference points from docs/backtests.md for divergence checks:

      - `recent_30d`: the rolling "1 mois" window for the matching capital
        (latest 30 days under current bot params)
      - `since_start`: the "depuis YYYY-MM-DD (<label>)" anchor for the
        matching capital (deployment-date anchor, see BOT_DEPLOYMENTS in
        backtests/backtest_rolling.py)

    Returns a dict with pnl_pct / dd_pct / n_trades / best_strat for each,
    or {} on parse failure (Claude falls back to the static extrapolation).
    """
    path = os.path.join(REPO_ROOT, "docs", "backtests.md")
    if not os.path.exists(path):
        return {}
    label_lc = bot_label.lower()
    cap_str = f"${capital_int}" if capital_int < 1000 else f"${capital_int:,}".replace(",", " ")
    out: dict = {}
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line.startswith("|"):
                    continue
                cols = [c.strip() for c in line.split("|")[1:-1]]
                # Columns: Window | Start | Capital | Balance | PnL | PnL% | DD% | Trades | WR | Strat
                if len(cols) < 10 or cols[2] != cap_str:
                    continue
                window = cols[0]
                try:
                    pnl_pct = float(cols[5].rstrip("%"))
                    dd_pct = float(cols[6].rstrip("%"))
                    n_trades = int(cols[7])
                    strat = cols[9]
                except (ValueError, IndexError):
                    continue
                row = {"pnl_pct": pnl_pct, "dd_pct": dd_pct,
                       "n_trades": n_trades, "best_strat": strat}
                if window == "1 mois":
                    out["recent_30d"] = row
                elif window == f"depuis 2026-03-26 ({label_lc})" or (
                        window.startswith("depuis ") and window.endswith(f"({label_lc})")):
                    out["since_start"] = row
    except Exception:
        return {}
    return out


def compress_bot_state(bot_data: dict) -> dict:
    """Reduce bot state payloads to what's useful for analysis.

    Drops noisy fields (per-symbol feature dumps, chart data) and keeps
    structured metrics, recent trades, and health signals.
    """
    compressed = {
        "label": bot_data["label"],
        "id": bot_data["id"],
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

    # Backtest reference points for THIS bot (parsed from docs/backtests.md).
    # Lets Claude compute live-vs-backtest divergence on actual numbers
    # instead of extrapolating an annualized rate. recent_30d = rolling
    # "1 mois" at this bot's capital. since_start = bot's deployment-date
    # anchor (added by backtests/backtest_rolling.py BOT_DEPLOYMENTS).
    cap = state.get("capital")
    if cap:
        ref = parse_backtest_for(bot_data["label"], int(cap))
        if ref:
            compressed["backtest_ref"] = ref

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
        "Couvre UNIQUEMENT le bot Live (SENIOR). Lis le champ `notes`. "
        "Paper/Junior, s'ils figurent, ne sont ni mentionnés ni comparés.\n\n"
        "## `bilan` — live vs backtest (calcule)\n"
        "- `days_live` : jours depuis `first_trade_date`.\n"
        "- `pnl_pct` : valeur directe du state.\n"
        "- `backtest_expected_pct` : **`backtest_ref.since_start.pnl_pct`** "
        "(anchor déploiement, même fenêtre, capital exact). Fallback si absent : "
        "extrapolation `((1.091)^(days/30) - 1) * 100`.\n"
        "- `vs_backtest_ratio` : `pnl_pct / backtest_expected_pct` "
        "(≈1 aligné, <0.5 sous-perf, >1.5 surperf).\n"
        "- `regime_note` : 1 phrase ; mentionne la dérive vs "
        "`backtest_ref.recent_30d.pnl_pct`.\n\n"
        "## `positions` — état de CHAQUE position ouverte\n"
        "Une entrée par position du champ `positions` du state (TOUTES, pas "
        "seulement la dernière). Pour chacune, état CONCIS : direction + stratégie, "
        "P&L latent en $ ET %, depuis combien d'heures, situation vs stop "
        "catastrophe, tendance récente. Termes techniques OK (MFE, MAE, bps, div) — "
        "pas besoin de les traduire. Si aucune position ouverte : `positions`=[].\n\n"
        "## `points` (max 4, anomalies + suggestions fusionnées)\n"
        "Du plus urgent au moins. `action`=null si rien à faire. "
        "Flag warn si |pnl_pct - backtest_ref.since_start.pnl_pct| > 10pp "
        "persistant 7j+. Flag alert si écart > 20pp OU drawdown > -20%. "
        "Si tout est nominal : `points`=[].\n\n"
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


# ── Output (DB log) ─────────────────────────────────────────────────────


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


def format_supervisor_tg(report: dict) -> str:
    """Message Telegram condensé (SENIOR). Texte brut, pas de markdown."""
    emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(report.get("health", ""), "⚪")
    lines = [f"{emoji} SUPERVISOR SENIOR"]
    s = (report.get("summary") or "").strip()
    if s:
        lines.append(s)
    b = report.get("bilan") or {}
    if b:
        lines.append(f"Jour {b.get('days_live', '?')} · live {b.get('pnl_pct', '?')}% · "
                     f"BT {b.get('backtest_expected_pct', '?')}% · ratio "
                     f"{b.get('vs_backtest_ratio', '?')}")
        if b.get("regime_note"):
            lines.append(b["regime_note"])
    pos = report.get("positions") or []
    if pos:
        lines.append("")
        lines.append("Positions ouvertes :")
        for p in pos:
            lines.append(f"• {p.get('symbol', '?')} : {p.get('etat', '')}")
    sev = {"info": "ℹ️", "warn": "⚠️", "alert": "🚨"}
    for p in (report.get("points") or [])[:4]:
        lines.append(f"{sev.get(p.get('severity', ''), '•')} {p.get('text', '')}")
        if p.get("action"):
            lines.append(f"   → {p['action']}")
    lines.append("")
    lines.append(f"📊 {SENIOR_DASHBOARD_URL}")
    msg = "\n".join(lines)
    return msg[:3990] + ("\n…" if len(msg) > 3990 else "")


# ── Orchestration ──────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + print context, no API call, no DB write")
    parser.add_argument("--no-write", action="store_true",
                        help="Call Claude but print report to stdout, no DB write")
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
        print(f"  {bot['label']:<12} ({bot['id']}) {status}")

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
        print(f"[supervisor] Claude call failed: {e}", file=sys.stderr)
        log_event(LOG_DB, "SUPERVISOR_ERROR", {"error": str(e)})
        return 1

    print(f"[supervisor] report health={report.get('health')}")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    # Output: persist as SUPERVISOR_REPORT (read by admin /master). No Telegram.
    if args.no_write:
        print("[supervisor] --no-write: rien écrit en DB, pas d'envoi TG")
    else:
        log_event(LOG_DB, "SUPERVISOR_REPORT", report)
        if report.get("_usage"):
            sys.path.insert(0, REPO_ROOT)
            import ai_cost as _aic
            log_event(SENIOR_DB, "AI_COST", _aic.cost_event(
                "supervisor", report.get("_model", model), report["_usage"]))
        print("[supervisor] SUPERVISOR_REPORT loggé")
        # Telegram retiré (2026-07-01) — rapport trop lourd pour TG ; reste dans
        # l'historique (event SUPERVISOR_REPORT / dashboard).
    return 0


if __name__ == "__main__":
    sys.exit(main())
