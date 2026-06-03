"""Session filter v3 — recent-only windows (market regime change hypothesis).

v1 (unconditioned) and v2 (regime-conditioned) both failed walk-forward
4/4 on the canonical 28m/12m/6m/3m windows. User observation: those
include the 2024 bear cycle where US-LONG worked; the current market
may have shifted in the last few months.

This v3 tests the same variants on RECENT windows only — 6m, 3m, 2m, 1m
— to check whether the filter pays in the current regime.

If 4/4 passes on these shorter windows = regime-shift signal is real
and the filter would help RIGHT NOW. Caveat: shorter windows = noisier;
result is more a "current regime check" than a long-term decision.
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


# Recent windows only — 6m, 3m, 2m, 1m
WINDOWS = [("6m", 6), ("3m", 3), ("2m", 2), ("1m", 1)]
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
    btc_candles = data.get("BTC", [])
    closes = np.array([c["c"] for c in btc_candles])
    n_lb = int(MACRO_LOOKBACK_DAYS * 6)
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
    print(f"  loaded in {time.time()-t0:.1f}s ; btc_z_map={len(btc_z_map)}")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts, btc_z_map=btc_z_map)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    return [(label, int((end_dt - relativedelta(months=months)).timestamp() * 1000), end_ts_ms)
            for label, months in WINDOWS]


def hour_of(ts_ms):
    return datetime.utcfromtimestamp(ts_ms // 1000).hour


def make_skip_fn(rule: str, btc_z_map: dict | None, z_thr: float | None):
    """rule defines hour/strat gate; btc_z_map+z_thr is optional regime gate."""
    state = {"fired": 0}
    def fn(coin, ts, strat, dir_):
        if dir_ != 1:
            return False
        h = hour_of(ts)
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
        if z_thr is not None and btc_z_map is not None:
            bz = btc_z_map.get(ts, 0.0)
            if bz >= z_thr:
                return False
        state["fired"] += 1
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
            n_trades=r["n_trades"],
        )
        print(f"  {label:<35} {lbl}: pnl={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:5.1f}%  trades={r['n_trades']:4d}  ({time.time()-t0:.1f}s)")
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
                    default="/home/crypto/backtests/session_filter_v3_recent_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/3] Baseline")
    base = run_window_set(ctx, None, "baseline")

    print("\n[2/3] Variants on recent windows")
    grid = []
    # Unconditioned (baseline of v1)
    for rule in ("block_long_us", "block_long_us_open", "block_long_s5_us"):
        grid.append((rule, rule, None))
    # Regime-conditioned (best of v2)
    for rule in ("block_long_us", "block_long_us_open", "block_long_s5_us"):
        for z_label, z_thr in (("bz_neg05", -0.5), ("bz_0", 0.0)):
            grid.append((f"{rule}__{z_label}", rule, z_thr))

    results = {}
    for i, (label, rule, z_thr) in enumerate(grid, 1):
        print(f"\n  [{i}/{len(grid)}] {label}")
        fn, state = make_skip_fn(rule, ctx["btc_z_map"], z_thr)
        res = run_window_set(ctx, fn, label)
        v = verdict(base, res)
        results[label] = dict(res=res, verdict=v, rule=rule, z_threshold=z_thr, fired=state["fired"])
        print(f"    verdict={v['verdict']}  pass={v['pass_pnl']}/4  ΔDDavg={v['avg_dd']:+.2f}pp  fired={state['fired']}")

    print("\n[3/3] Ranking (RECENT 6m/3m/2m/1m)")
    ranked = sorted(results.items(),
                    key=lambda kv: (kv[1]['verdict']['pass_pnl'],
                                    sum(d['d_pnl'] for d in kv[1]['verdict']['deltas'].values()),
                                    kv[1]['verdict']['avg_dd']),
                    reverse=True)
    print(f"\n{'variant':<35} {'verdict':8} {'pass':>5} {'ΔDDavg':>9}  {'sumΔPnL':>11}  "
          f"{'6mΔ':>10} {'3mΔ':>9} {'2mΔ':>8} {'1mΔ':>8}  {'fired':>5}")
    for label, s in ranked:
        v = s["verdict"]
        sum_d = sum(d["d_pnl"] for d in v["deltas"].values())
        deltas_str = " ".join(f"{v['deltas'][w[0]]['d_pnl']:+9.1f}" for w in WINDOWS)
        print(f"{label:<35} {v['verdict']:8} {v['pass_pnl']:>2}/4 "
              f"{v['avg_dd']:>+8.2f}pp  {sum_d:>+11.1f}  {deltas_str}  {s['fired']:>5}")

    payload = dict(
        version="session_filter_v3_recent_windows",
        timestamp=datetime.now(timezone.utc).isoformat(),
        windows=WINDOWS,
        baseline=base,
        variants={label: dict(rule=s["rule"], z_threshold=s["z_threshold"],
                              res=s["res"], verdict=s["verdict"], fired=s["fired"])
                  for label, s in results.items()},
    )
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nArtifacts → {args.out}")


if __name__ == "__main__":
    main()
