"""btlive — compare live bot trades to theoretical backtest over the same period.

Reads the target bot's trades DB + state.json, picks the bot's deployment
date from `backtests.backtest_rolling.BOT_DEPLOYMENTS`, runs the rolling
engine on that exact window+capital, and prints a side-by-side comparison.

Usage:
    .venv/bin/python3 -m analysis.btlive_compare --live
    .venv/bin/python3 -m analysis.btlive_compare --paper
    .venv/bin/python3 -m analysis.btlive_compare --junior

The script does NOT touch the running bot — read-only on DB + state.json.
Backtest data refresh is the caller's job (see `/backtest` skill or run
`backtests.fetch_4h_candles` / `fetch_funding_history` / `fetch_oi_history`
beforehand if the report should reflect today's market).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict


BOTS = {
    "paper":  ("analysis/output",       "paper"),
    "live":   ("analysis/output_live",  "live"),
    "junior": ("analysis/output_live2", "junior"),
}


def read_live_trades(db_path: str) -> list[dict]:
    """Pull closed trades from the bot's DB, normalized into dict per row."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT entry_time, exit_time, symbol, strategy, direction, "
        "       size_usdt, gross_bps, net_bps, pnl_usdt, funding_usdt, reason "
        "FROM trades ORDER BY entry_time"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "entry_t": r[0], "exit_t": r[1], "coin": r[2],
            "strat": r[3], "dir": r[4],
            "size_usdt": r[5], "gross_bps": r[6], "net_bps": r[7],
            "pnl": r[8], "funding": r[9] or 0.0, "reason": r[10],
        })
    return out


def equity_curve(trades: list[dict], start_cap: float) -> tuple[float, float]:
    """Returns (max_dd_pct, final_balance) from chronological trade pnls."""
    cum = 0.0
    peak = start_cap
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_t"]):
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


def run_backtest_for_period(start_dt: dt.datetime, start_cap: float, project_root: str) -> dict:
    """Load all backtest data and run a single window with production config."""
    sys.path.insert(0, project_root)
    from backtests import backtest_rolling
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )

    print("Loading backtest data (candles, sector, OI, funding, DXY)...")
    data = backtest_rolling.load_3y_candles()
    features = backtest_rolling.build_features(data)
    sector_features = backtest_rolling.compute_sector_features(features, data)
    dxy_data = backtest_rolling.load_dxy()
    oi_data = backtest_rolling.load_oi()
    funding_data = backtest_rolling.load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = dt.datetime.fromtimestamp(latest_ts / 1000, tz=dt.timezone.utc)
    print(f"Data latest: {end_dt.isoformat()}")

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
    print(f"Running backtest: {start_dt.date()} → {end_dt.date()} cap=${start_cap:.0f}...")
    res = backtest_rolling.run_window(
        features, data, sector_features, dxy_data,
        start_ms, latest_ts, start_capital=start_cap,
        oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        apply_adaptive_modulator=True,
    )
    res["_end_dt"] = end_dt
    return res


def fmt_pct(v: float, plus: bool = True) -> str:
    sign = "+" if plus and v >= 0 else ""
    return f"{sign}{v:.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper",  action="store_const", dest="bot", const="paper")
    g.add_argument("--live",   action="store_const", dest="bot", const="live")
    g.add_argument("--junior", action="store_const", dest="bot", const="junior")
    parser.add_argument("--start-cap", type=float, default=None,
                        help="Override starting capital (default: read from "
                             "BOT_DEPLOYMENTS default capital per bot)")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_rel, bot_key = BOTS[args.bot]
    output_dir = os.path.join(project_root, output_rel)
    db_path = os.path.join(output_dir, "reversal_ticks.db")
    state_path = os.path.join(output_dir, "reversal_state.json")

    if not os.path.exists(db_path):
        sys.exit(f"DB not found: {db_path}")

    # Pull bot deployment date from backtest_rolling constants
    sys.path.insert(0, project_root)
    from backtests.backtest_rolling import BOT_DEPLOYMENTS
    deploy_map = {b: d for b, d in BOT_DEPLOYMENTS}
    if bot_key not in deploy_map:
        sys.exit(f"No deployment date for {bot_key} in BOT_DEPLOYMENTS")
    start_dt = dt.datetime.fromisoformat(deploy_map[bot_key]).replace(tzinfo=dt.timezone.utc)

    # Default capital from state.json (current capital) — fallback to bot-specific default
    default_caps = {"paper": 1000.0, "live": 500.0, "junior": 300.0}
    start_cap = args.start_cap if args.start_cap else default_caps[bot_key]
    print(f"Starting capital: ${start_cap:.0f} (override via --start-cap)")

    # ── Read live trades ──
    live = read_live_trades(db_path)
    if not live:
        sys.exit(f"No live trades in {db_path}")
    # Filter trades within the deployment window (may exclude pre-deploy carry-overs)
    start_iso = start_dt.isoformat()
    live = [t for t in live if t["entry_t"] >= start_iso]
    live_max_dd, live_final_bal = equity_curve(live, start_cap)
    live_pnl = sum(t["pnl"] for t in live)
    live_wr = sum(1 for t in live if t["pnl"] > 0) / len(live) * 100
    live_strat = per_strat_stats(live)

    # Read current state.json for diagnostic info
    with open(state_path) as f:
        state = json.load(f)
    state_pnl = state.get("total_pnl", 0.0)
    state_offset = state.get("_pnl_realign_offset", 0.0)

    # ── Run backtest for same period ──
    bt = run_backtest_for_period(start_dt, start_cap, project_root)
    bt_trades = bt["trades"]
    bt_pnl = bt["pnl"]
    bt_dd = bt["max_dd_pct"]
    bt_n = bt["n_trades"]
    bt_wr = bt["win_rate"]
    bt_strat_raw = bt["by_strat"]
    end_dt = bt["_end_dt"]

    # ── Report ──
    print()
    print("=" * 72)
    print(f"  BTLIVE COMPARISON — {bot_key.upper()}")
    print(f"  Period: {start_dt.date()} → {end_dt.date()}  ({(end_dt.date()-start_dt.date()).days}d)")
    print(f"  Starting capital: ${start_cap:.0f}")
    print("=" * 72)
    print()
    print(f"{'Metric':<22} {'Live':>14} {'Backtest':>14} {'Δ (L-BT)':>14}")
    print(f"  {'-'*68}")
    print(f"  {'P&L $':<20} {live_pnl:>+13.2f}  {bt_pnl:>+13.2f}  {live_pnl-bt_pnl:>+13.2f}")
    print(f"  {'P&L %':<20} {fmt_pct(live_pnl/start_cap*100):>14s} {fmt_pct(bt_pnl/start_cap*100):>14s} {fmt_pct((live_pnl-bt_pnl)/start_cap*100):>14s}")
    print(f"  {'Max DD %':<20} {live_max_dd:>+13.1f}% {bt_dd:>+13.1f}% {live_max_dd-bt_dd:>+13.1f}pp")
    print(f"  {'Trades closed':<20} {len(live):>14} {bt_n:>14} {len(live)-bt_n:>+14}")
    print(f"  {'Win rate':<20} {live_wr:>13.1f}% {bt_wr:>13.1f}% {live_wr-bt_wr:>+13.1f}pp")
    print()

    # Per-strategy
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
    print()

    # Diagnostic: live total_pnl from DB vs state.json
    db_total = sum(t["pnl"] for t in live)
    print(f"  Sanity: DB sum(pnl_usdt) = ${db_total:+.2f}    "
          f"state.json total_pnl = ${state_pnl:+.2f}    "
          f"realign_offset = ${state_offset:+.2f}")
    coherence = db_total - state_pnl + state_offset
    coh_label = "OK" if abs(coherence) < 1.0 else "DRIFT"
    print(f"  Coherence: db - state_pnl + offset = ${coherence:+.4f}  [{coh_label}]")
    print()

    # Verdict line
    pnl_gap_pct = (live_pnl - bt_pnl) / start_cap * 100
    if abs(pnl_gap_pct) < 5:
        verdict = "✓ Live tracks backtest within 5pp"
    elif abs(pnl_gap_pct) < 15:
        verdict = "⚠ Live diverges from backtest"
    else:
        verdict = "✗ Significant live/backtest divergence"
    print(f"  Verdict: {verdict}")
    print()


if __name__ == "__main__":
    main()
