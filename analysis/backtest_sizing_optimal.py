"""Optimal Per-Signal Sizing — Find ideal allocation across S1/S5/S8/S9/S10.

Current sizing uses z-score as weight proxy. This backtest sweeps per-signal
multipliers to find the true optimal allocation.

Phase 1: One-at-a-time sweep (each signal independently, others at 1.0)
Phase 2: Grid search around top values from Phase 1
Phase 3: Fine-tune around the best combo

Active signals: S1, S5, S8, S9, S10. No pocket (S10_CAPITAL_SHARE=0).
Slot reservation: Macro 2 / Token 4. 2x leverage. $1000 capital. Compounding.

Usage:
    python3 -m analysis.backtest_sizing_optimal
"""

from __future__ import annotations

import json, os, time as _time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import product

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from analysis.backtest_sector import compute_sector_features, TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

STRAT_Z = {"S1": 6.42, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}
MACRO_SIGNALS = {"S1"}
TOKEN_SIGNALS = {"S5", "S8", "S9", "S10"}
SIGNALS = ["S1", "S5", "S8", "S9", "S10"]

S10_SQUEEZE_WINDOW = 3
S10_VOL_RATIO_MAX = 0.9
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


def detect_squeeze(candles, idx, vol_ratio):
    if vol_ratio > S10_VOL_RATIO_MAX:
        return None
    if idx < S10_SQUEEZE_WINDOW + S10_REINT_CANDLES + 2:
        return None
    for bo_offset in range(1, S10_REINT_CANDLES + 1):
        bo_idx = idx - bo_offset
        sq_start = bo_idx - S10_SQUEEZE_WINDOW
        if sq_start < 0:
            continue
        sq_candles = candles[sq_start:sq_start + S10_SQUEEZE_WINDOW]
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


def strat_size(strat, capital, mult=1.0):
    """Compute position size with per-signal multiplier."""
    z = STRAT_Z.get(strat, 3.0)
    weight = max(0.5, min(2.0, z / 4.0))
    pct = 0.12 + (0.03 if z > 4.0 else 0)
    haircut = 0.8 if strat == "S8" else 1.0
    return max(10, capital * pct * weight * haircut * mult)


def backtest(features, data, sector_features, dxy_data, config, precomputed):
    leverage = config.get("leverage", 2.0)
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    max_macro = config.get("max_macro", 2)
    max_token = config.get("max_token", 4)
    max_sector = config.get("max_sector", 2)
    start_capital = config.get("capital", 1000)
    cost = config.get("cost", COST_BPS)
    stop_default = config.get("stop_bps", -2500)
    stop_s8 = config.get("stop_s8", -1500)
    mults = config.get("mults", {s: 1.0 for s in SIGNALS})

    effective_cost = (cost + (leverage - 1) * 2) * leverage

    coins, all_ts_sorted, coin_by_ts, feat_by_ts = precomputed
    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    def btc_ret(ts, lookback):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i] / btc_closes[i-lookback] - 1) * 1e4 if i >= lookback and btc_closes[i-lookback] > 0 else 0

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0

    for ts in all_ts_sorted:
        # ── EXITS ──
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

            if pos["strat"] == "S9":
                stop = pos.get("stop_bps", stop_default)
            elif pos["strat"] == "S8":
                stop = stop_s8
            else:
                stop = stop_default
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
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── SIGNALS ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in MACRO_SIGNALS)
        n_token = sum(1 for p in positions.values() if p["strat"] in TOKEN_SIGNALS)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})

            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

            ret_24h = f.get("ret_6h", 0)
            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and ret_24h < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": 15, "strength": abs(f["drawdown"])})

            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = max(-2500, -1000 - abs(ret_24h) / 4)
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": 12, "strength": abs(ret_24h),
                    "stop_bps": s9_stop})

            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
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

            strat = cand["strat"]
            if strat in MACRO_SIGNALS and n_macro >= max_macro:
                continue
            if strat in TOKEN_SIGNALS and n_token >= max_token:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sector_count = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sector_count >= max_sector:
                    continue

            idx_f = f["_idx"] if (f := feat_by_ts.get(ts, {}).get(coin)) else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            size = strat_size(strat, capital, mult=mults.get(strat, 1.0))

            # Capital exposure limit: 90%
            used_margin = sum(p["size"] for p in positions.values())
            if used_margin + size > capital * 0.90:
                continue

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "strat": strat, "hold": cand["hold"], "size": size,
                "coin": coin,
                "stop_bps": cand.get("stop_bps"),
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if strat in MACRO_SIGNALS: n_macro += 1
            else: n_token += 1

    # ── RESULTS ──
    if not trades:
        return {"pnl": 0, "n": 0, "capital": start_capital, "max_dd_pct": 0,
                "train": 0, "test": 0, "by_strat": {}, "win": 0, "avg": 0}

    n = len(trades)
    pnl = capital - start_capital
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["net"] > 0: s["wins"] += 1

    return {
        "pnl": round(pnl, 0), "n": n, "capital": round(capital, 0),
        "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "max_dd_pct": round(max_dd_pct, 1),
        "by_strat": {k: dict(v) for k, v in by_strat.items()},
    }


def precompute(features, data):
    """Pre-compute lookups that don't change between runs."""
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
    return coins, sorted(all_ts), coin_by_ts, feat_by_ts


def main():
    print("=" * 100)
    print("  OPTIMAL PER-SIGNAL SIZING — Sweep multipliers for S1/S5/S8/S9/S10")
    print("  No pocket. Macro 2 / Token 4. 2x leverage. $1000. Compounding.")
    print("=" * 100)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    pre = precompute(features, data)
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "max_pos": 6, "max_dir": 4, "max_sector": 2,
            "max_macro": 2, "max_token": 4,
            "stop_bps": -2500, "stop_s8": -1500, "capital": 1000}

    # ═════════════════════════════════════════════════════════════
    # PHASE 1: One-at-a-time sweep
    # ═════════════════════════════════════════════════════════════
    print("PHASE 1: One-at-a-time sweep (others at 1.0)")
    print("=" * 100)

    sweep_values = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
    best_per_signal = {}

    for sig in SIGNALS:
        print(f"\n  Sweeping {sig}:")
        print(f"    {'Mult':>5} {'$Final':>8} {'P&L':>8} {'N':>5} {'DD%':>6} {'Train':>7} {'Test':>7} "
              f"{'S1$':>6} {'S5$':>6} {'S8$':>6} {'S9$':>6} {'S10$':>6}")
        print(f"    {'-'*90}")

        best_pnl = -999999
        best_m = 1.0
        for m in sweep_values:
            mults = {s: 1.0 for s in SIGNALS}
            mults[sig] = m
            cfg = {**base, "mults": mults}
            r = backtest(features, data, sf, dxy, cfg, pre)
            bs = r.get("by_strat", {})
            marker = " ◄" if m == 1.0 else ""

            strat_pnls = [f"${bs.get(s, {}).get('pnl', 0):>+5.0f}" for s in SIGNALS]
            valid = r.get("train", 0) > 0 and r.get("test", 0) > 0

            print(f"    {m:>5.2f} ${r['capital']:>7,} ${r['pnl']:>+7,} {r['n']:>4} "
                  f"{r['max_dd_pct']:>+5.1f}% ${r['train']:>+6,} ${r['test']:>+6,} "
                  f"{'  '.join(strat_pnls)}{'  ✓' if valid else ''}{marker}")

            if valid and r["pnl"] > best_pnl:
                best_pnl = r["pnl"]
                best_m = m

        best_per_signal[sig] = best_m
        print(f"    → Best {sig} multiplier: {best_m:.2f}")

    print(f"\n{'='*100}")
    print(f"  Phase 1 results: {best_per_signal}")
    print(f"{'='*100}")

    # ═════════════════════════════════════════════════════════════
    # PHASE 2: Grid search around Phase 1 best values
    # ═════════════════════════════════════════════════════════════
    print(f"\n{'='*100}")
    print("  PHASE 2: Grid search around Phase 1 best values")
    print("=" * 100)

    # For each signal, test best ± 1 step (3 values each)
    def nearby(val, values):
        idx = min(range(len(values)), key=lambda i: abs(values[i] - val))
        lo = max(0, idx - 1)
        hi = min(len(values) - 1, idx + 1)
        return sorted(set([values[lo], values[idx], values[hi]]))

    grids = {sig: nearby(best_per_signal[sig], sweep_values) for sig in SIGNALS}
    combos = list(product(*[grids[s] for s in SIGNALS]))
    print(f"  Grid: {len(combos)} combinations")
    for sig in SIGNALS:
        print(f"    {sig}: {grids[sig]}")

    results = []
    t0 = _time.time()
    for i, combo in enumerate(combos):
        mults = {SIGNALS[j]: combo[j] for j in range(5)}
        cfg = {**base, "mults": mults}
        r = backtest(features, data, sf, dxy, cfg, pre)
        valid = r.get("train", 0) > 0 and r.get("test", 0) > 0
        results.append((mults, r, valid))
        if (i + 1) % 50 == 0:
            elapsed = _time.time() - t0
            print(f"    {i+1}/{len(combos)} done ({elapsed:.0f}s)")

    elapsed = _time.time() - t0
    print(f"  {len(combos)} runs in {elapsed:.0f}s")

    # Filter valid (train > 0 AND test > 0)
    valid_results = [(m, r) for m, r, v in results if v]
    print(f"  Valid (train+test > 0): {len(valid_results)}/{len(results)}")

    if not valid_results:
        print("  No valid results!")
        return

    # ── TOP 10 by P&L ──
    valid_results.sort(key=lambda x: x[1]["pnl"], reverse=True)
    print(f"\n  TOP 10 by total P&L:")
    print(f"    {'S1':>5} {'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5} | {'$Final':>8} {'P&L':>8} {'DD%':>6} "
          f"{'Train':>7} {'Test':>7} | {'S1$':>6} {'S5$':>6} {'S8$':>6} {'S9$':>6} {'S10$':>6}")
    print(f"    {'-'*105}")
    for mults, r in valid_results[:10]:
        bs = r.get("by_strat", {})
        strat_pnls = [f"${bs.get(s, {}).get('pnl', 0):>+5.0f}" for s in SIGNALS]
        print(f"    {mults['S1']:>5.2f} {mults['S5']:>5.2f} {mults['S8']:>5.2f} "
              f"{mults['S9']:>5.2f} {mults['S10']:>5.2f} | "
              f"${r['capital']:>7,} ${r['pnl']:>+7,} {r['max_dd_pct']:>+5.1f}% "
              f"${r['train']:>+6,} ${r['test']:>+6,} | {'  '.join(strat_pnls)}")

    # ── TOP 5 by risk-adjusted ──
    valid_results.sort(key=lambda x: x[1]["pnl"] / max(abs(x[1]["max_dd_pct"]), 1), reverse=True)
    print(f"\n  TOP 5 risk-adjusted (P&L / |DD|):")
    print(f"    {'S1':>5} {'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5} | {'P&L':>8} {'DD%':>6} {'Ratio':>6} "
          f"{'Train':>7} {'Test':>7}")
    print(f"    {'-'*75}")
    for mults, r in valid_results[:5]:
        ratio = r["pnl"] / max(abs(r["max_dd_pct"]), 1)
        print(f"    {mults['S1']:>5.2f} {mults['S5']:>5.2f} {mults['S8']:>5.2f} "
              f"{mults['S9']:>5.2f} {mults['S10']:>5.2f} | "
              f"${r['pnl']:>+7,} {r['max_dd_pct']:>+5.1f}% {ratio:>5.0f} "
              f"${r['train']:>+6,} ${r['test']:>+6,}")

    # ═════════════════════════════════════════════════════════════
    # PHASE 3: Fine-tune around Phase 2 winner
    # ═════════════════════════════════════════════════════════════
    best_mults, best_r = valid_results[0]
    print(f"\n{'='*100}")
    print(f"  PHASE 3: Fine-tune around best risk-adjusted")
    print(f"  Base: {best_mults}")
    print("=" * 100)

    fine_values = [0.8, 0.9, 1.0, 1.1, 1.2]
    fine_combos = list(product(fine_values, repeat=5))
    print(f"  {len(fine_combos)} fine-tune combinations")

    fine_results = []
    t0 = _time.time()
    for i, adjustments in enumerate(fine_combos):
        mults = {SIGNALS[j]: round(best_mults[SIGNALS[j]] * adjustments[j], 3) for j in range(5)}
        cfg = {**base, "mults": mults}
        r = backtest(features, data, sf, dxy, cfg, pre)
        valid = r.get("train", 0) > 0 and r.get("test", 0) > 0
        if valid:
            fine_results.append((mults, r))
        if (i + 1) % 500 == 0:
            elapsed = _time.time() - t0
            print(f"    {i+1}/{len(fine_combos)} done ({elapsed:.0f}s)")

    elapsed = _time.time() - t0
    print(f"  {len(fine_combos)} runs in {elapsed:.0f}s, {len(fine_results)} valid")

    if fine_results:
        # Best by risk-adjusted
        fine_results.sort(key=lambda x: x[1]["pnl"] / max(abs(x[1]["max_dd_pct"]), 1), reverse=True)
        print(f"\n  TOP 5 fine-tuned (risk-adjusted):")
        print(f"    {'S1':>5} {'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5} | {'P&L':>8} {'DD%':>6} {'Ratio':>6} "
              f"{'Train':>7} {'Test':>7} | {'S1$':>6} {'S5$':>6} {'S8$':>6} {'S9$':>6} {'S10$':>6}")
        print(f"    {'-'*105}")
        for mults, r in fine_results[:5]:
            bs = r.get("by_strat", {})
            ratio = r["pnl"] / max(abs(r["max_dd_pct"]), 1)
            strat_pnls = [f"${bs.get(s, {}).get('pnl', 0):>+5.0f}" for s in SIGNALS]
            print(f"    {mults['S1']:>5.2f} {mults['S5']:>5.2f} {mults['S8']:>5.2f} "
                  f"{mults['S9']:>5.2f} {mults['S10']:>5.2f} | "
                  f"${r['pnl']:>+7,} {r['max_dd_pct']:>+5.1f}% {ratio:>5.0f} "
                  f"${r['train']:>+6,} ${r['test']:>+6,} | {'  '.join(strat_pnls)}")

        winner_m, winner_r = fine_results[0]
    else:
        winner_m, winner_r = best_mults, best_r

    # ═════════════════════════════════════════════════════════════
    # COMPARISON vs current (all 1.0)
    # ═════════════════════════════════════════════════════════════
    baseline_cfg = {**base, "mults": {s: 1.0 for s in SIGNALS}}
    baseline = backtest(features, data, sf, dxy, baseline_cfg, pre)

    print(f"\n{'='*100}")
    print("  FINAL COMPARISON")
    print("=" * 100)
    print(f"\n  Current (all 1.0):")
    print(f"    P&L ${baseline['pnl']:>+,} | DD {baseline['max_dd_pct']:.1f}% | "
          f"ratio {baseline['pnl'] / max(abs(baseline['max_dd_pct']), 1):.0f} | "
          f"train ${baseline['train']:>+,} | test ${baseline['test']:>+,}")
    bs = baseline.get("by_strat", {})
    for s in SIGNALS:
        info = bs.get(s, {"n": 0, "pnl": 0, "wins": 0})
        wr = info["wins"] / info["n"] * 100 if info["n"] > 0 else 0
        print(f"      {s}: {info['n']:>3} trades, ${info['pnl']:>+6,.0f}, {wr:.0f}% win")

    print(f"\n  ★ OPTIMAL ({winner_m}):")
    print(f"    P&L ${winner_r['pnl']:>+,} | DD {winner_r['max_dd_pct']:.1f}% | "
          f"ratio {winner_r['pnl'] / max(abs(winner_r['max_dd_pct']), 1):.0f} | "
          f"train ${winner_r['train']:>+,} | test ${winner_r['test']:>+,}")
    bs = winner_r.get("by_strat", {})
    for s in SIGNALS:
        info = bs.get(s, {"n": 0, "pnl": 0, "wins": 0})
        wr = info["wins"] / info["n"] * 100 if info["n"] > 0 else 0
        print(f"      {s}: {info['n']:>3} trades, ${info['pnl']:>+6,.0f}, {wr:.0f}% win, mult={winner_m[s]:.2f}")

    delta = winner_r["pnl"] - baseline["pnl"]
    print(f"\n  Δ P&L: ${delta:>+,.0f} ({delta / max(baseline['pnl'], 1) * 100:>+.0f}%)")
    print(f"  Δ DD:  {winner_r['max_dd_pct'] - baseline['max_dd_pct']:>+.1f}%")

    # Print the recommended SIZE_MULT dict for reversal.py
    print(f"\n  Recommended SIGNAL_MULT for reversal.py:")
    print(f"    SIGNAL_MULT = {dict(winner_m)}")


if __name__ == "__main__":
    main()
