"""Backtest: Volume as proxy for OI rising — 1 year validation.

Key insight from OI filter tests:
  Extreme Reversion >150bps + OI rising >1% = +8.1 bps net (62 trades / 27d)

OI data limited to 27 days. Volume available for 1 year.
Hypothesis: volume surge during extreme move ≈ OI surge (new positions entering).

Tests:
  A. Extreme Rev + Volume surge (proxy for OI rising)
  B. Cross-validate: OI vs Volume on 27-day overlap period
  C. Best config on 1 year with monthly breakdown

Usage:
    python3 -m analysis.backtest_volume_proxy
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
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "output", "vol_proxy_results")

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "ZROUSDT",
    "AAVEUSDT", "SUIUSDT", "AVAXUSDT", "XRPUSDT", "XMRUSDT",
    "XLMUSDT", "TONUSDT", "LTCUSDT",
]

COST_BPS = 3.0
SLIPPAGE_BPS = 1.0
TOTAL_COST = COST_BPS + SLIPPAGE_BPS

TRADE_SESSIONS = {"asian": (0, 8), "overnight": (21, 24)}


def load_data(days: int) -> dict[str, pd.DataFrame]:
    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)
    data = {}

    for sym in SYMBOLS:
        prefix = os.path.join(DATA_DIR, sym)
        kf = f"{prefix}_klines_1m.csv"
        if not os.path.exists(kf):
            continue

        klines = pd.read_csv(kf)
        klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
        klines = klines.set_index("timestamp").sort_index()
        klines = klines[start_ts:end_ts]
        if len(klines) < 1000:
            continue
        df = klines[["close", "high", "low", "volume"]].copy()

        # OI (optional — only ~27 days)
        of = f"{prefix}_oi_5m.csv"
        if os.path.exists(of):
            oi = pd.read_csv(of)
            oi["timestamp"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
            oi = oi.set_index("timestamp").sort_index()
            oi = oi[~oi.index.duplicated(keep="last")]
            df = df.join(oi[["oi"]], how="left")
            df["oi"] = df["oi"].ffill()
        else:
            df["oi"] = np.nan

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

        # Pre-compute
        df["ret_60m"] = df["close"].pct_change(60) * 1e4  # 1h return bps
        df["oi_chg_60m"] = df["oi"].pct_change(60) * 100  # 1h OI change %

        # Volume metrics
        df["vol_60m"] = df["volume"].rolling(60).sum()  # total volume last 60 min
        # Volume z-score: how unusual is current 60m volume vs rolling 24h?
        vol_roll_mean = df["vol_60m"].rolling(1440, min_periods=360).mean()
        vol_roll_std = df["vol_60m"].rolling(1440, min_periods=360).std()
        df["vol_zscore"] = (df["vol_60m"] - vol_roll_mean) / vol_roll_std.replace(0, np.nan)
        df["vol_zscore"] = df["vol_zscore"].fillna(0)

        # Volume change % (60m vs previous 60m)
        df["vol_chg_60m"] = df["vol_60m"].pct_change(60) * 100

        # Volume ratio: current 60m / average 60m over last 24h
        df["vol_ratio"] = df["vol_60m"] / vol_roll_mean.replace(0, np.nan)
        df["vol_ratio"] = df["vol_ratio"].fillna(1.0)

        df["funding_bps"] = df["funding_rate"] * 1e4

        data[sym] = df

    return data


def in_session(ts):
    h = ts.hour
    return any(s <= h < e for s, e in TRADE_SESSIONS.values())


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
    vol_zscore: float = 0.0
    vol_ratio: float = 0.0
    oi_chg: float = 0.0


def simulate(entries: list[dict], data: dict[str, pd.DataFrame],
             strategy_name: str,
             stop_loss_bps: float = -50.0,
             trail_activate_bps: float = 25.0,
             trail_drawdown_bps: float = 15.0,
             hold_max_min: int = 120,
             size_usdt: float = 250.0) -> list[Trade]:
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
                    vol_zscore=round(entry.get("vol_zscore", 0), 2),
                    vol_ratio=round(entry.get("vol_ratio", 0), 2),
                    oi_chg=round(entry.get("oi_chg", 0), 3),
                ))
                cooldowns[sym] = df.index[idx + j] + timedelta(minutes=30)
                break

    return trades


# ── Strategies ────────────────────────────────────────────────────────

def strat_extreme_rev_vol_surge(data, thresh_bps=150.0, vol_zscore_min=0.0,
                                 vol_ratio_min=1.0):
    """Extreme reversion + volume surge (proxy for OI rising)."""
    entries = []
    for sym, df in data.items():
        for i in range(1441, len(df)):  # need 24h warmup for vol stats
            ts = df.index[i]
            if not in_session(ts):
                continue

            ret = df["ret_60m"].iloc[i]
            vol_z = df["vol_zscore"].iloc[i]
            vol_r = df["vol_ratio"].iloc[i]

            if pd.isna(ret) or pd.isna(vol_z) or pd.isna(vol_r):
                continue
            if abs(ret) < thresh_bps:
                continue
            if vol_z < vol_zscore_min:
                continue
            if vol_r < vol_ratio_min:
                continue

            direction = -1 if ret > 0 else 1

            oi_chg = df["oi_chg_60m"].iloc[i] if not pd.isna(df["oi_chg_60m"].iloc[i]) else 0

            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "vol_zscore": vol_z, "vol_ratio": vol_r, "oi_chg": oi_chg,
            })
    return entries


def strat_extreme_rev_baseline(data, thresh_bps=150.0):
    """Extreme reversion baseline — no volume filter."""
    entries = []
    for sym, df in data.items():
        for i in range(1441, len(df)):
            ts = df.index[i]
            if not in_session(ts):
                continue
            ret = df["ret_60m"].iloc[i]
            if pd.isna(ret):
                continue
            if abs(ret) < thresh_bps:
                continue
            direction = -1 if ret > 0 else 1
            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "vol_zscore": df["vol_zscore"].iloc[i] if not pd.isna(df["vol_zscore"].iloc[i]) else 0,
                "vol_ratio": df["vol_ratio"].iloc[i] if not pd.isna(df["vol_ratio"].iloc[i]) else 1,
            })
    return entries


def strat_extreme_rev_oi_rising(data, thresh_bps=150.0, oi_rise_min=1.0):
    """Original OI rising filter (for cross-validation on 27-day overlap)."""
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
                continue
            direction = -1 if ret > 0 else 1
            vol_z = df["vol_zscore"].iloc[i] if not pd.isna(df["vol_zscore"].iloc[i]) else 0
            vol_r = df["vol_ratio"].iloc[i] if not pd.isna(df["vol_ratio"].iloc[i]) else 1
            entries.append({
                "symbol": sym, "time": ts, "direction": direction,
                "oi_chg": oi_chg, "vol_zscore": vol_z, "vol_ratio": vol_r,
            })
    return entries


# ── Analysis ──────────────────────────────────────────────────────────

def analyze(trades: list[Trade], label: str, show_monthly=False) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0, "win_rate": 0, "avg_net": 0}

    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    avg_net = np.mean([t.net_bps for t in trades])
    avg_gross = np.mean([t.gross_bps for t in trades])
    avg_hold = np.mean([t.hold_min for t in trades])

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:     {n}  ({n/max(1,len(set(t.entry_time[:10] for t in trades))):.1f}/day)")
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

    # Trail/SL ratio
    trail_trades = [t for t in trades if t.reason == "trail_stop"]
    sl_trades = [t for t in trades if t.reason == "stop_loss"]
    trail_pnl = sum(t.pnl_usdt for t in trail_trades)
    sl_pnl = sum(t.pnl_usdt for t in sl_trades)
    print(f"\n  Trail/SL ratio: {len(trail_trades)}:{len(sl_trades)} = "
          f"{len(trail_trades)/max(1,len(sl_trades)):.2f}x")
    print(f"  Trail P&L: ${trail_pnl:+.2f} | SL P&L: ${sl_pnl:+.2f} | "
          f"Coverage: {trail_pnl/max(0.01,abs(sl_pnl))*100:.0f}%")

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
        marker = " ✓" if p > 0 else " ✗"
        print(f"  {s:<12} {c:>7} {avg:>+7.1f} {'$'+f'{p:+.2f}':>10}{marker}")

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

    # Daily stats
    by_day = defaultdict(float)
    for t in trades:
        by_day[t.entry_time[:10]] += t.pnl_usdt
    win_days = sum(1 for v in by_day.values() if v > 0)
    if by_day:
        print(f"  Days: {len(by_day)} total, {win_days} winning ({win_days/len(by_day)*100:.0f}%)")
        print(f"  Daily: avg ${np.mean(list(by_day.values())):+.2f}, "
              f"best ${max(by_day.values()):+.2f}, worst ${min(by_day.values()):+.2f}")

    # Monthly breakdown
    if show_monthly:
        by_month = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            m = t.entry_time[:7]
            by_month[m]["trades"] += 1
            by_month[m]["pnl"] += t.pnl_usdt
            if t.pnl_usdt > 0:
                by_month[m]["wins"] += 1

        print(f"\n  {'Month':<10} {'Trades':>7} {'Win%':>6} {'P&L':>10} {'bps/t':>8}")
        print(f"  {'-'*44}")
        losing_months = 0
        for m in sorted(by_month.keys()):
            md = by_month[m]
            wr = md["wins"] / md["trades"] * 100 if md["trades"] > 0 else 0
            avg = md["pnl"] / md["trades"] * 1e4 / 250 if md["trades"] > 0 else 0
            marker = "✓" if md["pnl"] > 0 else "✗"
            if md["pnl"] <= 0:
                losing_months += 1
            pnl_str = f"${md['pnl']:+.2f}"
            print(f"  {m:<10} {md['trades']:>7} {wr:>5.0f}% {pnl_str:>10} "
                  f"{avg:>+7.1f} {marker}")
        total_months = len(by_month)
        print(f"\n  Losing months: {losing_months}/{total_months}")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_gross": avg_gross, "avg_net": avg_net,
        "pnl": total_pnl, "max_dd": max_dd, "avg_hold": avg_hold,
    }


def main():
    print("=" * 60)
    print("  VOLUME PROXY BACKTEST — 1 Year Validation")
    print("  Can volume proxy for OI rising during extreme moves?")
    print("=" * 60)

    results = []

    # ── PART A: Cross-validate OI vs Volume on 27-day overlap ────────
    print("\n" + "▓" * 60)
    print("  PART A: Cross-validation (27 days)")
    print("  Compare OI rising vs Volume surge on same period")
    print("▓" * 60)

    print("\nLoading 27-day data...")
    data27 = load_data(27)
    print(f"Loaded {len(data27)} symbols")

    # A1. OI rising >1% (known winner)
    entries = strat_extreme_rev_oi_rising(data27, thresh_bps=150, oi_rise_min=1.0)
    trades = simulate(entries, data27, "oi_rise_1pct")
    r = analyze(trades, "A1. OI rising >1% (reference)")
    results.append(r)

    # A2. Volume z-score > 1.0
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_zscore_min=1.0)
    trades = simulate(entries, data27, "vol_z1")
    r = analyze(trades, "A2. Vol z-score > 1.0")
    results.append(r)

    # A3. Volume z-score > 1.5
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_zscore_min=1.5)
    trades = simulate(entries, data27, "vol_z15")
    r = analyze(trades, "A3. Vol z-score > 1.5")
    results.append(r)

    # A4. Volume z-score > 2.0
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_zscore_min=2.0)
    trades = simulate(entries, data27, "vol_z20")
    r = analyze(trades, "A4. Vol z-score > 2.0")
    results.append(r)

    # A5. Volume ratio > 1.5x average
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_ratio_min=1.5)
    trades = simulate(entries, data27, "vol_r15")
    r = analyze(trades, "A5. Vol ratio > 1.5x")
    results.append(r)

    # A6. Volume ratio > 2.0x average
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_ratio_min=2.0)
    trades = simulate(entries, data27, "vol_r20")
    r = analyze(trades, "A6. Vol ratio > 2.0x")
    results.append(r)

    # A7. Volume ratio > 2.5x
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_ratio_min=2.5)
    trades = simulate(entries, data27, "vol_r25")
    r = analyze(trades, "A7. Vol ratio > 2.5x")
    results.append(r)

    # A8. Volume ratio > 3.0x
    entries = strat_extreme_rev_vol_surge(data27, thresh_bps=150, vol_ratio_min=3.0)
    trades = simulate(entries, data27, "vol_r30")
    r = analyze(trades, "A8. Vol ratio > 3.0x")
    results.append(r)

    # Cross-validation summary
    print("\n" + "─" * 60)
    print("  27-DAY CROSS-VALIDATION SUMMARY")
    print("─" * 60)
    print(f"  {'Config':<35} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>8}")
    print(f"  {'-'*65}")
    for r in results:
        if r["trades"] == 0:
            print(f"  {r['label']:<35} {'0':>6}")
            continue
        print(f"  {r['label']:<35} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+7.2f}")

    # Find best volume proxy
    vol_results = [r for r in results if r["trades"] >= 20 and "Vol" in r["label"]]
    if vol_results:
        best_vol = max(vol_results, key=lambda r: r.get("avg_net", -999))
        print(f"\n  Best volume proxy: {best_vol['label']}")

    # ── PART B: 1-Year Backtest with best configs ────────────────────
    print("\n\n" + "▓" * 60)
    print("  PART B: 1-YEAR BACKTEST")
    print("  Testing volume proxy over 365 days")
    print("▓" * 60)

    print("\nLoading 365-day data...")
    t0 = time.time()
    data365 = load_data(365)
    print(f"Loaded {len(data365)} symbols in {time.time()-t0:.1f}s")

    results_1y = []

    # B0. Baseline: no filter
    print("\nRunning 1-year baseline...")
    entries = strat_extreme_rev_baseline(data365, thresh_bps=150)
    trades = simulate(entries, data365, "baseline_1y")
    r = analyze(trades, "B0. Baseline Rev>150 (no filter)", show_monthly=True)
    results_1y.append(r)

    # B1. Vol z-score > 1.0
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_zscore_min=1.0)
    trades = simulate(entries, data365, "vol_z1_1y")
    r = analyze(trades, "B1. Rev>150 + Vol z>1.0", show_monthly=True)
    results_1y.append(r)

    # B2. Vol z-score > 1.5
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_zscore_min=1.5)
    trades = simulate(entries, data365, "vol_z15_1y")
    r = analyze(trades, "B2. Rev>150 + Vol z>1.5", show_monthly=True)
    results_1y.append(r)

    # B3. Vol z-score > 2.0
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_zscore_min=2.0)
    trades = simulate(entries, data365, "vol_z20_1y")
    r = analyze(trades, "B3. Rev>150 + Vol z>2.0", show_monthly=True)
    results_1y.append(r)

    # B4. Vol ratio > 2.0x
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_ratio_min=2.0)
    trades = simulate(entries, data365, "vol_r20_1y")
    r = analyze(trades, "B4. Rev>150 + Vol ratio>2x", show_monthly=True)
    results_1y.append(r)

    # B5. Vol ratio > 2.5x
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_ratio_min=2.5)
    trades = simulate(entries, data365, "vol_r25_1y")
    r = analyze(trades, "B5. Rev>150 + Vol ratio>2.5x", show_monthly=True)
    results_1y.append(r)

    # B6. Vol ratio > 3.0x
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_ratio_min=3.0)
    trades = simulate(entries, data365, "vol_r30_1y")
    r = analyze(trades, "B6. Rev>150 + Vol ratio>3x", show_monthly=True)
    results_1y.append(r)

    # B7. Rev>200 + Vol ratio>2x
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=200, vol_ratio_min=2.0)
    trades = simulate(entries, data365, "rev200_vol_r20_1y")
    r = analyze(trades, "B7. Rev>200 + Vol ratio>2x", show_monthly=True)
    results_1y.append(r)

    # B8. Rev>200 + Vol z>1.5
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=200, vol_zscore_min=1.5)
    trades = simulate(entries, data365, "rev200_vol_z15_1y")
    r = analyze(trades, "B8. Rev>200 + Vol z>1.5", show_monthly=True)
    results_1y.append(r)

    # B9. Tight stops: Vol ratio>2x, SL -30, trail 20/12
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_ratio_min=2.0)
    trades = simulate(entries, data365, "vol_r20_tight_1y",
                      stop_loss_bps=-30.0, trail_activate_bps=20.0, trail_drawdown_bps=12.0)
    r = analyze(trades, "B9. Rev>150 + Vol>2x + tight SL(-30/20/12)", show_monthly=True)
    results_1y.append(r)

    # B10. Wide stops: Vol ratio>2x, SL -70, trail 35/20
    entries = strat_extreme_rev_vol_surge(data365, thresh_bps=150, vol_ratio_min=2.0)
    trades = simulate(entries, data365, "vol_r20_wide_1y",
                      stop_loss_bps=-70.0, trail_activate_bps=35.0, trail_drawdown_bps=20.0)
    r = analyze(trades, "B10. Rev>150 + Vol>2x + wide SL(-70/35/20)", show_monthly=True)
    results_1y.append(r)

    # ── 1-YEAR SUMMARY ───────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  1-YEAR SUMMARY — ALL CONFIGS")
    print("█" * 70)
    print(f"\n  {'Config':<45} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'DD$':>8}")
    print(f"  {'-'*85}")
    for r in results_1y:
        if r["trades"] == 0:
            print(f"  {r['label']:<45} {'0':>6}")
            continue
        marker = "✓" if r["pnl"] > 0 else "✗"
        print(f"  {r['label']:<45} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+8.2f} ${r.get('max_dd',0):>7.2f} {marker}")

    valid = [r for r in results_1y if r["trades"] >= 30]
    if valid:
        best = max(valid, key=lambda r: r["pnl"])
        best_net = max(valid, key=lambda r: r.get("avg_net", -999))
        print(f"\n  BEST total P&L:  {best['label']} → ${best['pnl']:+.2f}")
        print(f"  BEST net/trade:  {best_net['label']} → {best_net.get('avg_net',0):+.1f} bps")

    os.makedirs(RESULTS_DIR, exist_ok=True)


if __name__ == "__main__":
    main()
