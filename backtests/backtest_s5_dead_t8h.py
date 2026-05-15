"""S5 LONG "dead trade walking" — T+8h checkpoint exit, walk-forward 4/4 strict.

Validates two variants from `backtests/mid_trade_profiling_eda.md`:

    Variant A — "strong" (safety parachute):
        At T+8h, if mfe_bps_to_date < 50 AND time_in_pain_pct >= 50  → exit.

    Variant B — "triple_mid" (surgical):
        At T+8h, if mfe_bps_to_date < 300 AND time_in_pain_pct > 60
        AND sector_div_delta < -500                                   → exit.

Constraints:
  * S5 LONG only (strat="S5" AND direction=+1). S5 SHORT untouched.
  * `apply_adaptive_modulator=True` in every run (canonical v11.10.0 prod config).
  * Single checkpoint T+8h, evaluated once per position (one-shot rule).
  * New exit reason: `s5_dead_t8h`.

Hook needs per-position state to know whether the T+8h evaluation already
fired. The snapshot now carries `trade_id` so a closure-level dict is enough.

Acceptance criteria (walk-forward 4/4 strict):
  * ΔPnL > 0 on EACH of 4 windows (28m / 12m / 6m / 3m)
  * avg ΔDD ≤ +2pp across the 4 windows
  * Both → GREEN. 3/4 → YELLOW. ≤2/4 → RED.

Parity check: with the hook installed but always returning None, the result
must be bit-identical to baseline (no mutation of the engine path).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
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
T8_HOURS = 8.0  # checkpoint
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)

# ── Data loading ───────────────────────────────────────────────────────
def load_all():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    out = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        out.append((label, start, end_ts_ms))
    return out


# ── Hook factories ─────────────────────────────────────────────────────
def _make_dead_t8h_hook(variant: str):
    """Return (hook, state) where state is mutable for inspection.

    The hook fires the first time the position crosses T+8h (held >= 2 on 4h
    candles). After that, the trade_id is marked done and never re-evaluated.

    S5 LONG only. Other strats / directions short-circuit to None.
    """
    if variant == "strong":
        mfe_max = 50
        pain_min = 50
        sd_max = None
    elif variant == "triple_mid":
        mfe_max = 300
        pain_min = 60  # strictly > 60 in spec; we'll use >= 60.0001 ≈ > 60
        sd_max = -500
    elif variant == "parity":
        mfe_max = pain_min = None
        sd_max = None
    else:
        raise ValueError(f"unknown variant {variant!r}")

    state = {
        "evaluated": set(),     # trade_ids already evaluated at T+8h
        "fired": 0,             # count of fires
        "evaluated_count": 0,   # count of S5 LONG positions reaching T+8h
    }

    def hook(snap):
        if variant == "parity":
            return None  # never fires — used for parity check

        # S5 LONG only
        if snap["strat"] != "S5" or snap["dir"] != 1:
            return None

        tid = snap.get("trade_id")
        if tid is None:
            return None
        if tid in state["evaluated"]:
            return None  # already decided at T+8h for this position

        hold_h = snap.get("hold_h", 0.0)
        if hold_h < T8_HOURS:
            return None  # not yet at the checkpoint

        # First time we see this position at hold_h >= 8 — evaluate once.
        state["evaluated"].add(tid)
        state["evaluated_count"] += 1

        mfe = snap.get("mfe_bps", 0.0)
        pain = snap.get("time_in_pain_pct", 0.0)
        sd = snap.get("sector_div_delta")

        if variant == "strong":
            fire = (mfe < mfe_max) and (pain >= pain_min)
        else:  # triple_mid
            if sd is None or sd != sd:  # NaN guard
                return None
            fire = (mfe < mfe_max) and (pain > pain_min) and (sd < sd_max)

        if fire:
            state["fired"] += 1
            return (True, "s5_dead_t8h")
        return None

    return hook, state


# ── Backtest runner ────────────────────────────────────────────────────
def run_one(ctx, start_ts, end_ts, *, hook=None):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        start_ts, end_ts,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=True,
        inlife_exit_extra=hook,
    )


def _exit_distribution(trades):
    c = Counter(t["reason"] for t in trades)
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def _strat_dir_breakdown(trades, reason="s5_dead_t8h"):
    """Return (n_cut, n_total_s5_long) for sanity."""
    n_cut = sum(1 for t in trades if t["reason"] == reason)
    n_s5_long = sum(1 for t in trades if t["strat"] == "S5" and t["dir"] == 1)
    return n_cut, n_s5_long


def run_window_set(ctx, hook, label_extra=""):
    specs = window_specs(ctx["end_ts"])
    out = {}
    for label, s, e in specs:
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        exit_dist = _exit_distribution(r["trades"])
        n_cut, n_s5_long = _strat_dir_breakdown(r["trades"])
        out[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], win_rate=r["win_rate"],
            by_strat=r["by_strat"], exit_dist=exit_dist,
            n_dead_t8h=n_cut, n_s5_long=n_s5_long,
            elapsed=time.time() - t0,
        )
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+.1f}% "
              f"DD={r['max_dd_pct']:.1f}% trades={r['n_trades']} "
              f"S5L={n_s5_long} cut={n_cut} ({time.time()-t0:.1f}s)")
    return out


# ── Verdict ────────────────────────────────────────────────────────────
def verdict(baseline, variant_res):
    deltas = {}
    pass_pnl_count = 0
    sum_d_dd = 0.0
    for label, _, _ in window_specs(0) if False else [(w[0], 0, 0) for w in WINDOWS]:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl_count += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    pnl_strict = (pass_pnl_count == 4)
    dd_strict = (avg_dd <= 2.0)
    if pnl_strict and dd_strict:
        v = "GREEN"
    elif pass_pnl_count == 3:
        v = "YELLOW"
    else:
        v = "RED"
    return dict(verdict=v, pass_pnl_count=pass_pnl_count,
                avg_dd=avg_dd, deltas=deltas)


# ── Main pipeline ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="3m window only, quick sanity check")
    ap.add_argument("--out", default="/home/crypto/backtests/s5_dead_t8h_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/4] Parity check (hook installed, always returns None)")
    parity_hook, _ = _make_dead_t8h_hook("parity")
    print("  baseline 4 windows...")
    baseline = run_window_set(ctx, hook=None, label_extra="baseline")
    print("  parity (hook = None-returning) 4 windows...")
    parity = run_window_set(ctx, hook=parity_hook, label_extra="parity  ")

    parity_ok = True
    for label, _, _ in window_specs(ctx["end_ts"]):
        b, p = baseline[label], parity[label]
        if (b["n_trades"] != p["n_trades"]
                or abs(b["pnl_pct"] - p["pnl_pct"]) > 1e-6
                or abs(b["max_dd_pct"] - p["max_dd_pct"]) > 1e-6):
            print(f"  ✗ PARITY FAIL on {label}: "
                  f"baseline={b['n_trades']}/{b['pnl_pct']:.4f}/{b['max_dd_pct']:.4f} "
                  f"parity={p['n_trades']}/{p['pnl_pct']:.4f}/{p['max_dd_pct']:.4f}")
            parity_ok = False
        else:
            print(f"  ✓ parity {label} matches baseline "
                  f"({b['n_trades']} trades, {b['pnl_pct']:+.2f}%, {b['max_dd_pct']:.2f}% DD)")

    if not parity_ok:
        print("\n!!! PARITY FAILED — aborting before variant runs.")
        return

    print("\n[2/4] Running variant 'strong' 4 windows")
    strong_hook, strong_state = _make_dead_t8h_hook("strong")
    strong = run_window_set(ctx, hook=strong_hook, label_extra="strong  ")
    print(f"  strong total: evaluated={strong_state['evaluated_count']} fired={strong_state['fired']}")

    print("\n[2/4] Running variant 'triple_mid' 4 windows")
    triple_hook, triple_state = _make_dead_t8h_hook("triple_mid")
    triple = run_window_set(ctx, hook=triple_hook, label_extra="triple  ")
    print(f"  triple_mid total: evaluated={triple_state['evaluated_count']} fired={triple_state['fired']}")

    print("\n[3/4] Computing deltas & verdicts")
    strong_v = verdict(baseline, strong)
    triple_v = verdict(baseline, triple)

    print(f"\n  STRONG:     verdict={strong_v['verdict']} "
          f"({strong_v['pass_pnl_count']}/4 PnL pos, ΔDD avg={strong_v['avg_dd']:+.2f}pp)")
    for label, _, _ in window_specs(ctx["end_ts"]):
        d = strong_v["deltas"][label]
        print(f"    {label}: ΔPnL={d['d_pnl']:+9.2f}pp  ΔDD={d['d_dd']:+6.2f}pp")

    print(f"\n  TRIPLE_MID: verdict={triple_v['verdict']} "
          f"({triple_v['pass_pnl_count']}/4 PnL pos, ΔDD avg={triple_v['avg_dd']:+.2f}pp)")
    for label, _, _ in window_specs(ctx["end_ts"]):
        d = triple_v["deltas"][label]
        print(f"    {label}: ΔPnL={d['d_pnl']:+9.2f}pp  ΔDD={d['d_dd']:+6.2f}pp")

    artifacts = dict(
        ts=datetime.utcnow().isoformat(),
        baseline=baseline,
        strong=strong, strong_verdict=strong_v,
        strong_state=dict(evaluated=strong_state["evaluated_count"],
                          fired=strong_state["fired"]),
        triple_mid=triple, triple_mid_verdict=triple_v,
        triple_mid_state=dict(evaluated=triple_state["evaluated_count"],
                              fired=triple_state["fired"]),
        parity=parity, parity_ok=parity_ok,
    )
    with open(args.out, "w") as f:
        json.dump(artifacts, f, indent=2, default=str)
    print(f"\n[4/4] Artifacts: {args.out}")


if __name__ == "__main__":
    main()
