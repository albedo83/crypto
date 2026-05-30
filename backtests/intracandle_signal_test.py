"""Test if live-only trades are intra-candle signal fires that wouldn't have
triggered at the next 4h candle close.

For each live trade:
  - Find the 4h candle CONTAINING the entry time (entry_4h_candle)
  - Find the price at the entry_4h_candle CLOSE (what BT sees as "current price")
  - Quantify intra-candle price drift = entry_price vs candle_close

For live-only trades (not matched to a BT entry):
  - Did the BT take an entry on the same (coin, strat, dir) within ±4h?
  - If NO → confirmed intra-candle signal that died before BT could see it

If most live-only entries show large intra-candle price drift AND no
corresponding BT entry → confirms hypothesis : ~$257 of the gap = bot
firing on intra-candle prices that wouldn't have triggered at candle close.

Doesn't modify the BT engine — uses the existing BT trade list from
btlive output + live trade DB + 4h candle data.
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

def parse_ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "")).timestamp())


def find_4h_candle_at(candles, ts_sec):
    """Find the 4h candle (the one containing ts_sec). Returns (idx, open_ts, close_price)
    or None."""
    ts_ms = ts_sec * 1000
    if not candles or ts_ms < candles[0]["t"]:
        return None
    # Find latest candle with open time <= ts_sec
    lo, hi = 0, len(candles) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if candles[mid]["t"] <= ts_ms:
            lo = mid
        else:
            hi = mid - 1
    return (lo, candles[lo]["t"] // 1000, float(candles[lo]["c"]))


def find_next_4h_close(candles, ts_sec):
    """Find the FIRST 4h candle close at or after ts_sec.
    Returns close_price or None."""
    ts_ms = ts_sec * 1000
    for c in candles:
        if c["t"] >= ts_ms:
            return float(c["c"])
    return None


def main():
    # Load live closed trades
    c = sqlite3.connect(DB)
    rows = c.execute("""
      SELECT id, symbol, strategy, direction, entry_time, exit_time,
             entry_price, exit_price, size_usdt, pnl_usdt, reason
      FROM trades
      WHERE exit_time IS NOT NULL
      ORDER BY entry_time
    """).fetchall()
    print(f"Live closed trades: {len(rows)}\n")

    # Run BT for same window to get BT entries (we need a list of BT entries)
    # — reuse btlive's BT trade list. But to keep this simple, reload via run_window.
    print("Loading BT trades for the same window...")
    from backtests.backtest_genetic import load_3y_candles, build_features
    from backtests.backtest_sector import compute_sector_features
    from backtests.backtest_rolling import (
        run_window, load_dxy, load_oi, load_funding,
    )
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )
    data = load_3y_candles()
    feats = build_features(data)
    sec = compute_sector_features(feats, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c0["t"] for c0 in data["BTC"])
    start_ms = int(dt.datetime.fromisoformat("2026-03-26").timestamp() * 1000)
    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext = {
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    }
    res = run_window(feats, data, sec, dxy, start_ms, end_ts,
                      start_capital=500.0,
                      oi_data=oi, funding_data=fund,
                      early_exit_params=early_exit,
                      runner_extension=runner_ext,
                      apply_adaptive_modulator=True)
    bt_entries = []
    for t in res["trades"]:
        bt_entries.append({
            "coin": t["coin"], "strat": t["strat"], "dir": t["dir"],
            "entry_ts": int(t["entry_t"]) // 1000,  # to seconds
            "pnl": t.get("pnl", 0.0),
        })
    print(f"BT trades: {len(bt_entries)}")

    # Index BT entries by (coin, strat, dir)
    bt_by_key = defaultdict(list)
    for b in bt_entries:
        key = (b["coin"], b["strat"], b["dir"])
        bt_by_key[key].append(b)

    candle_cache = {}
    def get_candles(sym):
        if sym not in candle_cache:
            p = DATA / f"{sym}_4h_3y.json"
            candle_cache[sym] = json.loads(p.read_text()) if p.exists() else []
        return candle_cache[sym]

    # For each live trade: classify as Matched, Live-only, or BT-only-style
    matched_count = 0
    intracandle_dies = 0  # live-only AND no BT entry within ±4h
    intracandle_survives = 0  # live-only AND BT entry within ±4h
    matched_results = []
    intracandle_results = []  # (sym, strat, dir, entry_ts, entry_price, candle_close_price, drift_bps, pnl)

    for r in rows:
        tid, sym, strat, dirstr, et, xt, ep, xp, sz, pnl, reason = r
        d = 1 if dirstr == "LONG" else -1
        ets = parse_ts(et)
        candles = get_candles(sym)
        if not candles:
            continue
        # Find 4h candle containing entry
        candle_info = find_4h_candle_at(candles, ets)
        if not candle_info:
            continue
        idx, open_ts, candle_close_price = candle_info
        # The close of THIS 4h candle = open_ts + 4h
        candle_close_ts = open_ts + 4 * 3600
        # Drift bps: how much price moved between live entry and end-of-candle close
        if candle_close_price > 0:
            drift_bps = d * (candle_close_price - ep) / ep * 1e4
        else:
            drift_bps = 0
        # Now check if BT has an entry on same (coin, strat, dir) within ±4h of live entry
        key = (sym, strat, d)
        bt_match = None
        for b in bt_by_key.get(key, []):
            if abs(b["entry_ts"] - ets) <= 4 * 3600:
                bt_match = b
                break
        if bt_match:
            matched_count += 1
            matched_results.append((sym, strat, dirstr, ets, ep, candle_close_price, drift_bps, pnl, bt_match["pnl"]))
        else:
            intracandle_dies += 1
            intracandle_results.append((sym, strat, dirstr, ets, ep, candle_close_price, drift_bps, pnl, reason))

    print(f"\n══ Classification of {len(rows)} live trades ══")
    print(f"  Matched (live entry has corresponding BT entry within ±4h): {matched_count}")
    print(f"  Live-only intra-candle (no BT entry within ±4h):           {intracandle_dies}")
    print(f"  → these are signals that fired live but BT didn't see")

    print(f"\n══ Intra-candle signal hypothesis ══")
    print(f"For live-only trades, measure 'price drift' from live entry price to the")
    print(f"4h candle close price. If large drift = the signal trigger condition changed")
    print(f"between live entry and the candle close (BT would see different state).")

    drifts = [r[6] for r in intracandle_results]
    arr = np.array(drifts)
    print(f"\n  Live-only (n={len(arr)}): price drift entry→candle_close in bps")
    print(f"    Mean drift:    {arr.mean():+.1f} bps")
    print(f"    Median drift:  {np.median(arr):+.1f} bps")
    print(f"    p25 / p75:     {np.percentile(arr,25):+.1f} / {np.percentile(arr,75):+.1f}")
    print(f"    abs Mean:      {np.abs(arr).mean():.1f} bps")

    # Cumulative PnL of live-only
    sum_live_only = sum(r[7] for r in intracandle_results)
    print(f"\n  Cumulative PnL of intra-candle live-only trades: ${sum_live_only:+.2f}")
    print(f"  (btlive reported live-only contribution: $-257.22)")

    # Top 20 worst
    worst = sorted(intracandle_results, key=lambda r: r[7])[:15]
    print(f"\n  Top 15 worst intra-candle live-only trades:")
    print(f"  {'sym':6s}  {'strat':5s}  {'dir':4s}  {'entry':18s}  {'live_px':>10s}  {'cdl_close':>10s}  {'drift bps':>10s}  {'pnl $':>8s}  reason")
    for sym, strat, dirstr, ets, ep, cp, drift, pnl, reason in worst:
        et_str = dt.datetime.fromtimestamp(ets, dt.UTC).strftime("%m-%d %H:%M")
        print(f"  {sym:6s}  {strat:5s}  {dirstr:4s}  {et_str:18s}  {ep:>10.4f}  {cp:>10.4f}  {drift:+10.1f}  {pnl:+8.2f}  {reason}")

    # Also matched : check drift bps distribution for SAME population
    if matched_results:
        m_drifts = np.array([r[6] for r in matched_results])
        print(f"\n  CONTROL — matched live trades (BT took same trade): drift entry→candle_close")
        print(f"    Mean:    {m_drifts.mean():+.1f} bps")
        print(f"    Median:  {np.median(m_drifts):+.1f} bps")
        print(f"    abs Mean:  {np.abs(m_drifts).mean():.1f} bps")
        print(f"  → if |drift| similar for matched vs unmatched, then drift alone doesn't")
        print(f"    explain why live-only signals died (other features must change too).")


if __name__ == "__main__":
    main()
