"""Exploratory: correlate OI/impact features at entry time with trade outcomes.

Pre-registered features (decided before looking at any result to avoid
p-hacking):
    1. oi_delta_6h   : (oi_now - oi_6h_ago) / oi_6h_ago * 1e4        (bps)
    2. oi_delta_24h  : (oi_now - oi_24h_ago) / oi_24h_ago * 1e4      (bps)
    3. impact_spread : (impact_ask - impact_bid) / mid * 1e4         (bps)
    4. mark_oracle   : (mark_px - oracle_px) / oracle_px * 1e4       (bps)

For each feature, bin trades into quartiles and report avg net bps + WR per
bin, per signal. Same structure as backtest_funding_explore.py — so we can
compare directly whether OI adds something funding didn't.

Usage:
    python3 -m backtests.backtest_oi_explore
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

OI_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")
HOUR_S = 3600


def load_oi_lookup() -> dict:
    """Load asset_ctx into nested dict {symbol: {hour_ts: row}} for fast lookup."""
    db = sqlite3.connect(OI_DB)
    out: dict[str, dict[int, tuple]] = defaultdict(dict)
    for row in db.execute(
        "SELECT symbol, ts, oi, funding, premium, mark_px, oracle_px, "
        "day_ntl_vlm, impact_bid, impact_ask FROM asset_ctx"
    ):
        sym, ts = row[0], (row[1] // HOUR_S) * HOUR_S
        out[sym][ts] = row[2:]
    return dict(out)


def features_at(oi_lookup, sym: str, ts_ms: int) -> dict | None:
    """Compute the 4 pre-registered features for (sym, entry_time)."""
    ts = (ts_ms // 1000 // HOUR_S) * HOUR_S
    sym_data = oi_lookup.get(sym)
    if not sym_data:
        return None
    now = sym_data.get(ts)
    past_6h = sym_data.get(ts - 6 * HOUR_S)
    past_24h = sym_data.get(ts - 24 * HOUR_S)
    if not now or not past_6h or not past_24h:
        return None
    oi_now, _, _, mark, oracle, _, ib, ia = now
    oi_6h = past_6h[0]
    oi_24h = past_24h[0]
    if oi_6h <= 0 or oi_24h <= 0 or oracle <= 0 or mark <= 0:
        return None
    mid = (ib + ia) / 2
    if mid <= 0:
        return None
    return {
        "oi_delta_6h": (oi_now / oi_6h - 1) * 1e4,
        "oi_delta_24h": (oi_now / oi_24h - 1) * 1e4,
        "impact_spread": (ia - ib) / mid * 1e4,
        "mark_oracle": (mark / oracle - 1) * 1e4,
    }


def report_bins(label: str, trades: list[dict], values: list[float], n_bins: int = 4) -> None:
    if not trades:
        print(f"  {label}: no trades")
        return
    edges = list(np.quantile(values, np.linspace(0, 1, n_bins + 1)))
    buckets: dict[int, list] = defaultdict(list)
    for t, v in zip(trades, values):
        for i in range(n_bins):
            if v <= edges[i + 1]:
                buckets[i].append(t)
                break
        else:
            buckets[n_bins - 1].append(t)
    print(f"\n  {label}:")
    print(f"    {'bin':<5} {'≤edge':>12} {'n':>5} {'avg_net_bps':>12} {'WR':>6} {'sum_pnl_$':>10}")
    for i in range(n_bins):
        ts_in = buckets.get(i, [])
        if not ts_in:
            continue
        avg_net = float(np.mean([t["net"] for t in ts_in]))
        wr = sum(1 for t in ts_in if t["pnl"] > 0) / len(ts_in) * 100
        sum_pnl = sum(t["pnl"] for t in ts_in)
        print(f"    [{i}]  {edges[i + 1]:>+12.2f} {len(ts_in):>5} "
              f"{avg_net:>+12.1f} {wr:>5.0f}% {sum_pnl:>+10.1f}")


def main() -> int:
    print("Loading candles + features...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    print(f"  {len(data)} coins, {sum(len(f) for f in features.values())} feature points")

    print("Loading OI history...")
    oi_lookup = load_oi_lookup()
    print(f"  {len(oi_lookup)} symbols, "
          f"{sum(len(v) for v in oi_lookup.values()):,} hourly rows")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    # Match OI coverage: data stops at 2026-02-21, so cap the backtest window too
    oi_last = max(max(v.keys()) for v in oi_lookup.values())
    oi_last_dt = datetime.fromtimestamp(oi_last, tz=timezone.utc)
    eff_end_dt = min(end_dt, oi_last_dt)
    eff_end_ts = int(eff_end_dt.timestamp() * 1000)
    start_dt = eff_end_dt - relativedelta(months=28)
    print(f"\nWindow: {start_dt.date()} → {eff_end_dt.date()} "
          f"(capped to OI coverage)")

    print("Running backtest to generate trades...")
    r = run_window(features, data, sector_features, {},
                   int(start_dt.timestamp() * 1000), eff_end_ts)
    trades = r["trades"]
    print(f"  → {len(trades)} trades, P&L ${r['pnl']:.0f}, WR {r['win_rate']:.0f}%")

    # Enrich
    enriched = []
    missed = 0
    for t in trades:
        f = features_at(oi_lookup, t["coin"], t["entry_t"])
        if not f:
            missed += 1
            continue
        t = dict(t)
        t.update(f)
        # Signed by direction for asymmetric features
        t["oi_delta_6h_signed"] = f["oi_delta_6h"] * t["dir"]
        t["oi_delta_24h_signed"] = f["oi_delta_24h"] * t["dir"]
        t["mark_oracle_signed"] = f["mark_oracle"] * t["dir"]
        enriched.append(t)
    print(f"  enriched {len(enriched)} trades ({missed} skipped — no OI row)")

    by_strat = defaultdict(list)
    for t in enriched:
        by_strat[t["strat"]].append(t)

    for strat in sorted(by_strat):
        ts_in = by_strat[strat]
        print(f"\n{'='*64}\n{strat}: {len(ts_in)} trades, "
              f"avg net {np.mean([t['net'] for t in ts_in]):+.0f} bps, "
              f"P&L ${sum(t['pnl'] for t in ts_in):+.0f}")
        report_bins("oi_delta_6h_signed (bps, in direction)",
                    ts_in, [t["oi_delta_6h_signed"] for t in ts_in])
        report_bins("oi_delta_24h_signed (bps, in direction)",
                    ts_in, [t["oi_delta_24h_signed"] for t in ts_in])
        report_bins("impact_spread (bps, thin → thick)",
                    ts_in, [t["impact_spread"] for t in ts_in])
        report_bins("mark_oracle_signed (bps, in direction)",
                    ts_in, [t["mark_oracle_signed"] for t in ts_in])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
