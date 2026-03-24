"""Backtest OI as filter — 3 hypotheses.

Test 1: Extreme Reversion filtered by OI direction (exhaustion vs new positioning)
Test 2: OI z-score as regime filter (crowded = reversion works better)
Test 3: OI + Funding double exhaustion

Usage:
    python3 -m analysis.backtest_oi_filter
"""

from __future__ import annotations

import csv
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "output", "oi_filter_results")

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "ZROUSDT",
    "AAVEUSDT", "SUIUSDT", "AVAXUSDT", "XRPUSDT", "XMRUSDT",
    "XLMUSDT", "TONUSDT", "LTCUSDT",
]

COST_BPS = 3.0       # BNB roundtrip fees
SLIPPAGE_BPS = 1.0   # roundtrip slippage
TOTAL_COST = COST_BPS + SLIPPAGE_BPS  # 4 bps

TRADE_SESSIONS = {"asian": (0, 8), "overnight": (21, 24)}


# ── Data Loading ──────────────────────────────────────────────────────

def load_data(days: int = 27) -> dict[str, pd.DataFrame]:
    """Load klines + OI + funding for all symbols, limited to OI period."""
    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)
    data = {}

    for sym in SYMBOLS:
        prefix = os.path.join(DATA_DIR, sym)

        # Klines (required)
        kf = f"{prefix}_klines_1m.csv"
        if not os.path.exists(kf):
            continue
        klines = pd.read_csv(kf)
        klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
        klines = klines.set_index("timestamp").sort_index()
        klines = klines[start_ts:end_ts]
        if len(klines) < 100:
            continue
        df = klines[["close", "high", "low", "volume"]].copy()

        # OI (required for this test)
        of = f"{prefix}_oi_5m.csv"
        if not os.path.exists(of):
            continue
        oi = pd.read_csv(of)
        oi["timestamp"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
        oi = oi.set_index("timestamp").sort_index()
        oi = oi[~oi.index.duplicated(keep="last")]
        df = df.join(oi[["oi"]], how="left")
        df["oi"] = df["oi"].ffill()
        if df["oi"].isna().sum() > len(df) * 0.5:
            continue

        # Funding
        ff = f"{prefix}_funding.csv"
        if os.path.exists(ff):
            fund = pd.read_csv(ff)
            fund["timestamp"] = pd.to_datetime(fund["timestamp"], unit="ms", utc=True)
            fund = fund.set_index("timestamp").sort_index()
            fund = fund[~fund.index.duplicated(keep="last")]
            df = df.join(fund[["funding_rate"]], how="left")
            df["funding_rate"] = df["funding_rate"].ffill().fillna(0)
        else:
            df["funding_rate"] = 0.0

        # Pre-compute rolling stats
        df["ret_60m"] = df["close"].pct_change(60) * 1e4  # 1h return in bps
        df["oi_chg_60m"] = df["oi"].pct_change(60) * 100  # 1h OI change in %

        # OI z-score (7-day rolling = 10080 minutes, but OI updates every 5min)
        # Use 2016 periods of 5-min OI = 7 days
        oi_clean = df["oi"].dropna()
        oi_roll_mean = oi_clean.rolling(2016, min_periods=500).mean()
        oi_roll_std = oi_clean.rolling(2016, min_periods=500).std()
        df["oi_zscore"] = (df["oi"] - oi_roll_mean.reindex(df.index, method="ffill")) / \
                          oi_roll_std.reindex(df.index, method="ffill").replace(0, np.nan)
        df["oi_zscore"] = df["oi_zscore"].ffill().fillna(0)

        # Funding in bps
        df["funding_bps"] = df["funding_rate"] * 1e4

        data[sym] = df
        print(f"  {sym}: {len(df)} rows, OI coverage: {df['oi'].notna().mean()*100:.0f}%")

    return data


# ── Position Management ───────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    direction: int  # +1 long, -1 short
    entry_price: float
    entry_idx: int
    entry_time: pd.Timestamp
    size_usdt: float = 250.0
    peak_bps: float = 0.0


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    hold_min: int
    gross_bps: float
    net_bps: float
    pnl_usdt: float
    reason: str
    strategy: str
    oi_chg: float = 0.0
    oi_zscore: float = 0.0
    funding_bps: float = 0.0


def simulate_trades(entries: list[dict], data: dict[str, pd.DataFrame],
                    strategy_name: str,
                    stop_loss_bps: float = -50.0,
                    trail_activate_bps: float = 25.0,
                    trail_drawdown_bps: float = 15.0,
                    hold_max_min: int = 120,
                    size_usdt: float = 250.0,
                    max_positions: int = 4) -> list[Trade]:
    """Simulate position management for a list of entry signals."""
    trades = []
    positions: dict[str, Position] = {}
    cooldowns: dict[str, pd.Timestamp] = {}

    # Sort entries by time
    entries.sort(key=lambda e: e["time"])

    for entry in entries:
        sym = entry["symbol"]
        ts = entry["time"]
        direction = entry["direction"]
        df = data[sym]

        # Check cooldown
        if sym in cooldowns and ts < cooldowns[sym]:
            continue
        if sym in positions:
            continue
        if len(positions) >= max_positions:
            continue

        # Get entry price
        idx = df.index.get_indexer([ts], method="ffill")[0]
        if idx < 0 or idx >= len(df) - 10:
            continue
        entry_price = float(df["close"].iloc[idx])
        if entry_price == 0:
            continue

        pos = Position(
            symbol=sym, direction=direction,
            entry_price=entry_price, entry_idx=idx,
            entry_time=ts, size_usdt=size_usdt,
        )

        # Simulate minute by minute
        exited = False
        for j in range(1, min(hold_max_min + 1, len(df) - idx)):
            row = df.iloc[idx + j]
            current_price = float(row["close"])
            if current_price == 0:
                continue

            unrealized = direction * (current_price / entry_price - 1) * 1e4
            if unrealized > pos.peak_bps:
                pos.peak_bps = unrealized

            exit_reason = None
            if j >= hold_max_min:
                exit_reason = "timeout"
            elif unrealized < stop_loss_bps:
                exit_reason = "stop_loss"
            elif j >= 10 and pos.peak_bps >= trail_activate_bps and \
                    unrealized < pos.peak_bps - trail_drawdown_bps:
                exit_reason = "trail_stop"

            if exit_reason:
                gross_bps = direction * (current_price / entry_price - 1) * 1e4
                net_bps = gross_bps - TOTAL_COST
                pnl_usdt = size_usdt * (direction * (current_price / entry_price - 1)) - \
                           size_usdt * TOTAL_COST / 1e4

                trades.append(Trade(
                    symbol=sym, direction="LONG" if direction == 1 else "SHORT",
                    entry_time=str(ts), exit_time=str(df.index[idx + j]),
                    entry_price=entry_price, exit_price=current_price,
                    hold_min=j, gross_bps=round(gross_bps, 2),
                    net_bps=round(net_bps, 2), pnl_usdt=round(pnl_usdt, 4),
                    reason=exit_reason, strategy=strategy_name,
                    oi_chg=round(entry.get("oi_chg", 0), 3),
                    oi_zscore=round(entry.get("oi_zscore", 0), 2),
                    funding_bps=round(entry.get("funding_bps", 0), 2),
                ))
                cooldowns[sym] = df.index[idx + j] + timedelta(minutes=30)
                exited = True
                break

        if not exited and sym in positions:
            del positions[sym]

    return trades


# ── Strategy 1: Extreme Reversion + OI Filter ────────────────────────

def strategy_extreme_reversion_oi_filter(data: dict[str, pd.DataFrame],
                                          thresh_bps: float = 150.0,
                                          oi_filter: str = "none",
                                          oi_thresh_pct: float = 0.0) -> list[dict]:
    """
    Extreme reversion: fade moves > thresh_bps in 1h.
    OI filter modes:
      - "none": no filter (baseline)
      - "exhaustion": only trade when OI drops (exhaustion)
      - "new_money": only trade when OI rises (new money enters)
    """
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df), 1):  # step 1 min
            ts = df.index[i]

            # Session filter
            h = ts.hour
            in_session = any(s <= h < e for s, e in TRADE_SESSIONS.values())
            if not in_session:
                continue

            ret = df["ret_60m"].iloc[i]
            oi_chg = df["oi_chg_60m"].iloc[i]

            if pd.isna(ret) or pd.isna(oi_chg):
                continue

            # Extreme move?
            if abs(ret) < thresh_bps:
                continue

            # Direction: fade the move
            direction = -1 if ret > 0 else 1

            # OI filter
            if oi_filter == "exhaustion" and oi_chg > -oi_thresh_pct:
                continue  # skip if OI didn't drop enough
            elif oi_filter == "new_money" and oi_chg < oi_thresh_pct:
                continue  # skip if OI didn't rise enough

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "oi_zscore": df["oi_zscore"].iloc[i],
                "funding_bps": df["funding_bps"].iloc[i],
            })

    return entries


# ── Strategy 2: Extreme Reversion + OI Z-Score Regime ────────────────

def strategy_extreme_reversion_oi_zscore(data: dict[str, pd.DataFrame],
                                          thresh_bps: float = 150.0,
                                          min_zscore: float = 1.0) -> list[dict]:
    """Only take reversion trades when OI z-score > threshold (overcrowded)."""
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df), 1):
            ts = df.index[i]

            h = ts.hour
            in_session = any(s <= h < e for s, e in TRADE_SESSIONS.values())
            if not in_session:
                continue

            ret = df["ret_60m"].iloc[i]
            zscore = df["oi_zscore"].iloc[i]

            if pd.isna(ret) or pd.isna(zscore):
                continue

            if abs(ret) < thresh_bps:
                continue

            # Only trade when market is overcrowded (high OI relative to recent)
            if abs(zscore) < min_zscore:
                continue

            direction = -1 if ret > 0 else 1

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": df["oi_chg_60m"].iloc[i] if not pd.isna(df["oi_chg_60m"].iloc[i]) else 0,
                "oi_zscore": zscore,
                "funding_bps": df["funding_bps"].iloc[i],
            })

    return entries


# ── Strategy 3: Funding + OI Double Exhaustion ────────────────────────

def strategy_funding_oi_double(data: dict[str, pd.DataFrame],
                                fund_thresh_bps: float = 3.0,
                                oi_drop_thresh: float = 0.5,
                                entry_before_min: int = 60) -> list[dict]:
    """
    Trade when funding is extreme AND OI is dropping = crowded position unwinding.
    Enter before settlement when both conditions met.
    """
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df), 1):
            ts = df.index[i]

            h = ts.hour
            in_session = any(s <= h < e for s, e in TRADE_SESSIONS.values())
            if not in_session:
                continue

            # Minutes to next settlement (00h, 08h, 16h)
            next_settle_h = [0, 8, 16, 24]
            mins_to = min(
                (sh * 60 - h * 60 - ts.minute) % (24 * 60)
                for sh in next_settle_h
                if (sh * 60 - h * 60 - ts.minute) % (24 * 60) > 0
            )

            if mins_to > entry_before_min:
                continue

            funding = df["funding_bps"].iloc[i]
            oi_chg = df["oi_chg_60m"].iloc[i]

            if pd.isna(funding) or pd.isna(oi_chg):
                continue

            # Extreme funding?
            if abs(funding) < fund_thresh_bps:
                continue

            # OI dropping? (the crowd is unwinding)
            if oi_chg > -oi_drop_thresh:
                continue

            # Direction: against the crowd
            # If funding positive (longs pay) + OI dropping = longs closing → go short stays risky
            # Actually: longs closing = selling pressure done → go LONG (reversion)
            # If funding negative (shorts pay) + OI dropping = shorts closing → go SHORT (reversion)
            direction = 1 if funding > 0 else -1

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "oi_zscore": df["oi_zscore"].iloc[i],
                "funding_bps": funding,
            })

    return entries


# ── Analysis & Output ─────────────────────────────────────────────────

def analyze_trades(trades: list[Trade], label: str):
    """Print analysis of trade results."""
    n = len(trades)
    if n == 0:
        print(f"\n{'─'*60}")
        print(f"  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0, "win_rate": 0, "avg_net": 0}

    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    avg_net = np.mean([t.net_bps for t in trades])
    avg_gross = np.mean([t.gross_bps for t in trades])
    avg_hold = np.mean([t.hold_min for t in trades])

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:     {n}")
    print(f"  Win rate:   {wins/n*100:.0f}%")
    print(f"  Gross avg:  {avg_gross:+.1f} bps")
    print(f"  Net avg:    {avg_net:+.1f} bps")
    print(f"  Total P&L:  ${total_pnl:+.2f} (at ${trades[0].pnl_usdt and 250}/trade)")
    print(f"  Avg hold:   {avg_hold:.0f} min")

    # By exit reason
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.reason].append(t)
    print(f"\n  {'Reason':<14} {'Count':>6} {'Win':>6} {'AvgNet':>8} {'Total$':>10}")
    print(f"  {'-'*48}")
    for reason in sorted(by_reason, key=lambda k: -len(by_reason[k])):
        rt = by_reason[reason]
        rw = sum(1 for t in rt if t.pnl_usdt > 0)
        rp = sum(t.pnl_usdt for t in rt)
        rn = np.mean([t.net_bps for t in rt])
        print(f"  {reason:<14} {len(rt):>6} {rw/len(rt)*100:>5.0f}% {rn:>+7.1f} {'$'+f'{rp:+.2f}':>10}")

    # By symbol
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    sym_pnl = [(s, sum(t.pnl_usdt for t in ts), len(ts),
                 np.mean([t.net_bps for t in ts])) for s, ts in by_sym.items()]
    sym_pnl.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  {'Symbol':<12} {'Trades':>7} {'AvgNet':>8} {'Total$':>10}")
    print(f"  {'-'*40}")
    for s, p, c, avg in sym_pnl:
        print(f"  {s:<12} {c:>7} {avg:>+7.1f} {'$'+f'{p:+.2f}':>10}")

    # Max drawdown
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

    # Per-day P&L
    by_day = defaultdict(float)
    for t in trades:
        day = t.entry_time[:10]
        by_day[day] += t.pnl_usdt
    winning_days = sum(1 for v in by_day.values() if v > 0)
    print(f"  Days: {len(by_day)} total, {winning_days} winning ({winning_days/len(by_day)*100:.0f}%)")
    print(f"  Daily P&L: avg ${np.mean(list(by_day.values())):+.2f}, "
          f"best ${max(by_day.values()):+.2f}, worst ${min(by_day.values()):+.2f}")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_gross": avg_gross, "avg_net": avg_net,
        "pnl": total_pnl, "max_dd": max_dd,
        "avg_hold": avg_hold, "winning_days_pct": winning_days/len(by_day) if by_day else 0,
    }


def save_trades(trades: list[Trade], filepath: str):
    """Save trades to CSV."""
    if not trades:
        return
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "direction", "entry_time", "exit_time",
                     "entry_price", "exit_price", "hold_min",
                     "gross_bps", "net_bps", "pnl_usdt",
                     "reason", "strategy", "oi_chg", "oi_zscore", "funding_bps"])
        for t in trades:
            w.writerow([t.symbol, t.direction, t.entry_time, t.exit_time,
                        t.entry_price, t.exit_price, t.hold_min,
                        t.gross_bps, t.net_bps, t.pnl_usdt,
                        t.reason, t.strategy, t.oi_chg, t.oi_zscore, t.funding_bps])


def save_summary(results: list[dict], filepath: str):
    """Save comparison table."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "trades", "win_rate", "avg_gross_bps", "avg_net_bps",
                     "total_pnl", "max_dd", "avg_hold_min", "winning_days_pct"])
        for r in results:
            w.writerow([r["label"], r["trades"], round(r.get("win_rate", 0), 3),
                        round(r.get("avg_gross", 0), 2), round(r.get("avg_net", 0), 2),
                        round(r.get("pnl", 0), 2), round(r.get("max_dd", 0), 2),
                        round(r.get("avg_hold", 0), 1),
                        round(r.get("winning_days_pct", 0), 3)])


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  OI FILTER BACKTEST — 3 Hypotheses")
    print("  27 days, Asia + Overnight sessions")
    print("=" * 60)

    print("\nLoading data...")
    t0 = time.time()
    data = load_data(27)
    print(f"Loaded {len(data)} symbols in {time.time()-t0:.1f}s\n")

    all_results = []
    all_trades = []

    # ── TEST 1: Extreme Reversion + OI Direction Filter ──────────────

    print("\n" + "▓" * 60)
    print("  TEST 1: Extreme Reversion + OI Direction Filter")
    print("▓" * 60)

    # 1a. Baseline: no OI filter
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=150, oi_filter="none")
    trades = simulate_trades(entries, data, "extreme_rev_baseline")
    r = analyze_trades(trades, "1a. Extreme Rev >150bps — NO OI filter (baseline)")
    all_results.append(r)
    all_trades.extend(trades)

    # 1b. OI exhaustion filter (OI drops > 0%)
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=150, oi_filter="exhaustion", oi_thresh_pct=0.0)
    trades = simulate_trades(entries, data, "extreme_rev_oi_exhaust_0")
    r = analyze_trades(trades, "1b. Extreme Rev >150bps — OI dropping (any)")
    all_results.append(r)
    all_trades.extend(trades)

    # 1c. OI exhaustion filter (OI drops > 0.5%)
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=150, oi_filter="exhaustion", oi_thresh_pct=0.5)
    trades = simulate_trades(entries, data, "extreme_rev_oi_exhaust_05")
    r = analyze_trades(trades, "1c. Extreme Rev >150bps — OI drops > 0.5%")
    all_results.append(r)
    all_trades.extend(trades)

    # 1d. OI exhaustion filter (OI drops > 1%)
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=150, oi_filter="exhaustion", oi_thresh_pct=1.0)
    trades = simulate_trades(entries, data, "extreme_rev_oi_exhaust_1")
    r = analyze_trades(trades, "1d. Extreme Rev >150bps — OI drops > 1%")
    all_results.append(r)
    all_trades.extend(trades)

    # 1e. INVERSE: only trade when OI RISES (new money — should be WORSE)
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=150, oi_filter="new_money", oi_thresh_pct=0.0)
    trades = simulate_trades(entries, data, "extreme_rev_oi_newmoney")
    r = analyze_trades(trades, "1e. Extreme Rev >150bps — OI RISING (control, should lose)")
    all_results.append(r)
    all_trades.extend(trades)

    # 1f. Higher threshold 200bps + OI exhaustion
    entries = strategy_extreme_reversion_oi_filter(data, thresh_bps=200, oi_filter="exhaustion", oi_thresh_pct=0.0)
    trades = simulate_trades(entries, data, "extreme_rev200_oi_exhaust")
    r = analyze_trades(trades, "1f. Extreme Rev >200bps — OI dropping")
    all_results.append(r)
    all_trades.extend(trades)

    # ── TEST 2: OI Z-Score Regime Filter ─────────────────────────────

    print("\n" + "▓" * 60)
    print("  TEST 2: Extreme Reversion + OI Z-Score Regime")
    print("▓" * 60)

    # 2a. z-score > 0.5
    entries = strategy_extreme_reversion_oi_zscore(data, thresh_bps=150, min_zscore=0.5)
    trades = simulate_trades(entries, data, "extreme_rev_zscore_05")
    r = analyze_trades(trades, "2a. Extreme Rev >150bps — OI z-score > 0.5")
    all_results.append(r)
    all_trades.extend(trades)

    # 2b. z-score > 1.0
    entries = strategy_extreme_reversion_oi_zscore(data, thresh_bps=150, min_zscore=1.0)
    trades = simulate_trades(entries, data, "extreme_rev_zscore_10")
    r = analyze_trades(trades, "2b. Extreme Rev >150bps — OI z-score > 1.0")
    all_results.append(r)
    all_trades.extend(trades)

    # 2c. z-score > 1.5
    entries = strategy_extreme_reversion_oi_zscore(data, thresh_bps=150, min_zscore=1.5)
    trades = simulate_trades(entries, data, "extreme_rev_zscore_15")
    r = analyze_trades(trades, "2c. Extreme Rev >150bps — OI z-score > 1.5")
    all_results.append(r)
    all_trades.extend(trades)

    # ── TEST 3: Funding + OI Double Exhaustion ───────────────────────

    print("\n" + "▓" * 60)
    print("  TEST 3: Funding + OI Double Exhaustion")
    print("▓" * 60)

    # 3a. Fund > 3bps + OI drop > 0%
    entries = strategy_funding_oi_double(data, fund_thresh_bps=3.0, oi_drop_thresh=0.0)
    trades = simulate_trades(entries, data, "fund3_oi_drop_0")
    r = analyze_trades(trades, "3a. Funding >3bps + OI dropping (any) — 60min pre-settle")
    all_results.append(r)
    all_trades.extend(trades)

    # 3b. Fund > 3bps + OI drop > 0.5%
    entries = strategy_funding_oi_double(data, fund_thresh_bps=3.0, oi_drop_thresh=0.5)
    trades = simulate_trades(entries, data, "fund3_oi_drop_05")
    r = analyze_trades(trades, "3b. Funding >3bps + OI drops > 0.5% — 60min pre-settle")
    all_results.append(r)
    all_trades.extend(trades)

    # 3c. Fund > 2bps + OI drop > 0%
    entries = strategy_funding_oi_double(data, fund_thresh_bps=2.0, oi_drop_thresh=0.0)
    trades = simulate_trades(entries, data, "fund2_oi_drop_0")
    r = analyze_trades(trades, "3c. Funding >2bps + OI dropping (any) — 60min pre-settle")
    all_results.append(r)
    all_trades.extend(trades)

    # 3d. Fund > 2bps + OI drop > 0%, entry 120min before
    entries = strategy_funding_oi_double(data, fund_thresh_bps=2.0, oi_drop_thresh=0.0, entry_before_min=120)
    trades = simulate_trades(entries, data, "fund2_oi_drop_0_120m")
    r = analyze_trades(trades, "3d. Funding >2bps + OI dropping — 120min pre-settle")
    all_results.append(r)
    all_trades.extend(trades)

    # ── SUMMARY ──────────────────────────────────────────────────────

    print("\n\n" + "█" * 60)
    print("  SUMMARY — ALL CONFIGS")
    print("█" * 60)
    print(f"\n  {'Config':<52} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>8} {'DD$':>7}")
    print(f"  {'-'*88}")
    for r in all_results:
        if r["trades"] == 0:
            print(f"  {r['label']:<52} {'0':>6}")
            continue
        print(f"  {r['label']:<52} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+7.2f} ${r.get('max_dd',0):>6.2f}")

    # Save
    os.makedirs(RESULTS_DIR, exist_ok=True)
    save_summary(all_results, os.path.join(RESULTS_DIR, "summary.csv"))
    save_trades(all_trades, os.path.join(RESULTS_DIR, "all_trades.csv"))
    print(f"\n  Results saved to {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
