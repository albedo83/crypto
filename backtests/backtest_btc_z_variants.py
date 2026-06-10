#!/usr/bin/env python3
"""Walk-forward 4/4 strict — compare 4 btc_z variants for v12.18.0 R&D.

Variants:
  baseline     : current (mean + std on ret_30d / 180d) — what live runs today
  robust       : median + MAD on ret_30d / 180d (whale-liquidation tolerant)
  multi        : 0.6 × baseline + 0.4 × (ret_7d on 60d, mean+std)
  robust_multi : 0.6 × robust + 0.4 × (ret_7d on 60d, MAD)

Acceptance gate : strict 4/4 over (28m, 12m, 6m, 3m) windows
  - ΔPnL > 0 in each window
  - ΔDD ≤ +2pp in each window

Usage : .venv/bin/python3 -m backtests.backtest_btc_z_variants

The btc_z value drives :
  - Adaptive modulator (S1/S8/S9 + S5 SHORT size scaling)
  - traj_cut exit (S5 LONG cut at MAE in bear regime)
  - s8_inlife trail (S8 in-life trail by regime bucket)
  - prop_trail (S9 bull-only proportional trail)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

# Make backtest package importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests import backtest_rolling as br
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy, load_3y_candles,
)
from backtests.backtest_genetic import build_features
from backtests.backtest_sector import compute_sector_features

# Production knobs — same as docs/backtests.md generator
from analysis.bot.config import (
    SIZE_PCT, STOP_LOSS_BPS, STOP_LOSS_S8, HOLD_HOURS_DEFAULT, HOLD_HOURS_S5,
    HOLD_HOURS_S8, MAX_NOTIONAL_PER_TRADE, COOLDOWN_HOURS,
)

VARIANTS = ["baseline", "winsorize", "adaptive_window"]

WINDOWS = [
    ("28m", "2024-02-04"),
    ("12m", "2025-06-04"),
    ("6m",  "2025-12-04"),
    ("3m",  "2026-03-04"),
]

START_CAP = 500.0


def _ts_ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def _end_ts_ms() -> int:
    """End at the latest 4h-aligned timestamp covered by data."""
    btc_path = "backtests/output/pairs_data/BTC_4h_3y.json"
    with open(btc_path) as f:
        btc = json.load(f)
    return int(btc[-1]["t"])


def main() -> int:
    print("=" * 78)
    print("  Walk-forward 4/4 — btc_z variants  (v12.18.0 R&D)")
    print("=" * 78)
    print()
    print(f"  Variants     : {VARIANTS}")
    print(f"  Windows      : {[w[0] for w in WINDOWS]}")
    print(f"  Capital      : ${START_CAP:.0f}")
    print()

    # Load all data once
    print("  Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi = load_oi()
    funding = load_funding()
    dxy = load_dxy()
    end_ms = _end_ts_ms()
    end_dt = datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat()[:16]
    print(f"  Data through : {end_dt}")
    print()

    # Run all 4 variants on all 4 windows
    results: dict[str, dict[str, dict]] = {v: {} for v in VARIANTS}
    for v in VARIANTS:
        for win_name, start_date in WINDOWS:
            t0 = time.time()
            r = run_window(
                features, data, sectors, dxy,
                start_ts_ms=_ts_ms(start_date),
                end_ts_ms=end_ms,
                start_capital=START_CAP,
                oi_data=oi,
                funding_data=funding,
                apply_adaptive_modulator=True,
                max_notional_per_trade=MAX_NOTIONAL_PER_TRADE,
                margin_check=True,
                cooldown_hours=COOLDOWN_HOURS,
                btc_z_variant=v,
            )
            elapsed = time.time() - t0
            results[v][win_name] = {
                "balance": r["end_capital"],
                "pnl": r["pnl"],
                "pnl_pct": r["pnl_pct"],
                "dd_pct": r.get("max_dd_pct", 0),
                "n_trades": r.get("n_trades", 0),
                "wr": r.get("win_rate", 0),
            }
            print(f"  {v:13s} | {win_name:3s} | "
                  f"pnl=${results[v][win_name]['pnl']:+9.0f} "
                  f"({results[v][win_name]['pnl_pct']:+8.1f}%) "
                  f"dd={results[v][win_name]['dd_pct']:6.1f}% "
                  f"n={results[v][win_name]['n_trades']:4d} "
                  f"wr={results[v][win_name]['wr']:5.1f}% "
                  f"({elapsed:.0f}s)")

    # Compare each variant vs baseline
    print()
    print("=" * 78)
    print("  ATTRIBUTION  ('robust' − 'baseline', 'multi' − 'baseline', etc.)")
    print("=" * 78)
    print()

    for v in [x for x in VARIANTS if x != "baseline"]:
        print(f"  [{v}] vs baseline:")
        print(f"    {'win':5s} {'ΔPnL ($)':>12s} {'ΔPnL %':>10s} {'ΔDD (pp)':>10s} {'Δtrades':>8s}")
        n_pnl_pos = 0
        n_dd_ok = 0
        for win_name, _ in WINDOWS:
            b = results["baseline"][win_name]
            t = results[v][win_name]
            d_pnl = t["pnl"] - b["pnl"]
            d_pnl_pct = t["pnl_pct"] - b["pnl_pct"]
            d_dd = t["dd_pct"] - b["dd_pct"]
            d_n = t["n_trades"] - b["n_trades"]
            n_pnl_pos += 1 if d_pnl > 0 else 0
            n_dd_ok += 1 if d_dd <= 2.0 else 0
            print(f"    {win_name:5s} {d_pnl:+12.0f} {d_pnl_pct:+10.1f} {d_dd:+10.2f} {d_n:+8d}")
        gate_pnl = "✓" if n_pnl_pos == 4 else "✗"
        gate_dd = "✓" if n_dd_ok == 4 else "✗"
        verdict = "PASS strict 4/4" if (n_pnl_pos == 4 and n_dd_ok == 4) else "FAIL"
        print(f"    {'gate':5s}    PnL>0: {n_pnl_pos}/4 {gate_pnl}  "
              f"  ΔDD≤+2pp: {n_dd_ok}/4 {gate_dd}    → {verdict}")
        print()

    # Save full JSON
    out_path = "backtests/output/btc_z_variants_walkforward.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "version": "12.18.0-rnd",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_through": end_dt,
            "start_capital": START_CAP,
            "windows": [w[0] for w in WINDOWS],
            "results": results,
        }, f, indent=2)
    print(f"  Saved : {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
