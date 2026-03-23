"""Backtester v2 — Replay LiveBot v5.6 strategy on historical data.

Usage:
    python3 -m analysis.backtest_v2                    # default config, 90 days
    python3 -m analysis.backtest_v2 --days 30          # 30 days
    python3 -m analysis.backtest_v2 --stop-loss 30     # override stop loss
    python3 -m analysis.backtest_v2 --multi-run        # parameter optimization
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# Import constants from live bot (read-only)
from analysis.livebot import (
    TRADE_SYMBOLS_LIST, REFERENCE_SYMBOLS, TRADE_SESSIONS, SESSION_CONFIG,
    LEVERAGE_MAP, MAX_LEVERAGE, OI_LOOKBACK, COST_BPS, SLIPPAGE_BPS,
    HOLD_MINUTES, MIN_HOLD_MINUTES, COOLDOWN_MINUTES,
    TRAIL_ACTIVATE_BPS, TRAIL_DRAWDOWN_BPS,
    VOL_WINDOW, VOL_MAX_BPS, MAX_SPREAD_BPS,
    TREND_LOOKBACK, TREND_THRESHOLD_BPS, CORRELATION_MAX,
    CAPITAL_USDT, BASE_RISK_PCT, MAX_RISK_PCT, MAX_RISK_TOTAL_PCT,
    STREAK_DISABLE, STREAK_COOLDOWN_H, FUNDING_GRAB_MINUTES,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_data")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "output", "backtest_results")

ALL_SYMBOLS = [s.upper() for s in REFERENCE_SYMBOLS] + TRADE_SYMBOLS_LIST
TRADE_SET = set(TRADE_SYMBOLS_LIST)


# ── Data Loading ──────────────────────────────────────────────────────

def load_symbol_data(symbol: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame | None:
    """Load and merge all data for one symbol into a 1-minute DataFrame."""
    prefix = os.path.join(DATA_DIR, symbol)

    # Klines (required)
    kline_path = f"{prefix}_klines_1m.csv"
    if not os.path.exists(kline_path):
        return None
    klines = pd.read_csv(kline_path)
    klines["timestamp"] = pd.to_datetime(klines["timestamp"], unit="ms", utc=True)
    klines = klines.set_index("timestamp").sort_index()
    klines = klines[start_ts:end_ts]
    if klines.empty:
        return None

    df = klines[["close", "high", "low", "volume"]].copy()

    # OI (optional, forward-fill)
    oi_path = f"{prefix}_oi_5m.csv"
    if os.path.exists(oi_path):
        oi = pd.read_csv(oi_path)
        oi["timestamp"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
        oi = oi.set_index("timestamp").sort_index()
        oi = oi[~oi.index.duplicated(keep="last")]
        df = df.join(oi[["oi"]], how="left")
        df["oi"] = df["oi"].ffill()
    else:
        df["oi"] = 0.0

    # Funding (optional, forward-fill)
    fund_path = f"{prefix}_funding.csv"
    if os.path.exists(fund_path):
        fund = pd.read_csv(fund_path)
        fund["timestamp"] = pd.to_datetime(fund["timestamp"], unit="ms", utc=True)
        fund = fund.set_index("timestamp").sort_index()
        fund = fund[~fund.index.duplicated(keep="last")]
        df = df.join(fund[["funding_rate"]], how="left")
        df["funding_rate"] = df["funding_rate"].ffill().fillna(0)
    else:
        df["funding_rate"] = 0.0

    # L/S ratios (optional, forward-fill)
    for key, col in [("ls_global", "crowd_long"), ("ls_top", "top_long")]:
        ls_path = f"{prefix}_{key}_5m.csv"
        if os.path.exists(ls_path):
            ls = pd.read_csv(ls_path)
            ls["timestamp"] = pd.to_datetime(ls["timestamp"], unit="ms", utc=True)
            ls = ls.set_index("timestamp").sort_index()
            ls = ls[~ls.index.duplicated(keep="last")]
            ls = ls.rename(columns={"long_account": col})
            df = df.join(ls[[col]], how="left")
            df[col] = df[col].ffill().fillna(0.5)
        else:
            df[col] = 0.5

    df["symbol"] = symbol
    return df


def load_all_data(days: int) -> dict[str, pd.DataFrame]:
    """Load data for all symbols."""
    end_ts = pd.Timestamp.now(tz="UTC")
    start_ts = end_ts - pd.Timedelta(days=days)

    data = {}
    for sym in ALL_SYMBOLS:
        df = load_symbol_data(sym, start_ts, end_ts)
        if df is not None and len(df) > 100:
            data[sym] = df
    return data


# ── Backtest Engine ───────────────────────────────────────────────────

@dataclass
class BtPosition:
    symbol: str
    direction: int
    entry_price: float
    entry_idx: int
    entry_time: pd.Timestamp
    leverage: float
    size_usdt: float
    margin_usdt: float
    peak_bps: float = 0.0
    funding_paid: float = 0.0
    last_funding_h: int = -1  # last settlement hour processed


@dataclass
class BtTrade:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    hold_min: float
    leverage: float
    size_usdt: float
    gross_bps: float
    net_bps: float
    leveraged_net_bps: float
    pnl_usdt: float
    reason: str
    session: str


@dataclass
class BtConfig:
    """Overridable backtest parameters."""
    stop_loss_bps: float = -40.0
    trail_activate: float = TRAIL_ACTIVATE_BPS
    trail_drawdown: float = TRAIL_DRAWDOWN_BPS
    oi_lookback: int = OI_LOOKBACK
    trend_threshold: float = TREND_THRESHOLD_BPS


def get_session(ts: pd.Timestamp) -> str | None:
    h = ts.hour
    for name, (start, end) in TRADE_SESSIONS.items():
        if start <= h < end:
            return name
    return None


def is_funding_settlement(ts: pd.Timestamp) -> bool:
    """Check if this minute is a funding settlement (00h, 08h, 16h UTC)."""
    return ts.hour in (0, 8, 16) and ts.minute == 0


def run_backtest(data: dict[str, pd.DataFrame], config: BtConfig | None = None) -> dict:
    """Run a single backtest with given config. Returns results dict."""
    cfg = config or BtConfig()

    # Get aligned timestamps from BTC (most liquid, always present)
    btc_key = "BTCUSDT"
    if btc_key not in data:
        return {"error": "No BTC data"}

    timestamps = data[btc_key].index

    # State
    positions: dict[str, BtPosition] = {}
    trades: list[BtTrade] = []
    cooldowns: dict[str, pd.Timestamp] = {}
    streak_losses: dict[str, int] = {}
    streak_disabled: dict[str, pd.Timestamp] = {}
    total_pnl_usdt = 0.0
    total_gross = 0.0
    total_leveraged = 0.0
    wins = 0

    # Rolling buffers per symbol
    mids: dict[str, list] = defaultdict(list)
    oi_hist: dict[str, list] = defaultdict(list)
    price_hist: dict[str, list] = defaultdict(list)
    smart_div_hist: dict[str, list] = defaultdict(list)

    # P&L curve
    pnl_curve = []

    for i, ts in enumerate(timestamps):
        if i % 10000 == 0 and i > 0:
            pass  # silent progress

        session = get_session(ts)

        # ── Update buffers ──
        btc_close = data[btc_key]["close"].iloc[data[btc_key].index.get_indexer([ts], method="ffill")[0]] if ts in data[btc_key].index or True else 0
        # Safe BTC close lookup
        btc_idx = data[btc_key].index.get_indexer([ts], method="ffill")[0]
        btc_close_val = float(data[btc_key]["close"].iloc[btc_idx]) if btc_idx >= 0 else 0
        btc_mids = mids.get(btc_key, [])

        btc_ret = 0.0
        if len(btc_mids) >= 2:
            btc_ret = (btc_mids[-1] / btc_mids[-2] - 1) * 1e4

        signals: dict[str, dict] = {}

        for sym in ALL_SYMBOLS:
            if sym not in data:
                continue
            df = data[sym]
            idx = df.index.get_indexer([ts], method="ffill")[0]
            if idx < 0:
                continue
            row = df.iloc[idx]
            mid = float(row["close"])
            if mid == 0:
                continue

            mids[sym].append(mid)
            if len(mids[sym]) > 720:
                mids[sym] = mids[sym][-720:]

            oi_val = float(row.get("oi", 0))
            if oi_val > 0:
                oi_hist[sym].append(oi_val)
                if len(oi_hist[sym]) > 120:
                    oi_hist[sym] = oi_hist[sym][-120:]

            price_hist[sym].append(mid)
            if len(price_hist[sym]) > 120:
                price_hist[sym] = price_hist[sym][-120:]

            if len(mids[sym]) < 6:
                continue

            # ── Signal 1: OI Divergence ──
            oi_signal = 0.0
            lb = cfg.oi_lookback
            if len(oi_hist[sym]) >= lb and len(price_hist[sym]) >= lb:
                oi_now = oi_hist[sym][-1]
                oi_prev = oi_hist[sym][-lb]
                oi_change = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0

                price_now = price_hist[sym][-1]
                price_prev = price_hist[sym][-lb]
                price_change = (price_now / price_prev - 1) * 1e4

                strength = float(np.clip(
                    (min(abs(price_change), 20) / 20 + min(abs(oi_change), 0.3) / 0.3) / 2,
                    0.3, 1.0
                ))

                if price_change > 3 and oi_change < -0.03:
                    oi_signal = -strength
                elif price_change < -3 and oi_change > 0.03:
                    oi_signal = +strength

            # ── Signal 2: Funding proximity ──
            funding_signal = 0.0
            rate = float(row.get("funding_rate", 0))
            # Next settlement: 00h, 08h, 16h
            current_h = ts.hour
            next_settle_h = [0, 8, 16, 24]
            mins_to = min((h * 60 - current_h * 60 - ts.minute) % (24 * 60)
                          for h in next_settle_h if (h * 60 - current_h * 60 - ts.minute) % (24 * 60) > 0)
            if 0 < mins_to < 120:
                if rate > 0.0003:
                    funding_signal = -1.0 * min(1.0, (120 - mins_to) / 60)
                elif rate < -0.0003:
                    funding_signal = +1.0 * min(1.0, (120 - mins_to) / 60)

            # ── Signal 3: BTC lead-lag ──
            leadlag_signal = 0.0
            if sym != btc_key and abs(btc_ret) > 2:
                leadlag_signal = float(np.clip(btc_ret / 10, -1, 1))

            # ── Signal 4: Smart money ──
            smart_signal = 0.0
            if sym in TRADE_SET:
                crowd = float(row.get("crowd_long", 0.5))
                top = float(row.get("top_long", 0.5))
                div = top - crowd
                smart_div_hist[sym].append(div)
                if len(smart_div_hist[sym]) > 60:
                    smart_div_hist[sym] = smart_div_hist[sym][-60:]
                if len(smart_div_hist[sym]) >= 30 and crowd != 0.5:
                    arr = np.array(smart_div_hist[sym])
                    std = float(np.std(arr))
                    if std > 0:
                        z = float((arr[-1] - np.mean(arr)) / std)
                        smart_signal = float(np.clip(z / 2, -1, 1))

            # ── Composite ──
            composite = (
                oi_signal * 0.35 +
                funding_signal * 0.20 +
                leadlag_signal * 0.15 +
                smart_signal * 0.30
            )
            active_signals = sum([
                abs(oi_signal) > 0.5,
                abs(funding_signal) > 0.3,
                abs(leadlag_signal) > 0.3,
                abs(smart_signal) > 0.3,
            ])
            leverage = LEVERAGE_MAP.get(min(active_signals, 4), 1.0)
            leverage = min(leverage, MAX_LEVERAGE)

            # Spread estimate from kline (high-low as proxy)
            spread_bps = (float(row["high"]) - float(row["low"])) / mid * 1e4 * 0.1 if mid > 0 else 0

            signals[sym] = {
                "composite": composite, "oi_signal": oi_signal,
                "funding_signal": funding_signal, "leadlag_signal": leadlag_signal,
                "smart_signal": smart_signal, "active_signals": active_signals,
                "leverage": leverage, "mid": mid, "spread_bps": spread_bps,
            }

        # ── Trading Logic ──

        # Step 1: Check exits
        for sym in list(positions.keys()):
            sig = signals.get(sym)
            if not sig:
                continue
            mid = sig["mid"]
            pos = positions[sym]
            held = (i - pos.entry_idx)  # minutes
            comp = sig["composite"]

            unrealized = pos.direction * (mid / pos.entry_price - 1) * 1e4
            leveraged_unreal = unrealized * pos.leverage

            if unrealized > pos.peak_bps:
                pos.peak_bps = unrealized

            # Funding simulation
            if is_funding_settlement(ts) and ts.hour != pos.last_funding_h:
                sym_data = data.get(sym)
                if sym_data is not None:
                    fund_rate = float(sym_data.iloc[sym_data.index.get_indexer([ts], method="ffill")[0]].get("funding_rate", 0))
                    cost = pos.size_usdt * fund_rate * pos.direction
                    pos.funding_paid += cost
                    pos.last_funding_h = ts.hour

            exit_reason = None
            if held >= HOLD_MINUTES:
                exit_reason = "timeout"
            elif held >= MIN_HOLD_MINUTES and (
                (pos.direction == 1 and comp < -0.3) or (pos.direction == -1 and comp > 0.3)
            ):
                exit_reason = "reversal"
            elif leveraged_unreal < cfg.stop_loss_bps:
                exit_reason = "stop_loss"
            elif held >= MIN_HOLD_MINUTES and (
                pos.peak_bps >= cfg.trail_activate and
                unrealized < pos.peak_bps - cfg.trail_drawdown
            ):
                exit_reason = "trail_stop"

            if exit_reason:
                # Close position
                gross_bps = pos.direction * (mid / pos.entry_price - 1) * 1e4
                hold_min = held
                pnl_usdt = pos.size_usdt * (pos.direction * (mid / pos.entry_price - 1))
                fee_usdt = pos.size_usdt * COST_BPS / 1e4
                slip_usdt = pos.size_usdt * SLIPPAGE_BPS / 1e4
                net_pnl = pnl_usdt - fee_usdt - slip_usdt - pos.funding_paid

                total_cost = COST_BPS + SLIPPAGE_BPS
                lev_gross = gross_bps * pos.leverage
                lev_net = lev_gross - total_cost

                total_pnl_usdt += net_pnl
                total_gross += gross_bps
                total_leveraged += lev_net
                if net_pnl > 0:
                    wins += 1

                trade = BtTrade(
                    symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
                    entry_time=str(pos.entry_time), exit_time=str(ts),
                    entry_price=pos.entry_price, exit_price=mid,
                    hold_min=hold_min, leverage=pos.leverage,
                    size_usdt=round(pos.size_usdt, 2),
                    gross_bps=round(gross_bps, 2),
                    net_bps=round(gross_bps - total_cost, 2),
                    leveraged_net_bps=round(lev_net, 2),
                    pnl_usdt=round(net_pnl, 2),
                    reason=exit_reason, session=session or "?",
                )
                trades.append(trade)
                del positions[sym]
                cooldowns[sym] = ts + timedelta(minutes=COOLDOWN_MINUTES)

                # Streak tracking
                if net_pnl < 0:
                    streak_losses[sym] = streak_losses.get(sym, 0) + 1
                    if streak_losses[sym] >= STREAK_DISABLE:
                        streak_disabled[sym] = ts + timedelta(hours=STREAK_COOLDOWN_H)
                else:
                    streak_losses[sym] = 0

                pnl_curve.append({"time": str(ts), "cum_pnl": round(total_pnl_usdt, 2)})

        # Step 2: Entries
        if session is None:
            continue

        sess_cfg = SESSION_CONFIG.get(session, {"min_score": 0.35, "lev_mult": 0.8})
        min_score = sess_cfg["min_score"]
        lev_mult = sess_cfg["lev_mult"]

        # Funding grab
        if mins_to <= FUNDING_GRAB_MINUTES:
            min_score *= 0.8

        # Cross-symbol correlation
        oi_long = sum(1 for s in signals.values() if s.get("oi_signal", 0) > 0.3)
        oi_short = sum(1 for s in signals.values() if s.get("oi_signal", 0) < -0.3)
        macro_move = max(oi_long, oi_short) > CORRELATION_MAX

        candidates = []
        for sym in TRADE_SYMBOLS_LIST:
            if sym in positions:
                continue
            if sym in cooldowns and ts < cooldowns[sym]:
                continue
            if sym in streak_disabled and ts < streak_disabled[sym]:
                continue
            sig = signals.get(sym)
            if not sig:
                continue
            comp = sig["composite"]
            if abs(comp) < min_score:
                continue
            if sig["active_signals"] < 1:
                continue
            if abs(sig["oi_signal"]) < 0.1:
                continue
            if sig["spread_bps"] > MAX_SPREAD_BPS:
                continue

            effective_score = abs(comp)
            if macro_move:
                effective_score *= 0.6
                if effective_score < min_score:
                    continue

            direction = 1 if comp > 0 else -1

            # Vol filter
            sym_mids = mids.get(sym, [])
            if len(sym_mids) >= VOL_WINDOW:
                recent = sym_mids[-VOL_WINDOW:]
                returns = [(recent[j] / recent[j-1] - 1) * 1e4 for j in range(1, len(recent))]
                vol = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0
                if vol > VOL_MAX_BPS:
                    continue

            # Trend filter
            if len(sym_mids) >= TREND_LOOKBACK:
                trend = (sym_mids[-1] / sym_mids[-TREND_LOOKBACK] - 1) * 1e4
                if direction == 1 and trend < -cfg.trend_threshold:
                    continue
                if direction == -1 and trend > cfg.trend_threshold:
                    continue

            candidates.append((sym, sig, effective_score))

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[2], reverse=True)
        slots = 4 - len(positions)
        if slots <= 0:
            continue

        current_capital = CAPITAL_USDT + total_pnl_usdt
        max_exposure = current_capital * MAX_RISK_TOTAL_PCT / 100
        margin_used = sum(p.margin_usdt for p in positions.values())
        remaining = max(0, max_exposure - margin_used)

        for rank, (sym, sig, score) in enumerate(candidates[:slots], 1):
            mid = sig["mid"]
            if mid == 0:
                continue

            comp = sig["composite"]
            direction = 1 if comp > 0 else -1
            leverage = min(sig["leverage"] * lev_mult, MAX_LEVERAGE)

            score_factor = min(score / 0.6, 1.0)
            risk_pct = BASE_RISK_PCT + (MAX_RISK_PCT - BASE_RISK_PCT) * score_factor
            margin = min(current_capital * risk_pct / 100, remaining)

            if margin < current_capital * BASE_RISK_PCT / 100 * 0.5:
                break

            size = margin * leverage
            remaining -= margin

            positions[sym] = BtPosition(
                symbol=sym, direction=direction,
                entry_price=mid, entry_idx=i, entry_time=ts,
                leverage=leverage, size_usdt=size, margin_usdt=margin,
            )

    # Close remaining positions at end
    for sym in list(positions.keys()):
        sig = signals.get(sym)
        if sig and sig["mid"] > 0:
            pos = positions[sym]
            mid = sig["mid"]
            gross_bps = pos.direction * (mid / pos.entry_price - 1) * 1e4
            hold_min = len(timestamps) - pos.entry_idx
            pnl_usdt = pos.size_usdt * (pos.direction * (mid / pos.entry_price - 1))
            fee_usdt = pos.size_usdt * COST_BPS / 1e4
            slip_usdt = pos.size_usdt * SLIPPAGE_BPS / 1e4
            net_pnl = pnl_usdt - fee_usdt - slip_usdt - pos.funding_paid
            total_cost = COST_BPS + SLIPPAGE_BPS
            lev_net = gross_bps * pos.leverage - total_cost
            total_pnl_usdt += net_pnl
            total_gross += gross_bps
            if net_pnl > 0:
                wins += 1
            trades.append(BtTrade(
                symbol=sym, direction="LONG" if pos.direction == 1 else "SHORT",
                entry_time=str(pos.entry_time), exit_time=str(timestamps[-1]),
                entry_price=pos.entry_price, exit_price=mid,
                hold_min=hold_min, leverage=pos.leverage,
                size_usdt=round(pos.size_usdt, 2), gross_bps=round(gross_bps, 2),
                net_bps=round(gross_bps - total_cost, 2),
                leveraged_net_bps=round(lev_net, 2), pnl_usdt=round(net_pnl, 2),
                reason="end_of_data", session=get_session(timestamps[-1]) or "?",
            ))

    n = len(trades)
    return {
        "trades": trades,
        "pnl_curve": pnl_curve,
        "total_trades": n,
        "wins": wins,
        "win_rate": wins / n if n > 0 else 0,
        "total_pnl_usdt": round(total_pnl_usdt, 2),
        "total_gross_bps": round(total_gross, 2),
        "total_leveraged_bps": round(total_leveraged, 2),
        "avg_hold_min": round(sum(t.hold_min for t in trades) / n, 1) if n else 0,
        "max_drawdown": _max_drawdown(trades),
        "config": cfg,
    }


def _max_drawdown(trades: list[BtTrade]) -> float:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t.pnl_usdt
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


# ── Multi-Run ─────────────────────────────────────────────────────────

PARAM_GRID = {
    "stop_loss":      {"field": "stop_loss_bps",  "values": [-30, -40, -50, -60]},
    "trail_activate": {"field": "trail_activate",  "values": [15, 20, 25, 30]},
    "trail_drawdown": {"field": "trail_drawdown",  "values": [10, 15, 20]},
    "oi_lookback":    {"field": "oi_lookback",     "values": [12, 18, 24, 30]},
    "trend_threshold":{"field": "trend_threshold", "values": [30, 50, 70]},
}


def run_multi(data: dict[str, pd.DataFrame]) -> list[dict]:
    """Run baseline + one-at-a-time parameter variations."""
    results = []

    # Baseline
    print("Running baseline...")
    baseline = run_backtest(data)
    baseline["label"] = "[baseline]"
    baseline["param"] = "-"
    baseline["value"] = "-"
    results.append(baseline)

    # Parameter variations
    for param_name, spec in PARAM_GRID.items():
        for val in spec["values"]:
            cfg = BtConfig()
            setattr(cfg, spec["field"], val)
            label = f"{param_name}={val}"
            print(f"Running {label}...")
            r = run_backtest(data, cfg)
            r["label"] = label
            r["param"] = param_name
            r["value"] = val
            results.append(r)

    return results


# ── Output ────────────────────────────────────────────────────────────

def print_results(r: dict, label: str = ""):
    """Print single run results."""
    n = r["total_trades"]
    print(f"\n{'═' * 60}")
    print(f"  {label or 'Backtest Results'}")
    print(f"{'═' * 60}")
    print(f"  Trades:       {n}")
    print(f"  Win rate:     {r['win_rate']*100:.0f}%")
    print(f"  P&L:          ${r['total_pnl_usdt']:+.2f}")
    print(f"  Gross:        {r['total_gross_bps']:+.1f} bps")
    print(f"  Avg hold:     {r['avg_hold_min']:.0f} min")
    print(f"  Max drawdown: ${r['max_drawdown']:.2f}")

    # By session
    by_session = defaultdict(list)
    for t in r["trades"]:
        by_session[t.session].append(t)
    print(f"\n  {'Session':<12} {'Trades':>7} {'Win':>6} {'P&L':>10}")
    print(f"  {'-'*38}")
    for sess in ["asian", "us", "overnight"]:
        st = by_session.get(sess, [])
        if not st:
            continue
        sw = sum(1 for t in st if t.pnl_usdt > 0)
        sp = sum(t.pnl_usdt for t in st)
        print(f"  {sess:<12} {len(st):>7} {sw/len(st)*100:>5.0f}% {'$'+f'{sp:+.2f}':>10}")

    # By reason
    by_reason = defaultdict(list)
    for t in r["trades"]:
        by_reason[t.reason].append(t)
    print(f"\n  {'Reason':<14} {'Count':>6} {'Win':>6} {'Total':>10}")
    print(f"  {'-'*40}")
    for reason in sorted(by_reason, key=lambda k: -len(by_reason[k])):
        rt = by_reason[reason]
        rw = sum(1 for t in rt if t.pnl_usdt > 0)
        rp = sum(t.pnl_usdt for t in rt)
        print(f"  {reason:<14} {len(rt):>6} {rw/len(rt)*100:>5.0f}% {'$'+f'{rp:+.2f}':>10}")

    # By symbol (top 5 / bottom 5)
    by_sym = defaultdict(list)
    for t in r["trades"]:
        by_sym[t.symbol].append(t)
    sym_pnl = [(s, sum(t.pnl_usdt for t in ts), len(ts)) for s, ts in by_sym.items()]
    sym_pnl.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  {'Symbol':<12} {'Trades':>7} {'P&L':>10}")
    print(f"  {'-'*32}")
    for s, p, c in sym_pnl:
        print(f"  {s:<12} {c:>7} {'$'+f'{p:+.2f}':>10}")


def print_multi_results(results: list[dict]):
    """Print comparison table."""
    print(f"\n{'═' * 75}")
    print(f"  Multi-Run Comparison")
    print(f"{'═' * 75}")
    print(f"  {'Label':<22} {'Trades':>7} {'Win%':>6} {'Net P&L':>10} {'MaxDD':>8} {'Hold':>6}")
    print(f"  {'-'*62}")
    for r in results:
        label = r.get("label", "?")
        n = r["total_trades"]
        pnl = r["total_pnl_usdt"]
        dd = r["max_drawdown"]
        wr = r["win_rate"] * 100
        hold = r["avg_hold_min"]
        print(f"  {label:<22} {n:>7} {wr:>5.0f}% {'$'+f'{pnl:+.2f}':>10} {'$'+f'{dd:.2f}':>8} {hold:>5.0f}m")

    # Best run
    best = max(results, key=lambda r: r["total_pnl_usdt"])
    print(f"\n  Best: {best.get('label', '?')} → ${best['total_pnl_usdt']:+.2f}")


def save_trades_csv(trades: list[BtTrade], filepath: str):
    """Save trades to CSV."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "direction", "entry_time", "exit_time",
                     "entry_price", "exit_price", "hold_min", "leverage",
                     "size_usdt", "gross_bps", "net_bps", "leveraged_net_bps",
                     "pnl_usdt", "reason", "session"])
        for t in trades:
            w.writerow([t.symbol, t.direction, t.entry_time, t.exit_time,
                        t.entry_price, t.exit_price, t.hold_min, t.leverage,
                        t.size_usdt, t.gross_bps, t.net_bps, t.leveraged_net_bps,
                        t.pnl_usdt, t.reason, t.session])


def save_runs_csv(results: list[dict], filepath: str):
    """Save multi-run comparison to CSV."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "param", "value", "trades", "wins", "win_rate",
                     "total_pnl_usdt", "max_drawdown", "avg_hold_min"])
        for r in results:
            w.writerow([r.get("label", ""), r.get("param", ""), r.get("value", ""),
                        r["total_trades"], r["wins"], round(r["win_rate"], 3),
                        r["total_pnl_usdt"], r["max_drawdown"], r["avg_hold_min"]])


def plot_equity(results: list[dict] | dict, filepath: str):
    """Plot equity curve."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(14, 6))

    def _plot_curve(result, label, color, lw=1.5):
        curve = result.get("pnl_curve", [])
        if not curve:
            return
        times = [pd.Timestamp(p["time"]) for p in curve]
        pnl = [p["cum_pnl"] for p in curve]
        ax.plot(times, pnl, label=label, color=color, linewidth=lw)

    if isinstance(results, dict):
        _plot_curve(results, "Default", "#3fb950", 2)
    else:
        # Baseline + best
        baseline = results[0]
        best = max(results, key=lambda r: r["total_pnl_usdt"])
        _plot_curve(baseline, f"Baseline (${baseline['total_pnl_usdt']:+.2f})", "#3fb950", 2)
        if best != baseline:
            _plot_curve(best, f"Best: {best.get('label', '?')} (${best['total_pnl_usdt']:+.2f})", "#58a6ff", 2)

    ax.axhline(y=0, color="#7d8590", linewidth=0.5, linestyle="--")
    ax.set_title("LiveBot Backtest — Equity Curve", fontsize=14, color="#e6edf3")
    ax.set_xlabel("Time", color="#7d8590")
    ax.set_ylabel("Cumulative P&L ($)", color="#7d8590")
    ax.legend(loc="upper left")
    ax.set_facecolor("#0d1117")
    fig.set_facecolor("#0d1117")
    ax.tick_params(colors="#7d8590")
    ax.spines["bottom"].set_color("#30363d")
    ax.spines["left"].set_color("#30363d")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

    plt.tight_layout()
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  Saved: {filepath}")


def plot_by_session(trades: list[BtTrade], filepath: str):
    """Bar chart P&L by session."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_session = defaultdict(float)
    for t in trades:
        by_session[t.session] += t.pnl_usdt

    sessions = ["asian", "us", "overnight"]
    values = [by_session.get(s, 0) for s in sessions]
    colors = ["#3fb950" if v >= 0 else "#f85149" for v in values]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(sessions, values, color=colors)
    ax.set_title("P&L by Session", fontsize=14, color="#e6edf3")
    ax.set_ylabel("P&L ($)", color="#7d8590")
    ax.axhline(y=0, color="#7d8590", linewidth=0.5)
    ax.set_facecolor("#0d1117")
    fig.set_facecolor("#0d1117")
    ax.tick_params(colors="#7d8590")
    for spine in ax.spines.values():
        spine.set_color("#30363d")

    plt.tight_layout()
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  Saved: {filepath}")


def plot_by_symbol(trades: list[BtTrade], filepath: str):
    """Bar chart P&L by symbol."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_sym = defaultdict(float)
    for t in trades:
        by_sym[t.symbol] += t.pnl_usdt

    syms = sorted(by_sym.keys(), key=lambda s: by_sym[s], reverse=True)
    values = [by_sym[s] for s in syms]
    labels = [s.replace("USDT", "") for s in syms]
    colors = ["#3fb950" if v >= 0 else "#f85149" for v in values]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(labels, values, color=colors)
    ax.set_title("P&L by Symbol", fontsize=14, color="#e6edf3")
    ax.set_ylabel("P&L ($)", color="#7d8590")
    ax.axhline(y=0, color="#7d8590", linewidth=0.5)
    ax.set_facecolor("#0d1117")
    fig.set_facecolor("#0d1117")
    ax.tick_params(colors="#7d8590", rotation=45)
    for spine in ax.spines.values():
        spine.set_color("#30363d")

    plt.tight_layout()
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  Saved: {filepath}")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest LiveBot strategy on historical data")
    parser.add_argument("--days", type=int, default=90, help="Days of data to backtest (default: 90)")
    parser.add_argument("--multi-run", action="store_true", help="Run parameter optimization")
    parser.add_argument("--stop-loss", type=float, default=None, help="Override stop loss (bps, negative)")
    parser.add_argument("--trail-activate", type=float, default=None, help="Override trail activate (bps)")
    parser.add_argument("--trail-drawdown", type=float, default=None, help="Override trail drawdown (bps)")
    parser.add_argument("--oi-lookback", type=int, default=None, help="Override OI lookback (ticks)")
    parser.add_argument("--trend-threshold", type=float, default=None, help="Override trend threshold (bps)")
    args = parser.parse_args()

    print(f"Loading {args.days} days of data...")
    t0 = time.time()
    data = load_all_data(args.days)
    print(f"Loaded {len(data)} symbols in {time.time()-t0:.1f}s")

    if len(data) < 3:
        print("Not enough data. Run: python3 -m analysis.download_data --days", args.days)
        return

    if args.multi_run:
        print(f"\nMulti-run optimization ({sum(len(s['values']) for s in PARAM_GRID.values())+1} runs)...")
        t0 = time.time()
        results = run_multi(data)
        elapsed = time.time() - t0
        print(f"Completed in {elapsed:.0f}s")

        print_multi_results(results)
        print_results(results[0], "Baseline Detail")

        # Save outputs
        save_trades_csv(results[0]["trades"], os.path.join(RESULTS_DIR, "trades_default.csv"))
        save_runs_csv(results, os.path.join(RESULTS_DIR, "runs_comparison.csv"))
        plot_equity(results, os.path.join(RESULTS_DIR, "equity_curve.png"))
        plot_by_session(results[0]["trades"], os.path.join(RESULTS_DIR, "pnl_by_session.png"))
        plot_by_symbol(results[0]["trades"], os.path.join(RESULTS_DIR, "pnl_by_symbol.png"))
    else:
        # Single run
        cfg = BtConfig()
        if args.stop_loss is not None:
            cfg.stop_loss_bps = -abs(args.stop_loss)
        if args.trail_activate is not None:
            cfg.trail_activate = args.trail_activate
        if args.trail_drawdown is not None:
            cfg.trail_drawdown = args.trail_drawdown
        if args.oi_lookback is not None:
            cfg.oi_lookback = args.oi_lookback
        if args.trend_threshold is not None:
            cfg.trend_threshold = args.trend_threshold

        print("Running backtest...")
        t0 = time.time()
        r = run_backtest(data, cfg)
        elapsed = time.time() - t0
        print(f"Completed in {elapsed:.0f}s")

        print_results(r, f"Backtest {args.days}d")

        save_trades_csv(r["trades"], os.path.join(RESULTS_DIR, "trades_default.csv"))
        plot_equity(r, os.path.join(RESULTS_DIR, "equity_curve.png"))
        plot_by_session(r["trades"], os.path.join(RESULTS_DIR, "pnl_by_session.png"))
        plot_by_symbol(r["trades"], os.path.join(RESULTS_DIR, "pnl_by_symbol.png"))


if __name__ == "__main__":
    main()
