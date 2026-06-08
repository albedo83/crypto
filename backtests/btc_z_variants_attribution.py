#!/usr/bin/env python3
"""Attribution analysis : WHEN do btc_z variants diverge from baseline on 28m?

Re-runs baseline + robust_multi on the 28m window with trade-level capture,
then analyzes:
  - Cumulative PnL drift by month
  - Strategy-direction breakdown of the gap
  - Identify the 2-3 months that drive the 28m underperformance

Usage : .venv/bin/python3 -m backtests.btc_z_variants_attribution
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy, load_3y_candles,
)
from backtests.backtest_genetic import build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import MAX_NOTIONAL_PER_TRADE, COOLDOWN_HOURS


def _ts_ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def main() -> int:
    print("=" * 78)
    print("  Attribution : baseline vs robust_multi on 28m")
    print("=" * 78)

    print("\n  Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi = load_oi()
    funding = load_funding()
    dxy = load_dxy()
    with open("backtests/output/pairs_data/BTC_4h_3y.json") as f:
        btc = json.load(f)
    end_ms = int(btc[-1]["t"])

    start_ms = _ts_ms("2024-02-04")
    print(f"  Window : 2024-02-04 → {datetime.fromtimestamp(end_ms/1000, timezone.utc).isoformat()[:10]}")

    results = {}
    for v in ("baseline", "robust_multi"):
        print(f"\n  Running {v}...")
        r = run_window(
            features, data, sectors, dxy,
            start_ts_ms=start_ms, end_ts_ms=end_ms, start_capital=500.0,
            oi_data=oi, funding_data=funding,
            apply_adaptive_modulator=True,
            max_notional_per_trade=MAX_NOTIONAL_PER_TRADE,
            margin_check=True, cooldown_hours=COOLDOWN_HOURS,
            btc_z_variant=v,
        )
        results[v] = r
        print(f"  {v:13s} : final ${r['end_capital']:>10.0f} (pnl ${r['pnl']:+.0f}, n={r['n_trades']})")

    # Per-month cumulative PnL comparison
    print("\n" + "=" * 78)
    print("  CUMULATIVE PnL DRIFT BY MONTH  (positive = baseline ahead)")
    print("=" * 78)

    by_month = {"baseline": defaultdict(float), "robust_multi": defaultdict(float)}
    by_strat = {"baseline": defaultdict(float), "robust_multi": defaultdict(float)}
    by_month_strat = {"baseline": defaultdict(lambda: defaultdict(float)),
                       "robust_multi": defaultdict(lambda: defaultdict(float))}

    for v in ("baseline", "robust_multi"):
        for t in results[v].get("trades", []):
            try:
                exit_ms = t.get("exit_t", 0)
                if not exit_ms:
                    continue
                dt = datetime.fromtimestamp(exit_ms / 1000, timezone.utc)
                month = dt.strftime("%Y-%m")
            except Exception:
                continue
            pnl = float(t.get("pnl", 0))
            strat = t.get("strat", "?")
            direction = "LONG" if t.get("dir", 0) == 1 else "SHORT"
            key = f"{strat}_{direction}"
            by_month[v][month] += pnl
            by_strat[v][key] += pnl
            by_month_strat[v][month][key] += pnl

    # All months that appear in either run
    months = sorted(set(by_month["baseline"].keys()) | set(by_month["robust_multi"].keys()))

    # Cumulative running sum
    print(f"\n  {'Month':10s} {'Baseline':>10s} {'Robust_M':>10s} {'Δ this':>10s} {'cum Δ':>10s}")
    cum = 0.0
    rows = []
    for m in months:
        b = by_month["baseline"].get(m, 0.0)
        r = by_month["robust_multi"].get(m, 0.0)
        d = r - b  # positive = robust_multi ahead, negative = baseline ahead
        cum += d
        rows.append((m, b, r, d, cum))
        flag = ""
        if abs(d) > 200:
            flag = "  ← big swing"
        print(f"  {m:10s} {b:+10.0f} {r:+10.0f} {d:+10.0f} {cum:+10.0f}{flag}")

    # Identify the top 5 months by absolute delta
    print("\n" + "=" * 78)
    print("  TOP 5 MONTHS BY ABSOLUTE Δ  (robust_multi − baseline)")
    print("=" * 78)
    sorted_rows = sorted(rows, key=lambda x: abs(x[3]), reverse=True)[:5]
    for m, b, r, d, _ in sorted_rows:
        sign = "robust_multi ahead" if d > 0 else "BASELINE ahead"
        print(f"  {m} : baseline ${b:+8.0f}  robust_multi ${r:+8.0f}  Δ ${d:+8.0f}  ({sign})")
        # Top strategies in that month for the delta
        for key in sorted(by_month_strat["baseline"][m].keys() | by_month_strat["robust_multi"][m].keys(),
                           key=lambda k: abs(by_month_strat["robust_multi"][m].get(k, 0) -
                                              by_month_strat["baseline"][m].get(k, 0)),
                           reverse=True)[:4]:
            b_strat = by_month_strat["baseline"][m].get(key, 0)
            r_strat = by_month_strat["robust_multi"][m].get(key, 0)
            d_strat = r_strat - b_strat
            print(f"    {key:13s} : baseline ${b_strat:+7.0f}  variant ${r_strat:+7.0f}  Δ ${d_strat:+7.0f}")

    # Strategy-direction totals
    print("\n" + "=" * 78)
    print("  STRATEGY-DIRECTION TOTALS over 28m")
    print("=" * 78)
    all_keys = sorted(set(by_strat["baseline"].keys()) | set(by_strat["robust_multi"].keys()))
    print(f"\n  {'Strat_Dir':14s} {'Baseline':>10s} {'Robust_M':>10s} {'Δ':>10s}")
    for k in all_keys:
        b = by_strat["baseline"].get(k, 0.0)
        r = by_strat["robust_multi"].get(k, 0.0)
        d = r - b
        flag = "  ← biggest hit" if abs(d) > 500 else ""
        print(f"  {k:14s} {b:+10.0f} {r:+10.0f} {d:+10.0f}{flag}")

    # Summary
    print("\n" + "=" * 78)
    final_b = results["baseline"]["pnl"]
    final_r = results["robust_multi"]["pnl"]
    print(f"  Total over 28m : baseline ${final_b:+.0f}, robust_multi ${final_r:+.0f}, Δ ${final_r-final_b:+.0f}")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(main())
