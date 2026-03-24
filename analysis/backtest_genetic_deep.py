"""Deep analysis of top strategies from genetic search.

1. Monthly breakdown of top strategies
2. Bull vs bear period analysis
3. Signal frequency analysis (how often is it triggered?)
4. Combined LONG + SHORT portfolio
5. Additional hold period / stop loss optimization

Usage:
    python3 -m analysis.backtest_genetic_deep
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy,
    backtest_strategy, quick_score, monte_carlo_validate,
    TOKENS, COST_BPS, MAX_POSITIONS, MAX_SAME_DIR,
    STOP_LOSS_BPS, POSITION_SIZE, TRAIN_END, TEST_START,
)


def monthly_breakdown(trades, label=""):
    """Detailed monthly P&L analysis."""
    if not trades:
        print(f"  {label}: 0 trades")
        return

    by_month = defaultdict(lambda: {"pnl": 0, "n": 0, "wins": 0, "longs": 0, "shorts": 0})
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        m = dt.strftime("%Y-%m")
        by_month[m]["pnl"] += t["pnl"]
        by_month[m]["n"] += 1
        if t["net"] > 0:
            by_month[m]["wins"] += 1
        if t["direction"] == "LONG":
            by_month[m]["longs"] += 1
        else:
            by_month[m]["shorts"] += 1

    print(f"\n  {label}")
    print(f"  {'Month':<8} {'P&L':>8} {'Trades':>7} {'Win%':>5} {'L/S':>6} {'Cum':>8}")
    print(f"  {'-'*45}")

    cum = 0
    winning_months = 0
    for m in sorted(by_month):
        d = by_month[m]
        cum += d["pnl"]
        wr = d["wins"] / d["n"] * 100 if d["n"] > 0 else 0
        marker = "✓" if d["pnl"] > 0 else "✗"
        if d["pnl"] > 0:
            winning_months += 1
        print(f"  {m:<8} ${d['pnl']:>+7.1f} {d['n']:>5}t  {wr:>3.0f}% "
              f"{d['longs']:>2}L/{d['shorts']:>2}S ${cum:>+7.0f} {marker}")

    total_months = len(by_month)
    print(f"\n  Winning months: {winning_months}/{total_months} "
          f"({winning_months/total_months*100:.0f}%)")
    total_pnl = sum(d["pnl"] for d in by_month.values())
    print(f"  Total P&L: ${total_pnl:+.2f} | Monthly avg: ${total_pnl/total_months:+.1f}")

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for m in sorted(by_month):
        cum += by_month[m]["pnl"]
        peak = max(peak, cum)
        dd = cum - peak
        max_dd = min(max_dd, dd)
    print(f"  Max drawdown: ${max_dd:.0f}")


def signal_frequency(strategy, features, data):
    """How often does the signal fire?"""
    total_candles = 0
    signal_candles = 0

    for coin in TOKENS:
        if coin not in features:
            continue
        for f in features[coin]:
            total_candles += 1
            if strategy.signal(f) is not None:
                signal_candles += 1

    pct = signal_candles / total_candles * 100 if total_candles > 0 else 0
    print(f"  Signal frequency: {signal_candles}/{total_candles} candles "
          f"({pct:.1f}% of the time)")
    return pct


def period_analysis(strategy, features, data, label=""):
    """Analyze by sub-periods: bull 2024 vs bear 2025."""
    periods = {
        "2023-H2": (datetime(2023, 7, 1, tzinfo=timezone.utc).timestamp() * 1000,
                     datetime(2023, 12, 31, tzinfo=timezone.utc).timestamp() * 1000),
        "2024-H1": (datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000,
                     datetime(2024, 6, 30, tzinfo=timezone.utc).timestamp() * 1000),
        "2024-H2": (datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp() * 1000,
                     datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000),
        "2025-Q1": (datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000,
                     datetime(2025, 3, 31, tzinfo=timezone.utc).timestamp() * 1000),
        "2025-Q2+": (datetime(2025, 4, 1, tzinfo=timezone.utc).timestamp() * 1000,
                      datetime(2026, 3, 31, tzinfo=timezone.utc).timestamp() * 1000),
    }

    print(f"\n  Period breakdown: {label}")
    print(f"  {'Period':<10} {'P&L':>8} {'Trades':>7} {'Avg':>7} {'Win%':>5}")
    print(f"  {'-'*42}")

    for name, (start, end) in periods.items():
        period_trades = [t for t in backtest_strategy(strategy, features, data, period="all")
                        if start <= t["entry_t"] <= end]
        if not period_trades:
            print(f"  {name:<10} {'—':>8}")
            continue
        s = quick_score(period_trades)
        marker = "✓" if s["pnl"] > 0 else "✗"
        print(f"  {name:<10} ${s['pnl']:>+7.0f} {s['n']:>5}t {s['avg']:>+5.1f}bp "
              f"{s['win']:.0f}% {marker}")


def combined_portfolio(strategies, features, data, label=""):
    """Run multiple strategies simultaneously with shared position limits.

    Each strategy can open positions; total across all is capped at MAX_POSITIONS.
    """
    print(f"\n{'='*70}")
    print(f"  COMBINED PORTFOLIO: {label}")
    print(f"{'='*70}")

    coins = [c for c in TOKENS if c in features and c in data]
    all_trades = []

    # Collect all signals from all strategies
    events = []  # (timestamp, coin, direction, strategy_idx, features)
    for si, strat in enumerate(strategies):
        for coin in coins:
            if coin not in features:
                continue
            for f in features[coin]:
                direction = strat.signal(f)
                if direction is not None:
                    events.append((f["t"], coin, direction, si, f))

    events.sort(key=lambda x: x[0])

    # Group by timestamp
    by_ts = defaultdict(list)
    for t, coin, direction, si, f in events:
        by_ts[t].append((coin, direction, si, f))

    # Backtest with shared position limits
    positions = {}
    cooldown = {}
    max_hold = max(s.hold for s in strategies)

    for ts in sorted(by_ts.keys()):
        # Check exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            candles = data.get(coin, [])
            for ci in range(pos["entry_idx"], min(pos["entry_idx"] + max_hold + 5, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - pos["entry_idx"]
                    current = candles[ci]["c"]
                    if current <= 0:
                        break

                    exit_reason = None
                    exit_price = current

                    # Stop loss
                    if pos["direction"] == 1:
                        worst_bps = (candles[ci]["l"] / pos["entry_price"] - 1) * 1e4
                        if worst_bps < STOP_LOSS_BPS:
                            exit_reason = "stop"
                            exit_price = pos["entry_price"] * (1 + STOP_LOSS_BPS / 1e4)
                    else:
                        worst_bps = -(candles[ci]["h"] / pos["entry_price"] - 1) * 1e4
                        if worst_bps < STOP_LOSS_BPS:
                            exit_reason = "stop"
                            exit_price = pos["entry_price"] * (1 - STOP_LOSS_BPS / 1e4)

                    if held >= pos["hold"]:
                        exit_reason = "timeout"

                    if exit_reason:
                        gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                        net = gross - COST_BPS
                        pnl = POSITION_SIZE * net / 1e4
                        all_trades.append({
                            "coin": coin,
                            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                            "entry_t": pos["entry_t"],
                            "exit_t": ts,
                            "hold": held,
                            "gross": round(gross, 1),
                            "net": round(net, 1),
                            "pnl": round(pnl, 2),
                            "reason": exit_reason,
                            "strat": pos["strat_name"],
                        })
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000
                    break

        # Check entries
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for coin, direction, si, f in by_ts[ts]:
            if coin in positions:
                continue
            if coin in cooldown and ts < cooldown[coin]:
                continue

            if direction == 1 and n_long >= MAX_SAME_DIR:
                continue
            if direction == -1 and n_short >= MAX_SAME_DIR:
                continue

            idx = f["_idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry_price = data[coin][idx + 1]["o"]
            if entry_price <= 0:
                continue

            candidates.append({
                "coin": coin, "direction": direction,
                "entry_price": entry_price,
                "entry_idx": idx + 1,
                "entry_t": data[coin][idx + 1]["t"],
                "strength": abs(f.get("ret_42h", 0)),
                "strat_idx": si,
                "hold": strategies[si].hold,
            })

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = MAX_POSITIONS - len(positions)

        for cand in candidates[:slots]:
            positions[cand["coin"]] = {
                "direction": cand["direction"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_t": cand["entry_t"],
                "hold": cand["hold"],
                "strat_name": f"S{cand['strat_idx']}",
            }

    # Analyze
    s = quick_score(all_trades)
    print(f"\n  Total: ${s['pnl']:+.0f} | {s['n']} trades | "
          f"avg {s['avg']:+.1f} bps | win {s['win']:.0f}%")

    # Per-strategy breakdown
    by_strat = defaultdict(list)
    for t in all_trades:
        by_strat[t.get("strat", "?")].append(t)
    for sname in sorted(by_strat):
        st = by_strat[sname]
        ss = quick_score(st)
        longs = sum(1 for t in st if t["direction"] == "LONG")
        shorts = len(st) - longs
        print(f"    {sname}: ${ss['pnl']:>+7.0f} ({ss['n']:>3}t, {longs}L/{shorts}S, "
              f"avg={ss['avg']:>+5.1f}bps)")

    monthly_breakdown(all_trades, label)
    return all_trades


def main():
    print("=" * 70)
    print("  DEEP ANALYSIS — Top Strategy Verification")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    print("\nBuilding features...")
    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # ═══════════════════════════════════════════════════════════════
    # Define top strategies
    # ═══════════════════════════════════════════════════════════════

    # SHORT: volatility contraction
    s_short = Strategy([
        Rule("vol_ratio", "<", 1.0, -1),
        Rule("range_pct", "<", 200, -1),
    ], hold=18)  # 72h

    # LONG: alt market crash
    s_alt_crash = Strategy([
        Rule("alt_index_7d", "<", -1000, 1),
    ], hold=18)

    # LONG: BTC rip
    s_btc_rip = Strategy([
        Rule("btc_30d", ">", 2000, 1),
    ], hold=42)  # 168h = 7 days (best hold from optimization)

    # LONG: BTC dip + alt drop
    s_btc_dip_alt = Strategy([
        Rule("btc_7d", "<", -500, 1),
        Rule("ret_42h", "<", -2000, 1),
    ], hold=24)  # 96h (best hold from optimization)

    # LONG: BTC dip (simple)
    s_btc_dip = Strategy([
        Rule("btc_7d", "<", -500, 1),
    ], hold=18)

    strategies = {
        "SHORT vol_contraction": s_short,
        "LONG alt_crash": s_alt_crash,
        "LONG btc_rip": s_btc_rip,
        "LONG btc_dip+alt": s_btc_dip_alt,
        "LONG btc_dip": s_btc_dip,
    }

    # ═══════════════════════════════════════════════════════════════
    # Analysis 1: Signal frequency (is it always triggered?)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  SIGNAL FREQUENCY ANALYSIS")
    print(f"{'='*70}")

    for name, strat in strategies.items():
        print(f"\n  {name}:")
        signal_frequency(strat, features, data)

    # ═══════════════════════════════════════════════════════════════
    # Analysis 2: Period breakdown (bull vs bear)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  PERIOD BREAKDOWN (Bull vs Bear)")
    print(f"{'='*70}")

    for name, strat in strategies.items():
        trades = backtest_strategy(strat, features, data, period="all")
        monthly_breakdown(trades, name)
        period_analysis(strat, features, data, name)

    # ═══════════════════════════════════════════════════════════════
    # Analysis 3: Combined portfolio (LONG + SHORT)
    # ═══════════════════════════════════════════════════════════════

    # Combo 1: SHORT vol contraction + LONG alt crash
    combo1 = combined_portfolio(
        [s_short, s_alt_crash],
        features, data,
        "SHORT vol_contraction + LONG alt_crash"
    )

    # Combo 2: SHORT vol contraction + LONG btc_rip + LONG btc_dip
    combo2 = combined_portfolio(
        [s_short, s_btc_rip, s_btc_dip],
        features, data,
        "SHORT vol_contraction + LONG btc_rip + LONG btc_dip"
    )

    # Combo 3: All strategies
    combo3 = combined_portfolio(
        [s_short, s_alt_crash, s_btc_rip, s_btc_dip_alt],
        features, data,
        "ALL 4 strategies"
    )

    # Combo 4: Just the confirmed ones (z>2)
    combo4 = combined_portfolio(
        [s_short, s_alt_crash, s_btc_rip],
        features, data,
        "TOP 3 (z>2.5 each)"
    )

    # ═══════════════════════════════════════════════════════════════
    # Analysis 4: Monte Carlo on best combo
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'▓'*70}")
    print(f"  MONTE CARLO — Best combo validation")
    print(f"{'▓'*70}")

    # For MC, we need to test the combined trades
    # Use the strategy with best combined result
    best_combos = [
        ("SHORT + alt_crash", [s_short, s_alt_crash]),
        ("SHORT + btc_rip + btc_dip", [s_short, s_btc_rip, s_btc_dip]),
        ("TOP 3", [s_short, s_alt_crash, s_btc_rip]),
    ]

    for label, strats in best_combos:
        print(f"\n  {label}:")
        # Run combined and get trades for MC
        all_trades = []
        for strat in strats:
            trades = backtest_strategy(strat, features, data, period="all")
            all_trades.extend(trades)

        actual_pnl = sum(t["pnl"] for t in all_trades)
        n_trades = len(all_trades)

        # Direction-matched MC
        by_coin = defaultdict(lambda: {"long": 0, "short": 0})
        for t in all_trades:
            by_coin[t["coin"]]["long" if t["direction"] == "LONG" else "short"] += 1

        avg_hold = max(1, int(np.mean([s.hold for s in strats])))
        sim_pnls = []
        for _ in range(500):
            sim_total = 0
            for coin, counts in by_coin.items():
                if coin not in data:
                    continue
                candles = data[coin]
                n_candles = len(candles)
                if n_candles < 200:
                    continue
                available = list(range(180, n_candles - avg_hold - 1))
                n_needed = counts["long"] + counts["short"]
                if len(available) < n_needed:
                    continue
                sampled = random.sample(available, n_needed)
                for j, idx in enumerate(sampled):
                    direction = 1 if j < counts["long"] else -1
                    entry = candles[min(idx + 1, n_candles - 1)]["o"]
                    exit_idx = min(idx + 1 + avg_hold, n_candles - 1)
                    exit_p = candles[exit_idx]["c"]
                    if entry <= 0:
                        continue
                    gross = direction * (exit_p / entry - 1) * 1e4
                    net = gross - COST_BPS
                    sim_total += POSITION_SIZE * net / 1e4
            sim_pnls.append(sim_total)

        sim_mean = np.mean(sim_pnls)
        sim_std = np.std(sim_pnls)
        z = (actual_pnl - sim_mean) / sim_std if sim_std > 0 else 0

        print(f"    Actual:      ${actual_pnl:>+.0f} ({n_trades} trades)")
        print(f"    Random mean: ${sim_mean:>+.0f} (std: ${sim_std:.0f})")
        print(f"    Z-score:     {z:+.2f}")
        if z > 2.5:
            print(f"    → ✓✓ STRONGLY SIGNIFICANT")
        elif z > 2.0:
            print(f"    → ✓ SIGNIFICANT")
        else:
            print(f"    → ⚠ MARGINAL")


if __name__ == "__main__":
    main()
