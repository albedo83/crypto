"""Walk-forward — S5 LONG mid-trade exit conditioned on disp_7d (cross-sectional 7d std).

Tests two variants identified by `backtests/eda_s5_unexplored*.py`:

    R1_disp_strong (primary, EDA z=+3.43, n=38):
        At T+8h, if mfe_bps < 50 AND time_in_pain_pct >= 50 AND
        disp_7d(now) >= 700 bps                                  → exit.

    R2_disp_triple (secondary, EDA z=+2.76, n=22):
        At T+8h OR T+12h (first matching checkpoint),
        if mfe_bps < 300 AND time_in_pain_pct >= 60 AND
        sector_div_delta < -500 AND disp_7d(now) >= 700 bps      → exit.

Mechanic: high cross-sectional 7d dispersion = broken alt regime where
S5 LONG fades catch falling knives. Mid-trade variant of the v11.7.28
entry gate (which only filters entries on disp_24h ≥ 700). Mid-trade
gate avoids slot substitution since the position is already open.

S5 LONG only. apply_adaptive_modulator=True (canonical v11.10.0 prod).
New exit reason: `s5_disp_inlife`.

Acceptance: ΔPnL > 0 on each of 4 windows AND ΔDD avg ≤ +1pp.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

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
    TRADE_SYMBOLS,
)


WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
T8_HOURS = 8.0
T12_HOURS = 12.0
DISP_7D_GATE = 700.0  # bps — same threshold as v11.7.28 entry gate (disp_24h)

EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "analysis" / "output" / "pairs_data"


# ── disp_7d lookup ──
def build_disp_7d_lookup():
    """Build ts -> disp_7d (bps) using the same recipe as
    signals.compute_cross_context (std across alts of ret_7d)."""
    by_ts = defaultdict(list)
    n_files = 0
    for sym in TRADE_SYMBOLS:
        p = DATA / f"{sym}_4h_3y.json"
        if not p.exists():
            continue
        candles = json.loads(p.read_text())
        closes = [float(c["c"]) for c in candles]
        for i, c in enumerate(candles):
            if i < 42:  # 7d = 42 × 4h candles
                continue
            if closes[i - 42] > 0:
                by_ts[c["t"]].append(closes[i] / closes[i - 42] - 1)
        n_files += 1
    out = {}
    for ts, rets in by_ts.items():
        if len(rets) >= 10:
            out[ts] = float(np.std(rets) * 1e4)
    print(f"disp_7d lookup: {len(out)} timestamps from {n_files} alt files")
    return out


# ── Hook factories ──
def _make_disp_strong_hook(disp_lookup):
    """R1: At T+8h, fire if mfe<50 AND pain>=50 AND disp_7d(now)>=700."""
    state = {"evaluated": set(), "fired": 0, "evaluated_count": 0}

    def hook(snap):
        if snap["strat"] != "S5" or snap["dir"] != 1:
            return None
        tid = snap.get("trade_id")
        if tid is None or tid in state["evaluated"]:
            return None
        if snap.get("hold_h", 0.0) < T8_HOURS:
            return None
        state["evaluated"].add(tid)
        state["evaluated_count"] += 1

        mfe = snap.get("mfe_bps", 0.0)
        pain = snap.get("time_in_pain_pct", 0.0)
        disp = disp_lookup.get(snap.get("ts_ms"), 0.0)

        if mfe < 50 and pain >= 50 and disp >= DISP_7D_GATE:
            state["fired"] += 1
            return (True, "s5_disp_inlife")
        return None

    return hook, state


def _make_disp_strict_hook(disp_lookup):
    """R3 (post-hoc, super-strict): At T+8h, fire if mfe<50 AND pain>=50 AND
    disp_7d>=700 AND mae_bps<=-500 (deeply underwater + dead regime + dead trade)."""
    state = {"evaluated": set(), "fired": 0, "evaluated_count": 0}

    def hook(snap):
        if snap["strat"] != "S5" or snap["dir"] != 1:
            return None
        tid = snap.get("trade_id")
        if tid is None or tid in state["evaluated"]:
            return None
        if snap.get("hold_h", 0.0) < T8_HOURS:
            return None
        state["evaluated"].add(tid)
        state["evaluated_count"] += 1

        mfe = snap.get("mfe_bps", 0.0)
        mae = snap.get("mae_bps", 0.0)
        pain = snap.get("time_in_pain_pct", 0.0)
        disp = disp_lookup.get(snap.get("ts_ms"), 0.0)

        if mfe < 50 and pain >= 50 and disp >= DISP_7D_GATE and mae <= -500:
            state["fired"] += 1
            return (True, "s5_disp_inlife")
        return None

    return hook, state


def _make_disp_triple_hook(disp_lookup):
    """R2: At T+8h OR T+12h (first match), fire if mfe<300 AND pain>=60 AND
    sd_delta<-500 AND disp_7d(now)>=700."""
    state = {"done": set(), "fired": 0, "evaluated_cps": Counter()}

    def hook(snap):
        if snap["strat"] != "S5" or snap["dir"] != 1:
            return None
        tid = snap.get("trade_id")
        if tid is None or tid in state["done"]:
            return None
        hold_h = snap.get("hold_h", 0.0)
        if hold_h < T8_HOURS:
            return None
        # close the window after T+12h checkpoint
        if hold_h > T12_HOURS + 4:  # +4h grace = next candle past T+12h
            state["done"].add(tid)
            return None

        # Only evaluate at the two discrete checkpoints (held_h ∈ [8,12) once + [12, 16) once)
        bucket = 8 if hold_h < T12_HOURS else 12
        key = (tid, bucket)
        if key in state["evaluated_cps"]:
            return None
        state["evaluated_cps"][key] += 1

        mfe = snap.get("mfe_bps", 0.0)
        pain = snap.get("time_in_pain_pct", 0.0)
        sd = snap.get("sector_div_delta")
        disp = disp_lookup.get(snap.get("ts_ms"), 0.0)

        if sd is None or sd != sd:  # NaN guard
            return None

        if mfe < 300 and pain >= 60 and sd < -500 and disp >= DISP_7D_GATE:
            state["fired"] += 1
            state["done"].add(tid)
            return (True, "s5_disp_inlife")
        return None

    return hook, state


def _make_parity_hook():
    state = {"called": 0}

    def hook(snap):
        state["called"] += 1
        return None
    return hook, state


# ── Data loading ──
def load_all():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    disp_lookup = build_disp_7d_lookup()
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts, disp_lookup=disp_lookup)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    out = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        out.append((label, start, end_ts_ms))
    return out


# ── Run helpers ──
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
    return dict(sorted(Counter(t["reason"] for t in trades).items(), key=lambda kv: -kv[1]))


def _strat_dir_breakdown(trades, reason):
    n_cut = sum(1 for t in trades if t["reason"] == reason)
    n_s5_long = sum(1 for t in trades if t["strat"] == "S5" and t["dir"] == 1)
    return n_cut, n_s5_long


def run_window_set(ctx, hook, label_extra="", reason="s5_disp_inlife"):
    specs = window_specs(ctx["end_ts"])
    out = {}
    for label, s, e in specs:
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        n_cut, n_s5_long = _strat_dir_breakdown(r["trades"], reason)
        out[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], win_rate=r["win_rate"],
            exit_dist=_exit_dist(r["trades"]),
            n_cut=n_cut, n_s5_long=n_s5_long,
            elapsed=time.time() - t0,
        )
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+10.2f}%  "
              f"DD={r['max_dd_pct']:6.2f}%  trades={r['n_trades']:4d}  "
              f"S5L={n_s5_long}  cut={n_cut}  ({time.time()-t0:.1f}s)")
    return out


def verdict(baseline, variant_res, dd_threshold=1.0):
    deltas = {}
    pass_pnl_count = 0
    sum_d_dd = 0.0
    for label, _ in WINDOWS:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl_count += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    pnl_strict = (pass_pnl_count == 4)
    dd_strict = (avg_dd <= dd_threshold)
    if pnl_strict and dd_strict:
        v = "GREEN"
    elif pass_pnl_count == 3 and dd_strict:
        v = "YELLOW"
    else:
        v = "RED"
    return dict(verdict=v, pass_pnl_count=pass_pnl_count,
                avg_dd=avg_dd, deltas=deltas)


# ── Main ──
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="3m window only")
    ap.add_argument("--out", default=str(REPO / "backtests" / "s5_disp_inlife_artifacts.json"))
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/4] Parity check (hook installed, always returns None)")
    parity_hook, _ = _make_parity_hook()
    baseline = run_window_set(ctx, hook=None, label_extra="baseline")
    parity = run_window_set(ctx, hook=parity_hook, label_extra="parity  ")

    parity_ok = True
    for label, _ in WINDOWS:
        b, p = baseline[label], parity[label]
        if (b["n_trades"] != p["n_trades"]
                or abs(b["pnl_pct"] - p["pnl_pct"]) > 1e-6
                or abs(b["max_dd_pct"] - p["max_dd_pct"]) > 1e-6):
            print(f"  ✗ PARITY FAIL on {label}")
            parity_ok = False
        else:
            print(f"  ✓ parity {label} matches baseline ({b['n_trades']} trades)")
    if not parity_ok:
        print("\n!!! PARITY FAILED — aborting.")
        return

    print("\n[2/4] Running R1_disp_strong (4 windows)")
    r1_hook, r1_state = _make_disp_strong_hook(ctx["disp_lookup"])
    r1 = run_window_set(ctx, hook=r1_hook, label_extra="R1      ")
    print(f"  R1 total: evaluated={r1_state['evaluated_count']} fired={r1_state['fired']}")

    print("\n[2/4] Running R2_disp_triple (4 windows)")
    r2_hook, r2_state = _make_disp_triple_hook(ctx["disp_lookup"])
    r2 = run_window_set(ctx, hook=r2_hook, label_extra="R2      ")
    print(f"  R2 total: evaluated_cps={sum(r2_state['evaluated_cps'].values())} fired={r2_state['fired']}")

    print("\n[2/4] Running R3_disp_strict (mfe<50 & pain>=50 & disp>=700 & mae<=-500)")
    r3_hook, r3_state = _make_disp_strict_hook(ctx["disp_lookup"])
    r3 = run_window_set(ctx, hook=r3_hook, label_extra="R3      ")
    print(f"  R3 total: evaluated={r3_state['evaluated_count']} fired={r3_state['fired']}")

    print("\n[3/4] Verdicts (strict ΔDD ≤ +1pp)")
    r1_v = verdict(baseline, r1, dd_threshold=1.0)
    r2_v = verdict(baseline, r2, dd_threshold=1.0)
    r3_v = verdict(baseline, r3, dd_threshold=1.0)

    for name, v in [("R1_disp_strong", r1_v), ("R2_disp_triple", r2_v), ("R3_disp_strict", r3_v)]:
        print(f"\n  {name}: verdict={v['verdict']} "
              f"({v['pass_pnl_count']}/4 PnL>0, ΔDD avg={v['avg_dd']:+.2f}pp)")
        for label, _ in WINDOWS:
            d = v["deltas"][label]
            print(f"    {label}: ΔPnL={d['d_pnl']:+12.2f}pp  ΔDD={d['d_dd']:+6.2f}pp")

    print("\n[4/4] Writing artifacts →", args.out)
    artifacts = {
        "WINDOWS": [w[0] for w in WINDOWS],
        "DISP_7D_GATE": DISP_7D_GATE,
        "baseline": baseline,
        "R1_disp_strong": {"result": r1, "verdict": r1_v, "state": {"evaluated": r1_state["evaluated_count"], "fired": r1_state["fired"]}},
        "R2_disp_triple": {"result": r2, "verdict": r2_v, "state": {"evaluated_cps": dict(r2_state["evaluated_cps"]), "fired": r2_state["fired"]}},
        "R3_disp_strict": {"result": r3, "verdict": r3_v, "state": {"evaluated": r3_state["evaluated_count"], "fired": r3_state["fired"]}},
    }
    # Convert any tuple keys (e.g. R2 evaluated_cps) to strings for json
    def _clean(obj):
        if isinstance(obj, dict):
            return {(str(k) if isinstance(k, tuple) else k): _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(x) for x in obj]
        return obj
    Path(args.out).write_text(json.dumps(_clean(artifacts), indent=2, default=str))
    print("Done.")


if __name__ == "__main__":
    main()
