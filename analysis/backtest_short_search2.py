"""Short Signal Search v2 — Fresh scan for SHORT signals.

Previous search (378 variants) found nothing z>2.0. This search adds:
- BTC negative momentum (inverse of S1)
- S9-like fade but at lower thresholds (15%, 12%)
- Alt overextension (ret_7d > +X AND vol expanding)
- Sector leader fade (token leads sector by >15%)
- Multi-day pump exhaustion (ret_7d high + ret_24h reversing)

Uses the corrected backtest framework (ret_6h = 24h).
Portfolio context: slot reservation 2 macro / 3 token, compounding.

Usage:
    python3 -m analysis.backtest_short_search2
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from analysis.backtest_sector import compute_sector_features, TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")


def monte_carlo(trades, n_sims=1000):
    """Monte Carlo z-score: compare actual mean return vs random timing."""
    if len(trades) < 10:
        return 0.0
    actual_mean = np.mean([t["net"] for t in trades])
    all_nets = [t["net"] for t in trades]
    random_means = []
    for _ in range(n_sims):
        random.shuffle(all_nets)
        random_means.append(np.mean(all_nets[:len(trades)]))
    std = np.std(random_means)
    return (actual_mean - np.mean(random_means)) / std if std > 0 else 0


def backtest_short_signal(features, data, config):
    """Standalone SHORT signal backtest (no portfolio, just this signal)."""
    leverage = config.get("leverage", 2.0)
    cost = config.get("cost", COST_BPS)
    hold = config.get("hold", 12)  # candles
    stop_bps = config.get("stop_bps", -2500)
    max_pos = config.get("max_pos", 3)
    size = config.get("size", 150)  # fixed size for standalone

    effective_cost = (cost + (leverage - 1) * 2) * leverage
    signal_fn = config["signal_fn"]

    coins = [c for c in TOKENS if c in features and c in data]

    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted(all_ts):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]:
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

            effective_stop = stop_bps / leverage
            # SHORT: high is worst
            worst = -(candle["h"] / pos["entry"] - 1) * 1e4
            if worst < effective_stop:
                exit_reason = "stop"
                exit_price = pos["entry"] * (1 - effective_stop / 1e4)

            if held >= hold:
                exit_reason = "timeout"

            if exit_reason:
                gross = -1 * (exit_price / pos["entry"] - 1) * 1e4 * leverage  # SHORT
                net = gross - effective_cost
                trades.append({"net": round(net, 1), "pnl": round(size * net / 1e4, 2),
                               "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # Entries
        if len(positions) >= max_pos:
            continue

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            strength = signal_fn(f, ts, feat_by_ts)
            if strength > 0:
                candidates.append({"coin": coin, "strength": strength, "f": f})

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        for cand in candidates[:max_pos - len(positions)]:
            coin = cand["coin"]
            f = cand["f"]
            idx_f = f["_idx"]
            if idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue
            positions[coin] = {"entry": entry, "idx": idx_f + 1,
                               "entry_t": data[coin][idx_f + 1]["t"], "coin": coin}

    if not trades:
        return None

    n = len(trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    return {
        "n": n, "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "pnl": round(total_pnl, 0),
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "trades": trades,
    }


def main():
    print("=" * 100)
    print("  SHORT SIGNAL SEARCH v2 — Fresh ideas with corrected framework")
    print("=" * 100)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)

    # BTC features for macro signals
    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
    def btc_ret(ts, lookback):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i] / btc_closes[i-lookback] - 1) * 1e4 if i >= lookback and btc_closes[i-lookback] > 0 else 0

    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "cost": COST_BPS, "max_pos": 3, "size": 150, "stop_bps": -2500}

    hdr = f"  {'Signal':<55} {'N':>5} {'Avg':>7} {'W%':>5} {'P&L':>8} {'Trn':>7} {'Tst':>7} {'z':>6}"
    sep = f"  {'-'*100}"

    # ═══════════════════════════════════════════════════════════
    print("TEST 1: BTC NEGATIVE MOMENTUM → SHORT ALTS (inverse of S1)")
    print(hdr); print(sep)
    for btc_thresh in [-1000, -1500, -2000, -2500]:
        for hold in [6, 12, 18]:
            def sig(f, ts, fbt, bt=btc_thresh):
                b30 = btc_ret(ts, 180)
                return abs(b30) if b30 < bt else 0
            r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
            if r and r["n"] >= 10:
                z = monte_carlo(r["trades"])
                v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                print(f"  BTC30d < {btc_thresh:+5d} hold={hold*4:>3}h{'':<27} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 2: FADE PUMP (lower S9 threshold, SHORT only)")
    print(hdr); print(sep)
    for thresh in [1000, 1200, 1500, 1800, 2000]:
        for hold in [6, 12, 18]:
            def sig(f, ts, fbt, th=thresh):
                ret = f.get("ret_6h", 0)
                return abs(ret) if ret > th else 0  # only SHORT pumps
            r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
            if r and r["n"] >= 10:
                z = monte_carlo(r["trades"])
                v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                print(f"  Fade pump > {thresh:>5}bps hold={hold*4:>3}h{'':<28} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 3: ALT OVEREXTENSION (ret_7d high + vol expanding)")
    print(hdr); print(sep)
    for ret_thresh in [1500, 2000, 3000]:
        for vol_thresh in [1.0, 1.3, 1.5]:
            for hold in [6, 12, 18]:
                def sig(f, ts, fbt, _rt=ret_thresh, _vt=vol_thresh):
                    ret = f.get("ret_42h", 0)
                    vr = f.get("vol_ratio", 0)
                    return abs(ret) if ret > _rt and vr > _vt else 0
                r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
                if r and r["n"] >= 10:
                    z = monte_carlo(r["trades"])
                    v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                    print(f"  ret7d>{ret_thresh:>5} vr>{vol_thresh:.1f} hold={hold*4:>3}h{'':<26} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 4: SECTOR LEADER FADE (token leads sector by >X%)")
    print(hdr); print(sep)
    for div_thresh in [1000, 1500, 2000]:
        for vol_z_min in [0.5, 1.0, 1.5]:
            for hold in [6, 12, 18]:
                def sig(f, ts, fbt, dt=div_thresh, vz=vol_z_min):
                    s = sf.get((ts, f.get("_coin", "")))
                    if not s:
                        # Try to find sector features for this coin
                        return 0
                    if s["divergence"] > dt and s.get("vol_z", 0) > vz:
                        return abs(s["divergence"])
                    return 0
                # Need to inject coin name into features
                r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
                if r and r["n"] >= 10:
                    z = monte_carlo(r["trades"])
                    v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                    print(f"  SectorLead>{dt:>5} vz>{vz:.1f} hold={hold*4:>3}h{'':<22} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 5: PUMP EXHAUSTION (ret_7d up + ret_24h reversing)")
    print(hdr); print(sep)
    for ret7_thresh in [1500, 2000, 3000]:
        for ret24_thresh in [-100, -200, -500]:
            for hold in [6, 12, 18]:
                def sig(f, ts, fbt, _r7=ret7_thresh, _r24=ret24_thresh):
                    ret7 = f.get("ret_42h", 0)
                    ret24 = f.get("ret_6h", 0)
                    return abs(ret7) if ret7 > _r7 and ret24 < _r24 else 0
                r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
                if r and r["n"] >= 10:
                    z = monte_carlo(r["trades"])
                    v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                    print(f"  r7d>{ret7_thresh:>5} r24h<{ret24_thresh:>+5} hold={hold*4:>3}h{'':<24} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")

    # ═══════════════════════════════════════════════════════════
    print(f"\nTEST 6: BTC WEAK + ALT STILL UP (delayed contagion)")
    print(hdr); print(sep)
    for btc_thresh in [-300, -500, -1000]:
        for alt_thresh in [500, 1000, 1500]:
            for hold in [6, 12, 18]:
                def sig(f, ts, fbt, _bt=btc_thresh, _at=alt_thresh):
                    b7 = btc_ret(ts, 42)
                    ret7 = f.get("ret_42h", 0)
                    return abs(ret7) if b7 < _bt and ret7 > _at else 0
                r = backtest_short_signal(features, data, {**base, "hold": hold, "signal_fn": sig})
                if r and r["n"] >= 10:
                    z = monte_carlo(r["trades"])
                    v = "✓" if r["train"] > 0 and r["test"] > 0 else ""
                    print(f"  BTC7d<{btc_thresh:>+5} alt7d>{alt_thresh:>+5} hold={hold*4:>3}h{'':<22} {r['n']:>5} {r['avg']:>+6.1f} {r['win']:>4}% ${r['pnl']:>+7} ${r['train']:>+6} ${r['test']:>+6} {z:>+5.2f} {v}")


if __name__ == "__main__":
    main()
