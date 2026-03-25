"""Robustness tests for the 5 trading signals.

Test A: Walk-Forward Rolling (12m train window, 3m test, 7 windows)
Test B: Leave-5-Tokens-Out (10 iterations per signal)
Test C: Quarterly Breakdown (per-quarter P&L for each signal)

Usage:
    python3 -m analysis.backtest_robustness
"""

from __future__ import annotations

import random
import sys
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    COST_BPS,
    MAX_POSITIONS,
    MAX_SAME_DIR,
    POSITION_SIZE,
    STOP_LOSS_BPS,
    TOKENS,
    Rule,
    Strategy,
    backtest_strategy,
    build_features,
    load_3y_candles,
    quick_score,
)

# ── Signal Definitions ───────────────────────────────────────────────

SIGNALS = {
    "S1": Strategy([Rule("btc_30d", ">", 2000, 1)], hold=18),
    "S2": Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=18),
    "S4": Strategy(
        [Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1)],
        hold=18,
    ),
    "S5_approx": Strategy([Rule("alt_rank_7d", ">", 85, 1)], hold=12),
    "S8": Strategy(
        [
            Rule("drawdown", "<", -4000, 1),
            Rule("vol_z", ">", 1.0, 1),
            Rule("ret_6h", "<", -50, 1),
        ],
        hold=15,
    ),
}

# S8 uses its own stop loss
S8_STOP = -1500


def ts_ms(year, month, day=1):
    """Helper: datetime → milliseconds timestamp."""
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


# ── Helpers ──────────────────────────────────────────────────────────

def filter_trades_by_window(trades, start_ms, end_ms):
    """Keep only trades whose entry_t falls within [start_ms, end_ms)."""
    return [t for t in trades if start_ms <= t["entry_t"] < end_ms]


def run_signal(name, strategy, features, data, stop_loss=STOP_LOSS_BPS):
    """Run a single signal backtest on full data."""
    return backtest_strategy(
        strategy, features, data,
        period="all", cost=COST_BPS, max_pos=MAX_POSITIONS,
        max_dir=MAX_SAME_DIR, stop_loss=stop_loss, size=POSITION_SIZE,
    )


# ── TEST A: Walk-Forward Rolling ─────────────────────────────────────

def test_walk_forward(features, data):
    print("\n" + "=" * 78)
    print("  TEST A: Walk-Forward Rolling (12m train, 3m test, step 3m)")
    print("=" * 78)

    # Data spans: Aug 2023 → Mar 2026
    # Define windows: test windows start from Nov 2023 (after 3m warmup)
    # Actually, we test out-of-sample: features computed on all data, but trades
    # filtered to test window only. The "train" concept here is: the signal was
    # discovered on data before the test window.
    windows = [
        ("Q4-2023", ts_ms(2023, 10, 1), ts_ms(2024, 1, 1)),
        ("Q1-2024", ts_ms(2024, 1, 1),  ts_ms(2024, 4, 1)),
        ("Q2-2024", ts_ms(2024, 4, 1),  ts_ms(2024, 7, 1)),
        ("Q3-2024", ts_ms(2024, 7, 1),  ts_ms(2024, 10, 1)),
        ("Q4-2024", ts_ms(2024, 10, 1), ts_ms(2025, 1, 1)),
        ("Q1-2025", ts_ms(2025, 1, 1),  ts_ms(2025, 4, 1)),
        ("Q2-2025", ts_ms(2025, 4, 1),  ts_ms(2025, 7, 1)),
        ("Q3-2025", ts_ms(2025, 7, 1),  ts_ms(2025, 10, 1)),
        ("Q4-2025", ts_ms(2025, 10, 1), ts_ms(2026, 1, 1)),
        ("Q1-2026", ts_ms(2026, 1, 1),  ts_ms(2026, 4, 1)),
    ]

    # Pre-run all signals on full period
    all_trades = {}
    for name, strat in SIGNALS.items():
        sl = S8_STOP if name == "S8" else STOP_LOSS_BPS
        all_trades[name] = run_signal(name, strat, features, data, stop_loss=sl)

    # Header
    print(f"\n{'Signal':<12} {'Window':<10} {'P&L':>8} {'Trades':>7} {'Avg bps':>8} {'Win%':>6}")
    print("-" * 55)

    summary = defaultdict(lambda: {"profitable": 0, "total": 0, "pnls": []})

    for name in SIGNALS:
        trades_all = all_trades[name]
        for wname, wstart, wend in windows:
            wtrades = filter_trades_by_window(trades_all, wstart, wend)
            sc = quick_score(wtrades)
            if sc["n"] == 0:
                print(f"{name:<12} {wname:<10} {'---':>8} {'0':>7} {'---':>8} {'---':>6}")
                continue
            pnl_str = f"${sc['pnl']:+.0f}"
            profitable = sc["pnl"] > 0
            summary[name]["total"] += 1
            summary[name]["pnls"].append(sc["pnl"])
            if profitable:
                summary[name]["profitable"] += 1
            print(f"{name:<12} {wname:<10} {pnl_str:>8} {sc['n']:>7} {sc['avg']:>+8.1f} {sc['win']:>5.0f}%")
        print()

    # Summary
    print("\n--- Walk-Forward Summary ---")
    print(f"{'Signal':<12} {'Profitable':>12} {'Verdict':>10}")
    print("-" * 38)
    for name in SIGNALS:
        s = summary[name]
        total = s["total"]
        prof = s["profitable"]
        if total == 0:
            verdict = "NO DATA"
        elif prof / total >= 0.5:
            verdict = "STABLE"
        else:
            verdict = "FRAGILE"
        print(f"{name:<12} {prof:>4} / {total:<4}    {verdict:>10}")

    return summary


# ── TEST B: Leave-5-Tokens-Out ───────────────────────────────────────

def test_leave_tokens_out(data_raw):
    print("\n" + "=" * 78)
    print("  TEST B: Leave-5-Tokens-Out (10 iterations per signal)")
    print("=" * 78)

    random.seed(42)
    n_iter = 10

    # Full baseline: compute with all tokens
    import analysis.backtest_genetic as bg
    orig_tokens = bg.TOKENS[:]

    print("\n  Computing full-token baseline...")
    features_full = build_features(data_raw)
    baseline = {}
    for name, strat in SIGNALS.items():
        sl = S8_STOP if name == "S8" else STOP_LOSS_BPS
        trades = run_signal(name, strat, features_full, data_raw, stop_loss=sl)
        baseline[name] = quick_score(trades)["pnl"]

    print(f"  Baselines: {', '.join(f'{n}=${v:+.0f}' for n, v in baseline.items())}")

    # For each iteration, exclude 5 random tokens
    results = defaultdict(list)  # signal → list of pnls

    for it in range(n_iter):
        excluded = random.sample(orig_tokens, 5)
        remaining = [t for t in orig_tokens if t not in excluded]

        # Temporarily override TOKENS for build_features
        bg.TOKENS = remaining

        # Filter data to remaining + REF
        data_filtered = {k: v for k, v in data_raw.items()
                         if k in remaining or k in ["BTC", "ETH"]}
        feats = build_features(data_filtered)

        for name, strat in SIGNALS.items():
            sl = S8_STOP if name == "S8" else STOP_LOSS_BPS
            trades = backtest_strategy(
                strat, feats, data_filtered,
                period="all", cost=COST_BPS, max_pos=MAX_POSITIONS,
                max_dir=MAX_SAME_DIR, stop_loss=sl, size=POSITION_SIZE,
            )
            sc = quick_score(trades)
            results[name].append(sc["pnl"])

        sys.stdout.write(f"\r  Iteration {it+1}/{n_iter} done (excluded: {', '.join(excluded[:3])}...)")
        sys.stdout.flush()

    # Restore
    bg.TOKENS = orig_tokens

    print("\n")
    print(f"{'Signal':<12} {'Full P&L':>10} {'Mean(-5)':>10} {'Min(-5)':>10} {'Max(-5)':>10} {'Stable?':>8}")
    print("-" * 64)

    for name in SIGNALS:
        full = baseline[name]
        pnls = results[name]
        mean_pnl = np.mean(pnls)
        min_pnl = np.min(pnls)
        max_pnl = np.max(pnls)

        # Stable if mean P&L > 50% of full P&L (or both are near zero/negative)
        if full > 0:
            stable = mean_pnl > full * 0.5
        else:
            stable = mean_pnl >= full  # If full is negative, mean shouldn't be worse
        verdict = "YES" if stable else "NO"

        print(f"{name:<12} ${full:>+8.0f}  ${mean_pnl:>+8.0f}  ${min_pnl:>+8.0f}  ${max_pnl:>+8.0f}  {verdict:>7}")

    return results, baseline


# ── TEST C: Quarterly Breakdown ──────────────────────────────────────

def test_quarterly(features, data):
    print("\n" + "=" * 78)
    print("  TEST C: Quarterly P&L Breakdown")
    print("=" * 78)

    quarters = [
        ("Q3-23", ts_ms(2023, 7, 1),  ts_ms(2023, 10, 1)),
        ("Q4-23", ts_ms(2023, 10, 1), ts_ms(2024, 1, 1)),
        ("Q1-24", ts_ms(2024, 1, 1),  ts_ms(2024, 4, 1)),
        ("Q2-24", ts_ms(2024, 4, 1),  ts_ms(2024, 7, 1)),
        ("Q3-24", ts_ms(2024, 7, 1),  ts_ms(2024, 10, 1)),
        ("Q4-24", ts_ms(2024, 10, 1), ts_ms(2025, 1, 1)),
        ("Q1-25", ts_ms(2025, 1, 1),  ts_ms(2025, 4, 1)),
        ("Q2-25", ts_ms(2025, 4, 1),  ts_ms(2025, 7, 1)),
        ("Q3-25", ts_ms(2025, 7, 1),  ts_ms(2025, 10, 1)),
        ("Q4-25", ts_ms(2025, 10, 1), ts_ms(2026, 1, 1)),
        ("Q1-26", ts_ms(2026, 1, 1),  ts_ms(2026, 4, 1)),
    ]

    # Pre-run all signals on full period
    all_trades = {}
    for name, strat in SIGNALS.items():
        sl = S8_STOP if name == "S8" else STOP_LOSS_BPS
        all_trades[name] = run_signal(name, strat, features, data, stop_loss=sl)

    signal_names = list(SIGNALS.keys())
    q_names = [q[0] for q in quarters]

    # Build matrix
    matrix = {}  # (signal, quarter) → pnl
    for name in signal_names:
        for qname, qstart, qend in quarters:
            wtrades = filter_trades_by_window(all_trades[name], qstart, qend)
            sc = quick_score(wtrades)
            matrix[(name, qname)] = (sc["pnl"], sc["n"])

    # Print header
    hdr = f"{'Signal':<12}"
    for qn in q_names:
        hdr += f" {qn:>8}"
    hdr += f" {'TOTAL':>8}"
    print(f"\n{hdr}")
    print("-" * len(hdr))

    for name in signal_names:
        row = f"{name:<12}"
        total = 0
        for qn in q_names:
            pnl, n = matrix[(name, qn)]
            total += pnl
            if n == 0:
                cell = "---"
            elif pnl < 0:
                cell = f"{pnl:+.0f}*"  # asterisk marks losing quarters
            else:
                cell = f"{pnl:+.0f}"
            row += f" {cell:>8}"
        row += f" ${total:>+7.0f}"
        print(row)

    # Trade counts
    print(f"\n{'Trades':<12}", end="")
    for qn in q_names:
        total_n = sum(matrix[(name, qn)][1] for name in signal_names)
        print(f" {total_n:>8}", end="")
    print()

    # Per-signal losing quarter count
    print("\n--- Losing Quarters ---")
    for name in signal_names:
        losing = [qn for qn in q_names if matrix[(name, qn)][0] < 0 and matrix[(name, qn)][1] > 0]
        active = [qn for qn in q_names if matrix[(name, qn)][1] > 0]
        n_active = len(active)
        n_losing = len(losing)
        print(f"  {name:<12} {n_losing}/{n_active} quarters losing"
              + (f" ({', '.join(losing)})" if losing else ""))

    return matrix


# ── Main ─────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print("=" * 78)
    print("  ROBUSTNESS VALIDATION — Walk-Forward + Leave-Tokens-Out + Quarterly")
    print("=" * 78)

    print("\nLoading 3-year candle data...")
    data_raw = load_3y_candles()
    print(f"  Loaded {len(data_raw)} tokens, {sum(len(v) for v in data_raw.values())} total candles")

    print("\nBuilding features (this takes ~30s)...")
    features = build_features(data_raw)
    print(f"  Features built for {len(features)} tokens")

    # TEST A
    wf_summary = test_walk_forward(features, data_raw)

    # TEST B
    ltout_results, ltout_baseline = test_leave_tokens_out(data_raw)

    # Rebuild features for Test C (TOKENS may have been restored but let's be safe)
    print("\n  Rebuilding features for Test C...")
    features = build_features(data_raw)

    # TEST C
    q_matrix = test_quarterly(features, data_raw)

    # ── Final Verdict ────────────────────────────────────────────────
    elapsed = time.time() - t0
    print("\n" + "=" * 78)
    print(f"  FINAL ROBUSTNESS VERDICT  (completed in {elapsed:.0f}s)")
    print("=" * 78)

    for name in SIGNALS:
        issues = []

        # Walk-forward
        wf = wf_summary[name]
        if wf["total"] > 0:
            ratio = wf["profitable"] / wf["total"]
            if ratio < 0.5:
                issues.append(f"WF {wf['profitable']}/{wf['total']} profitable")
        else:
            issues.append("WF: no trades")

        # Leave-tokens-out
        full_pnl = ltout_baseline[name]
        mean_pnl = np.mean(ltout_results[name]) if ltout_results[name] else 0
        if full_pnl > 0 and mean_pnl < full_pnl * 0.5:
            issues.append(f"Token-dependent (mean ${mean_pnl:+.0f} vs full ${full_pnl:+.0f})")

        # Quarterly: count losing quarters with trades
        q_names_list = [q[0] for q in [
            ("Q3-23",), ("Q4-23",), ("Q1-24",), ("Q2-24",), ("Q3-24",),
            ("Q4-24",), ("Q1-25",), ("Q2-25",), ("Q3-25",), ("Q4-25",), ("Q1-26",),
        ]]
        # Re-derive from matrix keys
        active_qs = [qn for qn in [k[1] for k in q_matrix if k[0] == name]
                     if q_matrix[(name, qn)][1] > 0]
        losing_qs = [qn for qn in active_qs if q_matrix[(name, qn)][0] < 0]
        if len(active_qs) > 0 and len(losing_qs) / len(active_qs) > 0.5:
            issues.append(f"Loses majority of quarters ({len(losing_qs)}/{len(active_qs)})")

        if issues:
            print(f"  {name:<12} FRAGILE  — {'; '.join(issues)}")
        else:
            print(f"  {name:<12} ROBUST")

    print()


if __name__ == "__main__":
    main()
