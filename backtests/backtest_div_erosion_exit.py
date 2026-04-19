"""Walk-forward sweep for piste 1 — exit S5 when sector divergence erodes.

Rollback audit showed rollbacks lose -612 bps of div from peak to exit, vs
-229 for kept winners (2.7x more). A rule that exits when current divergence
drops significantly below the in-trade peak might preserve gains.

Variants tested:
- E1a/b/c: exit S5 when current div <= peak_div - X (X = 400, 600, 800 bps)
  and unrealized >= 0 (only protect positive trades)
- E2a/b/c: exit S5 when current div <= peak_div * ratio (ratio = 0.5, 0.6, 0.7)
  and unrealized >= 0
- E3a/b/c: exit S5 when current div <= 0 (flipped sign)
- Combined with D2: E1b + D2 (best dead_timeout)

Pass criteria: 4/4 positive + DD stable.
"""
from __future__ import annotations

import numpy as np
from collections import defaultdict
from datetime import datetime, timezone

from analysis.bot.config import (
    MACRO_STRATEGIES, TRADE_BLACKLIST,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
    STOP_LOSS_BPS, STOP_LOSS_S8,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, COOLDOWN_HOURS,
    MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS,
    OI_LONG_GATE_BPS, TOKEN_SECTOR,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S9_ADAPTIVE_STOP,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    rolling_windows, load_dxy, load_oi, oi_delta_24h_pct,
    detect_squeeze, strat_size, COST,
    HOLD_CANDLES, STRAT_Z, S9_EARLY_EXIT_CANDLES, S9_EARLY_EXIT_BPS,
)
from dateutil.relativedelta import relativedelta


def run_with_div_exit(features, data, sector_features, oi_data,
                      start_ts_ms, end_ts_ms,
                      div_exit: dict | None,
                      early_exit_params: dict | None = None,
                      start_capital: float = 1000.0):
    """Like run_window but with optional div-erosion exit for S5 trades."""
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
        if ts not in btc_by_ts: return 0.0
        i = btc_by_ts[ts]
        if i < lookback or btc_closes[i - lookback] <= 0: return 0.0
        return (btc_closes[i] / btc_closes[i - lookback] - 1) * 1e4

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0.0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

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

            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
                worst_bps = (candle["l"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
                worst_bps = -(candle["h"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0):
                pos["mfe"] = best_bps
            if worst_bps < pos.get("mae", 0):
                pos["mae"] = worst_bps

            # Track sector divergence aligned with position direction
            if pos["strat"] == "S5":
                sf = sector_features.get((ts, coin))
                if sf:
                    signed_div = sf["divergence"] * pos["dir"]
                    if signed_div > pos.get("peak_div", 0):
                        pos["peak_div"] = signed_div
                    pos["current_div"] = signed_div

            stop = (STOP_LOSS_S8 if pos["strat"] == "S8"
                    else pos["stop"] if pos.get("stop", 0) != 0
                    else STOP_LOSS_BPS)

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

            # Dead timeout (D2 from previous step)
            if (not exit_reason and early_exit_params is not None
                    and held >= pos["hold"] - early_exit_params["exit_lead_candles"]):
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                mfe = pos.get("mfe", 0.0)
                mae = pos.get("mae", 0.0)
                if (mfe <= early_exit_params["mfe_cap_bps"]
                        and mae <= early_exit_params["mae_floor_bps"]
                        and cur_bps <= mae + early_exit_params["slack_bps"]):
                    exit_reason = "dead_timeout"
                    exit_price = current

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

            # ── DIV EROSION EXIT (piste 1) ──
            if not exit_reason and pos["strat"] == "S5" and div_exit is not None:
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                peak_div = pos.get("peak_div", 0)
                cur_div = pos.get("current_div", peak_div)
                # Apply only once unrealized crossed the gain floor
                if cur_bps >= div_exit.get("min_gain_bps", 300):
                    trigger = False
                    if "max_drop_bps" in div_exit:
                        if cur_div <= peak_div - div_exit["max_drop_bps"]:
                            trigger = True
                    if "max_ratio" in div_exit:
                        if peak_div > 0 and cur_div <= peak_div * div_exit["max_ratio"]:
                            trigger = True
                    if div_exit.get("flip_only") and cur_div < 0:
                        trigger = True
                    if trigger:
                        exit_reason = "div_erosion"
                        exit_price = current

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                    "mfe": pos.get("mfe", 0), "mae": pos.get("mae", 0),
                })
                del positions[coin]
                cooldown[coin] = ts + COOLDOWN_HOURS * 3600 * 1000

        # ── ENTRIES ──
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
                                   "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                   "strength": max(f.get("ret_42h", 0), 0)})

            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({"coin": coin, "dir": 1 if sf["divergence"] > 0 else -1,
                                   "strat": "S5", "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                   "strength": abs(sf["divergence"])})

            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                                   "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                                   "strength": abs(f["drawdown"])})

            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                                   "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                                   "strength": abs(ret_24h), "stop": s9_stop})

            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci_e = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci_e, f.get("vol_ratio", 2))
                if sq_dir:
                    s10_block = ((not S10_ALLOW_LONGS and sq_dir == 1)
                                 or coin not in S10_ALLOWED_TOKENS)
                    if not s10_block:
                        candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                                           "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                           "strength": 1000})

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions: continue
            seen.add(coin)
            if coin in TRADE_BLACKLIST: continue
            if cand["dir"] == 1 and oi_data is not None:
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                if oi_d is not None and oi_d < -OI_LONG_GATE_BPS: continue
            if len(positions) >= MAX_POSITIONS: break
            if cand["dir"] == 1 and n_long >= MAX_SAME_DIRECTION: continue
            if cand["dir"] == -1 and n_short >= MAX_SAME_DIRECTION: continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO_SLOTS: continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN_SLOTS: continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_PER_SECTOR: continue

            f = feat_by_ts.get(ts, {}).get(coin)
            idx_f = f.get("_idx") if f else None
            if idx_f is None or idx_f + 1 >= len(data[coin]): continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0: continue

            size = strat_size(cand["strat"], capital)
            # Seed peak_div with entry divergence if S5
            peak_div_init = 0.0
            if cand["strat"] == "S5":
                sf_e = sector_features.get((ts, coin))
                if sf_e:
                    peak_div_init = abs(sf_e["divergence"])  # same sign as dir
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0, "mae": 0.0,
                "peak_div": peak_div_init, "current_div": peak_div_init,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

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
            trades.append({
                "pnl": pnl, "net": net, "dir": pos["dir"],
                "strat": pos["strat"], "coin": coin,
                "entry_t": pos["entry_t"], "exit_t": last_ts,
                "reason": "mtm_final", "size": pos["size"],
            })

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "end_capital": capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "trades": trades,
    }


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.strftime('%Y-%m-%d')}\n")

    WIN_LABELS = {"28 mois", "12 mois", "6 mois", "3 mois"}
    labels = ["28 mois", "12 mois", "6 mois", "3 mois"]
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    D2 = dict(exit_lead_candles=3, mfe_cap_bps=150, mae_floor_bps=-1000, slack_bps=300)

    VARIANTS = [
        ("BASELINE (D2 already in live)", None, D2),
        ("E1a: drop>=400 bps, gain>=300",  dict(max_drop_bps=400, min_gain_bps=300), D2),
        ("E1b: drop>=600 bps, gain>=300",  dict(max_drop_bps=600, min_gain_bps=300), D2),
        ("E1c: drop>=800 bps, gain>=300",  dict(max_drop_bps=800, min_gain_bps=300), D2),
        ("E2a: ratio<=0.5, gain>=300",     dict(max_ratio=0.5, min_gain_bps=300), D2),
        ("E2b: ratio<=0.6, gain>=300",     dict(max_ratio=0.6, min_gain_bps=300), D2),
        ("E2c: ratio<=0.7, gain>=300",     dict(max_ratio=0.7, min_gain_bps=300), D2),
        ("E3a: flip (div<0), gain>=300",   dict(flip_only=True, min_gain_bps=300), D2),
        ("E4a: drop>=600 bps, gain>=500",  dict(max_drop_bps=600, min_gain_bps=500), D2),
        ("E4b: drop>=800 bps, gain>=500",  dict(max_drop_bps=800, min_gain_bps=500), D2),
    ]

    results = {}
    for name, div_exit, early_exit in VARIANTS:
        results[name] = {}
        print(f"  {name}…")
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_with_div_exit(features, data, sector_features, oi_data,
                                   start_ts, latest_ts, div_exit, early_exit)
            results[name][label] = r

    base = results["BASELINE (D2 already in live)"]
    print(f"\nBaseline (D2 exit only): 28m=${base['28 mois']['end_capital']:.0f} "
          f"12m=${base['12 mois']['end_capital']:.0f} "
          f"6m=${base['6 mois']['end_capital']:.0f} "
          f"3m=${base['3 mois']['end_capital']:.0f}\n")

    print("=" * 120)
    print(f"{'Variant':<42} {'28m Δ':>10} {'12m Δ':>10} {'6m Δ':>10} {'3m Δ':>10}   DDs 28/12/6/3   Pass")
    print("-" * 120)
    for name, _, _ in VARIANTS:
        r = results[name]
        row = f"{name:<42}"
        all_positive = True
        dd_ok = True
        for w in labels:
            if name.startswith("BASELINE"):
                row += f"  ${r[w]['end_capital']:>7.0f}"
            else:
                delta = r[w]["end_capital"] - base[w]["end_capital"]
                row += f"  {delta:+8.0f}"
                if delta <= 0:
                    all_positive = False
                if r[w]["max_dd_pct"] < base[w]["max_dd_pct"] - 2.0:
                    dd_ok = False
        dd_str = " / ".join(f"{r[w]['max_dd_pct']:+5.1f}%" for w in labels)
        if name.startswith("BASELINE"):
            verdict = "baseline"
        else:
            verdict = "✓ PASS" if (all_positive and dd_ok) else ("+" if all_positive else "-")
        print(f"{row}   {dd_str}   {verdict}")

    print("\n" + "=" * 120)
    passers = [n for n, _, _ in VARIANTS if not n.startswith("BASELINE")
               and all(results[n][w]["end_capital"] > base[w]["end_capital"] for w in labels)
               and all(results[n][w]["max_dd_pct"] >= base[w]["max_dd_pct"] - 2.0 for w in labels)]
    if passers:
        print("PASSING variants:")
        for p in passers:
            tot = sum(results[p][w]["end_capital"] - base[w]["end_capital"] for w in labels)
            print(f"  ✓ {p}  (cumulative Δ = ${tot:+.0f})")
    else:
        print("No variant passes 4/4 + DD stable. div_erosion exit isn't a free lunch.")


if __name__ == "__main__":
    main()
