"""Sizing optimization — Find optimal per-signal multipliers.

Tests all combinations of signal multipliers (0.5x to 3.0x in 0.25 steps)
accounting for both z-score AND frequency. Uses 2026 data only.

Usage:
    python3 -m backtests.backtest_sizing
"""

from __future__ import annotations

import json, os, itertools
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS, COST_BPS
from backtests.backtest_sector import compute_sector_features, TOKEN_SECTOR
from backtests.backtest_2026 import (
    load_dxy, detect_squeeze, LEVERAGE, MAX_POS, MAX_DIR, MAX_MACRO,
    MAX_TOKEN, MAX_SECTOR, STRAT_Z, HOLD, STOP_DEFAULT, STOP_S8,
    S10_BREAKOUT_PCT, S10_REINT_CANDLES,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
START_2026 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
COST = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE
START_CAPITAL = 1000


def backtest_with_mults(features, data, sector_features, dxy_data, mults, start_ts=None):
    """Run backtest with custom per-signal multipliers. Returns (capital, trades, max_dd)."""
    if start_ts is None:
        start_ts = START_2026
    coins = [c for c in TOKENS if c in features and c in data]
    macro_strats = {"S1"}

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

    positions = {}
    trades = []
    cooldown = {}
    capital = START_CAPITAL
    peak_capital = START_CAPITAL
    max_dd_pct = 0

    for ts in sorted(ts for ts in all_ts if ts >= start_ts):
        # EXITS
        for coin in list(positions.keys()):
            pos = positions[coin]
            if ts not in coin_by_ts.get(coin, {}): continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0: continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0: continue

            exit_reason = None
            exit_price = current
            stop = pos.get("stop", STOP_DEFAULT)
            if pos["strat"] == "S8": stop = STOP_S8
            eff_stop = stop / LEVERAGE

            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < eff_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + eff_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < eff_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - eff_stop / 1e4)

            if held >= pos["hold"]: exit_reason = "timeout"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({"pnl": pnl, "net": net, "strat": pos["strat"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # SIGNALS
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]): continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue
            ret_24h = f.get("ret_6h", 0)

            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": HOLD["S1"],
                    "strength": max(f.get("ret_42h", 0), 0)})

            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": HOLD["S5"],
                    "strength": abs(sf["divergence"])})

            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and ret_24h < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": HOLD["S8"],
                    "strength": abs(f["drawdown"])})

            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                stop_val = max(-2500, -1000 - int(abs(ret_24h) / 4))
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": HOLD["S9"],
                    "strength": abs(ret_24h), "stop": stop_val})

            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                        "z": STRAT_Z["S10"], "hold": HOLD["S10"], "strength": 1000})

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions: continue
            seen.add(coin)
            if len(positions) >= MAX_POS: break
            if cand["dir"] == 1 and n_long >= MAX_DIR: continue
            if cand["dir"] == -1 and n_short >= MAX_DIR: continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO: continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN: continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_SECTOR: continue

            f2 = feat_by_ts.get(ts, {}).get(coin)
            if not f2: continue
            idx_f = f2["_idx"]
            if idx_f + 1 >= len(data[coin]): continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0: continue

            # Sizing with custom multiplier
            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            pct = 0.12 + (0.03 if z > 4 else 0)
            haircut = 0.8 if cand["strat"] == "S8" else 1.0
            mult = mults.get(cand["strat"], 1.0)
            size = capital * pct * w * haircut * mult

            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "strat": cand["strat"], "hold": cand["hold"], "size": size,
                "coin": coin, "stop": cand.get("stop", STOP_DEFAULT),
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

    # Close remaining
    for coin in list(positions.keys()):
        pos = positions[coin]
        last_idx = min(pos["idx"] + pos["hold"], len(data[coin]) - 1)
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4 * LEVERAGE
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            capital += pnl
            trades.append({"pnl": pnl, "net": net, "strat": pos["strat"]})

    return capital, trades, max_dd_pct


def run_sweep(features, data, sf, dxy, start_ts, label):
    """Run full sizing sweep for a given period. Returns best mults and results."""
    baseline_mults = {"S1": 1.125, "S5": 1.50, "S8": 1.25, "S9": 1.35, "S10": 1.10}

    print(f"\n{'═'*80}")
    print(f"  {label}")
    print(f"{'═'*80}")

    # Baseline
    cap_b, trades_b, dd_b = backtest_with_mults(features, data, sf, dxy, baseline_mults, start_ts)
    pnl_b = cap_b - START_CAPITAL

    by_s = defaultdict(lambda: {"n": 0, "pnl": 0})
    for t in trades_b:
        by_s[t["strat"]]["n"] += 1
        by_s[t["strat"]]["pnl"] += t["pnl"]

    print(f"\n  BASELINE: ${pnl_b:+.0f} | DD: {dd_b:.1f}% | {len(trades_b)} trades")
    for s in ["S1", "S5", "S8", "S9", "S10"]:
        d = by_s[s]
        avg = d["pnl"] / d["n"] if d["n"] else 0
        print(f"    {s}: {d['n']:>4} trades, ${d['pnl']:>+8.0f}, avg ${avg:>+5.0f}/trade")

    # Independent sweeps
    sweep_vals = [0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00]
    best_per_signal = {}
    active_signals = [s for s in ["S1", "S5", "S8", "S9", "S10"] if by_s[s]["n"] > 0]

    print(f"\n  Independent sweeps:")
    for sig in active_signals:
        best_pnl, best_mult = -9999, 1.0
        for m in sweep_vals:
            test_mults = dict(baseline_mults)
            test_mults[sig] = m
            cap, trades, dd = backtest_with_mults(features, data, sf, dxy, test_mults, start_ts)
            pnl = cap - START_CAPITAL
            if pnl > best_pnl:
                best_pnl = pnl
                best_mult = m
        best_per_signal[sig] = best_mult
        delta = best_pnl - pnl_b
        print(f"    {sig}: best={best_mult:.2f}x → ${best_pnl:+.0f} (Δ${delta:+.0f})")

    # Grid search S5/S9/S10
    fine_vals = {
        "S5": [1.00, 1.25, 1.50, 1.75, 2.00, 2.50],
        "S9": [1.00, 1.25, 1.50, 1.75, 2.00, 2.50],
        "S10": [1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00],
    }

    results = []
    s8_best = best_per_signal.get("S8", baseline_mults["S8"])
    s1_best = best_per_signal.get("S1", baseline_mults["S1"])

    for s5m in fine_vals["S5"]:
        for s9m in fine_vals["S9"]:
            for s10m in fine_vals["S10"]:
                test_mults = {"S1": s1_best, "S5": s5m, "S8": s8_best,
                              "S9": s9m, "S10": s10m}
                cap, trades, dd = backtest_with_mults(features, data, sf, dxy, test_mults, start_ts)
                pnl = cap - START_CAPITAL
                results.append({
                    "s5": s5m, "s8": s8_best, "s9": s9m, "s10": s10m,
                    "pnl": pnl, "dd": dd, "n": len(trades),
                    "mults": dict(test_mults),
                })

    results.sort(key=lambda x: x["pnl"], reverse=True)

    print(f"\n  Top 10:")
    print(f"  {'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5} {'P&L':>8} {'DD':>7} {'Δ base':>8}")
    print(f"  {'-'*50}")
    for r in results[:10]:
        delta = r["pnl"] - pnl_b
        print(f"  {r['s5']:>4.2f}x {r['s8']:>4.2f}x {r['s9']:>4.2f}x {r['s10']:>4.2f}x "
              f"${r['pnl']:>+7.0f} {r['dd']:>+6.1f}% {delta:>+7.0f}")

    best = results[0]
    print(f"\n  Best with DD > -35%:")
    for r in results:
        if r["dd"] > -35:
            delta = r["pnl"] - pnl_b
            print(f"    S5={r['s5']:.2f} S8={r['s8']:.2f} S9={r['s9']:.2f} S10={r['s10']:.2f} "
                  f"→ ${r['pnl']:+.0f} (DD {r['dd']:.1f}%) Δ${delta:+.0f}")
            break

    return {
        "label": label, "baseline_pnl": pnl_b, "baseline_dd": dd_b,
        "best": best, "s8_best": s8_best, "s1_best": s1_best,
        "by_signal": dict(by_s), "n_trades": len(trades_b),
    }


def main():
    print("=" * 80)
    print("  SIZING OPTIMIZATION — 3m / 12m / 24m comparison")
    print("=" * 80)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens")

    periods = [
        (datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000,
         "3 MONTHS — Jan 2026 → Apr 2026"),
        (datetime(2025, 4, 8, tzinfo=timezone.utc).timestamp() * 1000,
         "12 MONTHS — Apr 2025 → Apr 2026"),
        (datetime(2024, 4, 8, tzinfo=timezone.utc).timestamp() * 1000,
         "24 MONTHS — Apr 2024 → Apr 2026"),
    ]

    all_results = []
    for start_ts, label in periods:
        r = run_sweep(features, data, sf, dxy, start_ts, label)
        all_results.append(r)

    # ── Final comparison ──
    print(f"\n\n{'█'*80}")
    print(f"  CROSS-PERIOD COMPARISON")
    print(f"{'█'*80}")

    baseline_mults = {"S1": 1.125, "S5": 1.50, "S8": 1.25, "S9": 1.35, "S10": 1.10}

    print(f"\n  Current mults: S1={baseline_mults['S1']:.2f} S5={baseline_mults['S5']:.2f} "
          f"S8={baseline_mults['S8']:.2f} S9={baseline_mults['S9']:.2f} S10={baseline_mults['S10']:.2f}")

    print(f"\n  {'Period':<32} {'Trades':>6} {'Base P&L':>9} {'Best P&L':>9} {'DD':>6} "
          f"{'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5}")
    print(f"  {'-'*90}")
    for r in all_results:
        b = r["best"]
        print(f"  {r['label'][:30]:<32} {r['n_trades']:>6} ${r['baseline_pnl']:>+7.0f} "
              f"${b['pnl']:>+7.0f} {b['dd']:>+5.1f}% "
              f"{b['s5']:>4.2f}x {b['s8']:>4.2f}x {b['s9']:>4.2f}x {b['s10']:>4.2f}x")

    # Find consensus: which mults are stable across periods?
    print(f"\n  Consensus (values that agree across periods):")
    for sig in ["S5", "S8", "S9", "S10"]:
        vals = []
        for r in all_results:
            b = r["best"]
            key = sig.lower()
            vals.append(b.get(key, b["mults"].get(sig, 0)))
        print(f"    {sig}: {' / '.join(f'{v:.2f}x' for v in vals)}  "
              f"{'STABLE' if max(vals) - min(vals) <= 0.5 else 'VARIES'}")

    print(f"\n{'█'*80}")


if __name__ == "__main__":
    main()
