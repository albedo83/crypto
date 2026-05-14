"""In-life exit research (S5 / S8) — three rule families compared.

Spec: docs/superpowers/specs/2026-05-14-inlife-exit-design.md
Plan: docs/superpowers/plans/2026-05-14-inlife-exit.md

Families:
  A — Multi-feature MFE trail (incremental: A.1 global, A.2 + regime, A.3 + hold)
  B — Empirical percentile of (MFE_peak - exit_value) per bucket
  C — ML (logit + light GBM) on per-snapshot features

Validation: walk-forward 4/4 strict on 28m / 12m / 6m / 3m,
            null-shuffle (A & C) on btc_z, parameter stability (A & B).
Output: backtests/inlife_exit_results.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from collections import defaultdict

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
STRATS = ["S5", "S8"]
# Reads DEAD_TIMEOUT_* live from config so the research baseline always tracks
# the currently-shipping dead_timeout (v12.5.0 tightened MAE floor to -500;
# future retunes will shift this baseline). Pin these locally if you need a
# frozen reference run.
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)


# ── Data loading helpers ────────────────────────────────────────────
def load_all():
    """Load data once and cache. Returns dict with everything run_window needs."""
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
    """Build (label, start_ts_ms, end_ts_ms) for each walk-forward window."""
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    out = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        out.append((label, start, end_ts_ms))
    return out


def run_one(ctx, start_ts, end_ts, *, hook=None, apply_adaptive=True):
    """Single run_window invocation with our standard settings."""
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        start_ts, end_ts,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=apply_adaptive,
        inlife_exit_extra=hook,
    )


# ── Baseline (no hook) per window ──────────────────────────────────
def compute_baseline(ctx):
    base = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        r = run_one(ctx, s, e, hook=None)
        base[label] = dict(pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
                           n_trades=r["n_trades"])
        print(f"  baseline {label}: pnl={r['pnl_pct']:+.1f}% DD={r['max_dd_pct']:.1f}% trades={r['n_trades']}")
    return base


# ── Family A.1 — Global MFE trail ──────────────────────────────────
A1_ACTIVATIONS = [300, 500, 700, 1000, 1500]
A1_OFFSETS = [100, 150, 200, 300]


def make_A1_rule(strat: str, activation_bps: int, offset_bps: int):
    """Returns a hook closure for run_window. Fires when MFE>=activation
    and current drops to MFE-offset. Strategy-filtered."""
    def hook(snap):
        if snap["strat"] != strat:
            return False, ""
        if snap["mfe_bps"] < activation_bps:
            return False, ""
        if snap["cur_bps"] <= snap["mfe_bps"] - offset_bps:
            return True, f"{strat.lower()}_inlife_A1"
        return False, ""
    return hook


def _save_results(family_tag, winners, baseline, raw):
    """Persist to JSON for the report stage. Append-mode-safe."""
    import os, json
    out = "/home/crypto/backtests/inlife_exit_artifacts.json"
    state = {}
    if os.path.exists(out):
        with open(out) as f:
            state = json.load(f)
    raw_safe = {}
    for k, v in raw.items():
        key = f"{k[0]}|{k[1]}|{k[2]}" if isinstance(k, tuple) else str(k)
        raw_safe[key] = v
    state[family_tag] = dict(
        winners=winners, baseline=baseline, raw=raw_safe,
        ts=datetime.utcnow().isoformat(),
    )
    with open(out, "w") as f:
        json.dump(state, f, indent=2, default=str)
    print(f"  → saved {family_tag} artifacts to {out}")


def run_family_A(ctx, quick=False):
    import time
    print("\n" + "=" * 70)
    print(" Family A.1 — Global MFE trail")
    print("=" * 70)
    base = compute_baseline(ctx)
    specs = window_specs(ctx["end_ts"]) if not quick else window_specs(ctx["end_ts"])[-1:]
    n_combos = len(STRATS) * len(A1_ACTIVATIONS) * len(A1_OFFSETS)
    print(f"\nGrid: {n_combos} combos × {len(specs)} windows = {n_combos*len(specs)} run_window calls")

    results = {}
    t0 = time.time()
    n_done = 0
    for strat in STRATS:
        for act in A1_ACTIVATIONS:
            for off in A1_OFFSETS:
                key = (strat, act, off)
                results[key] = {}
                for label, s, e in specs:
                    hook = make_A1_rule(strat, act, off)
                    r = run_one(ctx, s, e, hook=hook)
                    results[key][label] = dict(
                        pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
                        n_trades=r["n_trades"])
                n_done += 1
                elapsed = time.time() - t0
                eta = elapsed / n_done * (n_combos - n_done)
                print(f"  [{n_done:3d}/{n_combos}] {strat} act={act} off={off}  elapsed={elapsed:.0f}s  eta={eta:.0f}s")

    # ── Delta table
    print("\n" + "─" * 70)
    print(" A.1 deltas vs baseline  (Δ = candidate - baseline, in PnL pp)")
    print("─" * 70)
    header = f"{'strat':<6}{'act':>5}{'off':>5}  " + "  ".join(f"Δ{lab:<6}" for lab,_,_ in specs)
    print(header)
    winners_A1 = []
    for (strat, act, off), ws in results.items():
        d_pnl = [ws[lab]["pnl_pct"] - base[lab]["pnl_pct"] for lab,_,_ in specs]
        d_dd  = [ws[lab]["max_dd_pct"] - base[lab]["max_dd_pct"] for lab,_,_ in specs]
        avg_dd = sum(d_dd) / len(d_dd)
        is_robust = all(d > 0 for d in d_pnl) and (avg_dd <= 1.0)
        mark = "✓" if is_robust else " "
        print(f"{strat:<6}{act:>5}{off:>5}  " + "  ".join(f"{d:+7.1f}" for d in d_pnl) + f"  {mark}")
        if is_robust:
            winners_A1.append(dict(
                family="A.1", strat=strat,
                params=dict(activation_bps=act, offset_bps=off),
                d_pnl=d_pnl, d_dd=d_dd,
            ))
    print(f"\nA.1 winners (4/4 strict + ΔDD avg ≤+1pp): {len(winners_A1)}")
    for w in winners_A1:
        avg_pnl = sum(w['d_pnl']) / 4
        avg_dd  = sum(w['d_dd']) / 4
        print(f"  ✓ {w['strat']} activation={w['params']['activation_bps']} "
              f"offset={w['params']['offset_bps']}  Δpnl avg={avg_pnl:+.1f}pp  ΔDD avg={avg_dd:+.2f}pp")

    _save_results("A1", winners_A1, base, results)
    return winners_A1


# ── Family B — placeholder, filled in Task 5 ───────────────────────
def run_family_B(ctx, quick=False):
    print("Family B — not yet implemented")
    return []


# ── Family C — placeholder, filled in Task 6 ───────────────────────
def run_family_C(ctx, quick=False):
    print("Family C — not yet implemented")
    return []


def _self_test(ctx):
    """Tiny sanity check: baseline runs and produces sensible numbers."""
    base = compute_baseline(ctx)
    for label, _, _ in window_specs(ctx["end_ts"]):
        assert label in base, f"missing {label}"
        assert -200 < base[label]["pnl_pct"] < 1_000_000, f"absurd PnL on {label}: {base[label]['pnl_pct']}"
        assert base[label]["n_trades"] > 0, f"zero trades on {label}"
    print("\n_self_test OK")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--family", choices=["A", "B", "C", "all"], default="all")
    p.add_argument("--quick", action="store_true",
                   help="run only on 3m window (smoke test)")
    args = p.parse_args()
    ctx = load_all()
    if args.self_test:
        _self_test(ctx)
        return
    if args.family in ("A", "all"):
        run_family_A(ctx, quick=args.quick)
    if args.family in ("B", "all"):
        run_family_B(ctx, quick=args.quick)
    if args.family in ("C", "all"):
        run_family_C(ctx, quick=args.quick)


if __name__ == "__main__":
    main()
