"""Cross funding/premium history with the bot's historical trade decisions.

For every trade the bot would have taken on the 28-month window, look up the
entry-hour funding_rate and 24h-avg premium from funding_history.db, then bin
trades by quartile and report avg net bps + WR per bin per signal. Goal: see
if there's an exploitable funding/premium pattern that could become a gate.

Usage:
    python3 -m backtests.backtest_funding_explore
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window

FUNDING_DB = os.path.join(os.path.dirname(__file__), "output", "funding_history.db")
HOUR_MS = 3600 * 1000


def load_funding() -> dict[str, dict[int, tuple[float, float]]]:
    """Load all funding+premium history into memory: {sym: {hour_ts: (funding, premium)}}."""
    db = sqlite3.connect(FUNDING_DB)
    out: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)
    for sym, ts, fr, pr in db.execute("SELECT symbol, ts, funding_rate, premium FROM funding"):
        out[sym][(ts // HOUR_MS) * HOUR_MS] = (float(fr), float(pr))
    return out


def funding_at(funding, sym: str, ts: int) -> tuple[float, float] | None:
    return funding.get(sym, {}).get((ts // HOUR_MS) * HOUR_MS)


def funding_avg(funding, sym: str, ts: int, hours: int) -> tuple[float, float] | None:
    rows = []
    for h in range(hours):
        v = funding_at(funding, sym, ts - h * HOUR_MS)
        if v:
            rows.append(v)
    if not rows:
        return None
    return (float(np.mean([r[0] for r in rows])), float(np.mean([r[1] for r in rows])))


def report_bins(label: str, trades: list[dict], values: list[float], n_bins: int = 4) -> None:
    if not trades:
        print(f"  {label}: no trades")
        return
    edges = list(np.quantile(values, np.linspace(0, 1, n_bins + 1)))
    buckets = defaultdict(list)
    for t, v in zip(trades, values):
        for i in range(n_bins):
            if v <= edges[i + 1]:
                buckets[i].append(t)
                break
        else:
            buckets[n_bins - 1].append(t)
    print(f"\n  {label} (n={len(trades)}, edges={[round(e,3) for e in edges]}):")
    print(f"    {'bin':<5} {'≤edge':>12} {'n':>5} {'avg_net_bps':>12} {'WR':>6} {'sum_pnl_$':>10}")
    for i in range(n_bins):
        ts_in = buckets.get(i, [])
        if not ts_in:
            continue
        avg_net = float(np.mean([t["net"] for t in ts_in]))
        wr = sum(1 for t in ts_in if t["pnl"] > 0) / len(ts_in) * 100
        sum_pnl = sum(t["pnl"] for t in ts_in)
        print(f"    [{i}]  {edges[i + 1]:>+12.4f} {len(ts_in):>5} "
              f"{avg_net:>+12.1f} {wr:>5.0f}% {sum_pnl:>+10.1f}")


def main() -> int:
    print("Loading candles + features...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    print(f"  {len(data)} coins, {sum(len(f) for f in features.values())} feature points")

    print("Loading funding history...")
    funding = load_funding()
    print(f"  {len(funding)} symbols, {sum(len(v) for v in funding.values())} hourly rows")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    start_dt = end_dt - relativedelta(months=28)
    print(f"\nWindow: {start_dt.date()} → {end_dt.date()}")

    print("Running portfolio backtest to generate trades...")
    r = run_window(features, data, sector_features, {},
                   int(start_dt.timestamp() * 1000), latest_ts)
    trades = r["trades"]
    print(f"  → {len(trades)} trades, P&L ${r['pnl']:.0f}, WR {r['win_rate']:.0f}%")

    # Enrich each trade with funding context
    enriched = []
    missed = 0
    for t in trades:
        sym, et = t["coin"], t["entry_t"]
        f1 = funding_at(funding, sym, et)
        f24 = funding_avg(funding, sym, et, 24)
        if f1 is None or f24 is None:
            missed += 1
            continue
        t = dict(t)
        # Convert to bps for readability (funding is hourly fraction)
        t["fund_1h_bps"] = f1[0] * 1e4
        t["prem_1h_bps"] = f1[1] * 1e4
        t["fund_24h_bps"] = f24[0] * 1e4
        t["prem_24h_bps"] = f24[1] * 1e4
        # Signed by direction (positive = funding goes against the position)
        t["fund_against"] = t["fund_1h_bps"] * t["dir"]
        t["prem_against"] = t["prem_1h_bps"] * t["dir"]
        enriched.append(t)

    print(f"  enriched {len(enriched)} trades ({missed} skipped — no funding row)")

    # Per-strategy quartile reports
    by_strat = defaultdict(list)
    for t in enriched:
        by_strat[t["strat"]].append(t)

    for strat in sorted(by_strat):
        ts_in = by_strat[strat]
        print(f"\n{'='*60}\n{strat}: {len(ts_in)} trades, "
              f"avg net {np.mean([t['net'] for t in ts_in]):+.0f} bps, "
              f"P&L ${sum(t['pnl'] for t in ts_in):+.0f}")
        report_bins("funding_1h (bps, signed)",
                    ts_in, [t["fund_1h_bps"] for t in ts_in])
        report_bins("funding_24h_avg (bps, signed)",
                    ts_in, [t["fund_24h_bps"] for t in ts_in])
        report_bins("premium_1h (bps, signed)",
                    ts_in, [t["prem_1h_bps"] for t in ts_in])
        report_bins("funding_against_position (bps)",
                    ts_in, [t["fund_against"] for t in ts_in])
        report_bins("premium_against_position (bps)",
                    ts_in, [t["prem_against"] for t in ts_in])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
