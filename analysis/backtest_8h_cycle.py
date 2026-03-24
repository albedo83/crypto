"""Backtest: 8-hour funding settlement cycle.

Hypothesis: Price oscillates predictably around funding settlements.
- Before settlement (funding > 0): longs sell to avoid paying → price dips
- After settlement: selling pressure gone → price recovers
- Opposite when funding < 0

Strategy variants:
A. Buy X min before settlement, sell Y min after (fade pre-settle dip)
B. Sell before settlement, buy after (ride pre-settle pressure)
C. Conditional: only when funding > threshold
D. Time-weighted: scale position based on distance to settlement

1 year of data, 13 symbols, 3 settlements/day = ~14,000 events.

Usage:
    python3 -m analysis.backtest_8h_cycle
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "ZROUSDT",
    "AAVEUSDT", "SUIUSDT", "AVAXUSDT", "XRPUSDT", "XMRUSDT",
    "XLMUSDT", "TONUSDT", "LTCUSDT",
]

COST_BPS = 3.0
SLIPPAGE_BPS = 1.0
TOTAL_COST = COST_BPS + SLIPPAGE_BPS  # 4 bps roundtrip

SETTLEMENT_HOURS = [0, 8, 16]  # UTC


def load_symbol(sym: str, days: int = 365) -> pd.DataFrame | None:
    prefix = os.path.join(DATA_DIR, sym)
    kf = f"{prefix}_klines_1m.csv"
    ff = f"{prefix}_funding.csv"
    if not os.path.exists(kf):
        return None

    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)

    klines = pd.read_csv(kf, usecols=["timestamp", "close", "volume"])
    klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
    klines = klines.set_index("timestamp").sort_index()
    klines = klines[start_ts:end_ts]
    if len(klines) < 1000:
        return None

    df = klines.copy()

    # Funding
    if os.path.exists(ff):
        fund = pd.read_csv(ff)
        fund["timestamp"] = pd.to_datetime(fund["timestamp"], unit="ms", utc=True)
        fund = fund.set_index("timestamp").sort_index()
        fund = fund[~fund.index.duplicated(keep="last")]
        df = df.join(fund[["funding_rate"]], how="left")
        df["funding_rate"] = df["funding_rate"].ffill().fillna(0)
    else:
        df["funding_rate"] = 0.0

    df["funding_bps"] = df["funding_rate"] * 1e4
    df["hour"] = df.index.hour
    df["minute"] = df.index.minute
    return df


# ── Part 1: Raw price pattern around settlements ─────────────────────

def analyze_settlement_pattern(sym: str, df: pd.DataFrame) -> dict:
    """Measure average price change at various offsets from settlement."""
    results = []

    # Find all settlement timestamps
    settlements = df[(df["hour"].isin(SETTLEMENT_HOURS)) & (df["minute"] == 0)].index

    offsets_min = [-120, -90, -60, -45, -30, -20, -15, -10, -5,
                   0, 5, 10, 15, 20, 30, 45, 60, 90, 120]

    for settle_ts in settlements:
        settle_idx = df.index.get_indexer([settle_ts], method="nearest")[0]
        if settle_idx < 120 or settle_idx > len(df) - 121:
            continue

        settle_price = float(df["close"].iloc[settle_idx])
        if settle_price == 0:
            continue

        funding = float(df["funding_bps"].iloc[settle_idx])

        row = {"settle_time": settle_ts, "funding_bps": funding}
        for offset in offsets_min:
            idx = settle_idx + offset
            if 0 <= idx < len(df):
                p = float(df["close"].iloc[idx])
                row[f"ret_{offset}m"] = (p / settle_price - 1) * 1e4
        results.append(row)

    return results


def analyze_all_patterns(days: int = 365) -> pd.DataFrame:
    """Analyze settlement patterns across all symbols."""
    all_rows = []
    for sym in SYMBOLS:
        df = load_symbol(sym, days)
        if df is None:
            continue
        rows = analyze_settlement_pattern(sym, df)
        for r in rows:
            r["symbol"] = sym
        all_rows.extend(rows)
        del df

    return pd.DataFrame(all_rows)


# ── Part 2: Trading strategies ────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    entry_time: str
    exit_time: str
    direction: str
    hold_min: int
    gross_bps: float
    net_bps: float
    pnl_usdt: float
    funding_bps: float


def strat_settlement_fade(sym: str, df: pd.DataFrame,
                           entry_offset: int = -30,
                           exit_offset: int = 30,
                           min_funding_bps: float = 0.0,
                           size_usdt: float = 250.0) -> list[Trade]:
    """
    Fade the pre-settlement pressure:
    - If funding > 0: longs sell before settle → buy the dip (LONG before, close after)
    - If funding < 0: shorts cover before settle → sell the rip (SHORT before, close after)
    """
    trades = []
    settlements = df[(df["hour"].isin(SETTLEMENT_HOURS)) & (df["minute"] == 0)].index

    for settle_ts in settlements:
        settle_idx = df.index.get_indexer([settle_ts], method="nearest")[0]
        entry_idx = settle_idx + entry_offset
        exit_idx = settle_idx + exit_offset

        if entry_idx < 0 or exit_idx >= len(df):
            continue

        funding = float(df["funding_bps"].iloc[settle_idx])
        if abs(funding) < min_funding_bps:
            continue

        entry_price = float(df["close"].iloc[entry_idx])
        exit_price = float(df["close"].iloc[exit_idx])
        if entry_price == 0 or exit_price == 0:
            continue

        # Fade: buy when funding positive (selling pressure), short when negative
        direction = 1 if funding > 0 else -1
        gross = direction * (exit_price / entry_price - 1) * 1e4
        net = gross - TOTAL_COST
        pnl = size_usdt * direction * (exit_price / entry_price - 1) - size_usdt * TOTAL_COST / 1e4

        trades.append(Trade(
            symbol=sym, entry_time=str(df.index[entry_idx]),
            exit_time=str(df.index[exit_idx]),
            direction="LONG" if direction == 1 else "SHORT",
            hold_min=exit_offset - entry_offset,
            gross_bps=round(gross, 2), net_bps=round(net, 2),
            pnl_usdt=round(pnl, 4), funding_bps=round(funding, 3),
        ))

    return trades


def strat_settlement_momentum(sym: str, df: pd.DataFrame,
                               entry_offset: int = -30,
                               exit_offset: int = 30,
                               min_funding_bps: float = 0.0,
                               size_usdt: float = 250.0) -> list[Trade]:
    """
    Ride the pre-settlement pressure (opposite of fade):
    - If funding > 0: longs selling → SHORT before settle (ride the pressure)
    - If funding < 0: shorts covering → LONG before settle
    Then close after settlement when pressure reverses.
    """
    trades = []
    settlements = df[(df["hour"].isin(SETTLEMENT_HOURS)) & (df["minute"] == 0)].index

    for settle_ts in settlements:
        settle_idx = df.index.get_indexer([settle_ts], method="nearest")[0]
        entry_idx = settle_idx + entry_offset
        exit_idx = settle_idx + exit_offset

        if entry_idx < 0 or exit_idx >= len(df):
            continue

        funding = float(df["funding_bps"].iloc[settle_idx])
        if abs(funding) < min_funding_bps:
            continue

        entry_price = float(df["close"].iloc[entry_idx])
        exit_price = float(df["close"].iloc[exit_idx])
        if entry_price == 0 or exit_price == 0:
            continue

        # Momentum: short when funding positive (join selling), long when negative
        direction = -1 if funding > 0 else 1
        gross = direction * (exit_price / entry_price - 1) * 1e4
        net = gross - TOTAL_COST
        pnl = size_usdt * direction * (exit_price / entry_price - 1) - size_usdt * TOTAL_COST / 1e4

        trades.append(Trade(
            symbol=sym, entry_time=str(df.index[entry_idx]),
            exit_time=str(df.index[exit_idx]),
            direction="LONG" if direction == 1 else "SHORT",
            hold_min=exit_offset - entry_offset,
            gross_bps=round(gross, 2), net_bps=round(net, 2),
            pnl_usdt=round(pnl, 4), funding_bps=round(funding, 3),
        ))

    return trades


def strat_post_settlement_reversion(sym: str, df: pd.DataFrame,
                                     entry_offset: int = 1,
                                     exit_offset: int = 60,
                                     min_funding_bps: float = 0.0,
                                     size_usdt: float = 250.0) -> list[Trade]:
    """
    After settlement, pressure reverses:
    - If funding was > 0: selling done → buy (LONG post-settle)
    - If funding was < 0: covering done → sell (SHORT post-settle)
    """
    trades = []
    settlements = df[(df["hour"].isin(SETTLEMENT_HOURS)) & (df["minute"] == 0)].index

    for settle_ts in settlements:
        settle_idx = df.index.get_indexer([settle_ts], method="nearest")[0]
        entry_idx = settle_idx + entry_offset
        exit_idx = settle_idx + exit_offset

        if entry_idx < 0 or exit_idx >= len(df):
            continue

        funding = float(df["funding_bps"].iloc[settle_idx])
        if abs(funding) < min_funding_bps:
            continue

        entry_price = float(df["close"].iloc[entry_idx])
        exit_price = float(df["close"].iloc[exit_idx])
        if entry_price == 0 or exit_price == 0:
            continue

        # After high funding settlement: buy (pressure reversed)
        direction = 1 if funding > 0 else -1
        gross = direction * (exit_price / entry_price - 1) * 1e4
        net = gross - TOTAL_COST
        pnl = size_usdt * direction * (exit_price / entry_price - 1) - size_usdt * TOTAL_COST / 1e4

        trades.append(Trade(
            symbol=sym, entry_time=str(df.index[entry_idx]),
            exit_time=str(df.index[exit_idx]),
            direction="LONG" if direction == 1 else "SHORT",
            hold_min=exit_offset - entry_offset,
            gross_bps=round(gross, 2), net_bps=round(net, 2),
            pnl_usdt=round(pnl, 4), funding_bps=round(funding, 3),
        ))

    return trades


# ── Analysis ──────────────────────────────────────────────────────────

def run_strategy(strat_fn, label: str, days: int = 365, **kwargs) -> dict:
    """Run strategy across all symbols."""
    all_trades = []
    for sym in SYMBOLS:
        df = load_symbol(sym, days)
        if df is None:
            continue
        trades = strat_fn(sym, df, **kwargs)
        all_trades.extend(trades)
        del df

    return analyze_trades(all_trades, label)


def analyze_trades(trades: list[Trade], label: str) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0, "avg_net": 0, "win_rate": 0}

    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    avg_net = float(np.mean([t.net_bps for t in trades]))
    avg_gross = float(np.mean([t.gross_bps for t in trades]))

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:    {n} ({n/365:.1f}/day)")
    print(f"  Win rate:  {wins/n*100:.0f}%")
    print(f"  Gross avg: {avg_gross:+.2f} bps")
    print(f"  Net avg:   {avg_net:+.2f} bps")
    print(f"  Total P&L: ${total_pnl:+.2f}")
    print(f"  Monthly:   ${total_pnl/12:+.2f} ({total_pnl/12/10:.2f}%)")

    # By symbol
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    sym_pnl = sorted([(s, sum(t.pnl_usdt for t in ts), len(ts),
                        float(np.mean([t.net_bps for t in ts])))
                       for s, ts in by_sym.items()], key=lambda x: x[1], reverse=True)
    winners = sum(1 for _, p, _, _ in sym_pnl if p > 0)
    print(f"\n  Symbols: {winners}/{len(sym_pnl)} profitable")
    for s, p, c, avg in sym_pnl[:5]:
        print(f"    {s:<12} {c:>5}t  {avg:>+6.2f} bps  ${p:>+7.2f}")
    if len(sym_pnl) > 5:
        print(f"    ...")
        for s, p, c, avg in sym_pnl[-3:]:
            print(f"    {s:<12} {c:>5}t  {avg:>+6.2f} bps  ${p:>+7.2f}")

    # By month
    by_month = defaultdict(float)
    for t in trades:
        by_month[t.entry_time[:7]] += t.pnl_usdt
    losing = sum(1 for v in by_month.values() if v <= 0)
    print(f"\n  Months: {len(by_month)-losing}/{len(by_month)} winning")
    for m in sorted(by_month.keys()):
        marker = "✓" if by_month[m] > 0 else "✗"
        print(f"    {m}  ${by_month[m]:>+8.2f} {marker}")

    # By settlement hour
    by_hour = defaultdict(list)
    for t in trades:
        h = int(t.entry_time[11:13])
        # Map to nearest settlement
        for sh in SETTLEMENT_HOURS:
            if abs(h - sh) <= 2 or abs(h - sh - 24) <= 2:
                by_hour[sh].append(t)
                break
    if by_hour:
        print(f"\n  By settlement:")
        for h in sorted(by_hour.keys()):
            ht = by_hour[h]
            hp = sum(t.pnl_usdt for t in ht)
            ha = float(np.mean([t.net_bps for t in ht]))
            print(f"    {h:02d}h UTC: {len(ht)} trades, {ha:+.2f} bps, ${hp:+.2f}")

    # Drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda t: t.entry_time):
        cum += t.pnl_usdt
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    print(f"\n  Max drawdown: ${max_dd:.2f}")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins / n,
        "avg_gross": avg_gross, "avg_net": avg_net,
        "pnl": total_pnl, "max_dd": max_dd,
        "losing_months": losing, "total_months": len(by_month),
    }


def main():
    print("=" * 60)
    print("  8-HOUR SETTLEMENT CYCLE BACKTEST — 1 Year")
    print("  Does price oscillate predictably around funding?")
    print("=" * 60)

    results = []

    # ── Part 1: Raw price pattern ────────────────────────────────
    print("\n" + "▓" * 60)
    print("  PART 1: Average price pattern around settlements")
    print("▓" * 60)

    print("\nAnalyzing settlement patterns...")
    t0 = time.time()
    pattern_df = analyze_all_patterns(365)
    print(f"  {len(pattern_df)} settlement events in {time.time()-t0:.0f}s")

    # Overall pattern
    ret_cols = [c for c in pattern_df.columns if c.startswith("ret_")]
    print("\n  Average return (bps) relative to settlement (t=0):")
    print(f"  {'Offset':<10} {'All':>8} {'Fund>0':>8} {'Fund<0':>8} {'Fund>2':>8}")
    print(f"  {'-'*40}")
    for col in sorted(ret_cols, key=lambda c: int(c.split("_")[1].replace("m", ""))):
        offset = col.replace("ret_", "").replace("m", "")
        all_mean = pattern_df[col].mean()
        pos = pattern_df[pattern_df["funding_bps"] > 0][col].mean()
        neg = pattern_df[pattern_df["funding_bps"] < 0][col].mean()
        high = pattern_df[pattern_df["funding_bps"] > 2][col].mean()
        sign = "+" if int(offset) >= 0 else ""
        print(f"  t{sign}{offset:>4}min  {all_mean:>+7.2f}  {pos:>+7.2f}  {neg:>+7.2f}  {high:>+7.2f}")

    # ── Part 2: Trading strategies ───────────────────────────────
    print("\n\n" + "▓" * 60)
    print("  PART 2: Trading strategies")
    print("▓" * 60)

    # A. FADE: buy before settle (when fund>0), sell after
    configs = [
        # (label, strat_fn, kwargs)
        ("A1. Fade -30/+30 (any funding)", strat_settlement_fade,
         {"entry_offset": -30, "exit_offset": 30, "min_funding_bps": 0}),
        ("A2. Fade -30/+30 (fund>1bps)", strat_settlement_fade,
         {"entry_offset": -30, "exit_offset": 30, "min_funding_bps": 1.0}),
        ("A3. Fade -30/+30 (fund>2bps)", strat_settlement_fade,
         {"entry_offset": -30, "exit_offset": 30, "min_funding_bps": 2.0}),
        ("A4. Fade -60/+60 (fund>1bps)", strat_settlement_fade,
         {"entry_offset": -60, "exit_offset": 60, "min_funding_bps": 1.0}),
        ("A5. Fade -15/+15 (fund>1bps)", strat_settlement_fade,
         {"entry_offset": -15, "exit_offset": 15, "min_funding_bps": 1.0}),
        ("A6. Fade -30/+60 (fund>1bps)", strat_settlement_fade,
         {"entry_offset": -30, "exit_offset": 60, "min_funding_bps": 1.0}),

        # B. MOMENTUM: short before settle (when fund>0)
        ("B1. Momentum -30/+30 (any)", strat_settlement_momentum,
         {"entry_offset": -30, "exit_offset": 30, "min_funding_bps": 0}),
        ("B2. Momentum -30/+30 (fund>1)", strat_settlement_momentum,
         {"entry_offset": -30, "exit_offset": 30, "min_funding_bps": 1.0}),
        ("B3. Momentum -60/0 (fund>1)", strat_settlement_momentum,
         {"entry_offset": -60, "exit_offset": 0, "min_funding_bps": 1.0}),
        ("B4. Momentum -30/0 (fund>1)", strat_settlement_momentum,
         {"entry_offset": -30, "exit_offset": 0, "min_funding_bps": 1.0}),

        # C. POST-SETTLE REVERSION: buy after settle (when fund was >0)
        ("C1. Post-settle +1/+30 (any)", strat_post_settlement_reversion,
         {"entry_offset": 1, "exit_offset": 30, "min_funding_bps": 0}),
        ("C2. Post-settle +1/+30 (fund>1)", strat_post_settlement_reversion,
         {"entry_offset": 1, "exit_offset": 30, "min_funding_bps": 1.0}),
        ("C3. Post-settle +1/+60 (fund>1)", strat_post_settlement_reversion,
         {"entry_offset": 1, "exit_offset": 60, "min_funding_bps": 1.0}),
        ("C4. Post-settle +5/+60 (fund>2)", strat_post_settlement_reversion,
         {"entry_offset": 5, "exit_offset": 60, "min_funding_bps": 2.0}),
        ("C5. Post-settle +1/+120 (fund>1)", strat_post_settlement_reversion,
         {"entry_offset": 1, "exit_offset": 120, "min_funding_bps": 1.0}),
    ]

    for label, fn, kwargs in configs:
        print(f"\nRunning: {label}")
        t0 = time.time()
        r = run_strategy(fn, label, days=365, **kwargs)
        print(f"  ({time.time()-t0:.0f}s)")
        results.append(r)

    # ── SUMMARY ──────────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  FINAL SUMMARY — 8H CYCLE STRATEGIES (1 Year)")
    print("█" * 70)
    print(f"\n  {'Config':<42} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'DD$':>8} {'L.Mo':>5}")
    print(f"  {'-'*82}")
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r["pnl"] > 0 else "✗"
        lm = f"{r.get('losing_months', '?')}/{r.get('total_months', '?')}"
        print(f"  {r['label']:<42} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.2f} ${r['pnl']:>+8.2f} ${r.get('max_dd',0):>7.2f} {lm:>5} {m}")

    valid = [r for r in results if r["trades"] >= 50]
    if valid:
        best = max(valid, key=lambda r: r["pnl"])
        print(f"\n  BEST: {best['label']} → ${best['pnl']:+.2f}/an = ${best['pnl']/12:+.2f}/mois")


if __name__ == "__main__":
    main()
