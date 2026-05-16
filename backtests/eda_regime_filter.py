"""EDA — BTC short-term pre-entry momentum vs strategy outcome.

Premise check (per memory `feedback_premise_gate_before_sweep`): does BTC return
in the -4h/-8h/-24h window before entry predict P&L for specific (strategy, direction)?

If YES on at least one (strat, dir) → premise PASS → run walk-forward sweep.
If NO across all → premise FAIL → classer, no sweep waste.

Strategies targeted (based on live divergence analysis 2026-05-16):
- S5 LONG  : 2 currently open at MAE -353/-573 bps, premier sample live
- S5 SHORT : n=53 live, total -$14, marginally losing
- S9 SHORT : n=4 live, total -$39, avg -453 bps — catastrophic
- S10 SHORT: control — performing OK live (+$7 on n=36)

Output: per (strat, dir), table with BTC pre-momentum bucket vs WR, avg_pnl, n.

Usage:
    .venv/bin/python3 -m backtests.eda_regime_filter
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

# Import backtest engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding,
)
from backtests.backtest_genetic import (
    load_3y_candles, build_features,
)
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import load_dxy
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)


def main():
    print("=" * 90)
    print("EDA — BTC short-term momentum × (strategy, direction) outcome")
    print("=" * 90)

    print("\nLoading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()
    print(f"  {len(data)} coins")

    # Latest BTC ts → end of window
    btc_candles = sorted(data["BTC"], key=lambda c: c["t"])
    btc_ts = np.array([c["t"] for c in btc_candles])  # ms
    btc_close = np.array([float(c["c"]) for c in btc_candles])
    end_ts_ms = int(btc_ts[-1])

    # 28-month window
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    start_dt = datetime(end_dt.year, end_dt.month, end_dt.day, tzinfo=timezone.utc)
    # 28 months back, approx
    from dateutil.relativedelta import relativedelta
    start_dt = end_dt - relativedelta(months=28)
    start_ts_ms = int(start_dt.timestamp() * 1000)

    print(f"Window: {start_dt.date()} → {end_dt.date()} (~28 months)")

    # Production parameters (mirror live bot)
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

    print("\nRunning 28m backtest with production params + adaptive modulator...")
    r = run_window(
        features, data, sector_features, dxy_data,
        start_ts_ms, end_ts_ms,
        start_capital=500.0,
        oi_data=oi_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=True,
    )
    print(f"  → {r['n_trades']} trades, final ${r['end_capital']:.0f} ({r['pnl_pct']:+.1f}%)")
    trades = r["trades"]

    # ── Compute BTC pre-entry returns ──────────────────────────────────────
    def btc_return(ts_ms_target: int, hours_back: int) -> float | None:
        """Return BTC % change from (ts - hours_back) to ts."""
        ts_start = ts_ms_target - hours_back * 3600 * 1000
        if ts_start < btc_ts[0] or ts_ms_target > btc_ts[-1]:
            return None
        end_idx = int(np.searchsorted(btc_ts, ts_ms_target, side="right")) - 1
        start_idx = int(np.searchsorted(btc_ts, ts_start, side="right")) - 1
        if end_idx < 0 or start_idx < 0:
            return None
        p_end = btc_close[end_idx]
        p_start = btc_close[start_idx]
        if p_start <= 0:
            return None
        return (p_end / p_start - 1) * 100  # pct

    # Enrich trades with BTC pre-momentum
    enriched = []
    for t in trades:
        et_ms = t.get("entry_t")
        if not et_ms:
            continue
        b24 = btc_return(et_ms, 24)
        b8 = btc_return(et_ms, 8)
        b4 = btc_return(et_ms, 4)
        if b24 is None or b8 is None or b4 is None:
            continue
        enriched.append({
            "strat": t["strat"],
            "dir": t["dir"],
            "coin": t["coin"],
            "entry_t": et_ms,
            "pnl": t.get("pnl", 0),
            "net": t.get("net", 0),
            "mfe": t.get("mfe_bps", 0),
            "mae": t.get("mae_bps", 0),
            "reason": t.get("reason", ""),
            "btc_24h": b24,
            "btc_8h": b8,
            "btc_4h": b4,
        })

    print(f"\nEnriched {len(enriched)} trades with BTC pre-momentum context.")

    # ── Per-strategy analysis ──────────────────────────────────────────────
    TARGETS = [
        ("S5", 1, "S5 LONG"),
        ("S5", -1, "S5 SHORT"),
        ("S9", -1, "S9 SHORT"),
        ("S10", -1, "S10 SHORT (control)"),
        ("S1", -1, "S1 SHORT (control)"),
        ("S8", 1, "S8 LONG (control)"),
    ]

    # Bucket edges for BTC return
    BUCKETS = [
        ("BTC <= -5%",  -100, -5),
        ("-5% < BTC <= -2%", -5, -2),
        ("-2% < BTC <= -0.5%", -2, -0.5),
        ("-0.5% < BTC < +0.5%", -0.5, 0.5),
        ("+0.5% <= BTC < +2%", 0.5, 2),
        ("+2% <= BTC < +5%", 2, 5),
        ("+5% <= BTC", 5, 100),
    ]

    results_summary = {}

    for window_name, win_attr in [("24h", "btc_24h"), ("8h", "btc_8h"), ("4h", "btc_4h")]:
        print(f"\n{'='*90}")
        print(f"BUCKETED ANALYSIS by BTC return over -{window_name} preceding entry")
        print(f"{'='*90}")

        for strat, direction, label in TARGETS:
            trades_sd = [t for t in enriched if t["strat"] == strat and t["dir"] == direction]
            if not trades_sd:
                continue
            total = sum(t["pnl"] for t in trades_sd)
            wr = sum(1 for t in trades_sd if t["pnl"] > 0) / len(trades_sd) * 100
            avg_net = float(np.mean([t["net"] for t in trades_sd]))

            print(f"\n  {label}  — n={len(trades_sd)}  total=${total:+.0f}  avg_net={avg_net:+.0f}bps  WR={wr:.0f}%")
            print(f"  {'bucket':<25} {'n':>4} {'total':>8} {'avg':>7} {'avg_net':>8} {'WR':>5}")

            for bname, lo, hi in BUCKETS:
                bucket_trades = [t for t in trades_sd if lo <= t[win_attr] < hi]
                if not bucket_trades:
                    continue
                bn = len(bucket_trades)
                btot = sum(t["pnl"] for t in bucket_trades)
                bavg = btot / bn
                bavg_net = float(np.mean([t["net"] for t in bucket_trades]))
                bwr = sum(1 for t in bucket_trades if t["pnl"] > 0) / bn * 100
                results_summary.setdefault((strat, direction, window_name), []).append({
                    "bucket": bname, "n": bn, "total": btot,
                    "avg": bavg, "avg_net": bavg_net, "wr": bwr,
                })
                flag = ""
                if bwr < 35 and bn >= 8:
                    flag = " ⚠ low WR"
                if bavg_net < -200 and bn >= 8:
                    flag += " ⚠ avg_net deep red"
                print(f"  {bname:<25} {bn:>4} ${btot:>+7.0f} ${bavg:>+6.1f} {bavg_net:>+7.0f}bps {bwr:>4.0f}%{flag}")

    # ── Premise verdict ────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"PREMISE GATE VERDICT (looking for non-monotone signal per strat)")
    print(f"{'='*90}")

    for strat, direction, label in TARGETS:
        for window_name in ("24h", "8h", "4h"):
            key = (strat, direction, window_name)
            buckets = results_summary.get(key, [])
            if len(buckets) < 3:
                continue
            # Look for asymmetry: leftmost (BTC down) avg_net vs rightmost (BTC up)
            valid = [b for b in buckets if b["n"] >= 5]
            if len(valid) < 3:
                continue
            avg_net_vals = [b["avg_net"] for b in valid]
            spread = max(avg_net_vals) - min(avg_net_vals)
            # Find worst bucket
            worst = min(valid, key=lambda b: b["avg_net"])
            best = max(valid, key=lambda b: b["avg_net"])
            verdict = ""
            if spread > 300 and worst["avg_net"] < -100:
                # Significant signal
                verdict = f"  ⭐ SIGNAL: worst={worst['bucket']} avg_net={worst['avg_net']:+.0f}bps (n={worst['n']}) | best={best['bucket']} {best['avg_net']:+.0f}bps (n={best['n']}) | spread={spread:.0f}bps"
            elif spread > 200 and worst["avg_net"] < -50:
                verdict = f"  📊 weak: spread={spread:.0f}bps, worst={worst['bucket']} ({worst['avg_net']:+.0f}bps)"
            if verdict:
                print(f"\n  {label} × BTC-{window_name}:")
                print(verdict)

    # Save trades dump for downstream walk-forward (if premise PASS)
    out_path = os.path.join(os.path.dirname(__file__), "output", "eda_regime_trades.jsonl")
    with open(out_path, "w") as fh:
        for t in enriched:
            fh.write(json.dumps(t) + "\n")
    print(f"\nTrades dump saved: {out_path} ({len(enriched)} rows)")
    print("\nDone.")


if __name__ == "__main__":
    main()
