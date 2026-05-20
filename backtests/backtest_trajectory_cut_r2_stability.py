"""R2 stability tests — null-shuffle + sliding walk-forward.

R2 from v2: cut when btc_z < 0 (bear OR below-neutral) + traj params.
v2 showed R2 passes 4/4 PnL strict on the canonical windows. But is the
edge from the regime filter (genuine signal) or from random chance?

Two tests:

  1. Null-shuffle: shuffle btc_z values across timestamps, run R2 with the
     fake regime assignments. Repeat N times. If R2's real edge is
     significantly above the null distribution, the regime filter is the
     thing doing the work. If null shuffles routinely match R2's gain, the
     edge is noise.

  2. Sliding walk-forward: 4 sliding (18m train, 6m test) splits. Test
     parameters fixed (no fitting per split). The variant is required to
     beat baseline on OOS PnL on at least 3/4 test windows.

Inspired by backtest_adaptive_robustness.py.
"""
from __future__ import annotations

import argparse
import json
import random
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


EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)

TRAJ = dict(
    decline_rate_min_bps_per_h=100.0,
    time_since_mfe_min_h=4.0,
    at_mae_slack_bps=100.0,
    min_loss_bps=-200.0,
)


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


def make_hook(*, threshold_z: float, btc_z_override: dict | None = None,
              strategies=("S5",), parity=False):
    """Build a hook that cuts when btc_z_override.get(ts) < threshold.

    btc_z_override=None → use snap['btc_z'] directly (real regime).
    btc_z_override=dict → use the shuffled values keyed by ts_ms.
    """
    state = {"fired": 0}

    def hook(snap):
        if parity:
            return None
        if snap["strat"] not in strategies:
            return None
        cur = snap.get("cur_bps", 0.0)
        mfe = snap.get("mfe_bps", 0.0)
        mae = snap.get("mae_bps", 0.0)
        t_since_mfe = snap.get("time_since_mfe_h", 0.0)
        if cur != cur or mfe != mfe or mae != mae or t_since_mfe != t_since_mfe:
            return None
        if t_since_mfe < TRAJ["time_since_mfe_min_h"]:
            return None
        if cur > TRAJ["min_loss_bps"]:
            return None
        if (cur - mae) > TRAJ["at_mae_slack_bps"]:
            return None
        decline = (mfe - cur) / max(t_since_mfe, 1.0)
        if decline < TRAJ["decline_rate_min_bps_per_h"]:
            return None
        # Regime gate
        if btc_z_override is not None:
            z = btc_z_override.get(snap.get("ts_ms"), 0.0)
        else:
            z = snap.get("btc_z", 0.0)
        if z >= threshold_z:
            return None
        state["fired"] += 1
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


def collect_btc_z_map(ctx, start_ts, end_ts):
    """Reconstruct the btc_z map that run_window would build for this window.

    Replicates backtest_rolling.py's btc_z calc (rolling 30d ret with 6m z).
    """
    btc_candles = [c for c in ctx["data"]["BTC"] if start_ts <= c["t"] <= end_ts]
    if len(btc_candles) < 200:
        return {}
    closes = np.array([c["c"] for c in btc_candles])
    n_lb = 180
    out: dict[int, float] = {}
    rets_history = []
    for i in range(n_lb, len(closes)):
        r = (closes[i] / closes[i - n_lb] - 1) * 1e4
        rets_history.append(r)
        if len(rets_history) < 30:
            continue
        arr = np.array(rets_history[-1080:])  # 6m rolling
        m, s = arr.mean(), arr.std()
        if s > 0:
            out[btc_candles[i]["t"]] = (rets_history[-1] - m) / s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--null-trials", type=int, default=10,
                    help="Number of null-shuffle trials")
    ap.add_argument("--out",
                    default="/home/crypto/backtests/trajectory_cut_r2_stability.json")
    args = ap.parse_args()

    ctx = load_all()
    end_dt = datetime.fromtimestamp(ctx["end_ts"] / 1000, tz=timezone.utc)

    # ─── Part 1: null-shuffle on the 28m window ────────────────────────
    print("\n" + "=" * 70)
    print("PART 1 — null-shuffle on 28m")
    print("=" * 70)
    start_28m = int((end_dt - relativedelta(months=28)).timestamp() * 1000)

    print("  computing real btc_z map for 28m...")
    real_z = collect_btc_z_map(ctx, start_28m, ctx["end_ts"])
    print(f"  real_z has {len(real_z)} points; range [{min(real_z.values()):.2f}, {max(real_z.values()):.2f}]")

    # Run real R2 first
    print("\n  baseline 28m (no hook)...")
    r_base = run_one(ctx, start_28m, ctx["end_ts"], hook=None)
    base_pnl = r_base["pnl_pct"]
    base_dd = r_base["max_dd_pct"]
    print(f"    baseline: pnl={base_pnl:+.1f}%  DD={base_dd:.1f}%  trades={r_base['n_trades']}")

    print("\n  R2 with REAL btc_z (z < 0)...")
    real_hook, real_st = make_hook(threshold_z=0.0, btc_z_override=None)
    r_real = run_one(ctx, start_28m, ctx["end_ts"], hook=real_hook)
    real_d_pnl = r_real["pnl_pct"] - base_pnl
    real_d_dd = r_real["max_dd_pct"] - base_dd
    print(f"    R2 real:  pnl={r_real['pnl_pct']:+.1f}%  DD={r_real['max_dd_pct']:.1f}%  "
          f"trades={r_real['n_trades']}  fires={real_st['fired']}")
    print(f"    ΔPnL = {real_d_pnl:+.1f}pp  ΔDD = {real_d_dd:+.2f}pp (positive = improvement)")

    # Null shuffles
    print(f"\n  Running {args.null_trials} null-shuffle trials...")
    ts_list = list(real_z.keys())
    z_values = list(real_z.values())
    null_d_pnls = []
    null_d_dds = []
    null_fires = []
    random.seed(42)
    for trial in range(args.null_trials):
        shuffled = z_values[:]
        random.shuffle(shuffled)
        fake_z = dict(zip(ts_list, shuffled))
        hook, st = make_hook(threshold_z=0.0, btc_z_override=fake_z)
        r = run_one(ctx, start_28m, ctx["end_ts"], hook=hook)
        d_pnl = r["pnl_pct"] - base_pnl
        d_dd = r["max_dd_pct"] - base_dd
        null_d_pnls.append(d_pnl)
        null_d_dds.append(d_dd)
        null_fires.append(st["fired"])
        print(f"    null[{trial+1:>2}/{args.null_trials}]: ΔPnL={d_pnl:+10.1f}pp  ΔDD={d_dd:+6.2f}pp  fires={st['fired']}")

    null_arr = np.array(null_d_pnls)
    null_mean, null_std = null_arr.mean(), null_arr.std()
    if null_std > 0:
        z_score = (real_d_pnl - null_mean) / null_std
    else:
        z_score = float("inf") if real_d_pnl > null_mean else 0.0
    n_better_than_real = sum(1 for x in null_d_pnls if x >= real_d_pnl)
    print(f"\n  Null distribution: mean ΔPnL={null_mean:+.1f}pp, std={null_std:.1f}pp")
    print(f"  Real ΔPnL = {real_d_pnl:+.1f}pp")
    print(f"  z-score of real vs null: {z_score:+.2f}")
    print(f"  trials where null >= real: {n_better_than_real}/{args.null_trials} "
          f"(p-value ≈ {n_better_than_real/args.null_trials:.2f})")

    null_verdict = "GENUINE" if z_score >= 2.0 and n_better_than_real <= max(1, args.null_trials // 10) else "NOISE_RISK"

    # ─── Part 2: sliding walk-forward 18m/6m ───────────────────────────
    print("\n" + "=" * 70)
    print("PART 2 — sliding walk-forward 18m/6m")
    print("=" * 70)
    splits = []
    for months_offset in (0, 6, 12, 18):
        train_end = end_dt - relativedelta(months=months_offset)
        test_end = train_end + relativedelta(months=6) if months_offset > 0 else train_end
        train_start = train_end - relativedelta(months=18)
        if train_start < end_dt - relativedelta(months=28):
            continue
        test_start = train_end
        if test_end > end_dt:
            test_end = end_dt
        if test_start >= test_end:
            continue
        splits.append((train_start, train_end, test_start, test_end))

    if not splits:
        # Synthesize at least 1 OOS test even if data tight
        splits = [(end_dt - relativedelta(months=24), end_dt - relativedelta(months=6),
                   end_dt - relativedelta(months=6), end_dt)]

    print(f"\n  {len(splits)} splits (train 18m IS, test 6m OOS)")
    split_results = []
    for i, (ts, te, tts, tte) in enumerate(splits, 1):
        ts_ms = int(ts.timestamp() * 1000)
        te_ms = int(te.timestamp() * 1000)
        tts_ms = int(tts.timestamp() * 1000)
        tte_ms = int(tte.timestamp() * 1000)
        # Baseline on test
        rb = run_one(ctx, tts_ms, tte_ms, hook=None)
        # R2 on test
        hook, st = make_hook(threshold_z=0.0)
        rr = run_one(ctx, tts_ms, tte_ms, hook=hook)
        d_pnl = rr["pnl_pct"] - rb["pnl_pct"]
        d_dd = rr["max_dd_pct"] - rb["max_dd_pct"]
        split_results.append(dict(
            split=i,
            test_start=tts.strftime("%Y-%m-%d"),
            test_end=tte.strftime("%Y-%m-%d"),
            base_pnl=rb["pnl_pct"], r2_pnl=rr["pnl_pct"],
            base_dd=rb["max_dd_pct"], r2_dd=rr["max_dd_pct"],
            d_pnl=d_pnl, d_dd=d_dd, fires=st["fired"],
        ))
        verdict_s = "WIN" if d_pnl > 0 else "LOSS"
        print(f"    split {i}: test {tts.strftime('%Y-%m-%d')}→{tte.strftime('%Y-%m-%d')}  "
              f"ΔPnL={d_pnl:+8.1f}pp  ΔDD={d_dd:+6.2f}pp  fires={st['fired']}  {verdict_s}")

    n_wins = sum(1 for s in split_results if s["d_pnl"] > 0)
    print(f"\n  Walk-forward: {n_wins}/{len(split_results)} OOS test windows positive")
    sw_verdict = "STABLE" if n_wins >= max(2, len(split_results) - 1) else "UNSTABLE"

    # ─── Final ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT R2")
    print("=" * 70)
    print(f"  Null-shuffle (28m, {args.null_trials} trials):")
    print(f"    z-score real vs null: {z_score:+.2f}")
    print(f"    p ≈ {n_better_than_real/args.null_trials:.2f} ({n_better_than_real}/{args.null_trials})")
    print(f"    → {null_verdict}")
    print(f"  Sliding walk-forward ({len(split_results)} splits):")
    print(f"    {n_wins}/{len(split_results)} OOS wins")
    print(f"    → {sw_verdict}")
    overall = "DEPLOY-OK" if null_verdict == "GENUINE" and sw_verdict == "STABLE" else "HOLD"
    print(f"\n  OVERALL: {overall}")

    payload = dict(
        version="r2_stability_v1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        traj_params=TRAJ,
        threshold_z=0.0,
        null_shuffle=dict(
            n_trials=args.null_trials,
            real_d_pnl=real_d_pnl,
            real_d_dd=real_d_dd,
            null_d_pnls=null_d_pnls,
            null_mean=null_mean, null_std=null_std,
            z_score=z_score,
            n_better_than_real=n_better_than_real,
            verdict=null_verdict,
        ),
        sliding_walk_forward=dict(
            splits=split_results,
            n_wins=n_wins,
            verdict=sw_verdict,
        ),
        overall=overall,
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nArtifacts → {args.out}")


if __name__ == "__main__":
    main()
