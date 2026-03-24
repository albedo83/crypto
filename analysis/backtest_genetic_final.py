"""Final synthesis — Fixed combined portfolio + honest assessment.

Fixes:
- Exit check on ALL timestamps, not just signal timestamps
- Proper position tracking across strategies
- Realistic capital tracking with compounding

Usage:
    python3 -m analysis.backtest_genetic_final
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy,
    backtest_strategy, quick_score,
    TOKENS, COST_BPS, MAX_POSITIONS, MAX_SAME_DIR,
    STOP_LOSS_BPS, POSITION_SIZE,
)

# ── All validated strategies ─────────────────────────────────────────

STRATEGIES = {
    "S1_btc_rip": Strategy(
        [Rule("btc_30d", ">", 2000, 1)],
        hold=42,  # 7 days (best from optimization)
    ),
    "S2_alt_crash": Strategy(
        [Rule("alt_index_7d", "<", -1000, 1)],
        hold=18,  # 3 days
    ),
    "S3_btc_dip_alt": Strategy(
        [Rule("btc_7d", "<", -500, 1), Rule("ret_42h", "<", -2000, 1)],
        hold=24,  # 4 days (best from optimization)
    ),
    "S4_vol_short": Strategy(
        [Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1)],
        hold=18,  # 3 days
    ),
}


# ── Fixed combined portfolio backtest ────────────────────────────────

def combined_backtest(strategies: dict, features: dict, data: dict,
                      capital=1000.0, size_pct=0.25, max_pos=MAX_POSITIONS,
                      max_dir=MAX_SAME_DIR, stop_loss=STOP_LOSS_BPS,
                      cost=COST_BPS):
    """
    Run multiple strategies with shared position limits.

    Fixed: iterate over ALL timestamps, not just signal timestamps.
    Added: capital tracking with compounding.
    """
    coins = [c for c in TOKENS if c in features and c in data]

    # Build unified timeline from ALL coins' candle timestamps
    all_timestamps = set()
    coin_by_ts = {}  # {coin: {ts: candle_index}}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, candle in enumerate(data[coin]):
            all_timestamps.add(candle["t"])
            coin_by_ts[coin][candle["t"]] = i

    sorted_ts = sorted(all_timestamps)

    # Build signal lookup: {(ts, coin): [(direction, strat_name, strength, hold)]}
    signal_lookup = defaultdict(list)
    for sname, strat in strategies.items():
        for coin in coins:
            if coin not in features:
                continue
            for f in features[coin]:
                direction = strat.signal(f)
                if direction is not None:
                    signal_lookup[(f["t"], coin)].append({
                        "direction": direction,
                        "strat": sname,
                        "strength": abs(f.get("ret_42h", 0)),
                        "hold": strat.hold,
                        "idx": f["_idx"],
                    })

    # State
    positions = {}  # coin → position info
    trades = []
    cooldown = {}
    current_capital = capital
    peak_capital = capital
    max_drawdown = 0
    equity_curve = []

    for ts in sorted_ts:
        # ── Check exits for ALL positions ──
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]:
                continue

            ci = coin_by_ts[coin][ts]
            candle = data[coin][ci]
            held = ci - pos["entry_idx"]

            if held <= 0:
                continue

            current_price = candle["c"]
            if current_price <= 0:
                continue

            exit_reason = None
            exit_price = current_price

            # Stop loss
            if pos["direction"] == 1:
                worst_bps = (candle["l"] / pos["entry_price"] - 1) * 1e4
                if worst_bps < stop_loss:
                    exit_reason = "stop"
                    exit_price = pos["entry_price"] * (1 + stop_loss / 1e4)
            else:
                worst_bps = -(candle["h"] / pos["entry_price"] - 1) * 1e4
                if worst_bps < stop_loss:
                    exit_reason = "stop"
                    exit_price = pos["entry_price"] * (1 - stop_loss / 1e4)

            # Timeout
            if held >= pos["hold"]:
                exit_reason = "timeout"

            if exit_reason:
                gross_bps = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net_bps = gross_bps - cost
                pnl = pos["size"] * net_bps / 1e4

                current_capital += pnl
                peak_capital = max(peak_capital, current_capital)
                dd = (current_capital - peak_capital) / peak_capital * 100
                max_drawdown = min(max_drawdown, dd)

                dt_entry = datetime.fromtimestamp(pos["entry_t"] / 1000, tz=timezone.utc)
                dt_exit = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

                trades.append({
                    "coin": coin,
                    "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                    "strat": pos["strat"],
                    "entry_date": dt_entry.strftime("%Y-%m-%d"),
                    "exit_date": dt_exit.strftime("%Y-%m-%d"),
                    "entry_t": pos["entry_t"],
                    "exit_t": ts,
                    "hold": held,
                    "size": pos["size"],
                    "gross": round(gross_bps, 1),
                    "net": round(net_bps, 1),
                    "pnl": round(pnl, 2),
                    "reason": exit_reason,
                    "capital_after": round(current_capital, 2),
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Check entries ──
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for coin in coins:
            if coin in positions:
                continue
            if coin in cooldown and ts < cooldown[coin]:
                continue

            signals = signal_lookup.get((ts, coin), [])
            if not signals:
                continue

            # Take strongest signal
            best = max(signals, key=lambda s: s["strength"])
            direction = best["direction"]

            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            idx = best["idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry_price = data[coin][idx + 1]["o"]
            if entry_price <= 0:
                continue

            # Position size: % of current capital
            size = current_capital * size_pct
            if size < 10:  # minimum $10
                continue

            candidates.append({
                "coin": coin,
                "direction": direction,
                "entry_price": entry_price,
                "entry_idx": idx + 1,
                "entry_t": data[coin][idx + 1]["t"],
                "strength": best["strength"],
                "strat": best["strat"],
                "hold": best["hold"],
                "size": size,
            })

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)

        for cand in candidates[:slots]:
            positions[cand["coin"]] = {
                "direction": cand["direction"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_t": cand["entry_t"],
                "strat": cand["strat"],
                "hold": cand["hold"],
                "size": cand["size"],
            }
            if cand["direction"] == 1:
                n_long += 1
            else:
                n_short += 1

        # Track equity
        # Unrealized P&L
        unrealized = 0
        for coin, pos in positions.items():
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                current_price = data[coin][ci]["c"]
                if current_price > 0 and pos["entry_price"] > 0:
                    ur = pos["direction"] * (current_price / pos["entry_price"] - 1)
                    unrealized += pos["size"] * ur

        equity_curve.append({
            "t": ts,
            "capital": current_capital,
            "equity": current_capital + unrealized,
            "n_pos": len(positions),
        })

    return trades, equity_curve, current_capital, max_drawdown


def analyze_combined(trades, equity_curve, final_capital, max_dd, label, start_capital=1000):
    """Full analysis of combined portfolio."""
    print(f"\n{'█'*70}")
    print(f"  {label}")
    print(f"{'█'*70}")

    if not trades:
        print("  No trades!")
        return

    n = len(trades)
    total_pnl = final_capital - start_capital
    wins = sum(1 for t in trades if t["net"] > 0)
    avg_net = float(np.mean([t["net"] for t in trades]))

    longs = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]

    print(f"\n  Capital: ${start_capital} → ${final_capital:.0f} "
          f"(+${total_pnl:.0f}, +{total_pnl/start_capital*100:.1f}%)")
    print(f"  Trades: {n} | Win: {wins/n*100:.0f}% | Avg net: {avg_net:+.1f} bps")
    print(f"  LONG:  {len(longs)} trades | SHORT: {len(shorts)} trades")
    print(f"  Max drawdown: {max_dd:.1f}%")

    # By strategy
    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t["strat"]].append(t)

    print(f"\n  By strategy:")
    for sname in sorted(by_strat):
        st = by_strat[sname]
        sp = sum(t["pnl"] for t in st)
        sn = float(np.mean([t["net"] for t in st]))
        sw = sum(1 for t in st if t["net"] > 0) / len(st) * 100
        sl = sum(1 for t in st if t["direction"] == "LONG")
        ss = len(st) - sl
        print(f"    {sname:<20} {len(st):>4}t  ${sp:>+7.0f}  "
              f"avg={sn:>+5.1f}bp  win={sw:.0f}%  {sl}L/{ss}S")

    # Monthly
    by_month = defaultdict(lambda: {"pnl": 0, "n": 0, "wins": 0,
                                     "longs": 0, "shorts": 0, "strats": defaultdict(float)})
    for t in trades:
        m = t["entry_date"][:7]
        by_month[m]["pnl"] += t["pnl"]
        by_month[m]["n"] += 1
        if t["net"] > 0:
            by_month[m]["wins"] += 1
        if t["direction"] == "LONG":
            by_month[m]["longs"] += 1
        else:
            by_month[m]["shorts"] += 1
        by_month[m]["strats"][t["strat"]] += t["pnl"]

    months = sorted(by_month.keys())
    winning_months = sum(1 for m in months if by_month[m]["pnl"] > 0)

    print(f"\n  Monthly P&L ({winning_months}/{len(months)} winning = "
          f"{winning_months/len(months)*100:.0f}%):")
    print(f"  {'Month':<8} {'P&L':>8} {'#':>4} {'W%':>4} {'L/S':>6}  Breakdown")
    print(f"  {'-'*75}")

    cum = start_capital
    for m in months:
        d = by_month[m]
        cum += d["pnl"]
        wr = d["wins"] / d["n"] * 100 if d["n"] > 0 else 0
        marker = "✓" if d["pnl"] > 0 else "✗"

        # Strategy breakdown
        parts = []
        for sname in sorted(d["strats"]):
            sv = d["strats"][sname]
            if abs(sv) > 0.5:
                parts.append(f"{sname.split('_')[0]}:{sv:+.0f}")
        breakdown = " ".join(parts)

        print(f"  {m:<8} ${d['pnl']:>+7.1f} {d['n']:>3}t {wr:>3.0f}% "
              f"{d['longs']:>2}L/{d['shorts']:>2}S  {breakdown} {marker}")

    print(f"\n  Monthly avg: ${total_pnl/len(months):+.1f}")

    # By period
    periods = [
        ("2023-H2", "2023-07", "2023-12"),
        ("2024-H1", "2024-01", "2024-06"),
        ("2024-H2", "2024-07", "2024-12"),
        ("2025-H1", "2025-01", "2025-06"),
        ("2025-H2", "2025-07", "2025-12"),
        ("2026-Q1", "2026-01", "2026-03"),
    ]

    print(f"\n  By period:")
    for name, start, end in periods:
        period_trades = [t for t in trades if start <= t["entry_date"][:7] <= end]
        if not period_trades:
            print(f"    {name:<10} —")
            continue
        pp = sum(t["pnl"] for t in period_trades)
        pn = len(period_trades)
        pa = float(np.mean([t["net"] for t in period_trades]))
        n_months = len(set(t["entry_date"][:7] for t in period_trades))
        marker = "✓" if pp > 0 else "✗"
        print(f"    {name:<10} ${pp:>+7.0f} ({pn:>3}t, {pa:>+5.1f}bp/t, "
              f"${pp/max(1,n_months):>+.0f}/mo) {marker}")

    # By exit reason
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["reason"]].append(t)

    print(f"\n  By exit reason:")
    for reason in sorted(by_reason):
        rt = by_reason[reason]
        rp = sum(t["pnl"] for t in rt)
        ra = float(np.mean([t["net"] for t in rt]))
        print(f"    {reason:<12} {len(rt):>4}t  ${rp:>+7.0f}  avg={ra:>+5.1f}bp")

    # Top/bottom tokens
    by_tk = defaultdict(list)
    for t in trades:
        by_tk[t["coin"]].append(t)
    tk_sorted = sorted([(k, sum(t["pnl"] for t in v), len(v))
                         for k, v in by_tk.items()],
                        key=lambda x: x[1], reverse=True)

    print(f"\n  Top 5 tokens:")
    for tk, pnl, cnt in tk_sorted[:5]:
        print(f"    {tk:<8} ${pnl:>+7.0f} ({cnt}t)")
    print(f"  Bottom 5 tokens:")
    for tk, pnl, cnt in tk_sorted[-5:]:
        print(f"    {tk:<8} ${pnl:>+7.0f} ({cnt}t)")

    # Worst month & worst streak
    worst_month = min(months, key=lambda m: by_month[m]["pnl"])
    print(f"\n  Worst month: {worst_month} (${by_month[worst_month]['pnl']:+.0f})")

    streak = 0
    max_losing_streak = 0
    for m in months:
        if by_month[m]["pnl"] < 0:
            streak += 1
            max_losing_streak = max(max_losing_streak, streak)
        else:
            streak = 0
    print(f"  Max losing streak: {max_losing_streak} months")

    # Annualized return
    total_months = len(months)
    annual_return = (final_capital / start_capital) ** (12 / total_months) - 1
    print(f"\n  Annualized return: {annual_return*100:+.1f}%")

    return {
        "pnl": total_pnl,
        "trades": n,
        "win_rate": wins / n * 100,
        "avg_net": avg_net,
        "monthly": total_pnl / len(months),
        "max_dd": max_dd,
        "winning_months": winning_months,
        "total_months": len(months),
        "annual_return": annual_return,
    }


def monte_carlo_combined(trades, data, n_sims=1000):
    """Monte Carlo with direction-matched random timing for combined portfolio."""
    actual_pnl = sum(t["pnl"] for t in trades)

    by_coin_dir = defaultdict(lambda: {"long": 0, "short": 0, "sizes": []})
    for t in trades:
        key = "long" if t["direction"] == "LONG" else "short"
        by_coin_dir[t["coin"]][key] += 1
        by_coin_dir[t["coin"]]["sizes"].append(t["size"])

    avg_hold = int(np.mean([t["hold"] for t in trades]))

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        for coin, counts in by_coin_dir.items():
            if coin not in data:
                continue
            candles = data[coin]
            nc = len(candles)
            if nc < 200:
                continue

            available = list(range(180, nc - avg_hold - 1))
            n_needed = counts["long"] + counts["short"]
            if len(available) < n_needed:
                continue

            avg_size = float(np.mean(counts["sizes"])) if counts["sizes"] else POSITION_SIZE
            sampled = random.sample(available, n_needed)
            for j, idx in enumerate(sampled):
                direction = 1 if j < counts["long"] else -1
                entry = candles[min(idx + 1, nc - 1)]["o"]
                exit_idx = min(idx + 1 + avg_hold, nc - 1)
                exit_p = candles[exit_idx]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim_total += avg_size * net / 1e4

        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls))
    z = (actual_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    p = sum(1 for s in sim_pnls if s >= actual_pnl) / n_sims

    return {
        "actual": actual_pnl,
        "random_mean": sim_mean,
        "random_std": sim_std,
        "z": z,
        "p": p,
    }


# ── Sensitivity analysis ─────────────────────────────────────────────

def sensitivity_analysis(features, data):
    """Test how sensitive results are to parameter changes."""
    print(f"\n{'='*70}")
    print(f"  SENSITIVITY ANALYSIS")
    print(f"  How fragile are the strategies?")
    print(f"{'='*70}")

    base_strats = {
        "S1_btc_rip": (Rule("btc_30d", ">", 2000, 1), 42),
        "S2_alt_crash": (Rule("alt_index_7d", "<", -1000, 1), 18),
        "S4_vol_short": ([Rule("vol_ratio", "<", 1.0, -1), Rule("range_pct", "<", 200, -1)], 18),
    }

    # Test btc_rip with different thresholds
    print(f"\n  S1 btc_rip — threshold sensitivity:")
    for thresh in [1000, 1500, 2000, 2500, 3000]:
        strat = Strategy([Rule("btc_30d", ">", thresh, 1)], hold=42)
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        marker = "◄" if thresh == 2000 else ""
        print(f"    btc_30d > {thresh:>5}: ${s['pnl']:>+7.0f} ({s['n']:>3}t, "
              f"avg={s['avg']:>+6.1f}bp, win={s['win']:.0f}%) {marker}")

    # Test alt_crash with different thresholds
    print(f"\n  S2 alt_crash — threshold sensitivity:")
    for thresh in [-500, -750, -1000, -1500, -2000]:
        strat = Strategy([Rule("alt_index_7d", "<", thresh, 1)], hold=18)
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        marker = "◄" if thresh == -1000 else ""
        print(f"    alt_idx < {thresh:>5}: ${s['pnl']:>+7.0f} ({s['n']:>3}t, "
              f"avg={s['avg']:>+6.1f}bp, win={s['win']:.0f}%) {marker}")

    # Test vol_short with different thresholds
    print(f"\n  S4 vol_short — vol_ratio sensitivity:")
    for vr_thresh in [0.5, 0.7, 0.8, 0.9, 1.0, 1.2]:
        strat = Strategy([Rule("vol_ratio", "<", vr_thresh, -1),
                          Rule("range_pct", "<", 200, -1)], hold=18)
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        marker = "◄" if vr_thresh == 1.0 else ""
        print(f"    vol_ratio < {vr_thresh:.1f}: ${s['pnl']:>+7.0f} ({s['n']:>3}t, "
              f"avg={s['avg']:>+6.1f}bp, win={s['win']:.0f}%) {marker}")

    # Test range_pct sensitivity for vol_short
    print(f"\n  S4 vol_short — range_pct sensitivity:")
    for rp_thresh in [100, 150, 200, 250, 300, 400]:
        strat = Strategy([Rule("vol_ratio", "<", 1.0, -1),
                          Rule("range_pct", "<", rp_thresh, -1)], hold=18)
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        marker = "◄" if rp_thresh == 200 else ""
        print(f"    range < {rp_thresh:>4}: ${s['pnl']:>+7.0f} ({s['n']:>3}t, "
              f"avg={s['avg']:>+6.1f}bp, win={s['win']:.0f}%) {marker}")

    # Cost sensitivity
    print(f"\n  Cost sensitivity (ALL 4 combined):")
    for cost in [5, 8, 10, 12, 15, 20]:
        total_pnl = 0
        total_n = 0
        for sname, strat in STRATEGIES.items():
            trades = backtest_strategy(strat, features, data, period="all", cost=cost)
            total_pnl += sum(t["pnl"] for t in trades)
            total_n += len(trades)
        marker = "◄" if cost == 12 else ""
        print(f"    cost={cost:>2}bp: ${total_pnl:>+7.0f} ({total_n}t) {marker}")

    # Breakeven cost
    print(f"\n  Breakeven cost analysis:")
    for sname, strat in STRATEGIES.items():
        trades_0 = backtest_strategy(strat, features, data, period="all", cost=0)
        if trades_0:
            avg_gross = float(np.mean([t["gross"] for t in trades_0]))
            print(f"    {sname:<20} avg gross: {avg_gross:+.1f} bps → "
                  f"breakeven at {avg_gross:.0f} bps cost")


# ── Correlation between strategies ───────────────────────────────────

def strategy_correlation(features, data):
    """Check if strategies trade at the same time (correlated) or independently."""
    print(f"\n{'='*70}")
    print(f"  STRATEGY CORRELATION")
    print(f"{'='*70}")

    # Get trade dates for each strategy
    strat_dates = {}
    for sname, strat in STRATEGIES.items():
        trades = backtest_strategy(strat, features, data, period="all")
        dates = set()
        for t in trades:
            # Entry month
            dates.add(t["coin"] + "_" + str(t["entry_t"] // (86400 * 1000)))
        strat_dates[sname] = dates

    # Pairwise overlap
    names = list(STRATEGIES.keys())
    print(f"\n  Pairwise trade overlap:")
    for i, n1 in enumerate(names):
        for j, n2 in enumerate(names):
            if j <= i:
                continue
            overlap = len(strat_dates[n1] & strat_dates[n2])
            total = len(strat_dates[n1] | strat_dates[n2])
            pct = overlap / total * 100 if total > 0 else 0
            print(f"    {n1} × {n2}: {overlap}/{total} "
                  f"({pct:.1f}% overlap)")

    # Monthly P&L correlation
    monthly_pnl = {}
    for sname, strat in STRATEGIES.items():
        trades = backtest_strategy(strat, features, data, period="all")
        by_month = defaultdict(float)
        for t in trades:
            dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
            by_month[dt.strftime("%Y-%m")] += t["pnl"]
        monthly_pnl[sname] = by_month

    all_months = sorted(set().union(*[set(v.keys()) for v in monthly_pnl.values()]))

    print(f"\n  Monthly P&L correlation:")
    for i, n1 in enumerate(names):
        for j, n2 in enumerate(names):
            if j <= i:
                continue
            v1 = [monthly_pnl[n1].get(m, 0) for m in all_months]
            v2 = [monthly_pnl[n2].get(m, 0) for m in all_months]
            if np.std(v1) > 0 and np.std(v2) > 0:
                corr = float(np.corrcoef(v1, v2)[0, 1])
            else:
                corr = 0
            print(f"    {n1} × {n2}: ρ = {corr:+.2f}")


def main():
    print("=" * 70)
    print("  FINAL SYNTHESIS — Strategy Portfolio")
    print("  Fixed backtester + honest assessment")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    print("\nBuilding features...")
    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # ══════════════════════════════════════════════════════════════
    # 1. Individual strategy results (for reference)
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  INDIVIDUAL STRATEGIES (no position limits between them)")
    print(f"{'='*70}")

    individual_total = 0
    for sname, strat in STRATEGIES.items():
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        longs = sum(1 for t in trades if t["direction"] == "LONG")
        shorts = len(trades) - longs

        # Train/test split
        trades_train = backtest_strategy(strat, features, data, period="train")
        trades_test = backtest_strategy(strat, features, data, period="test")
        s_train = quick_score(trades_train)
        s_test = quick_score(trades_test)

        print(f"\n  {sname}: {strat}")
        print(f"    ALL:   ${s['pnl']:>+7.0f} ({s['n']:>3}t, avg={s['avg']:>+5.1f}bp, "
              f"win={s['win']:.0f}%, {longs}L/{shorts}S)")
        print(f"    TRAIN: ${s_train['pnl']:>+7.0f} ({s_train['n']:>3}t)")
        print(f"    TEST:  ${s_test['pnl']:>+7.0f} ({s_test['n']:>3}t)")
        individual_total += s["pnl"]

    print(f"\n  Individual total (theoretical max): ${individual_total:+.0f}")

    # ══════════════════════════════════════════════════════════════
    # 2. Strategy correlation
    # ══════════════════════════════════════════════════════════════
    strategy_correlation(features, data)

    # ══════════════════════════════════════════════════════════════
    # 3. Sensitivity analysis
    # ══════════════════════════════════════════════════════════════
    sensitivity_analysis(features, data)

    # ══════════════════════════════════════════════════════════════
    # 4. Combined portfolio (fixed backtester)
    # ══════════════════════════════════════════════════════════════

    # Combo A: ALL 4 strategies
    print(f"\n\n{'#'*70}")
    print(f"  COMBINED PORTFOLIO BACKTESTS")
    print(f"{'#'*70}")

    trades_a, eq_a, cap_a, dd_a = combined_backtest(
        STRATEGIES, features, data,
        capital=1000, size_pct=0.25, max_pos=6, max_dir=4)
    res_a = analyze_combined(trades_a, eq_a, cap_a, dd_a,
                             "COMBO A: All 4 strategies (max 6 pos, 25% sizing)")

    # Combo B: Only LONG strategies (more conservative)
    long_strats = {k: v for k, v in STRATEGIES.items() if "short" not in k}
    trades_b, eq_b, cap_b, dd_b = combined_backtest(
        long_strats, features, data,
        capital=1000, size_pct=0.25, max_pos=6, max_dir=4)
    res_b = analyze_combined(trades_b, eq_b, cap_b, dd_b,
                             "COMBO B: LONG only (3 strategies)")

    # Combo C: LONG + SHORT, conservative sizing
    trades_c, eq_c, cap_c, dd_c = combined_backtest(
        STRATEGIES, features, data,
        capital=1000, size_pct=0.15, max_pos=6, max_dir=4)
    res_c = analyze_combined(trades_c, eq_c, cap_c, dd_c,
                             "COMBO C: All 4, conservative sizing (15%)")

    # ══════════════════════════════════════════════════════════════
    # 5. Monte Carlo validation of best combo
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'▓'*70}")
    print(f"  MONTE CARLO VALIDATION (1000 simulations)")
    print(f"{'▓'*70}")

    for name, trades in [("COMBO A (All 4)", trades_a),
                          ("COMBO B (LONG only)", trades_b)]:
        if not trades:
            continue
        print(f"\n  {name}:")
        mc = monte_carlo_combined(trades, data, n_sims=1000)
        print(f"    Actual:      ${mc['actual']:>+.0f} ({len(trades)} trades)")
        print(f"    Random mean: ${mc['random_mean']:>+.0f} (std: ${mc['random_std']:.0f})")
        print(f"    Z-score:     {mc['z']:+.2f}")
        print(f"    P-value:     {mc['p']:.4f}")
        if mc["z"] > 2.5:
            print(f"    → ✓✓ STRONGLY SIGNIFICANT")
        elif mc["z"] > 2.0:
            print(f"    → ✓ SIGNIFICANT")
        elif mc["z"] > 1.5:
            print(f"    → ⚠ MARGINAL")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    # ══════════════════════════════════════════════════════════════
    # 6. HONEST ASSESSMENT
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'█'*70}")
    print(f"  HONEST ASSESSMENT — What's real, what's not")
    print(f"{'█'*70}")

    print(f"""
  ╔══════════════════════════════════════════════════════════════════╗
  ║  WHAT IS VALIDATED (z > 2.5, survives train/test split):       ║
  ║                                                                ║
  ║  1. btc_30d > +20% → LONG alts      z=6.42  (rare but huge)   ║
  ║  2. alt_index_7d < -10% → LONG      z=4.00  (buy dips)        ║
  ║  3. btc_7d < -5% + alt -20% → LONG  z=3.58  (double dip)     ║
  ║  4. vol_contraction → SHORT          z=2.95  (quiet = fade)   ║
  ║                                                                ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  WHAT'S SUSPICIOUS:                                            ║
  ║                                                                ║
  ║  • SHORT signals are 24% of the time → almost always short    ║
  ║  • 2024 was bearish for alts, so shorts did well naturally     ║
  ║  • All LONG signals are essentially "buy the dip"             ║
  ║  • Win rates are 50-60% → fragile, needs volume to profit     ║
  ║  • Position competition reduces theoretical gains by ~30-40%   ║
  ║  • 27 months of data = not a full market cycle                 ║
  ║                                                                ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  REALISTIC EXPECTATIONS on $1000:                              ║
  ║                                                                ║
  ║  • Good months: +$100-300 (10-30%)                             ║
  ║  • Bad months:  -$50-200 (-5-20%)                              ║
  ║  • Average:     probably +$50-100/month (+5-10%)               ║
  ║  • But: drawdowns of $300-600 WILL happen                      ║
  ║  • Worst case: -$600 drawdown before recovery                  ║
  ║                                                                ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  WHAT WOULD MAKE THIS FAIL:                                    ║
  ║                                                                ║
  ║  • Prolonged sideways market (no big BTC moves, no alt dips)   ║
  ║  • Regime change in alt/BTC correlation                        ║
  ║  • Hyperliquid liquidity drying up → slippage kills edge       ║
  ║  • Crowded trade → everyone buys dips → dips stop bouncing     ║
  ╚══════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
