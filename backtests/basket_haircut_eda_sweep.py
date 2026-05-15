"""basket_haircut_eda — haircut sweep on the 7d window.

Tests the proposal: `size *= max(MIN_HAIRCUT, min(1.0, eff_n / EFFN_REF))`
as a multiplicative haircut on top of the adaptive modulator.

Walk-forward 4 windows (28m / 12m / 6m / 3m).

Acceptance criteria (haircut wins if EITHER):
  A. ΔPnL ≥ -5% on every window AND ΔDD ≥ +5pp on every window
  B. ΔCalmar ≥ +15% on every window

We additionally report:
  - Baseline: current bot config (no haircut)
  - The 7d window was selected as best risk-side signal but it's a weak
    signal; 14d and 30d are also tested for completeness.

Reads:  current `backtest_rolling.run_window` baseline (computed in-process)
Writes: backtests/basket_haircut_eda_data/sweep_results.json
"""
from __future__ import annotations
import os
import sys
import json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

sys.path.insert(0, "/home/crypto")
from backtests.backtest_rolling import (
    run_window, load_3y_candles, build_features,
    compute_sector_features, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS, RUNNER_EXT_MIN_MFE_BPS,
    RUNNER_EXT_MIN_CUR_TO_MFE,
)


OUTDIR = "/home/crypto/backtests/basket_haircut_eda_data"
WINDOW_LABELS = [
    ("28m", 28),
    ("12m", 12),
    ("6m",  6),
    ("3m",  3),
]
HAIRCUT_WINDOW_DAYS = (7, 14, 30)
EFFN_REF_GRID = (3.0, 4.0, 6.0)
MIN_HAIRCUT_GRID = (0.25, 0.40, 0.50)


def make_haircut_fn(window_d: int, effn_ref: float, min_haircut: float):
    """Returns a basket_haircut_fn closure.

    Multiplier = max(min_haircut, min(1.0, eff_n / effn_ref)). When the basket
    has < 2 positions OR eff_n is None for the chosen window, multiplier = 1.0
    (no haircut). This is the "fully diversified" cap — we never UP-size, only
    haircut when the basket is concentrated.
    """
    def fn(cand, effn_dict, n_positions):
        if n_positions < 2:
            return 1.0
        v = effn_dict.get(window_d)
        if v is None:
            return 1.0
        return max(min_haircut, min(1.0, v / effn_ref))
    return fn


def calmar(pnl: float, dd_pct: float) -> float:
    """Calmar-like: PnL / |max_DD_pct|. dd_pct is negative; we use abs."""
    if dd_pct == 0:
        return float("inf") if pnl > 0 else 0.0
    return pnl / abs(dd_pct)


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()
    print(f"  {len(data)} coins")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    common_kwargs = dict(
        oi_data=oi_data, early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=True,
    )

    def one_run(label, months, haircut_fn=None):
        start_dt = end_dt - relativedelta(months=months)
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window(features, data, sector_features, dxy_data,
                       start_ts, latest_ts, start_capital=1000.0,
                       basket_haircut_fn=haircut_fn,
                       **common_kwargs)
        return {
            "n_trades": r["n_trades"],
            "pnl": float(r["pnl"]),
            "pnl_pct": float(r["pnl_pct"]),
            "dd_pct": float(r["max_dd_pct"]),
            "calmar": calmar(r["pnl"], r["max_dd_pct"]),
            "wr": float(r["win_rate"]),
        }

    # ── Baselines (no haircut) ──
    print("\n=== Baselines (no haircut, current config) ===")
    baselines = {}
    for label, months in WINDOW_LABELS:
        r = one_run(label, months, haircut_fn=None)
        baselines[label] = r
        print(f"  {label}: pnl={r['pnl']:+.0f} pnl%={r['pnl_pct']:+.1f}% "
              f"dd={r['dd_pct']:.1f}% calmar={r['calmar']:.1f} "
              f"trades={r['n_trades']}")

    # ── Sweep ──
    results = []
    n_configs = len(HAIRCUT_WINDOW_DAYS) * len(EFFN_REF_GRID) * len(MIN_HAIRCUT_GRID)
    print(f"\n=== Sweep: {n_configs} configs × 4 windows = {n_configs*4} runs ===")
    cfg_i = 0
    for wd in HAIRCUT_WINDOW_DAYS:
        for ref in EFFN_REF_GRID:
            for mh in MIN_HAIRCUT_GRID:
                cfg_i += 1
                print(f"\n[{cfg_i}/{n_configs}] window={wd}d REF={ref} MIN={mh}")
                fn = make_haircut_fn(wd, ref, mh)
                cfg = {"window_d": wd, "effn_ref": ref, "min_haircut": mh,
                       "windows": {}}
                for label, months in WINDOW_LABELS:
                    r = one_run(label, months, haircut_fn=fn)
                    b = baselines[label]
                    delta_pnl_pct = (r["pnl"] - b["pnl"]) / abs(b["pnl"]) * 100 \
                        if b["pnl"] != 0 else 0
                    delta_dd_pp = r["dd_pct"] - b["dd_pct"]  # DD is negative; +pp = better (less negative)
                    delta_calmar_pct = (r["calmar"] - b["calmar"]) / abs(b["calmar"]) * 100 \
                        if b["calmar"] != 0 else 0
                    cfg["windows"][label] = {
                        "pnl": r["pnl"], "pnl_pct": r["pnl_pct"],
                        "dd_pct": r["dd_pct"], "calmar": r["calmar"],
                        "n_trades": r["n_trades"],
                        "delta_pnl_pct_vs_baseline": delta_pnl_pct,
                        "delta_dd_pp": delta_dd_pp,
                        "delta_calmar_pct": delta_calmar_pct,
                    }
                    print(f"  {label}: pnl={r['pnl']:+.0f} ({delta_pnl_pct:+.1f}% vs bl) "
                          f"dd={r['dd_pct']:.1f}% ({delta_dd_pp:+.1f}pp) "
                          f"calmar={r['calmar']:.1f} ({delta_calmar_pct:+.1f}%)")
                # Acceptance criteria
                wins_A = all(
                    cfg["windows"][lbl]["delta_pnl_pct_vs_baseline"] >= -5 and
                    cfg["windows"][lbl]["delta_dd_pp"] >= 5
                    for lbl, _ in WINDOW_LABELS
                )
                wins_B = all(
                    cfg["windows"][lbl]["delta_calmar_pct"] >= 15
                    for lbl, _ in WINDOW_LABELS
                )
                cfg["passes_A"] = wins_A
                cfg["passes_B"] = wins_B
                if wins_A:
                    print(f"  → PASSES criterion A (PnL ≥-5% AND DD ≥+5pp on all 4)")
                if wins_B:
                    print(f"  → PASSES criterion B (Calmar ≥+15% on all 4)")
                results.append(cfg)

    out = {
        "baselines": baselines,
        "configs": results,
        "criteria": {
            "A": "ΔPnL ≥ -5% AND ΔDD ≥ +5pp on all 4 windows",
            "B": "ΔCalmar ≥ +15% on all 4 windows",
        },
    }
    out_path = f"{OUTDIR}/sweep_results.json"
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=2, default=float)
    print(f"\nWritten: {out_path}")

    # Summary
    print("\n=== SUMMARY ===")
    passers_A = [c for c in results if c["passes_A"]]
    passers_B = [c for c in results if c["passes_B"]]
    print(f"Criterion A passers: {len(passers_A)} / {len(results)}")
    for c in passers_A:
        print(f"  window={c['window_d']}d REF={c['effn_ref']} MIN={c['min_haircut']}")
    print(f"Criterion B passers: {len(passers_B)} / {len(results)}")
    for c in passers_B:
        print(f"  window={c['window_d']}d REF={c['effn_ref']} MIN={c['min_haircut']}")

    # If none pass, report best Calmar config
    if not passers_A and not passers_B:
        # Rank by aggregate calmar delta
        def agg(c):
            return sum(c["windows"][lbl]["delta_calmar_pct"] for lbl, _ in WINDOW_LABELS) / 4
        results_sorted = sorted(results, key=agg, reverse=True)
        print("\nBest 3 configs by average Calmar delta (no winner):")
        for c in results_sorted[:3]:
            avg = agg(c)
            print(f"  window={c['window_d']}d REF={c['effn_ref']} MIN={c['min_haircut']} "
                  f"avg ΔCalmar={avg:+.1f}%")
            for lbl, _ in WINDOW_LABELS:
                w = c["windows"][lbl]
                print(f"    {lbl}: ΔPnL={w['delta_pnl_pct_vs_baseline']:+.1f}% "
                      f"ΔDD={w['delta_dd_pp']:+.1f}pp ΔCalmar={w['delta_calmar_pct']:+.1f}%")


if __name__ == "__main__":
    main()
