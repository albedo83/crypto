"""S11 EDA — Per-token funding statistics and persistence.

Goal: identify which tokens have a capturable funding premium post-fees.

For each token, compute on multiple windows (28m / 12m / 6m):
- % time funding > 0
- Median funding rate (annualized bps)
- Mean funding rate (annualized bps) — vs historical
- Persistence: avg run length of consecutive positive funding hours
- Cap rate: % of hours funding hit the +100 ppm/h cap (= +876% annualized)
- Naive yield: if short when funding ≥ X bps annualized, average funding bps collected per 24h hold
- Naive net yield: same minus 9 bps round-trip fees

Output: per-token table sorted by recent (12m) net yield expectation.

Usage:
    .venv/bin/python3 -m backtests.eda_funding_persistence
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "output", "funding_history.db")

# Match the bot's current TRADE_SYMBOLS (35 tokens v12.7.0)
TRADE_TOKENS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA", "TON",
    "BCH", "DOT", "ADA", "XMR", "ENA", "UNI",
]

FEES_RT_BPS = 9.0  # 4.5 entry + 4.5 exit, taker on both legs

# Settlement detection — if median delta between consecutive ts < 1.5h, treat as hourly
# (HL switched many tokens from 8h → 1h funding around 2024-2025)

NOW_MS = int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def load_funding(con: sqlite3.Connection, symbol: str, start_ms: int = 0) -> list[tuple[int, float]]:
    cur = con.cursor()
    cur.execute(
        "SELECT ts, funding_rate FROM funding WHERE symbol = ? AND ts >= ? ORDER BY ts",
        (symbol, start_ms),
    )
    return cur.fetchall()


def detect_settlement_hours(ts_list: list[int]) -> float:
    """Return median delta between consecutive ts, in hours."""
    if len(ts_list) < 10:
        return 1.0
    deltas = [(ts_list[i + 1] - ts_list[i]) / 3600000 for i in range(len(ts_list) - 1)]
    return float(np.median(deltas))


def compute_stats(symbol: str, rows: list[tuple[int, float]], settle_h: float) -> dict:
    if not rows:
        return None
    rates = np.array([r[1] for r in rows])
    # Annualization factor: 8760 hours per year / settle_h
    ann_factor = (8760.0 / settle_h) * 1e4  # raw rate × ann_factor → annualized bps

    ann_bps = rates * ann_factor

    # Per-period bps (NOT annualized)
    period_bps = rates * 1e4

    n = len(rates)
    pct_positive = float((rates > 0).mean() * 100)
    pct_50ann = float((ann_bps >= 50).mean() * 100)
    pct_100ann = float((ann_bps >= 100).mean() * 100)
    pct_500ann = float((ann_bps >= 500).mean() * 100)
    pct_1000ann = float((ann_bps >= 1000).mean() * 100)

    median_ann = float(np.median(ann_bps))
    mean_ann = float(np.mean(ann_bps))
    p25_ann = float(np.percentile(ann_bps, 25))
    p75_ann = float(np.percentile(ann_bps, 75))

    # Persistence: avg run length of consecutive positive funding
    runs_pos = []
    cur_run = 0
    for r in rates:
        if r > 0:
            cur_run += 1
        else:
            if cur_run > 0:
                runs_pos.append(cur_run)
            cur_run = 0
    if cur_run > 0:
        runs_pos.append(cur_run)
    avg_run_pos = float(np.mean(runs_pos)) if runs_pos else 0.0
    median_run_pos = float(np.median(runs_pos)) if runs_pos else 0.0
    max_run_pos = max(runs_pos) if runs_pos else 0

    # Naive yield calculations for different entry thresholds.
    # Strategy: "short whenever funding > threshold (annualized), hold 24h, collect periods"
    # Per-period collection = funding_rate (in raw, positive → we receive as short)
    yields = {}
    for thresh_ann_bps in (50, 100, 200, 500, 1000):
        # rate threshold per period = thresh_ann / ann_factor
        rate_thresh = thresh_ann_bps / ann_factor

        # Identify entry indices where rate > thresh, but only "first" of a run (avoid stacking)
        in_pos = False
        entries = []
        for i, r in enumerate(rates):
            if not in_pos and r > rate_thresh:
                entries.append(i)
                in_pos = True
            elif r <= 0:
                in_pos = False
        # For each entry, collect 24h of funding (= ceil(24/settle_h) periods)
        n_periods = max(1, int(round(24 / settle_h)))
        collected = []
        for ei in entries:
            slice_end = min(ei + n_periods, n)
            if slice_end - ei < n_periods:
                continue
            # Per-period bps collected during the hold
            period_collected = period_bps[ei:slice_end].sum()
            collected.append(period_collected)
        if collected:
            avg_per_trade = float(np.mean(collected))
            net_per_trade = avg_per_trade - FEES_RT_BPS
            # Annualize: trades per year if each entry triggers 24h hold + assume entries fire on average X per year
            # In this token's data window, total trades over total time
            window_yrs = (rows[-1][0] - rows[0][0]) / 31536000000.0
            trades_per_yr = len(collected) / max(window_yrs, 0.01)
            ann_yield_bps = net_per_trade * trades_per_yr
            yields[thresh_ann_bps] = {
                "n": len(collected),
                "avg_gross_bps": round(avg_per_trade, 1),
                "avg_net_bps": round(net_per_trade, 1),
                "trades_per_yr": round(trades_per_yr, 1),
                "ann_yield_bps": round(ann_yield_bps, 0),
                "ann_yield_pct": round(ann_yield_bps / 100, 2),
            }
        else:
            yields[thresh_ann_bps] = None

    return {
        "n": n,
        "settle_h": round(settle_h, 2),
        "pct_pos": round(pct_positive, 1),
        "pct_50ann": round(pct_50ann, 1),
        "pct_100ann": round(pct_100ann, 1),
        "pct_500ann": round(pct_500ann, 1),
        "pct_1000ann": round(pct_1000ann, 1),
        "median_ann_pct": round(median_ann / 100, 2),
        "mean_ann_pct": round(mean_ann / 100, 2),
        "p25_ann_pct": round(p25_ann / 100, 2),
        "p75_ann_pct": round(p75_ann / 100, 2),
        "avg_run_pos_h": round(avg_run_pos * settle_h, 1),
        "median_run_pos_h": round(median_run_pos * settle_h, 1),
        "max_run_pos_h": round(max_run_pos * settle_h, 1),
        "yields": yields,
    }


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"Missing DB: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)

    windows = {
        "28m": NOW_MS - int(28 * 30.44 * 86400 * 1000),
        "12m": NOW_MS - int(12 * 30.44 * 86400 * 1000),
        "6m": NOW_MS - int(6 * 30.44 * 86400 * 1000),
        "3m": NOW_MS - int(3 * 30.44 * 86400 * 1000),
    }

    print("=" * 100)
    print(f"S11 EDA — Funding statistics over {len(TRADE_TOKENS)} bot tokens")
    print(f"DB: {DB_PATH}")
    print(f"Today: {datetime.fromtimestamp(NOW_MS/1000, tz=timezone.utc).isoformat()}")
    print("=" * 100)

    all_stats = {}

    for window_name, start_ms in windows.items():
        print(f"\n{'═' * 100}")
        print(f"WINDOW: {window_name}  (since {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).date()})")
        print(f"{'═' * 100}")
        print(f"\n  {'SYM':<7} {'N':>6} {'Stl':>4} {'%pos':>5} {'%>50':>5} {'%>500':>6} {'%>1k':>5} "
              f"{'med%':>7} {'mean%':>7} {'p25-p75%':>14} {'avg_run_h':>9}")

        window_stats = {}
        for token in TRADE_TOKENS:
            rows = load_funding(con, token, start_ms)
            if len(rows) < 50:
                continue
            ts_list = [r[0] for r in rows]
            settle_h = detect_settlement_hours(ts_list)
            s = compute_stats(token, rows, settle_h)
            if s is None:
                continue
            window_stats[token] = s

            iqr = f"{s['p25_ann_pct']:+.0f}/{s['p75_ann_pct']:+.0f}"
            print(f"  {token:<7} {s['n']:>6} {s['settle_h']:>4.1f} {s['pct_pos']:>5.1f} "
                  f"{s['pct_50ann']:>5.1f} {s['pct_500ann']:>6.1f} {s['pct_1000ann']:>5.1f} "
                  f"{s['median_ann_pct']:>+6.0f}% {s['mean_ann_pct']:>+6.0f}% {iqr:>14} "
                  f"{s['avg_run_pos_h']:>9.1f}")

        all_stats[window_name] = window_stats

    # ── Naive yield ranking ──────────────────────────────────────────────
    print(f"\n{'═' * 100}")
    print(f"NAIVE YIELD ESTIMATES (short when funding > threshold, hold 24h, net of 9bps RT fees)")
    print(f"{'═' * 100}")
    for window_name in ["28m", "12m", "6m", "3m"]:
        print(f"\n--- {window_name} ---")
        rows = []
        for token, s in all_stats[window_name].items():
            best = None
            for thresh, y in s["yields"].items():
                if y is None:
                    continue
                if y["n"] < 5:
                    continue
                if best is None or y["ann_yield_bps"] > best[1]["ann_yield_bps"]:
                    best = (thresh, y)
            if best:
                rows.append((token, best[0], best[1]))
        rows.sort(key=lambda x: x[2]["ann_yield_bps"], reverse=True)

        print(f"  {'SYM':<7} {'best_thresh':>11} {'n':>4} {'gross/trade':>11} {'net/trade':>10} "
              f"{'trades/yr':>9} {'ann_yield':>10}")
        for token, thresh, y in rows[:20]:
            print(f"  {token:<7} {thresh:>11} {y['n']:>4} {y['avg_gross_bps']:>+10.1f} "
                  f"{y['avg_net_bps']:>+9.1f} {y['trades_per_yr']:>9.1f} "
                  f"{y['ann_yield_pct']:>+9.1f}%")

    # ── Persistence summary ──────────────────────────────────────────────
    print(f"\n{'═' * 100}")
    print(f"PERSISTENCE (avg run length of positive funding, last 12m)")
    print(f"{'═' * 100}")
    if "12m" in all_stats:
        rows = sorted(all_stats["12m"].items(), key=lambda x: x[1]["avg_run_pos_h"], reverse=True)
        print(f"\n  {'SYM':<7} {'avg_run_h':>9} {'median_run_h':>12} {'max_run_h':>9} {'%pos':>5}")
        for token, s in rows[:20]:
            print(f"  {token:<7} {s['avg_run_pos_h']:>9.1f} {s['median_run_pos_h']:>12.1f} "
                  f"{s['max_run_pos_h']:>9.0f} {s['pct_pos']:>5.1f}")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
