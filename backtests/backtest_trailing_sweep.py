"""Walk-forward sweep — generalized trailing stop on S5 / S8 / S9.

S10 already has a production trailing stop (S10_TRAILING_TRIGGER /
S10_TRAILING_OFFSET) validated walk-forward in v11.4.0. This script
tests whether the same idea — exit at MFE − offset once MFE crosses an
activation threshold — produces robust improvements when applied to S5,
S8 and S9 individually.

Walk-forward criterion: a (strategy, trigger, offset) candidate is
considered robust only if it improves P&L% on ALL 4 reference windows
(28m / 12m / 6m / 3m). Single-window wins are noise.

Output: one comparison table per strategy + a summary list of robust
combos (4/4 winners). Nothing is committed to bot config — research only.

Usage:
    python3 -m backtests.backtest_trailing_sweep
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0  # single-capital sweep; deltas are P&L% so capital-invariant

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]

# Parameter grid per strategy. Wide enough to find a sweet spot, tight
# enough to keep the total runtime under ~15 min on the VPS.
GRID = {
    "S5": {"triggers": [300, 400, 500, 600, 800], "offsets": [100, 150, 200]},
    "S8": {"triggers": [300, 400, 500, 600, 800], "offsets": [100, 150, 200]},
    "S9": {"triggers": [300, 400, 500, 600, 800], "offsets": [100, 150, 200]},
}


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.isoformat()}")

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    window_specs = []
    for label, months in WINDOWS:
        start_dt = end_dt - relativedelta(months=months)
        window_specs.append((label, int(start_dt.timestamp() * 1000)))
    end_ts = latest_ts

    common_kwargs = dict(
        sector_features=sector_features,
        dxy_data=dxy_data,
        end_ts_ms=end_ts,
        start_capital=CAP,
        oi_data=oi_data,
        early_exit_params=early_exit,
        funding_data=funding_data,
    )

    # ── Baseline (no extra trailing) ─────────────────────────────────
    print("\nBaseline (no extra trailing) per window:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common_kwargs)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+7.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  best={r['best_strat']}")

    # ── Sweep ────────────────────────────────────────────────────────
    n_combos = sum(len(g["triggers"]) * len(g["offsets"]) for g in GRID.values())
    n_total = n_combos * len(WINDOWS)
    print(f"\nSweep: {n_combos} combos × {len(WINDOWS)} windows = {n_total} run_window calls")
    print(f"(plus {len(WINDOWS)} baseline already done)")

    results: dict[tuple[str, int, int], dict[str, dict]] = {}
    t0 = time.time()
    n_done = 0
    for strat, grid in GRID.items():
        for trig in grid["triggers"]:
            for off in grid["offsets"]:
                key = (strat, trig, off)
                results[key] = {}
                for label, start_ts in window_specs:
                    r = run_window(
                        features, data, start_ts_ms=start_ts,
                        trailing_extra={"strategy": strat, "trigger_bps": trig, "offset_bps": off},
                        **common_kwargs,
                    )
                    results[key][label] = r
                    n_done += 1
                elapsed = time.time() - t0
                eta = elapsed / max(1, n_done) * (n_total - n_done)
                d = {lab: results[key][lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                     for lab, _ in window_specs}
                positives = sum(1 for v in d.values() if v > 0)
                print(f"  [{n_done:3d}/{n_total}] {strat} +{trig:4d} -{off:3d}  "
                      f"Δ28m={d['28m']:+6.1f} Δ12m={d['12m']:+6.1f} "
                      f"Δ6m={d['6m']:+6.1f} Δ3m={d['3m']:+6.1f}  "
                      f"{positives}/4  eta {eta:.0f}s")

    # ── Per-strategy comparison tables ───────────────────────────────
    print(f"\n{'=' * 100}")
    print(f"{'PER-STRATEGY DELTAS (vs baseline)':^100}")
    print(f"{'=' * 100}")
    for strat, grid in GRID.items():
        print(f"\n=== {strat} ===")
        print(f"  trig   off      Δ28m       Δ12m        Δ6m        Δ3m   |  ΔDD avg | verdict")
        for trig in grid["triggers"]:
            for off in grid["offsets"]:
                key = (strat, trig, off)
                d_pnl = {lab: results[key][lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                         for lab, _ in window_specs}
                d_dd = {lab: results[key][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                        for lab, _ in window_specs}
                positives = sum(1 for v in d_pnl.values() if v > 0)
                avg_pnl = sum(d_pnl.values()) / len(d_pnl)
                avg_dd = sum(d_dd.values()) / len(d_dd)
                tag = "4/4 ✓" if positives == 4 else f"{positives}/4"
                if positives == 4 and avg_dd <= 0.5:  # extra: DD shouldn't worsen
                    tag = "4/4 ★"
                print(f"  +{trig:4d}  -{off:3d}   {d_pnl['28m']:+8.1f}  {d_pnl['12m']:+8.1f}  "
                      f"{d_pnl['6m']:+8.1f}  {d_pnl['3m']:+8.1f}  | {avg_dd:+7.1f}  | {tag} (avg ΔPnL {avg_pnl:+.1f})")

    # ── Robust combos summary ───────────────────────────────────────
    print(f"\n{'=' * 100}")
    print(f"{'ROBUST COMBOS (4/4 windows positive ΔP&L)':^100}")
    print(f"{'=' * 100}")
    found = []
    for key, ws in results.items():
        d_pnl = [ws[lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [ws[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
        if all(d > 0 for d in d_pnl):
            found.append((key, d_pnl, d_dd))
    if not found:
        print("  (none — sweep grid did not find a 4/4 winner)")
    else:
        # Rank by sum of ΔP&L%
        found.sort(key=lambda x: -sum(x[1]))
        for (strat, trig, off), d_pnl, d_dd in found:
            print(f"  {strat} +{trig:4d} -{off:3d}: ΔPnL avg {sum(d_pnl)/4:+6.1f}pp  "
                  f"ΔDD avg {sum(d_dd)/4:+5.1f}pp  "
                  f"(28m {d_pnl[0]:+5.1f}, 12m {d_pnl[1]:+5.1f}, 6m {d_pnl[2]:+5.1f}, 3m {d_pnl[3]:+5.1f})")
    elapsed = time.time() - t0
    print(f"\nTotal sweep runtime: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
