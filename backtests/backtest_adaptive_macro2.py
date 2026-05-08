"""Walk-forward — adaptive bot phase 2: refinement + alternative axes.

Phase 1 (backtest_adaptive_macro.py) found that continuous macro modulators
beat static parameters by +19340pp on 28m walk-forward 4/4 (DD intact).
Top: `CONT: S1 +0.3·btc_z + S8 -0.3·btc_z` and `CONT: S8 -0.5·btc_z`.

Phase 2 explores:

  A) FINE-GRAIN α SWEEP — for each strategy, sweep α from -1.0 to +1.0
     to find the optimal continuous coefficient on btc_z.

  B) MULTI-STRAT CONT COMBOS — build the optimal joint α vector by
     stacking the best individual α's from (A).

  C) DXY MODULATOR — same continuous approach but with DXY 30d change as
     the macro. Independent test from BTC trend.

  D) ADAPTIVE THRESHOLD — instead of size, modulate S9_RET_THRESH (the
     ±20% trigger). When BTC is calm, lower threshold; when wild, raise.

  E) MEGA COMBO — best-of-everything stacking.

Usage:
    python3 -m backtests.backtest_adaptive_macro2
"""
from __future__ import annotations

import time
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore
import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def fmt_row(name, deltas_pnl, deltas_dd):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {name:55s}  "
            f"Δ28m={deltas_pnl['28m']:+8.1f}  Δ12m={deltas_pnl['12m']:+7.1f}  "
            f"Δ6m={deltas_pnl['6m']:+6.1f}  Δ3m={deltas_pnl['3m']:+5.1f}  "
            f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")


def compute_btc_z_by_ts(data: dict) -> dict:
    """BTC 30d return z-score per ts."""
    btc = data["BTC"]
    n_30d = 30 * 6  # 4h candles
    closes = np.array([c["c"] for c in btc])
    rets = []
    ts_with_ret = []
    for i in range(n_30d, len(btc)):
        ret = (closes[i] / closes[i - n_30d] - 1) if closes[i - n_30d] > 0 else 0
        rets.append(ret)
        ts_with_ret.append(btc[i]["t"])
    rets = np.array(rets)
    mean = float(np.mean(rets))
    std = float(np.std(rets)) or 1.0
    return {ts: (r - mean) / std for ts, r in zip(ts_with_ret, rets)}


def compute_dxy_z_by_ts(dxy_data: dict, ts_list: list) -> dict:
    """DXY 30d change z-score per ts."""
    if not dxy_data or "values" not in dxy_data:
        return {}
    dxy_values = sorted(dxy_data["values"])
    if not dxy_values:
        return {}
    dxy_ts = np.array([v[0] for v in dxy_values])
    dxy_v = np.array([v[1] for v in dxy_values])

    changes = {}
    for ts in ts_list:
        if ts < dxy_ts[0] + 30 * 86400 * 1000:
            continue
        idx = int(np.searchsorted(dxy_ts, ts) - 1)
        if idx < 0:
            continue
        cur = dxy_v[idx]
        target = ts - 30 * 86400 * 1000
        idx30 = int(np.searchsorted(dxy_ts, target) - 1)
        if idx30 < 0 or dxy_v[idx30] <= 0:
            continue
        changes[ts] = cur / dxy_v[idx30] - 1

    if not changes:
        return {}
    vals = np.array(list(changes.values()))
    mean = float(np.mean(vals))
    std = float(np.std(vals)) or 0.001
    return {ts: (c - mean) / std for ts, c in changes.items()}


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Computing macro features...")
    btc_z = compute_btc_z_by_ts(data)
    print(f"  BTC z-scores for {len(btc_z)} candles")

    all_ts = list(btc_z.keys())
    dxy_z = compute_dxy_z_by_ts(dxy_data, all_ts)
    print(f"  DXY z-scores for {len(dxy_z)} candles")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]
    end_ts = latest_ts
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
    )

    print("\nBaseline (static):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    def macro_at(ts, source):
        return (btc_z if source == "btc" else dxy_z).get(ts, 0)

    # ── (A) FINE-GRAIN α SWEEP per strat with btc_z ──────────────────
    print("\n" + "=" * 110)
    print(f"{'(A) FINE-GRAIN α SWEEP — single strat, α from −1.0 to +1.0':^110}")
    print("=" * 110)
    best_alpha_per_strat: dict[str, tuple[float, float]] = {}  # strat → (best α, sum ΔPnL)
    for strat in ["S1", "S5", "S8", "S9", "S10"]:
        for alpha in [-1.0, -0.7, -0.5, -0.3, -0.1, 0.1, 0.3, 0.5, 0.7, 1.0]:
            def make_fn(s, a):
                def fn(cand, f, n_pos):
                    if cand["strat"] != s:
                        return 1.0
                    return max(0.3, min(2.5, 1 + a * macro_at(f["t"], "btc")))
                return fn
            name = f"α[{strat}]={alpha:+.1f}·btc_z"
            positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(strat, alpha))
            sum_pnl = sum(d_pnl.values())
            avg_dd = sum(d_dd.values()) / 4
            # Track best α per strat (4/4 winner with DD intact)
            if positives == 4 and avg_dd <= 0.5:
                cur = best_alpha_per_strat.get(strat, (0, -1e9))
                if sum_pnl > cur[1]:
                    best_alpha_per_strat[strat] = (alpha, sum_pnl)
            if abs(sum_pnl) > 200:
                print(fmt_row(name, d_pnl, d_dd))

    print(f"\n  Best α per strat (4/4 strict, DD ≤ +0.5pp):")
    for strat, (a, p) in sorted(best_alpha_per_strat.items()):
        print(f"    {strat}: α={a:+.1f}  sum ΔPnL={p:+8.1f}")

    # ── (B) MULTI-STRAT CONT COMBOS ──────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) MULTI-STRAT — joint α vector from best singles':^110}")
    print("=" * 110)
    # Build the joint α vector and run it
    joint_alphas = {s: a for s, (a, _) in best_alpha_per_strat.items()}
    if joint_alphas:
        def make_fn(av):
            def fn(cand, f, n_pos):
                a = av.get(cand["strat"], 0)
                return max(0.3, min(2.5, 1 + a * macro_at(f["t"], "btc")))
            return fn
        name = f"JOINT: {' '.join(f'{s}={a:+.1f}' for s, a in sorted(joint_alphas.items()))}"
        positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(joint_alphas))
        print(fmt_row(name, d_pnl, d_dd))

    # Try a few hand-tuned variants near the joint optimum
    variants = [
        ("S1+0.3 S8-0.3 S5-0.2", {"S1": 0.3, "S8": -0.3, "S5": -0.2}),
        ("S1+0.5 S8-0.5",         {"S1": 0.5, "S8": -0.5}),
        ("S1+0.3 S8-0.5 S5-0.2 S9-0.3", {"S1": 0.3, "S8": -0.5, "S5": -0.2, "S9": -0.3}),
        ("ALL: S1+0.3 S8-0.5 S5-0.2 S9-0.3 S10-0.1",
         {"S1": 0.3, "S8": -0.5, "S5": -0.2, "S9": -0.3, "S10": -0.1}),
    ]
    for name, av in variants:
        def make_fn(a):
            def fn(cand, f, n_pos):
                alpha = a.get(cand["strat"], 0)
                return max(0.3, min(2.5, 1 + alpha * macro_at(f["t"], "btc")))
            return fn
        full_name = f"VARIANT: {name}"
        positives, d_pnl, d_dd = run_and_record(full_name, size_fn=make_fn(av))
        print(fmt_row(full_name, d_pnl, d_dd))

    # ── (C) DXY MODULATOR ─────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) DXY MODULATOR — α·dxy_z instead of btc_z':^110}")
    print("=" * 110)
    if dxy_z:
        for strat in ["S1", "S5", "S8", "S9", "S10"]:
            for alpha in [-0.5, -0.3, +0.3, +0.5]:
                def make_fn(s, a):
                    def fn(cand, f, n_pos):
                        if cand["strat"] != s:
                            return 1.0
                        return max(0.3, min(2.5, 1 + a * macro_at(f["t"], "dxy")))
                    return fn
                name = f"DXY α[{strat}]={alpha:+.1f}"
                positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(strat, alpha))
                if abs(sum(d_pnl.values())) > 200:
                    print(fmt_row(name, d_pnl, d_dd))
    else:
        print("  (DXY data unavailable — skipped)")

    # ── (D) ADAPTIVE THRESHOLD — SKIP when BTC z is in extreme range ──
    print("\n" + "=" * 110)
    print(f"{'(D) ADAPTIVE GATE — skip strat when btc_z outside [-K, +K]':^110}")
    print("=" * 110)
    # Test if disabling certain strats during extreme BTC regimes is helpful
    for strat in ["S5", "S9"]:
        for k in [1.0, 1.5, 2.0]:
            def make_skip(s, kk):
                def skip(coin, ts, st, dr):
                    return st == s and abs(macro_at(ts, "btc")) > kk
                return skip
            name = f"GATE {strat} when |btc_z|>{k:.1f}"
            positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(strat, k))
            if abs(sum(d_pnl.values())) > 200:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (E) MEGA COMBO — joint α + best volatility-regime + best gate ──
    print("\n" + "=" * 110)
    print(f"{'(E) MEGA COMBOS — joint α + S9 high_vol×1.5 + ...':^110}")
    print("=" * 110)
    # Recompute vol regime
    btc = data["BTC"]
    closes = np.array([c["c"] for c in btc])
    n_30d = 30 * 6
    btc_vol = {}
    for i in range(n_30d, len(btc)):
        log_rets = np.diff(np.log(closes[max(0, i - n_30d):i + 1]))
        btc_vol[btc[i]["t"]] = float(np.std(log_rets)) if len(log_rets) > 1 else 0
    vol_med = statistics.median(v for v in btc_vol.values() if v > 0)

    best_alphas = {"S1": 0.3, "S8": -0.5, "S5": -0.2}
    def mega_fn(cand, f, n_pos):
        ts = f["t"]
        a = best_alphas.get(cand["strat"], 0)
        m = max(0.3, min(2.5, 1 + a * macro_at(ts, "btc")))
        # Add S9 high-vol boost
        if cand["strat"] == "S9" and btc_vol.get(ts, 0) > vol_med:
            m *= 1.5
        # Add S10 low-vol boost
        if cand["strat"] == "S10" and btc_vol.get(ts, 0) <= vol_med:
            m *= 1.3
        return m
    positives, d_pnl, d_dd = run_and_record("MEGA: α-vec + S9 hv×1.5 + S10 lv×1.3", size_fn=mega_fn)
    print(fmt_row("MEGA: α-vec + S9 hv×1.5 + S10 lv×1.3", d_pnl, d_dd))

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^110}")
    print("=" * 110)
    found = []
    for name, info in all_results.items():
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found[:25]:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # ── Top 25 ────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 25 by sum(ΔPnL) — even if not 4/4':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:25]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:60s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
