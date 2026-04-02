"""Smart Priority — Intelligent position management.

Tests:
1. Scoring: z × strength × diversification bonus
2. Slot reservation: keep 1 slot for high-z signals
3. Dynamic replacement: close losers for better signals
4. Portfolio balance: penalize correlated positions
5. Compare all vs current simple priority

Usage:
    python3 -m analysis.backtest_smart
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from backtests.backtest_sector import compute_sector_features, TOKEN_SECTOR, SECTORS

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
STRAT_Z = {"S1": 6.42, "S2": 4.00, "S4": 2.95, "S5": 3.67}
LEVERAGE = 2.0
SIZE_PCT = 0.15


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


def strat_size(name, capital):
    z = STRAT_Z.get(name, 3.0)
    w = max(0.5, min(2.0, z / 4.0))
    return max(10, capital * SIZE_PCT * w)


# Average bps per trade from backtest (used for expected value)
STRAT_AVG_BPS = {"S1": 137, "S2": 53, "S4": 382, "S5": 51}


def backtest_smart(features, data, sector_features, dxy_data, config):
    """Smart priority backtest."""
    mode = config.get("mode", "simple")  # simple, scored, reserve, replace, full
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    start_capital = 1000

    coins = [c for c in TOKENS if c in features and c in data]

    # Build lookups
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
        return dxy_data.get(dxy_ts[lo], 0) if abs(dxy_ts[lo] - ts) < 5*86400*1000 else 0

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
    def btc_30d(ts):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i]/btc_closes[i-180]-1)*1e4 if i>=180 and btc_closes[i-180]>0 else 0

    def alt_index(ts):
        rets = [f.get("ret_42h",0) for f in feat_by_ts.get(ts,{}).values() if "ret_42h" in f]
        return float(np.mean(rets)) if rets else 0

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0
    effective_cost = COST_BPS + (LEVERAGE - 1) * 2

    for ts in sorted(all_ts):
        # ── Exits ──
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]: continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0: continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0: continue

            exit_reason = None
            exit_price = current
            effective_stop = -2500 / LEVERAGE
            if pos["dir"] == 1:
                if (candle["l"]/pos["entry"]-1)*1e4 < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"]*(1+effective_stop/1e4)
            else:
                if -(candle["h"]/pos["entry"]-1)*1e4 < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"]*(1-effective_stop/1e4)

            if held >= pos["hold"]:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["dir"]*(exit_price/pos["entry"]-1)*1e4*LEVERAGE
                net = gross - effective_cost
                pnl = pos["size"]*net/1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital-peak_capital)/peak_capital*100
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts})
                del positions[coin]
                cooldown[coin] = ts + 24*3600*1000

        # ── Collect candidates ──
        dxy = get_dxy(ts)
        btc30 = btc_30d(ts)
        alt_idx = alt_index(ts)

        raw_candidates = []
        for coin in coins:
            if coin in cooldown and ts < cooldown[coin]: continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue

            if btc30 > 2000:
                raw_candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "hold": 18, "threshold_excess": (btc30-2000)/1000})
            if alt_idx < -1000:
                raw_candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                    "hold": 18, "threshold_excess": (abs(alt_idx)-1000)/1000})
            if f.get("vol_ratio",2)<1.0 and f.get("range_pct",999)<200 and dxy>100:
                raw_candidates.append({"coin": coin, "dir": -1, "strat": "S4",
                    "hold": 18, "threshold_excess": (1.0-f["vol_ratio"])*2})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"])>=1000 and sf["vol_z"]>=1.0:
                d = 1 if sf["divergence"]>0 else -1
                raw_candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "hold": 12, "threshold_excess": (abs(sf["divergence"])-1000)/1000})

        # ── Score candidates ──
        n_long = sum(1 for p in positions.values() if p["dir"]==1)
        n_short = sum(1 for p in positions.values() if p["dir"]==-1)
        n_pos = len(positions)
        slots_left = max_pos - n_pos

        # Count sectors in portfolio
        sectors_in = defaultdict(int)
        for p in positions.values():
            sectors_in[TOKEN_SECTOR.get(p["coin"], "?")] += 1

        scored_candidates = []
        seen_coins = set()
        for c in raw_candidates:
            coin = c["coin"]
            if coin in positions or coin in seen_coins: continue
            seen_coins.add(coin)

            if c["dir"]==1 and n_long>=max_dir: continue
            if c["dir"]==-1 and n_short>=max_dir: continue

            z = STRAT_Z[c["strat"]]
            avg_bps = STRAT_AVG_BPS.get(c["strat"], 50)
            excess = min(3.0, c["threshold_excess"])  # how far beyond threshold (0-3)

            if mode == "simple":
                # Current: z-score priority
                score = z * 100 + excess * 10

            elif mode in ("scored", "reserve", "replace", "full"):
                # Expected value: z × avg_bps × strength_bonus
                ev = z * avg_bps * (1 + excess * 0.3)

                # Diversification bonus: reward different sectors and directions
                coin_sector = TOKEN_SECTOR.get(coin, "?")
                if sectors_in.get(coin_sector, 0) == 0:
                    ev *= 1.3  # 30% bonus for new sector
                elif sectors_in.get(coin_sector, 0) >= 2:
                    ev *= 0.7  # 30% penalty for concentrated sector

                # Direction balance bonus
                if c["dir"] == 1 and n_short > n_long:
                    ev *= 1.2  # bonus for balancing
                elif c["dir"] == -1 and n_long > n_short:
                    ev *= 1.2

                score = ev

            scored_candidates.append({**c, "score": score, "z": z})

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # ── Slot reservation ──
        effective_slots = slots_left
        if mode in ("reserve", "full") and slots_left <= 2 and slots_left > 0:
            # Reserve 1 slot for high-z signals (S1)
            has_high_z = any(c["z"] >= 5.0 for c in scored_candidates)
            if not has_high_z:
                effective_slots = max(0, slots_left - 1)

        # ── Dynamic replacement ──
        if mode in ("replace", "full") and slots_left == 0 and scored_candidates:
            best_cand = scored_candidates[0]
            if best_cand["z"] >= 4.0:  # only S1 and S2 can replace
                # Find worst performing position
                worst_coin = None
                worst_value = float("inf")
                for pcoin, pos in positions.items():
                    if pcoin not in coin_by_ts or ts not in coin_by_ts[pcoin]: continue
                    ci = coin_by_ts[pcoin][ts]
                    cur = data[pcoin][ci]["c"]
                    if cur <= 0: continue
                    unreal = pos["dir"]*(cur/pos["entry"]-1)*1e4*LEVERAGE
                    held_pct = (ci-pos["idx"]) / pos["hold"]  # 0-1 progress
                    # Value = unrealized + expected remaining
                    remaining_ev = STRAT_AVG_BPS.get(pos["strat"],50) * (1-held_pct)
                    value = unreal + remaining_ev
                    if value < worst_value:
                        worst_value = value
                        worst_coin = pcoin

                # Replace if new signal's EV > 2× remaining value of worst
                new_ev = best_cand["score"]
                if worst_coin and worst_value < new_ev * 0.3:
                    pos = positions[worst_coin]
                    ci = coin_by_ts[worst_coin][ts]
                    exit_price = data[worst_coin][ci]["c"]
                    if exit_price > 0:
                        gross = pos["dir"]*(exit_price/pos["entry"]-1)*1e4*LEVERAGE
                        net = gross - effective_cost
                        pnl = pos["size"]*net/1e4
                        capital += pnl
                        trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                            "strat": pos["strat"], "coin": worst_coin,
                            "entry_t": pos["entry_t"], "exit_t": ts})
                        del positions[worst_coin]
                        effective_slots = 1

        # ── Fill slots ──
        for cand in scored_candidates:
            if effective_slots <= 0: break
            coin = cand["coin"]
            if coin in positions: continue

            idx_f = feat_by_ts.get(ts, {}).get(coin, {}).get("_idx") if coin in feat_by_ts.get(ts, {}) else None
            if idx_f is None:
                f = feat_by_ts.get(ts, {}).get(coin)
                if f: idx_f = f.get("_idx")
            if idx_f is None or idx_f + 1 >= len(data[coin]): continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0: continue

            size = strat_size(cand["strat"], capital)
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f+1,
                "entry_t": data[coin][idx_f+1]["t"], "strat": cand["strat"],
                "hold": cand["hold"], "size": size, "coin": coin,
            }
            if cand["dir"]==1: n_long += 1
            else: n_short += 1
            effective_slots -= 1
            sectors_in[TOKEN_SECTOR.get(coin,"?")] += 1

    # Results
    if not trades:
        return {"pnl": 0, "n": 0, "capital": start_capital}

    n = len(trades)
    pnl = capital - start_capital
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"]>0)
    by_month = defaultdict(float)
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"]/1000, tz=timezone.utc)
        by_month[dt.strftime("%Y-%m")] += t["pnl"]
    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m]>0)
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"]<TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"]>=TEST_START)

    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0})
    for t in trades:
        by_strat[t["strat"]]["n"] += 1
        by_strat[t["strat"]]["pnl"] += t["pnl"]

    return {
        "pnl": round(pnl,0), "n": n, "capital": round(capital,0),
        "avg": round(avg,1), "win": round(wins/n*100,0),
        "months": len(months), "winning": winning,
        "train": round(train_pnl,0), "test": round(test_pnl,0),
        "max_dd_pct": round(max_dd_pct,1),
        "by_strat": dict(by_strat),
        "trades": trades, "by_month": dict(by_month),
    }


def main():
    print("=" * 70)
    print("  SMART PRIORITY — Intelligent position management")
    print("=" * 70)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready\n")

    modes = [
        ("simple",  "A: Simple (z-score priority)"),
        ("scored",  "B: Scored (EV × diversification)"),
        ("reserve", "C: Scored + slot reservation"),
        ("replace", "D: Scored + dynamic replacement"),
        ("full",    "E: All smart features combined"),
    ]

    print(f"  {'Mode':<40} {'Capital':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*85}")

    results = []
    for mode, label in modes:
        r = backtest_smart(features, data, sf, dxy, {"mode": mode})
        v = "✓" if r["train"]>0 and r["test"]>0 else ""
        print(f"  {label:<40} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} {v}")
        results.append((mode, label, r))

    # Detail on best
    best_mode, best_label, best_r = max(results, key=lambda x: x[2]["pnl"] if x[2]["train"]>0 and x[2]["test"]>0 else -1e9)

    print(f"\n{'='*70}")
    print(f"  BEST: {best_label}")
    print(f"  ${best_r['capital']} final | {best_r['n']} trades | DD {best_r['max_dd_pct']}%")
    print(f"{'='*70}")

    # By strategy
    print(f"\n  By strategy:")
    for sname in sorted(best_r["by_strat"]):
        s = best_r["by_strat"][sname]
        print(f"    {sname}: ${s['pnl']:>+7.0f} ({s['n']:>3}t)")

    # Compare simple vs best
    simple_r = results[0][2]
    print(f"\n  Comparison:")
    print(f"    Simple:  ${simple_r['pnl']:>+7.0f} | {simple_r['n']} trades")
    print(f"    Smart:   ${best_r['pnl']:>+7.0f} | {best_r['n']} trades")
    delta = best_r["pnl"] - simple_r["pnl"]
    print(f"    Δ:       ${delta:>+7.0f} ({delta/max(1,simple_r['pnl'])*100:+.0f}%)")

    # Monthly comparison
    print(f"\n  Monthly (simple vs smart):")
    simple_months = results[0][2].get("by_month", {})
    smart_months = best_r.get("by_month", {})
    all_months = sorted(set(list(simple_months.keys()) + list(smart_months.keys())))
    cum_s, cum_b = 1000, 1000
    for m in all_months:
        sv = simple_months.get(m, 0)
        bv = smart_months.get(m, 0)
        cum_s += sv
        cum_b += bv
        delta_m = bv - sv
        marker = "✓" if delta_m > 0 else "✗" if delta_m < -10 else "="
        print(f"    {m}: simple=${sv:>+7.0f} smart=${bv:>+7.0f} Δ=${delta_m:>+6.0f} "
              f"(${cum_s:>.0f} vs ${cum_b:>.0f}) {marker}")


if __name__ == "__main__":
    main()
