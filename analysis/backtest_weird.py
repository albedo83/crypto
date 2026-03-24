"""Unconventional strategies backtest — Hyperliquid.

Three improbable strategies nobody talks about:
1. Beta Catch-Up: tokens lagging the market catch up violently
2. Funding Contrarian: pay funding, bet on price reversal
3. Volume Anomaly: unusual volume precedes price moves

Usage:
    python3 -m analysis.backtest_weird
"""

from __future__ import annotations

import json, os, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
os.makedirs(DATA_DIR, exist_ok=True)

TOKENS = [
    "ARB", "OP", "STRK", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH", "EIGEN",
    "SOL", "BTC", "ETH",
]

COST_BPS = 7.0  # taker roundtrip


def fetch_candles(coin, interval="1d", days=365):
    cache = os.path.join(DATA_DIR, f"{coin}_{interval}.json")
    if os.path.exists(cache):
        age = (time.time() - os.path.getmtime(cache)) / 3600
        if age < 12:
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


def load_all(interval="1d", days=365):
    prices = {}  # coin → {date → close}
    volumes = {}  # coin → {date → volume}
    for coin in TOKENS:
        candles = fetch_candles(coin, interval, days)
        if not candles:
            continue
        p, v = {}, {}
        for c in candles:
            dt = datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc)
            d = dt.strftime("%Y-%m-%d") if interval == "1d" else dt.strftime("%Y-%m-%d %H:%M")
            p[d] = float(c["c"])
            v[d] = float(c["v"])
        if len(p) > 50:
            prices[coin] = p
            volumes[coin] = v
        time.sleep(0.3)
    return prices, volumes


def analyze(trades, label):
    n = len(trades)
    if n == 0:
        print(f"\n  {label}: 0 trades")
        return {"label": label, "trades": 0, "pnl": 0}
    wins = sum(1 for t in trades if t["net"] > 0)
    avg = float(np.mean([t["net"] for t in trades]))
    avg_g = float(np.mean([t["gross"] for t in trades]))
    total = sum(t["pnl"] for t in trades)

    print(f"\n{'═'*60}")
    print(f"  {label}")
    print(f"{'═'*60}")
    print(f"  Trades: {n} ({n/12:.1f}/mois) | Win: {wins/n*100:.0f}%")
    print(f"  Gross: {avg_g:+.1f} bps | Net: {avg:+.1f} bps")
    print(f"  P&L: ${total:+.2f} (at $250/trade)")

    by_month = defaultdict(float)
    for t in trades:
        by_month[t["date"][:7]] += t["pnl"]
    losing = sum(1 for v in by_month.values() if v <= 0)
    print(f"  Months: {len(by_month)-losing}/{len(by_month)} winning")
    for m in sorted(by_month.keys()):
        marker = "✓" if by_month[m] > 0 else "✗"
        print(f"    {m}: ${by_month[m]:>+8.2f} {marker}")

    # By token
    by_tk = defaultdict(list)
    for t in trades:
        by_tk[t["coin"]].append(t)
    tk_s = sorted([(k, sum(t["pnl"] for t in v), len(v), float(np.mean([t["net"] for t in v])))
                    for k, v in by_tk.items()], key=lambda x: x[1], reverse=True)
    print(f"\n  {'Token':<8} {'N':>4} {'AvgNet':>8} {'P&L$':>9}")
    for tk, pnl, cnt, avg in tk_s[:5]:
        print(f"  {tk:<8} {cnt:>4} {avg:>+7.1f} ${pnl:>+8.2f} ✓")
    if len(tk_s) > 5:
        print(f"  ...")
    for tk, pnl, cnt, avg in tk_s[-3:]:
        print(f"  {tk:<8} {cnt:>4} {avg:>+7.1f} ${pnl:>+8.2f} ✗")

    return {"label": label, "trades": n, "wins": wins, "win_rate": wins/n,
            "avg_net": avg, "pnl": total, "losing_months": losing, "total_months": len(by_month)}


def main():
    print("=" * 60)
    print("  UNCONVENTIONAL STRATEGIES — Hyperliquid")
    print("=" * 60)

    print("\nLoading data...")
    prices, volumes = load_all("1d", 365)
    print(f"Loaded {len(prices)} tokens")

    coins = [c for c in TOKENS if c in prices and c != "BTC" and c != "ETH"]
    dates = sorted(set.intersection(*[set(prices[c].keys()) for c in prices if c in coins + ["BTC", "ETH"]]))
    print(f"Common dates: {len(dates)} ({dates[0]} → {dates[-1]})")

    # Pre-compute returns
    btc_ret = {}
    eth_ret = {}
    coin_ret = {c: {} for c in coins}
    coin_vol_ratio = {c: {} for c in coins}

    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i-1]
        if dp in prices["BTC"] and d in prices["BTC"] and prices["BTC"][dp] > 0:
            btc_ret[d] = (prices["BTC"][d] / prices["BTC"][dp] - 1) * 1e4
        if dp in prices["ETH"] and d in prices["ETH"] and prices["ETH"][dp] > 0:
            eth_ret[d] = (prices["ETH"][d] / prices["ETH"][dp] - 1) * 1e4

        for c in coins:
            if dp in prices[c] and d in prices[c] and prices[c][dp] > 0:
                coin_ret[c][d] = (prices[c][d] / prices[c][dp] - 1) * 1e4

            # Volume ratio: today's volume / 14-day average
            if c in volumes and d in volumes[c]:
                lookback = [volumes[c].get(dates[j], 0) for j in range(max(0, i-14), i)]
                avg_vol = np.mean(lookback) if lookback else 1
                coin_vol_ratio[c][d] = volumes[c][d] / avg_vol if avg_vol > 0 else 1

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 1: BETA CATCH-UP
    # When a token lags the market (BTC), it catches up the next day
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'▓'*60}")
    print(f"  STRATEGY 1: BETA CATCH-UP")
    print(f"  Token lags market → catches up next day")
    print(f"{'▓'*60}")

    results = []

    for hold in [1, 3]:
        for residual_thresh in [150, 200, 300]:
            trades = []
            # Compute rolling beta for each coin (30-day window)
            for c in coins:
                betas = {}
                for i in range(30, len(dates)):
                    d = dates[i]
                    window_dates = dates[i-30:i]
                    x = [btc_ret.get(wd, 0) for wd in window_dates]
                    y = [coin_ret[c].get(wd, 0) for wd in window_dates]
                    x, y = np.array(x), np.array(y)
                    if np.std(x) > 0:
                        beta = np.cov(x, y)[0, 1] / np.var(x)
                    else:
                        beta = 1.0
                    betas[d] = beta

                # Trade: when residual (actual - expected) is extreme
                for i in range(31, len(dates) - hold):
                    d = dates[i]
                    if d not in betas or d not in btc_ret or d not in coin_ret[c]:
                        continue
                    expected = betas[d] * btc_ret[d]
                    actual = coin_ret[c][d]
                    residual = actual - expected  # positive = outperformed, negative = lagged

                    if residual < -residual_thresh:
                        # Token lagged → buy (catch-up)
                        exit_d = dates[i + hold]
                        if exit_d in prices[c] and d in prices[c]:
                            gross = (prices[c][exit_d] / prices[c][d] - 1) * 1e4
                            # Hedge: short BTC proportional to beta
                            btc_move = 0
                            if exit_d in prices["BTC"] and d in prices["BTC"] and prices["BTC"][d] > 0:
                                btc_move = (prices["BTC"][exit_d] / prices["BTC"][d] - 1) * 1e4
                            hedged_gross = gross - betas[d] * btc_move  # market-neutral P&L
                            net = hedged_gross - COST_BPS * 2  # cost for 2 legs (token + BTC hedge)
                            trades.append({"coin": c, "date": d, "gross": round(hedged_gross, 1),
                                           "net": round(net, 1), "pnl": round(250 * net / 1e4, 2),
                                           "residual": round(residual, 1)})

                    elif residual > residual_thresh:
                        # Token outperformed → short (revert)
                        exit_d = dates[i + hold]
                        if exit_d in prices[c] and d in prices[c]:
                            gross = -(prices[c][exit_d] / prices[c][d] - 1) * 1e4
                            btc_move = 0
                            if exit_d in prices["BTC"] and d in prices["BTC"] and prices["BTC"][d] > 0:
                                btc_move = (prices["BTC"][exit_d] / prices["BTC"][d] - 1) * 1e4
                            hedged_gross = gross + betas[d] * btc_move
                            net = hedged_gross - COST_BPS * 2
                            trades.append({"coin": c, "date": d, "gross": round(hedged_gross, 1),
                                           "net": round(net, 1), "pnl": round(250 * net / 1e4, 2),
                                           "residual": round(residual, 1)})

            label = f"1. CatchUp resid>{residual_thresh}bps hold={hold}d"
            r = analyze(trades, label)
            results.append(r)

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 2: VOLUME ANOMALY — high volume today → price move tomorrow
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'▓'*60}")
    print(f"  STRATEGY 2: VOLUME PRECEDES PRICE")
    print(f"  Unusual volume + price direction → follow")
    print(f"{'▓'*60}")

    for hold in [1, 3]:
        for vol_mult in [3, 5, 7]:
            trades = []
            for c in coins:
                for i in range(15, len(dates) - hold):
                    d = dates[i]
                    if d not in coin_vol_ratio[c] or d not in coin_ret[c]:
                        continue
                    vratio = coin_vol_ratio[c][d]
                    ret_today = coin_ret[c][d]

                    if vratio < vol_mult:
                        continue

                    # High volume day. Which direction?
                    # If price went UP on high volume → momentum → LONG
                    # If price went DOWN on high volume → momentum → SHORT
                    if abs(ret_today) < 50:  # need meaningful move too
                        continue

                    direction = 1 if ret_today > 0 else -1
                    exit_d = dates[i + hold]

                    if exit_d in prices[c] and d in prices[c]:
                        gross = direction * (prices[c][exit_d] / prices[c][d] - 1) * 1e4
                        net = gross - COST_BPS
                        trades.append({"coin": c, "date": d, "gross": round(gross, 1),
                                       "net": round(net, 1), "pnl": round(250 * net / 1e4, 2)})

            label = f"2a. VolMomentum vol>{vol_mult}x ret>50bps hold={hold}d"
            r = analyze(trades, label)
            results.append(r)

    # Volume anomaly CONTRARIAN: high volume + move → REVERSAL next day
    for hold in [1, 3]:
        for vol_mult in [3, 5]:
            trades = []
            for c in coins:
                for i in range(15, len(dates) - hold):
                    d = dates[i]
                    if d not in coin_vol_ratio[c] or d not in coin_ret[c]:
                        continue
                    vratio = coin_vol_ratio[c][d]
                    ret_today = coin_ret[c][d]

                    if vratio < vol_mult or abs(ret_today) < 100:
                        continue

                    # Contrarian: fade the move on high volume
                    direction = -1 if ret_today > 0 else 1
                    exit_d = dates[i + hold]

                    if exit_d in prices[c] and d in prices[c]:
                        gross = direction * (prices[c][exit_d] / prices[c][d] - 1) * 1e4
                        net = gross - COST_BPS
                        trades.append({"coin": c, "date": d, "gross": round(gross, 1),
                                       "net": round(net, 1), "pnl": round(250 * net / 1e4, 2)})

            label = f"2b. VolContrarian vol>{vol_mult}x ret>100bps hold={hold}d"
            r = analyze(trades, label)
            results.append(r)

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 3: MULTI-DAY MOMENTUM (simple but often overlooked)
    # Tokens that moved a lot over 3-5 days continue the next 1-3 days
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'▓'*60}")
    print(f"  STRATEGY 3: MULTI-DAY MOMENTUM")
    print(f"  Strong 3-7 day moves continue")
    print(f"{'▓'*60}")

    for lookback in [3, 5, 7]:
        for hold in [1, 3]:
            for thresh in [500, 1000]:
                trades = []
                for c in coins:
                    for i in range(lookback, len(dates) - hold):
                        d = dates[i]
                        d_lb = dates[i - lookback]
                        if d not in prices[c] or d_lb not in prices[c] or prices[c][d_lb] == 0:
                            continue
                        move = (prices[c][d] / prices[c][d_lb] - 1) * 1e4

                        if abs(move) < thresh:
                            continue

                        direction = 1 if move > 0 else -1  # follow momentum
                        exit_d = dates[i + hold]
                        if exit_d in prices[c]:
                            gross = direction * (prices[c][exit_d] / prices[c][d] - 1) * 1e4
                            net = gross - COST_BPS
                            trades.append({"coin": c, "date": d, "gross": round(gross, 1),
                                           "net": round(net, 1), "pnl": round(250 * net / 1e4, 2)})

                label = f"3a. Momentum {lookback}d>{thresh}bps hold={hold}d"
                r = analyze(trades, label)
                results.append(r)

    # REVERSAL instead of momentum
    for lookback in [3, 5]:
        for hold in [1, 3]:
            for thresh in [1000, 1500]:
                trades = []
                for c in coins:
                    for i in range(lookback, len(dates) - hold):
                        d = dates[i]
                        d_lb = dates[i - lookback]
                        if d not in prices[c] or d_lb not in prices[c] or prices[c][d_lb] == 0:
                            continue
                        move = (prices[c][d] / prices[c][d_lb] - 1) * 1e4

                        if abs(move) < thresh:
                            continue

                        direction = -1 if move > 0 else 1  # fade
                        exit_d = dates[i + hold]
                        if exit_d in prices[c]:
                            gross = direction * (prices[c][exit_d] / prices[c][d] - 1) * 1e4
                            net = gross - COST_BPS
                            trades.append({"coin": c, "date": d, "gross": round(gross, 1),
                                           "net": round(net, 1), "pnl": round(250 * net / 1e4, 2)})

                label = f"3b. Reversal {lookback}d>{thresh}bps hold={hold}d"
                r = analyze(trades, label)
                results.append(r)

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'█'*70}")
    print(f"  FINAL SUMMARY — UNCONVENTIONAL STRATEGIES")
    print(f"{'█'*70}")
    print(f"\n  {'Config':<48} {'N':>5} {'Win%':>5} {'Net/t':>7} {'P&L$':>9} {'L.Mo':>5}")
    print(f"  {'-'*78}")

    # Sort by P&L
    results.sort(key=lambda r: r.get("pnl", -9999), reverse=True)
    for r in results:
        if r["trades"] == 0:
            continue
        m = "✓" if r["pnl"] > 0 else "✗"
        lm = f"{r.get('losing_months','?')}/{r.get('total_months','?')}"
        print(f"  {r['label']:<48} {r['trades']:>5} {r.get('win_rate',0)*100:>4.0f}% "
              f"{r.get('avg_net',0):>+6.1f} ${r['pnl']:>+8.2f} {lm:>5} {m}")

    winners = [r for r in results if r.get("pnl", 0) > 0 and r["trades"] >= 10]
    if winners:
        best = winners[0]
        print(f"\n  BEST: {best['label']}")
        monthly = best["pnl"] / max(1, best.get("total_months", 1))
        print(f"  → ${best['pnl']:+.2f}/an = ${monthly:+.2f}/mois = {monthly/10:+.2f}%/mois")
    else:
        print(f"\n  Aucune stratégie gagnante.")


if __name__ == "__main__":
    main()
