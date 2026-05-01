"""Walk-forward sweep — BTC-correlation exit (in-engine, v2).

Second iteration after `backtest_btc_corr_exit.py` (first-order post-hoc
beta=1 estimate that surfaced 5 4/4 candidates). This version uses the
new `btc_corr_exit` parameter on run_window for a proper engine
simulation that:
  - exits at the alt's actual mark price (not BTC-derived proxy)
  - frees the slot for new entries
  - tracks path-dependent compounding

Hypothesis: cut a position when BTC moves >= threshold_bps against the
trade direction within lookback_h hours of entry.

Usage:
    python3 -m backtests.backtest_btc_corr_exit_v2
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

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


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

    common = dict(
        sector_features=sector_features,
        dxy_data=dxy_data,
        end_ts_ms=end_ts,
        start_capital=CAP,
        oi_data=oi_data,
        early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline (with v11.7.28 dispersion gate active, no BTC-corr exit):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # Grid focused on combinations that surfaced as 4/4 in the first-order test
    candidates: list[tuple[str, dict]] = []
    for thr in [300, 500, 800]:
        for lb in [None, 24, 12, 8]:
            for scope_name, do_long, do_short in [
                ("LONG", True, False),
                ("SHORT", False, True),
                ("LONG+SHORT", True, True),
            ]:
                lb_str = "full" if lb is None else f"{lb}h"
                name = f"thr={thr} lb={lb_str} scope={scope_name}"
                cfg = {
                    "threshold_bps": thr,
                    "lookback_h": lb,
                    "apply_long": do_long,
                    "apply_short": do_short,
                }
                candidates.append((name, cfg))

    print(f"\nSweep: {len(candidates)} combos × {len(WINDOWS)} windows = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, cfg in candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           btc_corr_exit=cfg, **common)
            rs[label] = r
        results[name] = rs
        d = {lab: rs[lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs}
        d_dd = {lab: rs[lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        avg_dd = sum(d_dd.values())/4
        n_trades_28m = rs["28m"]["n_trades"]
        print(f"  {name:40s}  Δ28m={d['28m']:+8.1f}  Δ12m={d['12m']:+7.1f}  "
              f"Δ6m={d['6m']:+6.1f}  Δ3m={d['3m']:+5.1f}  ΔDD={avg_dd:+5.1f}  "
              f"trades={n_trades_28m}  {positives}/4")

    print(f"\n{'=' * 100}\n{'Robust 4/4 + DD intact':^100}\n{'=' * 100}")
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][lab]['pnl_pct'] - baseline[lab]['pnl_pct'] for lab, _ in window_specs]
        d_dd = [results[name][lab]['max_dd_pct'] - baseline[lab]['max_dd_pct'] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd)/4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none with DD intact — first-order optimism didn't hold)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}: avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.1f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
