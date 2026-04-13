"""Token scoring — identify underperforming tokens for potential removal.

Walk-forward approach: score tokens on a 16-month train window, then test
with the bottom scorers removed on a 12-month test window. Must pass the
same validation as all other gates: improve on 4/4 rolling windows.

Usage:
    python3 -m backtests.backtest_token_score
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, BACKTEST_SLIPPAGE_BPS, VERSION,
)
from analysis.bot.config import COST_BPS


COST = COST_BPS + BACKTEST_SLIPPAGE_BPS


def score_tokens_on_window(features, data, sector_features, dxy_data,
                           start_ts: int, end_ts: int) -> dict[str, dict]:
    """Run baseline and extract per-token stats."""
    result = run_window(features, data, sector_features, dxy_data,
                        start_ts, end_ts)
    trades = result["trades"]

    by_token = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "net_bps": []})
    for t in trades:
        tk = by_token[t["coin"]]
        tk["n"] += 1
        tk["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            tk["wins"] += 1
        tk["net_bps"].append(t["net"])

    scores = {}
    for token, stats in by_token.items():
        avg_bps = np.mean(stats["net_bps"]) if stats["net_bps"] else 0
        wr = stats["wins"] / stats["n"] * 100 if stats["n"] else 0
        # Score: avg net bps weighted by sqrt(n) for statistical confidence
        score = avg_bps * np.sqrt(stats["n"])
        scores[token] = {
            "n": stats["n"],
            "pnl": round(stats["pnl"], 2),
            "wr": round(wr, 1),
            "avg_bps": round(avg_bps, 1),
            "score": round(score, 1),
        }
    return scores


def run_with_excluded(features, data, sector_features, dxy_data,
                      start_ts: int, end_ts: int,
                      excluded: set[str]) -> dict:
    """Run backtest skipping excluded tokens."""
    def skip_fn(coin, ts, strat, direction):
        return coin in excluded
    return run_window(features, data, sector_features, dxy_data,
                      start_ts, end_ts, skip_fn=skip_fn)


def main():
    print("=" * 70)
    print("TOKEN SCORING & ROTATION ANALYSIS")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.isoformat()}")

    # ── Walk-forward split ──
    # Train: oldest 16 months, Test: most recent 12 months
    train_end_dt = end_dt - relativedelta(months=12)
    train_start_dt = train_end_dt - relativedelta(months=16)
    test_start_dt = train_end_dt

    train_start_ts = int(train_start_dt.timestamp() * 1000)
    train_end_ts = int(train_end_dt.timestamp() * 1000)
    test_start_ts = int(test_start_dt.timestamp() * 1000)
    test_end_ts = latest_ts

    print(f"\nTrain: {train_start_dt.strftime('%Y-%m-%d')} → {train_end_dt.strftime('%Y-%m-%d')}")
    print(f"Test:  {test_start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")

    # ── Score tokens on train window ──
    print("\n--- TOKEN SCORES (train window) ---")
    scores = score_tokens_on_window(features, data, sector_features, dxy_data,
                                    train_start_ts, train_end_ts)

    # Sort by score
    sorted_tokens = sorted(scores.items(), key=lambda x: x[1]["score"])
    print(f"\n  {'Token':6s} {'N':>4s} {'WR':>6s} {'Avg bps':>8s} {'P&L':>8s} {'Score':>8s}")
    print(f"  {'-'*6} {'-'*4} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")
    for token, s in sorted_tokens:
        marker = " ◀" if s["score"] < 0 else ""
        print(f"  {token:6s} {s['n']:4d} {s['wr']:5.1f}% {s['avg_bps']:+7.1f} "
              f"${s['pnl']:+7.0f} {s['score']:+7.0f}{marker}")

    # Identify negative-score tokens
    negative_tokens = {t for t, s in scores.items() if s["score"] < 0}
    unused_tokens = set(TOKENS) - set(scores.keys())
    print(f"\n  Negative score tokens ({len(negative_tokens)}): {sorted(negative_tokens)}")
    print(f"  Tokens with 0 trades on train: {sorted(unused_tokens)}")

    # ── Test: baseline vs excluding negative tokens ──
    print(f"\n{'=' * 70}")
    print("WALK-FORWARD VALIDATION")
    print(f"{'=' * 70}")

    # Rolling windows for validation
    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]

    # Test different exclusion sets
    exclusion_sets = []

    # Option A: exclude all negative-score tokens
    if negative_tokens:
        exclusion_sets.append(("Negative score", negative_tokens))

    # Option B: exclude worst N tokens
    worst_tokens = sorted(scores.items(), key=lambda x: x[1]["score"])
    for n_exclude in [3, 5, 7]:
        if n_exclude <= len(worst_tokens):
            excluded = {t for t, _ in worst_tokens[:n_exclude]}
            exclusion_sets.append((f"Worst {n_exclude}", excluded))

    # Option C: exclude tokens with negative P&L AND low trade count
    low_conf_bad = {t for t, s in scores.items()
                    if s["score"] < 0 and s["n"] <= 10}
    if low_conf_bad and low_conf_bad != negative_tokens:
        exclusion_sets.append(("Low-conf negative", low_conf_bad))

    for set_name, excluded in exclusion_sets:
        print(f"\n  --- {set_name}: excluding {sorted(excluded)} ---")
        all_better = True
        results = []
        for wlabel, start_dt_w in windows:
            start_ts = int(start_dt_w.timestamp() * 1000)

            baseline = run_window(features, data, sector_features, dxy_data,
                                  start_ts, latest_ts)
            test = run_with_excluded(features, data, sector_features, dxy_data,
                                     start_ts, latest_ts, excluded)

            delta_pnl = test["pnl"] - baseline["pnl"]
            delta_dd = test["max_dd_pct"] - baseline["max_dd_pct"]
            better = delta_pnl >= 0
            if not better:
                all_better = False

            print(f"    {wlabel}: ${baseline['pnl']:+,.0f} → ${test['pnl']:+,.0f} "
                  f"(Δ${delta_pnl:+,.0f}, DD {delta_dd:+.1f}pp, "
                  f"trades {baseline['n_trades']}→{test['n_trades']})")

        status = "PASS" if all_better else "FAIL"
        print(f"    [{status}]")

    # ── Per-token P&L on the FULL 28m window ──
    print(f"\n{'=' * 70}")
    print("PER-TOKEN P&L — FULL 28-MONTH WINDOW")
    print(f"{'=' * 70}")

    full_start = int((end_dt - relativedelta(months=28)).timestamp() * 1000)
    full_scores = score_tokens_on_window(features, data, sector_features, dxy_data,
                                         full_start, latest_ts)

    sorted_full = sorted(full_scores.items(), key=lambda x: x[1]["pnl"])
    print(f"\n  {'Token':6s} {'N':>4s} {'WR':>6s} {'Avg bps':>8s} {'P&L':>10s} {'Score':>8s}")
    print(f"  {'-'*6} {'-'*4} {'-'*6} {'-'*8} {'-'*10} {'-'*8}")
    total_pnl = 0
    for token, s in sorted_full:
        total_pnl += s["pnl"]
        marker = " ◀" if s["pnl"] < 0 else ""
        print(f"  {token:6s} {s['n']:4d} {s['wr']:5.1f}% {s['avg_bps']:+7.1f} "
              f"${s['pnl']:+9.0f} {s['score']:+7.0f}{marker}")
    print(f"\n  Total: ${total_pnl:+,.0f}")

    # ── Concentration analysis ──
    print(f"\n  --- Top 5 tokens by P&L ---")
    top5 = sorted(full_scores.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    top5_pnl = sum(s["pnl"] for _, s in top5)
    print(f"  {', '.join(t for t, _ in top5)}: ${top5_pnl:+,.0f} "
          f"({top5_pnl/total_pnl*100:.0f}% of total)")

    neg_tokens_full = [t for t, s in full_scores.items() if s["pnl"] < 0]
    neg_pnl = sum(full_scores[t]["pnl"] for t in neg_tokens_full)
    print(f"\n  Negative P&L tokens ({len(neg_tokens_full)}): "
          f"{', '.join(sorted(neg_tokens_full))}")
    print(f"  Total negative P&L: ${neg_pnl:+,.0f}")

    print(f"\n{'=' * 70}")
    print("DONE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
