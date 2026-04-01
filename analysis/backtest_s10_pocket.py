"""S10 Capital Pocket Sweep — Find optimal S10_CAPITAL_SHARE.

Currently S10 gets 15% of capital (pocket), other signals share 85%.
S10 positions are ~$17 on $1000 — 5.7x smaller than S5 with same z-score.
Is this optimal, or should S10 share the full pool?

Sweep S10_CAPITAL_SHARE from 0% (no pocket) to 50%.
Active signals only: S1, S5, S8, S9, S10 (S2 removed, S4 suspended).

Usage:
    python3 -m analysis.backtest_s10_pocket
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

STRAT_Z = {"S1": 6.42, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}
MACRO_SIGNALS = {"S1"}
TOKEN_SIGNALS = {"S5", "S8", "S9", "S10"}

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


def backtest(features, data, sector_features, dxy_data, config):
    leverage = config.get("leverage", 2.0)
    size_pct = config.get("size_pct", 0.12)
    size_bonus = config.get("size_bonus", 0.03)
    max_pos = config.get("max_pos", 6)
    max_dir = config.get("max_dir", 4)
    max_macro = config.get("max_macro", 2)
    max_token = config.get("max_token", 4)
    max_sector = config.get("max_sector", 2)
    start_capital = config.get("capital", 1000)
    cost = config.get("cost", COST_BPS)
    stop_default = config.get("stop_bps", -2500)
    stop_s8 = config.get("stop_s8", -1500)
    s10_share = config.get("s10_share", 0.15)

    effective_cost = (cost + (leverage - 1) * 2) * leverage

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

            # S9 adaptive stop
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

            # S1: BTC momentum
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": 18, "strength": abs(btc30)})

            # S5: Sector divergence
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": 12, "strength": abs(sf["divergence"])})

            # S8: Capitulation
            ret_24h = f.get("ret_6h", 0)
            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and ret_24h < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": 15, "strength": abs(f["drawdown"])})

            # S9: Fade extreme (with adaptive stop)
            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = max(-2500, -1000 - abs(ret_24h) / 4)
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": 12, "strength": abs(ret_24h),
                    "stop_bps": s9_stop})

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

            # ── SIZING with S10 pocket ──
            z = cand["z"]
            w = max(0.5, min(2.0, z / 4.0))
            pct = size_pct + (size_bonus if z > 4 else 0)
            haircut = 0.8 if strat == "S8" else 1.0

            if s10_share > 0:
                # Pocket mode: S10 sizes from its pocket, others from the rest
                if strat == "S10":
                    eff_cap = capital * s10_share
                else:
                    eff_cap = capital * (1 - s10_share)
            else:
                # No pocket: all signals size from full capital
                eff_cap = capital

            size = eff_cap * pct * w * haircut

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
    print("=" * 90)
    print("  S10 CAPITAL POCKET SWEEP — Optimal S10_CAPITAL_SHARE")
    print("  Active signals: S1, S5, S8, S9, S10 | Macro 2 / Token 4 | 2x leverage")
    print("=" * 90)

    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy()
    print(f"Data ready: {len(data)} tokens\n")

    base = {"leverage": 2, "size_pct": 0.12, "size_bonus": 0.03,
            "max_pos": 6, "max_dir": 4, "max_sector": 2,
            "max_macro": 2, "max_token": 4,
            "stop_bps": -2500, "stop_s8": -1500, "capital": 1000}

    shares = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

    print(f"  {'Share':<8} {'$Final':>8} {'P&L':>8} {'N':>5} {'W%':>4} {'DD%':>6} "
          f"{'Train':>7} {'Test':>7} {'S1':>7} {'S5':>7} {'S8':>7} {'S9':>7} {'S10':>7}  ")
    print(f"  {'-'*110}")

    results = []
    for share in shares:
        cfg = {**base, "s10_share": share}
        r = backtest(features, data, sf, dxy, cfg)
        bs = r.get("by_strat", {})
        marker = " ◄ current" if share == 0.15 else ""

        strat_cols = []
        for s in ["S1", "S5", "S8", "S9", "S10"]:
            info = bs.get(s, {"n": 0, "pnl": 0})
            strat_cols.append(f"{info['n']:>2}/${info['pnl']:>+5.0f}")

        print(f"  {share:>5.0%}    ${r['capital']:>7,.0f} ${r['pnl']:>+7,.0f} {r['n']:>4} "
              f"{r['win']:>3.0f}% {r['max_dd_pct']:>+5.1f}% ${r['train']:>+6,.0f} ${r['test']:>+6,.0f} "
              f"{'  '.join(strat_cols)}{marker}")

        results.append((share, r))

    # ── ANALYSIS ──
    print(f"\n{'='*90}")
    print("  ANALYSIS")
    print("=" * 90)

    # Best by total P&L
    results.sort(key=lambda x: x[1]["pnl"], reverse=True)
    print(f"\n  Best by total P&L:")
    for share, r in results[:3]:
        print(f"    {share:>5.0%}: ${r['pnl']:>+,} | DD {r['max_dd_pct']:.1f}% | "
              f"S10: {r['by_strat'].get('S10',{}).get('n',0)} trades, "
              f"${r['by_strat'].get('S10',{}).get('pnl',0):+,.0f}")

    # Best by risk-adjusted
    results.sort(key=lambda x: x[1]["pnl"] / max(abs(x[1]["max_dd_pct"]), 1), reverse=True)
    print(f"\n  Best risk-adjusted (P&L / DD):")
    for share, r in results[:3]:
        ratio = r["pnl"] / max(abs(r["max_dd_pct"]), 1)
        print(f"    {share:>5.0%}: ratio={ratio:.0f} | ${r['pnl']:>+,} | DD {r['max_dd_pct']:.1f}%")

    # Best by test P&L
    results.sort(key=lambda x: x[1].get("test", 0), reverse=True)
    print(f"\n  Best out-of-sample (test P&L):")
    for share, r in results[:3]:
        print(f"    {share:>5.0%}: test ${r.get('test',0):>+,} | train ${r.get('train',0):>+,}")

    # Train+test both positive
    valid = [(s, r) for s, r in results if r.get("train", 0) > 0 and r.get("test", 0) > 0]
    if valid:
        valid.sort(key=lambda x: x[1]["pnl"], reverse=True)
        best_s, best_r = valid[0]
        print(f"\n  ★ RECOMMENDATION: S10_CAPITAL_SHARE = {best_s:.0%}")
        print(f"    P&L ${best_r['pnl']:>+,} | DD {best_r['max_dd_pct']:.1f}% | "
              f"train ${best_r.get('train',0):>+,} | test ${best_r.get('test',0):>+,}")


if __name__ == "__main__":
    main()
