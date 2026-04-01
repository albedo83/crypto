"""Fast Signal Backtest — High-frequency signals on 1h candles.

Tests on 90 days of 1h data (Jan-Apr 2026):
  1. S9-fast: Fade ±X% in Y hours (1h-6h lookback)
  2. Micro-squeeze: 3-4h compression → false breakout → fade
  3. Volume spike reversal: vol > Nx average → fade

These run independently from the 4h signals.
Uses 50/50 train/test split (45 days each).

Usage:
    python3 -m analysis.backtest_1h_fast
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
COST_BPS = 12  # taker
LEVERAGE = 2.0

TOKENS = ['ARB','OP','AVAX','SUI','APT','SEI','NEAR','AAVE','COMP','SNX','PENDLE','DYDX',
          'DOGE','WLD','BLUR','LINK','PYTH','SOL','INJ','CRV','LDO','STX','GMX','IMX','SAND',
          'GALA','MINA']


def load_1h_candles():
    data = {}
    for coin in TOKENS + ['BTC', 'ETH']:
        path = os.path.join(DATA_DIR, f"{coin}_1h.json")
        if not os.path.exists(path):
            path = os.path.join(DATA_DIR, f"{coin}_1h_90d.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        if not raw:
            continue
        candles = [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]),
                    "l": float(c["l"]), "c": float(c["c"]),
                    "v": float(c.get("v", 0))} for c in raw]
        data[coin] = candles
    return data


def monte_carlo(trades, n_sims=1000):
    if len(trades) < 10:
        return 0.0
    actual_mean = np.mean([t["net"] for t in trades])
    nets = [t["net"] for t in trades]
    rand_means = [np.mean(random.sample(nets, len(nets))) for _ in range(n_sims)]
    std = np.std(rand_means)
    return (actual_mean - np.mean(rand_means)) / std if std > 0 else 0


def backtest_fast(data, config):
    """Generic fast signal backtest on 1h candles."""
    hold = config.get("hold", 6)  # candles (hours)
    stop_bps = config.get("stop_bps", -2500)
    max_pos = config.get("max_pos", 4)
    size = config.get("size", 150)
    signal_fn = config["signal_fn"]

    effective_cost = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE

    coins = [c for c in TOKENS if c in data and len(data[c]) > 100]

    # Build time-aligned index
    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    # Split: first 45 days = train, last 45 days = test
    sorted_ts = sorted(all_ts)
    mid = sorted_ts[len(sorted_ts) // 2]

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted_ts:
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if ts not in coin_by_ts.get(coin, {}):
                continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0:
                continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0:
                continue

            exit_reason = None
            exit_price = current
            d = pos["dir"]
            effective_stop = stop_bps / LEVERAGE

            if d == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + effective_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - effective_stop / 1e4)

            if held >= hold:
                exit_reason = "timeout"

            if exit_reason:
                gross = d * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - effective_cost
                trades.append({"net": round(net, 1), "pnl": round(size * net / 1e4, 2),
                               "coin": coin, "dir": d, "entry_t": pos["entry_t"], "exit_t": ts,
                               "reason": exit_reason})
                del positions[coin]
                cooldown[coin] = ts + 4 * 3600 * 1000  # 4h cooldown (shorter for fast signals)

        # Entries
        if len(positions) >= max_pos:
            continue

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            if ts not in coin_by_ts.get(coin, {}):
                continue
            ci = coin_by_ts[coin][ts]
            if ci < 24:  # need some history
                continue

            candles = data[coin]
            result = signal_fn(candles, ci)
            if result:
                candidates.append({"coin": coin, "dir": result["dir"],
                                   "strength": result["strength"], "ci": ci})

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        for cand in candidates[:max_pos - len(positions)]:
            coin = cand["coin"]
            ci = cand["ci"]
            if ci + 1 >= len(data[coin]):
                continue
            entry = data[coin][ci + 1]["o"]
            if entry <= 0:
                continue
            positions[coin] = {"dir": cand["dir"], "entry": entry,
                               "idx": ci + 1, "entry_t": data[coin][ci + 1]["t"]}

    if not trades:
        return None

    n = len(trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < mid)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= mid)
    n_long = sum(1 for t in trades if t["dir"] == 1)
    n_short = sum(1 for t in trades if t["dir"] == -1)

    return {
        "n": n, "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "pnl": round(total_pnl, 0),
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "n_long": n_long, "n_short": n_short,
        "trades": trades,
    }


def pr(label, r):
    if not r:
        print(f"  {label:<50} (no trades)")
        return
    z = monte_carlo(r["trades"])
    v = "✓" if r.get("train", 0) > 0 and r.get("test", 0) > 0 else ""
    print(f"  {label:<50} {r['n']:>5} {r['avg']:>+7.1f} {r['win']:>4}% ${r['pnl']:>+7} "
          f"${r['train']:>+6} ${r['test']:>+6} {r['n_long']:>4}L {r['n_short']:>4}S z={z:>+5.2f} {v}")


def main():
    print("=" * 120)
    print("  FAST SIGNALS — 1h candles, 90 days (Jan-Apr 2026)")
    print("=" * 120)

    data = load_1h_candles()
    print(f"Loaded: {len(data)} tokens, ~{len(data.get('BTC',[])):,} candles each\n")

    hdr = f"  {'Signal':<50} {'N':>5} {'Avg':>7} {'W%':>5} {'P&L':>8} {'Trn':>7} {'Tst':>7} {'L':>5} {'S':>5} {'z':>7}"
    sep = f"  {'-'*110}"

    # ═══════════════════════════════════════════════════════════
    print("TEST 1: S9-FAST — Fade ±X% in Y hours")
    print(hdr); print(sep)

    for lookback in [1, 2, 3, 6]:
        for thresh in [300, 500, 800, 1000, 1500]:
            for hold in [3, 6, 12, 24]:
                def sig(candles, ci, _lb=lookback, _th=thresh):
                    if ci < _lb:
                        return None
                    ret = (candles[ci]["c"] / candles[ci - _lb]["c"] - 1) * 1e4
                    if abs(ret) >= _th:
                        d = -1 if ret > 0 else 1  # fade
                        return {"dir": d, "strength": abs(ret)}
                    return None
                r = backtest_fast(data, {"hold": hold, "signal_fn": sig, "max_pos": 4})
                if r and r["n"] >= 20:
                    z = monte_carlo(r["trades"])
                    if abs(z) >= 1.5 or (r.get("train", 0) > 0 and r.get("test", 0) > 0):
                        pr(f"Fade {thresh}bps in {lookback}h hold={hold}h", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 2: MICRO-SQUEEZE — Compression → false breakout → fade")
    print(hdr); print(sep)

    for sq_window in [3, 4, 6]:
        for bo_pct in [0.3, 0.5, 0.7]:
            for hold in [3, 6, 12]:
                def sig(candles, ci, _sw=sq_window, _bp=bo_pct):
                    if ci < _sw + 2:
                        return None
                    # Squeeze range
                    sq = candles[ci - _sw - 1:ci - 1]
                    rh = max(c["h"] for c in sq)
                    rl = min(c["l"] for c in sq)
                    rs = rh - rl
                    if rs <= 0 or rl <= 0:
                        return None
                    # Breakout candle (previous)
                    bo = candles[ci - 1]
                    th = rs * _bp
                    above = bo["h"] > rh + th
                    below = bo["l"] < rl - th
                    if not above and not below:
                        return None
                    if above and below:
                        return None
                    bo_dir = 1 if above else -1
                    # Reintegration (current candle)
                    if rl <= candles[ci]["c"] <= rh:
                        return {"dir": -bo_dir, "strength": 1000 / max(rs / rl * 100, 0.1)}
                    return None
                r = backtest_fast(data, {"hold": hold, "signal_fn": sig, "max_pos": 4})
                if r and r["n"] >= 15:
                    pr(f"Squeeze w={sq_window}h bo={bo_pct:.1f} hold={hold}h", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 3: VOLUME SPIKE REVERSAL — Vol > Nx avg → fade move")
    print(hdr); print(sep)

    for vol_mult in [2.0, 3.0, 5.0]:
        for ret_min in [100, 200, 300]:
            for hold in [3, 6, 12]:
                def sig(candles, ci, _vm=vol_mult, _rm=ret_min):
                    if ci < 24:
                        return None
                    vol_avg = np.mean([c["v"] for c in candles[ci-24:ci]])
                    if vol_avg <= 0:
                        return None
                    current_vol = candles[ci]["v"]
                    if current_vol < vol_avg * _vm:
                        return None
                    ret = (candles[ci]["c"] / candles[ci]["o"] - 1) * 1e4
                    if abs(ret) < _rm:
                        return None
                    d = -1 if ret > 0 else 1  # fade
                    return {"dir": d, "strength": abs(ret)}
                r = backtest_fast(data, {"hold": hold, "signal_fn": sig, "max_pos": 4})
                if r and r["n"] >= 15:
                    pr(f"Vol>{vol_mult:.0f}x ret>{ret_min}bps hold={hold}h", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 4: MOMENTUM BURST — Strong move + continue (not fade)")
    print(hdr); print(sep)

    for lookback in [1, 2, 3]:
        for thresh in [300, 500, 800]:
            for hold in [3, 6, 12]:
                def sig(candles, ci, _lb=lookback, _th=thresh):
                    if ci < _lb:
                        return None
                    ret = (candles[ci]["c"] / candles[ci - _lb]["c"] - 1) * 1e4
                    if abs(ret) >= _th:
                        d = 1 if ret > 0 else -1  # FOLLOW (not fade)
                        return {"dir": d, "strength": abs(ret)}
                    return None
                r = backtest_fast(data, {"hold": hold, "signal_fn": sig, "max_pos": 4})
                if r and r["n"] >= 20:
                    z = monte_carlo(r["trades"])
                    if abs(z) >= 1.5 or (r.get("train", 0) > 0 and r.get("test", 0) > 0):
                        pr(f"Follow {thresh}bps in {lookback}h hold={hold}h", r)


if __name__ == "__main__":
    main()
