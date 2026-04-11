"""Deep diagnostic on S10: is it still paying for its slot?

Runs 4 diagnostics on the 28-month rolling backtest:
  1. S10 P&L per calendar year (decay check)
  2. S10 P&L per token (concentration check)
  3. Baseline vs S10-disabled (slot substitution effect)
  4. S10 exit reasons (stop vs timeout vs ...)

Read-only. Does not touch bot code or live state.
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


def skip_s10(sym, ts, strat, direction):
    return strat == "S10"


def fmt_dollar(v: float) -> str:
    return f"${v:>8,.0f}".replace(",", " ")


def main() -> int:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    print(f"  {len(data)} coins, {sum(len(f) for f in features.values())} feature points")

    # Cap to OI coverage for fair comparison with previous tests
    latest_ts = max(c["t"] for c in data["BTC"])
    db = sqlite3.connect(OI_DB) if os.path.exists(OI_DB) else None
    if db:
        oi_last = db.execute("SELECT MAX(ts) FROM asset_ctx").fetchone()[0]
        end_dt = min(
            datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc),
            datetime.fromtimestamp(oi_last, tz=timezone.utc),
        )
    else:
        end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    end_ts = int(end_dt.timestamp() * 1000)
    print(f"End capped to {end_dt.date()}\n")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]

    # ── 1. Baseline vs S10 removed on each window ─────────
    print("=" * 70)
    print("DIAG 1: What if we killed S10 entirely?")
    print("=" * 70)
    print(f"{'window':<6} {'baseline $':>12} {'no-S10 $':>12} {'Δ$':>10} {'Δ%':>7} "
          f"{'Δ DD':>7} {'Δ n':>6}")
    longest_base = None
    longest_no_s10 = None
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        base = run_window(features, data, sector_features, {}, start_ts, end_ts)
        noS10 = run_window(features, data, sector_features, {}, start_ts, end_ts,
                           skip_fn=skip_s10)
        d_dol = noS10["end_capital"] - base["end_capital"]
        d_pct = noS10["pnl_pct"] - base["pnl_pct"]
        d_dd = noS10["max_dd_pct"] - base["max_dd_pct"]
        d_n = noS10["n_trades"] - base["n_trades"]
        sign = "+" if d_dol >= 0 else ""
        print(f"{label:<6} {fmt_dollar(base['end_capital']):>12} "
              f"{fmt_dollar(noS10['end_capital']):>12} "
              f"{sign}{fmt_dollar(d_dol):>9} {d_pct:>+6.0f}% {d_dd:>+6.1f} {d_n:>+6}")
        if label == "28m":
            longest_base = base
            longest_no_s10 = noS10

    # ── 2. S10 P&L per year (from longest baseline) ─────────
    print("\n" + "=" * 70)
    print("DIAG 2: S10 P&L per calendar year (decay check)")
    print("=" * 70)
    s10_trades = [t for t in longest_base["trades"] if t["strat"] == "S10"]
    by_year = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0,
                                    "sum_bps": 0.0, "stops": 0, "timeouts": 0})
    for t in s10_trades:
        y = datetime.fromtimestamp(t["exit_t"] / 1000, tz=timezone.utc).year
        b = by_year[y]
        b["n"] += 1
        b["pnl"] += t["pnl"]
        b["sum_bps"] += t["net"]
        if t["pnl"] > 0:
            b["wins"] += 1
        if t["reason"] == "stop":
            b["stops"] += 1
        elif t["reason"] == "timeout":
            b["timeouts"] += 1
    print(f"{'year':<6} {'n':>5} {'WR':>5} {'avg bps':>10} {'P&L':>12} "
          f"{'stops':>7} {'timeouts':>9}")
    for y in sorted(by_year):
        b = by_year[y]
        wr = b["wins"] / b["n"] * 100
        avg = b["sum_bps"] / b["n"]
        print(f"{y:<6} {b['n']:>5} {wr:>4.0f}% {avg:>+9.1f} {fmt_dollar(b['pnl']):>12} "
              f"{b['stops']:>7} {b['timeouts']:>9}")

    # ── 3. S10 P&L per token (concentration check) ─────────
    print("\n" + "=" * 70)
    print("DIAG 3: S10 P&L per token (concentration check)")
    print("=" * 70)
    by_tok = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "sum_bps": 0.0})
    for t in s10_trades:
        b = by_tok[t["coin"]]
        b["n"] += 1
        b["pnl"] += t["pnl"]
        b["sum_bps"] += t["net"]
        if t["pnl"] > 0:
            b["wins"] += 1
    print(f"{'token':<8} {'n':>5} {'WR':>5} {'avg bps':>10} {'P&L':>12}")
    sorted_toks = sorted(by_tok.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
    for tok, b in sorted_toks:
        wr = b["wins"] / b["n"] * 100
        avg = b["sum_bps"] / b["n"]
        print(f"{tok:<8} {b['n']:>5} {wr:>4.0f}% {avg:>+9.1f} {fmt_dollar(b['pnl']):>12}")
    # Cumulative contribution
    total_pnl = sum(b["pnl"] for _, b in sorted_toks)
    print(f"\n  Total S10 P&L (28m): ${total_pnl:+,.0f}")
    cum = 0
    for i, (tok, b) in enumerate(sorted_toks):
        cum += b["pnl"]
        if cum >= total_pnl * 0.8:
            print(f"  Top {i+1} tokens account for 80% of S10 P&L")
            break

    # ── 4. S10 exit reasons ─────────
    print("\n" + "=" * 70)
    print("DIAG 4: S10 exit reason distribution (28m)")
    print("=" * 70)
    reasons = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in s10_trades:
        r = reasons[t["reason"]]
        r["n"] += 1
        r["pnl"] += t["pnl"]
    for reason, b in sorted(reasons.items(), key=lambda kv: -kv[1]["n"]):
        pct = b["n"] / len(s10_trades) * 100
        print(f"  {reason:<15} n={b['n']:>4} ({pct:>4.0f}%)  P&L={fmt_dollar(b['pnl'])}")

    # ── 5. S10 direction bias ─────────
    print("\n" + "=" * 70)
    print("DIAG 5: S10 LONG vs SHORT (fade direction)")
    print("=" * 70)
    for d in [1, -1]:
        ts = [t for t in s10_trades if t["dir"] == d]
        if not ts:
            continue
        name = "LONG" if d == 1 else "SHORT"
        wr = sum(1 for t in ts if t["pnl"] > 0) / len(ts) * 100
        pnl = sum(t["pnl"] for t in ts)
        avg = sum(t["net"] for t in ts) / len(ts)
        print(f"  {name:<6} n={len(ts):>4}  WR {wr:>4.0f}%  avg {avg:>+7.1f} bps  "
              f"P&L {fmt_dollar(pnl)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
