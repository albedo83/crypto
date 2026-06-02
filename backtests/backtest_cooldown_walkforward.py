"""Walk-forward strict — COOLDOWN_HOURS variants.

Tests global cooldown {0, 6, 12, 24 (baseline), 48} on 4 splits 6m non-overlapping.
Strict 4/4 PASS = ΔPnL > 0 AND ΔDD ≥ -2pp on all 4 splits vs baseline (24h).

Phase 2 (conditional): if a non-24h global value PASS or near-PASS, also test
per-strat configs (give S9/S5/S8 different cooldowns).

Usage: python3 -m backtests.backtest_cooldown_walkforward
"""
from __future__ import annotations

from datetime import datetime, timezone

import backtests.backtest_genetic as bg


SPLITS = [
    ("split_1  2024-06 → 2024-12", datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    ("split_2  2024-12 → 2025-06", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    ("split_3  2025-06 → 2025-12", datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    ("split_4  2025-12 → 2026-06", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]

START_CAP = 500.0
DD_TOL = 2.0
GLOBAL_GRID = [0, 6, 12, 24, 48]  # 24 = baseline
BASELINE_HOURS = 24


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding,
              cooldown_h=24.0, cooldown_by_strat=None):
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
        cooldown_hours=cooldown_h,
        cooldown_by_strat=cooldown_by_strat,
    )


def main():
    data, features, sector_feats, dxy, oi, funding = reload_data()

    # Baseline (24h)
    print("=" * 84)
    print(f"BASELINE  COOLDOWN_HOURS = {BASELINE_HOURS}")
    print("=" * 84)
    baseline = {}
    for name, sd, ed in SPLITS:
        r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, cooldown_h=BASELINE_HOURS)
        baseline[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                          "pnl": r["pnl"]}
        print(f"  {name}  ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  n={r['n_trades']:4d}  $={r['pnl']:+,.0f}", flush=True)

    # Sweep global grid
    print()
    print("=" * 84)
    print("PHASE 1 — GLOBAL COOLDOWN SWEEP")
    print("=" * 84)
    results = []
    for cd in GLOBAL_GRID:
        if cd == BASELINE_HOURS:
            continue  # baseline already done
        per_split = {}
        roi_pass = dd_pass = 0
        sum_dpnl = sum_ddd = 0.0
        print(f"\n[cooldown={cd}h]", flush=True)
        for name, sd, ed in SPLITS:
            r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, cooldown_h=cd)
            b = baseline[name]
            d_roi = r["pnl_pct"] - b["roi"]
            d_dd = r["max_dd_pct"] - b["dd"]
            d_pnl = r["pnl"] - b["pnl"]
            per_split[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                               "pnl": r["pnl"], "d_roi": d_roi, "d_dd": d_dd, "d_pnl": d_pnl}
            roi_ok = d_pnl > 0
            dd_ok = d_dd >= -DD_TOL
            if roi_ok: roi_pass += 1
            if dd_ok: dd_pass += 1
            sum_dpnl += d_pnl
            sum_ddd += d_dd
            tag = "✓" if (roi_ok and dd_ok) else "✗"
            print(f"   {name}  ROI={r['pnl_pct']:+8.1f}%  Δn={r['n_trades']-b['n']:+4d}  ΔPnL=${d_pnl:+9.0f}  ΔDD={d_dd:+6.2f}pp  {tag}", flush=True)
        results.append({"cooldown": cd, "per_split": per_split,
                        "roi_pass": roi_pass, "dd_pass": dd_pass,
                        "sum_dpnl": sum_dpnl, "avg_ddd": sum_ddd / 4})

    # Ranking
    print()
    print("=" * 84)
    print("PHASE 1 RANKING — sorted by (4/4 PASS first, then sum ΔPnL)")
    print("=" * 84)
    results.sort(key=lambda r: (-(r["roi_pass"] == 4 and r["dd_pass"] == 4), -r["sum_dpnl"]))
    print(f"{'Cooldown':>9} {'ROI 4/4':>9} {'DD 4/4':>9} {'sum ΔPnL':>12} {'avg ΔDD':>10}  Verdict")
    for r in results:
        strict = (r["roi_pass"] == 4 and r["dd_pass"] == 4)
        verdict = "STRICT PASS 4/4 ✓" if strict else f"FAIL ({r['roi_pass']}/4 ROI, {r['dd_pass']}/4 DD)"
        print(f"{r['cooldown']:>7}h  {r['roi_pass']:>9}/4 {r['dd_pass']:>9}/4 {r['sum_dpnl']:>+11,.0f}$ {r['avg_ddd']:>+9.2f}pp  {verdict}")

    # Phase 2 — per-strat sweep on the most promising global value
    # Trigger condition: any value with sum_dpnl > 0 OR roi_pass >= 3
    best = results[0]
    if best["sum_dpnl"] > 0 or best["roi_pass"] >= 3:
        print()
        print("=" * 84)
        print(f"PHASE 2 — PER-STRAT SWEEP (around best global = {best['cooldown']}h)")
        print("=" * 84)
        # Test: S9 reduced + others 24h, S5 reduced + others 24h, S8 reduced + others 24h, etc.
        # And per-strat with best['cooldown'] as the reduced value
        variants = []
        for short_strat in ("S1", "S5", "S8", "S9", "S10"):
            cfg = {short_strat: best["cooldown"]}
            variants.append((f"only_{short_strat}={best['cooldown']}h", cfg))
        # Also: cluster of fade strats (S5, S8, S9)
        variants.append((f"fade_S5_S8_S9={best['cooldown']}h", {"S5": best["cooldown"], "S8": best["cooldown"], "S9": best["cooldown"]}))
        variants.append((f"all_strats={best['cooldown']}h (= global)", {}))  # sanity check

        phase2 = []
        for label, cfg in variants:
            per_split = {}
            roi_pass = dd_pass = 0
            sum_dpnl = 0.0
            sum_ddd = 0.0
            print(f"\n[{label}]")
            for name, sd, ed in SPLITS:
                r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding,
                              cooldown_h=BASELINE_HOURS,  # baseline for non-overridden strats
                              cooldown_by_strat=cfg if cfg else None)
                b = baseline[name]
                d_roi = r["pnl_pct"] - b["roi"]
                d_dd = r["max_dd_pct"] - b["dd"]
                d_pnl = r["pnl"] - b["pnl"]
                per_split[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                                   "pnl": r["pnl"], "d_pnl": d_pnl, "d_dd": d_dd}
                roi_ok = d_pnl > 0
                dd_ok = d_dd >= -DD_TOL
                if roi_ok: roi_pass += 1
                if dd_ok: dd_pass += 1
                sum_dpnl += d_pnl
                sum_ddd += d_dd
                tag = "✓" if (roi_ok and dd_ok) else "✗"
                print(f"   {name}  Δn={r['n_trades']-b['n']:+4d}  ΔPnL=${d_pnl:+9.0f}  ΔDD={d_dd:+6.2f}pp  {tag}")
            phase2.append({"label": label, "per_split": per_split,
                           "roi_pass": roi_pass, "dd_pass": dd_pass,
                           "sum_dpnl": sum_dpnl, "avg_ddd": sum_ddd / 4})

        print()
        print("=" * 84)
        print("PHASE 2 RANKING")
        print("=" * 84)
        phase2.sort(key=lambda r: (-(r["roi_pass"] == 4 and r["dd_pass"] == 4), -r["sum_dpnl"]))
        print(f"{'Variant':<40} {'ROI 4/4':>9} {'DD 4/4':>9} {'sum ΔPnL':>12} {'avg ΔDD':>10}  Verdict")
        for r in phase2:
            strict = (r["roi_pass"] == 4 and r["dd_pass"] == 4)
            verdict = "STRICT PASS 4/4 ✓" if strict else f"FAIL ({r['roi_pass']}/4 ROI, {r['dd_pass']}/4 DD)"
            print(f"{r['label']:<40} {r['roi_pass']:>9}/4 {r['dd_pass']:>9}/4 {r['sum_dpnl']:>+11,.0f}$ {r['avg_ddd']:>+9.2f}pp  {verdict}")
    else:
        print("\n[PHASE 2 skipped — no global variant promising enough]")


if __name__ == "__main__":
    main()
