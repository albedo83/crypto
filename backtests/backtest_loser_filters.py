"""Validate loser-mitigation filters derived from autopsy:

1. Blacklist tokens that are net negative per window (SUI, IMX, MINA, LINK).
   Walk-forward check: do they underperform on all 4 windows or just 28m?

2. vol_z minimum filter: skip signals with vol_z < threshold (losers have
   lower vol_z than winners on average).

3. S9 sizing reduction: multiply S9 size by factor < 1.

Each tested independently, then combined.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from analysis.bot.config import (
    STRAT_Z, SIGNAL_MULT, MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8, S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP, VERSION, SIZE_PCT, SIZE_BONUS, LIQUIDITY_HAIRCUT,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
    OI_LONG_GATE_BPS,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    detect_squeeze, COST, HOLD_CANDLES, S9_EARLY_EXIT_CANDLES,
)
from backtests.backtest_external_gates import load_oi, oi_delta_24h_pct


def strat_size_custom(strat, capital, s9_mult=1.0):
    """Mirror strat_size with S9 override."""
    z = STRAT_Z.get(strat, 3.0)
    w = max(0.5, min(2.0, z / 4.0))
    pct = SIZE_PCT + (SIZE_BONUS if z > 4.0 else 0)
    haircut = LIQUIDITY_HAIRCUT.get(strat, 1.0)
    mult = SIGNAL_MULT.get(strat, 1.0)
    if strat == "S9":
        mult = mult * s9_mult
    return round(max(10, capital * pct * w * haircut * mult), 2)


def run_window(features, data, sector_features, oi_data,
               start_ts_ms, end_ts_ms, start_capital=1000.0,
               blacklist=None, vol_z_min=None, s9_mult=1.0):
    """v11.4.9 baseline + optional filters."""
    blacklist = blacklist or set()
    coins = [c for c in TOKENS if c in features and c in data and c not in blacklist]
    macro_strats = set(MACRO_STRATEGIES)

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

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    def btc_ret(ts, lookback):
        if ts not in btc_by_ts:
            return 0.0
        i = btc_by_ts[ts]
        if i < lookback or btc_closes[i - lookback] <= 0:
            return 0.0
        return (btc_closes[i] / btc_closes[i - lookback] - 1) * 1e4

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0.0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

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
            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0):
                pos["mfe"] = best_bps

            if pos["strat"] == "S8":
                stop = STOP_LOSS_S8
            elif pos.get("stop", 0) != 0:
                stop = pos["stop"]
            else:
                stop = STOP_LOSS_BPS

            exit_reason = None
            exit_price = current
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - stop / 1e4)
            if held >= pos["hold"]:
                exit_reason = exit_reason or "timeout"
            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                ur = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"
            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
                    ur = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur <= mfe - S10_TRAILING_OFFSET:
                        exit_reason = "s10_trailing"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                               "strat": pos["strat"], "coin": coin,
                               "reason": exit_reason, "size": pos["size"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        candidates = []
        for coin in coins:
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            ret_24h = f.get("ret_6h", 0)
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                                   "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                   "strength": max(f.get("ret_42h", 0), 0),
                                   "vol_z": f.get("vol_z", 0)})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({"coin": coin,
                                   "dir": 1 if sf["divergence"] > 0 else -1,
                                   "strat": "S5",
                                   "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                   "strength": abs(sf["divergence"]),
                                   "vol_z": f.get("vol_z", 0)})
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                                   "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                                   "strength": abs(f["drawdown"]),
                                   "vol_z": f.get("vol_z", 0)})
            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                                   "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                                   "strength": abs(ret_24h), "stop": s9_stop,
                                   "vol_z": f.get("vol_z", 0)})
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    blocked = ((not S10_ALLOW_LONGS and sq_dir == 1)
                               or coin not in S10_ALLOWED_TOKENS)
                    if not blocked:
                        candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                                           "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                           "strength": 1000, "vol_z": f.get("vol_z", 0)})

        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        filtered = [c for c in candidates
                    if c["coin"] not in positions
                    and not (c["coin"] in cooldown and ts < cooldown[c["coin"]])]
        filtered.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in filtered:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)

            # vol_z minimum filter
            if vol_z_min is not None and cand.get("vol_z", 0) < vol_z_min:
                continue

            # v11.4.9 OI entry gate
            if cand["dir"] == 1:
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                if oi_d is not None and oi_d < -OI_LONG_GATE_BPS:
                    continue

            if len(positions) >= MAX_POSITIONS:
                break
            if cand["dir"] == 1 and n_long >= MAX_SAME_DIRECTION:
                continue
            if cand["dir"] == -1 and n_short >= MAX_SAME_DIRECTION:
                continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO_SLOTS:
                continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN_SLOTS:
                continue
            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_PER_SECTOR:
                    continue
            f = feat_by_ts.get(ts, {}).get(coin)
            idx_f = f.get("_idx") if f else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue
            size = strat_size_custom(cand["strat"], capital, s9_mult=s9_mult)
            positions[coin] = {"dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                               "strat": cand["strat"], "hold": cand["hold"],
                               "size": size, "coin": coin,
                               "stop": cand.get("stop", 0), "mfe": 0.0}
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1

    for coin in list(positions.keys()):
        pos = positions[coin]
        last_ts = max(t for t in coin_by_ts[coin] if t <= end_ts_ms)
        last_idx = coin_by_ts[coin][last_ts]
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            capital += pnl
            trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                           "strat": pos["strat"], "coin": coin,
                           "reason": "mtm_final", "size": pos["size"]})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
    }


def main():
    print("Loading...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data thru {end_dt.date()}")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m",  end_dt - relativedelta(months=6)),
        ("3m",  end_dt - relativedelta(months=3)),
    ]

    baselines = {}
    for lb, sd in windows:
        r = run_window(features, data, sector_features, oi_data,
                       int(sd.timestamp() * 1000), latest_ts)
        baselines[lb] = r

    def evaluate(label, **kwargs):
        print(f"\n── {label} ──")
        wins = 0
        for lb, sd in windows:
            r = run_window(features, data, sector_features, oi_data,
                           int(sd.timestamp() * 1000), latest_ts, **kwargs)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            status = "+" if dp > 0 else "-"
            if dp > 0:
                wins += 1
            print(f"  {lb:4}: P&L ${r['pnl']:+7.0f} | DD {r['max_dd_pct']:+5.1f}% | "
                  f"{r['n_trades']:>4}t WR {r['win_rate']:.0f}% | "
                  f"Δ${dp:+7.0f}{status} ΔDD {dd:+5.1f}pp")
        mark = "✓" if wins == 4 else ("≈" if wins == 3 else "✗")
        print(f"  → {wins}/4 wins {mark}")

    print(f"\n{'='*100}")
    print(f"Loser filters — v{VERSION} — data thru {end_dt.date()}")
    print(f"{'='*100}")

    print("\nBaseline:")
    for lb in ("28m", "12m", "6m", "3m"):
        b = baselines[lb]
        print(f"  {lb:4}: P&L ${b['pnl']:+.0f} | DD {b['max_dd_pct']:+.1f}% | "
              f"{b['n_trades']}t WR {b['win_rate']:.0f}%")

    # Per-coin P&L by window to check walk-forward of blacklist
    print(f"\n{'='*100}")
    print(f"Per-coin P&L by window (helps decide blacklist safety)")
    print(f"{'='*100}")

    def per_coin_pnl(sd_ts):
        """Run baseline on window and return per-coin P&L dict."""
        coins = [c for c in TOKENS if c in features and c in data]
        macro = set(MACRO_STRATEGIES)
        # Quick re-run to collect per-coin
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
        btc_candles = data.get("BTC", [])
        btc_closes = np.array([c["c"] for c in btc_candles])
        btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
        def btr(ts, lb):
            if ts not in btc_by_ts:
                return 0.0
            i = btc_by_ts[ts]
            if i < lb or btc_closes[i - lb] <= 0:
                return 0.0
            return (btc_closes[i] / btc_closes[i - lb] - 1) * 1e4
        positions, trades = {}, []
        cooldown = {}
        capital = 1000.0
        sorted_ts = sorted(t for t in all_ts if sd_ts <= t <= latest_ts)
        for ts in sorted_ts:
            btc30 = btr(ts, 180); btc7 = btr(ts, 42)
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
                if pos["dir"] == 1:
                    best = (candle["h"] / pos["entry"] - 1) * 1e4
                else:
                    best = -(candle["l"] / pos["entry"] - 1) * 1e4
                if best > pos.get("mfe", 0):
                    pos["mfe"] = best
                if pos["strat"] == "S8":
                    stop = STOP_LOSS_S8
                elif pos.get("stop", 0) != 0:
                    stop = pos["stop"]
                else:
                    stop = STOP_LOSS_BPS
                exit_reason, exit_price = None, current
                if pos["dir"] == 1:
                    w = (candle["l"] / pos["entry"] - 1) * 1e4
                    if w < stop:
                        exit_reason, exit_price = "stop", pos["entry"] * (1 + stop / 1e4)
                else:
                    w = -(candle["h"] / pos["entry"] - 1) * 1e4
                    if w < stop:
                        exit_reason, exit_price = "stop", pos["entry"] * (1 - stop / 1e4)
                if held >= pos["hold"]:
                    exit_reason = exit_reason or "timeout"
                if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                    u = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if u < S9_EARLY_EXIT_BPS:
                        exit_reason = "s9_early_exit"
                if not exit_reason and pos["strat"] == "S10":
                    m = pos.get("mfe", 0)
                    if m >= S10_TRAILING_TRIGGER:
                        u = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                        if u <= m - S10_TRAILING_OFFSET:
                            exit_reason = "s10_trailing"
                if exit_reason:
                    gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                    net = gross - COST
                    pnl = pos["size"] * net / 1e4
                    capital += pnl
                    trades.append({"pnl": pnl, "coin": coin, "strat": pos["strat"]})
                    del positions[coin]
                    cooldown[coin] = ts + 24 * 3600 * 1000
            cands = []
            for coin in coins:
                f = feat_by_ts.get(ts, {}).get(coin)
                if not f:
                    continue
                r24 = f.get("ret_6h", 0)
                if btc30 > 2000:
                    cands.append({"coin": coin, "dir": 1, "strat": "S1",
                                  "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                  "strength": max(f.get("ret_42h", 0), 0)})
                sf = sector_features.get((ts, coin))
                if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                    cands.append({"coin": coin, "dir": 1 if sf["divergence"] > 0 else -1,
                                  "strat": "S5", "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                  "strength": abs(sf["divergence"])})
                if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                        and f.get("vol_z", 0) > S8_VOL_Z_MIN
                        and r24 < S8_RET_24H_THRESH and btc7 < S8_BTC_7D_THRESH):
                    cands.append({"coin": coin, "dir": 1, "strat": "S8",
                                  "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                                  "strength": abs(f["drawdown"])})
                if abs(r24) >= S9_RET_THRESH:
                    sd9 = -1 if r24 > 0 else 1
                    ss = max(STOP_LOSS_BPS, -500 - abs(r24) / 8) if S9_ADAPTIVE_STOP else 0
                    cands.append({"coin": coin, "dir": sd9, "strat": "S9",
                                  "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                                  "strength": abs(r24), "stop": ss})
                if coin in coin_by_ts and ts in coin_by_ts[coin]:
                    ci = coin_by_ts[coin][ts]
                    sq = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                    if sq:
                        bl = ((not S10_ALLOW_LONGS and sq == 1)
                              or coin not in S10_ALLOWED_TOKENS)
                        if not bl:
                            cands.append({"coin": coin, "dir": sq, "strat": "S10",
                                          "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                          "strength": 1000})
            nl = sum(1 for p in positions.values() if p["dir"] == 1)
            ns = sum(1 for p in positions.values() if p["dir"] == -1)
            nm = sum(1 for p in positions.values() if p["strat"] in macro)
            nt = sum(1 for p in positions.values() if p["strat"] not in macro)
            filt = [c for c in cands if c["coin"] not in positions
                    and not (c["coin"] in cooldown and ts < cooldown[c["coin"]])]
            filt.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
            seen = set()
            for cn in filt:
                cc = cn["coin"]
                if cc in seen or cc in positions:
                    continue
                seen.add(cc)
                if cn["dir"] == 1:
                    oid = oi_delta_24h_pct(oi_data, cc, ts)
                    if oid is not None and oid < -OI_LONG_GATE_BPS:
                        continue
                if len(positions) >= MAX_POSITIONS:
                    break
                if cn["dir"] == 1 and nl >= MAX_SAME_DIRECTION:
                    continue
                if cn["dir"] == -1 and ns >= MAX_SAME_DIRECTION:
                    continue
                if cn["strat"] in macro and nm >= MAX_MACRO_SLOTS:
                    continue
                if cn["strat"] not in macro and nt >= MAX_TOKEN_SLOTS:
                    continue
                sec = TOKEN_SECTOR.get(cc)
                if sec:
                    scc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sec)
                    if scc >= MAX_PER_SECTOR:
                        continue
                f = feat_by_ts.get(ts, {}).get(cc)
                idx_f = f.get("_idx") if f else None
                if idx_f is None or idx_f + 1 >= len(data[cc]):
                    continue
                entry = data[cc][idx_f + 1]["o"]
                if entry <= 0:
                    continue
                size = strat_size_custom(cn["strat"], capital)
                positions[cc] = {"dir": cn["dir"], "entry": entry, "idx": idx_f + 1,
                                 "strat": cn["strat"], "hold": cn["hold"],
                                 "size": size, "coin": cc, "stop": cn.get("stop", 0),
                                 "mfe": 0.0}
                if cn["dir"] == 1:
                    nl += 1
                else:
                    ns += 1
                if cn["strat"] in macro:
                    nm += 1
                else:
                    nt += 1
        per_c = defaultdict(lambda: {"n": 0, "pnl": 0.0})
        for t in trades:
            per_c[t["coin"]]["n"] += 1
            per_c[t["coin"]]["pnl"] += t["pnl"]
        return per_c

    coin_pnl = {}
    for lb, sd in windows:
        coin_pnl[lb] = per_coin_pnl(int(sd.timestamp() * 1000))

    all_coins = set()
    for cp in coin_pnl.values():
        all_coins.update(cp.keys())

    # Show only problematic coins (net negative on 28m)
    candidates_blacklist = {c for c in all_coins if coin_pnl["28m"].get(c, {"pnl": 0})["pnl"] < 0}
    print(f"\n  Coins with net-negative P&L on 28m (candidates for blacklist):")
    print(f"  {'coin':6} {'28m':>12} {'12m':>12} {'6m':>12} {'3m':>12}  verdict")
    for c in sorted(candidates_blacklist,
                    key=lambda x: coin_pnl["28m"].get(x, {"pnl": 0})["pnl"]):
        row = " ".join(f"${coin_pnl[lb].get(c, {'pnl': 0})['pnl']:>+9.0f}"
                       for lb in ("28m", "12m", "6m", "3m"))
        # Verdict: "solid loser" if negative on ≥3 windows (big sample >= 5)
        neg_windows = sum(1 for lb in ("28m", "12m", "6m", "3m")
                          if coin_pnl[lb].get(c, {"pnl": 0})["pnl"] < 0)
        verdict = (f"{neg_windows}/4 windows neg" +
                   (" ← solid loser" if neg_windows >= 3 else ""))
        print(f"  {c:6} {row}  {verdict}")

    # ── Test variants ──
    print(f"\n{'='*100}")
    print("VARIANT TESTS")
    print(f"{'='*100}")

    # 1. Blacklist tests
    blacklists = [
        ("BL {SUI, IMX, MINA, LINK}", {"SUI", "IMX", "MINA", "LINK"}),
        ("BL {SUI, IMX, LINK}",       {"SUI", "IMX", "LINK"}),
        ("BL {SUI, IMX}",             {"SUI", "IMX"}),
        ("BL {SUI}",                  {"SUI"}),
    ]
    for name, bl in blacklists:
        evaluate(name, blacklist=bl)

    # 2. vol_z minimum
    print("\n\n─── vol_z min filter ───")
    for vz in [0.5, 1.0, 1.5, 2.0, 3.0]:
        evaluate(f"vol_z_min={vz}", vol_z_min=vz)

    # 3. S9 sizing reduction
    print("\n\n─── S9 sizing reduction ───")
    for m in [0.25, 0.5, 0.75]:
        evaluate(f"S9 sizing × {m}", s9_mult=m)

    # 4. Combinations
    print("\n\n─── Combined ───")
    evaluate("BL{SUI,IMX,LINK} + S9 × 0.5",
             blacklist={"SUI", "IMX", "LINK"}, s9_mult=0.5)
    evaluate("BL{SUI,IMX,LINK} + vol_z_min=1.5",
             blacklist={"SUI", "IMX", "LINK"}, vol_z_min=1.5)
    evaluate("BL{SUI,IMX,LINK} + S9 × 0.5 + vol_z_min=1.5",
             blacklist={"SUI", "IMX", "LINK"}, s9_mult=0.5, vol_z_min=1.5)


if __name__ == "__main__":
    main()
