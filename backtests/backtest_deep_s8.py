"""Deep exploration of S8 capitulation flush theme.

Base S8: drawdown < -4000 AND vol_z > 1.0 AND ret_6h < -50 -> LONG 48h (z=5.47)

Explores:
  A. S8 + 4th condition (refine capitulation)
  B. Softer drawdown thresholds with tighter filters
  C. Recovery timing variants (buy bottom vs. first bounce)
  D. Hold period deep sweep (6-24 candles = 24h-96h)

Usage:
    python3 -m analysis.backtest_deep_s8
"""

from __future__ import annotations

import time
from copy import deepcopy

from backtests.backtest_genetic import (
    load_3y_candles, build_features, backtest_strategy,
    quick_score, monte_carlo_validate,
    Strategy, Rule, COST_BPS, POSITION_SIZE,
    MAX_POSITIONS, MAX_SAME_DIR, STOP_LOSS_BPS,
    TRAIN_END, TEST_START, TOKENS,
)


# ── Helpers ──────────────────────────────────────────────────────────

def make_strategy(rules_defs: list[tuple], hold: int = 12) -> Strategy:
    """Build a Strategy from (feature, op, threshold, direction) tuples."""
    rules = [Rule(feat, op, thresh, dirn) for feat, op, thresh, dirn in rules_defs]
    return Strategy(rules, hold=hold)


def evaluate(name: str, strat: Strategy, features: dict, data: dict,
             min_trades: int = 8) -> dict | None:
    """Run train/test split. Returns result dict if both profitable with >= min_trades."""
    trades_train = backtest_strategy(strat, features, data, period="train")
    s_train = quick_score(trades_train)

    if s_train["n"] < min_trades or s_train["pnl"] <= 0:
        return None

    trades_test = backtest_strategy(strat, features, data, period="test")
    s_test = quick_score(trades_test)

    if s_test["n"] < min_trades or s_test["pnl"] <= 0:
        return None

    trades_all = backtest_strategy(strat, features, data, period="all")
    s_all = quick_score(trades_all)

    return {
        "name": name,
        "strat": strat,
        "train": s_train,
        "test": s_test,
        "all": s_all,
        "combined_pnl": s_train["pnl"] + s_test["pnl"],
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print("=" * 80)
    print("  S8 CAPITULATION FLUSH — Deep Exploration")
    print("=" * 80)

    print("\n  Loading 3-year candles...")
    data = load_3y_candles()
    print(f"  Loaded {len(data)} coins")

    print("  Building features...")
    features = build_features(data)
    print(f"  Features built for {len(features)} coins")

    # ── Base S8 (reference) ──────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  BASELINE: S8 (drawdown < -4000 AND vol_z > 1.0 AND ret_6h < -50 -> LONG 48h)")
    print("=" * 80)

    base_rules = [
        ("drawdown", "<", -4000, 1),
        ("vol_z", ">", 1.0, 1),
        ("ret_6h", "<", -50, 1),
    ]
    base_s8 = make_strategy(base_rules, hold=12)  # 12 candles = 48h
    base_result = evaluate("S8_base", base_s8, features, data, min_trades=5)
    if base_result:
        s = base_result
        print(f"  Train: ${s['train']['pnl']:>+8.0f}  ({s['train']['n']:>3} trades, "
              f"avg {s['train']['avg']:>+6.1f} bps, win {s['train']['win']:.0f}%)")
        print(f"  Test:  ${s['test']['pnl']:>+8.0f}  ({s['test']['n']:>3} trades, "
              f"avg {s['test']['avg']:>+6.1f} bps, win {s['test']['win']:.0f}%)")
        print(f"  All:   ${s['all']['pnl']:>+8.0f}  ({s['all']['n']:>3} trades)")
    else:
        print("  WARNING: Base S8 does not pass train/test split!")

    # Collect all passing variants
    passing = []
    if base_result:
        passing.append(base_result)

    # ── A. S8 + 4th condition ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  A. S8 + 4th CONDITION (refine capitulation)")
    print("=" * 80)

    extra_conditions_a = [
        # Alt underperforming BTC
        ("alt_vs_btc_7d", "<", -1000, "alt_vs_btc<-1000"),
        ("alt_vs_btc_7d", "<", -1500, "alt_vs_btc<-1500"),
        ("alt_vs_btc_7d", "<", -500,  "alt_vs_btc<-500"),
        # Market scattered
        ("dispersion_7d", ">", 1000, "disp>1000"),
        ("dispersion_7d", ">", 1500, "disp>1500"),
        ("dispersion_7d", ">", 750,  "disp>750"),
        # Bottom quartile performer
        ("alt_rank_7d", "<", 30, "rank<30"),
        ("alt_rank_7d", "<", 20, "rank<20"),
        ("alt_rank_7d", "<", 40, "rank<40"),
        ("alt_rank_7d", "<", 10, "rank<10"),
        # Not bouncing yet
        ("recovery", "<", 500, "recovery<500"),
        ("recovery", "<", 300, "recovery<300"),
        ("recovery", "<", 750, "recovery<750"),
        ("recovery", "<", 1000, "recovery<1000"),
        # Alt index also crushed (market-wide crash)
        ("alt_index_7d", "<", -500,  "alt_idx<-500"),
        ("alt_index_7d", "<", -1000, "alt_idx<-1000"),
        # BTC also weak
        ("btc_7d", "<", -500,  "btc7d<-500"),
        ("btc_7d", "<", -300,  "btc7d<-300"),
        # More extreme volume
        ("vol_z", ">", 2.0, "vol_z>2.0"),
        ("vol_z", ">", 1.5, "vol_z>1.5"),
        # Longer term also down
        ("ret_84h", "<", -1000, "ret14d<-1000"),
        ("ret_84h", "<", -2000, "ret14d<-2000"),
        ("ret_42h", "<", -1000, "ret7d<-1000"),
        ("ret_42h", "<", -2000, "ret7d<-2000"),
        # Multiple consecutive down candles
        ("consec_dn", ">", 3, "consec_dn>3"),
        ("consec_dn", ">", 4, "consec_dn>4"),
    ]

    tested_a = 0
    passed_a = 0
    print(f"\n  {'Variant':<55} {'Train':>14} {'Test':>14} {'Total':>8}")
    print(f"  {'-' * 95}")

    for feat, op, thresh, label in extra_conditions_a:
        tested_a += 1
        rules = base_rules + [(feat, op, thresh, 1)]
        strat = make_strategy(rules, hold=12)
        name = f"S8+{label}"
        result = evaluate(name, strat, features, data)
        if result:
            passed_a += 1
            passing.append(result)
            t = result["train"]
            s = result["test"]
            print(f"  {name:<55} "
                  f"${t['pnl']:>+7.0f} ({t['n']:>3}t {t['avg']:>+5.0f}bps) "
                  f"${s['pnl']:>+7.0f} ({s['n']:>3}t {s['avg']:>+5.0f}bps) "
                  f"${result['combined_pnl']:>+7.0f}")

    print(f"\n  Section A: tested {tested_a}, passed {passed_a}")

    # ── B. Softer drawdown thresholds ────────────────────────────────
    print("\n" + "=" * 80)
    print("  B. SOFTER DRAWDOWN THRESHOLDS (more trades)")
    print("=" * 80)

    softer_configs = [
        # (drawdown_thresh, extra_conditions, label)
        # -3000 variants (30% drawdown)
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -50, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-50"),
        (-3000, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -50, 1)],
         "dd<-3000 vol_z>1.5 ret6h<-50"),
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -100, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-100"),
        (-3000, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -100, 1)],
         "dd<-3000 vol_z>1.5 ret6h<-100"),
        (-3000, [("vol_z", ">", 2.0, 1), ("ret_6h", "<", -50, 1)],
         "dd<-3000 vol_z>2.0 ret6h<-50"),
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -200, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-200"),
        # -3000 + extra filter
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -50, 1), ("alt_rank_7d", "<", 30, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-50 rank<30"),
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -50, 1), ("recovery", "<", 500, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-50 recov<500"),
        (-3000, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -50, 1), ("alt_vs_btc_7d", "<", -1000, 1)],
         "dd<-3000 vol_z>1.0 ret6h<-50 altbtc<-1000"),
        (-3000, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -100, 1), ("alt_rank_7d", "<", 30, 1)],
         "dd<-3000 vol_z>1.5 ret6h<-100 rank<30"),

        # -2500 variants (25% drawdown)
        (-2500, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -100, 1)],
         "dd<-2500 vol_z>1.5 ret6h<-100"),
        (-2500, [("vol_z", ">", 2.0, 1), ("ret_6h", "<", -100, 1)],
         "dd<-2500 vol_z>2.0 ret6h<-100"),
        (-2500, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -200, 1)],
         "dd<-2500 vol_z>1.5 ret6h<-200"),
        (-2500, [("vol_z", ">", 2.0, 1), ("ret_6h", "<", -200, 1)],
         "dd<-2500 vol_z>2.0 ret6h<-200"),
        (-2500, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -100, 1), ("alt_rank_7d", "<", 30, 1)],
         "dd<-2500 vol_z>1.5 ret6h<-100 rank<30"),
        (-2500, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -100, 1), ("recovery", "<", 500, 1)],
         "dd<-2500 vol_z>1.0 ret6h<-100 recov<500"),

        # -3500 (between base and softer)
        (-3500, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -50, 1)],
         "dd<-3500 vol_z>1.0 ret6h<-50"),
        (-3500, [("vol_z", ">", 1.5, 1), ("ret_6h", "<", -50, 1)],
         "dd<-3500 vol_z>1.5 ret6h<-50"),
        (-3500, [("vol_z", ">", 1.0, 1), ("ret_6h", "<", -100, 1)],
         "dd<-3500 vol_z>1.0 ret6h<-100"),
    ]

    tested_b = 0
    passed_b = 0
    print(f"\n  {'Variant':<55} {'Train':>14} {'Test':>14} {'Total':>8}")
    print(f"  {'-' * 95}")

    for dd_thresh, extra_conds, label in softer_configs:
        tested_b += 1
        rules = [("drawdown", "<", dd_thresh, 1)] + extra_conds
        strat = make_strategy(rules, hold=12)
        name = f"S8soft_{label}"
        result = evaluate(name, strat, features, data)
        if result:
            passed_b += 1
            passing.append(result)
            t = result["train"]
            s = result["test"]
            print(f"  {name:<55} "
                  f"${t['pnl']:>+7.0f} ({t['n']:>3}t {t['avg']:>+5.0f}bps) "
                  f"${s['pnl']:>+7.0f} ({s['n']:>3}t {s['avg']:>+5.0f}bps) "
                  f"${result['combined_pnl']:>+7.0f}")

    print(f"\n  Section B: tested {tested_b}, passed {passed_b}")

    # ── C. Recovery timing variants ──────────────────────────────────
    print("\n" + "=" * 80)
    print("  C. RECOVERY TIMING VARIANTS")
    print("=" * 80)

    recovery_configs = [
        # Buy AT the extreme bottom (very negative 24h return)
        ([("drawdown", "<", -3000, 1), ("vol_z", ">", 1.0, 1), ("ret_6h", "<", -200, 1)],
         "extreme_bottom_dd3000_ret6h<-200"),
        ([("drawdown", "<", -3000, 1), ("vol_z", ">", 1.0, 1), ("ret_6h", "<", -300, 1)],
         "extreme_bottom_dd3000_ret6h<-300"),
        ([("drawdown", "<", -4000, 1), ("vol_z", ">", 1.0, 1), ("ret_6h", "<", -200, 1)],
         "extreme_bottom_dd4000_ret6h<-200"),
        ([("drawdown", "<", -4000, 1), ("vol_z", ">", 1.0, 1), ("ret_6h", "<", -300, 1)],
         "extreme_bottom_dd4000_ret6h<-300"),

        # Buy AFTER first bounce (ret_6h positive but still in drawdown)
        ([("drawdown", "<", -3000, 1), ("ret_6h", ">", 0, 1), ("vol_z", ">", 1.0, 1)],
         "first_bounce_dd3000_ret6h>0"),
        ([("drawdown", "<", -3000, 1), ("ret_6h", ">", 50, 1), ("vol_z", ">", 1.0, 1)],
         "first_bounce_dd3000_ret6h>50"),
        ([("drawdown", "<", -4000, 1), ("ret_6h", ">", 0, 1), ("vol_z", ">", 1.0, 1)],
         "first_bounce_dd4000_ret6h>0"),
        ([("drawdown", "<", -4000, 1), ("ret_6h", ">", 50, 1)],
         "first_bounce_dd4000_ret6h>50"),
        ([("drawdown", "<", -3500, 1), ("ret_6h", ">", 0, 1), ("vol_z", ">", 1.0, 1)],
         "first_bounce_dd3500_ret6h>0"),

        # Early recovery: drawdown still deep but recovery has started
        ([("drawdown", "<", -3000, 1), ("recovery", ">", 500, 1), ("recovery", "<", 1500, 1)],
         "early_recov_dd3000_500<recov<1500"),
        ([("drawdown", "<", -3000, 1), ("recovery", ">", 300, 1), ("recovery", "<", 1000, 1)],
         "early_recov_dd3000_300<recov<1000"),
        ([("drawdown", "<", -3500, 1), ("recovery", ">", 500, 1), ("recovery", "<", 1500, 1)],
         "early_recov_dd3500_500<recov<1500"),
        ([("drawdown", "<", -4000, 1), ("recovery", ">", 500, 1), ("recovery", "<", 2000, 1)],
         "early_recov_dd4000_500<recov<2000"),
        ([("drawdown", "<", -3000, 1), ("recovery", ">", 500, 1), ("recovery", "<", 1500, 1),
          ("vol_z", ">", 1.0, 1)],
         "early_recov_dd3000_500<recov<1500_volz>1"),

        # Deep bottom + consecutive down candles (panic selling)
        ([("drawdown", "<", -3000, 1), ("consec_dn", ">", 3, 1), ("vol_z", ">", 1.0, 1)],
         "panic_dd3000_consec>3_volz>1"),
        ([("drawdown", "<", -3000, 1), ("consec_dn", ">", 4, 1)],
         "panic_dd3000_consec>4"),
        ([("drawdown", "<", -4000, 1), ("consec_dn", ">", 3, 1)],
         "panic_dd4000_consec>3"),
    ]

    tested_c = 0
    passed_c = 0
    print(f"\n  {'Variant':<55} {'Train':>14} {'Test':>14} {'Total':>8}")
    print(f"  {'-' * 95}")

    for rules_def, label in recovery_configs:
        tested_c += 1
        strat = make_strategy(rules_def, hold=12)
        name = f"S8recov_{label}"
        result = evaluate(name, strat, features, data)
        if result:
            passed_c += 1
            passing.append(result)
            t = result["train"]
            s = result["test"]
            print(f"  {name:<55} "
                  f"${t['pnl']:>+7.0f} ({t['n']:>3}t {t['avg']:>+5.0f}bps) "
                  f"${s['pnl']:>+7.0f} ({s['n']:>3}t {s['avg']:>+5.0f}bps) "
                  f"${result['combined_pnl']:>+7.0f}")

    print(f"\n  Section C: tested {tested_c}, passed {passed_c}")

    # ── D. Hold period deep sweep ────────────────────────────────────
    print("\n" + "=" * 80)
    print("  D. HOLD PERIOD DEEP SWEEP (on base S8 and top variants)")
    print("=" * 80)

    hold_candles = [6, 9, 12, 15, 18, 24]  # 24h, 36h, 48h, 60h, 72h, 96h
    hold_hours = {6: "24h", 9: "36h", 12: "48h", 15: "60h", 18: "72h", 24: "96h"}

    # Test hold periods on base S8
    print(f"\n  Base S8 hold sweep:")
    print(f"  {'Hold':<8} {'Train':>14} {'Test':>14} {'Total':>8}")
    print(f"  {'-' * 50}")

    tested_d = 0
    passed_d = 0
    for h in hold_candles:
        tested_d += 1
        strat = make_strategy(base_rules, hold=h)
        name = f"S8_hold{hold_hours[h]}"
        result = evaluate(name, strat, features, data, min_trades=5)
        if result:
            passed_d += 1
            if h != 12:  # Don't double-count base
                passing.append(result)
            t = result["train"]
            s = result["test"]
            marker = " <-- base" if h == 12 else ""
            print(f"  {hold_hours[h]:<8} "
                  f"${t['pnl']:>+7.0f} ({t['n']:>3}t {t['avg']:>+5.0f}bps) "
                  f"${s['pnl']:>+7.0f} ({s['n']:>3}t {s['avg']:>+5.0f}bps) "
                  f"${result['combined_pnl']:>+7.0f}{marker}")
        else:
            print(f"  {hold_hours[h]:<8}  FAIL (train or test not profitable or n < 5)")

    # Also sweep hold on top passing variants from A/B/C
    # Pick top 5 by combined PnL (excluding base)
    non_base = [r for r in passing if r["name"] != "S8_base"]
    non_base.sort(key=lambda x: x["combined_pnl"], reverse=True)
    top_variants = non_base[:5]

    for variant in top_variants:
        orig_strat = variant["strat"]
        orig_rules_def = [(r.feature, r.op, r.threshold, r.direction)
                          for r in orig_strat.rules]
        print(f"\n  Hold sweep for {variant['name']}:")
        print(f"  {'Hold':<8} {'Train':>14} {'Test':>14} {'Total':>8}")
        print(f"  {'-' * 50}")

        for h in hold_candles:
            tested_d += 1
            strat = make_strategy(orig_rules_def, hold=h)
            name = f"{variant['name']}_h{hold_hours[h]}"
            result = evaluate(name, strat, features, data)
            if result:
                passed_d += 1
                passing.append(result)
                t = result["train"]
                s = result["test"]
                marker = " <-- orig" if h == orig_strat.hold else ""
                print(f"  {hold_hours[h]:<8} "
                      f"${t['pnl']:>+7.0f} ({t['n']:>3}t {t['avg']:>+5.0f}bps) "
                      f"${s['pnl']:>+7.0f} ({s['n']:>3}t {s['avg']:>+5.0f}bps) "
                      f"${result['combined_pnl']:>+7.0f}{marker}")
            else:
                print(f"  {hold_hours[h]:<8}  FAIL")

    print(f"\n  Section D: tested {tested_d}, passed {passed_d}")

    # ── SUMMARY + Monte Carlo ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SUMMARY: All passing variants (train & test profitable, n >= 8)")
    print("=" * 80)

    # Deduplicate by name (keep best combined PnL)
    seen = {}
    for r in passing:
        key = r["name"]
        if key not in seen or r["combined_pnl"] > seen[key]["combined_pnl"]:
            seen[key] = r
    passing = list(seen.values())

    # Sort by combined PnL for display
    passing.sort(key=lambda x: x["combined_pnl"], reverse=True)

    print(f"\n  {len(passing)} variants passed train/test split")
    print(f"\n  {'#':<3} {'Variant':<55} {'Train PnL':>10} {'Test PnL':>10} {'N(all)':>7} {'Avg bps':>8}")
    print(f"  {'-' * 95}")

    for i, r in enumerate(passing[:30]):
        print(f"  {i+1:<3} {r['name']:<55} "
              f"${r['train']['pnl']:>+8.0f} ${r['test']['pnl']:>+8.0f} "
              f"{r['all']['n']:>6} {r['all']['avg']:>+7.1f}")

    # ── Monte Carlo on top variants ──────────────────────────────────
    print("\n" + "=" * 80)
    print("  MONTE CARLO VALIDATION (top 20 variants, 500 sims each)")
    print("=" * 80)

    mc_results = []
    for r in passing[:20]:
        print(f"  Running MC for {r['name']}...", end="", flush=True)
        mc = monte_carlo_validate(r["strat"], features, data, n_sims=500)
        mc["name"] = r["name"]
        mc["strat"] = r["strat"]
        mc["train"] = r["train"]
        mc["test"] = r["test"]
        mc["all"] = r["all"]
        mc_results.append(mc)
        print(f" z={mc['z']:.2f}, p={mc['p']:.4f}")

    # Sort by z-score
    mc_results.sort(key=lambda x: x["z"], reverse=True)

    print(f"\n  {'#':<3} {'Variant':<55} {'z':>6} {'p':>7} {'PnL':>8} {'N':>5} "
          f"{'Train$':>8} {'Test$':>8}")
    print(f"  {'-' * 105}")

    for i, mc in enumerate(mc_results):
        z_marker = " ***" if mc["z"] >= 3.0 else (" **" if mc["z"] >= 2.0 else "")
        print(f"  {i+1:<3} {mc['name']:<55} "
              f"{mc['z']:>5.2f} {mc['p']:>6.4f} "
              f"${mc['actual']:>+7.0f} {mc['n_trades']:>4} "
              f"${mc['train']['pnl']:>+7.0f} ${mc['test']['pnl']:>+7.0f}"
              f"{z_marker}")

    # ── Best variant details ─────────────────────────────────────────
    if mc_results:
        best = mc_results[0]
        print(f"\n" + "=" * 80)
        print(f"  BEST VARIANT: {best['name']}")
        print(f"=" * 80)
        print(f"  Strategy: {best['strat']}")
        print(f"  z-score: {best['z']:.2f}  (p={best['p']:.4f})")
        print(f"  Total PnL: ${best['actual']:>+.0f}  ({best['n_trades']} trades)")
        print(f"  Train:     ${best['train']['pnl']:>+.0f}  ({best['train']['n']} trades, "
              f"avg {best['train']['avg']:>+.1f} bps, win {best['train']['win']:.0f}%)")
        print(f"  Test:      ${best['test']['pnl']:>+.0f}  ({best['test']['n']} trades, "
              f"avg {best['test']['avg']:>+.1f} bps, win {best['test']['win']:.0f}%)")
        print(f"  Random baseline: ${best['random_mean']:>+.0f} +/- ${best['random_std']:.0f}")

    elapsed = time.time() - t0
    print(f"\n  Total time: {elapsed:.1f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()
