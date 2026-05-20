"""Trajectory-cut early exit — replicate the user's manual_close heuristic.

The user's intuition (verified empirically, see feedback memory
2026-05-20): when an open position has fallen steeply from its MFE peak,
hasn't bounced, and is sitting at/near its MAE, the curve "looks
unrecoverable". On the live bot (April-May 2026) this manual heuristic
saved 3 catastrophe_stops out of 5 manual_close decisions, net +$28.82
over what the bot's automated exits would have crystallized.

This backtest codifies that pattern into an automatic exit rule:

  exit if   ALL of:
     decline_rate >= decline_rate_min_bps_per_h    (fast fall since MFE)
     time_since_mfe_h >= time_since_mfe_min_h      (sustained, not a tick)
     (cur_bps - mae_bps) <= at_mae_slack_bps       (currently near MAE)
     cur_bps <= min_loss_bps                       (meaningfully in the red)

where decline_rate = (mfe_bps - cur_bps) / max(time_since_mfe_h, 1).

S5 only (where the live catastrophes hit). New exit reason: traj_cut.

Pass criteria (walk-forward 4/4 strict):
  * ΔPnL > 0 on EACH of 4 windows (28m / 12m / 6m / 3m)
  * avg ΔDD ≤ +2pp across the 4 windows
  * Both → GREEN. 3/4 → YELLOW. ≤2/4 → RED.

Parity check: with the hook installed but always returning None, the
result must be bit-identical to baseline (no engine path mutation).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone

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
    print(f"  loaded in {time.time()-t0:.1f}s")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    out = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        out.append((label, start, end_ts_ms))
    return out


# ── Hook factory ──────────────────────────────────────────────────────
def make_traj_cut_hook(*, decline_rate_min_bps_per_h: float,
                       time_since_mfe_min_h: float,
                       at_mae_slack_bps: float,
                       min_loss_bps: float,
                       strategies: set[str],
                       parity: bool = False):
    """Build (hook, state) for run_window's inlife_exit_extra slot.

    The hook re-evaluates at EVERY candle (no one-shot per trade) — the
    decline can develop late in the trade. Once cut, run_window pops the
    position so we won't see it again. parity=True returns None always.
    """
    state = {
        "evaluated_candles": 0,
        "fired": 0,
        "fired_by_strat_dir": Counter(),
    }

    def hook(snap):
        if parity:
            return None
        if snap["strat"] not in strategies:
            return None

        cur = snap.get("cur_bps", 0.0)
        mfe = snap.get("mfe_bps", 0.0)
        mae = snap.get("mae_bps", 0.0)
        t_since_mfe = snap.get("time_since_mfe_h", 0.0)
        state["evaluated_candles"] += 1

        # NaN guards
        if cur != cur or mfe != mfe or mae != mae or t_since_mfe != t_since_mfe:
            return None

        # Conditions
        if t_since_mfe < time_since_mfe_min_h:
            return None
        if cur > min_loss_bps:
            return None
        if (cur - mae) > at_mae_slack_bps:
            return None

        # Decline rate (positive = falling)
        decline = (mfe - cur) / max(t_since_mfe, 1.0)
        if decline < decline_rate_min_bps_per_h:
            return None

        state["fired"] += 1
        side = "L" if snap["dir"] == 1 else "S"
        state["fired_by_strat_dir"][f"{snap['strat']}_{side}"] += 1
        return (True, "traj_cut")

    return hook, state


# ── Backtest runner ───────────────────────────────────────────────────
def run_one(ctx, start_ts, end_ts, *, hook=None):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        start_ts, end_ts,
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
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+8.1f}%  "
              f"DD={r['max_dd_pct']:5.1f}%  trades={r['n_trades']:4d}  "
              f"cut={n_cut:3d}  ({time.time()-t0:.1f}s)")
    return out


# ── Verdict ───────────────────────────────────────────────────────────
def verdict(baseline, variant_res):
    deltas = {}
    pass_pnl = 0
    sum_d_dd = 0.0
    for label, _ in WINDOWS:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    if pass_pnl == 4 and avg_dd <= 2.0:
        v = "GREEN"
    elif pass_pnl == 3:
        v = "YELLOW"
    else:
        v = "RED"
    return dict(verdict=v, pass_pnl=pass_pnl, avg_dd=avg_dd, deltas=deltas)


# ── Grid ──────────────────────────────────────────────────────────────
def build_grid():
    """Return list of (label, params_dict) for the variants to test."""
    grid = []
    for decline in (75, 100, 150):
        for t_mfe in (4, 8):
            for min_loss in (-200, -400):
                for slack in (100, 200):
                    label = f"d{decline}_t{t_mfe}_l{abs(min_loss)}_s{slack}"
                    grid.append((label, dict(
                        decline_rate_min_bps_per_h=decline,
                        time_since_mfe_min_h=t_mfe,
                        at_mae_slack_bps=slack,
                        min_loss_bps=min_loss,
                        strategies={"S5"},
                    )))
    return grid


# ── Main ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="single best-guess variant + 3m only")
    ap.add_argument("--out",
                    default="/home/crypto/backtests/trajectory_cut_artifacts.json")
    ap.add_argument("--top", type=int, default=5,
                    help="rank top N variants in output")
    args = ap.parse_args()

    ctx = load_all()

    if args.smoke:
        print("\n[smoke] Best-guess variant on 3m only")
        hook, st = make_traj_cut_hook(
            decline_rate_min_bps_per_h=100,
            time_since_mfe_min_h=4,
            at_mae_slack_bps=200,
            min_loss_bps=-200,
            strategies={"S5"},
        )
        specs = window_specs(ctx["end_ts"])
        label, s, e = specs[-1]  # 3m
        r = run_one(ctx, s, e, hook=hook)
        n_cut = sum(1 for t in r["trades"] if t["reason"] == "traj_cut")
        print(f"  3m smoke: pnl={r['pnl_pct']:+.1f}%, "
              f"DD={r['max_dd_pct']:.1f}%, trades={r['n_trades']}, "
              f"cut={n_cut}, evaluated={st['evaluated_candles']}")
        return

    print("\n[1/4] Parity check")
    parity_hook, _ = make_traj_cut_hook(
        decline_rate_min_bps_per_h=100, time_since_mfe_min_h=4,
        at_mae_slack_bps=100, min_loss_bps=-200,
        strategies={"S5"}, parity=True)
    print("  baseline 4 windows...")
    baseline = run_window_set(ctx, hook=None, label_extra="baseline")
    print("  parity (hook=None-returning) 4 windows...")
    parity = run_window_set(ctx, hook=parity_hook, label_extra="parity  ")

    parity_ok = True
    for label, _ in WINDOWS:
        b, p = baseline[label], parity[label]
        if (b["n_trades"] != p["n_trades"]
                or abs(b["pnl_pct"] - p["pnl_pct"]) > 1e-6
                or abs(b["max_dd_pct"] - p["max_dd_pct"]) > 1e-6):
            print(f"  ✗ PARITY FAIL on {label}: "
                  f"b={b['n_trades']}/{b['pnl_pct']:.4f}/{b['max_dd_pct']:.4f}  "
                  f"p={p['n_trades']}/{p['pnl_pct']:.4f}/{p['max_dd_pct']:.4f}")
            parity_ok = False
        else:
            print(f"  ✓ parity {label} matches baseline "
                  f"({b['n_trades']} trades, {b['pnl_pct']:+.2f}%, "
                  f"{b['max_dd_pct']:.2f}% DD)")
    if not parity_ok:
        print("\n!!! PARITY FAILED — aborting")
        return

    print("\n[2/4] Running variant grid")
    grid = build_grid()
    print(f"  {len(grid)} variants × 4 windows = {len(grid)*4} runs")

    results = {}
    for i, (label, params) in enumerate(grid, 1):
        print(f"\n  [{i}/{len(grid)}] {label}  "
              f"(dec≥{params['decline_rate_min_bps_per_h']} "
              f"t_mfe≥{params['time_since_mfe_min_h']}h "
              f"loss≤{params['min_loss_bps']} "
              f"slack≤{params['at_mae_slack_bps']})")
        hook, st = make_traj_cut_hook(**params)
        res = run_window_set(ctx, hook=hook, label_extra=f"{label}")
        v = verdict(baseline, res)
        results[label] = dict(
            params=params,
            res=res,
            fired_by_strat_dir=dict(st["fired_by_strat_dir"]),
            verdict=v,
        )
        print(f"    verdict={v['verdict']}  "
              f"pnl_pass={v['pass_pnl']}/4  "
              f"ΔDDavg={v['avg_dd']:+.2f}pp")

    print("\n[3/4] Ranking variants")
    # Rank by: 4/4 first, then 3/4, then by sum_d_pnl, ΔDD as tiebreak
    def rank_key(v):
        s = v[1]
        sum_d_pnl = sum(d["d_pnl"] for d in s["verdict"]["deltas"].values())
        return (s["verdict"]["pass_pnl"], sum_d_pnl, -s["verdict"]["avg_dd"])

    ranked = sorted(results.items(), key=rank_key, reverse=True)
    top = ranked[:args.top]
    print(f"\n  Top {len(top)} variants:")
    print(f"  {'label':<22} {'verdict':8} {'pnl_pass':>9} {'avg_DD':>7}  "
          f"{'sumΔPnL':>10}  {'28mΔ':>8} {'12mΔ':>8} {'6mΔ':>8} {'3mΔ':>8}  cuts")
    for label, s in top:
        v = s["verdict"]
        sum_d = sum(d["d_pnl"] for d in v["deltas"].values())
        deltas_str = " ".join(f"{v['deltas'][w[0]]['d_pnl']:+8.1f}" for w in WINDOWS)
        cuts_by_w = " ".join(str(s["res"][w[0]]["n_cut"]) for w in WINDOWS)
        print(f"  {label:<22} {v['verdict']:8} {v['pass_pnl']:>4}/4 "
              f"{v['avg_dd']:>+7.2f}  {sum_d:>+10.1f}  {deltas_str}  {cuts_by_w}")

    print(f"\n[4/4] Writing artifacts to {args.out}")
    payload = dict(
        version="traj_cut_v1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        baseline={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                  for k, v in baseline.items()},
        variants={
            label: dict(
                params={k: list(v) if isinstance(v, set) else v
                        for k, v in s["params"].items()},
                res={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                     for k, v in s["res"].items()},
                fired_by_strat_dir=s["fired_by_strat_dir"],
                verdict=s["verdict"],
            )
            for label, s in results.items()
        },
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"  done.")


if __name__ == "__main__":
    main()
