"""Walk-forward 4/4 strict validation of the candidate gate
   `S5 LONG entries blocked when disp_7d >= 700`.

EDA on 6m live data (n=52) found Cliff's delta=+0.583 (LARGE) between
S5 LONG winners and losers on `disp_7d`, with perfect separation at
threshold 700 (15W/0L below vs 11W/26L above). Null-shuffle 0/5000.

This script tests the rule walk-forward across 28m/12m/6m/3m windows
of `backtest_rolling`, baseline (no extra gate) vs candidate (gate ON).

Strict 4/4 pass = ΔPnL > 0 on all 4 windows AND avg ΔDD ≤ +2pp.

Run: .venv/bin/python3 -m backtests.backtest_disp7d_gate
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import numpy as np

# Suppress backtest_rolling internal info prints in this script
from backtests.backtest_rolling import (
    load_3y_candles, build_features, compute_sector_features,
    load_dxy, load_oi, load_funding, run_window, rolling_windows,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

DISP_7D_THRESHOLD = 700.0  # candidate gate value


def precompute_disp_7d(features) -> dict[int, float]:
    """Cross-sectional std of ret_42h (7d return on 4h candles) per ts.
    Mirrors backtest_rolling line 1074-1076 logic.
    """
    from collections import defaultdict
    by_ts = defaultdict(list)
    for coin, fl in features.items():
        for f in fl:
            if "ret_42h" in f:
                by_ts[f["t"]].append(f["ret_42h"])
    return {ts: float(np.std(rets)) for ts, rets in by_ts.items() if len(rets) > 4}


def make_skip_fn(disp_7d_by_ts: dict[int, float], threshold: float):
    """skip_fn signature: (coin, ts, strat, direction) → bool (True = skip)."""
    def skip(coin: str, ts: int, strat: str, direction: int) -> bool:
        if strat != "S5" or direction != 1:
            return False
        d7 = disp_7d_by_ts.get(ts, 0.0)
        return d7 >= threshold
    return skip


def main():
    print("=" * 76)
    print(f"  Walk-forward: candidate gate `S5 LONG: disp_7d >= {DISP_7D_THRESHOLD:.0f}`")
    print("=" * 76)

    print("\nLoading data + features...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Precomputing disp_7d per ts...")
    disp_7d_by_ts = precompute_disp_7d(features)
    print(f"  ts entries: {len(disp_7d_by_ts)} (sample disp_7d range: "
          f"{min(disp_7d_by_ts.values()):.0f} → {max(disp_7d_by_ts.values()):.0f})")

    # Fine-grained windows up to 12m (per user directive — drop 28m, market regime
    # has shifted enough that older data isn't representative).
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    end_ts = latest_ts
    windows_cfg = [
        ("12m", end_dt - timedelta(days=365)),
        ("9m",  end_dt - timedelta(days=274)),
        ("6m",  end_dt - timedelta(days=182)),
        ("4m",  end_dt - timedelta(days=122)),
        ("3m",  end_dt - timedelta(days=91)),
        ("2m",  end_dt - timedelta(days=61)),
        ("1m",  end_dt - timedelta(days=30)),
    ]

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

    candidate_skip = make_skip_fn(disp_7d_by_ts, DISP_7D_THRESHOLD)

    common_kwargs = dict(
        start_capital=1000.0,
        oi_data=oi_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=True,
    )

    results = []
    print(f"\nEnd of data: {end_dt.date()}\n")
    for label, start_dt in windows_cfg:
        start_ts = int(start_dt.timestamp() * 1000)
        print(f"  Window {label} ({start_dt.date()} → {end_dt.date()})")
        baseline = run_window(features, data, sector_features, dxy_data,
                              start_ts, end_ts, skip_fn=None, **common_kwargs)
        candidate = run_window(features, data, sector_features, dxy_data,
                               start_ts, end_ts, skip_fn=candidate_skip, **common_kwargs)

        d_pnl = candidate["pnl_pct"] - baseline["pnl_pct"]
        d_dd = candidate["max_dd_pct"] - baseline["max_dd_pct"]   # less neg = better
        d_trades = candidate["n_trades"] - baseline["n_trades"]
        d_pnl_usdt = candidate["pnl"] - baseline["pnl"]

        # Count S5 LONG trades in each
        s5l_base = [t for t in baseline.get("trades", [])
                    if t.get("strat") == "S5" and t.get("dir") == 1]
        s5l_cand = [t for t in candidate.get("trades", [])
                    if t.get("strat") == "S5" and t.get("dir") == 1]
        s5l_pnl_base = sum(t.get("pnl", 0) for t in s5l_base)
        s5l_pnl_cand = sum(t.get("pnl", 0) for t in s5l_cand)

        results.append({
            "label": label, "start": start_dt.date().isoformat(),
            "base": baseline, "cand": candidate,
            "d_pnl_pct": d_pnl, "d_dd_pct": d_dd, "d_trades": d_trades,
            "d_pnl_usdt": d_pnl_usdt,
            "s5l_base_n": len(s5l_base), "s5l_cand_n": len(s5l_cand),
            "s5l_pnl_base": s5l_pnl_base, "s5l_pnl_cand": s5l_pnl_cand,
        })

        print(f"    baseline:  pnl={baseline['pnl_pct']:+.1f}% "
              f"(${baseline['pnl']:+.0f}), DD {baseline['max_dd_pct']:.1f}%, "
              f"{baseline['n_trades']} trades")
        print(f"    candidate: pnl={candidate['pnl_pct']:+.1f}% "
              f"(${candidate['pnl']:+.0f}), DD {candidate['max_dd_pct']:.1f}%, "
              f"{candidate['n_trades']} trades")
        print(f"    Δpnl: {d_pnl:+.1f}pp (${d_pnl_usdt:+.0f}) | "
              f"ΔDD: {d_dd:+.2f}pp | Δtrades: {d_trades:+d} | "
              f"S5 LONG: base={len(s5l_base)}(${s5l_pnl_base:+.0f}) "
              f"→ cand={len(s5l_cand)}(${s5l_pnl_cand:+.0f})")
        print()

    # ── Verdict ─────────────────────────────────────────────────────
    print("=" * 76)
    print("  SUMMARY")
    print("=" * 76)
    print(f"  {'Window':6} {'ΔPnL%':>10} {'ΔPnL$':>12} {'ΔDD pp':>10} {'Δtrades':>9} "
          f"{'S5L cut':>9} {'verdict':>9}")
    print("-" * 76)
    all_pnl_pos = True
    dd_sum = 0.0
    for r in results:
        s5l_cut = r["s5l_base_n"] - r["s5l_cand_n"]
        if r["d_pnl_pct"] <= 0:
            all_pnl_pos = False
            v = "FAIL"
        else:
            v = "PASS"
        dd_sum += r["d_dd_pct"]
        print(f"  {r['label']:6} {r['d_pnl_pct']:+10.1f} {r['d_pnl_usdt']:+12.0f} "
              f"{r['d_dd_pct']:+10.2f} {r['d_trades']:+9d} {s5l_cut:+9d} {v:>9}")
    dd_avg = dd_sum / len(results)
    print("-" * 76)
    print(f"  ΔDD avg: {dd_avg:+.2f}pp (gate: ≤ +2.0pp)")
    pnl_pass = all_pnl_pos
    dd_pass = dd_avg <= 2.0
    print()
    n_total = len(results)
    n_fail = sum(1 for r in results if r["d_pnl_pct"] <= 0)
    n_pass = n_total - n_fail
    if pnl_pass and dd_pass:
        print(f"  ✅ STRICT {n_total}/{n_total} PASS — candidate gate validates walk-forward.")
        print(f"     Ready to ship if user approves: add DISP_7D_GATE_BPS = {DISP_7D_THRESHOLD:.0f}")
    elif pnl_pass:
        print(f"  ⚠ {n_total}/{n_total} ΔPnL>0 but ΔDD avg {dd_avg:+.2f}pp > +2pp limit — "
              "drawdown degraded.")
    else:
        print(f"  ❌ {n_fail}/{n_total} windows FAIL on ΔPnL ({n_pass}/{n_total} PASS).")
    print()


if __name__ == "__main__":
    main()
