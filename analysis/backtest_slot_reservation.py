"""Slot Reservation Backtest — Find optimal max positions per signal category.

Problem: Macro signals (S1/S2/S4) fire on ALL tokens simultaneously, filling
all 6 slots at once. Token-level signals (S5/S8/S9/S10) that fire later are
blocked for 48-72h. Does reserving slots improve total P&L?

Test matrix:
  max_macro = {2, 3, 4, 5, 6}  (slots for S1/S2/S4)
  max_token = 6 - max_macro     (reserved for S5/S8/S9/S10)
  Also test: shared pool (current behavior, no reservation)

All 7 signals included. 2x leverage. $1000 capital. Compounding.

Usage:
    python3 -m analysis.backtest_slot_reservation
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
MACRO_SIGNALS = {"S1", "S2", "S4"}
TOKEN_SIGNALS = {"S5", "S8", "S9", "S10"}

# S10 squeeze params (frozen)
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
    """Detect squeeze → false breakout → reintegration (S10 Mode B)."""
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

        # Reintegration check
        ri_end = min(bo_idx + 1 + S10_REINT_CANDLES, idx + 1)
        for ri in range(bo_idx + 1, ri_end):
            if range_low <= candles[ri]["c"] <= range_high:
                return -bo_dir  # Mode B: fade breakout
    return None


def backtest(features, data, sector_features, dxy_data, config):
    """Portfolio backtest with slot reservation."""
    leverage = config.get("leverage", 2.0)
    size_pct = config.get("size_pct", 0.12)
    size_bonus = config.get("size_bonus", 0.03)
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    max_macro = config.get("max_macro", max_pos)   # max slots for S1/S2/S4
    max_token = config.get("max_token", max_pos)    # max slots for S5/S8/S9/S10
    max_sector = config.get("max_sector", 2)
    start_capital = config.get("capital", 1000)
    cost = config.get("cost", COST_BPS)
    stop_default = config.get("stop_bps", -2500)
    stop_s8 = config.get("stop_s8", -1500)

    effective_cost = (cost + (leverage - 1) * 2) * leverage

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
        if not dxy_ts:
            return 0
        lo, hi = 0, len(dxy_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if dxy_ts[mid] <= ts:
                lo = mid
            else:
                hi = mid - 1
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

            # S1: BTC momentum
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})

            # S2: Alt crash
            if alt_idx < -1000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                    "z": STRAT_Z["S2"], "hold": 18, "strength": abs(alt_idx)})

            # S4: Vol compression + DXY
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200 and dxy > 100:
                candidates.append({"coin": coin, "dir": -1, "strat": "S4",
                    "z": STRAT_Z["S4"], "hold": 18, "strength": (1-f["vol_ratio"])*1000})

            # S5: Sector divergence
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

            # S8: Capitulation (ret_6h = 6 candles × 4h = 24h)
            ret_24h = f.get("ret_6h", 0)
            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and ret_24h < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": 15, "strength": abs(f["drawdown"])})

            # S9: Fade extreme
            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": 12, "strength": abs(ret_24h)})

            # S10: Squeeze
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

            # Slot reservation check
            strat = cand["strat"]
            if strat in MACRO_SIGNALS and n_macro >= max_macro:
                continue
            if strat in TOKEN_SIGNALS and n_token >= max_token:
                continue

            # Sector limit
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

            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            pct = size_pct + (size_bonus if z > 4 else 0)
            haircut = 0.8 if strat == "S8" else 1.0
            size = capital * pct * w * haircut

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "strat": strat, "hold": cand["hold"], "size": size,
                "coin": coin,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if strat in MACRO_SIGNALS: n_macro += 1
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

    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0})
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


def main():
    print("=" * 80)
    print("  SLOT RESERVATION — Optimal macro vs token signal allocation")
    print("=" * 80)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "size_pct": 0.12, "size_bonus": 0.03,
            "max_pos": 6, "max_dir": 4, "max_sector": 2,
            "stop_bps": -2500, "stop_s8": -1500, "capital": 1000}

    # ═══════════════════════════════════════════════════════════
    print("TEST: SLOT RESERVATION (max_macro + max_token, total ≤ 6)")
    print(f"  {'Config':<30} {'$Final':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} "
          f"{'Trn':>7} {'Tst':>7} {'S1':>5} {'S2':>5} {'S4':>5} {'S5':>5} {'S8':>5} {'S9':>5} {'S10':>5}")
    print(f"  {'-'*120}")

    configs = [
        # (label, max_macro, max_token)
        ("No reservation (6/6)", 6, 6),
        ("Macro 5 / Token 5", 5, 5),
        ("Macro 4 / Token 4", 4, 4),
        ("Macro 3 / Token 5", 3, 5),
        ("Macro 3 / Token 3", 3, 3),
        ("Macro 2 / Token 4", 2, 4),
        ("Macro 2 / Token 3", 2, 3),
        ("Macro 4 / Token 2", 4, 2),
        ("Macro 1 / Token 5", 1, 5),
    ]

    results = []
    for label, mm, mt in configs:
        cfg = {**base, "max_macro": mm, "max_token": mt}
        r = backtest(features, data, sf, dxy, cfg)
        bs = r.get("by_strat", {})
        valid = "✓" if r.get("train", 0) > 0 and r.get("test", 0) > 0 else ""

        strat_pnls = []
        for s in ["S1", "S2", "S4", "S5", "S8", "S9", "S10"]:
            p = bs.get(s, {}).get("pnl", 0)
            strat_pnls.append(f"${p:>+4.0f}")

        print(f"  {label:<30} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
              f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} "
              f"{' '.join(strat_pnls)} {valid}")

        results.append((label, mm, mt, r))

    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*80}")
    print("  BEST RESULTS")
    print("=" * 80)

    # Sort by total P&L
    results.sort(key=lambda x: x[3]["pnl"], reverse=True)
    print(f"\n  By total P&L:")
    for label, mm, mt, r in results[:3]:
        print(f"    {label}: ${r['pnl']:+,.0f} | DD {r['max_dd_pct']:.1f}% | {r['n']} trades")

    # Sort by test P&L (out of sample)
    results.sort(key=lambda x: x[3].get("test", 0), reverse=True)
    print(f"\n  By test P&L (out of sample):")
    for label, mm, mt, r in results[:3]:
        print(f"    {label}: test ${r.get('test',0):+,.0f} | train ${r.get('train',0):+,.0f}")

    # Sort by risk-adjusted (P&L / DD)
    results.sort(key=lambda x: x[3]["pnl"] / max(abs(x[3]["max_dd_pct"]), 1), reverse=True)
    print(f"\n  By risk-adjusted (P&L / DD):")
    for label, mm, mt, r in results[:3]:
        ratio = r["pnl"] / max(abs(r["max_dd_pct"]), 1)
        print(f"    {label}: P&L/DD = {ratio:.0f} | ${r['pnl']:+,.0f} | DD {r['max_dd_pct']:.1f}%")


if __name__ == "__main__":
    main()
