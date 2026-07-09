"""btlive — compare live bot trades to theoretical backtest + diagnose the gap.

Three-phase flow (cf. /btlive skill):

  1. REFRESH — pull fresh 4h candles / funding / OI if data is stale (>2h).
  2. COMPARE — run the rolling backtest engine on the same deployment window
     and capital, then aggregate live vs backtest (PnL/DD/n/WR/per-strat).
  3. JUSTIFY — decompose the gap by root cause:
       • Matched vs live-only vs BT-only (trade-level audit, merged from
         the old btlive_audit.py)
       • SKIP events from the live `events` table (slot contention etc.)
       • Manual interventions (manual_close, manual_stop_set)
       • Funding cost gap (live real vs backtest flat estimate)
       • Sizing divergence on matched pairs
       • Slippage estimate on matched pairs

The script does NOT touch the running bot — read-only on DB + state.json.

Compare uniquement Alfred depuis son démarrage : le module lit les DB Alfred
(`alfred/data/bots/<id>/bot.db`) et ancre la fenêtre à l'inception Alfred du bot
(2026-06-10 live/paper, 2026-06-11 junior) via `BOT_DEPLOYMENTS` — les trades
legacy pré-Alfred (s'il en restait) sont filtrés par `entry_iso >= start_iso`.

Usage:
    .venv/bin/python3 -m analysis.btlive_compare --senior   # = --live (SENIOR)
    .venv/bin/python3 -m analysis.btlive_compare --live
    .venv/bin/python3 -m analysis.btlive_compare --paper
    .venv/bin/python3 -m analysis.btlive_compare --junior
    --skip-refresh : skip the data refresh step (assume caller did it)
    --start-cap X  : override the starting capital
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict


# Repointé sur Alfred le 2026-06-14 (legacy décommissionné). (dir, clé, capital).
# Reset 2026-07-09 : live+paper remis à $518.34 (equity live post-close), compteurs
# à zéro + historique de trades effacé, pour comparaison forward apples-to-apples.
BOTS = {
    "paper":  ("alfred/data/bots/paper",  "paper",  518.34),
    "live":   ("alfred/data/bots/live",   "live",    518.34),
    "junior": ("alfred/data/bots/junior", "junior",  332.76),
}

DATA_STALENESS_THRESHOLD_HOURS = 2.0  # refresh if BTC candle older than this


# ── Phase 1: data refresh ────────────────────────────────────────────────

def data_age_hours(project_root: str) -> float | None:
    """Return age in hours of latest BTC 4h candle, or None if missing."""
    path = os.path.join(project_root, "backtests/output/pairs_data/BTC_4h_3y.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            c = json.load(f)
        if not c:
            return None
        return (time.time() - c[-1]["t"] / 1000) / 3600
    except Exception:
        return None


def refresh_backtest_data(project_root: str) -> None:
    """Run the 3 fetchers sequentially. Each is idempotent."""
    fetchers = [
        ("4h candles",     "backtests.fetch_4h_candles"),
        ("funding history","backtests.fetch_funding_history"),
        ("OI history",     "backtests.fetch_oi_history"),
    ]
    py = os.path.join(project_root, ".venv/bin/python3")
    if not os.path.exists(py):
        py = "python3"
    for label, mod in fetchers:
        print(f"  → fetching {label} ({mod})...", flush=True)
        t0 = time.time()
        r = subprocess.run([py, "-m", mod], cwd=project_root, capture_output=True, text=True)
        elapsed = time.time() - t0
        if r.returncode != 0:
            print(f"    ⚠ {mod} failed (exit {r.returncode}, {elapsed:.0f}s)", flush=True)
            tail = r.stderr.strip().splitlines()[-3:] if r.stderr else []
            for line in tail:
                print(f"      {line}", flush=True)
        else:
            print(f"    ✓ {mod} done in {elapsed:.0f}s", flush=True)


# ── Phase 2: comparison primitives ───────────────────────────────────────

def read_live_trades(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT entry_time, exit_time, symbol, strategy, direction, "
        "       size_usdt, gross_bps, net_bps, pnl_usdt, funding_usdt, "
        "       reason, entry_price, exit_price, mfe_bps, mae_bps "
        "FROM trades ORDER BY entry_time"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        entry_iso, exit_iso = r[0], r[1]
        try:
            entry_dt = dt.datetime.fromisoformat(entry_iso.replace('Z', '+00:00'))
            exit_dt = dt.datetime.fromisoformat(exit_iso.replace('Z', '+00:00'))
        except Exception:
            continue
        d = 1 if r[4] == "LONG" else -1
        out.append({
            "src": "live",
            "coin": r[2], "strat": r[3], "dir": d,
            "entry_ts": int(entry_dt.timestamp() * 1000),
            "exit_ts": int(exit_dt.timestamp() * 1000),
            "entry_iso": entry_iso,
            "size": r[5] or 0.0, "gross_bps": r[6] or 0.0, "net_bps": r[7] or 0.0,
            "pnl": r[8] or 0.0, "funding": r[9] or 0.0,
            "reason": r[10] or "",
            "entry_px": r[11] or 0.0, "exit_px": r[12] or 0.0,
            "mfe_bps": r[13] or 0.0, "mae_bps": r[14] or 0.0,
            "matched_to": None,
        })
    return out


def _cooldown_hours() -> float:
    """Cooldown duration the bot/BT use (shared core default)."""
    try:
        from alfred.settings import Params
        return float(Params().cooldown_hours)
    except Exception:
        return 24.0


def read_skip_events(db_path: str, start_ts_sec: float) -> dict:
    """Return SKIP events grouped by reason in the window."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT json_extract(data,'$.reason'), json_extract(data,'$.strategy'), "
        "       json_extract(data,'$.dir'), symbol, ts "
        "FROM events WHERE event = 'SKIP' AND ts >= ?",
        (start_ts_sec,)
    ).fetchall()
    conn.close()
    by_reason: dict = defaultdict(int)
    by_reason_strat: dict = defaultdict(int)
    by_coin: dict = defaultdict(int)
    raw: list = []
    for reason, strat, dirn, sym, ts in rows:
        by_reason[reason] += 1
        by_reason_strat[(reason, strat, dirn)] += 1
        by_coin[(sym, reason)] += 1
        raw.append({"reason": reason, "strat": strat, "dir": dirn,
                    "coin": sym, "ts_ms": int(ts * 1000)})
    return {
        "total": len(rows),
        "by_reason": dict(by_reason),
        "by_reason_strat": dict(by_reason_strat),
        "by_coin": dict(by_coin),
        "rows": raw,
    }


def classify_bt_only_misses(bt_only: list[dict], live: list[dict],
                            cooldown_hours: float, skip: dict) -> dict:
    """Attribute each BT-only trade (BT took, bot didn't) to a precise cause.

    Reconstructs what the bot was doing on that coin at the BT entry time:
      - already_in_position_OPPOSITE_dir : bot held the coin in the opposite
        direction at that instant  → CAPTURABLE by allowing LONG+SHORT same coin
      - already_in_position_SAME_dir     : bot held the coin same direction
      - cooldown                         : a live trade on the coin exited within
        cooldown_hours before the BT entry
      - <skip reason>                    : a SKIP event on (coin, ±4h) with that
        reason (insufficient_margin, max_*, oi_gate, disp_gate, ...)
      - not_skipped / unexplained        : no trace — pure path divergence
        (signal-state differed: feature/timing made the signal not fire live)
    """
    cd_ms = int(cooldown_hours * 3600 * 1000)
    slack_ms = 4 * 3600 * 1000
    held: dict = defaultdict(list)  # coin -> [(entry_ms, exit_ms, dir)]
    for t in live:
        held[t["coin"]].append((t["entry_ts"], t["exit_ts"], t["dir"]))
    skip_rows = skip.get("rows", [])
    buckets: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "examples": []})
    for b in bt_only:
        coin, T, D = b["coin"], b["entry_ts"], b["dir"]
        cause = None
        # 1. Held at T?
        for (e, x, d) in held.get(coin, []):
            if e <= T < x:
                cause = ("already_in_position_OPPOSITE_dir" if d != D
                         else "already_in_position_SAME_dir")
                break
        # 2. Cooldown: a live trade on coin exited within cooldown window before T
        if cause is None:
            for (e, x, d) in held.get(coin, []):
                if 0 <= (T - x) <= cd_ms:
                    cause = "cooldown"
                    break
        # 3. SKIP event on this coin near T (margin / max_* / gates)
        if cause is None:
            best = None
            for s in skip_rows:
                if s["coin"] == coin and abs(s["ts_ms"] - T) <= slack_ms:
                    if best is None or abs(s["ts_ms"] - T) < abs(best["ts_ms"] - T):
                        best = s
            if best is not None:
                cause = best["reason"] or "skip_unknown"
        # 4. Unexplained — signal simply didn't fire live (feature/timing drift)
        if cause is None:
            cause = "not_skipped_signal_drift"
        buckets[cause]["n"] += 1
        buckets[cause]["pnl"] += b["pnl"]
        if len(buckets[cause]["examples"]) < 4:
            buckets[cause]["examples"].append(
                f"{coin} {b['strat']} {'L' if D == 1 else 'S'} {b['pnl']:+.0f}")
    return dict(buckets)


def equity_curve(trades: list[dict], start_cap: float) -> tuple[float, float]:
    cum = 0.0
    peak = start_cap
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        cum += t["pnl"]
        bal = start_cap + cum
        peak = max(peak, bal)
        dd = (bal / peak - 1) * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
    return max_dd, start_cap + cum


def per_strat_stats(trades: list[dict]) -> dict:
    out: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = t["strat"]
        out[s]["n"] += 1
        out[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            out[s]["wins"] += 1
    return out


def run_backtest_for_period(start_dt: dt.datetime, start_cap: float, project_root: str) -> tuple[dict, list[dict]]:
    """Run the rolling engine for the bot's deployment window. Returns (summary, trades_list)."""
    sys.path.insert(0, project_root)
    from backtests import backtest_rolling
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )

    print("  → loading candles / features / OI / funding / DXY...", flush=True)
    data = backtest_rolling.load_3y_candles()
    features = backtest_rolling.build_features(data)
    sector_features = backtest_rolling.compute_sector_features(features, data)
    dxy_data = backtest_rolling.load_dxy()
    oi_data = backtest_rolling.load_oi()
    funding_data = backtest_rolling.load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = dt.datetime.fromtimestamp(latest_ts / 1000, tz=dt.timezone.utc)

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    start_ms = int(start_dt.timestamp() * 1000)
    print(f"  → running backtest_rolling.run_window {start_dt.date()} → {end_dt.date()} cap=${start_cap:.0f}...", flush=True)
    # Sémantique ALIGNED (phase 6) — exits/sizing via alfred/rules.py, identique
    # au bot Alfred live ; + plafond de marge (réaliste). Les hooks legacy
    # (early_exit_params/runner_ext) sont redondants en aligned (dans evaluate_exit).
    res = backtest_rolling.run_window(
        features, data, sector_features, dxy_data,
        start_ms, latest_ts, start_capital=start_cap,
        oi_data=oi_data, funding_data=funding_data,
        apply_adaptive_modulator=True,
        aligned=True, margin_check=True,
        mfe_on_close=True,  # v1.4.0 : MFE sur le mark (comme le bot live), pas les
                            # mèches — sinon le BT surévalue les exits et l'écart est faux.
    )
    res["_end_dt"] = end_dt

    bt_trades = []
    for t in res["trades"]:
        bt_trades.append({
            "src": "bt",
            "coin": t["coin"], "strat": t["strat"], "dir": t["dir"],
            "entry_ts": int(t["entry_t"]), "exit_ts": int(t["exit_t"]),
            "size": t.get("size", 0.0), "pnl": t.get("pnl", 0.0),
            "net": t.get("net", 0.0),
            "reason": t.get("reason", ""),
            "matched_to": None,
        })
    return res, bt_trades


# ── Phase 3: matching + decomposition ────────────────────────────────────

def match_trades(live: list[dict], bt: list[dict], slack_ms: int = 4 * 3600 * 1000) -> list[tuple]:
    """Match each live trade to a backtest trade by (coin, strat, dir) and entry within slack."""
    bt_idx: dict = defaultdict(list)
    for i, b in enumerate(bt):
        bt_idx[(b["coin"], b["strat"], b["dir"])].append(i)
    pairs = []
    used = set()
    for i, lt in enumerate(live):
        key = (lt["coin"], lt["strat"], lt["dir"])
        cands = [j for j in bt_idx.get(key, []) if j not in used]
        if not cands:
            continue
        best = min(cands, key=lambda j: abs(bt[j]["entry_ts"] - lt["entry_ts"]))
        if abs(bt[best]["entry_ts"] - lt["entry_ts"]) <= slack_ms:
            pairs.append((i, best))
            used.add(best)
            lt["matched_to"] = best
            bt[best]["matched_to"] = i
    return pairs


# ── Formatting helpers ───────────────────────────────────────────────────

def fmt_pct(v: float, plus: bool = True) -> str:
    sign = "+" if plus and v >= 0 else ""
    return f"{sign}{v:.1f}%"


def fmt_ts(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%m-%d %H:%M")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper", action="store_const", dest="bot", const="paper")
    g.add_argument("--live", action="store_const", dest="bot", const="live")
    g.add_argument("--senior", action="store_const", dest="bot", const="live",
                   help="alias de --live (SENIOR = le bot live, argent réel)")
    g.add_argument("--junior", action="store_const", dest="bot", const="junior")
    parser.add_argument("--start-cap", type=float, default=None,
                        help="Override starting capital (default: bot's nominal)")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="Skip the data refresh step")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_rel, bot_key, _defcap = BOTS[args.bot]
    output_dir = os.path.join(project_root, output_rel)
    db_path = os.path.join(output_dir, "bot.db")
    state_path = os.path.join(output_dir, "state.json")

    if not os.path.exists(db_path):
        sys.exit(f"DB not found: {db_path}")

    # ── PHASE 1: DATA REFRESH ────────────────────────────────────────────
    print("=" * 72)
    print(f"  BTLIVE — {bot_key.upper()}")
    print("=" * 72)
    print("\n[1/3] DATA REFRESH")
    age = data_age_hours(project_root)
    if age is None:
        print("  ⚠ No existing BTC candles. Fetching from scratch...")
        if not args.skip_refresh:
            refresh_backtest_data(project_root)
    else:
        print(f"  Latest BTC candle: {age:.1f}h old")
        if args.skip_refresh:
            print("  → --skip-refresh active, skipping fetchers")
        elif age <= DATA_STALENESS_THRESHOLD_HOURS:
            print(f"  → fresh enough (<={DATA_STALENESS_THRESHOLD_HOURS}h), skipping fetchers")
        else:
            print(f"  → stale (>{DATA_STALENESS_THRESHOLD_HOURS}h), refreshing...")
            refresh_backtest_data(project_root)
            age = data_age_hours(project_root)
            print(f"  Latest BTC candle now: {age:.1f}h old")

    # ── PHASE 2: COMPARISON ──────────────────────────────────────────────
    print("\n[2/3] COMPARISON")
    sys.path.insert(0, project_root)
    from backtests.backtest_rolling import BOT_DEPLOYMENTS
    deploy_map = {b: d for b, d in BOT_DEPLOYMENTS}
    if bot_key not in deploy_map:
        sys.exit(f"No deployment date for {bot_key} in BOT_DEPLOYMENTS")
    deploy_dt = dt.datetime.fromisoformat(deploy_map[bot_key]).replace(tzinfo=dt.timezone.utc)
    # v12.17.4: prefer the bot's actual soft-reset / hard-restart date over the
    # hardcoded deployment when more recent — for paper this matters because the
    # bot was hard-reset on 2026-06-04 but deploy_map still says 2026-03-25.
    # Use max(deploy_date, perf_track_start_ts, min(trades.entry_time)).
    start_dt = deploy_dt
    start_source = "deployment date"
    perf_ts = 0.0
    if os.path.exists(state_path):
        try:
            with open(state_path) as _f:
                _s = json.load(_f)
            perf_ts = float(_s.get("_perf_track_start_ts", 0) or 0)
        except Exception:
            pass
    if perf_ts > 0:
        perf_dt = dt.datetime.fromtimestamp(perf_ts, dt.timezone.utc)
        if perf_dt > start_dt:
            start_dt = perf_dt
            start_source = "soft-reset (perf_track_start_ts)"
    # Also honor the actual earliest trade in the DB (covers hard resets that
    # wipe the DB without touching perf_track_start_ts).
    try:
        _conn = sqlite3.connect(db_path)
        _row = _conn.execute("SELECT MIN(entry_time) FROM trades").fetchone()
        _conn.close()
        if _row and _row[0]:
            _min_dt = dt.datetime.fromisoformat(_row[0])
            if _min_dt > start_dt:
                start_dt = _min_dt
                start_source = "earliest trade in DB"
    except Exception:
        pass
    # Capital par défaut = celui de la map BOTS (Alfred : equity au reset/migration)
    start_cap = args.start_cap if args.start_cap else _defcap
    print(f"  Period start: {start_dt.date()} ({start_source})")
    print(f"  Starting capital: ${start_cap:.0f}")

    # Live side
    live = read_live_trades(db_path)
    if not live:
        sys.exit(f"No live trades in {db_path}")
    # v12.17.3: keep the FULL db sum for the coherence check before
    # windowing. state.total_pnl is a lifetime counter, so the coherence
    # check must compare against the lifetime DB sum, not the windowed
    # one — otherwise we get a spurious "DRIFT" equal to pre-window
    # trade contributions.
    db_total_full = sum(t["pnl"] for t in live)
    start_iso = start_dt.isoformat()
    live = [t for t in live if t["entry_iso"] >= start_iso]

    # State.json diagnostic
    with open(state_path) as f:
        state = json.load(f)
    state_pnl = state.get("total_pnl", 0.0)
    state_offset = state.get("_pnl_realign_offset", 0.0)

    # Backtest side
    bt_summary, bt_trades = run_backtest_for_period(start_dt, start_cap, project_root)

    end_dt = bt_summary["_end_dt"]

    live_max_dd, live_final_bal = equity_curve(live, start_cap)
    live_pnl = sum(t["pnl"] for t in live)
    live_wr = sum(1 for t in live if t["pnl"] > 0) / len(live) * 100
    live_strat = per_strat_stats(live)

    bt_pnl = bt_summary["pnl"]
    bt_dd = bt_summary["max_dd_pct"]
    bt_n = bt_summary["n_trades"]
    bt_wr = bt_summary["win_rate"]
    bt_strat_raw = bt_summary["by_strat"]

    # ── Headline ─────────────────────────────────────────────────────────
    print()
    print("-" * 72)
    print(f"  HEADLINE  Period: {start_dt.date()} → {end_dt.date()}  ({(end_dt.date()-start_dt.date()).days}d)")
    print("-" * 72)
    print(f"{'Metric':<22} {'Live':>14} {'Backtest':>14} {'Δ (L-BT)':>14}")
    print(f"  {'-'*68}")
    print(f"  {'P&L $':<20} {live_pnl:>+13.2f}  {bt_pnl:>+13.2f}  {live_pnl-bt_pnl:>+13.2f}")
    print(f"  {'P&L %':<20} {fmt_pct(live_pnl/start_cap*100):>14s} {fmt_pct(bt_pnl/start_cap*100):>14s} {fmt_pct((live_pnl-bt_pnl)/start_cap*100):>14s}")
    print(f"  {'Max DD %':<20} {live_max_dd:>+13.1f}% {bt_dd:>+13.1f}% {live_max_dd-bt_dd:>+13.1f}pp")
    print(f"  {'Trades closed':<20} {len(live):>14} {bt_n:>14} {len(live)-bt_n:>+14}")
    print(f"  {'Win rate':<20} {live_wr:>13.1f}% {bt_wr:>13.1f}% {live_wr-bt_wr:>+13.1f}pp")

    print()
    print(f"  Per-strategy breakdown:")
    all_strats = sorted(set(live_strat) | set(bt_strat_raw))
    print(f"    {'Strat':<6} {'L_n':>4} {'L_pnl':>9} {'L_wr':>6}  |  {'BT_n':>4} {'BT_pnl':>9} {'BT_wr':>6}")
    print(f"    {'-'*52}")
    for s in all_strats:
        L = live_strat.get(s, {"n": 0, "pnl": 0.0, "wins": 0})
        B = bt_strat_raw.get(s, {"n": 0, "pnl": 0.0, "wr": 0})
        l_wr = L["wins"]/L["n"]*100 if L["n"] else 0
        b_wr = B.get("wr", 0)
        print(f"    {s:<6} {L['n']:>4} {L['pnl']:>+9.2f} {l_wr:>5.0f}%  |  {B['n']:>4} {B['pnl']:>+9.2f} {b_wr:>5.0f}%")

    # State.json coherence — v12.17.3: use the FULL DB sum (lifetime),
    # not the windowed one. state.total_pnl is a lifetime counter; comparing
    # against a windowed sum produces a spurious DRIFT equal to the pre-window
    # trade contributions.
    db_total_window = sum(t["pnl"] for t in live)
    print()
    print(f"  Coherence check:")
    print(f"    DB sum(pnl_usdt) [window]   = ${db_total_window:+.2f}")
    print(f"    DB sum(pnl_usdt) [lifetime] = ${db_total_full:+.2f}")
    print(f"    state.json total_pnl        = ${state_pnl:+.2f}")
    print(f"    realign_offset              = ${state_offset:+.2f}")
    coherence = db_total_full - state_pnl + state_offset
    coh_label = "OK" if abs(coherence) < 1.0 else "DRIFT"
    print(f"    [lifetime] db − state + offset = ${coherence:+.4f}   [{coh_label}]")

    # ── PHASE 3: JUSTIFY / ROOT CAUSE ────────────────────────────────────
    print()
    print("\n[3/3] ROOT CAUSE ANALYSIS")
    print(f"  Headline gap = Live − Backtest = ${live_pnl - bt_pnl:+.2f}\n")

    # Match trades
    pairs = match_trades(live, bt_trades, slack_ms=4 * 3600 * 1000)
    live_only = [t for t in live if t["matched_to"] is None]
    bt_only = [t for t in bt_trades if t["matched_to"] is None]

    matched_l_pnl = sum(live[li]["pnl"] for li, _ in pairs)
    matched_b_pnl = sum(bt_trades[bi]["pnl"] for _, bi in pairs)
    matched_delta = matched_l_pnl - matched_b_pnl
    live_only_pnl = sum(t["pnl"] for t in live_only)
    bt_only_pnl = sum(t["pnl"] for t in bt_only)

    print(f"  TRADE-LEVEL DECOMPOSITION:")
    print(f"  ─────────────────────────")
    print(f"    Matched pairs ({len(pairs)} trades, took both sides):")
    print(f"      Live PnL  = ${matched_l_pnl:+.2f}")
    print(f"      BT   PnL  = ${matched_b_pnl:+.2f}")
    print(f"      Δ matched = ${matched_delta:+.2f}    ← divergence on same trades (px/size/exit)")
    print(f"    Live-only ({len(live_only)} trades — bot took, BT didn't):")
    print(f"      PnL contribution  = ${live_only_pnl:+.2f}    ← live's extra trades net")
    print(f"    BT-only ({len(bt_only)} trades — BT took, bot didn't):")
    print(f"      PnL contribution  = ${bt_only_pnl:+.2f}    ← live MISSED these")
    decomp_sum = matched_delta + live_only_pnl - bt_only_pnl
    print(f"    Sum: ${matched_delta:+.2f} + ${live_only_pnl:+.2f} − ${bt_only_pnl:+.2f} = ${decomp_sum:+.2f}")
    print(f"    (should equal headline gap ${live_pnl - bt_pnl:+.2f}, diff = ${decomp_sum - (live_pnl - bt_pnl):+.4f})")

    # SKIP events
    print(f"\n  SKIP EVENTS (live's events table, in window):")
    print(f"  ─────────────────────────")
    skip = read_skip_events(db_path, start_dt.timestamp())
    if skip["total"] == 0:
        print(f"    No SKIP events recorded (signals all enacted).")
    else:
        print(f"    Total SKIP events: {skip['total']}")
        for reason, n in sorted(skip["by_reason"].items(), key=lambda x: -x[1])[:8]:
            pct = n / skip["total"] * 100
            print(f"      {reason:<20}  n={n:>4}  ({pct:.0f}%)")

    # ── Margin model: BT idealization vs live reality ──
    print(f"\n  MARGIN MODEL (BT capital×0.95 vs live real avail_margin):")
    print(f"  ─────────────────────────")
    bt_margin_skip = bt_summary.get("n_margin_skip", None)
    live_margin_skip = skip["by_reason"].get("insufficient_margin", 0)
    bt_ms_str = str(bt_margin_skip) if bt_margin_skip is not None else "n/a"
    print(f"    BT margin skips (capital×0.95 ceiling): {bt_ms_str}")
    print(f"    Live insufficient_margin SKIPs:         {live_margin_skip}")
    if bt_margin_skip is not None and bt_margin_skip < live_margin_skip:
        print(f"    → BT is MORE lenient (skipped fewer) → BT target slightly inflated")
    elif bt_margin_skip is not None and bt_margin_skip > live_margin_skip:
        print(f"    → BT margin model stricter than live this window (rare)")
    else:
        print(f"    → margin not a material driver this window")

    # ── Precise attribution of BT-only misses (why did the bot miss each one?) ──
    print(f"\n  BT-ONLY MISS ATTRIBUTION (per cause, reconciles to ${bt_only_pnl:+.2f}):")
    print(f"  ─────────────────────────")
    if not bt_only:
        print(f"    No BT-only trades (live captured every BT entry).")
    else:
        cd_h = _cooldown_hours()
        miss = classify_bt_only_misses(bt_only, live, cd_h, skip)
        # Order: opposite-dir (the testable lever) first, then by |pnl|
        order = sorted(miss.items(),
                       key=lambda kv: (kv[0] != "already_in_position_OPPOSITE_dir",
                                       -abs(kv[1]["pnl"])))
        for cause, d in order:
            tag = "  ← CAPTURABLE (Track 2)" if cause == "already_in_position_OPPOSITE_dir" else ""
            print(f"    {cause:<34} n={d['n']:>2}  ${d['pnl']:>+8.2f}{tag}")
            if d["examples"]:
                print(f"        e.g. {', '.join(d['examples'])}")
        opp = miss.get("already_in_position_OPPOSITE_dir", {"n": 0, "pnl": 0.0})
        print(f"\n    → opposite-direction block (capturable): "
              f"n={opp['n']}, ${opp['pnl']:+.2f} of the ${bt_only_pnl:+.2f} miss")

    # Manual interventions
    print(f"\n  MANUAL INTERVENTIONS (live's own actions):")
    print(f"  ─────────────────────────")
    manual_close = [t for t in live if t["reason"] == "manual_close"]
    manual_stop = [t for t in live if t["reason"] == "manual_stop_set"]
    if not manual_close and not manual_stop:
        print(f"    No manual interventions in window.")
    else:
        mc_pnl = sum(t["pnl"] for t in manual_close)
        ms_pnl = sum(t["pnl"] for t in manual_stop)
        print(f"    manual_close: n={len(manual_close)}, sum PnL=${mc_pnl:+.2f}")
        print(f"    manual_stop_set: n={len(manual_stop)}, sum PnL=${ms_pnl:+.2f}")
        # For each manual_close, check MFE — what was on the table at peak?
        if manual_close:
            total_mfe_left = 0.0
            for t in manual_close:
                # mfe_bps × size / 1e4 = peak unrealized in $; how much was cashed?
                peak_unreal = t["mfe_bps"] * t["size"] / 1e4
                left_on_table = peak_unreal - t["pnl"]
                total_mfe_left += max(0, left_on_table)
            print(f"    → cumulative 'peak − closed' on manual_close = ${total_mfe_left:+.2f} "
                  f"(MFE never realised by cutting early)")

    # Funding gap (live real vs backtest flat)
    print(f"\n  FUNDING COST GAP (live real vs backtest flat 1bps):")
    print(f"  ─────────────────────────")
    live_funding_sum = sum(t["funding"] for t in live)
    # Backtest uses funding_data when provided; otherwise FUNDING_DRAG_BPS=1
    # The flat est: net_bps already has -1bps baked in. Live's real funding is
    # subtracted on top via funding_usdt column. So the GAP = -live_funding_sum
    # minus what bt would have estimated (~size × 1bps × n_trades).
    bt_funding_estimate = sum(t["size"] * 1.0 / 1e4 for t in live)  # 1 bps approx
    print(f"    Live cumulative funding_usdt: ${live_funding_sum:+.2f}")
    print(f"    BT flat estimate (~1 bps × size × n): ${-bt_funding_estimate:+.2f}")
    funding_gap = live_funding_sum - (-bt_funding_estimate)
    print(f"    Δ funding contribution to gap: ${funding_gap:+.2f}")
    if abs(funding_gap) > 1.0:
        print(f"    → funding cost is {'lighter' if funding_gap > 0 else 'heavier'} on live "
              f"than backtest's flat model")

    # Sizing divergence on matched pairs
    if pairs:
        print(f"\n  SIZING DIVERGENCE (matched pairs):")
        print(f"  ─────────────────────────")
        size_ratios = []
        size_pnl_gap = 0.0  # $ PnL live forfeited by sizing smaller than BT
        for li, bi in pairs:
            L = live[li]
            B = bt_trades[bi]
            if B["size"] > 0:
                size_ratios.append(L["size"] / B["size"])
            if L["size"] > 0:
                # If live had been sized like BT, its PnL would scale by size ratio
                # (same % return). Isolates the pure-size effect from px/exit drift.
                size_pnl_gap += L["pnl"] * (B["size"] / L["size"] - 1.0)
        if size_ratios:
            import statistics as stat
            med = stat.median(size_ratios)
            mean = stat.mean(size_ratios)
            print(f"    Live/BT size ratio on {len(size_ratios)} matched: median={med:.3f}, mean={mean:.3f}")
            print(f"    Est. $ PnL gap from sizing (live@BT-size − live): ${size_pnl_gap:+.2f}")
            if med < 0.85:
                print(f"    → live sized DOWN: backtest path led to bigger capital base at signal times")
            elif med > 1.15:
                print(f"    → live sized UP: backtest path led to smaller capital base / heavier modulator cut")
            else:
                print(f"    → sizing tracks closely (within ±15%)")

    # Top contributors to matched delta
    if pairs:
        print(f"\n  TOP 10 MATCHED-PAIR DELTAS (live − BT):")
        print(f"  ─────────────────────────")
        details = []
        for li, bi in pairs:
            L = live[li]
            B = bt_trades[bi]
            d = L["pnl"] - B["pnl"]
            details.append((d, L, B))
        details.sort(key=lambda x: abs(x[0]), reverse=True)
        print(f"    {'Coin':<6} {'Strat':<5} {'Dir':<5} {'EntryL':<12} {'L_pnl':>8} {'BT_pnl':>8} {'Δpnl':>8}  {'L_reason':<18} {'BT_reason':<14}")
        for d, L, B in details[:10]:
            dir_s = "LONG" if L["dir"] == 1 else "SHORT"
            print(f"    {L['coin']:<6} {L['strat']:<5} {dir_s:<5} {fmt_ts(L['entry_ts']):<12} "
                  f"{L['pnl']:>+8.2f} {B['pnl']:>+8.2f} {d:>+8.2f}  "
                  f"{L['reason'][:18]:<18} {B['reason'][:14]:<14}")

    # Live-only top
    if live_only:
        live_only.sort(key=lambda x: abs(x["pnl"]), reverse=True)
        print(f"\n  TOP 10 LIVE-ONLY TRADES (taken by bot, not by BT):")
        print(f"  ─────────────────────────")
        print(f"    {'Coin':<6} {'Strat':<5} {'Dir':<5} {'Entry':<12} {'Size':>7} {'Pnl':>8}  Reason")
        for t in live_only[:10]:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"    {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} "
                  f"{t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")

    if bt_only:
        bt_only.sort(key=lambda x: abs(x["pnl"]), reverse=True)
        print(f"\n  TOP 10 BT-ONLY TRADES (taken by BT, missed by bot):")
        print(f"  ─────────────────────────")
        print(f"    {'Coin':<6} {'Strat':<5} {'Dir':<5} {'Entry':<12} {'Size':>7} {'Pnl':>8}  Reason")
        for t in bt_only[:10]:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"    {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} "
                  f"{t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")

    # ── Attribution summary ──────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  ATTRIBUTION SUMMARY")
    print("=" * 72)
    gap = live_pnl - bt_pnl
    print(f"  Headline gap (Live − BT):       ${gap:+8.2f}")
    print(f"    Matched-pair Δ:               ${matched_delta:+8.2f}")
    print(f"    Live-only contribution:       ${live_only_pnl:+8.2f}")
    print(f"    BT-only (missed by live):     ${-bt_only_pnl:+8.2f}   (negative = miss hurts gap)")
    print(f"    Funding cost delta:           ${funding_gap:+8.2f}")
    if manual_close or manual_stop:
        print(f"    Manual close+stop sum PnL:    ${mc_pnl + ms_pnl:+8.2f}   "
              f"(already counted in matched/live-only)")

    # Final verdict
    pnl_gap_pct = gap / start_cap * 100
    if abs(pnl_gap_pct) < 5:
        verdict = "✓ Live tracks backtest within 5pp"
    elif abs(pnl_gap_pct) < 15:
        verdict = "⚠ Live diverges from backtest"
    else:
        verdict = "✗ Significant live/backtest divergence"
    print()
    print(f"  Verdict: {verdict}  ({pnl_gap_pct:+.1f}pp of starting capital)")
    print()

    # Actionable hints
    print(f"  HINTS:")
    if abs(matched_delta) > abs(gap) * 0.5:
        print(f"    • Matched-pair Δ is the biggest driver. Inspect price/exit timing per trade.")
    if abs(bt_only_pnl) > abs(gap) * 0.5:
        print(f"    • BT-only trades dominate. Live is MISSING entries — investigate SKIP reasons above.")
    if abs(live_only_pnl) > abs(gap) * 0.5:
        print(f"    • Live-only trades dominate. Live took entries BT didn't — manual interventions or signal-state divergence.")
    if abs(funding_gap) > 2.0:
        print(f"    • Funding cost gap is material (>${abs(funding_gap):.0f}). Live's real funding diverges from backtest's 1bps flat.")
    if manual_close and (mc_pnl + ms_pnl) != 0:
        print(f"    • {len(manual_close)} manual_close + {len(manual_stop)} manual_stop_set — these never happen in backtest.")
    print()


if __name__ == "__main__":
    main()
