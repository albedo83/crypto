"""Regime-Based Signal Gating — Backtest on Hyperliquid 4h candles.

Tests whether enabling/disabling signals based on market regime
(bull/bear, volatility state) improves performance.

Part A: Existing signals split by regime (S1, S2, S4)
Part B: New regime-gated signals (bear-short, bull-long, volatile-short)
Part C: S2 with bull-regime filters
All variants sweep hold (9, 12, 18) and thresholds.
Monte Carlo validates winners.

Usage:
    python3 -m analysis.backtest_regime
"""

from __future__ import annotations

import time
from copy import deepcopy
from itertools import product

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features, backtest_strategy,
    quick_score, monte_carlo_validate,
    Strategy, Rule, COST_BPS, POSITION_SIZE,
    MAX_POSITIONS, MAX_SAME_DIR, STOP_LOSS_BPS,
    TRAIN_END, TEST_START, TOKENS,
)


# ── Helpers ──────────────────────────────────────────────────────────

def banner(title: str):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def print_result_row(label: str, train, test, combined_pnl):
    print(f"  {label:<55} "
          f"Train ${train['pnl']:>+7.0f} ({train['n']:>3}t, {train['avg']:>+5.0f}bps) | "
          f"Test ${test['pnl']:>+7.0f} ({test['n']:>3}t, {test['avg']:>+5.0f}bps) | "
          f"Total ${combined_pnl:>+7.0f}")


def evaluate_strategy(strat, features, data, label=""):
    """Run train+test, return dict with results or None if not enough trades."""
    trades_train = backtest_strategy(strat, features, data, period="train")
    trades_test = backtest_strategy(strat, features, data, period="test")
    s_train = quick_score(trades_train)
    s_test = quick_score(trades_test)
    combined = s_train["pnl"] + s_test["pnl"]
    return {
        "label": label,
        "strat": strat,
        "train": s_train,
        "test": s_test,
        "combined_pnl": combined,
    }


def validate_and_print(results, features, data, top_n=5, min_train_trades=10,
                        header="Top results"):
    """Sort results, print table, Monte Carlo validate top winners."""
    # Filter for minimum trade count
    results = [r for r in results if r["train"]["n"] >= min_train_trades]
    results.sort(key=lambda x: x["combined_pnl"], reverse=True)

    print(f"\n  {header} ({len(results)} variants passed filters)")
    print(f"  {'-' * 120}")

    for r in results[:20]:
        print_result_row(r["label"], r["train"], r["test"], r["combined_pnl"])

    # Monte Carlo validate top winners that are profitable on both periods
    winners = [r for r in results if r["train"]["pnl"] > 0 and r["test"]["pnl"] > 0]
    if winners:
        print(f"\n  Monte Carlo validation (top {min(top_n, len(winners))}):")
        print(f"  {'-' * 90}")
        for r in winners[:top_n]:
            mc = monte_carlo_validate(r["strat"], features, data, n_sims=500)
            status = "PASS" if mc["z"] >= 2.0 else "FAIL"
            print(f"  [{status}] z={mc['z']:.2f}  p={mc['p']:.3f}  "
                  f"actual=${mc['actual']:>+.0f}  "
                  f"random=${mc['random_mean']:>+.0f}+/-{mc['random_std']:.0f}  "
                  f"n={mc['n_trades']}  | {r['label']}")

    return results


# ── Part A: Existing signals split by regime ─────────────────────────

def part_a_regime_split(features, data):
    """Test existing S1, S2, S4 performance split by bull/bear regime."""
    banner("PART A: Existing Signals Split by Bull/Bear Regime")

    results = []
    hold_periods = [9, 12, 18]

    # -- S1: btc_30d > 2000 → LONG (fires in bull anyway) --
    print("\n  --- S1: BTC 30d momentum LONG ---")
    for hold in hold_periods:
        # S1 baseline
        strat = Strategy([Rule("btc_30d", ">", 2000, 1)], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S1 baseline btc_30d>2000 LONG hold={hold*4}h")
        results.append(r)

        # S1 bull-only (btc_30d > 0) — redundant since btc_30d > 2000 implies > 0
        # but test with tighter threshold
        for thresh in [1500, 2000, 2500, 3000]:
            strat = Strategy([Rule("btc_30d", ">", thresh, 1)], hold=hold)
            r = evaluate_strategy(strat, features, data,
                                  f"S1 btc_30d>{thresh} LONG hold={hold*4}h")
            results.append(r)

    validate_and_print(results, features, data, header="S1 variants")

    # -- S2: alt_index_7d < -1000 → LONG --
    print("\n\n  --- S2: Alt index dip LONG ---")
    results_s2 = []

    for hold in hold_periods:
        # S2 baseline (all regimes)
        strat = Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2 base alt_idx<-1000 LONG hold={hold*4}h ALL")
        results_s2.append(r)

        # S2 bull-only (btc_30d > 0)
        strat = Strategy([
            Rule("alt_index_7d", "<", -1000, 1),
            Rule("btc_30d", ">", 0, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2 alt_idx<-1000 LONG hold={hold*4}h BULL(btc>0)")
        results_s2.append(r)

        # S2 bear-only (btc_30d < 0)
        strat = Strategy([
            Rule("alt_index_7d", "<", -1000, 1),
            Rule("btc_30d", "<", 0, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2 alt_idx<-1000 LONG hold={hold*4}h BEAR(btc<0)")
        results_s2.append(r)

    validate_and_print(results_s2, features, data, header="S2 regime split")

    # -- S4: vol contraction → SHORT --
    print("\n\n  --- S4: Vol contraction SHORT ---")
    results_s4 = []

    for hold in hold_periods:
        # S4 baseline
        strat = Strategy([
            Rule("vol_ratio", "<", 1.0, -1),
            Rule("range_pct", "<", 200, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S4 base vr<1.0+rng<200 SHORT hold={hold*4}h ALL")
        results_s4.append(r)

        # S4 bear-only (btc_30d < 0)
        strat = Strategy([
            Rule("vol_ratio", "<", 1.0, -1),
            Rule("range_pct", "<", 200, -1),
            Rule("btc_30d", "<", 0, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S4 vr<1.0+rng<200 SHORT hold={hold*4}h BEAR(btc<0)")
        results_s4.append(r)

        # S4 bear-only (btc_30d < -500)
        strat = Strategy([
            Rule("vol_ratio", "<", 1.0, -1),
            Rule("range_pct", "<", 200, -1),
            Rule("btc_30d", "<", -500, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S4 vr<1.0+rng<200 SHORT hold={hold*4}h BEAR(btc<-500)")
        results_s4.append(r)

        # S4 not-bull (btc_30d < 1000)
        strat = Strategy([
            Rule("vol_ratio", "<", 1.0, -1),
            Rule("range_pct", "<", 200, -1),
            Rule("btc_30d", "<", 1000, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S4 vr<1.0+rng<200 SHORT hold={hold*4}h NOTBULL(btc<1k)")
        results_s4.append(r)

    validate_and_print(results_s4, features, data, header="S4 regime split")


# ── Part B: New regime-gated signals ─────────────────────────────────

def part_b_new_regime_signals(features, data):
    """Test new signals that only fire in specific regimes."""
    banner("PART B: New Regime-Gated Signals")

    all_results = []

    # -- B1: BEAR SHORT: btc_30d < X AND alt_rank_7d > Y → SHORT --
    print("\n  --- B1: Bear Short (fade outperformers in bear) ---")
    btc_thresholds = [-500, -1000, -1500, -2000]
    rank_thresholds = [60, 70, 80, 90]
    hold_periods = [9, 12, 18]

    results_b1 = []
    for btc_t, rank_t, hold in product(btc_thresholds, rank_thresholds, hold_periods):
        strat = Strategy([
            Rule("btc_30d", "<", btc_t, -1),
            Rule("alt_rank_7d", ">", rank_t, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"B1 btc<{btc_t}+rank>{rank_t} SHORT h={hold*4}h")
        results_b1.append(r)

    validate_and_print(results_b1, features, data, header="B1: Bear Short")
    all_results.extend(results_b1)

    # -- B2: BULL LONG: btc_30d > X AND drawdown < Y → LONG --
    print("\n\n  --- B2: Bull Long (buy dips in bull) ---")
    btc_thresholds = [500, 1000, 1500, 2000]
    dd_thresholds = [-2000, -2500, -3000, -4000]

    results_b2 = []
    for btc_t, dd_t, hold in product(btc_thresholds, dd_thresholds, hold_periods):
        strat = Strategy([
            Rule("btc_30d", ">", btc_t, 1),
            Rule("drawdown", "<", dd_t, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"B2 btc>{btc_t}+dd<{dd_t} LONG h={hold*4}h")
        results_b2.append(r)

    validate_and_print(results_b2, features, data, header="B2: Bull Long (buy dips)")
    all_results.extend(results_b2)

    # -- B3: VOLATILE SHORT: dispersion > X AND vol_ratio > Y → SHORT --
    print("\n\n  --- B3: Volatile Short (scatter + vol expansion) ---")
    disp_thresholds = [1000, 1500, 2000, 2500]
    vr_thresholds = [1.0, 1.3, 1.5, 1.8]

    results_b3 = []
    for disp_t, vr_t, hold in product(disp_thresholds, vr_thresholds, hold_periods):
        strat = Strategy([
            Rule("dispersion_7d", ">", disp_t, -1),
            Rule("vol_ratio", ">", vr_t, -1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"B3 disp>{disp_t}+vr>{vr_t} SHORT h={hold*4}h")
        results_b3.append(r)

    validate_and_print(results_b3, features, data, header="B3: Volatile Short")
    all_results.extend(results_b3)

    # Print combined best across all new signals
    print("\n")
    banner("PART B COMBINED: Best New Regime-Gated Signals")
    validate_and_print(all_results, features, data, top_n=8,
                       header="All Part B combined ranking")


# ── Part C: Regime-filtered S2 ───────────────────────────────────────

def part_c_regime_filtered_s2(features, data):
    """Compare S2 with different bull-regime filters."""
    banner("PART C: S2 with Regime Filters — Systematic Comparison")

    results = []
    hold_periods = [9, 12, 18]
    alt_thresholds = [-500, -800, -1000, -1200, -1500, -2000]

    for alt_t, hold in product(alt_thresholds, hold_periods):
        # C1: S2 original (all regimes)
        strat = Strategy([Rule("alt_index_7d", "<", alt_t, 1)], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2-orig alt_idx<{alt_t} LONG h={hold*4}h ALL")
        results.append(r)

        # C2: S2-bull (btc_30d > 0)
        strat = Strategy([
            Rule("alt_index_7d", "<", alt_t, 1),
            Rule("btc_30d", ">", 0, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2-bull alt_idx<{alt_t} LONG h={hold*4}h btc>0")
        results.append(r)

        # C3: S2-strong-bull (btc_30d > 500)
        strat = Strategy([
            Rule("alt_index_7d", "<", alt_t, 1),
            Rule("btc_30d", ">", 500, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2-sbull alt_idx<{alt_t} LONG h={hold*4}h btc>500")
        results.append(r)

        # C4: S2-very-strong-bull (btc_30d > 1000)
        strat = Strategy([
            Rule("alt_index_7d", "<", alt_t, 1),
            Rule("btc_30d", ">", 1000, 1),
        ], hold=hold)
        r = evaluate_strategy(strat, features, data,
                              f"S2-vsbull alt_idx<{alt_t} LONG h={hold*4}h btc>1000")
        results.append(r)

    validate_and_print(results, features, data, top_n=8,
                       header="S2 regime-filtered comparison")

    # Direct comparison table for the canonical S2 threshold (-1000)
    print("\n\n  === Direct Comparison: alt_index_7d < -1000 across regimes ===")
    print(f"  {'Variant':<55} {'Train PnL':>10} {'Train N':>8} {'Train Avg':>10} "
          f"{'Test PnL':>10} {'Test N':>8} {'Test Avg':>10} {'Total':>10}")
    print(f"  {'-' * 125}")
    for r in results:
        if "alt_idx<-1000" in r["label"]:
            tr = r["train"]
            te = r["test"]
            print(f"  {r['label']:<55} "
                  f"${tr['pnl']:>+8.0f} {tr['n']:>7} {tr['avg']:>+9.1f} "
                  f"${te['pnl']:>+8.0f} {te['n']:>7} {te['avg']:>+9.1f} "
                  f"${r['combined_pnl']:>+8.0f}")


# ── Summary ──────────────────────────────────────────────────────────

def final_summary(features, data):
    """Run all the best candidates through Monte Carlo one more time."""
    banner("FINAL SUMMARY: Best Regime-Gated Strategies")

    candidates = [
        # Current S1/S2/S4 baselines for reference
        ("S1-baseline btc_30d>2000 LONG 72h",
         Strategy([Rule("btc_30d", ">", 2000, 1)], hold=18)),
        ("S2-baseline alt_idx<-1000 LONG 72h",
         Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=18)),
        ("S4-baseline vr<1.0+rng<200 SHORT 72h",
         Strategy([Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1)], hold=18)),

        # S2 regime-gated variants
        ("S2-bull alt_idx<-1000+btc>0 LONG 72h",
         Strategy([Rule("alt_index_7d", "<", -1000, 1), Rule("btc_30d", ">", 0, 1)], hold=18)),
        ("S2-sbull alt_idx<-1000+btc>500 LONG 72h",
         Strategy([Rule("alt_index_7d", "<", -1000, 1), Rule("btc_30d", ">", 500, 1)], hold=18)),

        # S4 bear-gated
        ("S4-bear vr<1.0+rng<200+btc<0 SHORT 72h",
         Strategy([Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1),
                   Rule("btc_30d", "<", 0, -1)], hold=18)),

        # New signals
        ("B1 bear-short btc<-1000+rank>70 SHORT 72h",
         Strategy([Rule("btc_30d", "<", -1000, -1), Rule("alt_rank_7d", ">", 70, -1)], hold=18)),
        ("B2 bull-dip btc>1000+dd<-2000 LONG 72h",
         Strategy([Rule("btc_30d", ">", 1000, 1), Rule("drawdown", "<", -2000, 1)], hold=18)),
        ("B3 volatile-short disp>1500+vr>1.3 SHORT 72h",
         Strategy([Rule("dispersion_7d", ">", 1500, -1), Rule("vol_ratio", ">", 1.3, -1)], hold=18)),

        # Hold period variants for B1/B2/B3
        ("B1 bear-short btc<-1000+rank>70 SHORT 48h",
         Strategy([Rule("btc_30d", "<", -1000, -1), Rule("alt_rank_7d", ">", 70, -1)], hold=12)),
        ("B2 bull-dip btc>1000+dd<-2000 LONG 48h",
         Strategy([Rule("btc_30d", ">", 1000, 1), Rule("drawdown", "<", -2000, 1)], hold=12)),
        ("B3 volatile-short disp>1500+vr>1.3 SHORT 48h",
         Strategy([Rule("dispersion_7d", ">", 1500, -1), Rule("vol_ratio", ">", 1.3, -1)], hold=12)),

        # Tighter thresholds for B1
        ("B1 bear-short btc<-500+rank>80 SHORT 72h",
         Strategy([Rule("btc_30d", "<", -500, -1), Rule("alt_rank_7d", ">", 80, -1)], hold=18)),
        ("B1 bear-short btc<-1500+rank>70 SHORT 72h",
         Strategy([Rule("btc_30d", "<", -1500, -1), Rule("alt_rank_7d", ">", 70, -1)], hold=18)),
    ]

    print(f"\n  {'Label':<50} {'Train':>14} {'Test':>14} {'Total':>8}  "
          f"{'MC z':>6} {'MC p':>6} {'Status'}")
    print(f"  {'-' * 115}")

    for label, strat in candidates:
        tr_trades = backtest_strategy(strat, features, data, period="train")
        te_trades = backtest_strategy(strat, features, data, period="test")
        tr = quick_score(tr_trades)
        te = quick_score(te_trades)
        total = tr["pnl"] + te["pnl"]

        mc = monte_carlo_validate(strat, features, data, n_sims=500)
        status = "PASS" if mc["z"] >= 2.0 and tr["pnl"] > 0 and te["pnl"] > 0 else "FAIL"

        print(f"  {label:<50} "
              f"${tr['pnl']:>+6.0f}({tr['n']:>3}t) "
              f"${te['pnl']:>+6.0f}({te['n']:>3}t) "
              f"${total:>+7.0f}  "
              f"z={mc['z']:>5.2f} p={mc['p']:.3f} [{status}]")

    print("\n  Legend: PASS = z >= 2.0 AND profitable on BOTH train+test")
    print("  z-score: actual P&L vs random timing (500 sims)")


# ── Main ─────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print("=" * 78)
    print("  REGIME-BASED SIGNAL GATING BACKTEST")
    print("  Train: 2023-07 to 2024-12 | Test: 2025-01 to 2026-03")
    print("  Cost: 12 bps | Position: $250 | Max: 6 pos | Stop: -1500 bps")
    print("=" * 78)

    print("\n  Loading data...")
    data = load_3y_candles()
    print(f"  Loaded {len(data)} coins")

    print("  Building features...")
    features = build_features(data)
    print(f"  Features built for {len(features)} coins")
    t_load = time.time() - t0
    print(f"  Data loading took {t_load:.1f}s\n")

    part_a_regime_split(features, data)
    part_b_new_regime_signals(features, data)
    part_c_regime_filtered_s2(features, data)
    final_summary(features, data)

    elapsed = time.time() - t0
    print(f"\n  Total runtime: {elapsed:.0f}s")
    print("  Done.\n")


if __name__ == "__main__":
    main()
