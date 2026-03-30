"""Signal Boost Backtest — 5 targeted improvements to existing signals.

Tests:
  1. S2 + BTC filter: only fire S2 when BTC 7d > -200 bps (not in freefall)
  2. S9 lower threshold: 1500 bps (±15%) instead of 2000 (±20%)
  3. S10 wider squeeze window: 4 and 5 candles instead of 3
  4. S2 early exit on alt recovery: exit when alt_index > -300 bps
  5. S5 size boost when divergence > 1500 bps

Each test runs the full 7-signal portfolio with one change at a time.
Baseline = current production config (slot reservation 2 macro / 3 token).

Usage:
    python3 -m analysis.backtest_signal_boost
"""

from __future__ import annotations

import json, os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from analysis.backtest_sector import compute_sector_features, TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}

# S10 squeeze params
S10_BREAKOUT_PCT = 0.5
S10_REINT_CANDLES = 2


def load_dxy():
    path = os.path.join(DATA_DIR, "macro_DXY.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        daily = json.load(f)
    closes = [d["c"] for d in daily]
    result = {}
    for i in range(5, len(daily)):
        result[daily[i]["t"]] = (closes[i] / closes[i-5] - 1) * 1e4 if closes[i-5] > 0 else 0
    return result


def detect_squeeze(candles, idx, vol_ratio, squeeze_window=3):
    if vol_ratio > 0.9:
        return None
    if idx < squeeze_window + S10_REINT_CANDLES + 2:
        return None
    for bo_offset in range(1, S10_REINT_CANDLES + 1):
        bo_idx = idx - bo_offset
        sq_start = bo_idx - squeeze_window
        if sq_start < 0:
            continue
        sq_candles = candles[sq_start:sq_start + squeeze_window]
        range_high = max(c["h"] for c in sq_candles)
        range_low = min(c["l"] for c in sq_candles)
        range_size = range_high - range_low
        if range_size <= 0 or range_low <= 0:
            continue
        bo = candles[bo_idx]
        threshold = range_size * S10_BREAKOUT_PCT
        bo_above = bo["h"] > range_high + threshold
        bo_below = bo["l"] < range_low - threshold
        if not bo_above and not bo_below:
            continue
        if bo_above and bo_below:
            continue
        bo_dir = 1 if bo_above else -1
        ri_end = min(bo_idx + 1 + S10_REINT_CANDLES, idx + 1)
        for ri in range(bo_idx + 1, ri_end):
            if range_low <= candles[ri]["c"] <= range_high:
                return -bo_dir
    return None


def backtest(features, data, sector_features, dxy_data, config):
    leverage = config.get("leverage", 2.0)
    size_pct = config.get("size_pct", 0.12)
    size_bonus = config.get("size_bonus", 0.03)
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    max_macro = config.get("max_macro", 2)
    max_token = config.get("max_token", 3)
    max_sector = config.get("max_sector", 2)
    start_capital = config.get("capital", 1000)
    cost = config.get("cost", COST_BPS)
    stop_default = -2500
    stop_s8 = -1500

    # Test-specific params
    s2_btc_filter = config.get("s2_btc_filter", None)       # Test 1: btc_7d threshold
    s9_threshold = config.get("s9_threshold", 2000)          # Test 2: S9 ret threshold
    s10_squeeze_window = config.get("s10_squeeze_window", 3) # Test 3: squeeze window
    s2_early_exit = config.get("s2_early_exit", None)        # Test 4: alt_index recovery threshold
    s5_boost_div = config.get("s5_boost_div", None)          # Test 5: divergence threshold for size boost
    s5_boost_mult = config.get("s5_boost_mult", 1.5)         # Test 5: size multiplier

    effective_cost = (cost + (leverage - 1) * 2) * leverage
    macro_strats = {"S1", "S2", "S4"}

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

    dxy_ts = sorted(dxy_data.keys()) if dxy_data else []
    def get_dxy(ts):
        if not dxy_ts: return 0
        lo, hi = 0, len(dxy_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if dxy_ts[mid] <= ts: lo = mid
            else: hi = mid - 1
        return dxy_data.get(dxy_ts[lo], 0) if abs(dxy_ts[lo] - ts) < 5 * 86400 * 1000 else 0

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
    def btc_ret(ts, lookback):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i] / btc_closes[i-lookback] - 1) * 1e4 if i >= lookback and btc_closes[i-lookback] > 0 else 0

    def alt_index(ts):
        available = feat_by_ts.get(ts, {})
        rets = [f.get("ret_42h", 0) for f in available.values() if "ret_42h" in f]
        return float(np.mean(rets)) if rets else 0

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0

    for ts in sorted(all_ts):
        # ── EXITS ──
        alt_idx_now = alt_index(ts) if s2_early_exit else 0

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

            stop = stop_s8 if pos["strat"] == "S8" else stop_default
            effective_stop = stop / leverage
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + effective_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - effective_stop / 1e4)

            if held >= pos["hold"]:
                exit_reason = "timeout"

            # Test 4: S2 early exit on alt recovery
            if s2_early_exit and pos["strat"] == "S2" and alt_idx_now > s2_early_exit and held >= 3:
                exit_reason = "alt_recovery"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * leverage
                net = gross - effective_cost
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": round(pnl, 2), "net": round(net, 1),
                    "dir": pos["dir"], "strat": pos["strat"],
                    "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason,
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── SIGNALS ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        dxy = get_dxy(ts)
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)
        alt_idx = alt_index(ts)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            # S1
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})

            # S2 (with optional BTC filter)
            if alt_idx < -1000:
                if s2_btc_filter is None or btc7 > s2_btc_filter:
                    candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                        "z": STRAT_Z["S2"], "hold": 18, "strength": abs(alt_idx)})

            # S4
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200 and dxy > 100:
                candidates.append({"coin": coin, "dir": -1, "strat": "S4",
                    "z": STRAT_Z["S4"], "hold": 18, "strength": (1-f["vol_ratio"])*1000})

            # S5
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

            # S8
            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and f.get("ret_24h", 0) < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": 15, "strength": abs(f["drawdown"])})

            # S9 (configurable threshold)
            if abs(f.get("ret_24h", 0)) >= s9_threshold:
                s9_dir = -1 if f["ret_24h"] > 0 else 1
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": 12, "strength": abs(f["ret_24h"])})

            # S10 (configurable window)
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2), s10_squeeze_window)
                if sq_dir:
                    candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                        "z": STRAT_Z["S10"], "hold": 6, "strength": 1000})

        # ── RANK & FILL ──
        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            if len(positions) >= max_pos:
                break
            if cand["dir"] == 1 and n_long >= max_dir:
                continue
            if cand["dir"] == -1 and n_short >= max_dir:
                continue
            if cand["strat"] in macro_strats and n_macro >= max_macro:
                continue
            if cand["strat"] not in macro_strats and n_token >= max_token:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= max_sector:
                    continue

            idx_f = f["_idx"] if (f := feat_by_ts.get(ts, {}).get(coin)) else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            pct = size_pct + (size_bonus if z > 4 else 0)
            haircut = 0.8 if cand["strat"] == "S8" else 1.0
            size = capital * pct * w * haircut

            # Test 5: S5 size boost on large divergence
            if s5_boost_div and cand["strat"] == "S5":
                sf = sector_features.get((ts, coin))
                if sf and abs(sf["divergence"]) >= s5_boost_div:
                    size *= s5_boost_mult

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"], "size": size, "coin": coin,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

    # ── RESULTS ──
    if not trades:
        return {"pnl": 0, "n": 0, "capital": start_capital}

    n = len(trades)
    pnl = capital - start_capital
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    by_month = defaultdict(float)
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        by_month[dt.strftime("%Y-%m")] += t["pnl"]
    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m] > 0)

    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["net"] > 0: s["wins"] += 1

    return {
        "pnl": round(pnl, 0), "n": n, "capital": round(capital, 0),
        "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "months": len(months), "winning": winning,
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "max_dd_pct": round(max_dd_pct, 1),
        "by_strat": {k: dict(v) for k, v in by_strat.items()},
    }


def print_result(label, r):
    bs = r.get("by_strat", {})
    valid = "✓" if r.get("train", 0) > 0 and r.get("test", 0) > 0 else ""
    strat_cols = []
    for s in ["S1", "S2", "S4", "S5", "S8", "S9", "S10"]:
        d = bs.get(s, {})
        n = d.get("n", 0)
        p = d.get("pnl", 0)
        strat_cols.append(f"{n:>3}/{p:>+5.0f}")
    print(f"  {label:<40} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
          f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} "
          f"{'  '.join(strat_cols)} {valid}")


def main():
    print("=" * 130)
    print("  SIGNAL BOOST — 5 targeted improvements")
    print("=" * 130)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "size_pct": 0.12, "size_bonus": 0.03,
            "max_pos": 6, "max_dir": 4, "max_sector": 2,
            "max_macro": 2, "max_token": 3,
            "capital": 1000}

    hdr = (f"  {'Config':<40} {'$Final':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} "
           f"{'Trn':>7} {'Tst':>7}   "
           f"{'S1':>8}  {'S2':>8}  {'S4':>8}  {'S5':>8}  {'S8':>8}  {'S9':>8}  {'S10':>8}")
    sep = f"  {'-'*128}"

    # ═══════════════════════════════════════════════════════════
    print("BASELINE (current prod config: macro 2 / token 3)")
    print(hdr)
    print(sep)
    r_base = backtest(features, data, sf, dxy, base)
    print_result("Baseline (current)", r_base)

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*130}")
    print("TEST 1: S2 + BTC FILTER (block S2 when BTC 7d is falling)")
    print(hdr)
    print(sep)
    print_result("Baseline", r_base)
    for thresh in [-500, -300, -200, -100, 0, 200]:
        cfg = {**base, "s2_btc_filter": thresh}
        r = backtest(features, data, sf, dxy, cfg)
        print_result(f"S2 only when btc_7d > {thresh:+d}bps", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*130}")
    print("TEST 2: S9 THRESHOLD (lower = more trades)")
    print(hdr)
    print(sep)
    print_result("Baseline (2000 bps)", r_base)
    for thresh in [1000, 1200, 1500, 1800, 2500]:
        cfg = {**base, "s9_threshold": thresh}
        r = backtest(features, data, sf, dxy, cfg)
        print_result(f"S9 threshold {thresh} bps", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*130}")
    print("TEST 3: S10 SQUEEZE WINDOW (wider = more squeezes)")
    print(hdr)
    print(sep)
    print_result("Baseline (3 candles)", r_base)
    for sw in [2, 4, 5, 6]:
        cfg = {**base, "s10_squeeze_window": sw}
        r = backtest(features, data, sf, dxy, cfg)
        print_result(f"S10 window {sw} candles ({sw*4}h)", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*130}")
    print("TEST 4: S2 EARLY EXIT ON ALT RECOVERY")
    print(hdr)
    print(sep)
    print_result("Baseline (no early exit)", r_base)
    for thresh in [-500, -400, -300, -200, 0]:
        cfg = {**base, "s2_early_exit": thresh}
        r = backtest(features, data, sf, dxy, cfg)
        print_result(f"S2 exit when alt_idx > {thresh:+d}bps", r)

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*130}")
    print("TEST 5: S5 SIZE BOOST ON LARGE DIVERGENCE")
    print(hdr)
    print(sep)
    print_result("Baseline (no boost)", r_base)
    for div_thresh in [1200, 1500, 2000]:
        for mult in [1.3, 1.5, 2.0]:
            cfg = {**base, "s5_boost_div": div_thresh, "s5_boost_mult": mult}
            r = backtest(features, data, sf, dxy, cfg)
            print_result(f"S5 ×{mult:.1f} when div>{div_thresh}bps", r)


if __name__ == "__main__":
    main()
