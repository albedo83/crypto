"""Signal Boost 2 — 6 advanced improvement tests.

Tests (all on top of baseline: slot reservation 2/3 + S2 early exit -200bps):
  1. Adaptive hold (proportional to signal strength)
  2. Token picking for S1/S2 (vol_z, drawdown, ret_6h as strength)
  3. S4 + vol_z filter (require quiet volume too)
  4. S2+S8 combo boost (bigger size when both fire on same token)
  5. Adaptive stop for S9 (tighter stop on bigger moves)
  6. Immediate entry (close of current candle vs next open)

Usage:
    python3 -m analysis.backtest_signal_boost2
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
    if vol_ratio > 0.9 or idx < 7:
        return None
    for bo_offset in range(1, S10_REINT_CANDLES + 1):
        bo_idx = idx - bo_offset
        sq_start = bo_idx - 3
        if sq_start < 0:
            continue
        sq_candles = candles[sq_start:sq_start + 3]
        rh = max(c["h"] for c in sq_candles)
        rl = min(c["l"] for c in sq_candles)
        rs = rh - rl
        if rs <= 0 or rl <= 0:
            continue
        bo = candles[bo_idx]
        th = rs * S10_BREAKOUT_PCT
        above = bo["h"] > rh + th
        below = bo["l"] < rl - th
        if not above and not below:
            continue
        if above and below:
            continue
        bo_dir = 1 if above else -1
        ri_end = min(bo_idx + 1 + S10_REINT_CANDLES, idx + 1)
        for ri in range(bo_idx + 1, ri_end):
            if rl <= candles[ri]["c"] <= rh:
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

    # Test params
    adaptive_hold = config.get("adaptive_hold", False)
    hold_mult = config.get("hold_mult", 1.0)
    s1_strength_mode = config.get("s1_strength", "ret_42h")  # ret_42h, vol_z, drawdown, ret_6h
    s2_strength_mode = config.get("s2_strength", "alt_idx")  # alt_idx, vol_z, drawdown, ret_6h
    s4_vol_z_max = config.get("s4_vol_z_max", None)          # None = no filter
    combo_boost = config.get("combo_boost", None)             # None or multiplier
    s9_adaptive_stop = config.get("s9_adaptive_stop", False)
    immediate_entry = config.get("immediate_entry", False)
    s2_early_exit = config.get("s2_early_exit", -200)

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

            stop = pos.get("stop", stop_default)
            if pos["strat"] == "S8":
                stop = stop_s8
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

        # Track which signals fire for combo detection
        s2_coins = set()
        s8_coins = set()

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            ret_24h = f.get("ret_6h", 0)

            # S1
            if btc30 > 2000:
                if s1_strength_mode == "vol_z":
                    strength = f.get("vol_z", 0)
                elif s1_strength_mode == "drawdown":
                    strength = -abs(f.get("drawdown", 0))  # less drawdown = higher
                elif s1_strength_mode == "ret_6h":
                    strength = abs(ret_24h)
                else:
                    strength = max(f.get("ret_42h", 0), 0)

                hold = 18
                if adaptive_hold:
                    hold = max(6, min(24, int(12 + (btc30 - 2000) / 500)))
                    hold = int(hold * hold_mult)

                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": hold, "strength": strength})

            # S2
            if alt_idx < -1000:
                s2_coins.add(coin)
                if s2_strength_mode == "vol_z":
                    strength = f.get("vol_z", 0)
                elif s2_strength_mode == "drawdown":
                    strength = -abs(f.get("drawdown", 0))
                elif s2_strength_mode == "ret_6h":
                    strength = abs(ret_24h)
                else:
                    strength = abs(alt_idx)

                hold = 18
                if adaptive_hold:
                    hold = max(6, min(24, int(12 + (abs(alt_idx) - 1000) / 500)))
                    hold = int(hold * hold_mult)

                candidates.append({"coin": coin, "dir": 1, "strat": "S2",
                    "z": STRAT_Z["S2"], "hold": hold, "strength": strength})

            # S4
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 200 and dxy > 100:
                if s4_vol_z_max is None or f.get("vol_z", 0) < s4_vol_z_max:
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
                    and ret_24h < -50 and btc7 < -300):
                s8_coins.add(coin)
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": 15, "strength": abs(f["drawdown"])})

            # S9
            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                stop_val = stop_default
                if s9_adaptive_stop:
                    # Bigger move → tighter stop (more confident in reversion)
                    stop_val = max(-2500, -1000 - int(abs(ret_24h) / 4))
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": 12, "strength": abs(ret_24h),
                    "stop": stop_val})

            # S10
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                        "z": STRAT_Z["S10"], "hold": 6, "strength": 1000})

        # Combo detection: S2+S8 on same token
        combo_coins = s2_coins & s8_coins

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

            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            idx_f = f["_idx"]

            if immediate_entry:
                entry = data[coin][idx_f]["c"]  # enter at current candle close
                entry_idx = idx_f
            else:
                if idx_f + 1 >= len(data[coin]):
                    continue
                entry = data[coin][idx_f + 1]["o"]  # next candle open
                entry_idx = idx_f + 1
            if entry <= 0:
                continue

            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            pct = size_pct + (size_bonus if z > 4 else 0)
            haircut = 0.8 if cand["strat"] == "S8" else 1.0
            size = capital * pct * w * haircut

            # Combo boost
            if combo_boost and coin in combo_coins and cand["strat"] in ("S2", "S8"):
                size *= combo_boost

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": entry_idx, "entry_t": data[coin][entry_idx]["t"],
                "strat": cand["strat"], "hold": cand["hold"], "size": size, "coin": coin,
                "stop": cand.get("stop", stop_default),
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

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
        s["n"] += 1; s["pnl"] += t["pnl"]
        if t["net"] > 0: s["wins"] += 1

    return {
        "pnl": round(pnl, 0), "n": n, "capital": round(capital, 0),
        "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "months": len(months), "winning": winning,
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
        "max_dd_pct": round(max_dd_pct, 1),
        "by_strat": {k: dict(v) for k, v in by_strat.items()},
    }


def pr(label, r):
    bs = r.get("by_strat", {})
    v = "✓" if r.get("train", 0) > 0 and r.get("test", 0) > 0 else ""
    sc = []
    for s in ["S1","S2","S4","S5","S8","S9","S10"]:
        d = bs.get(s, {}); sc.append(f"{d.get('n',0):>3}/${d.get('pnl',0):>+5.0f}")
    print(f"  {label:<40} ${r['capital']:>7} ${r['pnl']:>+7} {r['n']:>4} "
          f"{r['win']:>3}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6} ${r['test']:>+6} "
          f"{'  '.join(sc)} {v}")


def hdr():
    h = f"  {'Config':<40} {'$Final':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} {'Trn':>7} {'Tst':>7}"
    for s in ['S1','S2','S4','S5','S8','S9','S10']:
        h += f"  {s:>8}"
    print(h)
    print(f"  {'-'*130}")


def main():
    print("=" * 130)
    print("  SIGNAL BOOST 2 — 6 advanced tests")
    print("=" * 130)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "size_pct": 0.12, "size_bonus": 0.03,
            "max_pos": 6, "max_dir": 4, "max_sector": 2,
            "max_macro": 2, "max_token": 3, "capital": 1000,
            "s2_early_exit": -200}

    r_base = backtest(features, data, sf, dxy, base)

    # ═══ TEST 1: ADAPTIVE HOLD ═══
    print("TEST 1: ADAPTIVE HOLD (stronger signal → longer hold)")
    hdr()
    pr("Baseline (fixed hold)", r_base)
    for hm in [0.7, 1.0, 1.3]:
        r = backtest(features, data, sf, dxy, {**base, "adaptive_hold": True, "hold_mult": hm})
        pr(f"Adaptive hold × {hm:.1f}", r)

    # ═══ TEST 2: TOKEN PICKING ═══
    print(f"\nTEST 2: TOKEN PICKING for S1/S2 (strength criterion)")
    hdr()
    pr("Baseline (S1=ret_42h, S2=alt_idx)", r_base)
    for s1m, s2m, label in [
        ("vol_z", "vol_z", "vol_z (volume confirms)"),
        ("drawdown", "drawdown", "drawdown (healthiest)"),
        ("ret_6h", "ret_6h", "ret_6h (recent move)"),
        ("ret_42h", "vol_z", "S1=momentum, S2=vol_z"),
        ("ret_42h", "drawdown", "S1=momentum, S2=drawdown"),
    ]:
        r = backtest(features, data, sf, dxy, {**base, "s1_strength": s1m, "s2_strength": s2m})
        pr(f"Strength: {label}", r)

    # ═══ TEST 3: S4 + VOL_Z FILTER ═══
    print(f"\nTEST 3: S4 + VOL_Z FILTER (require quiet volume)")
    hdr()
    pr("Baseline (no vol_z filter)", r_base)
    for vz in [0.5, 0, -0.3, -0.5, -1.0]:
        r = backtest(features, data, sf, dxy, {**base, "s4_vol_z_max": vz})
        pr(f"S4 only when vol_z < {vz}", r)

    # ═══ TEST 4: COMBO BOOST S2+S8 ═══
    print(f"\nTEST 4: COMBO BOOST (S2+S8 on same token → bigger size)")
    hdr()
    pr("Baseline (no combo)", r_base)
    for mult in [1.3, 1.5, 2.0]:
        r = backtest(features, data, sf, dxy, {**base, "combo_boost": mult})
        pr(f"Combo S2+S8 × {mult:.1f}", r)

    # ═══ TEST 5: ADAPTIVE STOP S9 ═══
    print(f"\nTEST 5: ADAPTIVE STOP S9 (bigger move → tighter stop)")
    hdr()
    pr("Baseline (fixed -2500 bps)", r_base)
    r = backtest(features, data, sf, dxy, {**base, "s9_adaptive_stop": True})
    pr("S9 stop = -1000 - abs(ret)/4", r)

    # ═══ TEST 6: IMMEDIATE ENTRY ═══
    print(f"\nTEST 6: IMMEDIATE ENTRY (close of current candle vs next open)")
    hdr()
    pr("Baseline (next candle open)", r_base)
    r = backtest(features, data, sf, dxy, {**base, "immediate_entry": True})
    pr("Enter at current close", r)

    # ═══ BEST COMBO ═══
    print(f"\n{'='*130}")
    print("BEST COMBO — Stack all winning improvements")
    hdr()
    pr("Baseline", r_base)


if __name__ == "__main__":
    main()
