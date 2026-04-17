"""Backtest recommendations B1 (signal-inverse exit) and A1 (S5 regime filter).

Compares 4 configurations on multiple time windows:
- baseline: current v11.4.8 config
- B1: exit a position early if an opposite-direction signal fires on the same symbol
- A1: gate S5 by BTC 30d trend magnitude (|btc_30d| > threshold)
- B1 + A1: both variants combined

Uses the same engine as backtest_rolling.py, modified to support exit hooks and
entry filters. Data ends on 2026-04-16.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from analysis.bot.config import (
    STRAT_Z, MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8, S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP, VERSION,
    S10_SQUEEZE_WINDOW, S10_VOL_RATIO_MAX, S10_BREAKOUT_PCT, S10_REINT_CANDLES,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    detect_squeeze, strat_size, COST,
    HOLD_CANDLES, S9_EARLY_EXIT_CANDLES,
)


def run_window(features, data, sector_features,
               start_ts_ms: int, end_ts_ms: int,
               start_capital: float = 1000.0,
               inverse_exit: bool = False,
               s5_regime_min_btc30: float = 0.0) -> dict:
    """Run portfolio backtest with optional variants.

    Args:
        inverse_exit: if True, close a held position when an opposite-direction
            signal fires on the same symbol ("inverse exit" — reason: 'inverse').
        s5_regime_min_btc30: if > 0, block S5 entries when |btc_30d| < threshold.
            0 = no filter.
    """
    coins = [c for c in TOKENS if c in features and c in data]
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

    def build_candidates(ts, btc30, btc7):
        """Collect all raw signals for this timestamp (before limit checks)."""
        cands = []
        for coin in coins:
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            ret_24h = f.get("ret_6h", 0)
            if btc30 > 2000:
                cands.append({"coin": coin, "dir": 1, "strat": "S1",
                              "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                              "strength": max(f.get("ret_42h", 0), 0)})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                # A1 regime filter: require trending BTC
                if s5_regime_min_btc30 > 0 and abs(btc30) < s5_regime_min_btc30:
                    pass  # skip S5 in choppy regime
                else:
                    cands.append({"coin": coin,
                                  "dir": 1 if sf["divergence"] > 0 else -1,
                                  "strat": "S5",
                                  "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                  "strength": abs(sf["divergence"])})
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                cands.append({"coin": coin, "dir": 1, "strat": "S8",
                              "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                              "strength": abs(f["drawdown"])})
            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                cands.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                              "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                              "strength": abs(ret_24h), "stop": s9_stop})
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    blocked = ((not S10_ALLOW_LONGS and sq_dir == 1)
                               or coin not in S10_ALLOWED_TOKENS)
                    if not blocked:
                        cands.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                                      "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                      "strength": 1000})
        return cands

    for ts in sorted_ts:
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)
        all_cands = build_candidates(ts, btc30, btc7)

        # ── B1 INVERSE EXIT (before normal exits) ──
        if inverse_exit and all_cands:
            dir_by_coin = {}
            for cnd in all_cands:
                dir_by_coin.setdefault(cnd["coin"], set()).add(cnd["dir"])
            for coin in list(positions.keys()):
                if coin not in dir_by_coin:
                    continue
                pos = positions[coin]
                # Trigger only if a signal fires in the OPPOSITE direction
                if -pos["dir"] in dir_by_coin[coin]:
                    if ts not in coin_by_ts.get(coin, {}):
                        continue
                    ci = coin_by_ts[coin][ts]
                    candle = data[coin][ci]
                    exit_price = candle["c"]
                    gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                    net = gross - COST
                    pnl = pos["size"] * net / 1e4
                    capital += pnl
                    peak_capital = max(peak_capital, capital)
                    dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                    max_dd_pct = min(max_dd_pct, dd)
                    trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                                   "strat": pos["strat"], "coin": coin,
                                   "entry_t": pos["entry_t"], "exit_t": ts,
                                   "reason": "inverse", "size": pos["size"]})
                    del positions[coin]
                    cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Standard EXITS ──
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
                ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"
            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps <= mfe - S10_TRAILING_OFFSET:
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
                               "entry_t": pos["entry_t"], "exit_t": ts,
                               "reason": exit_reason, "size": pos["size"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── ENTRIES ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        candidates = [c for c in all_cands
                      if c["coin"] not in positions
                      and not (c["coin"] in cooldown and ts < cooldown[c["coin"]])]
        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
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
            size = strat_size(cand["strat"], capital)
            positions[coin] = {"dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                               "entry_t": data[coin][idx_f + 1]["t"],
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

    # Close remaining at mark-to-market
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
                           "entry_t": pos["entry_t"], "exit_t": last_ts,
                           "reason": "mtm_final", "size": pos["size"]})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1
    by_reason = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in trades:
        by_reason[t["reason"]]["n"] += 1
        by_reason[t["reason"]]["pnl"] += t["pnl"]

    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "by_strat": {k: {"n": v["n"], "pnl": round(v["pnl"], 2),
                         "wr": round(v["wins"] / v["n"] * 100, 0) if v["n"] else 0}
                     for k, v in by_strat.items()},
        "by_reason": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)}
                      for k, v in by_reason.items()},
    }


def fmt_result(r):
    return (f"${r['end_capital']:>6.0f} | P&L ${r['pnl']:+7.0f} ({r['pnl_pct']:+6.1f}%) | "
            f"DD {r['max_dd_pct']:+5.1f}% | {r['n_trades']:>3}t WR {r['win_rate']:.0f}%")


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    print(f"  {len(data)} coins loaded")
    print("Computing sector features...")
    sector_features = compute_sector_features(features, data)
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data ends {end_dt.isoformat()}")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m",  end_dt - relativedelta(months=6)),
        ("3m",  end_dt - relativedelta(months=3)),
    ]

    # A1 filter thresholds to sweep (BTC 30d in bps — 1000 = 10%)
    a1_thresholds = [500, 1000, 1500, 2000]

    print(f"\n{'='*98}")
    print(f"Backtest recommendations — v{VERSION} — data thru {end_dt.date()}")
    print(f"{'='*98}\n")

    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        print(f"\n──── Window: {label}  ({start_dt.date()} → {end_dt.date()}) ────")

        base = run_window(features, data, sector_features, start_ts, latest_ts)
        print(f"  baseline         : {fmt_result(base)}")

        b1 = run_window(features, data, sector_features, start_ts, latest_ts,
                        inverse_exit=True)
        dp = b1["pnl"] - base["pnl"]
        dd = b1["max_dd_pct"] - base["max_dd_pct"]
        inv_trades = b1.get("by_reason", {}).get("inverse", {"n": 0, "pnl": 0})
        print(f"  + B1 inverse-exit: {fmt_result(b1)} | Δ${dp:+.0f} ΔDD {dd:+.1f}pp | "
              f"inv {inv_trades['n']}t ${inv_trades['pnl']:+.0f}")

        best_a1 = None
        for th in a1_thresholds:
            r = run_window(features, data, sector_features, start_ts, latest_ts,
                           s5_regime_min_btc30=th)
            dp = r["pnl"] - base["pnl"]
            dd = r["max_dd_pct"] - base["max_dd_pct"]
            s5 = r["by_strat"].get("S5", {"n": 0, "pnl": 0})
            s5_base = base["by_strat"].get("S5", {"n": 0, "pnl": 0})
            print(f"  + A1 |btc30|>{th:>4}: {fmt_result(r)} | Δ${dp:+.0f} ΔDD {dd:+.1f}pp | "
                  f"S5 {s5_base['n']}→{s5['n']}t ${s5_base['pnl']:+.0f}→${s5['pnl']:+.0f}")
            if best_a1 is None or r["pnl"] > best_a1[1]["pnl"]:
                best_a1 = (th, r)

        if best_a1:
            both = run_window(features, data, sector_features, start_ts, latest_ts,
                              inverse_exit=True, s5_regime_min_btc30=best_a1[0])
            dp = both["pnl"] - base["pnl"]
            dd = both["max_dd_pct"] - base["max_dd_pct"]
            print(f"  + B1 + A1(best)  : {fmt_result(both)} | Δ${dp:+.0f} ΔDD {dd:+.1f}pp")


if __name__ == "__main__":
    main()
