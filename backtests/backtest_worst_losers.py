"""Autopsy of the worst losing trades.

Runs v11.4.9 baseline on 28m, identifies the 20 worst losses, and dumps their
full entry context (BTC regime, funding, OI, vol_z, drawdown, hold hours,
reason, MAE/MFE). Looks for exploitable patterns.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
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
from backtests.backtest_external_gates import load_oi, load_funding, oi_delta_24h_pct, lookup_funding


def run_and_capture(features, data, sector_features, oi_data, funding_data,
                    start_ts_ms, end_ts_ms, start_capital=1000.0):
    """Same engine as v11.4.9 but captures rich entry context per trade."""
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

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)
        btc24 = btc_ret(ts, 6)

        # Exits
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
            if ur < pos.get("mae", 0):
                pos["mae"] = ur

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
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                    "hold_h": held * 4,
                    "mae": pos.get("mae", 0), "mfe": pos.get("mfe", 0),
                    # Entry context
                    "btc30": pos["btc30"], "btc7": pos["btc7"], "btc24": pos["btc24"],
                    "ret_24h": pos["ret_24h"], "vol_z": pos["vol_z"],
                    "drawdown": pos["drawdown"],
                    "oi_d24": pos["oi_d24"],
                    "funding": pos["funding"], "premium": pos["premium"],
                    "hour_utc": pos["hour_utc"],
                    "sector": pos["sector"],
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # Entries (with v11.4.9 OI gate)
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
            # Capture rich entry context
            oi_d = oi_delta_24h_pct(oi_data, coin, ts)
            fund, prem = lookup_funding(funding_data, coin, ts)
            hour = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0), "mfe": 0.0, "mae": 0.0,
                "btc30": btc30, "btc7": btc7, "btc24": btc24,
                "ret_24h": f.get("ret_6h", 0),
                "vol_z": f.get("vol_z", 0),
                "drawdown": f.get("drawdown", 0),
                "oi_d24": oi_d if oi_d is not None else 0,
                "funding": fund if fund is not None else 0,
                "premium": prem if prem is not None else 0,
                "hour_utc": hour,
                "sector": TOKEN_SECTOR.get(coin, "—"),
            }
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1

    # MTM remaining
    for coin in list(positions.keys()):
        pos = positions[coin]
        last_ts = max(t for t in coin_by_ts[coin] if t <= end_ts_ms)
        last_idx = coin_by_ts[coin][last_ts]
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            trades.append({
                "pnl": pnl, "net": net, "dir": pos["dir"],
                "strat": pos["strat"], "coin": coin,
                "entry_t": pos["entry_t"], "exit_t": last_ts,
                "reason": "mtm_final", "size": pos["size"],
                "hold_h": 0,
                "mae": pos.get("mae", 0), "mfe": pos.get("mfe", 0),
                "btc30": pos["btc30"], "btc7": pos["btc7"], "btc24": pos["btc24"],
                "ret_24h": pos["ret_24h"], "vol_z": pos["vol_z"],
                "drawdown": pos["drawdown"], "oi_d24": pos["oi_d24"],
                "funding": pos["funding"], "premium": pos["premium"],
                "hour_utc": pos["hour_utc"], "sector": pos["sector"],
            })

    return trades


def main():
    print("Loading...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()
    funding_data = load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    start_dt = end_dt - relativedelta(months=28)
    start_ts = int(start_dt.timestamp() * 1000)
    print(f"Running v{VERSION} baseline {start_dt.date()} → {end_dt.date()}...")

    trades = run_and_capture(features, data, sector_features, oi_data, funding_data,
                             start_ts, latest_ts)
    losers = sorted([t for t in trades if t["pnl"] < 0], key=lambda t: t["pnl"])
    winners = [t for t in trades if t["pnl"] > 0]
    print(f"  {len(trades)} trades, {len(losers)} losers, {len(winners)} winners")

    total_pnl = sum(t["pnl"] for t in trades)
    total_loss = sum(t["pnl"] for t in losers)
    print(f"  total P&L ${total_pnl:+.0f}, total loss contribution ${total_loss:+.0f}")

    # Top 20 worst
    print(f"\n{'='*180}")
    print(f"TOP 20 WORST TRADES")
    print(f"{'='*180}")
    hdr = ("#    date        coin  strat dir  size    hold  pnl       net    mae    mfe  reason         "
           "btc30 btc7 btc24 ret24 vol_z dd      oi24   fund  prem hour sector")
    print(hdr)
    for i, t in enumerate(losers[:20], 1):
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        dir_s = "L" if t["dir"] == 1 else "S"
        print(f"{i:2}  {dt.strftime('%Y-%m-%d'):11} "
              f"{t['coin']:5} {t['strat']:4} {dir_s}    "
              f"${t['size']:>6.0f}  {t['hold_h']:>4.0f}h "
              f"${t['pnl']:>+8.1f} {t['net']:>+7.0f} "
              f"{t['mae']:>+6.0f} {t['mfe']:>+6.0f} "
              f"{t['reason']:13} "
              f"{t['btc30']:>+5.0f} {t['btc7']:>+4.0f} {t['btc24']:>+5.0f} "
              f"{t['ret_24h']:>+5.0f} {t['vol_z']:>+4.1f} "
              f"{t['drawdown']:>+6.0f} "
              f"{t['oi_d24']:>+6.0f} "
              f"{t['funding']:>+4.1f} {t['premium']:>+4.1f} "
              f"{t['hour_utc']:>3}  {t['sector']}")

    # Pattern analysis on top 50
    top = losers[:50]
    print(f"\n{'='*120}")
    print(f"PATTERNS in top 50 losers (vs top 50 winners + vs all trades)")
    print(f"{'='*120}")

    def stats(subset, label):
        if not subset:
            print(f"  {label}: empty")
            return
        strat_c = Counter(t["strat"] for t in subset)
        dir_c = Counter("LONG" if t["dir"] == 1 else "SHORT" for t in subset)
        coin_c = Counter(t["coin"] for t in subset)
        sector_c = Counter(t["sector"] for t in subset)
        reason_c = Counter(t["reason"] for t in subset)
        hour_c = Counter(t["hour_utc"] // 4 * 4 for t in subset)  # bucketed
        vol_z_mean = np.mean([t["vol_z"] for t in subset])
        btc30_mean = np.mean([t["btc30"] for t in subset])
        btc7_mean = np.mean([t["btc7"] for t in subset])
        oi_mean = np.mean([t["oi_d24"] for t in subset])
        fund_mean = np.mean([t["funding"] for t in subset])
        hold_mean = np.mean([t["hold_h"] for t in subset])
        mae_mean = np.mean([t["mae"] for t in subset])
        mfe_mean = np.mean([t["mfe"] for t in subset])

        print(f"\n  {label} (n={len(subset)}):")
        print(f"    strategy: {dict(strat_c.most_common())}")
        print(f"    direction: {dict(dir_c)}")
        print(f"    top coins: {dict(coin_c.most_common(8))}")
        print(f"    sectors: {dict(sector_c.most_common())}")
        print(f"    exit reasons: {dict(reason_c.most_common())}")
        print(f"    hour buckets (UTC): {dict(sorted(hour_c.items()))}")
        print(f"    avg context: vol_z={vol_z_mean:+.2f} btc30={btc30_mean:+.0f} "
              f"btc7={btc7_mean:+.0f} oi_d24={oi_mean:+.0f} fund={fund_mean:+.2f} "
              f"hold_h={hold_mean:.0f}")
        print(f"    MAE/MFE: avg mae={mae_mean:+.0f} mfe={mfe_mean:+.0f}")

    stats(top, "TOP 50 LOSERS")
    stats(sorted(trades, key=lambda t: -t["pnl"])[:50], "TOP 50 WINNERS")
    stats(trades, "ALL TRADES")

    # Save losers for further analysis
    with open("/tmp/worst_losers.json", "w") as f:
        json.dump(losers[:50], f, indent=2, default=str)
    print(f"\n  Saved top 50 losers to /tmp/worst_losers.json")

    # Specific coin analysis — is there a coin that's consistently bad?
    print(f"\n{'='*120}")
    print("PER-COIN P&L (all trades, sorted by P&L asc)")
    print(f"{'='*120}")
    per_coin: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0, "worst": 0})
    for t in trades:
        c = per_coin[t["coin"]]
        c["n"] += 1
        c["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            c["wins"] += 1
        else:
            c["losses"] += 1
            if t["pnl"] < c["worst"]:
                c["worst"] = t["pnl"]
    sorted_coins = sorted(per_coin.items(), key=lambda kv: kv[1]["pnl"])
    print(f"  {'coin':6} {'n':>4} {'wr':>4} {'pnl':>10} {'worst':>9}")
    for coin, c in sorted_coins:
        wr = c["wins"] / c["n"] * 100 if c["n"] else 0
        print(f"  {coin:6} {c['n']:>4} {wr:>3.0f}% ${c['pnl']:>+8.0f} ${c['worst']:>+7.0f}")


if __name__ == "__main__":
    main()
