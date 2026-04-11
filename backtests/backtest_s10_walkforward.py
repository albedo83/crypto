"""Proper train/test split for S10 token filter.

Train on first 16 months (2023-10 → 2025-02), identify winning tokens for
S10 on that window only, then test the filter on the out-of-sample 12 months
(2025-02 → 2026-02). Compare H1 (SHORT-only, categorical, no look-ahead),
H2 (token filter, trained on train), and H3 (both combined) against baseline.

A hypothesis has a real edge only if it improves the TEST window — train
results are informative but cannot prove anything.
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window

OI_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")


def fmt_dollar(v: float) -> str:
    return f"${v:>8,.0f}".replace(",", " ")


def main() -> int:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)

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

    # 28m window split 16m train + 12m test
    train_start = end_dt - relativedelta(months=28)
    split_dt = end_dt - relativedelta(months=12)
    test_start = split_dt

    train_start_ts = int(train_start.timestamp() * 1000)
    split_ts = int(split_dt.timestamp() * 1000)
    test_end_ts = int(end_dt.timestamp() * 1000)

    print(f"Train: {train_start.date()} → {split_dt.date()} (16m)")
    print(f"Test:  {split_dt.date()} → {end_dt.date()} (12m, out-of-sample)\n")

    # ── 1. Run baseline on train to identify winning tokens ─────────
    print("Step 1: running train baseline to identify S10 winning tokens...")
    train_base = run_window(features, data, sector_features, {},
                            train_start_ts, split_ts)
    train_s10 = [t for t in train_base["trades"] if t["strat"] == "S10"]
    per_tok = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in train_s10:
        b = per_tok[t["coin"]]
        b["n"] += 1
        b["pnl"] += t["pnl"]

    keep_tokens = {tok for tok, b in per_tok.items() if b["pnl"] > 0}
    print(f"  {len(train_s10)} S10 trades in train, {len(per_tok)} tokens seen")
    print(f"  → winners (P&L > 0): {sorted(keep_tokens)}")
    print(f"  → losers (filtered out): "
          f"{sorted(set(per_tok.keys()) - keep_tokens)}")

    # ── 2. Define hypotheses using train-derived filter ─────────
    def gate_h1(sym, ts, strat, direction):
        return strat == "S10" and direction == 1

    def gate_h2(sym, ts, strat, direction):
        return strat == "S10" and sym not in keep_tokens

    def gate_h3(sym, ts, strat, direction):
        return strat == "S10" and (direction == 1 or sym not in keep_tokens)

    hypotheses = [
        ("baseline", None),
        ("H1 SHORT-only", gate_h1),
        ("H2 good-tokens-train", gate_h2),
        ("H3 SHORT + good-tok", gate_h3),
    ]

    # ── 3. Run each on test window ─────────
    print("\nStep 2: testing on out-of-sample 12m...")
    print(f"{'Hypothesis':<22} {'$end':>10} {'P&L%':>8} {'DD':>7} "
          f"{'n':>6} {'Δ$ vs base':>12}")
    test_baseline_capital = None
    for name, fn in hypotheses:
        r = run_window(features, data, sector_features, {},
                       split_ts, test_end_ts, skip_fn=fn)
        if name == "baseline":
            test_baseline_capital = r["end_capital"]
            d = "—"
        else:
            diff = r["end_capital"] - test_baseline_capital
            sign = "+" if diff >= 0 else ""
            d = f"{sign}{fmt_dollar(diff):>10}"
        print(f"{name:<22} {fmt_dollar(r['end_capital']):>10} "
              f"{r['pnl_pct']:>+7.0f}% {r['max_dd_pct']:>+6.1f}% "
              f"{r['n_trades']:>6}  {d}")

    # ── 4. Also run on train window for reference ─────────
    print("\nStep 3: same hypotheses on TRAIN (for reference, in-sample expected to win)...")
    train_base_capital = train_base["end_capital"]
    print(f"{'Hypothesis':<22} {'$end':>10} {'P&L%':>8} {'DD':>7} "
          f"{'n':>6} {'Δ$ vs base':>12}")
    print(f"{'baseline':<22} {fmt_dollar(train_base_capital):>10} "
          f"{train_base['pnl_pct']:>+7.0f}% {train_base['max_dd_pct']:>+6.1f}% "
          f"{train_base['n_trades']:>6}  —")
    for name, fn in hypotheses:
        if name == "baseline":
            continue
        r = run_window(features, data, sector_features, {},
                       train_start_ts, split_ts, skip_fn=fn)
        diff = r["end_capital"] - train_base_capital
        sign = "+" if diff >= 0 else ""
        print(f"{name:<22} {fmt_dollar(r['end_capital']):>10} "
              f"{r['pnl_pct']:>+7.0f}% {r['max_dd_pct']:>+6.1f}% "
              f"{r['n_trades']:>6}  {sign}{fmt_dollar(diff):>10}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
