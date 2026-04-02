"""Comprehensive new signal search — S13 (Leader-Follower), S14 (Dispersion Collapse),
S15 (OI as filter on existing signals), S16 (Funding extreme), S17 (Multi-timeframe momentum).

All tested in isolation first, then best candidates validated in portfolio.
Data: 4h candles (24 months overlap with OI) + Bybit OI + features.

Usage:
    python3 -m analysis.backtest_new_signals
"""
from __future__ import annotations
import json, os, time as _time
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from backtests.backtest_sector import compute_sector_features, TOKEN_SECTOR
from backtests.backtest_oi_divergence import load_oi_data, compute_oi_features
from backtests.backtest_sizing_optimal import (
    detect_squeeze, load_dxy, strat_size, STRAT_Z, MACRO_SIGNALS, SIGNALS,
    S10_SQUEEZE_WINDOW, S10_VOL_RATIO_MAX, S10_BREAKOUT_PCT, S10_REINT_CANDLES,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
LEVERAGE = 2.0
COST_EFFECTIVE = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE

SECTORS = {
    "L1": ["SOL","AVAX","SUI","APT","NEAR","SEI"],
    "DeFi": ["AAVE","MKR","CRV","SNX","PENDLE","COMP","DYDX","LDO","GMX"],
    "Gaming": ["GALA","IMX","SAND"],
    "Infra": ["LINK","PYTH","STX","INJ","ARB","OP"],
    "Meme": ["DOGE","WLD","BLUR","MINA"],
}
TOKEN_TO_SECTOR = {}
for sect, toks in SECTORS.items():
    for t in toks:
        TOKEN_TO_SECTOR[t] = sect


def isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts, signal_fn, config):
    """Generic isolation backtest. signal_fn(ts, coin, f, context) -> {dir, hold, stop_bps, strength} or None."""
    hold_default = config.get("hold", 12)
    stop_default = config.get("stop_bps", -2500)
    max_pos = config.get("max_pos", 4)
    start_capital = 1000
    effective_stop_default = stop_default / LEVERAGE

    coins = [c for c in TOKENS if c in data]
    positions = {}; trades = []; cooldown = {}; capital = start_capital

    for ts in all_ts_sorted:
        # EXITS
        for coin in list(positions.keys()):
            pos = positions[coin]
            if ts not in coin_by_ts.get(coin, {}): continue
            ci = coin_by_ts[coin][ts]; held = ci - pos["idx"]
            if held <= 0: continue
            candle = data[coin][ci]; current = candle["c"]
            if current <= 0: continue
            exit_reason = None; exit_price = current
            stop = pos.get("stop_bps", stop_default)
            eff_stop = stop / LEVERAGE
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < eff_stop: exit_reason = "stop"; exit_price = pos["entry"] * (1 + eff_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < eff_stop: exit_reason = "stop"; exit_price = pos["entry"] * (1 - eff_stop / 1e4)
            if held >= pos.get("hold", hold_default): exit_reason = "timeout"
            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - COST_EFFECTIVE; pnl = pos["size"] * net / 1e4; capital += pnl
                trades.append({"pnl": pnl, "net": net, "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts})
                del positions[coin]; cooldown[coin] = ts + 24 * 3600 * 1000

        if len(positions) >= max_pos: continue
        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]): continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue
            sig = signal_fn(ts, coin, f)
            if sig:
                candidates.append({"coin": coin, **sig})
        candidates.sort(key=lambda x: x.get("strength", 0), reverse=True)
        for cand in candidates:
            if len(positions) >= max_pos: break
            coin = cand["coin"]
            if coin in positions: continue
            sym_sector = TOKEN_TO_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_TO_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= 2: continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue
            idx_f = f["_idx"]
            if idx_f + 1 >= len(data[coin]): continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0: continue
            positions[coin] = {"dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"], "hold": cand.get("hold", hold_default),
                "size": capital * 0.12, "coin": coin, "stop_bps": cand.get("stop_bps", stop_default)}

    if not trades: return {"pnl": 0, "n": 0, "avg": 0, "win": 0, "train": 0, "test": 0}
    n = len(trades); pnl = capital - start_capital; wins = sum(1 for t in trades if t["net"] > 0)
    avg = float(np.mean([t["net"] for t in trades]))
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)
    return {"pnl": round(pnl, 0), "n": n, "avg": round(avg, 1), "win": round(wins/n*100, 0),
            "train": round(train_pnl, 0), "test": round(test_pnl, 0)}


def main():
    t0 = _time.time()
    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    oi_data = load_oi_data()
    oi_features = compute_oi_features(oi_data, data)

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
    all_ts_sorted = sorted(all_ts)

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}
    def btc_ret(ts, lookback):
        if ts not in btc_by_ts: return 0
        i = btc_by_ts[ts]
        return (btc_closes[i] / btc_closes[i-lookback] - 1) * 1e4 if i >= lookback and btc_closes[i-lookback] > 0 else 0

    print(f"Data ready: {len(data)} tokens, {len(oi_features):,} OI features ({_time.time()-t0:.0f}s)")

    def print_header(title):
        print(f"\n{'='*100}")
        print(f"  {title}")
        print(f"{'='*100}")
        print(f"  {'Config':<50} {'N':>5} {'W%':>4} {'Avg':>6} {'P&L':>7} {'Train':>6} {'Test':>6} | Valid")
        print(f"  {'-'*95}")

    def print_row(label, r):
        valid = "✓" if r["train"] > 0 and r["test"] > 0 else ""
        print(f"  {label:<50} {r['n']:>4} {r['win']:>3.0f}% {r['avg']:>+5.0f} "
              f"${r['pnl']:>+6,} ${r['train']:>+5,} ${r['test']:>+5,} | {valid}")

    # ═══════════════════════════════════════════════════════════════
    # TEST 1: S13 — Leader-Follower Rotation
    # When sector leader pumps, followers catch up
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 1: S13 — Leader-Follower Rotation")

    # Pre-compute sector leader returns
    sector_leader_cache = {}
    for ts in all_ts_sorted:
        available = feat_by_ts.get(ts, {})
        for sect, toks in SECTORS.items():
            rets = {}
            for t in toks:
                f = available.get(t)
                if f and "ret_6h" in f:
                    rets[t] = f["ret_6h"]
            if rets:
                leader = max(rets, key=rets.get)
                sector_leader_cache[(ts, sect)] = (leader, rets[leader], rets)

    for leader_thresh in [1000, 1500, 2000]:
        for follower_max in [200, 500]:
            for hold in [6, 12, 18]:
                def make_s13(lt, fm, h):
                    def signal(ts, coin, f):
                        sect = TOKEN_TO_SECTOR.get(coin)
                        if not sect: return None
                        info = sector_leader_cache.get((ts, sect))
                        if not info: return None
                        leader, leader_ret, all_rets = info
                        if leader == coin: return None
                        coin_ret = all_rets.get(coin, 0)
                        if leader_ret >= lt and coin_ret <= fm:
                            return {"dir": 1, "hold": h, "strength": leader_ret - coin_ret}
                        if leader_ret <= -lt and coin_ret >= -fm:
                            return {"dir": -1, "hold": h, "strength": abs(leader_ret) - abs(coin_ret)}
                        return None
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s13(leader_thresh, follower_max, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"leader>{leader_thresh} follower<{follower_max} hold={hold*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 2: S14 — Dispersion Collapse → Breakout
    # Low dispersion = compression, next breakout is tradeable
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 2: S14 — Dispersion Collapse → Breakout")

    # Pre-compute cross-sectional dispersion
    disp_history = {}
    for ts in all_ts_sorted:
        available = feat_by_ts.get(ts, {})
        rets = [f.get("ret_42h", 0) for f in available.values() if "ret_42h" in f]
        if len(rets) > 5:
            disp_history[ts] = float(np.std(rets))

    # Compute rolling percentiles
    disp_ts_sorted = sorted(disp_history.keys())
    disp_pctile = {}
    window = 180 * 6  # 180 days in 4h candles
    for i, ts in enumerate(disp_ts_sorted):
        start = max(0, i - window)
        vals = [disp_history[disp_ts_sorted[j]] for j in range(start, i+1)]
        if len(vals) > 50:
            current = disp_history[ts]
            disp_pctile[ts] = float(np.searchsorted(np.sort(vals), current) / len(vals) * 100)

    for disp_max_pctile in [10, 15, 20, 25]:
        for vol_ratio_max in [0.7, 0.8, 0.9]:
            for hold in [6, 12, 18]:
                def make_s14(dp, vrm, h):
                    prev_low = set()
                    def signal(ts, coin, f):
                        pct = disp_pctile.get(ts, 50)
                        vr = f.get("vol_ratio", 1)
                        ret = f.get("ret_6h", 0)
                        if pct <= dp and vr < vrm:
                            prev_low.add(ts)
                            return None
                        # Was compressed, now breaking out?
                        # Check if any of the recent timestamps were low-disp
                        recent_low = any(t in prev_low for t in range(ts - 6*4*3600*1000, ts, 4*3600*1000))
                        if not recent_low: return None
                        if abs(ret) > 500:  # breakout happening
                            return {"dir": 1 if ret > 0 else -1, "hold": h, "strength": abs(ret)}
                        return None
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s14(disp_max_pctile, vol_ratio_max, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"disp<p{disp_max_pctile} vr<{vol_ratio_max} hold={hold*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 3: S15 — OI as filter on existing signals
    # Does adding OI confirmation improve S5/S8/S9?
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 3: S15 — OI Filter on Existing Signals")

    # S5 + OI confirmation
    for oi_dir in ["same", "opposite"]:
        for oi_thresh in [3, 5, 8]:
            for hold in [12, 18]:
                def make_s5_oi(od, ot, h):
                    def signal(ts, coin, f):
                        sf_val = sf.get((ts, coin))
                        if not sf_val: return None
                        if abs(sf_val["divergence"]) < 1000 or sf_val["vol_z"] < 1.0: return None
                        oi_f = oi_features.get((ts, coin))
                        if not oi_f: return None
                        oi_d = oi_f.get("oi_delta_24h", 0)
                        d = 1 if sf_val["divergence"] > 0 else -1
                        if od == "same" and d * oi_d < ot: return None
                        if od == "opposite" and d * oi_d > -ot: return None
                        return {"dir": d, "hold": h, "strength": abs(sf_val["divergence"])}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s5_oi(oi_dir, oi_thresh, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"S5+OI_{oi_dir}>{oi_thresh}% hold={hold*4}h", r)

    # S9 + OI filter
    for oi_mode in ["high_oi", "low_oi"]:
        for oi_thresh in [3, 5, 8]:
            for hold in [12]:
                def make_s9_oi(om, ot, h):
                    def signal(ts, coin, f):
                        ret_24h = f.get("ret_6h", 0)
                        if abs(ret_24h) < 2000: return None
                        oi_f = oi_features.get((ts, coin))
                        if not oi_f: return None
                        oi_d = abs(oi_f.get("oi_delta_24h", 0))
                        if om == "high_oi" and oi_d < ot: return None
                        if om == "low_oi" and oi_d > ot: return None
                        s9_dir = -1 if ret_24h > 0 else 1
                        s9_stop = max(-2500, -1000 - abs(ret_24h) / 4)
                        return {"dir": s9_dir, "hold": h, "strength": abs(ret_24h), "stop_bps": s9_stop}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s9_oi(oi_mode, oi_thresh, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"S9+{oi_mode}>{oi_thresh}% hold={hold*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 4: S16 — Funding Rate Extreme
    # Very negative funding = too many shorts = squeeze potential
    # (Using OI as proxy — high OI + price drop suggests funding distress)
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 4: S16 — OI Extreme (proxy for crowding)")

    for oi_thresh in [10, 15, 20, 25]:
        for price_dir in ["any", "up", "down", "flat"]:
            for hold in [6, 12, 18]:
                def make_s16(ot, pd, h):
                    def signal(ts, coin, f):
                        oi_f = oi_features.get((ts, coin))
                        if not oi_f: return None
                        oi_d = oi_f.get("oi_delta_24h", 0)
                        ret = f.get("ret_6h", 0)
                        if abs(oi_d) < ot: return None
                        if pd == "up" and ret < 200: return None
                        if pd == "down" and ret > -200: return None
                        if pd == "flat" and abs(ret) > 500: return None
                        # Fade the OI direction: if OI rises a lot, fade (expect reversal of the leveraged crowd)
                        fade_dir = -1 if oi_d > 0 else 1
                        return {"dir": fade_dir, "hold": h, "strength": abs(oi_d)}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s16(oi_thresh, price_dir, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"OI_extreme>{oi_thresh}% price={price_dir} hold={hold*4}h", r)

    # Also test FOLLOW direction (not fade)
    for oi_thresh in [10, 15, 20]:
        for hold in [6, 12, 18]:
            def make_s16_follow(ot, h):
                def signal(ts, coin, f):
                    oi_f = oi_features.get((ts, coin))
                    if not oi_f: return None
                    oi_d = oi_f.get("oi_delta_24h", 0)
                    if abs(oi_d) < ot: return None
                    follow_dir = 1 if oi_d > 0 else -1
                    return {"dir": follow_dir, "hold": h, "strength": abs(oi_d)}
                return signal
            r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                make_s16_follow(oi_thresh, hold), {"hold": hold})
            if r["n"] >= 10:
                print_row(f"OI_follow>{oi_thresh}% hold={h*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 5: S17 — Multi-TF Momentum Agreement
    # When 6h, 24h, and 7d momentum all agree direction
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 5: S17 — Multi-Timeframe Momentum Agreement")

    for min_ret_6h in [200, 500, 800]:
        for min_ret_24h in [500, 1000, 1500]:
            for min_ret_7d in [500, 1000, 2000]:
                for hold in [6, 12, 18]:
                    for mode in ["follow", "fade"]:
                        def make_s17(r6, r24, r7d, h, m):
                            def signal(ts, coin, f):
                                ret6 = f.get("ret_2h", 0)  # 2 candles = 8h
                                ret24 = f.get("ret_6h", 0)  # 6 candles = 24h
                                ret7d = f.get("ret_42h", 0) # 42 candles ≈ 7d
                                if abs(ret6) < r6 or abs(ret24) < r24 or abs(ret7d) < r7d: return None
                                # All same direction?
                                if not (np.sign(ret6) == np.sign(ret24) == np.sign(ret7d)): return None
                                d = int(np.sign(ret24))
                                if m == "fade": d = -d
                                return {"dir": d, "hold": h, "strength": abs(ret24)}
                            return signal
                        r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                            make_s17(min_ret_6h, min_ret_24h, min_ret_7d, hold, mode), {"hold": hold})
                        if r["n"] >= 10:
                            print_row(f"MTF_{mode} 6h>{min_ret_6h} 24h>{min_ret_24h} 7d>{min_ret_7d} {h*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 6: S18 — BTC-Alt Lag (BTC moves, alts follow 4-12h later)
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 6: S18 — BTC-Alt Lag")

    for btc_ret_thresh in [200, 500, 800, 1000]:
        for alt_max in [100, 200, 500]:
            for hold in [6, 12]:
                def make_s18(bt, am, h):
                    def signal(ts, coin, f):
                        btc_r = btc_ret(ts, 2)  # BTC 8h return (2 candles)
                        if abs(btc_r) < bt: return None
                        coin_ret = f.get("ret_2h", 0)
                        if abs(coin_ret) > am: return None  # already moved
                        d = 1 if btc_r > 0 else -1
                        return {"dir": d, "hold": h, "strength": abs(btc_r)}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s18(btc_ret_thresh, alt_max, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"BTC_lag btc>{btc_ret_thresh} alt<{alt_max} hold={h*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 7: S19 — Volume Dry-Up Reversal
    # When volume collapses to extreme low + recent drawdown → reversal
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 7: S19 — Volume Dry-Up Reversal")

    for vol_z_max in [-1.5, -1.0, -0.5]:
        for dd_thresh in [-2000, -3000, -4000]:
            for hold in [6, 12, 18]:
                def make_s19(vz, dd, h):
                    def signal(ts, coin, f):
                        if f.get("vol_z", 0) > vz: return None
                        if f.get("drawdown", 0) > dd: return None
                        return {"dir": 1, "hold": h, "strength": abs(f.get("drawdown", 0))}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s19(vol_z_max, dd_thresh, hold), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"VolDryUp vz<{vol_z_max} dd<{dd_thresh} hold={h*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # TEST 8: S20 — OI Divergence Cross-Sectional
    # Token with highest OI increase in its sector vs others
    # ═══════════════════════════════════════════════════════════════
    print_header("TEST 8: S20 — OI Cross-Sectional (sector OI leader)")

    for oi_zscore_thresh in [1.5, 2.0, 2.5]:
        for hold in [6, 12, 18]:
            for mode in ["follow", "fade"]:
                def make_s20(oz, h, m):
                    def signal(ts, coin, f):
                        sect = TOKEN_TO_SECTOR.get(coin)
                        if not sect: return None
                        sect_oi = []
                        for t in SECTORS[sect]:
                            oi_f = oi_features.get((ts, t))
                            if oi_f and "oi_delta_24h" in oi_f:
                                sect_oi.append((t, oi_f["oi_delta_24h"]))
                        if len(sect_oi) < 3: return None
                        vals = [v for _, v in sect_oi]
                        mean = np.mean(vals); std = np.std(vals)
                        if std < 0.5: return None
                        coin_oi = dict(sect_oi).get(coin)
                        if coin_oi is None: return None
                        z = (coin_oi - mean) / std
                        if abs(z) < oz: return None
                        if m == "follow":
                            d = 1 if z > 0 else -1
                        else:
                            d = -1 if z > 0 else 1
                        return {"dir": d, "hold": h, "strength": abs(z) * 100}
                    return signal
                r = isolation_backtest(data, all_ts_sorted, coin_by_ts, feat_by_ts,
                    make_s20(oi_zscore_thresh, hold, mode), {"hold": hold})
                if r["n"] >= 10:
                    print_row(f"OI_xsect_{mode} z>{oi_zscore_thresh} hold={h*4}h", r)

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY — Collect all valid results
    # ═══════════════════════════════════════════════════════════════
    elapsed = _time.time() - t0
    print(f"\n{'='*100}")
    print(f"  COMPLETE — {elapsed:.0f}s total")
    print(f"{'='*100}")


if __name__ == "__main__":
    main()
