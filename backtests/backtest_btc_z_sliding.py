#!/usr/bin/env python3
"""Sliding walk-forward 18m-train / 6m-test : robust_multi vs baseline.

Goal : vérifier si le pattern observé (variant gagne 12m/6m, perd partiellement
sur 28m à cause de H1 2024) est stable across split positions. Si le variant
gagne SYSTÉMATIQUEMENT sur la test OOS, c'est un signal différent de "il gagne
juste par chance sur la fenêtre récente".

Splits (4-month stride) :
  Split 1 : train 2024-02 → 2025-08 (18m)  ;  test 2025-08 → 2026-02 (6m)
  Split 2 : train 2024-06 → 2025-12 (18m)  ;  test 2025-12 → 2026-06 (6m)

Note: with data ending ~2026-06-08, only 2 fully-complete splits at 18+6m are
possible. A third partial split with shorter test is also computed for context.

Usage : .venv/bin/python3 -m backtests.backtest_btc_z_sliding
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy, load_3y_candles,
)
from backtests.backtest_genetic import build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import MAX_NOTIONAL_PER_TRADE, COOLDOWN_HOURS


def _ts_ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def _month_offset(base_date: str, months: int) -> str:
    """Add `months` to base_date (rough — 30-day months)."""
    d = datetime.fromisoformat(base_date + "T00:00:00+00:00") + timedelta(days=months * 30)
    return d.strftime("%Y-%m-%d")


VARIANTS = ["baseline", "robust_multi"]
START_CAP = 500.0


def _run(features, data, sectors, dxy, oi, funding, start_ms, end_ms, variant):
    return run_window(
        features, data, sectors, dxy,
        start_ts_ms=start_ms, end_ts_ms=end_ms, start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
        apply_adaptive_modulator=True,
        max_notional_per_trade=MAX_NOTIONAL_PER_TRADE,
        margin_check=True, cooldown_hours=COOLDOWN_HOURS,
        btc_z_variant=variant,
    )


def main() -> int:
    print("=" * 80)
    print("  Sliding walk-forward 18m-train / 6m-test : robust_multi vs baseline")
    print("=" * 80)

    print("\n  Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi = load_oi()
    funding = load_funding()
    dxy = load_dxy()
    with open("backtests/output/pairs_data/BTC_4h_3y.json") as f:
        btc = json.load(f)
    data_end_ms = int(btc[-1]["t"])
    data_end_iso = datetime.fromtimestamp(data_end_ms / 1000, timezone.utc).isoformat()[:10]
    print(f"  Data ends : {data_end_iso}")

    # Build splits — 4-month stride, 18m train + 6m test
    # Start dates : 2024-02-04, 2024-06-04, 2024-10-04
    splits = []
    base_starts = ["2024-02-04", "2024-06-04", "2024-10-04"]
    for s in base_starts:
        train_start = _ts_ms(s)
        train_end_iso = _month_offset(s, 18)
        train_end = _ts_ms(train_end_iso)
        test_end_iso = _month_offset(s, 24)
        test_end = min(_ts_ms(test_end_iso), data_end_ms)
        # Only keep if test has at least 90 days of data
        test_days = (test_end - train_end) / (86400 * 1000)
        if test_days < 90:
            print(f"  SKIP split starting {s} : test window only {test_days:.0f} days")
            continue
        splits.append({
            "start": s,
            "train_end": train_end_iso,
            "test_end": datetime.fromtimestamp(test_end / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "train_start_ms": train_start,
            "train_end_ms": train_end,
            "test_end_ms": test_end,
            "test_days": test_days,
        })

    print(f"\n  {len(splits)} splits to run × {len(VARIANTS)} variants × 2 (train+test) = "
          f"{len(splits) * len(VARIANTS) * 2} runs\n")

    # Run all
    results = {}  # results[split_idx][variant][train|test] = {pnl, dd, n_trades, wr}
    for i, sp in enumerate(splits):
        results[i] = {}
        print(f"  ── Split {i+1} : train {sp['start']} → {sp['train_end']}  "
              f"test {sp['train_end']} → {sp['test_end']} ({sp['test_days']:.0f}d) ──")
        for v in VARIANTS:
            results[i][v] = {}
            for phase, s_ms, e_ms in [
                ("train", sp["train_start_ms"], sp["train_end_ms"]),
                ("test",  sp["train_end_ms"],   sp["test_end_ms"]),
            ]:
                r = _run(features, data, sectors, dxy, oi, funding, s_ms, e_ms, v)
                results[i][v][phase] = {
                    "pnl": r["pnl"],
                    "pnl_pct": r["pnl_pct"],
                    "dd_pct": r.get("max_dd_pct", 0),
                    "n_trades": r.get("n_trades", 0),
                    "wr": r.get("win_rate", 0),
                }
                print(f"    {v:13s} {phase:5s} : "
                      f"pnl ${r['pnl']:+8.0f} ({r['pnl_pct']:+7.1f}%) "
                      f"dd {r.get('max_dd_pct',0):6.1f}%  n={r.get('n_trades',0):4d}")

    # Summary table
    print("\n" + "=" * 80)
    print("  ATTRIBUTION : robust_multi − baseline, per phase per split")
    print("=" * 80)
    print(f"\n  {'Split':10s} {'Phase':5s} {'ΔPnL ($)':>10s} {'ΔPnL %':>10s} {'ΔDD (pp)':>10s}")
    pnl_gates_test = []
    dd_gates_test = []
    for i, sp in enumerate(splits):
        for phase in ("train", "test"):
            b = results[i]["baseline"][phase]
            r = results[i]["robust_multi"][phase]
            d_pnl = r["pnl"] - b["pnl"]
            d_pnl_pct = r["pnl_pct"] - b["pnl_pct"]
            d_dd = r["dd_pct"] - b["dd_pct"]
            label = f"split{i+1}"
            print(f"  {label:10s} {phase:5s} {d_pnl:+10.0f} {d_pnl_pct:+10.1f} {d_dd:+10.2f}")
            if phase == "test":
                pnl_gates_test.append(d_pnl > 0)
                dd_gates_test.append(d_dd <= 2.0)

    print("\n" + "=" * 80)
    print("  TEST (OOS) GATE — robust_multi must beat baseline on test windows")
    print("=" * 80)
    n = len(pnl_gates_test)
    p_ok = sum(pnl_gates_test)
    d_ok = sum(dd_gates_test)
    print(f"  PnL>0 on test  : {p_ok}/{n} {'✓' if p_ok == n else '✗'}")
    print(f"  ΔDD≤+2pp test  : {d_ok}/{n} {'✓' if d_ok == n else '✗'}")
    print(f"  Verdict        : {'PASS strict' if p_ok == n and d_ok == n else 'FAIL'}")

    # Save JSON
    out_path = "backtests/output/btc_z_sliding_walkforward.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "splits": [{"start": sp["start"], "train_end": sp["train_end"],
                        "test_end": sp["test_end"]} for sp in splits],
            "results": results,
            "pnl_gates_test": pnl_gates_test,
            "dd_gates_test": dd_gates_test,
        }, f, indent=2, default=str)
    print(f"\n  Saved : {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
