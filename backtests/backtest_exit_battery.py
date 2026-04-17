"""Exit battery — test 4 loss-mitigation exit rules on walk-forward.

1. ATR-adaptive stop: stop = -k × ATR(N candles), replaces flat stop
2. Breakeven: once MFE >= trig_bps, stop moves to 0 (or small offset for costs)
3. OI exit: close held LONG if Δ(OI, 24h) < -th (mirror of v11.4.9 entry gate)
4. MAE cry-uncle: close if |unrealized| stays below -mae_th for >= stale_candles

Each variant tested alone vs baseline on 28m/12m/6m/3m. A variant is VALID if
it improves P&L on ≥3 of 4 windows.

All other exit rules (timeout, S9 early, S10 trailing) remain active in all
variants — we only ADD an exit condition or REPLACE the flat stop.
"""
from __future__ import annotations

from bisect import bisect_right
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
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
    OI_LONG_GATE_BPS,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    detect_squeeze, strat_size, COST,
    HOLD_CANDLES, S9_EARLY_EXIT_CANDLES,
)
from backtests.backtest_external_gates import load_funding, load_oi, oi_delta_24h_pct


def atr_bps(candles, idx, window=6):
    """Average true range in bps over last `window` candles ending at idx."""
    if idx < window:
        return None
    total = 0.0
    for k in range(idx - window + 1, idx + 1):
        c = candles[k]
        if c["c"] <= 0:
            return None
        rng = (c["h"] - c["l"]) / c["c"] * 1e4
        total += rng
    return total / window


def run_window(features, data, sector_features, oi_data,
               start_ts_ms, end_ts_ms, start_capital=1000.0,
               variant=None, args=None):
    """Run backtest with v11.4.9 rules (OI gate on entry) + optional exit variant.

    variant in: None, "atr", "breakeven", "oi_exit", "cry_uncle"
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
    variant_exits = 0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        # ── Exits ──
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

            ur = pos["dir"] * (current / pos["entry"] - 1) * 1e4
            # Track MAE (worst unrealized)
            if ur < pos.get("mae", 0):
                pos["mae"] = ur
                pos["mae_candle"] = ci

            # ── Variant-specific stop override or extra exit ──
            stop_override = None
            extra_exit_reason = None

            if variant == "atr":
                # ATR-adaptive stop (replaces the flat strat stop entirely)
                atr = atr_bps(data[coin], pos["idx"], args["window"])
                if atr is not None:
                    stop_override = -args["k"] * atr

            if variant == "breakeven" and pos.get("mfe", 0) >= args["trig_bps"]:
                # Move stop to breakeven (with optional offset for costs)
                stop_override = -args.get("offset_bps", 0)

            if variant == "oi_exit" and pos["dir"] == 1:
                # If OI has fallen hard while we hold LONG → exit
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                if oi_d is not None and oi_d < -args["th"]:
                    extra_exit_reason = "oi_exit"

            if variant == "cry_uncle":
                # If MAE worse than th and no recovery for stale_candles → exit
                if pos.get("mae", 0) <= -args["mae_th"]:
                    mae_ci = pos.get("mae_candle", ci)
                    if ci - mae_ci >= args["stale"] and ur < 0:
                        extra_exit_reason = "cry_uncle"

            # ── Standard stop (flat or overridden) ──
            if pos["strat"] == "S8":
                stop_default = STOP_LOSS_S8
            elif pos.get("stop", 0) != 0:
                stop_default = pos["stop"]
            else:
                stop_default = STOP_LOSS_BPS

            # When ATR variant active, use stop_override exclusively; when
            # breakeven variant, use the TIGHTER of (override, default).
            if variant == "atr" and stop_override is not None:
                stop = stop_override
            elif variant == "breakeven" and stop_override is not None:
                stop = max(stop_override, stop_default)  # tighter (less negative)
            else:
                stop = stop_default

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

            if not exit_reason and extra_exit_reason:
                exit_reason = extra_exit_reason
                exit_price = current
                variant_exits += 1

            if not exit_reason and held >= pos["hold"]:
                exit_reason = "timeout"
            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                if ur < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"
            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
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
                               "entry_t": pos["entry_t"], "exit_t": ts,
                               "reason": exit_reason, "size": pos["size"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Candidates ──
        candidates = []
        for coin in coins:
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            ret_24h = f.get("ret_6h", 0)
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                                   "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                   "strength": max(f.get("ret_42h", 0), 0)})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({"coin": coin,
                                   "dir": 1 if sf["divergence"] > 0 else -1,
                                   "strat": "S5",
                                   "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
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
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    blocked = ((not S10_ALLOW_LONGS and sq_dir == 1)
                               or coin not in S10_ALLOWED_TOKENS)
                    if not blocked:
                        candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                                           "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                           "strength": 1000})

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
            size = strat_size(cand["strat"], capital)
            positions[coin] = {"dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                               "entry_t": data[coin][idx_f + 1]["t"],
                               "strat": cand["strat"], "hold": cand["hold"],
                               "size": size, "coin": coin,
                               "stop": cand.get("stop", 0),
                               "mfe": 0.0, "mae": 0.0, "mae_candle": idx_f + 1}
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
                           "entry_t": pos["entry_t"], "exit_t": last_ts,
                           "reason": "mtm_final", "size": pos["size"]})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    avg_loss = sum(losses) / len(losses) if losses else 0
    worst_loss = min(losses) if losses else 0
    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1

    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "avg_loss": avg_loss,
        "worst_loss": worst_loss,
        "n_losses": len(losses),
        "variant_exits": variant_exits,
        "by_strat": {k: {"n": v["n"], "pnl": round(v["pnl"], 2),
                         "wr": round(v["wins"] / v["n"] * 100, 0) if v["n"] else 0}
                     for k, v in by_strat.items()},
    }


def fmt(r):
    return (f"${r['end_capital']:>6.0f} | P&L ${r['pnl']:+7.0f} | DD {r['max_dd_pct']:+5.1f}% | "
            f"{r['n_trades']:>3}t WR {r['win_rate']:.0f}% | "
            f"L avg ${r['avg_loss']:+.1f} worst ${r['worst_loss']:+.0f}")


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

    # Baseline = v11.4.9 (OI gate on entry, no exit variant)
    print("\nBaseline (v11.4.9, no exit variant):")
    baselines = {}
    for lb, sd in windows:
        r = run_window(features, data, sector_features, oi_data,
                       int(sd.timestamp() * 1000), latest_ts)
        baselines[lb] = r
        print(f"  {lb:4}: {fmt(r)}")

    print(f"\n{'='*140}")
    print(f"Exit battery — v{VERSION} — data thru {end_dt.date()}")
    print(f"{'='*140}")

    # ── Variant 1: ATR stops ──
    print("\n── ATR-adaptive stops (stop = -k × ATR over window candles) ──")
    for args in [
        {"k": 1.5, "window": 6},  {"k": 2.0, "window": 6},  {"k": 2.5, "window": 6},
        {"k": 3.0, "window": 6},  {"k": 2.0, "window": 12}, {"k": 2.5, "window": 12},
        {"k": 3.0, "window": 12}, {"k": 4.0, "window": 6},
    ]:
        deltas = []
        wins = 0
        for lb, sd in windows:
            r = run_window(features, data, sector_features, oi_data,
                           int(sd.timestamp() * 1000), latest_ts,
                           variant="atr", args=args)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins == 3 else " ")
        print(f"  {status} k={args['k']:<4} w={args['window']:<2}: "
              + " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp"
                         for lb, dp, dd, _ in deltas))

    # ── Variant 2: Breakeven ──
    print("\n── Breakeven stop (stop → -offset once MFE >= trig_bps) ──")
    for args in [
        {"trig_bps": 400, "offset_bps": 0},   {"trig_bps": 400, "offset_bps": 50},
        {"trig_bps": 500, "offset_bps": 0},   {"trig_bps": 500, "offset_bps": 50},
        {"trig_bps": 600, "offset_bps": 0},   {"trig_bps": 600, "offset_bps": 50},
        {"trig_bps": 800, "offset_bps": 0},   {"trig_bps": 800, "offset_bps": 100},
        {"trig_bps": 1000, "offset_bps": 0},
    ]:
        deltas = []
        wins = 0
        for lb, sd in windows:
            r = run_window(features, data, sector_features, oi_data,
                           int(sd.timestamp() * 1000), latest_ts,
                           variant="breakeven", args=args)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins == 3 else " ")
        print(f"  {status} trig={args['trig_bps']:<4} off={args['offset_bps']:<3}: "
              + " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp"
                         for lb, dp, dd, _ in deltas))

    # ── Variant 3: OI exit for LONG ──
    print("\n── OI exit (close LONG if Δ(OI,24h) < -th while holding) ──")
    for args in [
        {"th": 700}, {"th": 1000}, {"th": 1200}, {"th": 1500},
        {"th": 2000}, {"th": 2500},
    ]:
        deltas = []
        wins = 0
        for lb, sd in windows:
            r = run_window(features, data, sector_features, oi_data,
                           int(sd.timestamp() * 1000), latest_ts,
                           variant="oi_exit", args=args)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins == 3 else " ")
        exits = " ".join(f"{lb}:{r['variant_exits']}" for lb, _, _, r in deltas)
        print(f"  {status} th={args['th']:<4}: "
              + " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp" for lb, dp, dd, _ in deltas)
              + f"  exits {exits}")

    # ── Variant 4: MAE cry-uncle ──
    print("\n── MAE cry-uncle (exit if MAE ≤ -mae_th for stale candles, still underwater) ──")
    for args in [
        {"mae_th": 400, "stale": 4},  {"mae_th": 600, "stale": 4},
        {"mae_th": 800, "stale": 4},  {"mae_th": 600, "stale": 6},
        {"mae_th": 800, "stale": 6},  {"mae_th": 1000, "stale": 6},
        {"mae_th": 800, "stale": 8},  {"mae_th": 1000, "stale": 8},
    ]:
        deltas = []
        wins = 0
        for lb, sd in windows:
            r = run_window(features, data, sector_features, oi_data,
                           int(sd.timestamp() * 1000), latest_ts,
                           variant="cry_uncle", args=args)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins == 3 else " ")
        exits = " ".join(f"{lb}:{r['variant_exits']}" for lb, _, _, r in deltas)
        print(f"  {status} mae={args['mae_th']:<4} stale={args['stale']}: "
              + " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp" for lb, dp, dd, _ in deltas)
              + f"  exits {exits}")


if __name__ == "__main__":
    main()
