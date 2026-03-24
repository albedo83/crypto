"""Cash-and-Carry backtest — 1 year.

Strategy: Buy spot + Short perp on SAME symbol = zero basis risk.
Collect funding every 8h when funding > 0 (longs pay shorts).
Rotate to highest-funding symbols.

Usage:
    python3 -m analysis.backtest_carry_pure
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "ZROUSDT",
    "AAVEUSDT", "SUIUSDT", "AVAXUSDT", "XRPUSDT", "XMRUSDT",
    "XLMUSDT", "TONUSDT", "LTCUSDT", "BTCUSDT", "ETHUSDT",
]

# Costs
SPOT_FEE_BPS = 5.0       # spot buy+sell roundtrip (0.1% taker × 2, or 0.075% BNB × 2)
PERP_FEE_BPS = 3.0       # perp open+close roundtrip (maker 0.02% × 2 × BNB discount)
REBALANCE_COST_BPS = SPOT_FEE_BPS + PERP_FEE_BPS  # 8 bps total to enter+exit a pair


def load_funding(days: int = 365) -> dict[str, pd.DataFrame]:
    """Load funding rate data for all symbols."""
    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)
    data = {}

    for sym in SYMBOLS:
        ff = os.path.join(DATA_DIR, f"{sym}_funding.csv")
        if not os.path.exists(ff):
            continue
        df = pd.read_csv(ff)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        df = df[start_ts:end_ts]
        if len(df) < 100:
            continue
        df["funding_bps"] = df["funding_rate"] * 1e4
        data[sym] = df

    return data


def analyze_funding_stats(data: dict[str, pd.DataFrame]):
    """Print funding rate statistics per symbol."""
    print("\n" + "═" * 70)
    print("  FUNDING RATE STATISTICS — 1 Year")
    print("═" * 70)
    print(f"\n  {'Symbol':<12} {'Mean':>7} {'Median':>7} {'Std':>7} {'%Pos':>6} "
          f"{'%>2bps':>7} {'%>5bps':>7} {'Ann%':>8}")
    print(f"  {'-'*68}")

    stats = []
    for sym in sorted(data.keys()):
        df = data[sym]
        f = df["funding_bps"]
        mean = f.mean()
        median = f.median()
        std = f.std()
        pct_pos = (f > 0).mean() * 100
        pct_gt2 = (f > 2).mean() * 100
        pct_gt5 = (f > 5).mean() * 100
        # Annualized: mean × 3 settlements/day × 365
        ann_pct = mean * 3 * 365 / 100

        print(f"  {sym:<12} {mean:>+6.2f} {median:>+6.2f} {std:>6.2f} {pct_pos:>5.0f}% "
              f"{pct_gt2:>6.1f}% {pct_gt5:>6.1f}% {ann_pct:>+7.1f}%")

        stats.append({
            "symbol": sym, "mean": mean, "median": median, "std": std,
            "pct_pos": pct_pos, "pct_gt2": pct_gt2, "ann_pct": ann_pct,
            "count": len(f),
        })

    return stats


def sim_static_carry(data: dict[str, pd.DataFrame], symbols: list[str],
                     capital: float = 1000.0, leverage: float = 1.0,
                     label: str = "") -> dict:
    """
    Simulate static cash-and-carry on fixed symbols.

    Capital split: 50% spot (buy), 50% perp margin (short).
    Notional per leg = capital × leverage / 2 per symbol.
    Collect funding on perp short when rate > 0.
    Pay funding on perp short when rate < 0.
    """
    n_syms = len(symbols)
    if n_syms == 0:
        return {"label": label, "pnl": 0, "months": {}}

    # Entry cost (one-time)
    entry_cost = capital * REBALANCE_COST_BPS / 1e4

    # Per-symbol notional on perp side
    notional_per_sym = capital * leverage / n_syms

    # Merge all funding into one timeline
    all_funding = []
    for sym in symbols:
        if sym not in data:
            continue
        df = data[sym]
        for _, row in df.iterrows():
            all_funding.append({
                "time": row.name,
                "symbol": sym,
                "funding_bps": row["funding_bps"],
            })

    if not all_funding:
        return {"label": label, "pnl": 0, "months": {}}

    # Sort by time
    all_funding.sort(key=lambda x: x["time"])

    # Simulate
    total_funding = 0.0
    by_month = defaultdict(float)
    by_symbol = defaultdict(float)
    n_settlements = 0
    n_positive = 0

    for f in all_funding:
        # Short perp: we RECEIVE funding when rate > 0, PAY when rate < 0
        pnl = notional_per_sym * f["funding_bps"] / 1e4
        total_funding += pnl
        by_month[str(f["time"])[:7]] += pnl
        by_symbol[f["symbol"]] += pnl
        n_settlements += 1
        if pnl > 0:
            n_positive += 1

    net_pnl = total_funding - entry_cost
    # Exit cost (if we close)
    # net_pnl -= entry_cost  # uncomment for roundtrip

    return {
        "label": label,
        "symbols": symbols,
        "pnl": net_pnl,
        "gross_funding": total_funding,
        "entry_cost": entry_cost,
        "n_settlements": n_settlements,
        "pct_positive": n_positive / max(1, n_settlements) * 100,
        "by_month": dict(by_month),
        "by_symbol": dict(by_symbol),
        "monthly_avg": net_pnl / max(1, len(by_month)),
        "monthly_pct": net_pnl / max(1, len(by_month)) / capital * 100,
    }


def sim_dynamic_carry(data: dict[str, pd.DataFrame], top_n: int = 3,
                      rebalance_days: int = 3, capital: float = 1000.0,
                      leverage: float = 1.0, min_funding_bps: float = 0.0,
                      label: str = "") -> dict:
    """
    Dynamic carry: rotate to top_n highest mean-funding symbols every N days.
    Only carry symbols with mean funding > min_funding_bps.
    """
    # Build daily funding summary
    daily = {}
    for sym, df in data.items():
        for _, row in df.iterrows():
            day = str(row.name.date())
            if day not in daily:
                daily[day] = {}
            if sym not in daily[day]:
                daily[day][sym] = []
            daily[day][sym].append(row["funding_bps"])

    days_sorted = sorted(daily.keys())
    if not days_sorted:
        return {"label": label, "pnl": 0}

    # Simulate
    total_pnl = 0.0
    total_rebalance_cost = 0.0
    current_syms = []
    by_month = defaultdict(float)
    by_symbol = defaultdict(float)
    n_rebalances = 0
    n_settlements = 0
    n_positive = 0

    for i, day in enumerate(days_sorted):
        # Rebalance?
        if i % rebalance_days == 0:
            # Rank symbols by recent mean funding (last rebalance_days × 3 settlements)
            lookback_days = min(rebalance_days * 2, i)
            recent_days = days_sorted[max(0, i - lookback_days):i + 1]

            sym_means = {}
            for sym in data.keys():
                rates = []
                for d in recent_days:
                    if d in daily and sym in daily[d]:
                        rates.extend(daily[d][sym])
                if rates:
                    sym_means[sym] = np.mean(rates)

            # Pick top_n with highest positive funding
            ranked = sorted(sym_means.items(), key=lambda x: x[1], reverse=True)
            new_syms = [s for s, m in ranked if m > min_funding_bps][:top_n]

            if set(new_syms) != set(current_syms):
                # Pay rebalance cost
                changed = set(new_syms).symmetric_difference(set(current_syms))
                cost = len(changed) * (capital / max(1, top_n)) * REBALANCE_COST_BPS / 1e4
                total_rebalance_cost += cost
                total_pnl -= cost
                current_syms = new_syms
                n_rebalances += 1

        if not current_syms:
            continue

        # Collect funding for today
        notional_per_sym = capital * leverage / len(current_syms)
        for sym in current_syms:
            if day in daily and sym in daily[day]:
                for rate in daily[day][sym]:
                    pnl = notional_per_sym * rate / 1e4
                    total_pnl += pnl
                    by_month[day[:7]] += pnl
                    by_symbol[sym] = by_symbol.get(sym, 0) + pnl
                    n_settlements += 1
                    if pnl > 0:
                        n_positive += 1

    return {
        "label": label,
        "pnl": total_pnl,
        "rebalance_cost": total_rebalance_cost,
        "n_rebalances": n_rebalances,
        "n_settlements": n_settlements,
        "pct_positive": n_positive / max(1, n_settlements) * 100,
        "by_month": dict(by_month),
        "by_symbol": dict(by_symbol),
        "monthly_avg": total_pnl / max(1, len(by_month)),
        "monthly_pct": total_pnl / max(1, len(by_month)) / capital * 100,
    }


def print_result(r: dict):
    """Print carry simulation result."""
    print(f"\n{'═'*60}")
    print(f"  {r['label']}")
    print(f"{'═'*60}")
    print(f"  Total P&L:    ${r['pnl']:+.2f}")
    print(f"  Monthly avg:  ${r.get('monthly_avg', 0):+.2f} ({r.get('monthly_pct', 0):+.2f}%)")
    print(f"  Settlements:  {r.get('n_settlements', 0)}")
    print(f"  % positive:   {r.get('pct_positive', 0):.0f}%")
    if "rebalance_cost" in r:
        print(f"  Rebalances:   {r.get('n_rebalances', 0)} (cost: ${r['rebalance_cost']:.2f})")
    if "entry_cost" in r:
        print(f"  Entry cost:   ${r['entry_cost']:.2f}")

    # Monthly breakdown
    by_month = r.get("by_month", {})
    if by_month:
        print(f"\n  {'Month':<10} {'P&L':>10} {'%':>8}")
        print(f"  {'-'*30}")
        losing = 0
        for m in sorted(by_month.keys()):
            pnl = by_month[m]
            pct = pnl / 1000 * 100  # assume $1000
            marker = "✓" if pnl > 0 else "✗"
            if pnl <= 0:
                losing += 1
            print(f"  {m:<10} ${pnl:>+8.2f} {pct:>+6.2f}% {marker}")
        print(f"\n  Losing months: {losing}/{len(by_month)}")

    # By symbol
    by_sym = r.get("by_symbol", {})
    if by_sym:
        sorted_syms = sorted(by_sym.items(), key=lambda x: x[1], reverse=True)
        print(f"\n  {'Symbol':<12} {'P&L':>10}")
        print(f"  {'-'*24}")
        for s, p in sorted_syms:
            print(f"  {s:<12} ${p:>+8.2f}")


def main():
    print("=" * 60)
    print("  CASH-AND-CARRY BACKTEST — 1 Year")
    print("  Buy spot + Short perp = collect funding, zero basis risk")
    print("=" * 60)

    print("\nLoading funding data...")
    data = load_funding(365)
    print(f"Loaded {len(data)} symbols")

    # ── Funding Statistics ────────────────────────────────────────────
    stats = analyze_funding_stats(data)

    # Sort by mean funding (best carry targets)
    stats.sort(key=lambda x: x["mean"], reverse=True)
    print(f"\n  Top carry targets (highest mean funding = most profitable to short):")
    for s in stats[:5]:
        print(f"    {s['symbol']}: mean {s['mean']:+.2f} bps/8h = {s['ann_pct']:+.1f}%/an")

    # ── Static Carry Simulations ─────────────────────────────────────
    print("\n\n" + "▓" * 60)
    print("  STATIC CARRY (fixed symbols, 1 year)")
    print("▓" * 60)

    results = []

    # Best single symbol
    best_sym = stats[0]["symbol"]
    r = sim_static_carry(data, [best_sym], capital=1000, leverage=1,
                         label=f"S1. Best single: {best_sym} (1x)")
    print_result(r)
    results.append(r)

    # Best single at 3x
    r = sim_static_carry(data, [best_sym], capital=1000, leverage=3,
                         label=f"S2. Best single: {best_sym} (3x)")
    print_result(r)
    results.append(r)

    # Top 3 symbols
    top3 = [s["symbol"] for s in stats[:3]]
    r = sim_static_carry(data, top3, capital=1000, leverage=1,
                         label=f"S3. Top 3: {', '.join(top3)} (1x)")
    print_result(r)
    results.append(r)

    # Top 3 at 3x
    r = sim_static_carry(data, top3, capital=1000, leverage=3,
                         label=f"S4. Top 3: {', '.join(top3)} (3x)")
    print_result(r)
    results.append(r)

    # Top 5 symbols
    top5 = [s["symbol"] for s in stats[:5]]
    r = sim_static_carry(data, top5, capital=1000, leverage=1,
                         label=f"S5. Top 5: {', '.join(top5)} (1x)")
    print_result(r)
    results.append(r)

    # Only symbols with >60% positive funding
    reliable = [s["symbol"] for s in stats if s["pct_pos"] > 60]
    r = sim_static_carry(data, reliable, capital=1000, leverage=1,
                         label=f"S6. >60% positive ({len(reliable)} syms, 1x)")
    print_result(r)
    results.append(r)

    # ── Dynamic Carry Simulations ────────────────────────────────────
    print("\n\n" + "▓" * 60)
    print("  DYNAMIC CARRY (rotate to best funding)")
    print("▓" * 60)

    # D1. Top 1, rebalance 3 days
    r = sim_dynamic_carry(data, top_n=1, rebalance_days=3, capital=1000, leverage=1,
                          label="D1. Top 1, rebal 3d, 1x")
    print_result(r)
    results.append(r)

    # D2. Top 1, rebalance 3 days, 3x
    r = sim_dynamic_carry(data, top_n=1, rebalance_days=3, capital=1000, leverage=3,
                          label="D2. Top 1, rebal 3d, 3x")
    print_result(r)
    results.append(r)

    # D3. Top 3, rebalance 3 days
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=3, capital=1000, leverage=1,
                          label="D3. Top 3, rebal 3d, 1x")
    print_result(r)
    results.append(r)

    # D4. Top 3, rebalance 3 days, 3x
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=3, capital=1000, leverage=3,
                          label="D4. Top 3, rebal 3d, 3x")
    print_result(r)
    results.append(r)

    # D5. Top 3, rebalance 7 days (less turnover)
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=7, capital=1000, leverage=1,
                          label="D5. Top 3, rebal 7d, 1x")
    print_result(r)
    results.append(r)

    # D6. Top 3, rebalance 7 days, 3x
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=7, capital=1000, leverage=3,
                          label="D6. Top 3, rebal 7d, 3x")
    print_result(r)
    results.append(r)

    # D7. Top 5, rebal 3d, only if funding > 1 bps
    r = sim_dynamic_carry(data, top_n=5, rebalance_days=3, capital=1000, leverage=1,
                          min_funding_bps=1.0, label="D7. Top 5, rebal 3d, min>1bps, 1x")
    print_result(r)
    results.append(r)

    # D8. Top 3, rebal 1d (aggressive), 3x
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=1, capital=1000, leverage=3,
                          label="D8. Top 3, rebal 1d, 3x")
    print_result(r)
    results.append(r)

    # D9. Top 3, rebal 7d, 3x, min > 1 bps
    r = sim_dynamic_carry(data, top_n=3, rebalance_days=7, capital=1000, leverage=3,
                          min_funding_bps=1.0, label="D9. Top 3, rebal 7d, min>1bps, 3x")
    print_result(r)
    results.append(r)

    # ── SUMMARY ──────────────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  FINAL SUMMARY")
    print("█" * 70)
    print(f"\n  {'Config':<42} {'P&L':>9} {'$/mois':>8} {'%/mois':>8} {'Mo perd':>8}")
    print(f"  {'-'*78}")

    for r in results:
        by_month = r.get("by_month", {})
        losing = sum(1 for v in by_month.values() if v <= 0)
        total = len(by_month)
        lm = f"{losing}/{total}" if total > 0 else "?"
        marker = "✓" if r["pnl"] > 0 else "✗"
        print(f"  {r['label']:<42} ${r['pnl']:>+7.2f} ${r.get('monthly_avg',0):>+6.2f} "
              f"{r.get('monthly_pct',0):>+6.2f}% {lm:>8} {marker}")

    best = max(results, key=lambda r: r["pnl"])
    print(f"\n  BEST: {best['label']} → ${best['pnl']:+.2f}/an = "
          f"{best.get('monthly_pct', 0):+.2f}%/mois")


if __name__ == "__main__":
    main()
