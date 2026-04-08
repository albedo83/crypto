"""Backtest 2026 — Active signals (S1/S5/S8/S9/S10) on Jan-Apr 2026 only.

Exact bot configuration: 2x leverage, adaptive S9 stop, slot reservation,
per-signal sizing, sector limits. No train/test split — pure recent performance.

Usage:
    python3 -m backtests.backtest_2026
"""

from __future__ import annotations

import json, os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS,
)
from backtests.backtest_sector import compute_sector_features, TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

# ── Bot config (exact match) ─────────────────────────────────────────
LEVERAGE = 2.0
COST = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE  # effective cost with leverage
MAX_POS = 6
MAX_DIR = 4
MAX_MACRO = 2
MAX_TOKEN = 4
MAX_SECTOR = 2
START_CAPITAL = 1000

# Per-signal sizing multipliers (from config.py)
SIGNAL_MULT = {"S1": 1.125, "S5": 2.50, "S8": 1.25, "S9": 2.00, "S10": 2.00}
STRAT_Z = {"S1": 6.42, "S5": 3.67, "S8": 6.99, "S9": 8.71, "S10": 3.66}

# Hold periods in 4h candles
HOLD = {"S1": 18, "S5": 12, "S8": 15, "S9": 12, "S10": 6}

# Stops (in leveraged bps)
STOP_DEFAULT = -2500
STOP_S8 = -1500

# S9 early exit (only S9 benefits — S5/S8 tested, both lose value in compounding)
S9_EARLY_EXIT_BPS = -1000
S9_EARLY_EXIT_CANDLES = 2   # 8h

# Date filter
START_2026 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000

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


def strat_size(strat, z, capital):
    """Per-signal sizing: base 12% + 3% bonus (z>4), z-weighted, haircut S8, signal mult."""
    w = max(0.5, min(2.0, z / 4.0))
    pct = 0.12 + (0.03 if z > 4 else 0)
    haircut = 0.8 if strat == "S8" else 1.0
    mult = SIGNAL_MULT.get(strat, 1.0)
    return capital * pct * w * haircut * mult


def backtest_2026(features, data, sector_features, dxy_data, start_ts=None, start_capital=None):
    coins = [c for c in TOKENS if c in features and c in data]
    macro_strats = {"S1"}
    if start_ts is None:
        start_ts = START_2026
    if start_capital is not None:
        global START_CAPITAL
        START_CAPITAL = start_capital

    # Build timeline
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
    equity_curve = []

    sorted_ts = sorted(ts for ts in all_ts if ts >= start_ts)
    # But we need earlier data for features — features are precomputed on all data
    # We only ENTER after START_2026

    # We need to process exits even on pre-2026 positions (but we won't have any)
    # Just iterate 2026 timestamps
    for ts in sorted_ts:
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
            stop = pos.get("stop", STOP_DEFAULT)
            if pos["strat"] == "S8":
                stop = STOP_S8
            effective_stop = stop / LEVERAGE

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

            # S9 early exit: cut if not reverting after 8h
            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4 * LEVERAGE
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": round(pnl, 2), "net": round(net, 1),
                    "dir": pos["dir"], "strat": pos["strat"],
                    "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": round(pos["size"], 0),
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── SIGNALS ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            ret_24h = f.get("ret_6h", 0)  # ret_6h is 24h on 4h candles (6 candles)

            # S1: BTC 30d > +20%
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": HOLD["S1"],
                    "strength": max(f.get("ret_42h", 0), 0)})

            # S5: Sector divergence
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= 1000 and sf["vol_z"] >= 1.0:
                d = 1 if sf["divergence"] > 0 else -1
                candidates.append({"coin": coin, "dir": d, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": HOLD["S5"],
                    "strength": abs(sf["divergence"])})

            # S8: Capitulation
            if (f.get("drawdown", 0) < -4000 and f.get("vol_z", 0) > 1.0
                    and ret_24h < -50 and btc7 < -300):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": HOLD["S8"],
                    "strength": abs(f["drawdown"])})

            # S9: Fade extreme moves (±20% in 24h)
            if abs(ret_24h) >= 2000:
                s9_dir = -1 if ret_24h > 0 else 1
                stop_val = max(-2500, -1000 - int(abs(ret_24h) / 4))  # adaptive stop
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": HOLD["S9"],
                    "strength": abs(ret_24h), "stop": stop_val})

            # S10: Squeeze + false breakout
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                        "z": STRAT_Z["S10"], "hold": HOLD["S10"],
                        "strength": 1000})

        # ── RANK & FILL ──
        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            if len(positions) >= MAX_POS:
                break
            if cand["dir"] == 1 and n_long >= MAX_DIR:
                continue
            if cand["dir"] == -1 and n_short >= MAX_DIR:
                continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO:
                continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_SECTOR:
                    continue

            idx_f = f["_idx"] if (f := feat_by_ts.get(ts, {}).get(coin)) else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            size = strat_size(cand["strat"], cand["z"], capital)

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", STOP_DEFAULT),
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

        equity_curve.append((ts, capital))

    # Close remaining positions at last price
    for coin in list(positions.keys()):
        pos = positions[coin]
        candles = data[coin]
        last_idx = min(pos["idx"] + pos["hold"], len(candles) - 1)
        exit_p = candles[last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4 * LEVERAGE
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            capital += pnl
            trades.append({
                "pnl": round(pnl, 2), "net": round(net, 1),
                "dir": pos["dir"], "strat": pos["strat"],
                "coin": coin, "entry_t": pos["entry_t"],
                "exit_t": candles[last_idx]["t"],
                "reason": "final", "size": round(pos["size"], 0),
            })

    return trades, capital, max_dd_pct, equity_curve


def print_results(trades, capital, max_dd_pct):
    if not trades:
        print("  No trades in 2026!")
        return

    n = len(trades)
    pnl = capital - START_CAPITAL
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    print(f"\n  Capital: ${START_CAPITAL} → ${capital:.0f} ({pnl:+.0f})")
    print(f"  Trades: {n} | Win rate: {wins/n*100:.0f}% | Avg: {avg:+.1f} bps")
    print(f"  Max drawdown: {max_dd_pct:+.1f}%")

    # By strategy
    by_strat = defaultdict(list)
    for t in trades:
        by_strat[t["strat"]].append(t)

    print(f"\n  {'Signal':<6} {'Trades':>6} {'Win%':>5} {'Avg bps':>8} {'P&L':>8} {'Avg size':>9}")
    print(f"  {'-'*50}")
    for s in ["S1", "S5", "S8", "S9", "S10"]:
        st = by_strat.get(s, [])
        if not st:
            print(f"  {s:<6} {'0':>6}")
            continue
        sp = sum(t["pnl"] for t in st)
        sa = float(np.mean([t["net"] for t in st]))
        sw = sum(1 for t in st if t["net"] > 0) / len(st) * 100
        avg_sz = float(np.mean([t.get("size", 0) for t in st]))
        print(f"  {s:<6} {len(st):>6} {sw:>4.0f}% {sa:>+7.1f} ${sp:>+7.0f} ${avg_sz:>8.0f}")

    # By month
    by_month = defaultdict(lambda: {"pnl": 0, "n": 0, "wins": 0})
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        m = dt.strftime("%Y-%m")
        by_month[m]["pnl"] += t["pnl"]
        by_month[m]["n"] += 1
        if t["net"] > 0:
            by_month[m]["wins"] += 1

    print(f"\n  {'Month':<8} {'Trades':>6} {'Win%':>5} {'P&L':>8}")
    print(f"  {'-'*30}")
    cum = START_CAPITAL
    for m in sorted(by_month):
        d = by_month[m]
        cum += d["pnl"]
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0
        marker = "+" if d["pnl"] > 0 else "-"
        print(f"  {m:<8} {d['n']:>6} {wr:>4.0f}% ${d['pnl']:>+7.0f}  bal=${cum:.0f} {marker}")

    # By direction
    longs = [t for t in trades if t["dir"] == 1]
    shorts = [t for t in trades if t["dir"] == -1]
    lp = sum(t["pnl"] for t in longs) if longs else 0
    sp_val = sum(t["pnl"] for t in shorts) if shorts else 0
    la = float(np.mean([t["net"] for t in longs])) if longs else 0
    sa_val = float(np.mean([t["net"] for t in shorts])) if shorts else 0
    print(f"\n  LONG:  {len(longs)} trades, avg {la:+.0f} bps, ${lp:+.0f}")
    print(f"  SHORT: {len(shorts)} trades, avg {sa_val:+.0f} bps, ${sp_val:+.0f}")

    # Individual trades
    print(f"\n  {'Date':<12} {'Coin':<6} {'Sig':<4} {'Dir':<6} {'Size':>5} {'Net bps':>8} {'P&L':>7} {'Exit':>8}")
    print(f"  {'-'*65}")
    for t in sorted(trades, key=lambda x: x["entry_t"]):
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        d = "LONG" if t["dir"] == 1 else "SHORT"
        print(f"  {dt.strftime('%m-%d %H:%M'):<12} {t['coin']:<6} {t['strat']:<4} {d:<6} "
              f"${t.get('size',0):>4.0f} {t['net']:>+7.1f} ${t['pnl']:>+6.0f} {t.get('reason',''):>8}")


def main():
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    features = build_features(data)
    print(f"Built features")

    sf = compute_sector_features(features, data)
    print(f"Built sector features")

    dxy = load_dxy()
    print(f"DXY data: {len(dxy)} days")

    # ── Full Q1 2026 ──
    print("\n" + "=" * 70)
    print("  BACKTEST Q1 2026 — Jan 1 → present")
    print("=" * 70)
    trades, capital, max_dd, eq = backtest_2026(features, data, sf, dxy)
    print_results(trades, capital, max_dd)

    # ── Paper bot period: Mar 25 → present ──
    paper_start = datetime(2026, 3, 25, tzinfo=timezone.utc).timestamp() * 1000
    print("\n" + "=" * 70)
    print("  BACKTEST vs PAPER BOT — Mar 25 → present ($1000)")
    print("=" * 70)
    trades2, capital2, max_dd2, eq2 = backtest_2026(features, data, sf, dxy,
                                                     start_ts=paper_start,
                                                     start_capital=1000)
    print_results(trades2, capital2, max_dd2)

    # ── Side-by-side comparison with paper bot actual ──
    print("\n" + "=" * 70)
    print("  COMPARISON: BACKTEST vs PAPER BOT ACTUAL")
    print("=" * 70)
    # Load paper bot trades
    import csv
    paper_trades = []
    csv_path = "/home/crypto/analysis/output/reversal_trades.csv"
    try:
        with open(csv_path) as f:
            for r in csv.DictReader(f):
                paper_trades.append(r)
    except FileNotFoundError:
        print("  No paper trades CSV found!")
        return

    p_pnl = sum(float(t["pnl_usdt"]) for t in paper_trades)
    p_wins = sum(1 for t in paper_trades if float(t["net_bps"]) > 0)
    p_avg = sum(float(t["net_bps"]) for t in paper_trades) / len(paper_trades) if paper_trades else 0

    b_pnl = capital2 - 1000
    b_wins = sum(1 for t in trades2 if t["net"] > 0)
    b_avg = float(np.mean([t["net"] for t in trades2])) if trades2 else 0

    from collections import defaultdict
    p_by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0})
    for t in paper_trades:
        s = p_by_strat[t["strategy"]]
        s["n"] += 1
        s["pnl"] += float(t["pnl_usdt"])
        if float(t["net_bps"]) > 0: s["wins"] += 1

    b_by_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0})
    for t in trades2:
        s = b_by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["net"] > 0: s["wins"] += 1

    print(f"\n  {'':20s} {'BACKTEST':>12} {'PAPER BOT':>12}")
    print(f"  {'-'*46}")
    print(f"  {'Trades':<20} {len(trades2):>12} {len(paper_trades):>12}")
    print(f"  {'Win rate':<20} {b_wins/len(trades2)*100 if trades2 else 0:>11.0f}% {p_wins/len(paper_trades)*100 if paper_trades else 0:>11.0f}%")
    print(f"  {'Avg bps':<20} {b_avg:>+11.1f} {p_avg:>+11.1f}")
    print(f"  {'Total P&L':<20} ${b_pnl:>+10.2f} ${p_pnl:>+10.2f}")

    print(f"\n  By signal:")
    print(f"  {'Signal':<6} {'BT trades':>10} {'BT P&L':>8} {'Paper trades':>13} {'Paper P&L':>10}")
    print(f"  {'-'*50}")
    for s in ["S1", "S5", "S8", "S9", "S10"]:
        b = b_by_strat.get(s, {"n": 0, "pnl": 0})
        p = p_by_strat.get(s, {"n": 0, "pnl": 0})
        print(f"  {s:<6} {b['n']:>10} ${b['pnl']:>+6.0f} {p['n']:>13} ${p['pnl']:>+8.2f}")

    print(f"\n{'=' * 70}")
    print(f"  DONE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
