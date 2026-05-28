"""Runtime discovery-bias test on 3 shipped rules.

For each shipped rule, run baseline (rule OFF = pre-rule config) vs
post-rule (rule ON = shipped config) on each of 4 windows (28m / 12m
/ 6m / 3m). Report per-window Δ.

Question (per rule × per window) : would the rule have passed
walk-forward on this window alone ? — strict gate ΔPnL > 0 AND ΔDD ≤ +1pp.

If a shipped rule fails the strict gate on 12m alone (or 6m alone),
its discovery was 28m-dependent. The rule may still be valid on 28m
strict 4/4 (the original acceptance criterion), but we are asking a
DIFFERENT question : how dependent is its empirical edge on the longer
training window ?

Rules tested :
  R1  traj_cut v12.7.1            (regime-bear cut, S5 LONG)
  R2  dispersion entry v11.7.28   (skip S5+S9 entries when disp_24h ≥ 700)
  R3  runner extension v11.7.32   (extend S9 hold +12h at timeout if MFE strong)
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
from backtests import backtest_rolling as br  # for monkey-patching DISP_GATE_BPS
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    DISP_GATE_BPS, DISP_GATE_STRATEGIES,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

# Reuse the v2 traj_cut hook (already proven, ships in v12.7.1)
from backtests.backtest_trajectory_cut_v2 import make_hook as make_traj_hook, TRAJ_PARAMS


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


# ── Data loading ──
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
    # disp_by_ts for traj_cut hook (mirrors backtest_rolling logic)
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


# ── Runner ──
def run_one(ctx, s, e, *, runner_ext=None, inlife_hook=None, disp_gate_bps=None):
    """Run a single window. Pass disp_gate_bps to override module-level constant
    (monkey-patch BR_DISP_GATE_BPS for the duration of this call)."""
    saved = br.DISP_GATE_BPS
    if disp_gate_bps is not None:
        br.DISP_GATE_BPS = disp_gate_bps
    try:
        return run_window(
            ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
            s, e,
            oi_data=ctx["oi"], funding_data=ctx["funding"],
            early_exit_params=EARLY_EXIT,
            apply_adaptive_modulator=True,
            inlife_exit_extra=inlife_hook,
            runner_extension=runner_ext,
        )
    finally:
        br.DISP_GATE_BPS = saved


def _exit_dist(trades):
    return dict(sorted(Counter(t["reason"] for t in trades).items(), key=lambda kv: -kv[1]))


def run_window_set(ctx, *, label_extra, **kwargs):
    out = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, **kwargs)
        ex = _exit_dist(r["trades"])
        out[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], exit_dist=ex,
            elapsed=time.time() - t0,
        )
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+12.2f}%  "
              f"DD={r['max_dd_pct']:6.2f}%  n={r['n_trades']:4d}  ({time.time()-t0:.1f}s)")
    return out


# ── Per-rule pairwise tests ──
def test_traj_cut(ctx):
    """OFF = no inlife hook. ON = R1 regime-bear traj_cut hook."""
    print("\n=== R1 traj_cut v12.7.1 (regime-bear S5 LONG cut) ===")
    off = run_window_set(ctx, label_extra="OFF", inlife_hook=None,
                          runner_ext=RUNNER_EXT_DICT)
    # Build R1 hook : btc_z<-0.5 regime check
    hook, st = make_traj_hook(
        **TRAJ_PARAMS,
        strategies={"S5"},
        regime_check=lambda bz, dp: bz < -0.5,
        disp_by_ts=ctx["disp_by_ts"],
    )
    on = run_window_set(ctx, label_extra="ON ", inlife_hook=hook,
                         runner_ext=RUNNER_EXT_DICT)
    print(f"  R1 hook stats: evaluated={st['evaluated']} fired={st['fired']} "
          f"by_strat_dir={dict(st['fired_by_strat_dir'])}")
    return off, on


def test_disp_gate(ctx):
    """OFF = monkey-patch DISP_GATE_BPS to 99999. ON = shipped 700."""
    print("\n=== R2 dispersion entry gate v11.7.28 (skip S5+S9 entries) ===")
    off = run_window_set(ctx, label_extra="OFF", disp_gate_bps=99999.0,
                          runner_ext=RUNNER_EXT_DICT)
    on  = run_window_set(ctx, label_extra="ON ", disp_gate_bps=DISP_GATE_BPS,
                          runner_ext=RUNNER_EXT_DICT)
    return off, on


def test_runner_ext(ctx):
    """OFF = runner_extension=None. ON = shipped dict."""
    print("\n=== R3 runner extension v11.7.32 (extend S9 hold by 12h) ===")
    off = run_window_set(ctx, label_extra="OFF", runner_ext=None)
    on  = run_window_set(ctx, label_extra="ON ", runner_ext=RUNNER_EXT_DICT)
    return off, on


def per_window_verdict(off, on, name, *, dd_threshold=1.0):
    """For each window, evaluate would the rule have been kept if validated
    on this window alone? Strict: ΔPnL > 0 AND ΔDD ≤ +dd_threshold pp."""
    print(f"\n  {'window':6s}  {'ΔPnL pp':>12s}  {'ΔDD pp':>8s}  {'verdict':>14s}")
    rows = {}
    for label, _ in WINDOWS:
        d_pnl = on[label]["pnl_pct"] - off[label]["pnl_pct"]
        d_dd  = on[label]["max_dd_pct"] - off[label]["max_dd_pct"]
        if d_pnl > 0 and d_dd <= dd_threshold:
            v = "KEEP ✓"
        elif d_pnl > 0:
            v = "PnL+ DD✗"
        elif d_dd <= dd_threshold:
            v = "PnL- DD✓"
        else:
            v = "REJECT ✗"
        rows[label] = dict(d_pnl=d_pnl, d_dd=d_dd, verdict=v)
        print(f"  {label:6s}  {d_pnl:+12.2f}  {d_dd:+8.2f}  {v:>14s}")
    pnl_pass_4_4 = all(rows[w]["d_pnl"] > 0 for w in rows)
    pnl_pass_smaller = all(rows[w]["d_pnl"] > 0 for w in ("12m", "6m", "3m"))
    print(f"\n  → strict 4/4 PnL+ : {'YES' if pnl_pass_4_4 else 'NO'}")
    print(f"  → discovery-on-smaller-windows (12m+6m+3m all positive) : "
          f"{'YES' if pnl_pass_smaller else 'NO'}")
    return rows


# ── Main ──
def main():
    ctx = load_all()
    print(f"\nEnd of data : {datetime.fromtimestamp(ctx['end_ts']/1000, tz=timezone.utc)}")

    results = {}

    off, on = test_traj_cut(ctx)
    print("\n[Verdict] traj_cut v12.7.1")
    results["traj_cut"] = per_window_verdict(off, on, "traj_cut")

    off, on = test_disp_gate(ctx)
    print("\n[Verdict] dispersion entry gate v11.7.28")
    results["disp_gate"] = per_window_verdict(off, on, "disp_gate")

    off, on = test_runner_ext(ctx)
    print("\n[Verdict] runner extension v11.7.32")
    results["runner_ext"] = per_window_verdict(off, on, "runner_ext")

    print("\n\n== SUMMARY (would the rule have been kept on each window alone?) ==")
    print(f"{'rule':30s}  {'28m':>10s}  {'12m':>10s}  {'6m':>10s}  {'3m':>10s}")
    for name, rows in results.items():
        cells = [rows[w]["verdict"] for w, _ in WINDOWS]
        print(f"  {name:30s}  {cells[0]:>10s}  {cells[1]:>10s}  {cells[2]:>10s}  {cells[3]:>10s}")

    Path(REPO / "backtests" / "discovery_bias_runtime.json").write_text(
        json.dumps(results, indent=2, default=str))
    print("\nDone.")


if __name__ == "__main__":
    main()
