"""Session filter v2 — regime-conditioned (gate on btc_z).

v1 (backtest_session_filter.py) tested 7 unconditioned variants — all RED.
The session pattern on 30j live is real but doesn't generalize on 28m.

v2 hypothesis: like traj_cut v1→v2, conditioning on bear regime
(btc_z < threshold) may flip the verdict. The session pattern might
matter only when macro is bear-leaning.

Grid:
  base session rules (best from v1):
    - block_long_us            (skip LONG 14-21h UTC)
    - block_long_us_open       (skip LONG 14-17h UTC)
    - block_long_s5_us         (skip S5 LONG 14-21h UTC)
  regime conditions:
    - bz < -0.5 (bear strict, like traj_cut R1)
    - bz <  0.0 (bear + below-neutral, like traj_cut R2)

= 6 variants.
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
    MACRO_LOOKBACK_DAYS, MACRO_Z_WINDOW_DAYS,
)


WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
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

    # Pre-compute btc_z map (mirrors backtest_rolling.py logic)
    btc_candles = data.get("BTC", [])
    closes = np.array([c["c"] for c in btc_candles])
    n_lb = int(MACRO_LOOKBACK_DAYS * 6)   # candles per day (4h)
    z_window = int(MACRO_Z_WINDOW_DAYS * 6)
    btc_z_map: dict[int, float] = {}
    rets_history = []
    for i in range(n_lb, len(closes)):
        r = (closes[i] / closes[i - n_lb] - 1) * 1e4
        rets_history.append(r)
        if len(rets_history) < 30:
            continue
        arr = np.array(rets_history[-z_window:])
        m, s = arr.mean(), arr.std()
        if s > 0:
            btc_z_map[btc_candles[i]["t"]] = (r - m) / s
    print(f"  loaded in {time.time()-t0:.1f}s ; btc_z_map has {len(btc_z_map)} points")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts, btc_z_map=btc_z_map)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    return [(label, int((end_dt - relativedelta(months=months)).timestamp() * 1000), end_ts_ms)
            for label, months in WINDOWS]


def hour_of(ts_ms: int) -> int:
    return datetime.utcfromtimestamp(ts_ms // 1000).hour


def make_skip_fn(label: str, btc_z_map: dict, btc_z_threshold: float):
    """Build a skip_fn that only fires when btc_z < threshold."""
    state = {"fired": 0, "fired_by_regime": Counter()}
    rule = label.split("__")[0]  # rule name without regime suffix

    def fn(coin, ts, strat, dir_):
        # Direction / strat / hour gating first
        if dir_ != 1:
            return False
        h = hour_of(ts)
        # Match rule
        if rule == "block_long_us":
            if not (14 <= h < 21):
                return False
        elif rule == "block_long_us_open":
            if not (14 <= h < 17):
                return False
        elif rule == "block_long_s5_us":
            if strat != "S5" or not (14 <= h < 21):
                return False
        else:
            return False
        # Regime gate: only fire skip if bear
        bz = btc_z_map.get(ts, 0.0)
        if bz >= btc_z_threshold:
            return False
        state["fired"] += 1
        state["fired_by_regime"]["bear" if bz < -0.5 else "neutral"] += 1
        return True

    return fn, state


def run_one(ctx, s, e, skip_fn=None):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        s, e,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=True,
        skip_fn=skip_fn,
    )


def run_window_set(ctx, skip_fn, label):
    out = {}
    for lbl, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, skip_fn=skip_fn)
        out[lbl] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], elapsed=time.time() - t0,
        )
        print(f"  {label:<32} {lbl}: pnl={r['pnl_pct']:+9.1f}%  "
              f"DD={r['max_dd_pct']:5.1f}%  trades={r['n_trades']:4d}  "
              f"({time.time()-t0:.1f}s)")
    return out


def verdict(base, var):
    pass_pnl = 0; sum_d_dd = 0.0; deltas = {}
    for lbl, _ in WINDOWS:
        d_pnl = var[lbl]["pnl_pct"] - base[lbl]["pnl_pct"]
        d_dd = var[lbl]["max_dd_pct"] - base[lbl]["max_dd_pct"]
        deltas[lbl] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    v = "GREEN" if (pass_pnl == 4 and avg_dd >= -2.0) else ("YELLOW" if pass_pnl == 3 else "RED")
    return dict(verdict=v, pass_pnl=pass_pnl, avg_dd=avg_dd, deltas=deltas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default="/home/crypto/backtests/session_filter_v2_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/3] Baseline (no filter)")
    base = run_window_set(ctx, None, "baseline")

    print("\n[2/3] Regime-conditioned variants")
    grid = []
    for rule in ("block_long_us", "block_long_us_open", "block_long_s5_us"):
        for z_thr_label, z_thr in (("bz_lt_neg05", -0.5), ("bz_lt_0", 0.0)):
            grid.append((f"{rule}__{z_thr_label}", rule, z_thr))

    results = {}
    for i, (label, rule, z_thr) in enumerate(grid, 1):
        print(f"\n  [{i}/{len(grid)}] {label}")
        fn, state = make_skip_fn(label, ctx["btc_z_map"], z_thr)
        res = run_window_set(ctx, fn, label)
        v = verdict(base, res)
        results[label] = dict(
            res=res, verdict=v, rule=rule, z_threshold=z_thr,
            fired=state["fired"],
            fired_by_regime=dict(state["fired_by_regime"]),
        )
        print(f"    verdict={v['verdict']}  pass={v['pass_pnl']}/4  "
              f"ΔDDavg={v['avg_dd']:+.2f}pp  fired={state['fired']}  by_regime={dict(state['fired_by_regime'])}")

    print("\n[3/3] Ranking")
    ranked = sorted(results.items(),
                    key=lambda kv: (kv[1]['verdict']['pass_pnl'],
                                    sum(d['d_pnl'] for d in kv[1]['verdict']['deltas'].values()),
                                    kv[1]['verdict']['avg_dd']),
                    reverse=True)
    print(f"\n{'variant':<35} {'verdict':8} {'pass':>5} {'ΔDDavg':>9}  {'sumΔPnL':>11}  "
          f"{'28mΔ':>10} {'12mΔ':>9} {'6mΔ':>8} {'3mΔ':>8}  {'fired':>5}")
    for label, s in ranked:
        v = s["verdict"]
        sum_d = sum(d["d_pnl"] for d in v["deltas"].values())
        deltas_str = " ".join(f"{v['deltas'][w[0]]['d_pnl']:+9.1f}" for w in WINDOWS)
        print(f"{label:<35} {v['verdict']:8} {v['pass_pnl']:>2}/4 "
              f"{v['avg_dd']:>+8.2f}pp  {sum_d:>+11.1f}  {deltas_str}  {s['fired']:>5}")

    payload = dict(
        version="session_filter_v2_regime",
        timestamp=datetime.now(timezone.utc).isoformat(),
        baseline={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                  for k, v in base.items()},
        variants={label: dict(
            rule=s["rule"], z_threshold=s["z_threshold"],
            res={k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                 for k, v in s["res"].items()},
            verdict=s["verdict"], fired=s["fired"],
            fired_by_regime=s["fired_by_regime"],
        ) for label, s in results.items()},
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nArtifacts → {args.out}")


if __name__ == "__main__":
    main()
