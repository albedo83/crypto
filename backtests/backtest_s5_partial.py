"""Walk-forward — S5 partial profit-taking (last shot at the +517bps slack).

Hypothesis (after exit-improvement sweep failed): the +517 bps slack on
S5 winners exists structurally, but trailing/extension can't capture it
because they cut winners or chase reversals. A different mechanic might:
take a FRACTION of the position off when MFE crosses a high threshold,
LOCK IN that profit at the current scan price, and let the rest ride
under normal exit rules.

This isn't quite "trailing" (we lock at current price, not MFE-offset)
and isn't "early exit" (we don't close the whole position). It's a
profit-realization mechanic that hopefully:
  - Captures part of the unrealized peak before mean-reversion eats it
  - Keeps exposure for the rare runners that continue further
  - Doesn't add new losses (only fires when already in profit)

Sweep:
  - trigger_bps  ∈ {500, 700, 900, 1200, 1500} (MFE level that fires)
  - fraction     ∈ {0.3, 0.5, 0.7}             (size to take off)
  - strategies   ∈ {S5}, {S5, S9}             (S9 also has slack pattern)

Walk-forward criterion: 4/4 windows positive ΔP&L AND avg ΔDD ≤ +0.5pp.

Usage:
    python3 -m backtests.backtest_s5_partial
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
        s5 = r["by_strat"].get("S5", {"n": 0, "pnl": 0, "wr": 0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  S5 n={s5['n']:3d} pnl=${s5['pnl']:+8.0f}")

    candidates = []
    for trigger in [500, 700, 900, 1200, 1500]:
        for fraction in [0.3, 0.5, 0.7]:
            for scope_label, scope in [("S5", {"S5"}), ("S5+S9", {"S5", "S9"})]:
                candidates.append((
                    f"PP {scope_label:5s} trig={trigger:4d} frac={fraction:.1f}",
                    {"strategies": scope, "trigger_bps": trigger, "fraction": fraction},
                ))

    print(f"\nSweep: {len(candidates)} configs × {len(WINDOWS)} windows = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, cfg in candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           partial_profit=cfg, **common)
            rs[label] = r
        results[name] = rs
        d_pnl = {lab: rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                 for lab, _ in window_specs}
        d_dd = {lab: rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        avg_dd = sum(d_dd.values()) / 4
        sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        if positives >= 3:  # only print decent ones
            print(f"  {sign} {name:35s}  Δ28m={d_pnl['28m']:+7.1f}  Δ12m={d_pnl['12m']:+6.1f}  "
                  f"Δ6m={d_pnl['6m']:+5.1f}  Δ3m={d_pnl['3m']:+5.1f}  "
                  f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")

    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp)':^100}")
    print("=" * 100)
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                 for lab, _ in window_specs]
        d_dd = [results[name][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                for lab, _ in window_specs]
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

    print(f"\nRuntime: {time.time()-t0:.0f}s  ({len(candidates)} configs tested)")


if __name__ == "__main__":
    main()
