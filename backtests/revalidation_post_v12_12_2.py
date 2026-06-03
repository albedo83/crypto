"""Re-validation walk-forward strict 4/4 post v12.12.2 candle-sync fixes.

For each feature shipped previously, compare:
- BASELINE: feature disabled (via in-place mutation of config dict/set, or
  monkey-patch of backtest_rolling's float bindings)
- TESTED: feature with current production config

Strict 4/4 PASS = 4/4 splits ΔPnL > 0 AND 4/4 ΔDD ≥ -2pp (DD tolerance)

Features re-validated:
1. S9 prop_trail v12.11.0  (PROP_TRAIL_PARAMS, S9 bull-only)
2. S5 SHORT modulator v12.2.0  (ADAPTIVE_ALPHA_DIR)
3. Trajectory cut S5 v12.7.1  (TRAJ_CUT_STRATEGIES bear regime)
4. S8 in-life trail v12.5.30  (S8_INLIFE_PARAMS regime-aware)
5. S8 dead-in-water v12.6.0  (S8_DEAD_MFE_MAX_BPS)

Usage: python3 -m backtests.revalidation_post_v12_12_2
"""
from __future__ import annotations
import sys
import time
from datetime import datetime, timezone

import backtests.backtest_genetic as bg
import backtests.backtest_rolling as brm
import analysis.bot.config as bc


SPLITS = [
    ("split_1  2024-06 → 2024-12", datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    ("split_2  2024-12 → 2025-06", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    ("split_3  2025-06 → 2025-12", datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    ("split_4  2025-12 → 2026-06", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]

CAPITAL = 500.0
DD_TOL = 2.0


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run_split(start_dt, end_dt, ctx, prop_cfg=None):
    data, features, sector_feats, dxy, oi, funding = ctx
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=CAPITAL,
        oi_data=oi, funding_data=funding,
        apply_adaptive_modulator=True,
        proportional_trail=prop_cfg,
    )


def compare(name, baseline, tested):
    print(f"\n  {'Split':<32} {'Base $':>10} {'Tested $':>10} {'ΔPnL':>10} {'Base DD':>9} {'New DD':>9} {'ΔDD':>9} Status")
    roi_pass = dd_pass = 0
    sum_dpnl = 0.0
    sum_ddd = 0.0
    for split_name in baseline:
        b = baseline[split_name]
        t = tested[split_name]
        d_pnl = t["pnl"] - b["pnl"]
        d_dd = t["dd"] - b["dd"]
        sum_dpnl += d_pnl
        sum_ddd += d_dd
        roi_ok = d_pnl > 0
        dd_ok = d_dd >= -DD_TOL
        if roi_ok: roi_pass += 1
        if dd_ok: dd_pass += 1
        tag = "✓" if (roi_ok and dd_ok) else "✗"
        print(f"  {split_name:<32} {b['pnl']:>+9.0f}$ {t['pnl']:>+9.0f}$ {d_pnl:>+9.0f}$ {b['dd']:>+8.2f}pp {t['dd']:>+8.2f}pp {d_dd:>+8.2f}pp  {tag}")

    strict = (roi_pass == 4 and dd_pass == 4)
    verdict = "STRICT PASS 4/4 ✓" if strict else f"FAIL ({roi_pass}/4 ROI, {dd_pass}/4 DD)"
    print(f"  TOTAL: sum_ΔPnL=${sum_dpnl:+.0f}, avg_ΔDD={sum_ddd/4:+.2f}pp  →  {verdict}")
    return strict, sum_dpnl, sum_ddd / 4


def run_baseline_then_tested(label, disable_fn, restore_fn, ctx, prop_cfg_for_test=None):
    print(f"\n{'='*84}\n{label}\n{'='*84}")
    print("[Baseline: feature DISABLED]", flush=True)
    disable_fn()
    baseline = {}
    t0 = time.time()
    for split_name, sd, ed in SPLITS:
        r = run_split(sd, ed, ctx)
        baseline[split_name] = {"pnl": r["pnl"], "dd": r["max_dd_pct"], "n": r["n_trades"]}
        print(f"   {split_name}  ROI={r['pnl_pct']:+8.1f}%  $={r['pnl']:+8.0f}  DD={r['max_dd_pct']:+5.1f}%  n={r['n_trades']}", flush=True)

    print("[Tested: feature ENABLED]", flush=True)
    restore_fn()
    tested = {}
    for split_name, sd, ed in SPLITS:
        r = run_split(sd, ed, ctx, prop_cfg=prop_cfg_for_test)
        tested[split_name] = {"pnl": r["pnl"], "dd": r["max_dd_pct"], "n": r["n_trades"]}
        print(f"   {split_name}  ROI={r['pnl_pct']:+8.1f}%  $={r['pnl']:+8.0f}  DD={r['max_dd_pct']:+5.1f}%  n={r['n_trades']}", flush=True)

    print(f"  ({time.time()-t0:.0f}s elapsed)", flush=True)
    return compare(label, baseline, tested)


def main():
    print("Loading data...", flush=True)
    t0 = time.time()
    ctx = reload_data()
    print(f"Data loaded in {time.time()-t0:.0f}s\n", flush=True)

    results = {}

    # ─── 1. S9 prop_trail v12.11.0 ───
    # Baseline = pass no prop_trail config. Tested = pass production config.
    results["S9 prop_trail v12.11.0"] = run_baseline_then_tested(
        "1. S9 prop_trail v12.11.0 (bull-only, arm=100 lock=0.65)",
        disable_fn=lambda: None,  # no-op: no prop_trail passed
        restore_fn=lambda: None,
        ctx=ctx,
        prop_cfg_for_test={
            "strategy": "S9",
            "by_regime": {
                "bear": None,
                "neutral": None,
                "bull": {"arm_bps": 100, "lock_ratio": 0.65},
            },
            "z_threshold": 0.5,
        },
    )

    # ─── 2. S5 SHORT modulator v12.2.0 ───
    # ADAPTIVE_ALPHA_DIR = {("S5", -1): -0.5}. Disable = clear dict.
    saved_adir = dict(bc.ADAPTIVE_ALPHA_DIR)
    results["S5 SHORT modulator v12.2.0"] = run_baseline_then_tested(
        "2. S5 SHORT modulator v12.2.0 (alpha=-0.5)",
        disable_fn=lambda: bc.ADAPTIVE_ALPHA_DIR.clear(),
        restore_fn=lambda: bc.ADAPTIVE_ALPHA_DIR.update(saved_adir),
        ctx=ctx,
    )

    # ─── 3. Trajectory cut S5 v12.7.1 ───
    saved_traj = set(bc.TRAJ_CUT_STRATEGIES)
    results["traj_cut S5 v12.7.1"] = run_baseline_then_tested(
        "3. traj_cut S5 v12.7.1 (bear regime mid-trade exit)",
        disable_fn=lambda: bc.TRAJ_CUT_STRATEGIES.clear(),
        restore_fn=lambda: bc.TRAJ_CUT_STRATEGIES.update(saved_traj),
        ctx=ctx,
    )

    # ─── 4. S8 in-life trail v12.5.30 ───
    saved_s8inlife = dict(bc.S8_INLIFE_PARAMS)
    results["S8 in-life trail v12.5.30"] = run_baseline_then_tested(
        "4. S8 in-life trail v12.5.30 (regime-aware MFE trail)",
        disable_fn=lambda: bc.S8_INLIFE_PARAMS.clear(),
        restore_fn=lambda: bc.S8_INLIFE_PARAMS.update(saved_s8inlife),
        ctx=ctx,
    )

    # ─── 5. S8 dead-in-water v12.6.0 ───
    # S8_DEAD_MFE_MAX_BPS is a float, imported by value into backtest_rolling.
    # Monkey-patch backtest_rolling's binding.
    saved_s8dead = brm.S8_DEAD_MFE_MAX_BPS
    def disable_s8dead():
        brm.S8_DEAD_MFE_MAX_BPS = -99999.0
    def restore_s8dead():
        brm.S8_DEAD_MFE_MAX_BPS = saved_s8dead
    results["S8 dead-in-water v12.6.0"] = run_baseline_then_tested(
        "5. S8 dead-in-water v12.6.0 (T+8h, MFE_cap=50bps)",
        disable_fn=disable_s8dead,
        restore_fn=restore_s8dead,
        ctx=ctx,
    )

    # ─── Bilan ───
    print()
    print("=" * 84)
    print("BILAN GLOBAL — re-validation post v12.12.2 candle-sync fixes")
    print("=" * 84)
    print(f"{'Feature':<45} {'Verdict':<25} {'sum ΔPnL':>12} {'avg ΔDD':>10}")
    print("-" * 96)
    pass_count = fail_count = 0
    for name, (strict, sum_dpnl, avg_ddd) in results.items():
        status = "STRICT PASS 4/4 ✓" if strict else "FAIL ✗"
        if strict: pass_count += 1
        else: fail_count += 1
        print(f"  {name:<43} {status:<25} {sum_dpnl:>+11,.0f}$ {avg_ddd:>+9.2f}pp")
    print()
    print(f"  Total : {pass_count} PASS / {fail_count} FAIL sur {len(results)} features re-validées")


if __name__ == "__main__":
    main()
