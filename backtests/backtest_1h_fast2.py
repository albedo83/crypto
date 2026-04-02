"""Fast Signal Search v2 — More 1h patterns on 7 months of data.

Tests:
  5. BTC lead → alt lag (BTC moves, alts follow with 1-3h delay)
  6. Consecutive candles (3+ same direction → fade)
  7. Intra-day range breakout (24h high/low breach → follow)
  8. Cross-alt momentum (long strongest 1h, short weakest 1h)
  9. Volatility contraction breakout (low vol → next big move continues)
  10. Multi-timeframe: 24h trend + 1h dip = buy the dip in uptrend

Usage:
    python3 -m analysis.backtest_1h_fast2
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
COST_BPS = 12
LEVERAGE = 2.0

TOKENS = ['ARB','OP','AVAX','SUI','APT','SEI','NEAR','AAVE','COMP','SNX','PENDLE','DYDX',
          'DOGE','WLD','BLUR','LINK','PYTH','SOL','INJ','CRV','LDO','STX','GMX','IMX','SAND',
          'GALA','MINA']


def load_1h():
    data = {}
    for coin in TOKENS + ['BTC', 'ETH']:
        path = os.path.join(DATA_DIR, f"{coin}_1h.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        if not raw:
            continue
        data[coin] = [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]),
                       "l": float(c["l"]), "c": float(c["c"]),
                       "v": float(c.get("v", 0))} for c in raw]
    return data


def mc(trades, n=1000):
    if len(trades) < 10:
        return 0.0
    actual = np.mean([t["net"] for t in trades])
    nets = [t["net"] for t in trades]
    rm = [np.mean(random.sample(nets, len(nets))) for _ in range(n)]
    s = np.std(rm)
    return (actual - np.mean(rm)) / s if s > 0 else 0


def backtest(data, config):
    hold = config.get("hold", 6)
    stop_bps = config.get("stop_bps", -2500)
    max_pos = config.get("max_pos", 4)
    size = 150
    signal_fn = config["signal_fn"]
    effective_cost = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE
    coins = [c for c in TOKENS if c in data and len(data[c]) > 100]

    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    sorted_ts = sorted(all_ts)
    mid = sorted_ts[len(sorted_ts) // 2]

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted_ts:
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
            es = stop_bps / LEVERAGE
            if d == 1:
                w = (candle["l"] / pos["entry"] - 1) * 1e4
                if w < es:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + es / 1e4)
            else:
                w = -(candle["h"] / pos["entry"] - 1) * 1e4
                if w < es:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - es / 1e4)
            if held >= hold:
                exit_reason = "timeout"
            if exit_reason:
                gross = d * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - effective_cost
                trades.append({"net": round(net, 1), "pnl": round(size * net / 1e4, 2),
                               "dir": d, "entry_t": pos["entry_t"], "exit_t": ts})
                del positions[coin]
                cooldown[coin] = ts + 4 * 3600 * 1000

        if len(positions) >= max_pos:
            continue

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            if ts not in coin_by_ts.get(coin, {}):
                continue
            ci = coin_by_ts[coin][ts]
            result = signal_fn(data, coin, ci, ts, coin_by_ts)
            if result:
                candidates.append({"coin": coin, **result, "ci": ci})

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
    total = sum(t["pnl"] for t in trades)
    train = sum(t["pnl"] for t in trades if t["entry_t"] < mid)
    test = sum(t["pnl"] for t in trades if t["entry_t"] >= mid)
    nl = sum(1 for t in trades if t["dir"] == 1)
    ns = sum(1 for t in trades if t["dir"] == -1)
    return {"n": n, "avg": round(avg, 1), "win": round(wins/n*100, 0),
            "pnl": round(total, 0), "train": round(train, 0), "test": round(test, 0),
            "n_long": nl, "n_short": ns, "trades": trades}


def pr(label, r):
    if not r or r["n"] < 15:
        return
    z = mc(r["trades"])
    v = "✓" if r.get("train", 0) > 0 and r.get("test", 0) > 0 else ""
    print(f"  {label:<55} {r['n']:>5} {r['avg']:>+7.1f} {r['win']:>4}% ${r['pnl']:>+7} "
          f"${r['train']:>+6} ${r['test']:>+6} {r['n_long']:>4}L {r['n_short']:>4}S z={z:>+5.2f} {v}")


def main():
    print("=" * 120)
    print("  FAST SIGNALS v2 — More 1h patterns, 7 months")
    print("=" * 120)
    data = load_1h()
    print(f"Loaded: {len(data)} tokens\n")

    hdr = f"  {'Signal':<55} {'N':>5} {'Avg':>7} {'W%':>5} {'P&L':>8} {'Trn':>7} {'Tst':>7} {'L':>5} {'S':>5} {'z':>7}"
    sep = f"  {'-'*115}"

    # BTC data for lead-lag
    btc = data.get("BTC", [])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc)}

    # ═══ TEST 5: BTC LEAD → ALT LAG ═══
    print("TEST 5: BTC LEAD → ALT LAG (BTC moves, alts follow with delay)")
    print(hdr); print(sep)
    for btc_thresh in [200, 300, 500]:
        for delay in [1, 2, 3]:
            for hold in [3, 6, 12]:
                def sig(data, coin, ci, ts, cbt, _bt=btc_thresh, _dl=delay):
                    if ts not in btc_by_ts:
                        return None
                    bi = btc_by_ts[ts]
                    if bi < _dl:
                        return None
                    btc_ret = (btc[bi - _dl]["c"] / btc[bi - _dl - 1]["c"] - 1) * 1e4 if btc[bi-_dl-1]["c"] > 0 else 0
                    if abs(btc_ret) < _bt:
                        return None
                    # Alt hasn't moved much yet in the same direction
                    candles = data[coin]
                    alt_ret = (candles[ci]["c"] / candles[ci - 1]["c"] - 1) * 1e4 if candles[ci-1]["c"] > 0 else 0
                    if btc_ret > 0 and alt_ret < btc_ret * 0.3:  # alt lagging BTC pump
                        return {"dir": 1, "strength": abs(btc_ret)}
                    if btc_ret < 0 and alt_ret > btc_ret * 0.3:  # alt lagging BTC dump
                        return {"dir": -1, "strength": abs(btc_ret)}
                    return None
                r = backtest(data, {"hold": hold, "signal_fn": sig})
                pr(f"BTC>{btc_thresh}bps delay={delay}h hold={hold}h", r)

    # ═══ TEST 6: CONSECUTIVE CANDLES ═══
    print(f"\nTEST 6: CONSECUTIVE CANDLES (N same direction → fade)")
    print(hdr); print(sep)
    for n_consec in [3, 4, 5, 6]:
        for min_move in [100, 200, 300]:
            for hold in [3, 6, 12]:
                def sig(data, coin, ci, ts, cbt, _nc=n_consec, _mm=min_move):
                    candles = data[coin]
                    if ci < _nc:
                        return None
                    # Check N consecutive up or down
                    up = all(candles[ci-j]["c"] > candles[ci-j]["o"] for j in range(_nc))
                    dn = all(candles[ci-j]["c"] < candles[ci-j]["o"] for j in range(_nc))
                    if not up and not dn:
                        return None
                    total_move = (candles[ci]["c"] / candles[ci-_nc]["c"] - 1) * 1e4
                    if abs(total_move) < _mm:
                        return None
                    d = -1 if up else 1  # fade
                    return {"dir": d, "strength": abs(total_move)}
                r = backtest(data, {"hold": hold, "signal_fn": sig})
                pr(f"{n_consec} consec min={min_move}bps hold={hold}h", r)

    # ═══ TEST 7: 24H RANGE BREAKOUT ═══
    print(f"\nTEST 7: 24H RANGE BREAKOUT (follow)")
    print(hdr); print(sep)
    for bo_pct in [0.0, 0.5, 1.0]:  # % beyond range
        for hold in [3, 6, 12, 24]:
            def sig(data, coin, ci, ts, cbt, _bp=bo_pct):
                candles = data[coin]
                if ci < 25:
                    return None
                h24 = max(c["h"] for c in candles[ci-24:ci])
                l24 = min(c["l"] for c in candles[ci-24:ci])
                rng = h24 - l24
                if rng <= 0:
                    return None
                curr = candles[ci]["c"]
                if curr > h24 + rng * _bp / 100:
                    return {"dir": 1, "strength": (curr / h24 - 1) * 1e4}
                if curr < l24 - rng * _bp / 100:
                    return {"dir": -1, "strength": (l24 / curr - 1) * 1e4}
                return None
            r = backtest(data, {"hold": hold, "signal_fn": sig})
            pr(f"24h breakout +{bo_pct:.0f}% hold={hold}h", r)

    # ═══ TEST 8: CROSS-ALT MOMENTUM ═══
    print(f"\nTEST 8: CROSS-ALT MOMENTUM (long strongest, short weakest hourly)")
    print(hdr); print(sep)
    for n_top in [1, 3, 5]:
        for hold in [3, 6, 12]:
            for min_spread in [100, 200, 300]:
                def sig(data, coin, ci, ts, cbt, _nt=n_top, _ms=min_spread):
                    # Rank all alts by 1h return
                    rets = {}
                    for c2 in TOKENS:
                        if c2 not in cbt or ts not in cbt[c2]:
                            continue
                        i2 = cbt[c2][ts]
                        if i2 < 1 or data[c2][i2-1]["c"] <= 0:
                            continue
                        rets[c2] = (data[c2][i2]["c"] / data[c2][i2-1]["c"] - 1) * 1e4
                    if len(rets) < 10:
                        return None
                    ranked = sorted(rets.items(), key=lambda x: x[1], reverse=True)
                    top = [r[0] for r in ranked[:_nt]]
                    bottom = [r[0] for r in ranked[-_nt:]]
                    spread = rets[ranked[0][0]] - rets[ranked[-1][0]]
                    if spread < _ms:
                        return None
                    if coin in top:
                        return {"dir": 1, "strength": rets[coin]}  # follow winner
                    if coin in bottom:
                        return {"dir": -1, "strength": abs(rets[coin])}  # follow loser down
                    return None
                r = backtest(data, {"hold": hold, "signal_fn": sig, "max_pos": min(6, n_top*2)})
                pr(f"Top/bottom {n_top} spread>{min_spread} hold={hold}h", r)

    # ═══ TEST 9: VOL CONTRACTION BREAKOUT ═══
    print(f"\nTEST 9: VOL CONTRACTION → BREAKOUT (follow the expansion)")
    print(hdr); print(sep)
    for lookback in [6, 12, 24]:
        for vol_ratio in [0.3, 0.5]:
            for min_move in [100, 200]:
                for hold in [3, 6, 12]:
                    def sig(data, coin, ci, ts, cbt, _lb=lookback, _vr=vol_ratio, _mm=min_move):
                        candles = data[coin]
                        if ci < _lb + 1:
                            return None
                        # Avg range of last N candles
                        ranges = [(candles[ci-j]["h"] - candles[ci-j]["l"]) for j in range(1, _lb+1)]
                        avg_range = np.mean(ranges)
                        if avg_range <= 0:
                            return None
                        # Current candle range vs average
                        curr_range = candles[ci]["h"] - candles[ci]["l"]
                        if curr_range < avg_range * (1 / _vr):  # current must be big (expansion)
                            return None
                        # Prior candles must have been quiet
                        recent_ranges = ranges[:3]
                        if np.mean(recent_ranges) > avg_range * _vr:
                            return None
                        # Direction of breakout
                        ret = (candles[ci]["c"] - candles[ci]["o"])
                        if candles[ci]["o"] <= 0:
                            return None
                        ret_bps = ret / candles[ci]["o"] * 1e4
                        if abs(ret_bps) < _mm:
                            return None
                        d = 1 if ret_bps > 0 else -1  # follow breakout
                        return {"dir": d, "strength": abs(ret_bps)}
                    r = backtest(data, {"hold": hold, "signal_fn": sig})
                    pr(f"VolBreak lb={lookback} vr={vol_ratio} min={min_move} h={hold}", r)

    # ═══ TEST 10: MULTI-TIMEFRAME DIP BUY ═══
    print(f"\nTEST 10: MULTI-TIMEFRAME (24h trend UP + 1h dip → buy)")
    print(hdr); print(sep)
    for trend_thresh in [200, 500, 1000]:
        for dip_thresh in [-100, -200, -300]:
            for hold in [3, 6, 12]:
                def sig(data, coin, ci, ts, cbt, _tt=trend_thresh, _dt=dip_thresh):
                    candles = data[coin]
                    if ci < 25:
                        return None
                    trend = (candles[ci]["c"] / candles[ci-24]["c"] - 1) * 1e4
                    dip = (candles[ci]["c"] / candles[ci-1]["c"] - 1) * 1e4
                    # Uptrend + 1h dip → buy
                    if trend > _tt and dip < _dt:
                        return {"dir": 1, "strength": abs(dip)}
                    # Downtrend + 1h bounce → short
                    if trend < -_tt and dip > -_dt:
                        return {"dir": -1, "strength": abs(dip)}
                    return None
                r = backtest(data, {"hold": hold, "signal_fn": sig})
                pr(f"Trend>{trend_thresh} dip<{dip_thresh} hold={hold}h", r)


if __name__ == "__main__":
    main()
