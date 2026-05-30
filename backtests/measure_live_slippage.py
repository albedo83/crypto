"""Measure realized slippage on live trades vs the 4h candle close BT would use.

For each closed live trade :
   entry slippage = direction × (live_entry_px − candle_close_at_entry_ts) / close × 1e4
   exit  slippage = direction × (candle_close_at_exit_ts − live_exit_px)  / close × 1e4
   round-trip slip = entry + exit

A positive RT slip = live paid MORE than BT would have. Aggregate gives the
"missing slippage cost" in BT.

Source : `analysis/output_live/reversal_ticks.db.trades`, full deployment period.
Compares with `BACKTEST_SLIPPAGE_BPS = 4.0` currently set in
`backtests/backtest_rolling.py`.
"""
from __future__ import annotations

import sqlite3
import json
import datetime as dt
from pathlib import Path
from collections import defaultdict

import numpy as np

DB = Path("analysis/output_live/reversal_ticks.db")
DATA = Path("analysis/output/pairs_data")
CURRENT_BT_SLIPPAGE_RT = 4.0  # bps, from backtests/backtest_rolling.py

def parse_ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "")).timestamp())


def find_candle_close_at(candles, ts_sec):
    """Find the 4h candle whose open time is the most recent at or before
    ts_sec. Return its close price. None if before data range."""
    ts_ms = ts_sec * 1000
    # candles are sorted by t (open time, ms)
    if not candles or ts_ms < candles[0]["t"]:
        return None
    # Binary-ish search
    lo, hi = 0, len(candles) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if candles[mid]["t"] <= ts_ms:
            lo = mid
        else:
            hi = mid - 1
    return float(candles[lo]["c"])


def main():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    rows = cur.execute("""
      SELECT symbol, strategy, direction, entry_time, exit_time,
             entry_price, exit_price, size_usdt, pnl_usdt, reason
      FROM trades
      WHERE exit_time IS NOT NULL
      ORDER BY entry_time
    """).fetchall()
    print(f"Total closed live trades: {len(rows)}\n")

    # Cache candle files
    candle_cache = {}
    def get_candles(sym):
        if sym not in candle_cache:
            p = DATA / f"{sym}_4h_3y.json"
            candle_cache[sym] = json.loads(p.read_text()) if p.exists() else []
        return candle_cache[sym]

    measured = []
    skipped = 0
    for r in rows:
        sym, strat, dirstr, et, xt, ep, xp, sz, pnl, reason = r
        d = 1 if dirstr == "LONG" else -1
        candles = get_candles(sym)
        if not candles:
            skipped += 1
            continue
        ets = parse_ts(et)
        xts = parse_ts(xt)
        entry_close = find_candle_close_at(candles, ets)
        exit_close = find_candle_close_at(candles, xts)
        if not entry_close or not exit_close:
            skipped += 1
            continue
        # Entry slippage = direction × (live_entry − bt_proxy) / bt_proxy in bps
        # For LONG, paying ABOVE the close is bad → positive slip cost
        entry_slip = d * (ep - entry_close) / entry_close * 1e4
        # Exit slippage: live exits at xp; BT would exit at exit_close
        # For LONG, BT_proxy − live_exit positive means live got worse exit → cost
        exit_slip = d * (exit_close - xp) / exit_close * 1e4
        rt = entry_slip + exit_slip
        measured.append({
            "sym": sym, "strat": strat, "dir": dirstr,
            "size": sz, "pnl": pnl, "reason": reason,
            "entry_slip_bps": entry_slip,
            "exit_slip_bps": exit_slip,
            "rt_slip_bps": rt,
        })

    print(f"Measured: {len(measured)} trades  (skipped {skipped} missing candles)\n")

    rt = np.array([m["rt_slip_bps"] for m in measured])
    es = np.array([m["entry_slip_bps"] for m in measured])
    xs = np.array([m["exit_slip_bps"] for m in measured])

    print(f"{'Metric':25s}  {'Entry bps':>10s}  {'Exit bps':>10s}  {'RT bps':>8s}")
    print("-" * 60)
    print(f"{'Mean':25s}  {es.mean():+10.2f}  {xs.mean():+10.2f}  {rt.mean():+8.2f}")
    print(f"{'Median':25s}  {np.median(es):+10.2f}  {np.median(xs):+10.2f}  {np.median(rt):+8.2f}")
    print(f"{'p25':25s}  {np.percentile(es, 25):+10.2f}  {np.percentile(xs, 25):+10.2f}  {np.percentile(rt, 25):+8.2f}")
    print(f"{'p75':25s}  {np.percentile(es, 75):+10.2f}  {np.percentile(xs, 75):+10.2f}  {np.percentile(rt, 75):+8.2f}")
    print(f"{'Std':25s}  {es.std():10.2f}  {xs.std():10.2f}  {rt.std():8.2f}")

    print(f"\n== Calibration vs current BT model ==")
    print(f"  BACKTEST_SLIPPAGE_BPS currently set: {CURRENT_BT_SLIPPAGE_RT} bps RT")
    print(f"  Measured live mean RT slippage:     {rt.mean():.2f} bps")
    print(f"  Measured live median RT slippage:   {np.median(rt):.2f} bps")
    print(f"  → suggested update: BACKTEST_SLIPPAGE_BPS = {max(CURRENT_BT_SLIPPAGE_RT, rt.mean()):.1f} bps")

    delta_per_trade = rt.mean() - CURRENT_BT_SLIPPAGE_RT
    print(f"\n  Per-trade gap underestimated: {delta_per_trade:+.2f} bps")
    print(f"  N trades: {len(measured)}")
    print(f"  Total $ gap from slippage misestimation: "
          f"${sum(m['size'] * delta_per_trade/1e4 for m in measured):+.2f}")

    print(f"\n== Per-strategy breakdown ==")
    by_strat = defaultdict(list)
    for m in measured:
        by_strat[m["strat"]].append(m["rt_slip_bps"])
    print(f"{'Strat':6s}  {'n':>4s}  {'mean RT bps':>12s}  {'median':>10s}")
    for s, vals in sorted(by_strat.items()):
        a = np.array(vals)
        print(f"{s:6s}  {len(a):>4d}  {a.mean():+12.2f}  {np.median(a):+10.2f}")

    print(f"\n== Top 10 worst slippage trades ==")
    worst = sorted(measured, key=lambda m: -m["rt_slip_bps"])[:10]
    print(f"{'sym':6s}  {'strat':5s}  {'dir':4s}  {'size$':>7s}  {'entry':>7s}  {'exit':>7s}  {'RT':>7s}  reason")
    for m in worst:
        print(f"  {m['sym']:6s}  {m['strat']:5s}  {m['dir']:4s}  {m['size']:>7.0f}  "
              f"{m['entry_slip_bps']:+7.1f}  {m['exit_slip_bps']:+7.1f}  {m['rt_slip_bps']:+7.1f}  {m['reason']}")


if __name__ == "__main__":
    main()
