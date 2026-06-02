"""Phase 4 — Walk-forward strict validation of regime-conditioned prop_trail.

For each strat in {S1, S5, S9}, tests:
  1. Baseline (no trail)
  2. Regime-conditioned config (top-3 offline optim picks per regime)
  3. Flat config (no regime split) as comparison

PASS strict 4/4: 4/4 splits ΔPnL > 0 AND 4/4 splits ΔDD ≥ -2pp.

Usage: python3 -m backtests.backtest_prop_trail_regime_walkforward
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

import backtests.backtest_genetic as bg


OUT_DIR = os.path.join(os.path.dirname(__file__), "output")

SPLITS = [
    ("split_1  2024-06 → 2024-12", datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    ("split_2  2024-12 → 2025-06", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    ("split_3  2025-06 → 2025-12", datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    ("split_4  2025-12 → 2026-06", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]

START_CAP = 500.0
DD_TOL = 2.0
Z_THRESHOLD = 0.5


def load_optim() -> dict:
    """Load Phase 3 optimization_results.csv and build per-(strat, dir) regime configs.

    For each strat × dir, picks regime-conditioned param set:
      - Include a regime bucket if its best config has splits_improved >= 3 (3/4 or 4/4)
      - Otherwise mark that regime as None (disabled)
    """
    path = os.path.join(OUT_DIR, "optimization_results.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Run Phase 3 first to produce {path}")
    configs: dict[tuple[str, int], dict] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["verdict"] == "insufficient_data":
                continue
            key = (row["strat"], int(row["dir"]))
            if key not in configs:
                configs[key] = {"by_regime": {}, "z_threshold": Z_THRESHOLD}
            regime = row["regime"]
            improved = int(row["splits_improved"])
            sum_dpnl = float(row["sum_dpnl"])
            if improved >= 3 and sum_dpnl > 0:
                configs[key]["by_regime"][regime] = {
                    "arm_bps": int(row["arm"]),
                    "lock_ratio": float(row["lock"]),
                }
            else:
                configs[key]["by_regime"][regime] = None
    return configs


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding, prop_cfg=None):
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
        proportional_trail=prop_cfg,
    )


def main():
    data, features, sector_feats, dxy, oi, funding = reload_data()

    # Baseline
    print("=" * 84)
    print("BASELINE (no prop_trail)")
    print("=" * 84)
    baseline = {}
    for name, sd, ed in SPLITS:
        r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, prop_cfg=None)
        baseline[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                          "pnl": r["pnl"]}
        print(f"  {name}  ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  n={r['n_trades']:4d}  $={r['pnl']:+,.0f}", flush=True)

    # Load configs from Phase 3
    configs = load_optim()
    print(f"\nLoaded {len(configs)} (strat, dir) configs from Phase 3 optimization:")
    for (strat, dir_), cfg in configs.items():
        active = {k: v for k, v in cfg["by_regime"].items() if v is not None}
        disabled = [k for k, v in cfg["by_regime"].items() if v is None]
        print(f"  {strat} dir={dir_:+d}: active={active} disabled={disabled}")

    # Test each (strat, dir) — but apply only one strat at a time since
    # proportional_trail is per-strategy. The bot's check_exits picks strat-specific.
    # For each (strat, dir), apply trail to that strat (any dir) and see impact.
    # Note: proportional_trail in run_window doesn't filter by direction directly —
    # the strategy filter alone determines eligibility. The regime config defines
    # what arm/lock to use; the regime is dynamic per tick.
    # Therefore here we test PER STRAT (collapsing both dirs into one config).
    # Build per-strat config by union of per-direction configs (taking the more
    # active one per regime).

    by_strat: dict[str, dict] = {}
    for (strat, dir_), cfg in configs.items():
        if strat not in by_strat:
            by_strat[strat] = {"strategy": strat, "by_regime": {}, "z_threshold": Z_THRESHOLD}
        for regime, regime_cfg in cfg["by_regime"].items():
            existing = by_strat[strat]["by_regime"].get(regime)
            # If either dir has a config, use it (prefer non-None)
            if regime_cfg is not None and existing is None:
                by_strat[strat]["by_regime"][regime] = regime_cfg
            elif regime_cfg is not None and existing is not None:
                # Both dirs have configs. Use the one with smaller arm (more aggressive).
                if regime_cfg["arm_bps"] < existing["arm_bps"]:
                    by_strat[strat]["by_regime"][regime] = regime_cfg

    # Fill missing regimes with None
    for cfg in by_strat.values():
        for regime in ("bear", "neutral", "bull"):
            cfg["by_regime"].setdefault(regime, None)

    print()
    print("=" * 84)
    print("STRAT-LEVEL CONFIGS (collapsed across directions)")
    print("=" * 84)
    for strat, cfg in by_strat.items():
        print(f"  {strat}: {cfg['by_regime']}")

    # Walk-forward each strat config
    print()
    print("=" * 84)
    print("WALK-FORWARD per strat")
    print("=" * 84)
    results = []
    for strat, cfg in by_strat.items():
        active_regimes = sum(1 for v in cfg["by_regime"].values() if v is not None)
        if active_regimes == 0:
            print(f"\n[{strat}] All regimes disabled — skip")
            continue
        print(f"\n[{strat}] active in {active_regimes}/3 regimes", flush=True)
        per_split = {}
        roi_pass = dd_pass = 0
        sum_dpnl = sum_ddd = 0.0
        for name, sd, ed in SPLITS:
            r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, prop_cfg=cfg)
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
            print(f"   {name}  ROI={r['pnl_pct']:+8.1f}%  ΔPnL=${d_pnl:+9.0f}  ΔDD={d_dd:+6.2f}pp  {tag}", flush=True)
        results.append({"strat": strat, "cfg": cfg, "roi_pass": roi_pass, "dd_pass": dd_pass,
                        "sum_dpnl": sum_dpnl, "avg_ddd": sum_ddd / 4, "per_split": per_split})

    # Verdict
    print()
    print("=" * 84)
    print("WALK-FORWARD VERDICT (strict 4/4 = ΔPnL > 0 AND ΔDD ≥ -2pp on ALL 4 splits)")
    print("=" * 84)
    print(f"{'Strat':>6} {'ROI 4/4':>9} {'DD 4/4':>9} {'sum ΔPnL':>11} {'avg ΔDD':>10}  Verdict")
    for r in results:
        strict = (r["roi_pass"] == 4 and r["dd_pass"] == 4)
        verdict = "STRICT PASS 4/4 ✓" if strict else f"FAIL ({r['roi_pass']}/4 ROI, {r['dd_pass']}/4 DD)"
        print(f"{r['strat']:>6} {r['roi_pass']:>9}/4 {r['dd_pass']:>9}/4 {r['sum_dpnl']:>+10,.0f}$ {r['avg_ddd']:>+9.2f}pp  {verdict}")


if __name__ == "__main__":
    main()
