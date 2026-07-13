"""Daily live-vs-BT divergence report → BT_DIVERGENCE event per bot.

Runs the SAME comparison engine as btlive (analysis/btlive_compare — single
source of truth) headless for SENIOR + PAPER, then writes one structured
BT_DIVERGENCE event into each bot's events table. The per-bot dashboard reads
these events and renders them in a daily "divergences BT" log window.

Doctrine: shows ALL divergences, including the justified ones (cooldown, slot,
opposite-position, gate skip) — each labelled with its cause. The point is to
watch the live bot never silently drift from what the strategy (BT) endorses.

Read-only on the bots except a single INSERT into each bot's events table
(SQLite file lock coordinates with the running bot; daily cadence = no
contention). Never restarts, never mutates state.

Run:
    python3 -m analysis.bt_divergence            # senior + paper
    python3 -m analysis.bt_divergence --dry-run  # compute + print, no DB write

Cron (daily, after supervisor):
    30 8 * * * /home/crypto/.venv/bin/python3 -m analysis.bt_divergence \
        >> /home/crypto/analysis/output/bt_divergence.log 2>&1
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from analysis import btlive_compare as B  # noqa: E402

BOTS = ["live", "paper"]  # SENIOR + PAPER ; junior/baby exclus (opérateur tiers)
MAX_LIST = 25  # cap la taille des listes dans l'event (log compact)


def _window_start(bot_key: str, db_path: str, state_path: str) -> tuple[dt.datetime, str]:
    """max(deployment, perf_track_start_ts, earliest trade) — même règle que btlive."""
    from backtests.backtest_rolling import BOT_DEPLOYMENTS
    deploy_map = {b: d for b, d in BOT_DEPLOYMENTS}
    start_dt = dt.datetime.fromisoformat(deploy_map[bot_key]).replace(tzinfo=dt.timezone.utc)
    src = "deployment"
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                perf_ts = float(json.load(f).get("_perf_track_start_ts", 0) or 0)
            if perf_ts > 0:
                pd = dt.datetime.fromtimestamp(perf_ts, dt.timezone.utc)
                if pd > start_dt:
                    start_dt, src = pd, "perf_reset"
        except Exception:
            pass
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT MIN(entry_time) FROM trades").fetchone()
        conn.close()
        if row and row[0]:
            md = dt.datetime.fromisoformat(row[0].replace('Z', '+00:00'))
            if md.tzinfo is None:
                md = md.replace(tzinfo=dt.timezone.utc)
            if md > start_dt:
                start_dt, src = md, "earliest_trade"
    except Exception:
        pass
    # Ancre de reset externe (fichier que le bot ne touche jamais → pas de clobber).
    # Posée par un clean-slate manuel : exclut les events pré-reset (cooldown, SKIP)
    # que _perf_track_start_ts figé ne peut plus dater. Voir bt_reset_anchor.json.
    anchor_path = os.path.join(os.path.dirname(db_path), "bt_reset_anchor.json")
    if os.path.exists(anchor_path):
        try:
            with open(anchor_path) as f:
                a_ts = float(json.load(f).get("reset_ts", 0) or 0)
            if a_ts > 0:
                ad = dt.datetime.fromtimestamp(a_ts, dt.timezone.utc)
                if ad > start_dt:
                    start_dt, src = ad, "reset_anchor"
        except Exception:
            pass
    return start_dt, src


def compute_divergence(bot_key: str, now_ts: float) -> tuple[dict, str]:
    """Build the divergence report for one bot. Returns (report, db_path)."""
    output_rel, key, defcap = B.BOTS[bot_key]
    output_dir = os.path.join(PROJECT_ROOT, output_rel)
    db_path = os.path.join(output_dir, "bot.db")
    state_path = os.path.join(output_dir, "state.json")

    start_dt, start_src = _window_start(bot_key, db_path, state_path)
    start_cap = defcap
    start_iso = start_dt.isoformat()

    live = [t for t in B.read_live_trades(db_path) if t["entry_iso"] >= start_iso]

    report = {
        "generated_ts": now_ts,
        "bot": bot_key,
        "window_start": start_dt.date().isoformat(),
        "window_start_src": start_src,
        "start_cap": round(start_cap, 2),
        "live_n": len(live),
        "bt_n": 0, "matched_n": 0,
        "live_pnl": round(sum(t["pnl"] for t in live), 2),
        "bt_pnl": 0.0, "gap": 0.0,
        "live_only": [], "bt_only_by_cause": [], "matched": [],
        "bt_error": None,
    }

    # Backtest side (heavy) — fail-open : si le BT ne peut tourner (fenêtre vide,
    # données manquantes), on rapporte quand même le côté live.
    try:
        _res, bt = B.run_backtest_for_period(start_dt, start_cap, PROJECT_ROOT)
    except SystemExit as e:
        report["bt_error"] = f"bt_exit: {e}"
        return report, db_path
    except Exception as e:
        report["bt_error"] = f"{type(e).__name__}: {e}"
        return report, db_path

    pairs = B.match_trades(live, bt)
    report["bt_n"] = len(bt)
    report["matched_n"] = len(pairs)
    report["bt_pnl"] = round(sum(b["pnl"] for b in bt), 2)
    report["gap"] = round(report["live_pnl"] - report["bt_pnl"], 2)
    # Equity BT mark-to-market (inclut les positions ouvertes via mtm_final) —
    # base comparable à l'equity live SENIOR/paper (même reset, même start_cap).
    # Le dashboard assemble le trio BT / SENIOR / paper (les 2 derniers en direct).
    report["bt_equity"] = round(start_cap + _res.get("pnl", 0.0), 2)
    report["bt_pnl_pct"] = round(_res.get("pnl_pct", 0.0), 2)
    report["bt_dd_pct"] = round(_res.get("max_dd_pct", 0.0), 2)

    # LIVE-ONLY : le bot a pris, le BT non (les plus importantes — dérive ?)
    live_only = [t for t in live if t["matched_to"] is None]
    live_only.sort(key=lambda t: abs(t["pnl"]), reverse=True)
    for t in live_only[:MAX_LIST]:
        report["live_only"].append({
            "coin": t["coin"], "strat": t["strat"],
            "dir": "L" if t["dir"] == 1 else "S",
            "entry": B.fmt_ts(t["entry_ts"]), "pnl": round(t["pnl"], 2),
            "reason": t["reason"],
        })
    report["live_only_n"] = len(live_only)

    # BT-ONLY : le BT a pris, le bot non — attribué à sa cause (justifiée incluse)
    bt_only = [b for b in bt if b["matched_to"] is None]
    skip = B.read_skip_events(db_path, start_dt.timestamp())
    causes = B.classify_bt_only_misses(bt_only, live, B._cooldown_hours(), skip)
    for cause, d in sorted(causes.items(), key=lambda kv: -abs(kv[1]["pnl"])):
        report["bt_only_by_cause"].append({
            "cause": cause, "n": d["n"], "pnl": round(d["pnl"], 2),
            "examples": d["examples"],
        })
    report["bt_only_n"] = len(bt_only)

    # MATCHED avec Δ : même trade, issue différente (timing/raison de sortie)
    matched = []
    for i, j in pairs:
        lt, bt_t = live[i], bt[j]
        dpnl = lt["pnl"] - bt_t["pnl"]
        dexit_h = (lt["exit_ts"] - bt_t["exit_ts"]) / 3.6e6
        if abs(dpnl) >= 1.0 or lt["reason"] != bt_t["reason"]:
            matched.append({
                "coin": lt["coin"], "strat": lt["strat"],
                "dir": "L" if lt["dir"] == 1 else "S",
                "entry": B.fmt_ts(lt["entry_ts"]),
                "live_pnl": round(lt["pnl"], 2), "bt_pnl": round(bt_t["pnl"], 2),
                "dpnl": round(dpnl, 2),
                "live_reason": lt["reason"], "bt_reason": bt_t["reason"],
                "dexit_h": round(dexit_h, 1),
            })
    matched.sort(key=lambda m: abs(m["dpnl"]), reverse=True)
    report["matched"] = matched[:MAX_LIST]
    report["matched_div_n"] = len(matched)

    return report, db_path


def write_event(db_path: str, report: dict) -> None:
    db = sqlite3.connect(db_path, timeout=15)
    try:
        db.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (report["generated_ts"], "BT_DIVERGENCE", None, json.dumps(report)))
        db.commit()
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute + print, no DB write")
    ap.add_argument("--skip-refresh", action="store_true", help="skip data refresh")
    args = ap.parse_args()

    now_ts = time.time()

    # Refresh une seule fois (partagé par les deux bots)
    if not args.skip_refresh:
        age = B.data_age_hours(PROJECT_ROOT)
        if age is None or age > B.DATA_STALENESS_THRESHOLD_HOURS:
            print(f"[refresh] BTC candle {age}h old → refreshing...", flush=True)
            B.refresh_backtest_data(PROJECT_ROOT)
        else:
            print(f"[refresh] BTC candle {age:.1f}h old → fresh", flush=True)

    for bot in BOTS:
        try:
            report, db_path = compute_divergence(bot, now_ts)
        except Exception as e:
            print(f"[{bot}] ERROR: {type(e).__name__}: {e}", flush=True)
            continue
        tag = f"live_only={report.get('live_only_n', 0)} " \
              f"bt_only={report.get('bt_only_n', 0)} " \
              f"matched_div={report.get('matched_div_n', 0)}"
        err = f" bt_error={report['bt_error']}" if report["bt_error"] else ""
        print(f"[{bot}] BT_DIVERGENCE window={report['window_start']} "
              f"live_n={report['live_n']} bt_n={report['bt_n']} {tag}{err}", flush=True)
        if args.dry_run:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            write_event(db_path, report)
            print(f"[{bot}] event written → {db_path}", flush=True)


if __name__ == "__main__":
    main()
