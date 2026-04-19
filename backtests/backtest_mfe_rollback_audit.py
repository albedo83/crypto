"""Audit of 'winners that rolled back' — MFE >= 300 bps then closed losing.

Question: is there a detectable signal at MFE peak (OI delta flipping, sector
divergence losing magnitude, BTC regime shift...) that would let us lock gains
via a targeted exit filter *without* triggering on real winners?

Method:
1. Instrument the backtest to track, per trade: MFE peak candle, sector
   divergence at entry / peak / exit, OI delta at entry / peak / exit.
2. Filter to (MFE >= 300 bps AND final net < 0) — the losers-that-were-winners.
3. Compare feature values at peak vs trades that kept their MFE (real winners).
4. Look for an asymmetric cutoff: a rule that would exit most rollbacks without
   hitting the winners' peaks.

Reads from 28m window for statistical power.
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


def run_instrumented(features, data, sector_features, oi_data,
                     start_ts_ms, end_ts_ms, start_capital=1000.0):
    """Like run_window but captures per-trade telemetry at MFE peak."""
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

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    def capture_snapshot(coin, ts, direction):
        """Sector divergence, OI delta, BTC regime at ts for coin."""
        snap = {"ts": ts}
        sf = sector_features.get((ts, coin))
        if sf:
            snap["div_signed"] = sf["divergence"] * direction  # positive means signal still strong
            snap["vol_z"] = sf["vol_z"]
        else:
            snap["div_signed"] = 0.0
            snap["vol_z"] = 0.0
        if oi_data is not None:
            oi_d = oi_delta_24h_pct(oi_data, coin, ts)
            snap["oi_delta"] = oi_d if oi_d is not None else 0.0
        else:
            snap["oi_delta"] = 0.0
        snap["btc7"] = btc_ret(ts, 42)
        snap["btc30"] = btc_ret(ts, 180)
        return snap

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
                pos["mfe_peak_ts"] = ts
                pos["peak_snap"] = capture_snapshot(coin, ts, pos["dir"])
            if worst_bps < pos.get("mae", 0):
                pos["mae"] = worst_bps

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
                exit_snap = capture_snapshot(coin, ts, pos["dir"])
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                    "mfe": pos.get("mfe", 0), "mae": pos.get("mae", 0),
                    "mfe_peak_ts": pos.get("mfe_peak_ts"),
                    "peak_snap": pos.get("peak_snap", {}),
                    "entry_snap": pos.get("entry_snap", {}),
                    "exit_snap": exit_snap,
                    "hold_candles": held,
                })
                del positions[coin]
                cooldown[coin] = ts + COOLDOWN_HOURS * 3600 * 1000

        # ── ENTRIES ──  (using same logic as backtest_rolling)
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
                if oi_d is not None and oi_d < -OI_LONG_GATE_BPS:
                    continue
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
            entry_snap = capture_snapshot(coin, ts, cand["dir"])
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0, "mae": 0.0,
                "entry_snap": entry_snap,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

    return trades


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    start_dt_28 = end_dt - relativedelta(months=28)
    start_ts = int(start_dt_28.timestamp() * 1000)

    print("Running instrumented backtest on 28m window…")
    trades = run_instrumented(features, data, sector_features, oi_data, start_ts, latest_ts)
    print(f"Captured {len(trades)} trades\n")

    # Focus on S5 (where rollbacks hurt most) with MFE >= 300
    MFE_THRESH = 300.0
    s5_big_mfe = [t for t in trades
                  if t["strat"] == "S5" and t["mfe"] >= MFE_THRESH]
    rollbacks = [t for t in s5_big_mfe if t["net"] < 0]
    kept = [t for t in s5_big_mfe if t["net"] >= 0]

    print(f"S5 trades with MFE ≥ {MFE_THRESH} bps: {len(s5_big_mfe)}")
    print(f"  → kept positive (real winners): {len(kept)}")
    print(f"  → rolled back to net<0 (our targets): {len(rollbacks)}")
    if not s5_big_mfe:
        return

    print(f"\nRollbacks total PnL: ${sum(t['pnl'] for t in rollbacks):+.0f}")
    print(f"Kept winners total PnL: ${sum(t['pnl'] for t in kept):+.0f}\n")

    def stats(label, subset, key_fn):
        vals = [key_fn(t) for t in subset if key_fn(t) is not None]
        if not vals:
            print(f"  {label}: no data")
            return
        arr = np.array(vals)
        print(f"  {label:<32} n={len(arr):3} mean={arr.mean():+8.2f} "
              f"med={np.median(arr):+8.2f}  p25={np.percentile(arr,25):+8.2f}  "
              f"p75={np.percentile(arr,75):+8.2f}")

    def safe_get(t, side, field):
        return t.get(f"{side}_snap", {}).get(field)

    print("=" * 100)
    print("FEATURE COMPARISON: rollbacks vs kept winners (same pool: S5, MFE ≥ 300)")
    print("=" * 100)

    for field in ["div_signed", "vol_z", "oi_delta", "btc7", "btc30"]:
        print(f"\n--- {field} ---")
        for side in ["entry", "peak", "exit"]:
            print(f"  [{side}]")
            stats(f"    rollbacks {side}", rollbacks, lambda t, s=side, f=field: safe_get(t, s, f))
            stats(f"    kept      {side}", kept,      lambda t, s=side, f=field: safe_get(t, s, f))

    # ── Key question: is there a peak-time signal that flips? ──
    print("\n" + "=" * 100)
    print("Δ (peak → exit) : how much did each feature move from peak to exit?")
    print("=" * 100)
    for field in ["div_signed", "oi_delta", "btc7"]:
        print(f"\n--- Δ {field} (peak → exit) ---")
        def delta(t, fld):
            p = safe_get(t, "peak", fld)
            e = safe_get(t, "exit", fld)
            if p is None or e is None: return None
            return e - p
        stats(f"  rollbacks", rollbacks, lambda t, f=field: delta(t, f))
        stats(f"  kept     ", kept,      lambda t, f=field: delta(t, f))

    # ── Specific hypothesis: divergence flipped sign at peak? ──
    print("\n" + "=" * 100)
    print("HYPOTHESIS: sector divergence weakened significantly at MFE peak")
    print("=" * 100)
    for threshold in [0, 200, 400, 600, 800]:
        # Flag: at peak, signed div dropped below threshold (i.e., the sector signal weakened)
        rollback_flagged = sum(1 for t in rollbacks
                               if safe_get(t, "peak", "div_signed") is not None
                               and safe_get(t, "peak", "div_signed") <= threshold)
        kept_flagged = sum(1 for t in kept
                           if safe_get(t, "peak", "div_signed") is not None
                           and safe_get(t, "peak", "div_signed") <= threshold)
        print(f"  At peak, signed_div <= {threshold:>4}: "
              f"rollbacks {rollback_flagged}/{len(rollbacks)} "
              f"({rollback_flagged*100/max(len(rollbacks),1):.0f}%), "
              f"kept {kept_flagged}/{len(kept)} "
              f"({kept_flagged*100/max(len(kept),1):.0f}%)")


if __name__ == "__main__":
    main()
