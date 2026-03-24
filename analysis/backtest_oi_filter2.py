"""Backtest OI as filter — Round 2: Follow-up on findings.

Key insight from round 1: OI RISING during extreme move = better reversion
(new positions create rubber band effect to unwind)

Tests:
4. Extreme Reversion + OI RISING filter (opposite of original hypothesis)
5. OI expansion rate as momentum signal
6. OI squeeze: high OI + low volatility = tension → breakout
7. Smart OI: combine OI direction + magnitude with better thresholds

Usage:
    python3 -m analysis.backtest_oi_filter2
"""

from __future__ import annotations

import csv
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "output", "oi_filter_results")

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "ZROUSDT",
    "AAVEUSDT", "SUIUSDT", "AVAXUSDT", "XRPUSDT", "XMRUSDT",
    "XLMUSDT", "TONUSDT", "LTCUSDT",
]

COST_BPS = 3.0
SLIPPAGE_BPS = 1.0
TOTAL_COST = COST_BPS + SLIPPAGE_BPS

TRADE_SESSIONS = {"asian": (0, 8), "overnight": (21, 24)}


def load_data(days: int = 27) -> dict[str, pd.DataFrame]:
    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)
    data = {}

    for sym in SYMBOLS:
        prefix = os.path.join(DATA_DIR, sym)
        kf = f"{prefix}_klines_1m.csv"
        of = f"{prefix}_oi_5m.csv"
        if not os.path.exists(kf) or not os.path.exists(of):
            continue

        klines = pd.read_csv(kf)
        klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
        klines = klines.set_index("timestamp").sort_index()
        klines = klines[start_ts:end_ts]
        if len(klines) < 100:
            continue
        df = klines[["close", "high", "low", "volume"]].copy()

        oi = pd.read_csv(of)
        oi["timestamp"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
        oi = oi.set_index("timestamp").sort_index()
        oi = oi[~oi.index.duplicated(keep="last")]
        df = df.join(oi[["oi"]], how="left")
        df["oi"] = df["oi"].ffill()

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

        # Pre-compute
        df["ret_60m"] = df["close"].pct_change(60) * 1e4
        df["ret_30m"] = df["close"].pct_change(30) * 1e4
        df["oi_chg_60m"] = df["oi"].pct_change(60) * 100
        df["oi_chg_30m"] = df["oi"].pct_change(30) * 100
        df["oi_chg_15m"] = df["oi"].pct_change(15) * 100

        # Volatility (realized vol over 30 min)
        df["vol_30m"] = df["close"].pct_change().rolling(30).std() * 1e4

        # OI z-score (7-day window)
        oi_clean = df["oi"].dropna()
        oi_roll_mean = oi_clean.rolling(2016, min_periods=500).mean()
        oi_roll_std = oi_clean.rolling(2016, min_periods=500).std()
        df["oi_zscore"] = (df["oi"] - oi_roll_mean.reindex(df.index, method="ffill")) / \
                          oi_roll_std.reindex(df.index, method="ffill").replace(0, np.nan)
        df["oi_zscore"] = df["oi_zscore"].ffill().fillna(0)

        # OI velocity (rate of change of OI change) = acceleration
        df["oi_accel"] = df["oi_chg_15m"].diff(15)

        df["funding_bps"] = df["funding_rate"] * 1e4

        data[sym] = df

    print(f"Loaded {len(data)} symbols")
    return data


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
    trades = []
    cooldowns: dict[str, pd.Timestamp] = {}

    entries.sort(key=lambda e: e["time"])

    for entry in entries:
        sym = entry["symbol"]
        ts = entry["time"]
        direction = entry["direction"]
        df = data[sym]

        if sym in cooldowns and ts < cooldowns[sym]:
            continue

        idx = df.index.get_indexer([ts], method="ffill")[0]
        if idx < 0 or idx >= len(df) - 10:
            continue
        entry_price = float(df["close"].iloc[idx])
        if entry_price == 0:
            continue

        peak_bps = 0.0
        for j in range(1, min(hold_max_min + 1, len(df) - idx)):
            current_price = float(df["close"].iloc[idx + j])
            if current_price == 0:
                continue

            unrealized = direction * (current_price / entry_price - 1) * 1e4
            if unrealized > peak_bps:
                peak_bps = unrealized

            exit_reason = None
            if j >= hold_max_min:
                exit_reason = "timeout"
            elif unrealized < stop_loss_bps:
                exit_reason = "stop_loss"
            elif j >= 10 and peak_bps >= trail_activate_bps and \
                    unrealized < peak_bps - trail_drawdown_bps:
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
                break

    return trades


def in_session(ts):
    h = ts.hour
    return any(s <= h < e for s, e in TRADE_SESSIONS.values())


# ── Strategy 4: Extreme Rev + OI RISING (rubber band) ────────────────

def strat_extreme_rev_oi_rising(data, thresh_bps=150.0, oi_rise_min=0.0):
    """OI rising during extreme move = new positions = rubber band snap back."""
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df)):
            ts = df.index[i]
            if not in_session(ts):
                continue
            ret = df["ret_60m"].iloc[i]
            oi_chg = df["oi_chg_60m"].iloc[i]
            if pd.isna(ret) or pd.isna(oi_chg):
                continue
            if abs(ret) < thresh_bps:
                continue
            if oi_chg < oi_rise_min:
                continue  # OI must be rising

            direction = -1 if ret > 0 else 1
            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "oi_zscore": df["oi_zscore"].iloc[i],
                "funding_bps": df["funding_bps"].iloc[i],
            })
    return entries


# ── Strategy 5: OI Velocity Momentum ─────────────────────────────────

def strat_oi_velocity_momentum(data, oi_chg_thresh=1.5, lookback=30):
    """When OI surges sharply, follow the price direction (momentum)."""
    entries = []
    for sym, df in data.items():
        col = f"oi_chg_{lookback}m" if f"oi_chg_{lookback}m" in df.columns else "oi_chg_30m"
        for i in range(61, len(df)):
            ts = df.index[i]
            if not in_session(ts):
                continue
            oi_chg = df[col].iloc[i]
            ret = df[f"ret_{lookback}m"].iloc[i] if f"ret_{lookback}m" in df.columns else df["ret_30m"].iloc[i]
            if pd.isna(oi_chg) or pd.isna(ret):
                continue
            if abs(oi_chg) < oi_chg_thresh:
                continue

            # Follow the price direction when OI surges
            if ret > 5 and oi_chg > oi_chg_thresh:
                direction = 1  # price up + OI up = strong momentum → follow
            elif ret < -5 and oi_chg > oi_chg_thresh:
                direction = -1  # price down + OI up = shorts piling in → follow
            else:
                continue

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "oi_zscore": df["oi_zscore"].iloc[i],
            })
    return entries


# ── Strategy 6: OI Squeeze → Breakout ────────────────────────────────

def strat_oi_squeeze(data, oi_zscore_min=1.5, vol_max=5.0, breakout_bps=30.0):
    """High OI + low vol = tension. When price breaks out, follow."""
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df)):
            ts = df.index[i]
            if not in_session(ts):
                continue

            zscore = df["oi_zscore"].iloc[i]
            vol = df["vol_30m"].iloc[i]
            ret_30 = df["ret_30m"].iloc[i]

            if pd.isna(zscore) or pd.isna(vol) or pd.isna(ret_30):
                continue

            # High OI (lots of positions)
            if abs(zscore) < oi_zscore_min:
                continue
            # Low recent volatility (compression)
            if vol > vol_max:
                continue
            # Breakout just happened
            if abs(ret_30) < breakout_bps:
                continue

            # Follow the breakout
            direction = 1 if ret_30 > 0 else -1

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": df["oi_chg_30m"].iloc[i] if not pd.isna(df["oi_chg_30m"].iloc[i]) else 0,
                "oi_zscore": zscore,
            })
    return entries


# ── Strategy 7: Smart OI — Extreme + OI rising + Funding aligned ─────

def strat_smart_oi(data, thresh_bps=150.0, oi_rise_min=0.5, fund_aligned=False):
    """
    Combine best findings:
    - Extreme move > threshold
    - OI rising (rubber band)
    - Optionally: funding aligned (funding pushes same direction as our trade)
    """
    entries = []
    for sym, df in data.items():
        for i in range(61, len(df)):
            ts = df.index[i]
            if not in_session(ts):
                continue

            ret = df["ret_60m"].iloc[i]
            oi_chg = df["oi_chg_60m"].iloc[i]
            funding = df["funding_bps"].iloc[i]

            if pd.isna(ret) or pd.isna(oi_chg) or pd.isna(funding):
                continue
            if abs(ret) < thresh_bps:
                continue
            if oi_chg < oi_rise_min:
                continue

            direction = -1 if ret > 0 else 1

            # Optional: funding must be aligned with our trade
            if fund_aligned:
                # If we're going long, funding should be positive (longs are paying = crowded long = contrarian)
                # If we're going short, funding should be negative
                if direction == 1 and funding < 0:
                    continue
                if direction == -1 and funding > 0:
                    continue

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "oi_zscore": df["oi_zscore"].iloc[i],
                "funding_bps": funding,
            })
    return entries


# ── Strategy 8: OI as dynamic stop loss ──────────────────────────────

def simulate_trades_dynamic_sl(entries: list[dict], data: dict[str, pd.DataFrame],
                                strategy_name: str,
                                base_sl: float = -50.0,
                                tight_sl: float = -25.0,
                                oi_watch_period: int = 15,
                                trail_activate_bps: float = 25.0,
                                trail_drawdown_bps: float = 15.0,
                                hold_max_min: int = 120,
                                size_usdt: float = 250.0) -> list[Trade]:
    """
    After entry, monitor OI:
    - If OI starts moving against us (new positions in opposite direction), tighten SL
    - If OI confirms our trade, use normal SL
    """
    trades = []
    cooldowns: dict[str, pd.Timestamp] = {}
    entries.sort(key=lambda e: e["time"])

    for entry in entries:
        sym = entry["symbol"]
        ts = entry["time"]
        direction = entry["direction"]
        df = data[sym]

        if sym in cooldowns and ts < cooldowns[sym]:
            continue

        idx = df.index.get_indexer([ts], method="ffill")[0]
        if idx < 0 or idx >= len(df) - 10:
            continue
        entry_price = float(df["close"].iloc[idx])
        entry_oi = float(df["oi"].iloc[idx]) if not pd.isna(df["oi"].iloc[idx]) else 0
        if entry_price == 0:
            continue

        peak_bps = 0.0
        for j in range(1, min(hold_max_min + 1, len(df) - idx)):
            current_price = float(df["close"].iloc[idx + j])
            if current_price == 0:
                continue

            unrealized = direction * (current_price / entry_price - 1) * 1e4
            if unrealized > peak_bps:
                peak_bps = unrealized

            # Dynamic stop loss based on OI behavior after entry
            current_oi = float(df["oi"].iloc[idx + j]) if not pd.isna(df["oi"].iloc[idx + j]) else entry_oi
            oi_since_entry = (current_oi / entry_oi - 1) * 100 if entry_oi > 0 else 0

            # If OI is dropping (positions closing = less fuel for our trade) → tighten SL
            # If OI is rising (positions building = more fuel) → normal SL
            sl = tight_sl if oi_since_entry < -0.3 else base_sl

            exit_reason = None
            if j >= hold_max_min:
                exit_reason = "timeout"
            elif unrealized < sl:
                exit_reason = "stop_loss"
            elif j >= 10 and peak_bps >= trail_activate_bps and \
                    unrealized < peak_bps - trail_drawdown_bps:
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
                break
    return trades


def analyze(trades: list[Trade], label: str) -> dict:
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
    print(f"  Total P&L:  ${total_pnl:+.2f}")
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

    # By symbol (top/bottom)
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

    by_day = defaultdict(float)
    for t in trades:
        by_day[t.entry_time[:10]] += t.pnl_usdt
    win_days = sum(1 for v in by_day.values() if v > 0)
    if by_day:
        print(f"  Days: {len(by_day)} total, {win_days} winning ({win_days/len(by_day)*100:.0f}%)")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_gross": avg_gross, "avg_net": avg_net,
        "pnl": total_pnl, "max_dd": max_dd, "avg_hold": avg_hold,
    }


def main():
    print("=" * 60)
    print("  OI FILTER BACKTEST — Round 2")
    print("  Testing the INVERSE hypothesis: OI rising = rubber band")
    print("=" * 60)

    print("\nLoading data...")
    data = load_data(27)

    results = []

    # ── TEST 4: Extreme Rev + OI Rising (rubber band) ────────────────
    print("\n" + "▓" * 60)
    print("  TEST 4: Extreme Rev + OI RISING (rubber band)")
    print("▓" * 60)

    # 4a. >150 + OI rising any
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=150, oi_rise_min=0.0)
    trades = simulate_trades(entries, data, "rev150_oi_up_0")
    results.append(analyze(trades, "4a. Rev>150 + OI rising (any)"))

    # 4b. >150 + OI rising >0.5%
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=150, oi_rise_min=0.5)
    trades = simulate_trades(entries, data, "rev150_oi_up_05")
    results.append(analyze(trades, "4b. Rev>150 + OI rising >0.5%"))

    # 4c. >150 + OI rising >1%
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=150, oi_rise_min=1.0)
    trades = simulate_trades(entries, data, "rev150_oi_up_1")
    results.append(analyze(trades, "4c. Rev>150 + OI rising >1%"))

    # 4d. >200 + OI rising any
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=200, oi_rise_min=0.0)
    trades = simulate_trades(entries, data, "rev200_oi_up_0")
    results.append(analyze(trades, "4d. Rev>200 + OI rising (any)"))

    # 4e. >200 + OI rising >0.5%
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=200, oi_rise_min=0.5)
    trades = simulate_trades(entries, data, "rev200_oi_up_05")
    results.append(analyze(trades, "4e. Rev>200 + OI rising >0.5%"))

    # ── TEST 5: OI Velocity Momentum ─────────────────────────────────
    print("\n" + "▓" * 60)
    print("  TEST 5: OI Velocity Momentum (follow OI surge)")
    print("▓" * 60)

    # 5a. OI surge >1.5% in 30m
    entries = strat_oi_velocity_momentum(data, oi_chg_thresh=1.5, lookback=30)
    trades = simulate_trades(entries, data, "oi_vel_15_30m")
    results.append(analyze(trades, "5a. OI surge >1.5% / 30m → follow price"))

    # 5b. OI surge >2% in 30m
    entries = strat_oi_velocity_momentum(data, oi_chg_thresh=2.0, lookback=30)
    trades = simulate_trades(entries, data, "oi_vel_20_30m")
    results.append(analyze(trades, "5b. OI surge >2% / 30m → follow price"))

    # 5c. OI surge >3% in 30m
    entries = strat_oi_velocity_momentum(data, oi_chg_thresh=3.0, lookback=30)
    trades = simulate_trades(entries, data, "oi_vel_30_30m")
    results.append(analyze(trades, "5c. OI surge >3% / 30m → follow price"))

    # ── TEST 6: OI Squeeze Breakout ──────────────────────────────────
    print("\n" + "▓" * 60)
    print("  TEST 6: OI Squeeze → Breakout")
    print("▓" * 60)

    # 6a. OI z>1.5, vol<5bps, breakout>30bps
    entries = strat_oi_squeeze(data, oi_zscore_min=1.5, vol_max=5.0, breakout_bps=30.0)
    trades = simulate_trades(entries, data, "squeeze_15_5_30")
    results.append(analyze(trades, "6a. OI z>1.5 + vol<5 + breakout>30bps"))

    # 6b. OI z>1.0, vol<7bps, breakout>50bps
    entries = strat_oi_squeeze(data, oi_zscore_min=1.0, vol_max=7.0, breakout_bps=50.0)
    trades = simulate_trades(entries, data, "squeeze_10_7_50")
    results.append(analyze(trades, "6b. OI z>1.0 + vol<7 + breakout>50bps"))

    # 6c. OI z>1.0, vol<5bps, breakout>50bps
    entries = strat_oi_squeeze(data, oi_zscore_min=1.0, vol_max=5.0, breakout_bps=50.0)
    trades = simulate_trades(entries, data, "squeeze_10_5_50")
    results.append(analyze(trades, "6c. OI z>1.0 + vol<5 + breakout>50bps"))

    # ── TEST 7: Smart OI Composite ───────────────────────────────────
    print("\n" + "▓" * 60)
    print("  TEST 7: Smart OI (Rev + OI rising + funding aligned)")
    print("▓" * 60)

    # 7a. Rev>150 + OI>0.5% + no funding filter
    entries = strat_smart_oi(data, thresh_bps=150, oi_rise_min=0.5, fund_aligned=False)
    trades = simulate_trades(entries, data, "smart_150_05")
    results.append(analyze(trades, "7a. Rev>150 + OI rise>0.5%"))

    # 7b. Rev>150 + OI>0.5% + funding aligned
    entries = strat_smart_oi(data, thresh_bps=150, oi_rise_min=0.5, fund_aligned=True)
    trades = simulate_trades(entries, data, "smart_150_05_fund")
    results.append(analyze(trades, "7b. Rev>150 + OI rise>0.5% + funding aligned"))

    # 7c. Rev>200 + OI>0.5% + funding aligned
    entries = strat_smart_oi(data, thresh_bps=200, oi_rise_min=0.5, fund_aligned=True)
    trades = simulate_trades(entries, data, "smart_200_05_fund")
    results.append(analyze(trades, "7c. Rev>200 + OI rise>0.5% + funding aligned"))

    # ── TEST 8: Dynamic Stop Loss based on OI ────────────────────────
    print("\n" + "▓" * 60)
    print("  TEST 8: Extreme Rev + Dynamic SL (OI-based)")
    print("▓" * 60)

    # 8a. Baseline extreme rev, fixed SL -50
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=150, oi_rise_min=-999)  # all trades
    trades = simulate_trades(entries, data, "baseline_sl50", stop_loss_bps=-50)
    results.append(analyze(trades, "8a. Rev>150 — fixed SL -50 (baseline)"))

    # 8b. Dynamic SL: tighten to -25 when OI drops after entry
    trades = simulate_trades_dynamic_sl(entries, data, "dynamic_sl",
                                         base_sl=-50, tight_sl=-25)
    results.append(analyze(trades, "8b. Rev>150 — dynamic SL (tight -25 if OI drops)"))

    # 8c. Dynamic SL: tighten to -30 when OI drops
    trades = simulate_trades_dynamic_sl(entries, data, "dynamic_sl_30",
                                         base_sl=-50, tight_sl=-30)
    results.append(analyze(trades, "8c. Rev>150 — dynamic SL (tight -30 if OI drops)"))

    # 8d. Rev>150 + OI rising + dynamic SL
    entries = strat_extreme_rev_oi_rising(data, thresh_bps=150, oi_rise_min=0.0)
    trades = simulate_trades_dynamic_sl(entries, data, "rising_dynamic_sl",
                                         base_sl=-50, tight_sl=-25)
    results.append(analyze(trades, "8d. Rev>150 + OI rising + dynamic SL"))

    # ── SUMMARY ──────────────────────────────────────────────────────
    print("\n\n" + "█" * 60)
    print("  SUMMARY — ROUND 2")
    print("█" * 60)
    print(f"\n  {'Config':<52} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>8} {'DD$':>7}")
    print(f"  {'-'*88}")
    for r in results:
        if r["trades"] == 0:
            print(f"  {r['label']:<52} {'0':>6}")
            continue
        print(f"  {r['label']:<52} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+7.2f} ${r.get('max_dd',0):>6.2f}")

    # Highlight best
    valid = [r for r in results if r["trades"] >= 10]
    if valid:
        best = max(valid, key=lambda r: r.get("avg_net", -999))
        print(f"\n  BEST net/trade: {best['label']}")
        best_pnl = max(valid, key=lambda r: r["pnl"])
        print(f"  BEST total P&L: {best_pnl['label']} → ${best_pnl['pnl']:+.2f}")

    os.makedirs(RESULTS_DIR, exist_ok=True)


if __name__ == "__main__":
    main()
