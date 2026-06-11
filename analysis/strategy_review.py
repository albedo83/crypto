#!/usr/bin/env python3
"""Strategy Drift Monitor — periodic review of bot trading patterns.

Runs monthly (1st of month, 8h Paris time). Computes per-(strategy, token,
direction) statistics over multiple time windows and flags drift in 5 categories:

  1. STRAT_DRIFT       per-strategy WR/PnL deviation from lifetime baseline
  2. TOKEN_TOXIC       (token, direction) pairs with consistent recent losses
  3. TOKEN_REVIVAL     previously-bad pairs showing recent improvement
  4. LIVE_VS_BT        live performance gap vs backtest expectation
  5. REGIME_SHIFT      macro context (btc_z 30d) deviation from recent norm

Outputs:
  - Telegram message (French, structured plain-text)
  - SUPERVISOR_REVIEW event in events DB for audit history

Usage:
    python3 -m analysis.strategy_review                # full report → Telegram
    python3 -m analysis.strategy_review --dry-run      # console only
    python3 -m analysis.strategy_review --db PATH      # custom DB path
    python3 -m analysis.strategy_review --no-telegram  # console + DB log
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ── Config ────────────────────────────────────────────────────────────
# Depuis 2026-06-11 : trades du bot Alfred (schéma identique au legacy).
DEFAULT_BOT = "live"   # SENIOR ; --bot junior pour le testeur
DEFAULT_DB = "/home/crypto/alfred/data/bots/{bot}/bot.db"
DEFAULT_STATE = "/home/crypto/alfred/data/bots/{bot}/state.json"
ENV_FILE = "/home/crypto/.env"
HTTP_TIMEOUT = 15

# Window sizes (days)
RECENT_DAYS = 30
MEDIUM_DAYS = 90
LIFETIME_DAYS = 365

# Drift thresholds
MIN_TRADES_RECENT = 3        # need at least N recent trades to flag
MIN_TRADES_LIFETIME = 8      # need at least N lifetime trades to baseline
WR_DRIFT_PP = 12.0           # ≥12pp WR drop = drift
RECENT_PNL_TOXIC_USD = -8.0  # recent sum below this = toxic flag
WR_REVIVAL_PP = 18.0         # ≥18pp WR gain vs lifetime = revival flag

# Live vs backtest threshold (annualized)
LIVE_BT_GAP_PP = 25.0        # ≥25pp gap = warn

# Macro regime
BTC_Z_SHIFT_THRESHOLD = 1.0  # |Δz| over 7d ≥ 1.0 = regime shift


# ── Helpers ────────────────────────────────────────────────────────────
def load_env(path: str) -> dict:
    """Tiny .env loader (avoids python-dotenv dep for this script)."""
    out = {}
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


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# ── Statistics ─────────────────────────────────────────────────────────
def fetch_trades(db_path: str) -> list[dict]:
    """Fetch all trades as dicts."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT symbol, strategy, direction, entry_time, exit_time, pnl_usdt, "
        "size_usdt, mae_bps, mfe_bps, reason FROM trades ORDER BY entry_time"
    ).fetchall()
    return [dict(r) for r in rows]


def filter_window(trades: list[dict], days: int) -> list[dict]:
    cutoff = days_ago(days)
    return [t for t in trades if (t.get("exit_time") or "") >= cutoff]


def stats_for(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "sum": 0.0, "avg": 0.0,
                "wins": 0, "losses": 0, "avg_win": 0.0, "avg_loss": 0.0}
    wins = [t for t in trades if t["pnl_usdt"] > 0]
    losses = [t for t in trades if t["pnl_usdt"] <= 0]
    s = sum(t["pnl_usdt"] for t in trades)
    return {
        "n": n,
        "wr": len(wins) / n * 100,
        "sum": s,
        "avg": s / n,
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": sum(t["pnl_usdt"] for t in wins) / max(1, len(wins)),
        "avg_loss": sum(t["pnl_usdt"] for t in losses) / max(1, len(losses)),
    }


# ── Drift detection ────────────────────────────────────────────────────
def detect_strat_drift(trades: list[dict]) -> list[dict]:
    """Per-strategy WR drift recent vs lifetime."""
    alerts = []
    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t["strategy"]].append(t)
    for strat, ts in by_strat.items():
        recent = filter_window(ts, RECENT_DAYS)
        if len(recent) < MIN_TRADES_RECENT:
            continue
        if len(ts) < MIN_TRADES_LIFETIME:
            continue
        s_recent = stats_for(recent)
        s_life = stats_for(ts)
        wr_drop = s_life["wr"] - s_recent["wr"]
        if wr_drop >= WR_DRIFT_PP:
            alerts.append({
                "type": "STRAT_DRIFT",
                "strat": strat,
                "msg": (f"{strat}: WR {s_life['wr']:.0f}% lifetime → "
                        f"{s_recent['wr']:.0f}% on last {s_recent['n']} trades "
                        f"({s_recent['n']} récents / {s_life['n']} total). "
                        f"Sum récent {s_recent['sum']:+.0f}$."),
                "severity": "warning",
            })
    return alerts


def detect_token_toxic(trades: list[dict]) -> list[dict]:
    """Per-(token, direction) consistent recent losses."""
    alerts = []
    recent = filter_window(trades, MEDIUM_DAYS)
    by_pair = defaultdict(list)
    for t in recent:
        by_pair[(t["symbol"], t["direction"], t["strategy"])].append(t)
    for (sym, direction, strat), ts in by_pair.items():
        if len(ts) < MIN_TRADES_RECENT:
            continue
        s = stats_for(ts)
        if s["sum"] >= RECENT_PNL_TOXIC_USD:
            continue
        # Cross-check lifetime — is this consistent or a fluke?
        all_pair = [t for t in trades if (t["symbol"] == sym
                                            and t["direction"] == direction
                                            and t["strategy"] == strat)]
        s_life = stats_for(all_pair)
        side = "LONG" if direction == "LONG" else "SHORT"
        verdict = "(confirme pattern lifetime)" if s_life["sum"] < 0 else "(récent isolé)"
        alerts.append({
            "type": "TOKEN_TOXIC",
            "strat": strat, "sym": sym, "dir": side,
            "msg": (f"{strat} {sym} {side}: {s['n']} trades sur {MEDIUM_DAYS}j, "
                    f"WR {s['wr']:.0f}%, sum {s['sum']:+.1f}$ {verdict}. "
                    f"Lifetime: {s_life['n']} trades, sum {s_life['sum']:+.0f}$."),
            "severity": "warning" if s_life["sum"] < 0 else "info",
        })
    return alerts


def detect_token_revival(trades: list[dict]) -> list[dict]:
    """Per-(token, direction) recent improvement vs negative lifetime."""
    alerts = []
    recent = filter_window(trades, MEDIUM_DAYS)
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[(t["symbol"], t["direction"], t["strategy"])].append(t)
    for (sym, direction, strat), ts in by_pair.items():
        if len(ts) < MIN_TRADES_LIFETIME:
            continue
        s_life = stats_for(ts)
        if s_life["sum"] >= 0:  # not a previously-bad pattern
            continue
        recent_pair = [t for t in recent
                       if t["symbol"] == sym and t["direction"] == direction
                       and t["strategy"] == strat]
        if len(recent_pair) < MIN_TRADES_RECENT:
            continue
        s_recent = stats_for(recent_pair)
        wr_gain = s_recent["wr"] - s_life["wr"]
        if s_recent["sum"] > 0 and wr_gain >= WR_REVIVAL_PP:
            side = "LONG" if direction == "LONG" else "SHORT"
            alerts.append({
                "type": "TOKEN_REVIVAL",
                "strat": strat, "sym": sym, "dir": side,
                "msg": (f"{strat} {sym} {side}: récent {s_recent['n']} trades, "
                        f"WR {s_recent['wr']:.0f}% (lifetime {s_life['wr']:.0f}%), "
                        f"sum {s_recent['sum']:+.1f}$ vs lifetime {s_life['sum']:+.0f}$."),
                "severity": "info",
            })
    return alerts


def detect_live_vs_bt(trades: list[dict], capital: float = 500.0) -> list[dict]:
    """Compare live cumulative PnL to backtest expectation for the deployment window."""
    if not trades:
        return []
    first_entry = min(t["entry_time"] for t in trades)
    days_live = (datetime.now(timezone.utc) - parse_iso(first_entry)).days
    if days_live < 14:
        return []  # too early
    live_pnl = sum(t["pnl_usdt"] for t in trades)
    live_pct = live_pnl / capital * 100
    # Attente backtest : la fenêtre ancrée de docs/backtests.md dont la date
    # de départ est la plus proche du déploiement live (re-baseline phase 6 :
    # le live SENIOR repart du 2026-06-10 — les ancres mensuelles glissent,
    # on prend la plus récente ≤ déploiement, sinon la plus proche).
    bt_path = "/home/crypto/docs/backtests.md"
    bt_pnl_pct = None
    deploy_dt = parse_iso(first_entry)
    best_gap_days = None
    if os.path.exists(bt_path):
        import re as _re
        with open(bt_path) as f:
            for line in f:
                m = _re.match(r"\|\s*depuis (\d{4}-\d{2}-\d{2})\s*\|", line)
                if not m:
                    continue
                try:
                    anchor_dt = datetime.fromisoformat(m.group(1) + "T00:00:00+00:00")
                except ValueError:
                    continue
                gap_days = abs((deploy_dt - anchor_dt).days)
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) >= 6 and (best_gap_days is None or gap_days < best_gap_days):
                    try:
                        bt_pnl_pct = float(parts[4].replace("%", "").replace("+", "").replace(" ", ""))
                        best_gap_days = gap_days
                    except ValueError:
                        pass
    if bt_pnl_pct is None:
        return []
    gap = live_pct - bt_pnl_pct
    if abs(gap) < LIVE_BT_GAP_PP:
        return []
    direction = "sous-perf" if gap < 0 else "sur-perf"
    return [{
        "type": "LIVE_VS_BT",
        "msg": (f"Live {live_pct:+.1f}% sur {days_live}j ({len(trades)} trades) "
                f"vs backtest {bt_pnl_pct:+.1f}%, écart {gap:+.1f}pp ({direction}). "
                f"Investiguer si gap > 30pp."),
        "severity": "warning" if gap < 0 else "info",
    }]


def detect_regime_shift(btc_candles_path: str = "/home/crypto/backtests/output/pairs_data/BTC_4h_3y.json") -> list[dict]:
    """Compute current btc_z from BTC 4h candles (independent of bot state)."""
    if not os.path.exists(btc_candles_path):
        return []
    try:
        with open(btc_candles_path) as f:
            raw = json.load(f)
    except Exception:
        return []
    closes = [float(c["c"]) for c in raw]
    n_lb = 30 * 6   # 30d × 6 candles/day
    n_zw = 180 * 6  # 180d window
    if len(closes) < n_lb + 30:
        return []
    rets = [closes[i] / closes[i - n_lb] - 1 for i in range(n_lb, len(closes))
            if closes[i - n_lb] > 0]
    if len(rets) < 30:
        return []
    past = rets[max(0, len(rets) - n_zw):]
    m = sum(past) / len(past)
    var = sum((r - m) ** 2 for r in past) / len(past)
    sd = math.sqrt(var) or 1.0
    btc_z = (rets[-1] - m) / sd
    if btc_z > 1.0:
        regime = "BULL marqué"
    elif btc_z > 0.3:
        regime = "bull modéré"
    elif btc_z < -1.0:
        regime = "BEAR marqué"
    elif btc_z < -0.3:
        regime = "bear modéré"
    else:
        regime = "neutre"
    s1_mult = max(0.3, min(2.5, 1 + 0.5 * max(-2.5, min(2.5, btc_z))))
    s89_mult = max(0.3, min(2.5, 1 - 0.5 * max(-2.5, min(2.5, btc_z))))
    return [{
        "type": "REGIME_SHIFT",
        "msg": (f"BTC z_30d = {btc_z:+.2f} ({regime}). "
                f"Modulator multipliers: S1×{s1_mult:.2f}, S8/S9×{s89_mult:.2f}."),
        "severity": "info",
    }]


# ── Report formatting ──────────────────────────────────────────────────
def format_report(alerts: list[dict], n_trades: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Strategy Drift Monitor — {now}",
             f"({n_trades} trades en historique)\n"]

    by_type = defaultdict(list)
    for a in alerts:
        by_type[a["type"]].append(a)

    sections = [
        ("STRAT_DRIFT",   "🔴 Dérive par stratégie"),
        ("TOKEN_TOXIC",   "🟡 Tokens toxiques (token, direction, strat)"),
        ("TOKEN_REVIVAL", "🟢 Revivals (token précédemment perdant qui remonte)"),
        ("LIVE_VS_BT",    "📐 Live vs Backtest"),
        ("REGIME_SHIFT",  "🌐 Régime macro"),
    ]
    any_alerts = False
    for tag, header in sections:
        if not by_type[tag]:
            continue
        lines.append(f"{header}:")
        for a in by_type[tag]:
            sev = "⚠" if a["severity"] == "warning" else "·"
            lines.append(f"  {sev} {a['msg']}")
        lines.append("")
        any_alerts = True

    if not any_alerts:
        lines.append("✓ Aucune dérive détectée. Tout est dans les normes statistiques.")

    lines.append("")
    next_review = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    lines.append(f"Prochaine revue: {next_review}")
    return "\n".join(lines)


# ── Telegram + persistence ────────────────────────────────────────────
def send_telegram(text: str, token: str, chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                return True
            print(f"[review] Telegram error: {body.get('description')}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[review] Telegram send failed: {e}", file=sys.stderr)
        return False


def log_event(db_path: str, alerts: list[dict], report: str) -> None:
    if not os.path.exists(db_path):
        return
    try:
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), "STRATEGY_REVIEW", "",
             json.dumps({"n_alerts": len(alerts),
                         "alerts": [{"type": a["type"], "severity": a["severity"]}
                                    for a in alerts],
                         "report_excerpt": report[:500]})),
        )
        db.commit()
    except Exception as e:
        print(f"[review] DB event log failed: {e}", file=sys.stderr)


# ── Main ───────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bot", default=DEFAULT_BOT,
                   help="Bot Alfred ciblé (live|junior|paper)")
    p.add_argument("--db", default=None, help="SQLite DB path (override --bot)")
    p.add_argument("--dry-run", action="store_true", help="No Telegram, no DB log")
    p.add_argument("--no-telegram", action="store_true", help="Skip Telegram, keep DB log")
    p.add_argument("--capital", type=float, default=None,
                   help="Capital de référence (défaut : capital du state.json du bot)")
    args = p.parse_args()
    if args.db is None:
        args.db = DEFAULT_DB.format(bot=args.bot)
    if args.capital is None:
        # Source de vérité = state.json du bot (capital post-reset migration)
        try:
            with open(DEFAULT_STATE.format(bot=args.bot)) as fh:
                args.capital = float(json.load(fh).get("capital", 500.0))
        except Exception:
            args.capital = 500.0

    env = load_env(ENV_FILE)
    tg_token = env.get("TG_BOT_TOKEN", os.environ.get("TG_BOT_TOKEN", ""))
    tg_chat = env.get("TG_CHAT_ID", os.environ.get("TG_CHAT_ID", ""))

    print(f"[review] Reading {args.db}...", file=sys.stderr)
    trades = fetch_trades(args.db)
    print(f"[review] {len(trades)} trades loaded.", file=sys.stderr)

    alerts = []
    alerts.extend(detect_strat_drift(trades))
    alerts.extend(detect_token_toxic(trades))
    alerts.extend(detect_token_revival(trades))
    alerts.extend(detect_live_vs_bt(trades, capital=args.capital))
    alerts.extend(detect_regime_shift())

    report = format_report(alerts, len(trades))
    print(report)

    if args.dry_run:
        print("\n[review] --dry-run, no Telegram, no DB event.", file=sys.stderr)
        return 0

    log_event(args.db, alerts, report)

    if args.no_telegram or not tg_token or not tg_chat:
        if not (tg_token and tg_chat):
            print("[review] TG creds missing, skipping Telegram.", file=sys.stderr)
        return 0

    sent = send_telegram(report, tg_token, tg_chat)
    if sent:
        print("[review] Telegram sent.", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
