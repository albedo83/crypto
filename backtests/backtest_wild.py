"""Wild Ideas Backtest — 6 unconventional strategies on altcoins.

1. Weekend effect (Friday→Monday, Saturday→Monday)
2. Fade individual pumps/dumps (>15-20% in 24h)
3. Dispersion extremes (low disp → breakout, high disp → revert)
4. Volume exhaustion (high vol → vol drops → fade)
5. Cross-token momentum (long top 5, short bottom 5 weekly)
6. Monday revenge (buy Sunday crash, sell Tuesday)

Usage:
    python3 -m analysis.backtest_wild
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS,
    TRAIN_END, TEST_START,
)

COST = COST_BPS
SIZE = POSITION_SIZE


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1), "win": round(wins / n * 100, 0)}


def monte_carlo_simple(real_trades, all_entries, hold, n_sims=500):
    """Generic MC: shuffle timing, keep trade count."""
    if len(real_trades) < 10 or not all_entries:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    n_trades = len(real_trades)

    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, idx, candles in sampled:
            if idx + hold >= len(candles):
                continue
            direction = random.choice([-1, 1])
            entry = candles[idx]["o"] if candles[idx]["o"] > 0 else candles[idx]["c"]
            exit_p = candles[idx + hold]["c"]
            if entry <= 0:
                continue
            gross = direction * (exit_p / entry - 1) * 1e4
            sim += SIZE * (gross - COST) / 1e4
        sim_pnls.append(sim)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    return round(z, 2)


# ═══════════════════════════════════════════════════════════
# 1. WEEKEND EFFECT
# ═══════════════════════════════════════════════════════════

def test_weekend(data, features):
    print("\n" + "=" * 60)
    print("1. WEEKEND EFFECT")
    print("=" * 60)

    results = []
    for enter_day in [4, 5]:  # Friday=4, Saturday=5
        for exit_day in [0, 1]:  # Monday=0, Tuesday=1
            for direction in [1, -1]:
                for period in ["train", "test"]:
                    trades = []
                    for coin in TOKENS:
                        if coin not in data:
                            continue
                        candles = data[coin]
                        i = 0
                        while i < len(candles) - 12:
                            c = candles[i]
                            if period == "train" and c["t"] >= TRAIN_END:
                                i += 1; continue
                            if period == "test" and c["t"] < TEST_START:
                                i += 1; continue

                            dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
                            if dt.weekday() != enter_day or dt.hour != 0:
                                i += 1; continue

                            entry = c["o"]
                            if entry <= 0:
                                i += 1; continue

                            # Find exit day
                            for j in range(i + 1, min(i + 30, len(candles))):
                                dt_j = datetime.fromtimestamp(candles[j]["t"] / 1000, tz=timezone.utc)
                                if dt_j.weekday() == exit_day and dt_j.hour == 0:
                                    exit_p = candles[j]["o"]
                                    if exit_p > 0:
                                        gross = direction * (exit_p / entry - 1) * 1e4
                                        net = gross - COST
                                        trades.append({"coin": coin, "net": net,
                                                       "pnl": SIZE * net / 1e4, "t": c["t"]})
                                    break
                            i += 7 * 6  # skip to next week (7 days × 6 candles)

                    s = score(trades)
                    dir_label = "LONG" if direction == 1 else "SHORT"
                    day_names = {0: "Mon", 1: "Tue", 4: "Fri", 5: "Sat"}
                    results.append({"enter": day_names[enter_day], "exit": day_names[exit_day],
                                    "dir": dir_label, "period": period, **s})

    # Show results
    print(f"\n  {'Enter':>5} → {'Exit':>5} {'Dir':>5} | {'Train':>30} | {'Test':>30}")
    for enter in ["Fri", "Sat"]:
        for exit_d in ["Mon", "Tue"]:
            for d in ["LONG", "SHORT"]:
                tr = [r for r in results if r["enter"] == enter and r["exit"] == exit_d and r["dir"] == d and r["period"] == "train"]
                te = [r for r in results if r["enter"] == enter and r["exit"] == exit_d and r["dir"] == d and r["period"] == "test"]
                if tr and te:
                    tr, te = tr[0], te[0]
                    flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 else " "
                    print(f"  {enter:>5} → {exit_d:>5} {d:>5} | n={tr['n']:>4} avg={tr['avg']:>+5.1f} ${tr['pnl']:>6.0f} | n={te['n']:>4} avg={te['avg']:>+5.1f} ${te['pnl']:>6.0f} {flag}")

    passing = [r for r in results if r["period"] == "train" and r["avg"] > 0 and r["n"] >= 20]
    for p in passing:
        te = [r for r in results if r["enter"] == p["enter"] and r["exit"] == p["exit"]
              and r["dir"] == p["dir"] and r["period"] == "test"]
        if te and te[0]["avg"] > 0:
            print(f"\n  ✓ PASS: {p['enter']}→{p['exit']} {p['dir']}")
            return True
    print("\n  No weekend effect found.")
    return False


# ═══════════════════════════════════════════════════════════
# 2. FADE INDIVIDUAL PUMPS/DUMPS
# ═══════════════════════════════════════════════════════════

def test_fade_extremes(data, features):
    print("\n" + "=" * 60)
    print("2. FADE INDIVIDUAL EXTREME MOVES")
    print("=" * 60)

    results = []
    for ret_thresh in [1500, 2000, 3000, 4000]:  # bps threshold for "extreme"
        for hold in [6, 12, 18, 24]:  # 24h, 48h, 72h, 96h
            for period in ["train", "test"]:
                trades = []
                for coin in TOKENS:
                    if coin not in features or coin not in data:
                        continue
                    candles = data[coin]
                    feats = features[coin]
                    cooldown_until = 0

                    for f in feats:
                        if period == "train" and f["t"] >= TRAIN_END:
                            continue
                        if period == "test" and f["t"] < TEST_START:
                            continue
                        if f["t"] < cooldown_until:
                            continue

                        ret_24h = f.get("ret_6h", 0)  # 6 candles = 24h
                        idx = f["_idx"]

                        if abs(ret_24h) < ret_thresh:
                            continue
                        if idx + 1 + hold >= len(candles):
                            continue

                        # Fade: if up → short, if down → long
                        direction = -1 if ret_24h > 0 else 1
                        entry = candles[idx + 1]["o"]
                        exit_p = candles[idx + 1 + hold]["c"]
                        if entry <= 0:
                            continue

                        gross = direction * (exit_p / entry - 1) * 1e4
                        net = gross - COST
                        trades.append({"coin": coin, "net": net,
                                       "pnl": SIZE * net / 1e4, "t": f["t"],
                                       "ret_24h": ret_24h, "idx": idx})
                        cooldown_until = f["t"] + hold * 4 * 3600 * 1000

                s = score(trades)
                results.append({"thresh": ret_thresh, "hold": f"{hold * 4}h",
                                "period": period, **s})

    # Show
    print(f"\n  {'Thresh':>7} {'Hold':>5} | {'Train':>30} | {'Test':>30}")
    for thresh in [1500, 2000, 3000, 4000]:
        for hold_h in ["24h", "48h", "72h", "96h"]:
            tr = [r for r in results if r["thresh"] == thresh and r["hold"] == hold_h and r["period"] == "train"]
            te = [r for r in results if r["thresh"] == thresh and r["hold"] == hold_h and r["period"] == "test"]
            if tr and te:
                tr, te = tr[0], te[0]
                flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 and tr["n"] >= 10 else " "
                print(f"  {thresh:>7} {hold_h:>5} | n={tr['n']:>4} avg={tr['avg']:>+6.1f} ${tr['pnl']:>6.0f} | n={te['n']:>4} avg={te['avg']:>+6.1f} ${te['pnl']:>6.0f} {flag}")

    passing = [(r, [t for t in results if t["thresh"] == r["thresh"] and t["hold"] == r["hold"] and t["period"] == "test"])
               for r in results if r["period"] == "train" and r["avg"] > 0 and r["n"] >= 10]
    for tr, tes in passing:
        if tes and tes[0]["avg"] > 0:
            print(f"\n  ✓ PASS: thresh={tr['thresh']} hold={tr['hold']}")
            return True
    print("\n  No fade-extreme edge found.")
    return False


# ═══════════════════════════════════════════════════════════
# 3. DISPERSION EXTREMES
# ═══════════════════════════════════════════════════════════

def test_dispersion(data, features):
    print("\n" + "=" * 60)
    print("3. DISPERSION EXTREMES")
    print("=" * 60)

    # Compute cross-sectional dispersion at each timestamp
    by_ts = defaultdict(dict)
    for coin in TOKENS:
        if coin not in features:
            continue
        for f in features[coin]:
            by_ts[f["t"]][coin] = f

    dispersion_ts = []
    for ts in sorted(by_ts.keys()):
        rets = [by_ts[ts][c].get("ret_42h", 0) for c in by_ts[ts] if "ret_42h" in by_ts[ts][c]]
        if len(rets) < 10:
            continue
        disp = float(np.std(rets))
        avg_ret = float(np.mean(rets))
        dispersion_ts.append({"t": ts, "disp": disp, "avg_ret": avg_ret, "n_tokens": len(rets)})

    if len(dispersion_ts) < 200:
        print("  Not enough data")
        return False

    # Rolling stats on dispersion
    disps = np.array([d["disp"] for d in dispersion_ts])
    results = []

    for lookback in [42, 84]:  # 7d, 14d of 4h candles for rolling stats
        for disp_z_thresh in [1.0, 1.5, 2.0]:
            for hold in [12, 18, 24]:
                for signal_type in ["low_disp_long", "high_disp_fade"]:
                    for period in ["train", "test"]:
                        trades = []

                        for i in range(lookback, len(dispersion_ts)):
                            d = dispersion_ts[i]
                            if period == "train" and d["t"] >= TRAIN_END:
                                continue
                            if period == "test" and d["t"] < TEST_START:
                                continue

                            window = disps[i - lookback:i]
                            mean_d = float(np.mean(window))
                            std_d = float(np.std(window))
                            if std_d < 1:
                                continue
                            disp_z = (d["disp"] - mean_d) / std_d

                            direction = None
                            if signal_type == "low_disp_long" and disp_z < -disp_z_thresh:
                                # Low dispersion → breakout coming → long (momentum)
                                direction = 1 if d["avg_ret"] > 0 else -1
                            elif signal_type == "high_disp_fade" and disp_z > disp_z_thresh:
                                # High dispersion → revert → fade avg direction
                                direction = -1 if d["avg_ret"] > 0 else 1

                            if direction is None:
                                continue

                            # Trade basket (avg of all tokens)
                            for coin in list(by_ts[d["t"]].keys())[:6]:
                                if coin not in data:
                                    continue
                                f = by_ts[d["t"]][coin]
                                idx = f.get("_idx", 0)
                                candles = data[coin]
                                if idx + 1 + hold >= len(candles):
                                    continue
                                entry = candles[idx + 1]["o"]
                                exit_p = candles[idx + 1 + hold]["c"]
                                if entry <= 0:
                                    continue
                                gross = direction * (exit_p / entry - 1) * 1e4
                                net = gross - COST
                                trades.append({"coin": coin, "net": net,
                                               "pnl": (SIZE / 6) * net / 1e4, "t": d["t"]})

                        s = score(trades)
                        results.append({"lb": lookback * 4, "disp_z": disp_z_thresh,
                                        "hold": hold * 4, "type": signal_type,
                                        "period": period, **s})

    # Best
    train = [r for r in results if r["period"] == "train" and r["n"] >= 20]
    if train:
        train.sort(key=lambda r: r["pnl"], reverse=True)
        print(f"\n  Top 5 (train):")
        for r in train[:5]:
            te = [t for t in results if t["lb"] == r["lb"] and t["disp_z"] == r["disp_z"]
                  and t["hold"] == r["hold"] and t["type"] == r["type"] and t["period"] == "test"]
            flag = ""
            if te and te[0]["avg"] > 0:
                flag = f" → test ✓ avg={te[0]['avg']:+.1f}"
            else:
                flag = f" → test ✗" + (f" avg={te[0]['avg']:+.1f}" if te else "")
            print(f"  {r['type']:>18} lb={r['lb']}h z>{r['disp_z']} hold={r['hold']}h: n={r['n']} ${r['pnl']:.0f} avg={r['avg']:+.1f}{flag}")

    has_pass = any(
        r["period"] == "train" and r["avg"] > 0 and r["n"] >= 20
        and any(t["period"] == "test" and t["avg"] > 0 and t["lb"] == r["lb"]
                and t["disp_z"] == r["disp_z"] and t["hold"] == r["hold"]
                and t["type"] == r["type"] for t in results)
        for r in results
    )
    if has_pass:
        print("\n  ✓ Some configs pass train+test")
    else:
        print("\n  No dispersion edge found.")
    return has_pass


# ═══════════════════════════════════════════════════════════
# 4. VOLUME EXHAUSTION
# ═══════════════════════════════════════════════════════════

def test_volume_exhaustion(data, features):
    print("\n" + "=" * 60)
    print("4. VOLUME EXHAUSTION")
    print("=" * 60)

    results = []
    for vol_drop_thresh in [0.3, 0.5, 0.7]:  # vol ratio (current/avg) < this = exhaustion
        for ret_thresh in [500, 1000, 2000]:  # min move before exhaustion matters
            for hold in [6, 12, 18]:
                for period in ["train", "test"]:
                    trades = []
                    for coin in TOKENS:
                        if coin not in features or coin not in data:
                            continue
                        feats = features[coin]
                        candles = data[coin]
                        cooldown = 0

                        for f in feats:
                            if period == "train" and f["t"] >= TRAIN_END:
                                continue
                            if period == "test" and f["t"] < TEST_START:
                                continue
                            if f["t"] < cooldown:
                                continue

                            vol_ratio = f.get("vol_ratio", 1.0)
                            ret_7d = f.get("ret_42h", 0)
                            idx = f.get("_idx", 0)

                            # Volume dropped AND there was a move
                            if vol_ratio >= vol_drop_thresh or abs(ret_7d) < ret_thresh:
                                continue
                            if idx + 1 + hold >= len(candles):
                                continue

                            # Fade the move
                            direction = -1 if ret_7d > 0 else 1
                            entry = candles[idx + 1]["o"]
                            exit_p = candles[idx + 1 + hold]["c"]
                            if entry <= 0:
                                continue

                            gross = direction * (exit_p / entry - 1) * 1e4
                            net = gross - COST
                            trades.append({"coin": coin, "net": net,
                                           "pnl": SIZE * net / 1e4, "t": f["t"]})
                            cooldown = f["t"] + hold * 4 * 3600 * 1000

                    s = score(trades)
                    results.append({"vol_drop": vol_drop_thresh, "ret": ret_thresh,
                                    "hold": hold * 4, "period": period, **s})

    print(f"\n  {'VolDrop':>8} {'Ret':>5} {'Hold':>5} | {'Train':>25} | {'Test':>25}")
    for vd in [0.3, 0.5, 0.7]:
        for ret in [500, 1000, 2000]:
            for h in [24, 48, 72]:
                tr = [r for r in results if r["vol_drop"] == vd and r["ret"] == ret and r["hold"] == h and r["period"] == "train"]
                te = [r for r in results if r["vol_drop"] == vd and r["ret"] == ret and r["hold"] == h and r["period"] == "test"]
                if tr and te:
                    tr, te = tr[0], te[0]
                    flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 and tr["n"] >= 10 else " "
                    print(f"  {vd:>8} {ret:>5} {h:>4}h | n={tr['n']:>4} avg={tr['avg']:>+5.1f} ${tr['pnl']:>5.0f} | n={te['n']:>4} avg={te['avg']:>+5.1f} ${te['pnl']:>5.0f} {flag}")

    has_pass = any(
        r["period"] == "train" and r["avg"] > 0 and r["n"] >= 10
        and any(t["period"] == "test" and t["avg"] > 0 and t["vol_drop"] == r["vol_drop"]
                and t["ret"] == r["ret"] and t["hold"] == r["hold"]
                for t in results)
        for r in results
    )
    if has_pass:
        print("\n  ✓ Volume exhaustion has edge")
    else:
        print("\n  No volume exhaustion edge.")
    return has_pass


# ═══════════════════════════════════════════════════════════
# 5. CROSS-TOKEN MOMENTUM
# ═══════════════════════════════════════════════════════════

def test_cross_momentum(data, features):
    print("\n" + "=" * 60)
    print("5. CROSS-TOKEN MOMENTUM (long top N, short bottom N)")
    print("=" * 60)

    by_ts = defaultdict(dict)
    for coin in TOKENS:
        if coin not in features:
            continue
        for f in features[coin]:
            by_ts[f["t"]][coin] = f

    results = []
    for n_top in [3, 5, 7]:
        for lookback_key in ["ret_42h", "ret_84h"]:
            lb_label = "7d" if "42" in lookback_key else "14d"
            for hold in [12, 18, 24]:
                for period in ["train", "test"]:
                    trades = []

                    for ts in sorted(by_ts.keys()):
                        if period == "train" and ts >= TRAIN_END:
                            continue
                        if period == "test" and ts < TEST_START:
                            continue

                        coins_ret = []
                        for coin, f in by_ts[ts].items():
                            if lookback_key in f and coin in data:
                                coins_ret.append((coin, f[lookback_key], f.get("_idx", 0)))

                        if len(coins_ret) < n_top * 2 + 4:
                            continue

                        coins_ret.sort(key=lambda x: x[1], reverse=True)
                        top = coins_ret[:n_top]
                        bottom = coins_ret[-n_top:]

                        for coin, ret, idx in top:
                            candles = data[coin]
                            if idx + 1 + hold >= len(candles):
                                continue
                            entry = candles[idx + 1]["o"]
                            exit_p = candles[idx + 1 + hold]["c"]
                            if entry <= 0:
                                continue
                            gross = 1 * (exit_p / entry - 1) * 1e4  # LONG top
                            net = gross - COST
                            trades.append({"coin": coin, "net": net, "side": "long_top",
                                           "pnl": (SIZE / n_top) * net / 1e4, "t": ts})

                        for coin, ret, idx in bottom:
                            candles = data[coin]
                            if idx + 1 + hold >= len(candles):
                                continue
                            entry = candles[idx + 1]["o"]
                            exit_p = candles[idx + 1 + hold]["c"]
                            if entry <= 0:
                                continue
                            gross = -1 * (exit_p / entry - 1) * 1e4  # SHORT bottom
                            net = gross - COST
                            trades.append({"coin": coin, "net": net, "side": "short_bot",
                                           "pnl": (SIZE / n_top) * net / 1e4, "t": ts})

                    s = score(trades)
                    results.append({"n_top": n_top, "lb": lb_label, "hold": hold * 4,
                                    "period": period, **s})

    print(f"\n  {'Top':>4} {'LB':>4} {'Hold':>5} | {'Train':>25} | {'Test':>25}")
    for nt in [3, 5, 7]:
        for lb in ["7d", "14d"]:
            for h in [48, 72, 96]:
                tr = [r for r in results if r["n_top"] == nt and r["lb"] == lb and r["hold"] == h and r["period"] == "train"]
                te = [r for r in results if r["n_top"] == nt and r["lb"] == lb and r["hold"] == h and r["period"] == "test"]
                if tr and te:
                    tr, te = tr[0], te[0]
                    flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 and tr["n"] >= 20 else " "
                    print(f"  {nt:>4} {lb:>4} {h:>4}h | n={tr['n']:>5} avg={tr['avg']:>+5.1f} ${tr['pnl']:>6.0f} | n={te['n']:>5} avg={te['avg']:>+5.1f} ${te['pnl']:>6.0f} {flag}")

    has_pass = any(
        r["period"] == "train" and r["avg"] > 0 and r["n"] >= 20
        and any(t["period"] == "test" and t["avg"] > 0 and t["n_top"] == r["n_top"]
                and t["lb"] == r["lb"] and t["hold"] == r["hold"]
                for t in results)
        for r in results
    )
    if has_pass:
        print("\n  ✓ Cross-momentum has edge")
    else:
        print("\n  No cross-momentum edge.")
    return has_pass


# ═══════════════════════════════════════════════════════════
# 6. MONDAY REVENGE
# ═══════════════════════════════════════════════════════════

def test_monday_revenge(data, features):
    print("\n" + "=" * 60)
    print("6. MONDAY REVENGE (buy Sunday dip, sell Tue/Wed)")
    print("=" * 60)

    results = []
    for direction in [1, -1]:  # 1=long (buy dip), -1=short (sell rip)
        for enter_hour in [0, 16]:  # Sunday midnight or Sunday afternoon
            for exit_day_offset in [1, 2, 3]:  # Mon, Tue, Wed
                for period in ["train", "test"]:
                    trades = []
                    for coin in TOKENS:
                        if coin not in data:
                            continue
                        candles = data[coin]
                        i = 0
                        while i < len(candles) - 24:
                            c = candles[i]
                            if period == "train" and c["t"] >= TRAIN_END:
                                i += 1; continue
                            if period == "test" and c["t"] < TEST_START:
                                i += 1; continue

                            dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
                            if dt.weekday() != 6 or dt.hour != enter_hour:  # Sunday
                                i += 1; continue

                            entry = c["o"]
                            if entry <= 0:
                                i += 1; continue

                            exit_idx = i + exit_day_offset * 6  # days × 6 candles
                            if exit_idx >= len(candles):
                                break
                            exit_p = candles[exit_idx]["o"]
                            if exit_p > 0:
                                gross = direction * (exit_p / entry - 1) * 1e4
                                net = gross - COST
                                trades.append({"coin": coin, "net": net,
                                               "pnl": SIZE * net / 1e4, "t": c["t"]})
                            i += 7 * 6

                    s = score(trades)
                    dir_label = "LONG" if direction == 1 else "SHORT"
                    results.append({"dir": dir_label, "enter_h": enter_hour,
                                    "exit_offset": exit_day_offset, "period": period, **s})

    print(f"\n  {'Dir':>5} {'Enter':>6} {'Exit':>6} | {'Train':>25} | {'Test':>25}")
    for d in ["LONG", "SHORT"]:
        for eh in [0, 16]:
            for eo in [1, 2, 3]:
                exit_label = {1: "Mon", 2: "Tue", 3: "Wed"}[eo]
                tr = [r for r in results if r["dir"] == d and r["enter_h"] == eh and r["exit_offset"] == eo and r["period"] == "train"]
                te = [r for r in results if r["dir"] == d and r["enter_h"] == eh and r["exit_offset"] == eo and r["period"] == "test"]
                if tr and te:
                    tr, te = tr[0], te[0]
                    flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 and tr["n"] >= 20 else " "
                    print(f"  {d:>5} Sun{eh:02d} {exit_label:>5} | n={tr['n']:>4} avg={tr['avg']:>+5.1f} ${tr['pnl']:>6.0f} | n={te['n']:>4} avg={te['avg']:>+5.1f} ${te['pnl']:>6.0f} {flag}")

    has_pass = any(
        r["period"] == "train" and r["avg"] > 0 and r["n"] >= 20
        and any(t["period"] == "test" and t["avg"] > 0 and t["dir"] == r["dir"]
                and t["enter_h"] == r["enter_h"] and t["exit_offset"] == r["exit_offset"]
                for t in results)
        for r in results
    )
    if has_pass:
        print("\n  ✓ Monday revenge has edge")
    else:
        print("\n  No Monday revenge edge.")
    return has_pass


# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("WILD IDEAS BACKTEST — 6 unconventional strategies")
    print("=" * 60)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"  {len(data)} tokens")

    print("Building features...")
    features = build_features(data)
    print(f"  {sum(len(v) for v in features.values())} feature rows")

    results = {}
    results["weekend"] = test_weekend(data, features)
    results["fade_extreme"] = test_fade_extremes(data, features)
    results["dispersion"] = test_dispersion(data, features)
    results["vol_exhaustion"] = test_volume_exhaustion(data, features)
    results["cross_momentum"] = test_cross_momentum(data, features)
    results["monday_revenge"] = test_monday_revenge(data, features)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        flag = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {name:>20}: {flag}")

    print("\nDone.")


if __name__ == "__main__":
    main()
