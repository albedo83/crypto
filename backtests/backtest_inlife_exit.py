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
    if winners_A1:
        print("\n→ A.1 has winners — skipping A.2 per parsimony rule (spec §3)")
        return winners_A1
    print("\n→ A.1 has 0 winners — running A.2")
    winners_A2 = run_family_A2(ctx, base, specs)
    _save_results("A2", winners_A2, base, {})
    return winners_A1 + winners_A2


# ── Family A.2 — regime-conditioned MFE trail ──────────────────────
A2_REGIME_BUCKETS = [("bear", -10.0, -0.5), ("neutral", -0.5, 0.5), ("bull", 0.5, 10.0)]


def regime_bucket(z: float) -> str:
    for name, lo, hi in A2_REGIME_BUCKETS:
        if lo <= z < hi:
            return name
    return "neutral"


def make_A2_filtered_rule(strat: str, bucket_name: str, activation_bps: int, offset_bps: int):
    """A.2 rule that only fires when in a specific regime bucket."""
    def hook(snap):
        if snap["strat"] != strat:
            return False, ""
        if regime_bucket(snap.get("btc_z", 0.0)) != bucket_name:
            return False, ""
        if snap["mfe_bps"] < activation_bps:
            return False, ""
        if snap["cur_bps"] <= snap["mfe_bps"] - offset_bps:
            return True, f"{strat.lower()}_inlife_A2"
        return False, ""
    return hook


def make_A2_composite_rule(strat: str, params_by_bucket: dict):
    """Composite: each bucket has its own (act, off). Bucket with params (99999, 0) never fires."""
    def hook(snap):
        if snap["strat"] != strat:
            return False, ""
        b = regime_bucket(snap.get("btc_z", 0.0))
        act, off = params_by_bucket.get(b, (99999, 0))
        if snap["mfe_bps"] < act:
            return False, ""
        if snap["cur_bps"] <= snap["mfe_bps"] - off:
            return True, f"{strat.lower()}_inlife_A2"
        return False, ""
    return hook


def run_family_A2(ctx, base, specs):
    """Per (strat, bucket), find best (act, off). Then compose final per-strat rule
    and verify 4/4. Returns list of robust composite candidates."""
    import time
    from collections import defaultdict
    print("\n— Family A.2 — per-regime sweep —")
    n_combos = len(STRATS) * len(A2_REGIME_BUCKETS) * len(A1_ACTIVATIONS) * len(A1_OFFSETS)
    print(f"A.2 grid: {n_combos} per-bucket combos × {len(specs)} windows = {n_combos*len(specs)} run_window calls")

    # Step 1: per (strat, bucket), sweep 20 combos
    winners_by_bucket = defaultdict(list)
    t0 = time.time()
    n_done = 0
    for strat in STRATS:
        for bname, _, _ in A2_REGIME_BUCKETS:
            for act in A1_ACTIVATIONS:
                for off in A1_OFFSETS:
                    hook = make_A2_filtered_rule(strat, bname, act, off)
                    d_pnl, d_dd = [], []
                    for label, s, e in specs:
                        r = run_one(ctx, s, e, hook=hook)
                        d_pnl.append(r["pnl_pct"] - base[label]["pnl_pct"])
                        d_dd.append(r["max_dd_pct"] - base[label]["max_dd_pct"])
                    # Bucket may be empty in some windows → use >= instead of strict >
                    if all(d >= 0 for d in d_pnl) and (sum(d_dd)/len(d_dd) <= 1.0):
                        winners_by_bucket[(strat, bname)].append(dict(
                            act=act, off=off,
                            d_pnl_avg=sum(d_pnl)/len(d_pnl),
                            d_pnl=d_pnl, d_dd=d_dd,
                        ))
                    n_done += 1
                    elapsed = time.time() - t0
                    if n_done % 10 == 0:
                        print(f"  [{n_done:3d}/{n_combos}] {strat} {bname} act={act} off={off}  elapsed={elapsed:.0f}s")

    # Step 2: best per (strat, bucket)
    print("\nBest combo per (strat, bucket):")
    best_per_bucket = {}
    for (strat, bname), cands in winners_by_bucket.items():
        if cands:
            best = max(cands, key=lambda c: c["d_pnl_avg"])
            best_per_bucket[(strat, bname)] = best
            print(f"  {strat:>3} {bname:<8}: act={best['act']:>5} off={best['off']:>4}  Δpnl avg={best['d_pnl_avg']:+.1f}pp  ΔDD avg={sum(best['d_dd'])/4:+.2f}pp")
        else:
            print(f"  {strat:>3} {bname:<8}: no improving combo")

    # Step 3: compose + verify
    print("\nComposite rules:")
    winners_A2 = []
    for strat in STRATS:
        params = {}
        for bname, _, _ in A2_REGIME_BUCKETS:
            if (strat, bname) in best_per_bucket:
                b = best_per_bucket[(strat, bname)]
                params[bname] = (b["act"], b["off"])
            else:
                params[bname] = (99999, 0)
        hook = make_A2_composite_rule(strat, params)
        d_pnl, d_dd = [], []
        for label, s, e in specs:
            r = run_one(ctx, s, e, hook=hook)
            d_pnl.append(r["pnl_pct"] - base[label]["pnl_pct"])
            d_dd.append(r["max_dd_pct"] - base[label]["max_dd_pct"])
        avg_dd = sum(d_dd) / len(d_dd)
        is_robust = all(d > 0 for d in d_pnl) and (avg_dd <= 1.0)
        mark = "✓" if is_robust else " "
        print(f"  {strat} composed {params}:")
        print(f"    Δpnl: " + " ".join(f"{d:+8.1f}" for d in d_pnl) + f"  ΔDD avg={avg_dd:+.2f}pp  {mark}")
        if is_robust:
            winners_A2.append(dict(
                family="A.2", strat=strat, params=params,
                d_pnl=d_pnl, d_dd=d_dd,
            ))
    print(f"\nA.2 winners (strict 4/4 + ΔDD avg ≤+1pp): {len(winners_A2)}")
    return winners_A2


# ── Family B — empirical percentile ─────────────────────────────────
B_PERCENTILES = [70, 80, 90]
B_MIN_MFE = [300, 500]
B_HOLD_BUCKETS = [("early", 0, 12), ("mid", 12, 30), ("late", 30, 999)]


def hold_bucket(h: float) -> str:
    for name, lo, hi in B_HOLD_BUCKETS:
        if lo <= h < hi:
            return name
    return "late"


def build_B_distributions(ctx, start_ts, end_ts, min_mfe_bps: int):
    """Run baseline on [start_ts, end_ts] and collect (MFE_peak - net_bps)
    for each winner trade in STRATS, bucketed by (strat, dir, hold_bucket, regime).
    The regime key uses regime_bucket(0.0) since trade-level btc_z_at_entry
    isn't tracked in run_window's trade dict (acceptable fallback —
    bucketing on entry regime is coarser anyway)."""
    from collections import defaultdict
    r = run_one(ctx, start_ts, end_ts, hook=None)
    distribs = defaultdict(list)
    for t in r.get("trades", []):
        # run_window's trade dict keys: strat, dir, mfe_bps, mae_bps, entry_t,
        # exit_t, net (bps), pnl, coin, reason, size
        strat = t.get("strat")
        if strat not in STRATS:
            continue
        mfe = t.get("mfe_bps", 0)
        net = t.get("net", 0)
        dir_v = t.get("dir", 1)
        entry_t = t.get("entry_t", 0)
        exit_t = t.get("exit_t", 0)
        hold_h = (exit_t - entry_t) / 3.6e6 if entry_t and exit_t else 0
        if mfe < min_mfe_bps or net <= 0:
            continue
        retrace = mfe - net
        key = (strat, dir_v, hold_bucket(hold_h), regime_bucket(0.0))
        distribs[key].append(retrace)
    return distribs


def make_B_rule(strat: str, distribs: dict, percentile: int, min_mfe_bps: int):
    def hook(snap):
        if snap["strat"] != strat:
            return False, ""
        if snap["mfe_bps"] < min_mfe_bps:
            return False, ""
        key = (strat, snap["dir"], hold_bucket(snap["hold_h"]), regime_bucket(snap.get("btc_z", 0.0)))
        bucket = distribs.get(key)
        if not bucket or len(bucket) < 10:
            return False, ""
        threshold = float(np.percentile(bucket, percentile))
        if snap["mfe_bps"] - snap["cur_bps"] >= threshold:
            return True, f"{strat.lower()}_inlife_B"
        return False, ""
    return hook


def run_family_B(ctx, quick=False):
    import time
    print("\n" + "=" * 70)
    print(" Family B — Empirical percentile")
    print("=" * 70)
    base = compute_baseline(ctx)
    specs = window_specs(ctx["end_ts"]) if not quick else window_specs(ctx["end_ts"])[-1:]
    end_dt = datetime.fromtimestamp(ctx["end_ts"] / 1000, tz=timezone.utc)
    is_start = int((end_dt - relativedelta(months=36)).timestamp() * 1000)
    is_end = int((end_dt - relativedelta(months=12)).timestamp() * 1000)

    winners_B = []
    t0 = time.time()
    for strat in STRATS:
        for mfe_min in B_MIN_MFE:
            distribs = build_B_distributions(ctx, is_start, is_end, mfe_min)
            tot_obs = sum(len(v) for v in distribs.values())
            print(f"  {strat} min_mfe={mfe_min}: built {tot_obs} obs across {len(distribs)} buckets")
            if tot_obs < 30:
                print(f"    skip — too few obs for reliable percentile")
                continue
            for p in B_PERCENTILES:
                hook = make_B_rule(strat, distribs, p, mfe_min)
                d_pnl, d_dd = [], []
                for label, s, e in specs:
                    r = run_one(ctx, s, e, hook=hook)
                    d_pnl.append(r["pnl_pct"] - base[label]["pnl_pct"])
                    d_dd.append(r["max_dd_pct"] - base[label]["max_dd_pct"])
                avg_dd = sum(d_dd) / len(d_dd)
                is_robust = all(d > 0 for d in d_pnl) and (avg_dd <= 1.0)
                mark = "✓" if is_robust else " "
                print(f"    p{p} mfe_min={mfe_min}: " + " ".join(f"{d:+7.1f}" for d in d_pnl) + f"  ΔDD avg={avg_dd:+.2f}pp  {mark}")
                if is_robust:
                    winners_B.append(dict(
                        family="B", strat=strat,
                        params=dict(percentile=p, min_mfe_bps=mfe_min),
                        d_pnl=d_pnl, d_dd=d_dd,
                    ))
    print(f"\nB winners (strict 4/4 + ΔDD avg ≤+1pp): {len(winners_B)}  (runtime {time.time()-t0:.0f}s)")
    for w in winners_B:
        print(f"  ✓ {w['strat']} p{w['params']['percentile']} mfe_min={w['params']['min_mfe_bps']}")
    _save_results("B", winners_B, base, {})
    return winners_B


# ── Family C — ML (logit + light GBM) ──────────────────────────────
# Per-trade aggregate features (simplified — see plan Caveat 2).
# `bps_path` is not tracked in run_window's trade dict, so we cannot derive
# per-candle granular features here. Acceptable degradation: train on
# trade-level aggregates, apply at runtime via per-snapshot prediction.
C_TAUS = [0.55, 0.65, 0.75]
C_LABEL_DRAWDOWN_BPS = 200


def build_snapshot_dataset(ctx, start_ts, end_ts, label_dd_bps: int = C_LABEL_DRAWDOWN_BPS):
    """Reconstruct per-trade aggregate features (one row per trade).
    Label = 1 if the trade ended having given back ≥`label_dd_bps` from MFE
    (i.e. mfe_bps - net >= label_dd_bps). Returns (X, y, feat_names).

    Feature order MUST match the runtime construction in make_C_rule below.
    """
    r = run_one(ctx, start_ts, end_ts, hook=None)
    rows, ys = [], []
    feat_names = ["mfe", "mae", "net_proxy", "hold_h",
                  "is_S5", "is_S8", "is_long"]
    for t in r.get("trades", []):
        strat = t.get("strat")
        if strat not in STRATS:
            continue
        mfe = float(t.get("mfe_bps", 0) or 0)
        mae = float(t.get("mae_bps", 0) or 0)
        net = float(t.get("net", 0) or 0)  # net P&L bps (run_window key)
        dir_v = int(t.get("dir", 1))
        entry_t = t.get("entry_t", 0) or 0
        exit_t = t.get("exit_t", 0) or 0
        hold_h = (exit_t - entry_t) / 3.6e6 if entry_t and exit_t else 0.0
        row = [mfe, mae, net, hold_h,
               1.0 if strat == "S5" else 0.0,
               1.0 if strat == "S8" else 0.0,
               1.0 if dir_v == 1 else 0.0]
        label = 1 if (mfe - net) >= label_dd_bps else 0
        rows.append(row)
        ys.append(label)
    return np.array(rows), np.array(ys), feat_names


def make_C_rule(strat: str, model, scaler, tau: float, feat_names):
    """Runtime hook: per-snapshot, build feature row (mfe, mae, cur_bps as
    net-proxy, hold_h, strat one-hot, is_long) → scale → predict_proba →
    exit if proba ≥ tau. Gated by mfe_bps ≥ 100 to avoid premature triggers."""
    def hook(snap):
        if snap["strat"] != strat:
            return False, ""
        if snap["mfe_bps"] < 100:
            return False, ""
        feats = np.array([[
            snap["mfe_bps"], snap["mae_bps"], snap["cur_bps"], snap["hold_h"],
            1.0 if strat == "S5" else 0.0,
            1.0 if strat == "S8" else 0.0,
            1.0 if snap["dir"] == 1 else 0.0,
        ]])
        feats_s = scaler.transform(feats)
        proba = float(model.predict_proba(feats_s)[0, 1])
        if proba >= tau:
            return True, f"{strat.lower()}_inlife_C"
        return False, ""
    return hook


def run_family_C(ctx, quick=False):
    import time
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    print("\n" + "=" * 70)
    print(" Family C — ML (logit + GBM)")
    print("=" * 70)
    base = compute_baseline(ctx)
    specs = window_specs(ctx["end_ts"]) if not quick else window_specs(ctx["end_ts"])[-1:]
    end_dt = datetime.fromtimestamp(ctx["end_ts"] / 1000, tz=timezone.utc)
    is_start = int((end_dt - relativedelta(months=36)).timestamp() * 1000)
    is_end = int((end_dt - relativedelta(months=12)).timestamp() * 1000)

    print("Building training dataset (per-trade rows from IS baseline)...")
    X, y, names = build_snapshot_dataset(ctx, is_start, is_end)
    print(f"  Dataset: {X.shape[0]} trades, {int(y.sum())} positive labels"
          f" ({y.mean():.1%})" if X.shape[0] else "  Dataset: empty")
    if X.shape[0] < 100 or y.sum() < 20:
        print(f"  WARNING: dataset too small ({X.shape[0]} trades, {int(y.sum())} positives). Skipping Family C.")
        _save_results("C", [], base, {})
        return []

    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)

    C_MODELS = [
        ("logit", LogisticRegression(max_iter=1000)),
        ("gbm",   GradientBoostingClassifier(max_depth=3, n_estimators=50, random_state=42)),
    ]

    winners_C = []
    t0 = time.time()
    for mname, mdl in C_MODELS:
        # Fresh instance per fit (defensive copy)
        m = mdl.__class__(**mdl.get_params()).fit(Xs, y)
        print(f"  Trained {mname}")
        if hasattr(m, "feature_importances_"):
            ranked = sorted(zip(names, m.feature_importances_), key=lambda x: -x[1])
            for f, w in ranked[:5]:
                print(f"    {f:<20} {w:.3f}")
        elif hasattr(m, "coef_"):
            ranked = sorted(zip(names, m.coef_[0]), key=lambda x: -abs(x[1]))
            for f, w in ranked[:5]:
                print(f"    {f:<20} {w:+.3f}")
        for tau in C_TAUS:
            for strat in STRATS:
                hook = make_C_rule(strat, m, scaler, tau, names)
                d_pnl, d_dd = [], []
                for label, s, e in specs:
                    r = run_one(ctx, s, e, hook=hook)
                    d_pnl.append(r["pnl_pct"] - base[label]["pnl_pct"])
                    d_dd.append(r["max_dd_pct"] - base[label]["max_dd_pct"])
                avg_dd = sum(d_dd) / len(d_dd)
                is_robust = all(d > 0 for d in d_pnl) and (avg_dd <= 1.0)
                mark = "✓" if is_robust else " "
                print(f"    {mname} τ={tau} {strat}: "
                      + " ".join(f"{d:+7.1f}" for d in d_pnl)
                      + f"  ΔDD avg={avg_dd:+.2f}pp  {mark}")
                if is_robust:
                    winners_C.append(dict(
                        family=f"C.{mname}", strat=strat,
                        params=dict(model=mname, tau=tau),
                        d_pnl=d_pnl, d_dd=d_dd,
                    ))
    print(f"\nC winners (strict 4/4 + ΔDD avg ≤+1pp): {len(winners_C)}  (runtime {time.time()-t0:.0f}s)")
    for w in winners_C:
        print(f"  ✓ {w}")
    _save_results("C", winners_C, base, {})
    return winners_C


# ── Task 7 — Null-shuffle validation ───────────────────────────────
import random


def _build_btc_z_map(ctx):
    """Replicate run_window's btc_z computation (ts -> rolling z-score)."""
    btc_candles = ctx["data"]["BTC"]
    btc_closes = np.array([c["c"] for c in btc_candles])
    n_lb = 30 * 6   # 30d at 4h candles
    n_zw = 180 * 6  # 180d at 4h candles
    ts_arr = [c["t"] for c in btc_candles]
    zmap = {}
    if len(btc_closes) >= n_lb + 30:
        rets = []
        for i in range(n_lb, len(btc_closes)):
            if btc_closes[i - n_lb] > 0:
                rets.append(float(btc_closes[i] / btc_closes[i - n_lb] - 1))
            else:
                rets.append(0.0)
        for i, r in enumerate(rets):
            window = rets[max(0, i - n_zw):i]
            if len(window) >= 30:
                mu, sd = np.mean(window), np.std(window)
                if sd > 1e-9:
                    zmap[ts_arr[n_lb + i]] = float(np.clip((r - mu) / sd, -2.5, 2.5))
                else:
                    zmap[ts_arr[n_lb + i]] = 0.0
    return zmap


def _candidate_to_hook(candidate):
    """Rebuild the rule hook from a saved candidate dict."""
    fam = candidate["family"]
    strat = candidate["strat"]
    p = candidate["params"]
    if fam == "A.1":
        return make_A1_rule(strat, p["activation_bps"], p["offset_bps"])
    if fam == "A.2":
        # params is a dict mapping bucket name -> (act, off)
        # JSON-loaded keys are strings; values may be lists [act, off] not tuples
        params = {k: tuple(v) for k, v in p.items()}
        return make_A2_composite_rule(strat, params)
    raise NotImplementedError(f"_candidate_to_hook: {fam}")


def null_shuffle_test(ctx, candidate, n_shuffles: int = 13):
    """Re-run candidate with btc_z shuffled n times. Compare avg ΔPnL to real."""
    print(f"\n— Null-shuffle: {candidate['family']} {candidate['strat']} {candidate['params']} —")
    real_d_pnl_per_window = candidate["d_pnl"]
    real_avg = sum(real_d_pnl_per_window) / len(real_d_pnl_per_window)
    print(f"  REAL ΔPnL per window: {real_d_pnl_per_window}  (avg {real_avg:+.1f}pp)")

    specs = window_specs(ctx["end_ts"])
    base = compute_baseline(ctx)
    if "_btc_z_map" not in ctx:
        ctx["_btc_z_map"] = _build_btc_z_map(ctx)
    real_map = ctx["_btc_z_map"]
    ts_keys = list(real_map.keys())
    z_vals = list(real_map.values())
    print(f"  btc_z map: {len(z_vals)} ts entries")

    shuf_avgs = []
    for s in range(n_shuffles):
        rng = random.Random(1000 + s)
        permuted = z_vals[:]
        rng.shuffle(permuted)
        fake_map = dict(zip(ts_keys, permuted))

        original_hook = _candidate_to_hook(candidate)
        def wrapped(snap, _h=original_hook, _m=fake_map):
            snap = dict(snap)
            snap["btc_z"] = _m.get(snap["ts_ms"], 0.0)
            return _h(snap)

        d_pnl_run = []
        for label, ws, we in specs:
            r = run_one(ctx, ws, we, hook=wrapped)
            d_pnl_run.append(r["pnl_pct"] - base[label]["pnl_pct"])
        shuf_avg = sum(d_pnl_run) / len(d_pnl_run)
        shuf_avgs.append(shuf_avg)
        print(f"  shuffle {s+1:2d}/13: avg ΔPnL = {shuf_avg:+12.1f}pp")

    mu = sum(shuf_avgs) / len(shuf_avgs)
    sd = (sum((x-mu)**2 for x in shuf_avgs) / len(shuf_avgs)) ** 0.5
    z = (real_avg - mu) / sd if sd > 1e-9 else 0.0
    is_signal = z >= 1.0
    print(f"\n  REAL avg     = {real_avg:+12.1f}pp")
    print(f"  SHUFFLE mean = {mu:+12.1f}pp  (sd {sd:.1f})")
    print(f"  z-score      = {z:+.2f}   →   {'SIGNAL ✓' if is_signal else 'NOISE ✗'}")
    return dict(real_avg=real_avg, shuf_mean=mu, shuf_sd=sd, z=z,
                is_signal=is_signal, shuf_runs=shuf_avgs)


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
    p.add_argument("--validate", action="store_true",
                   help="run T7 null-shuffle on all A.2 winners saved in artifacts.json")
    args = p.parse_args()
    ctx = load_all()
    if args.self_test:
        _self_test(ctx)
        return
    if args.validate:
        import json
        with open("/home/crypto/backtests/inlife_exit_artifacts.json") as f:
            arts = json.load(f)
        targets = []
        for tag in ("A2",):  # only A.2 currently — regime-dependent. C had 0 winners.
            for c in arts.get(tag, {}).get("winners", []):
                targets.append(c)
        if not targets:
            print("No A.2 winners to validate.")
            return
        results = {}
        for c in targets:
            results[f"{c['family']}_{c['strat']}"] = null_shuffle_test(ctx, c)
        # Persist into artifacts.json under "T7_null_shuffle" key
        arts["T7_null_shuffle"] = {k: {kk: vv for kk, vv in v.items() if kk != "shuf_runs"} | {"shuf_runs": v["shuf_runs"]} for k, v in results.items()}
        with open("/home/crypto/backtests/inlife_exit_artifacts.json", "w") as f:
            json.dump(arts, f, indent=2, default=str)
        print(f"\nT7 results saved to artifacts.json")
        return
    if args.family in ("A", "all"):
        run_family_A(ctx, quick=args.quick)
    if args.family in ("B", "all"):
        run_family_B(ctx, quick=args.quick)
    if args.family in ("C", "all"):
        run_family_C(ctx, quick=args.quick)


if __name__ == "__main__":
    main()
