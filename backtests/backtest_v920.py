"""Backtest v9.2.0 — Full portfolio with all 5 signals + DXY filter.

Tests:
1. Combined S1+S2+S4+S5+S6 with DXY filter (max 6 pos)
2. Same with max 8 pos
3. Same with priority eviction (S6 can kick lower signals)
4. Slot utilization analysis (how often is the bot idle vs full?)

Usage:
    python3 -m analysis.backtest_v920
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE, TRAIN_END, TEST_START,
)
from backtests.backtest_sector import (
    compute_sector_features, TOKEN_SECTOR, SECTORS,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67, "S6": 8.04}
SIZE_PCT = 0.15


def strat_size(name, capital):
    z = STRAT_Z.get(name, 3.0)
    w = max(0.5, min(2.0, z / 4.0))
    return max(10, capital * SIZE_PCT * w)


def load_dxy():
    """Load cached DXY daily data."""
    path = os.path.join(DATA_DIR, "macro_DXY.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        daily = json.load(f)
    # Build {timestamp: 7d_return_bps}
    closes = [d["c"] for d in daily]
    result = {}
    for i in range(5, len(daily)):
        ret = (closes[i] / closes[i-5] - 1) * 1e4 if closes[i-5] > 0 else 0
        result[daily[i]["t"]] = ret
    return result


def backtest_full(features, data, sector_features, dxy_data, config):
    """Full v9.2.0 backtest.

    config:
        max_pos: max positions
        max_dir: max same direction
        eviction: bool, allow high-z signals to kick low-z positions
        compounding: bool
        capital: starting capital
    """
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    eviction = config.get("eviction", False)
    compounding = config.get("compounding", True)
    start_capital = config.get("capital", 1000)

    coins = [c for c in TOKENS if c in features and c in data]

    # Build timeline
    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i
    sorted_ts = sorted(all_ts)

    # Feature lookups
    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    # DXY: find closest daily for each timestamp
    dxy_ts = sorted(dxy_data.keys()) if dxy_data else []

    def get_dxy(ts):
        if not dxy_ts:
            return 0
        # Binary search for closest
        lo, hi = 0, len(dxy_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if dxy_ts[mid] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return dxy_data.get(dxy_ts[lo], 0) if abs(dxy_ts[lo] - ts) < 5 * 86400 * 1000 else 0

    # BTC features
    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles]) if btc_candles else np.array([])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    def btc_30d(ts):
        if ts not in btc_by_ts:
            return 0
        i = btc_by_ts[ts]
        if i >= 180 and btc_closes[i-180] > 0:
            return (btc_closes[i] / btc_closes[i-180] - 1) * 1e4
        return 0

    # Alt index
    def alt_index(ts):
        available = feat_by_ts.get(ts, {})
        rets = [f.get("ret_42h", 0) for f in available.values() if "ret_42h" in f]
        return float(np.mean(rets)) if rets else 0

    # State
    positions = {}  # coin → {dir, entry, idx, strat, z, size, entry_t, hold}
    trades = []
    cooldown = {}
    capital = start_capital

    # Stats
    slot_usage = []  # (ts, n_positions)
    signals_blocked = 0
    signals_total = 0

    for ts in sorted_ts:
        # ── Exits ──
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

            # Catastrophe stop -25%
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < -2500:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - 2500 / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < -2500:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + 2500 / 1e4)

            if held >= pos["hold"]:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST_BPS
                pnl = pos["size"] * net / 1e4
                capital += pnl
                trades.append({
                    "coin": coin, "dir": pos["dir"],
                    "strat": pos["strat"], "pnl": round(pnl, 2),
                    "net": round(net, 1), "hold": held,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Collect signals ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)

        dxy = get_dxy(ts)
        btc30 = btc_30d(ts)
        alt_idx = alt_index(ts)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue

            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            # S1: btc_30d > 2000 → LONG
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})

            # S2: alt_index < -1000 → LONG
            if alt_idx < -1000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                    "z": STRAT_Z["S2"], "hold": 18, "strength": abs(alt_idx)})

            # S4: vol contraction + DXY rising
            if (f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200
                    and dxy > 100):
                candidates.append({"coin": coin, "dir": -1, "strat": "S4",
                    "z": STRAT_Z["S4"], "hold": 18, "strength": (1-f["vol_ratio"])*1000})

            # S5: sector divergence
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

            # S6: liquidation cascade
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                c = data[coin][ci]
                total_range = c["h"] - c["l"]
                if total_range > 0 and c["c"] > 0:
                    range_bps = total_range / c["c"] * 1e4
                    body = abs(c["c"] - c["o"])
                    wick_ratio = (total_range - body) / total_range
                    # Average range
                    avg_range = float(np.mean([
                        (data[coin][j]["h"] - data[coin][j]["l"]) / data[coin][j]["c"] * 1e4
                        for j in range(max(0, ci-42), ci)
                        if data[coin][j]["c"] > 0
                    ])) or 1

                    if range_bps >= avg_range * 4.0 and wick_ratio >= 0.3:
                        direction = 1 if c["c"] < c["o"] else -1
                        candidates.append({"coin": coin, "dir": direction, "strat": "S6",
                            "z": STRAT_Z["S6"], "hold": 12, "strength": range_bps})

        signals_total += len(candidates)

        # Sort by z then strength
        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)

        # Eviction: if high-z signal and full, kick lowest-z position
        if eviction and candidates and len(positions) >= max_pos:
            best_cand = candidates[0]
            if best_cand["z"] > 5.0:  # Only S1 and S6 can evict
                worst_pos = min(positions.items(),
                               key=lambda kv: STRAT_Z.get(kv[1]["strat"], 0))
                worst_coin, worst_p = worst_pos
                if STRAT_Z.get(worst_p["strat"], 0) < best_cand["z"] - 2:
                    # Evict
                    ci_w = coin_by_ts.get(worst_coin, {}).get(ts)
                    if ci_w is not None:
                        exit_price = data[worst_coin][ci_w]["c"]
                        if exit_price > 0:
                            gross = worst_p["dir"] * (exit_price / worst_p["entry"] - 1) * 1e4
                            net = gross - COST_BPS
                            pnl = worst_p["size"] * net / 1e4
                            capital += pnl
                            trades.append({
                                "coin": worst_coin, "dir": worst_p["dir"],
                                "strat": worst_p["strat"], "pnl": round(pnl, 2),
                                "net": round(net, 1), "hold": 0,
                                "entry_t": worst_p["entry_t"], "exit_t": ts,
                                "reason": "evicted", "size": worst_p["size"],
                            })
                            del positions[worst_coin]

        # Fill slots
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)

            if len(positions) >= max_pos:
                signals_blocked += 1
                continue

            n_long = sum(1 for p in positions.values() if p["dir"] == 1)
            n_short = sum(1 for p in positions.values() if p["dir"] == -1)
            if cand["dir"] == 1 and n_long >= max_dir:
                continue
            if cand["dir"] == -1 and n_short >= max_dir:
                continue

            idx = f["_idx"] if (f := feat_by_ts.get(ts, {}).get(coin)) else None
            if idx is None or idx + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx + 1]["o"]
            if entry <= 0:
                continue

            size = strat_size(cand["strat"], capital) if compounding else POSITION_SIZE

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx + 1, "entry_t": data[coin][idx + 1]["t"],
                "strat": cand["strat"], "z": cand["z"],
                "hold": cand["hold"], "size": size,
            }

        slot_usage.append((ts, len(positions)))

    # ── Analysis ──
    n = len(trades)
    if n == 0:
        return {"pnl": 0, "n": 0, "label": config.get("label", "")}

    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t["strat"]].append(t)

    by_month = defaultdict(float)
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        by_month[dt.strftime("%Y-%m")] += t["pnl"]

    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m] > 0)

    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    # Slot utilization
    avg_slots = float(np.mean([s for _, s in slot_usage]))
    full_pct = sum(1 for _, s in slot_usage if s >= max_pos) / len(slot_usage) * 100
    empty_pct = sum(1 for _, s in slot_usage if s == 0) / len(slot_usage) * 100

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for m in months:
        cum += by_month[m]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    label = config.get("label", "")
    print(f"\n  {label}")
    print(f"    Capital: ${start_capital} → ${capital:.0f} (+${pnl:.0f})")
    print(f"    Trades: {n} | Win: {wins/n*100:.0f}% | Avg: {avg:+.1f} bps")
    print(f"    Months: {winning}/{len(months)} winning | Max DD: ${max_dd:.0f}")
    print(f"    Train: ${train_pnl:+.0f} | Test: ${test_pnl:+.0f}")
    print(f"    Slots: avg {avg_slots:.1f}/{max_pos} | Full {full_pct:.0f}% | Empty {empty_pct:.0f}%")
    print(f"    Blocked signals: {signals_blocked}/{signals_total} ({signals_blocked/max(1,signals_total)*100:.0f}%)")

    print(f"\n    By strategy:")
    for sname in sorted(by_strat):
        st = by_strat[sname]
        sp = sum(t["pnl"] for t in st)
        sa = float(np.mean([t["net"] for t in st]))
        sw = sum(1 for t in st if t["net"] > 0) / len(st) * 100
        print(f"      {sname}: ${sp:>+7.0f} ({len(st):>3}t, avg={sa:>+5.1f}bp, win={sw:.0f}%)")

    # Monthly
    print(f"\n    Monthly:")
    cum = start_capital
    for m in months:
        cum += by_month[m]
        marker = "✓" if by_month[m] > 0 else "✗"
        print(f"      {m}: ${by_month[m]:>+7.0f} bal=${cum:>+.0f} {marker}")

    return {
        "pnl": pnl, "n": n, "capital": capital,
        "winning_months": winning, "total_months": len(months),
        "max_dd": max_dd, "avg_slots": avg_slots,
        "full_pct": full_pct, "empty_pct": empty_pct,
        "blocked": signals_blocked,
        "train": train_pnl, "test": test_pnl,
        "trades": trades,
    }


def main():
    print("=" * 70)
    print("  BACKTEST v9.2.0 — Full Portfolio")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    features = build_features(data)
    print(f"Built features")

    sector_features = compute_sector_features(features, data)
    print(f"Built sector features")

    dxy_data = load_dxy()
    print(f"DXY data: {len(dxy_data)} days")

    configs = [
        {"label": "A: 6 pos, no eviction", "max_pos": 6, "max_dir": 4, "eviction": False},
        {"label": "B: 8 pos, no eviction", "max_pos": 8, "max_dir": 5, "eviction": False},
        {"label": "C: 6 pos + eviction", "max_pos": 6, "max_dir": 4, "eviction": True},
        {"label": "D: 8 pos + eviction", "max_pos": 8, "max_dir": 5, "eviction": True},
        {"label": "E: 10 pos, no eviction", "max_pos": 10, "max_dir": 6, "eviction": False},
    ]

    results = []
    for config in configs:
        r = backtest_full(features, data, sector_features, dxy_data, config)
        results.append((config["label"], r))

    # Summary
    print(f"\n\n{'█'*70}")
    print(f"  SUMMARY")
    print(f"{'█'*70}")
    print(f"\n  {'Config':<30} {'P&L':>8} {'N':>5} {'W.Mo':>5} {'DD':>7} {'Slots':>6} {'Full%':>6} {'Block%':>7}")
    print(f"  {'-'*80}")
    for label, r in results:
        if r["n"] == 0:
            continue
        print(f"  {label:<30} ${r['pnl']:>+7.0f} {r['n']:>4} "
              f"{r['winning_months']}/{r['total_months']} ${r['max_dd']:>+6.0f} "
              f"{r['avg_slots']:>5.1f} {r['full_pct']:>5.0f}% {r['blocked']/max(1,r['n'])*100:>6.0f}%")


if __name__ == "__main__":
    main()
