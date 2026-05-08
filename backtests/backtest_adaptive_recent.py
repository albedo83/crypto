"""Adaptive modulator — focused on RECENT market regime only.

The 28m walk-forward includes old regimes that may not represent today.
Test the conservative α (S1+0.5, S8-0.5, S9-0.5) on recent rolling windows
to estimate REAL near-term impact.

Also test individual extremes (α=±1.0 for each strat) on recent data to
understand which strats are currently the strongest contributors.

Usage:
    python3 -m backtests.backtest_adaptive_recent
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
import numpy as np
from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0


def compute_btc_z_full(data: dict, lookback_days: int = 30) -> dict:
    btc = data["BTC"]
    n = lookback_days * 6
    closes = np.array([c["c"] for c in btc])
    rets, ts_list = [], []
    for i in range(n, len(btc)):
        ret = (closes[i] / closes[i - n] - 1) if closes[i - n] > 0 else 0
        rets.append(ret)
        ts_list.append(btc[i]["t"])
    rets_arr = np.array(rets)
    mean = float(np.mean(rets_arr))
    std = float(np.std(rets_arr)) or 1.0
    return {ts: (r - mean) / std for ts, r in zip(ts_list, rets_arr)}


def compute_btc_z_rolling(data: dict, lookback_days: int = 30,
                           z_window_days: int = 180) -> dict:
    btc = data["BTC"]
    n_lb = lookback_days * 6
    n_z = z_window_days * 6
    closes = np.array([c["c"] for c in btc])
    rets, ts_list = [], []
    for i in range(n_lb, len(btc)):
        ret = (closes[i] / closes[i - n_lb] - 1) if closes[i - n_lb] > 0 else 0
        rets.append(ret)
        ts_list.append(btc[i]["t"])
    out = {}
    for j in range(len(rets)):
        win_start = max(0, j - n_z)
        past = rets[win_start:j+1]
        if len(past) < 30: continue
        m = float(np.mean(past))
        s = float(np.std(past)) or 1.0
        out[ts_list[j]] = (rets[j] - m) / s
    return out


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    btc_z_full = compute_btc_z_full(data, 30)
    btc_z_roll = compute_btc_z_rolling(data, 30, 180)

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        end_ts_ms=latest_ts, start_capital=CAP,
        oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
    )

    # Focus: very recent windows + live deployment date
    WINDOWS = [
        ("1m",  int((end_dt - relativedelta(months=1)).timestamp() * 1000)),
        ("3m",  int((end_dt - relativedelta(months=3)).timestamp() * 1000)),
        ("6m",  int((end_dt - relativedelta(months=6)).timestamp() * 1000)),
        ("12m", int((end_dt - relativedelta(months=12)).timestamp() * 1000)),
        ("live(43d)", int(datetime(2026, 3, 26, tzinfo=timezone.utc).timestamp() * 1000)),
    ]

    def make_fn(av, z_map):
        def fn(cand, f, n_pos):
            a = av.get(cand["strat"], 0)
            return max(0.3, min(2.5, 1 + a * z_map.get(f["t"], 0)))
        return fn

    print(f"\nBaseline (current static parameters, no modulator):")
    print(f"  {'window':12s}  {'pnl%':>9s}  {'trades':>6s}  {'DD%':>6s}")
    baseline = {}
    for label, start_ts in WINDOWS:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label:12s}  {r['pnl_pct']:+8.1f}%  {r['n_trades']:6d}  {r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results = []

    def run_test(name, alphas, z_map):
        size_fn = make_fn(alphas, z_map)
        result = {"name": name, "rows": []}
        for label, start_ts in WINDOWS:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common)
            d_pnl = r['pnl_pct'] - baseline[label]['pnl_pct']
            d_dd  = r['max_dd_pct'] - baseline[label]['max_dd_pct']
            d_n   = r['n_trades'] - baseline[label]['n_trades']
            result["rows"].append((label, r['pnl_pct'], d_pnl, d_dd, d_n))
        all_results.append(result)
        return result

    # ── Test 1: Conservative recommended (S1+0.5, S8-0.5, S9-0.5) — rolling z ──
    print("\n" + "=" * 105)
    print(f"{'TEST 1 — Conservative ROLLING z-score (live-realistic): S1+0.5  S8-0.5  S9-0.5':^105}")
    print("=" * 105)
    r = run_test("conservative-rolling", {"S1": 0.5, "S8": -0.5, "S9": -0.5}, btc_z_roll)
    print(f"  {'window':12s}  {'pnl%':>9s}  {'Δpnl%':>8s}  {'ΔDD':>7s}  {'Δtr':>5s}")
    for lab, pnl, dpnl, dd, dn in r["rows"]:
        sign = "✓" if dpnl > 0 else "✗"
        print(f"  {lab:12s}  {pnl:+8.1f}%  {dpnl:+7.1f}pp  {dd:+6.1f}pp  {dn:+5d}  {sign}")

    # ── Test 2: Same but FULL z-score (look-ahead, theoretical max) ──
    print("\n" + "=" * 105)
    print(f"{'TEST 2 — Conservative FULL z-score (theoretical, full-sample mean/std)':^105}")
    print("=" * 105)
    r = run_test("conservative-full", {"S1": 0.5, "S8": -0.5, "S9": -0.5}, btc_z_full)
    print(f"  {'window':12s}  {'pnl%':>9s}  {'Δpnl%':>8s}  {'ΔDD':>7s}  {'Δtr':>5s}")
    for lab, pnl, dpnl, dd, dn in r["rows"]:
        sign = "✓" if dpnl > 0 else "✗"
        print(f"  {lab:12s}  {pnl:+8.1f}%  {dpnl:+7.1f}pp  {dd:+6.1f}pp  {dn:+5d}  {sign}")

    # ── Test 3: Aggressive (S1+1.0, S8-1.0, S9-1.0) — rolling ──
    print("\n" + "=" * 105)
    print(f"{'TEST 3 — Aggressive ROLLING (α=±1.0): risk of overfit but max upside':^105}")
    print("=" * 105)
    r = run_test("aggressive-rolling", {"S1": 1.0, "S8": -1.0, "S9": -1.0}, btc_z_roll)
    print(f"  {'window':12s}  {'pnl%':>9s}  {'Δpnl%':>8s}  {'ΔDD':>7s}  {'Δtr':>5s}")
    for lab, pnl, dpnl, dd, dn in r["rows"]:
        sign = "✓" if dpnl > 0 else "✗"
        print(f"  {lab:12s}  {pnl:+8.1f}%  {dpnl:+7.1f}pp  {dd:+6.1f}pp  {dn:+5d}  {sign}")

    # ── Test 4: Per-strat individual contribution (rolling) ──
    print("\n" + "=" * 105)
    print(f"{'TEST 4 — Per-strat α=−0.5/+0.5 ROLLING — isolated contributions':^105}")
    print("=" * 105)
    for strat, a in [("S1", +0.5), ("S5", -0.5), ("S8", -0.5), ("S9", -0.5), ("S10", -0.5)]:
        r = run_test(f"{strat}={a:+.1f}", {strat: a}, btc_z_roll)
        sym = "+" if a > 0 else ""
        line = f"  α[{strat}]={sym}{a:.1f}:"
        for lab, _, dpnl, dd, _ in r["rows"]:
            line += f"  {lab}={dpnl:+6.1f}%"
        print(line)

    # ── Test 5: Different window starting from live deployment date ──
    print("\n" + "=" * 105)
    print(f"{'TEST 5 — On LIVE deployment window (2026-03-26 → today, 43d)':^105}")
    print("=" * 105)
    print(f"  Baseline: pnl={baseline['live(43d)']['pnl_pct']:+.1f}%  trades={baseline['live(43d)']['n_trades']}  DD={baseline['live(43d)']['max_dd_pct']:+.1f}%")
    print()
    print(f"  {'config':50s}  {'pnl%':>9s}  {'Δpnl%':>8s}  {'ΔDD':>7s}  status")
    configs_for_live = [
        ("Conservative S1+0.5 S8-0.5 S9-0.5 (rolling)",
         {"S1": 0.5, "S8": -0.5, "S9": -0.5}, btc_z_roll),
        ("Aggressive   S1+1.0 S8-1.0 S9-1.0 (rolling)",
         {"S1": 1.0, "S8": -1.0, "S9": -1.0}, btc_z_roll),
        ("Conservative + S5/S10 too (rolling)",
         {"S1": 0.5, "S5": -0.5, "S8": -0.5, "S9": -0.5, "S10": -0.5}, btc_z_roll),
        ("Just S9 -0.5 (single, conservative)",
         {"S9": -0.5}, btc_z_roll),
        ("Just S8 -0.5 (single, conservative)",
         {"S8": -0.5}, btc_z_roll),
        ("Just S1 +0.5 (single, conservative)",
         {"S1": 0.5}, btc_z_roll),
    ]
    for name, av, z_map in configs_for_live:
        size_fn = make_fn(av, z_map)
        r = run_window(features, data, start_ts_ms=WINDOWS[4][1], size_fn=size_fn, **common)
        dpnl = r['pnl_pct'] - baseline['live(43d)']['pnl_pct']
        ddd = r['max_dd_pct'] - baseline['live(43d)']['max_dd_pct']
        sign = "✓" if dpnl > 0 else "✗"
        print(f"  {name:50s}  {r['pnl_pct']:+8.1f}%  {dpnl:+7.1f}pp  {ddd:+6.1f}pp  {sign}")

    # ── Summary table — what to expect on recent regimes ──
    print("\n" + "=" * 105)
    print(f"{'SUMMARY — Δpnl% expected on recent windows (rolling z, conservative S1+0.5 S8-0.5 S9-0.5)':^105}")
    print("=" * 105)
    cons = [r for r in all_results if r["name"] == "conservative-rolling"][0]
    print(f"  {'window':12s}  {'Baseline pnl':>12s}  {'+modulator':>12s}  {'Δpnl':>9s}  {'ΔDD':>8s}")
    for lab, pnl, dpnl, dd, _ in cons["rows"]:
        new_pnl = pnl
        old_pnl = pnl - dpnl
        print(f"  {lab:12s}  {old_pnl:+11.1f}%  {new_pnl:+11.1f}%  {dpnl:+8.1f}pp  {dd:+7.1f}pp")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
