"""S5 trailing stop CONDITIONED on bear regime — re-attempt.

Original `backtest_s5_trailing.py` tested 11 unconditioned variants and
failed 4/4 walk-forward: S5 runners reach MFE +2000 bps and continue.
Locking gains amputes the upside more than it saves the givebacks.

Hypothesis (this R&D): in BEAR regime (btc_z < -0.5), mean-reversion
upside expectation is reduced — locking gains may pay where it didn't
in the unconditioned grid. Same idea that made v12.7.1 traj_cut pass
(unconditioned 1/4 → bear-conditioned 4/4).

Rule:
  exit if  strategy == S5
      AND  pos.mfe_bps >= trigger_bps
      AND  cur_bps <= pos.mfe_bps - offset_bps
      AND  bot._btc_z < REGIME_THRESHOLD_Z

New exit reason: s5_trail_bear.

Walk-forward 4/4 strict pass criteria:
  * ΔPnL > 0 on EACH of 4 windows (28m / 12m / 6m / 3m)
  * avg ΔDD >= -2.0 (no avg degradation > 2pp)

Same parity-check pattern as traj_cut_v2.
"""
from __future__ import annotations

import argparse
import json
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

REGIME_THRESHOLD_Z = -0.5


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
    return [(label, int((end_dt - relativedelta(months=months)).timestamp() * 1000), end_ts_ms)
            for label, months in WINDOWS]


def make_hook(*, trigger_bps: float, offset_bps: float,
              regime_threshold_z: float = REGIME_THRESHOLD_Z,
              strategies=("S5",), parity=False):
    state = {"fired": 0, "fired_by_regime": Counter()}

    def hook(snap):
        if parity:
            return None
        if snap["strat"] not in strategies:
            return None
        mfe = snap.get("mfe_bps", 0.0)
        cur = snap.get("cur_bps", 0.0)
        if mfe != mfe or cur != cur:
            return None
        # Trigger check
        if mfe < trigger_bps:
            return None
        # Trailing distance check
        if cur > mfe - offset_bps:
            return None
        # Regime gate
        btc_z = snap.get("btc_z", 0.0)
        if btc_z >= regime_threshold_z:
            return None
        state["fired"] += 1
        bucket = "bear" if btc_z < -0.5 else ("neutral" if btc_z < 0.5 else "bull")
        state["fired_by_regime"][bucket] += 1
        return (True, "s5_trail_bear")

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
    return dict(sorted(Counter(t["reason"] for t in trades).items(),
                       key=lambda kv: -kv[1]))


def run_window_set(ctx, hook, label=""):
    out = {}
    for lbl, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        n_cut = sum(1 for t in r["trades"] if t["reason"] == "s5_trail_bear")
        out[lbl] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], n_cut=n_cut,
            exit_dist=_exit_dist(r["trades"]),
            elapsed=time.time() - t0,
        )
        print(f"  {label:<15} {lbl}: pnl={r['pnl_pct']:+9.1f}%  "
              f"DD={r['max_dd_pct']:5.1f}%  trades={r['n_trades']:4d}  "
              f"cut={n_cut:3d}  ({time.time()-t0:.1f}s)")
    return out


def verdict(baseline, variant_res):
    deltas, pass_pnl, sum_d_dd = {}, 0, 0.0
    for label, _ in WINDOWS:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        # Correct DD sign: max_dd_pct stored NEGATIVE. d_dd = variant - baseline.
        # POSITIVE d_dd = LESS NEGATIVE DD = IMPROVEMENT.
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    # Pass if 4/4 PnL AND avg DD doesn't DEGRADE more than 2pp (avg_dd >= -2.0).
    pnl_strict = pass_pnl == 4
    dd_ok = avg_dd >= -2.0
    if pnl_strict and dd_ok:
        v = "GREEN"
    elif pass_pnl == 3:
        v = "YELLOW"
    else:
        v = "RED"
    return dict(verdict=v, pass_pnl=pass_pnl, avg_dd=avg_dd, deltas=deltas)


def build_grid():
    grid = []
    for trig in (600, 800, 1000, 1200, 1500, 2000):
        for off in (150, 200, 300, 500, 800):
            if off >= trig:
                continue
            label = f"T{trig}_O{off}"
            grid.append((label, dict(trigger_bps=trig, offset_bps=off)))
    return grid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default="/home/crypto/backtests/s5_trail_bear_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/3] Parity check")
    parity_hook, _ = make_hook(trigger_bps=1000, offset_bps=300, parity=True)
    base = run_window_set(ctx, hook=None, label="baseline")
    par = run_window_set(ctx, hook=parity_hook, label="parity")
    parity_ok = True
    for lbl, _ in WINDOWS:
        b, p = base[lbl], par[lbl]
        if (b["n_trades"] != p["n_trades"]
                or abs(b["pnl_pct"] - p["pnl_pct"]) > 1e-6
                or abs(b["max_dd_pct"] - p["max_dd_pct"]) > 1e-6):
            print(f"  ✗ PARITY FAIL on {lbl}")
            parity_ok = False
    if not parity_ok:
        print("\n!!! PARITY FAILED — aborting")
        return
    print("  ✓ parity OK on 4 windows")

    print("\n[2/3] Grid sweep")
    grid = build_grid()
    print(f"  {len(grid)} variants × 4 windows = {len(grid)*4} runs")
    results = {}
    for i, (lbl, params) in enumerate(grid, 1):
        print(f"\n  [{i:>2}/{len(grid)}] {lbl}  trigger={params['trigger_bps']} offset={params['offset_bps']} z<{REGIME_THRESHOLD_Z}")
        hook, st = make_hook(**params)
        res = run_window_set(ctx, hook=hook, label=lbl)
        v = verdict(base, res)
        results[lbl] = dict(params=params, res=res, verdict=v, fires=dict(st["fired_by_regime"]))
        print(f"    verdict={v['verdict']}  pass={v['pass_pnl']}/4  ΔDDavg={v['avg_dd']:+.2f}pp  fired={st['fired']}")

    print("\n[3/3] Ranking")
    def rank_key(item):
        s = item[1]
        sum_d = sum(d["d_pnl"] for d in s["verdict"]["deltas"].values())
        return (s["verdict"]["pass_pnl"], sum_d, s["verdict"]["avg_dd"])
    ranked = sorted(results.items(), key=rank_key, reverse=True)
    top = ranked[:10]
    print(f"\n  Top {len(top)} variants:")
    print(f"  {'variant':<14} {'verdict':8} {'pass':>5} {'ΔDDavg':>8}  {'sumΔPnL':>11}  "
          f"{'28mΔ':>10} {'12mΔ':>9} {'6mΔ':>8} {'3mΔ':>8}  {'cuts(28/12/6/3)':>16}")
    for lbl, s in top:
        v = s["verdict"]
        sum_d = sum(d["d_pnl"] for d in v["deltas"].values())
        deltas_str = " ".join(f"{v['deltas'][w[0]]['d_pnl']:+9.1f}" for w in WINDOWS)
        cuts_str = "/".join(str(s["res"][w[0]]["n_cut"]) for w in WINDOWS)
        print(f"  {lbl:<14} {v['verdict']:8} {v['pass_pnl']:>2}/4 "
              f"{v['avg_dd']:>+8.2f}pp  {sum_d:>+11.1f}  {deltas_str}  {cuts_str:>16}")

    payload = dict(
        version="s5_trail_bear_v1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        regime_threshold_z=REGIME_THRESHOLD_Z,
        baseline={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                  for k, v in base.items()},
        variants={lbl: dict(
            params=s["params"],
            res={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                 for k, v in s["res"].items()},
            verdict=s["verdict"],
            fires=s["fires"],
        ) for lbl, s in results.items()},
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nArtifacts → {args.out}")


if __name__ == "__main__":
    main()
