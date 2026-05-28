"""2x2 interaction matrix : disp_gate v11.7.28 × traj_cut v12.7.1.

Q: Is disp_gate redundant once traj_cut is active, OR is it still
   genuinely contributing edge?

Method: 4 configs × 4 windows. All other shipped rules at default
(runner_ext ON, modulator ON, dead_timeout ON, S8 in-life ON, etc.).

Configs:
  N : neither            (disp_gate OFF, traj_cut OFF)
  D : disp_gate only     (disp_gate ON,  traj_cut OFF)
  T : traj_cut only      (disp_gate OFF, traj_cut ON)
  B : both (shipped)     (disp_gate ON,  traj_cut ON)

Per window we'll rank the 4 configs by PnL and by Calmar (PnL / max_dd).
Decision question: which config is Pareto-optimal on each window?
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, load_oi, load_funding,
)
from backtests import backtest_rolling as br
from backtests.backtest_trajectory_cut_v2 import make_hook as make_traj_hook, TRAJ_PARAMS
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    DISP_GATE_BPS as SHIP_DISP_GATE_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)


REPO = Path(__file__).resolve().parents[1]
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
RUNNER_EXT_DICT = {
    "strategies": RUNNER_EXT_STRATEGIES,
    "extra_candles": RUNNER_EXT_HOURS // 4,
    "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
    "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
}


def load_all():
    print("Loading data...")
    t0 = time.time()
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    feat_by_ts: dict[int, list[float]] = {}
    for coin, flist in features.items():
        for f in flist:
            ts = f["t"]
            r = f.get("ret_6h")
            if r is not None:
                feat_by_ts.setdefault(ts, []).append(r)
    disp_by_ts: dict[int, float] = {}
    for ts, rets in feat_by_ts.items():
        if len(rets) > 4:
            disp_by_ts[ts] = float(np.std(rets))
    print(f"  loaded in {time.time()-t0:.1f}s ; disp_by_ts has {len(disp_by_ts)} points")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts, disp_by_ts=disp_by_ts)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    return [(label, int((end_dt - relativedelta(months=months)).timestamp() * 1000), end_ts_ms)
            for label, months in WINDOWS]


def run_one(ctx, s, e, *, inlife_hook=None, disp_gate_on=True):
    saved = br.DISP_GATE_BPS
    if not disp_gate_on:
        br.DISP_GATE_BPS = 99999.0
    try:
        return run_window(
            ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
            s, e,
            oi_data=ctx["oi"], funding_data=ctx["funding"],
            early_exit_params=EARLY_EXIT,
            apply_adaptive_modulator=True,
            inlife_exit_extra=inlife_hook,
            runner_extension=RUNNER_EXT_DICT,
        )
    finally:
        br.DISP_GATE_BPS = saved


def build_traj_hook(ctx):
    hook, st = make_traj_hook(
        **TRAJ_PARAMS,
        strategies={"S5"},
        regime_check=lambda bz, dp: bz < -0.5,
        disp_by_ts=ctx["disp_by_ts"],
    )
    return hook, st


def run_config(ctx, name, disp_on, traj_on):
    print(f"\n── Config {name} (disp_gate={'ON' if disp_on else 'OFF'}, "
          f"traj_cut={'ON' if traj_on else 'OFF'}) ──")
    hook = None
    hook_state = None
    if traj_on:
        hook, hook_state = build_traj_hook(ctx)
    out = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, inlife_hook=hook, disp_gate_on=disp_on)
        out[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], calmar=r["pnl_pct"] / max(abs(r["max_dd_pct"]), 1),
        )
        print(f"  {label}: pnl={r['pnl_pct']:+12.2f}%  DD={r['max_dd_pct']:6.2f}%  "
              f"n={r['n_trades']:4d}  cal={out[label]['calmar']:+.1f}  ({time.time()-t0:.1f}s)")
    if hook_state:
        print(f"  traj_cut fired={hook_state['fired']} "
              f"by_dir={dict(hook_state['fired_by_strat_dir'])}")
    return out


def main():
    ctx = load_all()
    print(f"\nShipped DISP_GATE_BPS = {SHIP_DISP_GATE_BPS}")
    print(f"End of data : {datetime.fromtimestamp(ctx['end_ts']/1000, tz=timezone.utc)}")
    print(f"\nAll configs include runner_ext + modulator + dead_timeout + S8 inlife + S8 dead-in-water (default shipped).")

    results = {}
    for name, disp_on, traj_on in [
        ("N_neither",      False, False),
        ("D_disp_only",    True,  False),
        ("T_traj_only",    False, True),
        ("B_both_shipped", True,  True),
    ]:
        results[name] = run_config(ctx, name, disp_on, traj_on)

    print("\n\n=== PnL % per window (higher = better) ===")
    print(f"{'config':18s}  {'28m':>14s}  {'12m':>14s}  {'6m':>14s}  {'3m':>14s}")
    for name, rows in results.items():
        cells = [f"{rows[w]['pnl_pct']:+14.0f}" for w, _ in WINDOWS]
        print(f"  {name:18s}  " + "  ".join(cells))

    print("\n=== Max DD % per window (closer to 0 = better) ===")
    print(f"{'config':18s}  {'28m':>10s}  {'12m':>10s}  {'6m':>10s}  {'3m':>10s}")
    for name, rows in results.items():
        cells = [f"{rows[w]['max_dd_pct']:+10.2f}" for w, _ in WINDOWS]
        print(f"  {name:18s}  " + "  ".join(cells))

    print("\n=== Calmar (PnL / |DD|) per window (higher = better) ===")
    print(f"{'config':18s}  {'28m':>10s}  {'12m':>10s}  {'6m':>10s}  {'3m':>10s}")
    for name, rows in results.items():
        cells = [f"{rows[w]['calmar']:+10.1f}" for w, _ in WINDOWS]
        print(f"  {name:18s}  " + "  ".join(cells))

    print("\n=== Best config per window (PnL) ===")
    for w, _ in WINDOWS:
        best = max(results, key=lambda n: results[n][w]["pnl_pct"])
        print(f"  {w}: {best} (pnl={results[best][w]['pnl_pct']:+.0f}%, "
              f"DD={results[best][w]['max_dd_pct']:.2f}%)")

    print("\n=== Best config per window (Calmar) ===")
    for w, _ in WINDOWS:
        best = max(results, key=lambda n: results[n][w]["calmar"])
        print(f"  {w}: {best} (calmar={results[best][w]['calmar']:+.1f}, "
              f"pnl={results[best][w]['pnl_pct']:+.0f}%, DD={results[best][w]['max_dd_pct']:.2f}%)")

    # Decision aid: rank each config 1-4 per window by PnL and Calmar
    print("\n=== Rank summary (lower rank = better) ===")
    print(f"{'config':18s}  {'avg PnL rank':>15s}  {'avg Calmar rank':>17s}")
    pnl_ranks = {n: [] for n in results}
    cal_ranks = {n: [] for n in results}
    for w, _ in WINDOWS:
        order_pnl = sorted(results, key=lambda n: -results[n][w]["pnl_pct"])
        order_cal = sorted(results, key=lambda n: -results[n][w]["calmar"])
        for i, n in enumerate(order_pnl):
            pnl_ranks[n].append(i + 1)
        for i, n in enumerate(order_cal):
            cal_ranks[n].append(i + 1)
    for name in results:
        avg_p = sum(pnl_ranks[name]) / len(pnl_ranks[name])
        avg_c = sum(cal_ranks[name]) / len(cal_ranks[name])
        print(f"  {name:18s}  {avg_p:15.2f}  {avg_c:17.2f}")

    Path(REPO / "backtests" / "discovery_bias_2x2.json").write_text(
        json.dumps(results, indent=2, default=str))
    print("\nDone.")


if __name__ == "__main__":
    main()
