"""Validate backtest by running the exact live period and comparing.

If the backtest is correct, it should give RESULTS in the same statistical
range as live for the same dates. Big divergence = bug.

Live period: 2026-03-26 (first live trade) to 2026-04-24 (last full backtest day).
Compares: trade count, WR, avg P&L, big loser rate, sum P&L, distribution.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, load_oi, load_funding,
)


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])

    # Live period: from first live trade to last backtest day
    start_dt = datetime(2026, 3, 26, tzinfo=timezone.utc)
    end_dt = datetime(2026, 4, 24, tzinfo=timezone.utc)
    end_dt = min(end_dt, datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc))

    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    print(f"Backtest window: {start_dt.date()} → {end_dt.date()}")

    early_exit_params = dict(
        exit_lead_candles=3, mfe_cap_bps=150,
        mae_floor_bps=-800, slack_bps=300,
    )

    # ── Run backtest with $400 starting capital (match live) ──
    print(f"\nRunning backtest with start_capital=$400…")
    r = run_window(features, data, sector_features, dxy_data,
                   start_ts, end_ts,
                   start_capital=400.0,
                   oi_data=oi_data,
                   early_exit_params=early_exit_params,
                   funding_data=funding_data)

    bt_trades = r["trades"]
    print(f"  Backtest result: end=${r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%) "
          f"DD={r['max_dd_pct']:.1f}% n={r['n_trades']} wr={r['win_rate']:.0f}%")

    # ── Live trades for the same period (DB) ──
    con = sqlite3.connect('analysis/output_live/reversal_ticks.db')
    con.row_factory = sqlite3.Row
    live_rows = list(con.execute(
        "SELECT * FROM trades WHERE entry_time >= ? AND entry_time < ? AND reason != 'manual_stop' ORDER BY entry_time",
        (start_dt.isoformat(), end_dt.isoformat())
    ))
    print(f"\nLive trades (same window): {len(live_rows)}")
    live_pnl = sum(r['pnl_usdt'] for r in live_rows)
    live_wins = sum(1 for r in live_rows if r['pnl_usdt'] > 0)
    print(f"  Live sum P&L: ${live_pnl:+.2f}, WR {live_wins}/{len(live_rows)} ({live_wins/max(len(live_rows),1)*100:.0f}%)")

    # ── Trade count & WR comparison ──
    print("\n=== Comparison ===")
    bt_pnl = sum(t["pnl"] for t in bt_trades)
    bt_wins = sum(1 for t in bt_trades if t["pnl"] > 0)
    print(f"{'Metric':<20} {'Backtest':>15} {'Live':>15}")
    print(f"{'n trades':<20} {len(bt_trades):>15} {len(live_rows):>15}")
    print(f"{'WR %':<20} {bt_wins/max(len(bt_trades),1)*100:>14.0f}% {live_wins/max(len(live_rows),1)*100:>14.0f}%")
    print(f"{'sum P&L $':<20} {bt_pnl:>+14.2f}  {live_pnl:>+14.2f}")
    if bt_trades and live_rows:
        print(f"{'avg P&L $':<20} {bt_pnl/len(bt_trades):>+14.2f}  {live_pnl/len(live_rows):>+14.2f}")
        bt_avg_size = sum(t["size"] for t in bt_trades) / len(bt_trades)
        live_avg_size = sum(r["size_usdt"] for r in live_rows) / len(live_rows)
        print(f"{'avg size $':<20} {bt_avg_size:>14.0f}  {live_avg_size:>14.0f}")
        bt_avg_net = sum(t["net"] for t in bt_trades) / len(bt_trades)
        live_avg_net = sum(r["net_bps"] for r in live_rows) / len(live_rows)
        print(f"{'avg net bps':<20} {bt_avg_net:>+14.0f}  {live_avg_net:>+14.0f}")
        # Big loser rate
        bt_big = sum(1 for t in bt_trades if t["net"] <= -1000)
        live_big = sum(1 for r in live_rows if r["net_bps"] <= -1000)
        print(f"{'big losers (≤-1000)':<20} {bt_big:>15} {live_big:>15}")
        print(f"{'big loser %':<20} {bt_big/len(bt_trades)*100:>14.1f}% {live_big/len(live_rows)*100:>14.1f}%")

    # ── Per-strategy breakdown ──
    print("\n=== Per strategy ===")
    bt_by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0, "net_sum": 0})
    for t in bt_trades:
        s = t["strat"]
        bt_by_strat[s]["n"] += 1
        bt_by_strat[s]["pnl"] += t["pnl"]
        bt_by_strat[s]["net_sum"] += t["net"]
        if t["pnl"] > 0: bt_by_strat[s]["wins"] += 1

    live_by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0, "net_sum": 0})
    for r in live_rows:
        s = r["strategy"]
        live_by_strat[s]["n"] += 1
        live_by_strat[s]["pnl"] += r["pnl_usdt"]
        live_by_strat[s]["net_sum"] += r["net_bps"]
        if r["pnl_usdt"] > 0: live_by_strat[s]["wins"] += 1

    all_strats = sorted(set(bt_by_strat) | set(live_by_strat))
    print(f"{'Strat':<6} {'BT n':>5} {'BT pnl':>10} {'BT wr':>6} {'BT avg_net':>10}  | "
          f"{'Live n':>6} {'Live pnl':>10} {'Live wr':>6} {'Live avg_net':>12}")
    for s in all_strats:
        b = bt_by_strat[s]; l = live_by_strat[s]
        bn = b["n"]; ln = l["n"]
        bw = b["wins"]/bn*100 if bn else 0
        lw = l["wins"]/ln*100 if ln else 0
        bavg = b["net_sum"]/bn if bn else 0
        lavg = l["net_sum"]/ln if ln else 0
        print(f"{s:<6} {bn:>5} ${b['pnl']:>+8.0f} {bw:>5.0f}% {bavg:>+9.0f} bps  | "
              f"{ln:>6} ${l['pnl']:>+8.0f} {lw:>5.0f}% {lavg:>+11.0f} bps")

    # ── Tokens used ──
    bt_tokens = set(t["coin"] for t in bt_trades)
    live_tokens = set(r["symbol"] for r in live_rows)
    print(f"\nDistinct tokens: backtest={len(bt_tokens)}, live={len(live_tokens)}")
    only_bt = bt_tokens - live_tokens
    only_live = live_tokens - bt_tokens
    print(f"  In BT but not live: {sorted(only_bt)}")
    print(f"  In live but not BT: {sorted(only_live)}")


if __name__ == "__main__":
    main()
