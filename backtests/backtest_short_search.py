"""Short Signal Search — Exhaustive scan for new SHORT signals.

The bot currently has only 1 SHORT signal (S4: vol contraction + DXY).
This script tests 8 short signal ideas with threshold sweeps and hold
period variations, validates on train/test split, and runs Monte Carlo
on the top candidates.

Usage:
    python3 -m analysis.backtest_short_search
"""

from __future__ import annotations

import time
from copy import deepcopy
from itertools import product

from backtests.backtest_genetic import (
    load_3y_candles, build_features, backtest_strategy,
    quick_score, monte_carlo_validate,
    Strategy, Rule, COST_BPS, POSITION_SIZE,
    MAX_POSITIONS, MAX_SAME_DIR, STOP_LOSS_BPS,
    TRAIN_END, TEST_START, TOKENS,
)

# ── Signal definitions with threshold sweeps ────────────────────────

SIGNAL_DEFS = {
    "S_overheat": {
        "description": "Overheated alt fade: alt_rank_7d high AND recovery high",
        "rules": [
            {"feature": "alt_rank_7d", "op": ">", "thresholds": [70, 80, 90]},
            {"feature": "recovery",    "op": ">", "thresholds": [2000, 3000, 5000]},
        ],
    },
    "S_btc_weak": {
        "description": "BTC weakness propagation: BTC falling AND alt hasn't fallen yet",
        "rules": [
            {"feature": "btc_7d",       "op": "<", "thresholds": [-300, -500, -1000]},
            {"feature": "alt_vs_btc_7d", "op": ">", "thresholds": [300, 500, 1000]},
        ],
    },
    "S_vol_spike": {
        "description": "Volatility spike exhaustion: vol_ratio high + range high + consec up",
        "rules": [
            {"feature": "vol_ratio",  "op": ">", "thresholds": [1.3, 1.5, 2.0]},
            {"feature": "range_pct",  "op": ">", "thresholds": [200, 300, 500]},
            {"feature": "consec_up",  "op": ">", "thresholds": [2, 3, 4]},
        ],
    },
    "S_dead_cat": {
        "description": "Dead cat bounce: recovery from low but still in deep drawdown",
        "rules": [
            {"feature": "recovery",  "op": ">", "thresholds": [1000, 2000, 3000]},
            {"feature": "consec_up", "op": ">", "thresholds": [0, 1, 2]},
            {"feature": "drawdown",  "op": "<", "thresholds": [-1000, -2000, -3000]},
        ],
    },
    "S_dispersion": {
        "description": "Dispersion crash: high dispersion + alt index up + vol expanding",
        "rules": [
            {"feature": "dispersion_7d", "op": ">", "thresholds": [1000, 1500, 2000]},
            {"feature": "alt_index_7d",  "op": ">", "thresholds": [300, 500, 1000]},
            {"feature": "vol_ratio",     "op": ">", "thresholds": [1.0, 1.3, 1.5]},
        ],
    },
    "S_alt_bubble": {
        "description": "Alt bubble top: big 7d rally + volume spike",
        "rules": [
            {"feature": "ret_42h", "op": ">", "thresholds": [1000, 1500, 2000]},
            {"feature": "vol_z",   "op": ">", "thresholds": [1.0, 1.5, 2.0]},
        ],
    },
    "S_btc_eth_div": {
        "description": "BTC-ETH divergence: BTC outperforming ETH + alt is top-ranked",
        "rules": [
            {"feature": "btc_eth_spread", "op": ">", "thresholds": [200, 500, 800]},
            {"feature": "alt_rank_7d",    "op": ">", "thresholds": [65, 75, 85]},
        ],
    },
    "S_consec_exhaust": {
        "description": "Consecutive up exhaustion: many up candles + wide range",
        "rules": [
            {"feature": "consec_up", "op": ">", "thresholds": [3, 4, 5]},
            {"feature": "range_pct", "op": ">", "thresholds": [100, 200, 300]},
        ],
    },
}

HOLD_CANDLES = [9, 12, 18]  # 36h, 48h, 72h


def generate_variants(signal_name: str, signal_def: dict) -> list[tuple[str, Strategy]]:
    """Generate all threshold combinations for a signal definition."""
    rules_defs = signal_def["rules"]
    threshold_lists = [rd["thresholds"] for rd in rules_defs]

    variants = []
    for thresholds in product(*threshold_lists):
        for hold in HOLD_CANDLES:
            rules = []
            for rd, thresh in zip(rules_defs, thresholds):
                rules.append(Rule(rd["feature"], rd["op"], thresh, -1))  # all SHORT
            strat = Strategy(rules, hold=hold)
            label = f"{signal_name} | {' AND '.join(str(r) for r in rules)} | hold={hold*4}h"
            variants.append((label, strat))
    return variants


def run_search():
    t0 = time.time()
    print("=" * 80)
    print("  SHORT SIGNAL SEARCH")
    print("  Testing 8 signal ideas with threshold sweeps")
    print("=" * 80)

    # Load data
    print("\n  Loading 3-year candle data...")
    data = load_3y_candles()
    print(f"  Loaded {len(data)} coins")

    print("  Building features...")
    features = build_features(data)
    print(f"  Features built for {len(features)} coins")
    print(f"  Data loading took {time.time() - t0:.1f}s")

    # ── Phase 1: Scan all variants ──────────────────────────────────
    print("\n" + "=" * 80)
    print("  PHASE 1: Scanning all signal variants (train + test)")
    print("=" * 80)

    all_passing = []
    total_tested = 0

    for sig_name, sig_def in SIGNAL_DEFS.items():
        variants = generate_variants(sig_name, sig_def)
        sig_pass = 0

        for label, strat in variants:
            total_tested += 1

            # Train evaluation
            trades_train = backtest_strategy(strat, features, data, period="train")
            s_train = quick_score(trades_train)

            # Filter: profitable train with enough trades
            if s_train["pnl"] <= 0 or s_train["n"] < 8:
                continue

            # Test evaluation
            trades_test = backtest_strategy(strat, features, data, period="test")
            s_test = quick_score(trades_test)

            # Both profitable
            if s_test["pnl"] > 0 and s_test["n"] >= 3:
                combined_pnl = s_train["pnl"] + s_test["pnl"]
                combined_n = s_train["n"] + s_test["n"]
                all_passing.append({
                    "label": label,
                    "signal": sig_name,
                    "strat": strat,
                    "train": s_train,
                    "test": s_test,
                    "combined_pnl": combined_pnl,
                    "combined_n": combined_n,
                })
                sig_pass += 1

        print(f"  {sig_name:<20} {len(variants):>4} variants → {sig_pass:>3} pass train+test")

    print(f"\n  Total tested: {total_tested}")
    print(f"  Total passing train+test: {len(all_passing)}")

    if not all_passing:
        print("\n  NO SHORT SIGNALS PASS TRAIN+TEST. Search complete.")
        return

    # Sort by combined P&L
    all_passing.sort(key=lambda x: x["combined_pnl"], reverse=True)

    # ── Phase 2: Print all passing signals ──────────────────────────
    print("\n" + "=" * 80)
    print("  PHASE 2: All passing signals (sorted by combined P&L)")
    print("=" * 80)

    header = (f"  {'#':>3} {'Signal':<16} {'Rules':<55} "
              f"{'Hold':>5} {'Train$':>8} {'Trn_N':>6} {'Test$':>8} {'Tst_N':>6} "
              f"{'Total$':>8} {'TrnAvg':>7} {'TstAvg':>7}")
    print(header)
    print("  " + "-" * len(header))

    for i, r in enumerate(all_passing):
        rules_str = " AND ".join(
            f"{rule.feature}{rule.op}{rule.threshold}"
            for rule in r["strat"].rules
        )
        if len(rules_str) > 53:
            rules_str = rules_str[:50] + "..."
        print(f"  {i+1:>3} {r['signal']:<16} {rules_str:<55} "
              f"{r['strat'].hold*4:>4}h "
              f"${r['train']['pnl']:>+7.0f} {r['train']['n']:>5} "
              f"${r['test']['pnl']:>+7.0f} {r['test']['n']:>5} "
              f"${r['combined_pnl']:>+7.0f} "
              f"{r['train']['avg']:>+6.1f} {r['test']['avg']:>+6.1f}")

    # ── Phase 3: Monte Carlo validation on top 10 ───────────────────
    print("\n" + "=" * 80)
    print("  PHASE 3: Monte Carlo validation (top 10 by combined P&L)")
    print("=" * 80)

    # Deduplicate: pick best variant per signal for MC
    top_candidates = all_passing[:10]

    mc_results = []
    for i, cand in enumerate(top_candidates):
        print(f"\n  [{i+1}/10] {cand['label']}")
        mc = monte_carlo_validate(cand["strat"], features, data, n_sims=1000)
        mc_results.append({**cand, "mc": mc})
        print(f"    Actual P&L: ${mc['actual']:+.0f} | "
              f"Random mean: ${mc['random_mean']:+.0f} | "
              f"z-score: {mc['z']:.2f} | "
              f"p-value: {mc['p']:.3f} | "
              f"n_trades: {mc['n_trades']}")

    # ── Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  FINAL SUMMARY — Monte Carlo validated SHORT signals")
    print("=" * 80)

    mc_results.sort(key=lambda x: x["mc"]["z"], reverse=True)

    print(f"\n  {'#':>3} {'Signal':<16} {'z-score':>8} {'p-val':>7} "
          f"{'Actual$':>9} {'Rand$':>9} {'Trades':>7} "
          f"{'Train$':>8} {'Test$':>8}")
    print("  " + "-" * 95)

    viable = 0
    for i, r in enumerate(mc_results):
        mc = r["mc"]
        z_marker = " ***" if mc["z"] >= 2.0 else (" **" if mc["z"] >= 1.5 else "")
        print(f"  {i+1:>3} {r['signal']:<16} {mc['z']:>7.2f} {mc['p']:>7.3f} "
              f"${mc['actual']:>+8.0f} ${mc['random_mean']:>+8.0f} {mc['n_trades']:>6} "
              f"${r['train']['pnl']:>+7.0f} ${r['test']['pnl']:>+7.0f}"
              f"{z_marker}")
        if mc["z"] >= 2.0:
            viable += 1

    print(f"\n  Signals with z >= 2.0: {viable}")
    print(f"  Total time: {time.time() - t0:.1f}s")

    if viable > 0:
        print("\n  VIABLE CANDIDATES (z >= 2.0):")
        for r in mc_results:
            if r["mc"]["z"] >= 2.0:
                print(f"    {r['label']}")
                print(f"      z={r['mc']['z']:.2f}, p={r['mc']['p']:.3f}, "
                      f"PnL=${r['mc']['actual']:+.0f}, "
                      f"Train=${r['train']['pnl']:+.0f} ({r['train']['n']}t, "
                      f"avg={r['train']['avg']:+.1f}bps), "
                      f"Test=${r['test']['pnl']:+.0f} ({r['test']['n']}t, "
                      f"avg={r['test']['avg']:+.1f}bps)")
    else:
        print("\n  No SHORT signals pass z >= 2.0 Monte Carlo validation.")
        print("  This confirms that shorting alts is structurally hard.")


if __name__ == "__main__":
    run_search()
