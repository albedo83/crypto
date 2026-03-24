"""Reversal backtest v2 — Fixed overlapping trades + look-ahead bias.

Fixes from code review:
1. No overlapping positions (1 position per token at a time)
2. Entry at NEXT candle open, not signal candle close
3. Max 6 concurrent positions (matching bot)
4. Max 4 same direction
5. Stop loss -15% (matching bot)
6. Separate long/short analysis
7. Proper Monte Carlo control (direction-matched)

Usage:
    python3 -m analysis.backtest_reversal_v2
"""

from __future__ import annotations

import json, os, time, urllib.request, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

TOKENS = [
    "ARB", "OP", "STRK", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX", "DOGE", "WLD",
    "BLUR", "LINK", "PYTH", "EIGEN", "SOL",
    "TIA", "INJ", "FTM", "CRV", "LDO", "RNDR", "STX", "GMX",
    "IMX", "SAND", "AXS", "GALA", "MINA", "JUP", "ENA", "ONDO",
    "TAO", "HYPE", "ZRO", "W", "JTO",
]

COST_BPS = 7.0
MAX_POSITIONS = 6
MAX_SAME_DIR = 4
STOP_LOSS_BPS = -1500.0  # -15%


def fetch_candles(coin, interval="4h", days=400):
    cache = os.path.join(DATA_DIR, f"{coin}_{interval}.json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86400 * 1000
    try:
        payload = json.dumps({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval, "startTime": start_ts, "endTime": end_ts
        }}).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if data:
            with open(cache, "w") as f:
                json.dump(data, f)
        return data or []
    except:
        return []


def load_4h():
    """Load 4h candles: {coin: [{t, o, c, h, l}, ...]}"""
    data = {}
    for coin in TOKENS:
        candles = fetch_candles(coin, "4h", 400)
        if candles and len(candles) > 50:
            data[coin] = [{"t": c["t"], "o": float(c["o"]), "c": float(c["c"]),
                           "h": float(c["h"]), "l": float(c["l"])} for c in candles]
        time.sleep(0.1)
    return data


def backtest(data, lookback=18, threshold=500, hold=18, cost=COST_BPS,
             max_pos=MAX_POSITIONS, max_dir=MAX_SAME_DIR, stop_loss=STOP_LOSS_BPS,
             size=250.0):
    """
    Proper backtest with:
    - No overlapping positions per token
    - Entry at NEXT candle open (not signal candle close)
    - Position limits
    - Stop loss checked every candle
    """
    coins = [c for c in TOKENS if c in data]

    # Build per-coin index
    coin_candles = {}
    for c in coins:
        coin_candles[c] = data[c]

    # Find common time range
    all_ts = set()
    for c in coins:
        for candle in data[c]:
            all_ts.add(candle["t"])
    sorted_ts = sorted(all_ts)

    # Build time→index mapping per coin
    coin_idx = {}  # coin → {timestamp → index in coin's candle list}
    for c in coins:
        coin_idx[c] = {candle["t"]: i for i, candle in enumerate(data[c])}

    # State
    positions = {}  # coin → {direction, entry_price, entry_ts, entry_idx}
    trades = []
    cooldown = {}  # coin → earliest re-entry timestamp

    for ts in sorted_ts:
        # Check exits first
        for c in list(positions.keys()):
            pos = positions[c]
            if c not in coin_idx or ts not in coin_idx[c]:
                continue
            idx = coin_idx[c][ts]
            candle = data[c][idx]
            current = candle["c"]
            if current == 0:
                continue

            unrealized = pos["direction"] * (current / pos["entry_price"] - 1) * 1e4
            held = idx - pos["entry_idx"]

            exit_reason = None
            exit_price = current

            # Stop loss: check candle low/high
            if pos["direction"] == 1:  # long
                worst = candle["l"]
                worst_bps = (worst / pos["entry_price"] - 1) * 1e4
                if worst_bps < stop_loss:
                    exit_reason = "stop_loss"
                    exit_price = pos["entry_price"] * (1 + stop_loss / 1e4)  # stopped at stop level
            else:  # short
                worst = candle["h"]
                worst_bps = -(worst / pos["entry_price"] - 1) * 1e4
                if worst_bps < stop_loss:
                    exit_reason = "stop_loss"
                    exit_price = pos["entry_price"] * (1 - stop_loss / 1e4)

            # Timeout
            if held >= hold:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net = gross - cost
                pnl = size * net / 1e4

                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                trades.append({
                    "coin": c, "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                    "entry_date": pos["entry_date"], "exit_date": dt.strftime("%Y-%m-%d"),
                    "hold": held, "move": pos["move"],
                    "gross": round(gross, 1), "net": round(net, 1),
                    "pnl": round(pnl, 2), "reason": exit_reason,
                })
                del positions[c]
                cooldown[c] = ts + 6 * 3600 * 1000  # 6h cooldown (1.5 candles)

        # Check entries
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for c in coins:
            if c in positions:
                continue
            if c in cooldown and ts < cooldown[c]:
                continue
            if c not in coin_idx or ts not in coin_idx[c]:
                continue
            idx = coin_idx[c][ts]
            if idx < lookback or idx >= len(data[c]) - 1:
                continue

            price_now = data[c][idx]["c"]
            price_past = data[c][idx - lookback]["c"]
            if price_past == 0:
                continue

            move = (price_now / price_past - 1) * 1e4
            if abs(move) < threshold:
                continue

            direction = -1 if move > 0 else 1

            # Direction limit
            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            # Entry at NEXT candle open (fix look-ahead bias)
            next_idx = idx + 1
            if next_idx >= len(data[c]):
                continue
            entry_price = data[c][next_idx]["o"]  # OPEN of next candle
            if entry_price == 0:
                continue

            candidates.append({
                "coin": c, "direction": direction, "move": move,
                "strength": abs(move), "entry_price": entry_price,
                "entry_idx": next_idx,
                "entry_ts": data[c][next_idx]["t"],
            })

        # Rank by strength, take up to remaining slots
        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)

        for cand in candidates[:slots]:
            c = cand["coin"]
            dt = datetime.fromtimestamp(cand["entry_ts"] / 1000, tz=timezone.utc)
            positions[c] = {
                "direction": cand["direction"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_ts": cand["entry_ts"],
                "entry_date": dt.strftime("%Y-%m-%d"),
                "move": round(cand["move"], 0),
            }
            if cand["direction"] == 1:
                n_long += 1
            else:
                n_short += 1

    return trades


def analyze(trades, label):
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0, "avg_net": 0, "win_rate": 0}

    wins = sum(1 for t in trades if t["net"] > 0)
    avg_net = float(np.mean([t["net"] for t in trades]))
    total = sum(t["pnl"] for t in trades)

    longs = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]
    l_pnl = sum(t["pnl"] for t in longs)
    s_pnl = sum(t["pnl"] for t in shorts)
    l_avg = float(np.mean([t["net"] for t in longs])) if longs else 0
    s_avg = float(np.mean([t["net"] for t in shorts])) if shorts else 0
    l_wr = sum(1 for t in longs if t["net"] > 0) / len(longs) * 100 if longs else 0
    s_wr = sum(1 for t in shorts if t["net"] > 0) / len(shorts) * 100 if shorts else 0

    by_month = defaultdict(float)
    for t in trades:
        by_month[t["entry_date"][:7]] += t["pnl"]
    losing = sum(1 for v in by_month.values() if v <= 0)

    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["reason"]].append(t)

    print(f"\n{'═'*65}")
    print(f"  {label}")
    print(f"{'═'*65}")
    print(f"  Trades: {n} | Win: {wins/n*100:.0f}% | Net avg: {avg_net:+.1f} bps")
    print(f"  P&L: ${total:+.2f} | Monthly: ${total/max(1,len(by_month)):+.1f}")
    print(f"  LONG:  {len(longs):>4}t  {l_wr:.0f}% win  {l_avg:+.1f} bps  ${l_pnl:+.1f}")
    print(f"  SHORT: {len(shorts):>4}t  {s_wr:.0f}% win  {s_avg:+.1f} bps  ${s_pnl:+.1f}")

    for reason in sorted(by_reason):
        rt = by_reason[reason]
        rn = float(np.mean([t["net"] for t in rt]))
        rp = sum(t["pnl"] for t in rt)
        print(f"  {reason:<12}: {len(rt):>4}t  {rn:+.1f} bps  ${rp:+.1f}")

    print(f"\n  Months: {len(by_month)-losing}/{len(by_month)} winning")
    for m in sorted(by_month):
        marker = "✓" if by_month[m] > 0 else "✗"
        print(f"    {m}: ${by_month[m]:>+8.2f} {marker}")

    # Top/bottom tokens
    by_tk = defaultdict(list)
    for t in trades:
        by_tk[t["coin"]].append(t)
    tk_s = sorted([(k, sum(t["pnl"] for t in v), len(v), float(np.mean([t["net"] for t in v])))
                    for k, v in by_tk.items()], key=lambda x: x[1], reverse=True)
    winners = sum(1 for _, p, _, _ in tk_s if p > 0)
    print(f"\n  Tokens: {winners}/{len(tk_s)} profitable")
    for tk, pnl, cnt, avg in tk_s[:5]:
        print(f"    {tk:<8} {cnt:>4}t {avg:>+7.1f} bps ${pnl:>+8.2f} ✓")
    if len(tk_s) > 8:
        print(f"    ...")
    for tk, pnl, cnt, avg in tk_s[-3:]:
        print(f"    {tk:<8} {cnt:>4}t {avg:>+7.1f} bps ${pnl:>+8.2f} ✗")

    return {"label": label, "trades": n, "wins": wins, "win_rate": wins/n,
            "avg_net": avg_net, "pnl": total, "l_pnl": l_pnl, "s_pnl": s_pnl,
            "l_avg": l_avg, "s_avg": s_avg, "l_n": len(longs), "s_n": len(shorts),
            "losing_months": losing, "total_months": len(by_month),
            "monthly": total / max(1, len(by_month)), "trades_list": trades}


def monte_carlo(data, trades, n_sims=500, lookback=18, hold=18, cost=COST_BPS, size=250.0):
    """
    Direction-matched Monte Carlo: same number of longs and shorts per token,
    but at RANDOM dates. Tests whether the TIMING matters.
    """
    coins = list(set(t["coin"] for t in trades))
    # Count longs/shorts per token
    by_coin_dir = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        if t["direction"] == "LONG":
            by_coin_dir[t["coin"]]["long"] += 1
        else:
            by_coin_dir[t["coin"]]["short"] += 1

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        for c in coins:
            if c not in data:
                continue
            candles = data[c]
            n_candles = len(candles)
            if n_candles < lookback + hold + 5:
                continue

            available = list(range(lookback, n_candles - hold - 1))
            n_long = by_coin_dir[c]["long"]
            n_short = by_coin_dir[c]["short"]
            total_needed = n_long + n_short

            if len(available) < total_needed:
                continue

            sampled = random.sample(available, total_needed)
            # Assign directions: first n_long are long, rest are short
            for j, idx in enumerate(sampled):
                direction = 1 if j < n_long else -1
                entry_price = candles[idx + 1]["o"]  # next candle open
                exit_price = candles[idx + 1 + hold]["c"] if idx + 1 + hold < n_candles else candles[-1]["c"]
                if entry_price == 0:
                    continue
                gross = direction * (exit_price / entry_price - 1) * 1e4
                net = gross - cost
                sim_total += size * net / 1e4

        sim_pnls.append(sim_total)

    return sim_pnls


def main():
    print("=" * 65)
    print("  REVERSAL BACKTEST v2 — FIXED")
    print("  No overlaps, next-candle entry, position limits, stop loss")
    print("=" * 65)

    print("\nLoading 4h candles...")
    data = load_4h()
    print(f"Loaded {len(data)} tokens")

    results = []

    configs = [
        ("Rev 3d>500 hold 3d", 18, 500, 18),
        ("Rev 3d>750 hold 3d", 18, 750, 18),
        ("Rev 3d>1000 hold 3d", 18, 1000, 18),
        ("Rev 5d>500 hold 3d", 30, 500, 18),
        ("Rev 5d>1000 hold 3d", 30, 1000, 18),
        ("Rev 3d>500 hold 5d", 18, 500, 30),
        ("Rev 3d>1000 hold 5d", 18, 1000, 30),
        ("Rev 7d>500 hold 3d", 42, 500, 18),
        ("Rev 7d>1000 hold 3d", 42, 1000, 18),
    ]

    for label, lb, thresh, hold in configs:
        print(f"\nRunning: {label}")
        t0 = time.time()
        trades = backtest(data, lookback=lb, threshold=thresh, hold=hold)
        elapsed = time.time() - t0
        print(f"  ({elapsed:.0f}s)")
        r = analyze(trades, label)
        results.append(r)

    # ── Monte Carlo on best config ───────────────────────────────
    print(f"\n\n{'▓'*65}")
    print(f"  MONTE CARLO CONTROL — Direction-matched random timing")
    print(f"{'▓'*65}")

    # Top 3 by P&L
    top3 = sorted([r for r in results if r["trades"] >= 10], key=lambda r: r["pnl"], reverse=True)[:3]

    for r in top3:
        label = r["label"]
        trades = r["trades_list"]
        actual_pnl = r["pnl"]

        # Parse config
        parts = label.replace("Rev ", "").split()
        lb_str, thresh_str = parts[0].split(">")
        lb_map = {"3d": 18, "5d": 30, "7d": 42}
        hold_str = parts[2]
        lb = lb_map.get(lb_str, 18)
        thresh = int(thresh_str.replace("bps", ""))
        hold = lb_map.get(hold_str, 18)

        print(f"\n  {label} (actual P&L: ${actual_pnl:+.2f})")
        print(f"  Running 500 direction-matched random simulations...")

        sim_pnls = monte_carlo(data, trades, n_sims=500, lookback=lb, hold=hold)

        rand_mean = np.mean(sim_pnls)
        rand_std = np.std(sim_pnls)
        z = (actual_pnl - rand_mean) / rand_std if rand_std > 0 else 0
        pct_better = sum(1 for s in sim_pnls if s >= actual_pnl) / 500 * 100

        print(f"    Actual:    ${actual_pnl:+.2f}")
        print(f"    Random:    ${rand_mean:+.2f} (std: ${rand_std:.2f})")
        print(f"    Z-score:   {z:+.2f}")
        print(f"    Random >= actual: {pct_better:.1f}%")

        if z > 2:
            print(f"    → ✓✓ SIGNIFICANT (z > 2)")
        elif z > 1.5:
            print(f"    → ✓ MARGINAL")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    # ── FINAL SUMMARY ────────────────────────────────────────────
    print(f"\n\n{'█'*65}")
    print(f"  FINAL SUMMARY (FIXED BACKTEST)")
    print(f"{'█'*65}")
    print(f"\n  {'Config':<28} {'N':>5} {'Win%':>5} {'Net/t':>7} {'P&L$':>8} "
          f"{'L$':>7} {'S$':>7} {'$/mo':>7} {'L.Mo':>5}")
    print(f"  {'-'*85}")

    results.sort(key=lambda r: r.get("pnl", -9999), reverse=True)
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r["pnl"] > 0 else "✗"
        lm = f"{r.get('losing_months','?')}/{r.get('total_months','?')}"
        print(f"  {r['label']:<28} {r['trades']:>5} {r['win_rate']*100:>4.0f}% "
              f"{r['avg_net']:>+6.1f} ${r['pnl']:>+7.0f} "
              f"${r.get('l_pnl',0):>+6.0f} ${r.get('s_pnl',0):>+6.0f} "
              f"${r.get('monthly',0):>+6.0f} {lm:>5} {m}")


if __name__ == "__main__":
    main()
