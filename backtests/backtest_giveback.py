"""Walk-forward — "give-back" exit (was green, now red).

Hypothesis (from user observation 2026-05-07): DOGE LONG hit MFE +168 bps,
reverted, now at -342 bps with 10h to timeout. The intuition: when a
position WAS profitable (MFE ≥ X) and is now NEGATIVE (current ≤ Y), it
likely won't recover — give up.

This mechanic is DISTINCT from the trailing stops already tested:
  - Trailing fires continuously at `current ≤ MFE - offset`
  - Give-back fires ONLY when current crosses below `max_current_bps`
    AFTER MFE crossed above `min_mfe_bps`

Asymmetric: protects profit-given-back trades without touching winners
that stay green or trades that never showed any green (those use the
existing dead_timeout / catastrophe_stop).

Sweep:
  - min_mfe ∈ {50, 100, 150, 200, 300, 500}
  - max_current ∈ {0, -50, -100, -200, -300}
  - strategies ∈ {S5}, {S5,S9}, {S5,S9,S10}

Walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_giveback
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
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

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]
    end_ts = latest_ts
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=CAP, oi_data=oi_data, early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%")

    candidates = []
    for min_mfe in [50, 100, 150, 200, 300, 500]:
        for max_cur in [0, -50, -100, -200, -300]:
            for scope_label, scope in [("S5", {"S5"}),
                                        ("S5+S9", {"S5", "S9"}),
                                        ("S5+S9+S10", {"S5", "S9", "S10"})]:
                if max_cur >= -min_mfe:  # require asymmetry
                    candidates.append((
                        f"GB {scope_label:9s} mfe≥{min_mfe:4d} cur≤{max_cur:+5d}",
                        {"strategies": scope, "min_mfe_bps": min_mfe, "max_current_bps": max_cur},
                    ))

    print(f"\nSweep: {len(candidates)} configs × {len(WINDOWS)} = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, cfg in candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           giveback=cfg, **common)
            rs[label] = r
        results[name] = rs
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        avg_dd = sum(d_dd.values()) / 4
        sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        if positives >= 3:
            print(f"  {sign} {name:42s}  Δ28m={d_pnl['28m']:+8.1f}  Δ12m={d_pnl['12m']:+7.1f}  "
                  f"Δ6m={d_pnl['6m']:+6.1f}  Δ3m={d_pnl['3m']:+5.1f}  "
                  f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")

    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^100}")
    print("=" * 100)
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs]
        d_dd = [results[name][l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(candidates)} configs)")


if __name__ == "__main__":
    main()
