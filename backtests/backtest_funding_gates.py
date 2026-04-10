"""Validate funding/premium gates discovered in backtest_funding_explore.py.

Applies four candidate gates simultaneously and compares baseline vs gated
on multiple rolling windows. Walk-forward sanity check: a gate is only
trustworthy if it improves all windows, not just the in-sample 28m one.

Gates (skip the trade if true):
    - S1: funding_1h > 1.0 bps/h          (high funding = late long)
    - S5: funding_1h > 0.125 bps/h        (long already loaded)
    - S8: funding_1h < -0.165 bps/h       (capitulation already done)
    - S9: premium_1h > +12 bps            (market too tense for fade)

Usage:
    python3 -m backtests.backtest_funding_gates
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window, rolling_windows

FUNDING_DB = os.path.join(os.path.dirname(__file__), "output", "funding_history.db")
HOUR_MS = 3600 * 1000

# Gates: skip a candidate trade if the condition is true.
# Values come from backtest_funding_explore.py quartile analysis.
GATES = {
    "S1": ("funding_1h_bps_gt", 1.0),    # bps per hour
    "S5": ("funding_1h_bps_gt", 0.125),
    "S8": ("funding_1h_bps_lt", -0.165),
    "S9": ("premium_1h_bps_gt", 12.0),
}


def load_funding_lookup() -> dict[tuple[str, int], tuple[float, float]]:
    """Flat (sym, hour_ts) → (funding_bps_per_hour, premium_bps)."""
    db = sqlite3.connect(FUNDING_DB)
    out = {}
    for sym, ts, fr, pr in db.execute("SELECT symbol, ts, funding_rate, premium FROM funding"):
        # Convert: fundingRate is per-hour fraction → bps/h. premium is fraction → bps.
        out[(sym, (ts // HOUR_MS) * HOUR_MS)] = (float(fr) * 1e4, float(pr) * 1e4)
    return out


def make_skip_fn(funding_lookup):
    def skip(sym: str, ts: int, strat: str, direction: int) -> bool:
        gate = GATES.get(strat)
        if gate is None:
            return False
        kind, threshold = gate
        bucket = (ts // HOUR_MS) * HOUR_MS
        v = funding_lookup.get((sym, bucket))
        if v is None:
            return False
        funding_bps, premium_bps = v
        if kind == "funding_1h_bps_gt":
            return funding_bps > threshold
        if kind == "funding_1h_bps_lt":
            return funding_bps < threshold
        if kind == "premium_1h_bps_gt":
            return premium_bps > threshold
        return False
    return skip


def fmt_dollar(v: float) -> str:
    return f"${v:>7,.0f}".replace(",", " ")


def main() -> int:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    print(f"  {len(data)} coins, {sum(len(f) for f in features.values())} feature points")

    print("Loading funding history...")
    funding_lookup = load_funding_lookup()
    print(f"  {len(funding_lookup)} (symbol, hour) entries")
    skip_fn = make_skip_fn(funding_lookup)

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"\nData ends at {end_dt.isoformat()}")
    print(f"\nGates applied:")
    for s, (k, v) in GATES.items():
        print(f"  {s}: skip if {k} {v}")

    windows = rolling_windows(end_dt)
    print(f"\n{'Window':<22} {'Baseline':>40}     {'With gates':>40}     {'Δ':>20}")
    print(f"{'':<22} {'$end':>10} {'P&L%':>8} {'DD%':>7} {'n':>6} {'WR':>4}     "
          f"{'$end':>10} {'P&L%':>8} {'DD%':>7} {'n':>6} {'WR':>4}     "
          f"{'$':>10} {'pp DD':>8}")

    rows = []
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = latest_ts

        base = run_window(features, data, sector_features, {}, start_ts, end_ts)
        gated = run_window(features, data, sector_features, {}, start_ts, end_ts, skip_fn=skip_fn)

        d_dollar = gated["end_capital"] - base["end_capital"]
        d_dd = gated["max_dd_pct"] - base["max_dd_pct"]
        rows.append((label, base, gated, d_dollar, d_dd))
        print(f"{label:<22} "
              f"{fmt_dollar(base['end_capital'])} {base['pnl_pct']:>+7.0f}% "
              f"{base['max_dd_pct']:>+6.1f}% {base['n_trades']:>6} {base['win_rate']:>3.0f}%     "
              f"{fmt_dollar(gated['end_capital'])} {gated['pnl_pct']:>+7.0f}% "
              f"{gated['max_dd_pct']:>+6.1f}% {gated['n_trades']:>6} {gated['win_rate']:>3.0f}%     "
              f"{fmt_dollar(d_dollar)} {d_dd:>+7.1f}")

    # Per-strategy delta on the longest window
    longest = sorted(rows, key=lambda r: r[1]["start_capital"])[0]  # any
    base, gated = longest[1], longest[2]
    print(f"\nPer-strategy on longest window ({longest[0]}):")
    print(f"  {'strat':<6} {'base_n':>8} {'base_pnl':>10} {'gated_n':>8} {'gated_pnl':>11} "
          f"{'Δ_n':>6} {'Δ_pnl':>10}")
    for s in sorted(set(base["by_strat"]) | set(gated["by_strat"])):
        b = base["by_strat"].get(s, {"n": 0, "pnl": 0.0})
        g = gated["by_strat"].get(s, {"n": 0, "pnl": 0.0})
        print(f"  {s:<6} {b['n']:>8} {b['pnl']:>+10.0f} {g['n']:>8} {g['pnl']:>+11.0f} "
              f"{g['n'] - b['n']:>+6} {g['pnl'] - b['pnl']:>+10.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
