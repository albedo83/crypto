"""Trajectory-cut v2 — regime/dispersion-conditioned.

v1 (backtest_trajectory_cut.py) showed that the raw rule
  decline_rate ≥ N AND time_since_mfe ≥ M AND cur ≤ MAE+slack AND cur ≤ loss
fails walk-forward (1/4 PnL pass) because the cut frequently kills positions
that would have recovered. The edge exists on 28m (heavy with 2024 bear
catastrophes) but the rule misfires on 12m/6m/3m which are bull/choppy.

Hypothesis: catastrophes cluster in BEAR + HIGH-DISPERSION regimes. By
conditioning the cut on the macro state, we keep the saves on real bear-
flush events while avoiding the recovery-cuts in mean-revert chop.

Grid:
  base trajectory params: d100_t4_l200_s100 (best v1 variant on 28m gain)
  regime conditions:
    R1: btc_z < -0.5         (bear)
    R2: btc_z < 0.0          (bear or below-neutral)
    R3: disp_24h > 500       (panic widespread)
    R4: disp_24h > 700       (the disp_gate threshold)
    R5: btc_z < -0.5 AND disp_24h > 500   (bear AND panic)
    R6: btc_z < 0.0  AND disp_24h > 500   (slack bear AND panic)
    R7: btc_z < -0.5 OR  disp_24h > 700   (broader catastrophe regime)

Same 4/4 strict pass criteria (PnL > baseline on each window, ΔDD ≤ +2pp).
New exit reason: traj_cut.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone

import numpy as np
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
)


WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)

# Fixed trajectory params (best v1 variant by sumΔPnL on 28m)
TRAJ_PARAMS = dict(
    decline_rate_min_bps_per_h=100.0,
    time_since_mfe_min_h=4.0,
    at_mae_slack_bps=100.0,
    min_loss_bps=-200.0,
)


# ── Data loading ──────────────────────────────────────────────────────
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
    # Pre-compute disp_24h_by_ts ourselves, mirroring run_window's logic
    # (signals.compute_cross_context → std of ret_6h on 4h candles = 24h).
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


# ── Hook factory ──────────────────────────────────────────────────────
def make_hook(*, decline_rate_min_bps_per_h, time_since_mfe_min_h,
              at_mae_slack_bps, min_loss_bps,
              strategies, regime_check, disp_by_ts,
              parity=False):
    """regime_check(btc_z, disp_24h) -> bool. True = cut allowed."""
    state = {"evaluated": 0, "fired": 0, "fired_by_strat_dir": Counter(),
             "fired_by_regime": Counter()}

    def hook(snap):
        if parity:
            return None
        if snap["strat"] not in strategies:
            return None

        cur = snap.get("cur_bps", 0.0)
        mfe = snap.get("mfe_bps", 0.0)
        mae = snap.get("mae_bps", 0.0)
        t_since_mfe = snap.get("time_since_mfe_h", 0.0)
        state["evaluated"] += 1

        # NaN guards
        if cur != cur or mfe != mfe or mae != mae or t_since_mfe != t_since_mfe:
            return None
        if t_since_mfe < time_since_mfe_min_h:
            return None
        if cur > min_loss_bps:
            return None
        if (cur - mae) > at_mae_slack_bps:
            return None
        decline = (mfe - cur) / max(t_since_mfe, 1.0)
        if decline < decline_rate_min_bps_per_h:
            return None

        # Regime / dispersion conditioning
        btc_z = snap.get("btc_z", 0.0)
        disp = disp_by_ts.get(snap.get("ts_ms"), 0.0)
        if not regime_check(btc_z, disp):
            return None

        state["fired"] += 1
        side = "L" if snap["dir"] == 1 else "S"
        state["fired_by_strat_dir"][f"{snap['strat']}_{side}"] += 1
        # tag regime bucket for diagnostics
        if btc_z < -0.5:
            r = "bear"
        elif btc_z < 0.5:
            r = "neutral"
        else:
            r = "bull"
        state["fired_by_regime"][r] += 1
        return (True, "traj_cut")

    return hook, state


def run_one(ctx, s, e, hook=None):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        s, e,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=True,
        inlife_exit_extra=hook,
    )


def _exit_dist(trades):
    c = Counter(t["reason"] for t in trades)
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def run_window_set(ctx, hook, label_extra=""):
    out = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        ex_dist = _exit_dist(r["trades"])
        n_cut = sum(1 for t in r["trades"] if t["reason"] == "traj_cut")
        out[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], win_rate=r["win_rate"],
            by_strat=r["by_strat"], exit_dist=ex_dist,
            n_cut=n_cut, elapsed=time.time() - t0,
        )
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+9.1f}%  "
              f"DD={r['max_dd_pct']:5.1f}%  trades={r['n_trades']:4d}  "
              f"cut={n_cut:3d}  ({time.time()-t0:.1f}s)")
    return out


def verdict(baseline, variant_res):
    deltas, pass_pnl, sum_d_dd = {}, 0, 0.0
    for label, _ in WINDOWS:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    v = "GREEN" if (pass_pnl == 4 and avg_dd <= 2.0) else ("YELLOW" if pass_pnl == 3 else "RED")
    return dict(verdict=v, pass_pnl=pass_pnl, avg_dd=avg_dd, deltas=deltas)


# ── Regime predicates ─────────────────────────────────────────────────
REGIMES = {
    "R1_bz_lt_neg05":     lambda z, d: z < -0.5,
    "R2_bz_lt_0":         lambda z, d: z < 0.0,
    "R3_disp_gt_500":     lambda z, d: d > 500,
    "R4_disp_gt_700":     lambda z, d: d > 700,
    "R5_bz_neg05_d500":   lambda z, d: z < -0.5 and d > 500,
    "R6_bz_0_d500":       lambda z, d: z < 0.0 and d > 500,
    "R7_bz_neg05_or_d700": lambda z, d: z < -0.5 or d > 700,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default="/home/crypto/backtests/trajectory_cut_v2_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/3] Baseline (no hook)")
    baseline = run_window_set(ctx, hook=None, label_extra="baseline")

    print("\n[2/3] Regime-conditioned variants")
    results = {}
    for rname, rfn in REGIMES.items():
        print(f"\n  --- {rname} ---")
        hook, st = make_hook(
            **TRAJ_PARAMS,
            strategies={"S5"},
            regime_check=rfn,
            disp_by_ts=ctx["disp_by_ts"],
        )
        res = run_window_set(ctx, hook=hook, label_extra=rname[:8])
        v = verdict(baseline, res)
        results[rname] = dict(res=res, verdict=v,
                              fired_by_strat_dir=dict(st["fired_by_strat_dir"]),
                              fired_by_regime=dict(st["fired_by_regime"]))
        print(f"    verdict={v['verdict']}  pnl_pass={v['pass_pnl']}/4  "
              f"ΔDDavg={v['avg_dd']:+.2f}pp  fired={st['fired']} "
              f"by_regime={dict(st['fired_by_regime'])}")

    print("\n[3/3] Summary")
    def rank_key(item):
        s = item[1]
        sum_d_pnl = sum(d["d_pnl"] for d in s["verdict"]["deltas"].values())
        return (s["verdict"]["pass_pnl"], sum_d_pnl, -s["verdict"]["avg_dd"])
    ranked = sorted(results.items(), key=rank_key, reverse=True)
    print(f"\n{'variant':<22} {'verdict':8} {'pnl_pass':>9} {'ΔDDavg':>8}  "
          f"{'sumΔPnL':>11}  {'28mΔ':>10} {'12mΔ':>9} {'6mΔ':>9} {'3mΔ':>9}  cuts")
    for rname, s in ranked:
        v = s["verdict"]
        sum_d = sum(d["d_pnl"] for d in v["deltas"].values())
        deltas_str = " ".join(f"{v['deltas'][w[0]]['d_pnl']:+9.1f}" for w in WINDOWS)
        cuts_str = " ".join(str(s["res"][w[0]]["n_cut"]) for w in WINDOWS)
        print(f"{rname:<22} {v['verdict']:8} {v['pass_pnl']:>4}/4 "
              f"{v['avg_dd']:>+8.2f}pp  {sum_d:>+11.1f}  {deltas_str}  {cuts_str}")

    payload = dict(
        version="traj_cut_v2_regime_conditioned",
        timestamp=datetime.now(timezone.utc).isoformat(),
        traj_params=TRAJ_PARAMS,
        baseline={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                  for k, v in baseline.items()},
        variants={rname: dict(
            regime=rname,
            res={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                 for k, v in s["res"].items()},
            verdict=s["verdict"],
            fired_by_strat_dir=s["fired_by_strat_dir"],
            fired_by_regime=s["fired_by_regime"],
        ) for rname, s in results.items()},
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nArtifacts → {args.out}")


if __name__ == "__main__":
    main()
