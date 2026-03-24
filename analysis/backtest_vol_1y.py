"""1-Year backtest: Extreme Reversion + Volume z-score filter.

Optimized for memory: 5-min signal sampling, per-symbol processing.

Best configs from 27-day cross-validation:
  Vol z>1.5: +6.9 bps net, 66% win, 2.47x trail/SL
  Vol z>2.0: +8.3 bps net, 66% win, 2.78x trail/SL

Usage:
    python3 -m analysis.backtest_vol_1y
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

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
TOTAL_COST = COST_BPS + SLIPPAGE_BPS

TRADE_SESSIONS = {"asian": (0, 8), "overnight": (21, 24)}


def in_session(h: int) -> bool:
    return any(s <= h < e for s, e in TRADE_SESSIONS.values())


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    hold_min: int
    gross_bps: float
    net_bps: float
    pnl_usdt: float
    reason: str
    vol_zscore: float = 0.0


def load_symbol(sym: str, days: int) -> pd.DataFrame | None:
    """Load one symbol's data."""
    prefix = os.path.join(DATA_DIR, sym)
    kf = f"{prefix}_klines_1m.csv"
    if not os.path.exists(kf):
        return None

    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)

    klines = pd.read_csv(kf, usecols=["timestamp", "close", "volume"])
    klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
    klines = klines.set_index("timestamp").sort_index()
    klines = klines[start_ts:end_ts]
    if len(klines) < 1500:
        return None

    df = klines.copy()
    # Pre-compute
    df["ret_60m"] = df["close"].pct_change(60) * 1e4
    df["vol_60m"] = df["volume"].rolling(60).sum()
    vol_mean = df["vol_60m"].rolling(1440, min_periods=360).mean()
    vol_std = df["vol_60m"].rolling(1440, min_periods=360).std()
    df["vol_zscore"] = (df["vol_60m"] - vol_mean) / vol_std.replace(0, np.nan)
    df["vol_zscore"] = df["vol_zscore"].fillna(0)
    df["vol_ratio"] = (df["vol_60m"] / vol_mean.replace(0, np.nan)).fillna(1.0)

    return df


def find_entries(df: pd.DataFrame, sym: str, thresh_bps: float,
                 vol_zscore_min: float = 0.0, vol_ratio_min: float = 0.0,
                 step: int = 5) -> list[dict]:
    """Find entry signals, sampling every `step` minutes."""
    entries = []
    indices = range(1441, len(df), step)

    for i in indices:
        ts = df.index[i]
        if not in_session(ts.hour):
            continue

        ret = df["ret_60m"].iloc[i]
        vol_z = df["vol_zscore"].iloc[i]
        vol_r = df["vol_ratio"].iloc[i]

        if pd.isna(ret) or pd.isna(vol_z):
            continue
        if abs(ret) < thresh_bps:
            continue
        if vol_zscore_min > 0 and vol_z < vol_zscore_min:
            continue
        if vol_ratio_min > 0 and vol_r < vol_ratio_min:
            continue

        direction = -1 if ret > 0 else 1
        entries.append({
            "symbol": sym, "time": ts, "direction": direction,
            "idx": i, "vol_zscore": vol_z, "vol_ratio": vol_r,
        })
    return entries


def simulate_symbol(entries: list[dict], df: pd.DataFrame, sym: str,
                    strategy: str,
                    stop_loss: float = -50.0,
                    trail_act: float = 25.0,
                    trail_dd: float = 15.0,
                    hold_max: int = 120,
                    size: float = 250.0) -> list[Trade]:
    """Simulate trades for one symbol."""
    trades = []
    cooldown_until = pd.Timestamp("1970-01-01", tz="UTC")

    for entry in entries:
        ts = entry["time"]
        if ts < cooldown_until:
            continue

        idx = entry["idx"]
        entry_price = float(df["close"].iloc[idx])
        if entry_price == 0:
            continue

        direction = entry["direction"]
        peak_bps = 0.0
        end_idx = min(idx + hold_max + 1, len(df))

        for j in range(1, end_idx - idx):
            cp = float(df["close"].iloc[idx + j])
            if cp == 0:
                continue

            unreal = direction * (cp / entry_price - 1) * 1e4
            if unreal > peak_bps:
                peak_bps = unreal

            reason = None
            if j >= hold_max:
                reason = "timeout"
            elif unreal < stop_loss:
                reason = "stop_loss"
            elif j >= 10 and peak_bps >= trail_act and unreal < peak_bps - trail_dd:
                reason = "trail_stop"

            if reason:
                gross = direction * (cp / entry_price - 1) * 1e4
                net = gross - TOTAL_COST
                pnl = size * direction * (cp / entry_price - 1) - size * TOTAL_COST / 1e4

                trades.append(Trade(
                    symbol=sym,
                    direction="LONG" if direction == 1 else "SHORT",
                    entry_time=str(ts), exit_time=str(df.index[idx + j]),
                    hold_min=j, gross_bps=round(gross, 2),
                    net_bps=round(net, 2), pnl_usdt=round(pnl, 4),
                    reason=reason, vol_zscore=round(entry["vol_zscore"], 2),
                ))
                cooldown_until = df.index[idx + j] + timedelta(minutes=30)
                break

    return trades


def run_config(days: int, thresh_bps: float, vol_zscore_min: float = 0.0,
               vol_ratio_min: float = 0.0, stop_loss: float = -50.0,
               trail_act: float = 25.0, trail_dd: float = 15.0,
               hold_max: int = 120, label: str = "") -> list[Trade]:
    """Run a single config across all symbols."""
    all_trades = []
    for sym in SYMBOLS:
        df = load_symbol(sym, days)
        if df is None:
            continue

        entries = find_entries(df, sym, thresh_bps,
                               vol_zscore_min=vol_zscore_min,
                               vol_ratio_min=vol_ratio_min)
        trades = simulate_symbol(entries, df, sym, label,
                                  stop_loss=stop_loss, trail_act=trail_act,
                                  trail_dd=trail_dd, hold_max=hold_max)
        all_trades.extend(trades)
        del df  # free memory

    all_trades.sort(key=lambda t: t.entry_time)
    return all_trades


def analyze(trades: list[Trade], label: str, show_monthly: bool = True) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0, "win_rate": 0, "avg_net": 0}

    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    avg_net = float(np.mean([t.net_bps for t in trades]))
    avg_gross = float(np.mean([t.gross_bps for t in trades]))
    avg_hold = float(np.mean([t.hold_min for t in trades]))

    days_set = set(t.entry_time[:10] for t in trades)
    tpd = n / max(1, len(days_set))

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades:     {n}  ({tpd:.1f}/day)")
    print(f"  Win rate:   {wins/n*100:.0f}%")
    print(f"  Gross avg:  {avg_gross:+.1f} bps")
    print(f"  Net avg:    {avg_net:+.1f} bps")
    print(f"  Total P&L:  ${total_pnl:+.2f}")
    print(f"  Avg hold:   {avg_hold:.0f} min")

    # By reason
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.reason].append(t)
    print(f"\n  {'Reason':<14} {'Count':>6} {'Win':>6} {'AvgNet':>8} {'Total$':>10}")
    print(f"  {'-'*48}")
    for reason in sorted(by_reason, key=lambda k: -len(by_reason[k])):
        rt = by_reason[reason]
        rw = sum(1 for t in rt if t.pnl_usdt > 0)
        rp = sum(t.pnl_usdt for t in rt)
        rn = float(np.mean([t.net_bps for t in rt]))
        print(f"  {reason:<14} {len(rt):>6} {rw/len(rt)*100:>5.0f}% {rn:>+7.1f} {'$'+f'{rp:+.2f}':>10}")

    # Trail/SL coverage
    trail = [t for t in trades if t.reason == "trail_stop"]
    sl = [t for t in trades if t.reason == "stop_loss"]
    tp = sum(t.pnl_usdt for t in trail)
    sp = sum(t.pnl_usdt for t in sl)
    print(f"\n  Trail/SL: {len(trail)}:{len(sl)} = {len(trail)/max(1,len(sl)):.2f}x | "
          f"Coverage: {tp/max(0.01,abs(sp))*100:.0f}%")

    # By symbol
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    sym_pnl = [(s, sum(t.pnl_usdt for t in ts), len(ts),
                 float(np.mean([t.net_bps for t in ts]))) for s, ts in by_sym.items()]
    sym_pnl.sort(key=lambda x: x[1], reverse=True)
    winners = sum(1 for _, p, _, _ in sym_pnl if p > 0)
    print(f"\n  Symbols: {winners}/{len(sym_pnl)} profitable")
    print(f"  {'Symbol':<12} {'Trades':>7} {'AvgNet':>8} {'Total$':>10}")
    print(f"  {'-'*40}")
    for s, p, c, avg in sym_pnl:
        print(f"  {s:<12} {c:>7} {avg:>+7.1f} {'$'+f'{p:+.2f}':>10}")

    # Drawdown
    cum = 0.0
    peak_dd = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t.pnl_usdt
        if cum > peak_dd:
            peak_dd = cum
        dd = peak_dd - cum
        if dd > max_dd:
            max_dd = dd
    print(f"\n  Max drawdown: ${max_dd:.2f}")

    # Daily
    by_day = defaultdict(float)
    for t in trades:
        by_day[t.entry_time[:10]] += t.pnl_usdt
    win_days = sum(1 for v in by_day.values() if v > 0)
    if by_day:
        print(f"  Days: {len(by_day)} total, {win_days} winning ({win_days/len(by_day)*100:.0f}%)")

    # Monthly
    losing_months = 0
    total_months = 0
    if show_monthly:
        by_month = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            m = t.entry_time[:7]
            by_month[m]["trades"] += 1
            by_month[m]["pnl"] += t.pnl_usdt
            if t.pnl_usdt > 0:
                by_month[m]["wins"] += 1

        print(f"\n  {'Month':<10} {'Trades':>7} {'Win%':>6} {'P&L':>10} {'$/day':>8}")
        print(f"  {'-'*44}")
        for m in sorted(by_month.keys()):
            md = by_month[m]
            wr = md["wins"] / md["trades"] * 100 if md["trades"] > 0 else 0
            # Approximate days in month (from trade data)
            m_days = len(set(t.entry_time[:10] for t in trades if t.entry_time[:7] == m))
            dpd = md["pnl"] / max(1, m_days)
            pnl_s = f"${md['pnl']:+.2f}"
            marker = "✓" if md["pnl"] > 0 else "✗"
            total_months += 1
            if md["pnl"] <= 0:
                losing_months += 1
            print(f"  {m:<10} {md['trades']:>7} {wr:>5.0f}% {pnl_s:>10} ${dpd:>+6.2f} {marker}")
        print(f"\n  Losing months: {losing_months}/{total_months}")

    return {
        "label": label, "trades": n, "wins": wins, "win_rate": wins/n,
        "avg_gross": avg_gross, "avg_net": avg_net,
        "pnl": total_pnl, "max_dd": max_dd, "avg_hold": avg_hold,
        "losing_months": losing_months, "total_months": total_months,
    }


def main():
    print("=" * 60)
    print("  1-YEAR BACKTEST — Extreme Rev + Volume Filter")
    print("=" * 60)

    results = []

    configs = [
        {"label": "B0. Baseline Rev>150 (no filter)",
         "thresh_bps": 150, "vol_zscore_min": 0, "vol_ratio_min": 0},
        {"label": "B1. Rev>150 + Vol z>1.0",
         "thresh_bps": 150, "vol_zscore_min": 1.0, "vol_ratio_min": 0},
        {"label": "B2. Rev>150 + Vol z>1.5",
         "thresh_bps": 150, "vol_zscore_min": 1.5, "vol_ratio_min": 0},
        {"label": "B3. Rev>150 + Vol z>2.0",
         "thresh_bps": 150, "vol_zscore_min": 2.0, "vol_ratio_min": 0},
        {"label": "B4. Rev>150 + Vol z>2.5",
         "thresh_bps": 150, "vol_zscore_min": 2.5, "vol_ratio_min": 0},
        {"label": "B5. Rev>150 + Vol ratio>2x",
         "thresh_bps": 150, "vol_zscore_min": 0, "vol_ratio_min": 2.0},
        {"label": "B6. Rev>150 + Vol ratio>2.5x",
         "thresh_bps": 150, "vol_zscore_min": 0, "vol_ratio_min": 2.5},
        {"label": "B7. Rev>200 + Vol z>1.5",
         "thresh_bps": 200, "vol_zscore_min": 1.5, "vol_ratio_min": 0},
        {"label": "B8. Rev>200 + Vol z>2.0",
         "thresh_bps": 200, "vol_zscore_min": 2.0, "vol_ratio_min": 0},
    ]

    for cfg in configs:
        label = cfg.pop("label")
        print(f"\nRunning: {label}")
        t0 = time.time()
        trades = run_config(days=365, label=label, **cfg)
        elapsed = time.time() - t0
        print(f"  ({elapsed:.0f}s, {len(trades)} trades)")
        r = analyze(trades, label, show_monthly=True)
        results.append(r)

    # ── Test stop loss variations on best config ─────────────────────
    print("\n\n" + "▓" * 60)
    print("  STOP LOSS VARIATIONS on best filter")
    print("▓" * 60)

    sl_configs = [
        {"label": "C1. Vol z>2 + SL -30 / trail 20/12",
         "thresh_bps": 150, "vol_zscore_min": 2.0, "vol_ratio_min": 0,
         "stop_loss": -30, "trail_act": 20, "trail_dd": 12},
        {"label": "C2. Vol z>2 + SL -40 / trail 25/15",
         "thresh_bps": 150, "vol_zscore_min": 2.0, "vol_ratio_min": 0,
         "stop_loss": -40, "trail_act": 25, "trail_dd": 15},
        {"label": "C3. Vol z>2 + SL -70 / trail 35/20",
         "thresh_bps": 150, "vol_zscore_min": 2.0, "vol_ratio_min": 0,
         "stop_loss": -70, "trail_act": 35, "trail_dd": 20},
        {"label": "C4. Vol z>2 + NO SL / trail 25/15",
         "thresh_bps": 150, "vol_zscore_min": 2.0, "vol_ratio_min": 0,
         "stop_loss": -999, "trail_act": 25, "trail_dd": 15},
    ]

    for cfg in sl_configs:
        label = cfg.pop("label")
        print(f"\nRunning: {label}")
        t0 = time.time()
        trades = run_config(days=365, label=label, **cfg)
        elapsed = time.time() - t0
        print(f"  ({elapsed:.0f}s, {len(trades)} trades)")
        r = analyze(trades, label, show_monthly=True)
        results.append(r)

    # ── FINAL SUMMARY ────────────────────────────────────────────────
    print("\n\n" + "█" * 70)
    print("  FINAL 1-YEAR SUMMARY")
    print("█" * 70)
    print(f"\n  {'Config':<42} {'Trades':>6} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'DD$':>8} {'L.Mo':>5}")
    print(f"  {'-'*82}")
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r["pnl"] > 0 else "✗"
        lm = f"{r.get('losing_months', '?')}/{r.get('total_months', '?')}"
        print(f"  {r['label']:<42} {r['trades']:>6} {r['win_rate']*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+8.2f} ${r.get('max_dd',0):>7.2f} {lm:>5} {m}")

    valid = [r for r in results if r["trades"] >= 50]
    if valid:
        best_pnl = max(valid, key=lambda r: r["pnl"])
        best_net = max(valid, key=lambda r: r.get("avg_net", -999))
        print(f"\n  BEST P&L:      {best_pnl['label']} → ${best_pnl['pnl']:+.2f}")
        print(f"  BEST net/trade: {best_net['label']} → {best_net.get('avg_net', 0):+.1f} bps")
        if best_pnl["trades"] > 0:
            days_active = len(set(t for r in results for t in
                                   [t2.entry_time[:10] for t2 in []]))  # placeholder
            monthly = best_pnl["pnl"] / max(1, best_pnl.get("total_months", 12))
            print(f"  Monthly avg:   ${monthly:+.2f}/month")


if __name__ == "__main__":
    main()
