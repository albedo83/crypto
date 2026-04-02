"""BTC/Alts Correlation Breakdown Backtest.

Concept: BTC and alts are normally correlated (~0.7-0.8). When correlation breaks
(BTC moves but alts don't follow), bet on re-correlation:
  - BTC up, alts flat → long alts (catch-up)
  - BTC down, alts flat → short alts (catch-down)
  - BTC flat, alts diverge → alts revert to BTC

Data: BTC + 28 alts, 4h candles, 3 years.

Usage:
    python3 -m analysis.backtest_decorr
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS, MAX_SAME_DIR,
    TRAIN_END, TEST_START,
)


def compute_decorrelation(data, corr_window=42, ret_window=42):
    """Compute rolling correlation + return gap between BTC and each alt.

    Returns {timestamp: [{coin, corr, btc_ret, alt_ret, gap, gap_z}]}
    """
    if "BTC" not in data:
        return {}

    btc_candles = data["BTC"]
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_times = np.array([c["t"] for c in btc_candles])

    signals = defaultdict(list)

    for coin in TOKENS:
        if coin not in data:
            continue
        alt_candles = data[coin]
        alt_closes = np.array([c["c"] for c in alt_candles])
        alt_times = np.array([c["t"] for c in alt_candles])

        # Align timestamps
        btc_idx_map = {t: i for i, t in enumerate(btc_times)}
        aligned_btc = []
        aligned_alt = []
        aligned_ts = []
        aligned_alt_idx = []

        for j, ac in enumerate(alt_candles):
            t = ac["t"]
            if t in btc_idx_map:
                bi = btc_idx_map[t]
                aligned_btc.append(btc_closes[bi])
                aligned_alt.append(alt_closes[j])
                aligned_ts.append(t)
                aligned_alt_idx.append(j)

        if len(aligned_btc) < corr_window + ret_window + 10:
            continue

        ab = np.array(aligned_btc)
        aa = np.array(aligned_alt)

        # Compute rolling returns
        for i in range(max(corr_window, ret_window), len(ab)):
            # Rolling correlation of returns
            btc_rets_w = np.diff(ab[i - corr_window:i + 1]) / ab[i - corr_window:i]
            alt_rets_w = np.diff(aa[i - corr_window:i + 1]) / aa[i - corr_window:i]

            if np.std(btc_rets_w) < 1e-10 or np.std(alt_rets_w) < 1e-10:
                continue

            corr = float(np.corrcoef(btc_rets_w, alt_rets_w)[0, 1])

            # Return over ret_window
            btc_ret = (ab[i] / ab[i - ret_window] - 1) * 1e4
            alt_ret = (aa[i] / aa[i - ret_window] - 1) * 1e4
            gap = btc_ret - alt_ret  # positive = BTC outperformed alts

            # Gap z-score (rolling)
            if i >= ret_window + 30:
                recent_gaps = []
                for k in range(i - 30, i):
                    br = (ab[k] / ab[k - ret_window] - 1) * 1e4
                    ar = (aa[k] / aa[k - ret_window] - 1) * 1e4
                    recent_gaps.append(br - ar)
                gap_std = float(np.std(recent_gaps))
                gap_mean = float(np.mean(recent_gaps))
                gap_z = (gap - gap_mean) / gap_std if gap_std > 0 else 0
            else:
                gap_z = 0

            signals[aligned_ts[i]].append({
                "coin": coin,
                "corr": round(corr, 3),
                "btc_ret": round(btc_ret, 0),
                "alt_ret": round(alt_ret, 0),
                "gap": round(gap, 0),
                "gap_z": round(gap_z, 2),
                "alt_idx": aligned_alt_idx[i],
            })

    return signals


def backtest_decorr(signals, data, config):
    """Backtest correlation breakdown strategy.

    config:
        corr_threshold: enter when rolling corr < this (default 0.3)
        gap_threshold: min |gap| in bps to enter
        gap_z_threshold: min |gap_z| to enter (alternative to gap_threshold)
        mode: "gap" (absolute gap), "gap_z" (z-scored gap), "corr" (low corr only)
        direction_mode: "catchup" (alts catch up to BTC) or "revert" (BTC reverts to alts)
        hold: hold period in candles
        period: "train", "test", "all"
    """
    corr_thresh = config.get("corr_threshold", 0.3)
    gap_thresh = config.get("gap_threshold", 1000)
    gap_z_thresh = config.get("gap_z_threshold", 1.5)
    mode = config.get("mode", "gap")
    dir_mode = config.get("direction_mode", "catchup")
    hold = config.get("hold", 18)
    period = config.get("period", "all")
    max_pos = config.get("max_positions", MAX_POSITIONS)
    max_dir = config.get("max_dir", MAX_SAME_DIR)

    positions = {}
    trades = []

    for ts in sorted(signals.keys()):
        if period == "train" and ts >= TRAIN_END:
            continue
        if period == "test" and ts < TEST_START:
            continue

        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            candles = data[coin]
            exit_idx = pos["entry_idx"] + hold
            if exit_idx >= len(candles):
                exit_idx = len(candles) - 1

            # Check if we've reached hold
            current_idx = None
            for s in signals.get(ts, []):
                if s["coin"] == coin:
                    current_idx = s["alt_idx"]
                    break

            if current_idx is None:
                continue
            if current_idx - pos["entry_idx"] >= hold:
                exit_price = candles[current_idx]["c"]
                if exit_price <= 0:
                    continue
                gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                net = gross - COST_BPS
                pnl = POSITION_SIZE * net / 1e4
                trades.append({
                    "coin": coin,
                    "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                    "corr": pos["corr"], "gap": pos["gap"],
                    "gross_bps": round(gross, 1), "net_bps": round(net, 1),
                    "pnl": round(pnl, 2),
                    "entry_t": pos["entry_t"], "exit_t": ts,
                })
                del positions[coin]

        # Entries
        if len(positions) >= max_pos:
            continue

        candidates = []
        for s in signals.get(ts, []):
            coin = s["coin"]
            if coin in positions:
                continue

            ok = False
            if mode == "gap" and abs(s["gap"]) >= gap_thresh:
                ok = True
            elif mode == "gap_z" and abs(s["gap_z"]) >= gap_z_thresh:
                ok = True
            elif mode == "corr" and s["corr"] < corr_thresh:
                ok = True
            elif mode == "combo" and s["corr"] < corr_thresh and abs(s["gap"]) >= gap_thresh:
                ok = True

            if not ok:
                continue

            # Direction
            if dir_mode == "catchup":
                # BTC outperformed → long alt (catch up); BTC underperformed → short alt
                direction = 1 if s["gap"] > 0 else -1
            else:  # revert
                # BTC outperformed → short alt (BTC will come back down)
                direction = -1 if s["gap"] > 0 else 1

            candidates.append((s, direction, abs(s["gap"])))

        candidates.sort(key=lambda x: x[2], reverse=True)

        n_longs = sum(1 for p in positions.values() if p["direction"] == 1)
        n_shorts = sum(1 for p in positions.values() if p["direction"] == -1)

        for s, direction, strength in candidates:
            if len(positions) >= max_pos:
                break
            if direction == 1 and n_longs >= max_dir:
                continue
            if direction == -1 and n_shorts >= max_dir:
                continue

            coin = s["coin"]
            candles = data[coin]
            entry_idx = s["alt_idx"] + 1
            if entry_idx >= len(candles):
                continue

            positions[coin] = {
                "direction": direction,
                "entry_price": candles[entry_idx]["o"],
                "entry_idx": entry_idx,
                "entry_t": ts,
                "corr": s["corr"],
                "gap": s["gap"],
            }
            if direction == 1:
                n_longs += 1
            else:
                n_shorts += 1

    return trades


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0, "monthly": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net_bps"] for t in trades]))
    wins = sum(1 for t in trades if t["net_bps"] > 0)
    t_min = min(t["entry_t"] for t in trades)
    t_max = max(t["exit_t"] for t in trades)
    months = max(1, (t_max - t_min) / (30.44 * 86400 * 1000))
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1),
            "win": round(wins / n * 100, 0), "monthly": round(pnl / months, 1)}


def monte_carlo(signals, data, config, n_sims=500):
    real_trades = backtest_decorr(signals, data, {**config, "period": "all"})
    if len(real_trades) < 10:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    hold = config.get("hold", 18)
    n_trades = len(real_trades)

    all_entries = []
    for ts, sigs in signals.items():
        for s in sigs:
            coin = s["coin"]
            idx = s["alt_idx"]
            if idx + 1 + hold < len(data.get(coin, [])):
                all_entries.append((coin, idx))

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, idx in sampled:
            direction = random.choice([-1, 1])
            entry = data[coin][idx + 1]["o"]
            exit_p = data[coin][idx + 1 + hold]["c"]
            if entry <= 0:
                continue
            gross = direction * (exit_p / entry - 1) * 1e4
            net = gross - COST_BPS
            sim_total += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim_total)

    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    return {"real_pnl": round(real_pnl, 2), "z": round(z, 2)}


def main():
    print("=" * 60)
    print("BTC/ALTS CORRELATION BREAKDOWN BACKTEST")
    print("=" * 60)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"  {len(data)} tokens")

    corr_windows = [42, 84]  # 7d, 14d
    ret_windows = [42, 84]   # 7d, 14d

    for cw in corr_windows:
        for rw in ret_windows:
            print(f"\n{'=' * 60}")
            print(f"Corr window: {cw*4}h, Ret window: {rw*4}h")
            print(f"{'=' * 60}")

            print("Computing decorrelation signals...")
            signals = compute_decorrelation(data, corr_window=cw, ret_window=rw)
            print(f"  {len(signals)} timestamps")

            # Quick stats
            all_corrs = []
            all_gaps = []
            for ts, sigs in signals.items():
                for s in sigs:
                    all_corrs.append(s["corr"])
                    all_gaps.append(s["gap"])
            print(f"  Corr: avg={np.mean(all_corrs):.2f} std={np.std(all_corrs):.2f}")
            print(f"  Gap:  avg={np.mean(all_gaps):+.0f} std={np.std(all_gaps):.0f}")

            results = []

            # Test modes
            configs = []
            for mode in ["gap", "corr", "combo"]:
                for dir_mode in ["catchup", "revert"]:
                    for hold in [12, 18, 24, 36]:
                        if mode == "gap":
                            for gap_t in [500, 1000, 2000, 3000]:
                                configs.append({"mode": mode, "gap_threshold": gap_t,
                                                "direction_mode": dir_mode, "hold": hold})
                        elif mode == "corr":
                            for corr_t in [0.1, 0.3, 0.5]:
                                configs.append({"mode": mode, "corr_threshold": corr_t,
                                                "direction_mode": dir_mode, "hold": hold})
                        elif mode == "combo":
                            for gap_t in [500, 1000, 2000]:
                                for corr_t in [0.3, 0.5]:
                                    configs.append({"mode": mode, "gap_threshold": gap_t,
                                                    "corr_threshold": corr_t,
                                                    "direction_mode": dir_mode, "hold": hold})

            for cfg in configs:
                for period in ["train", "test"]:
                    trades = backtest_decorr(signals, data, {**cfg, "period": period})
                    s = score(trades)
                    results.append({**cfg, "cw": cw, "rw": rw, "period": period, **s})

            # Best train
            train = [r for r in results if r["period"] == "train" and r["n"] >= 20 and r["cw"] == cw and r["rw"] == rw]
            if train:
                train.sort(key=lambda r: r["pnl"], reverse=True)
                print(f"\n  Top 5 (train):")
                print(f"  {'Mode':>6} {'Dir':>8} {'Hold':>5} {'N':>5} {'P&L':>8} {'Avg':>6} {'Win%':>5}")
                for r in train[:5]:
                    extra = f"gap={r.get('gap_threshold','')}" if r["mode"] != "corr" else f"corr<{r.get('corr_threshold','')}"
                    print(f"  {r['mode']:>6} {r['direction_mode']:>8} {r['hold']*4:>4}h {r['n']:>5} ${r['pnl']:>7.0f} {r['avg']:>+6.1f} {r['win']:>4.0f}% [{extra}]")
                    test = [t for t in results if t["period"] == "test" and t["cw"] == cw and t["rw"] == rw
                            and t["mode"] == r["mode"] and t.get("gap_threshold") == r.get("gap_threshold")
                            and t.get("corr_threshold") == r.get("corr_threshold")
                            and t["direction_mode"] == r["direction_mode"] and t["hold"] == r["hold"]]
                    if test:
                        t = test[0]
                        f = "✓" if t["avg"] > 0 else "✗"
                        print(f"    → test: {f} n={t['n']} P&L=${t['pnl']:.0f} avg={t['avg']:+.1f}")

    # Global passing
    print(f"\n{'=' * 60}")
    print("ALL PASSING (train + test avg > 0)")
    print(f"{'=' * 60}")

    all_results = results  # already contains all windows
    passing = []
    for r_tr in [r for r in all_results if r["period"] == "train" and r["n"] >= 20 and r["avg"] > 0]:
        matches = [t for t in all_results if t["period"] == "test"
                   and t["cw"] == r_tr["cw"] and t["rw"] == r_tr["rw"]
                   and t["mode"] == r_tr["mode"]
                   and t.get("gap_threshold") == r_tr.get("gap_threshold")
                   and t.get("corr_threshold") == r_tr.get("corr_threshold")
                   and t["direction_mode"] == r_tr["direction_mode"]
                   and t["hold"] == r_tr["hold"]]
        if matches and matches[0]["avg"] > 0 and matches[0]["n"] >= 10:
            passing.append({"train": r_tr, "test": matches[0],
                            "total": r_tr["pnl"] + matches[0]["pnl"]})

    if not passing:
        print("  None. Correlation breakdown doesn't produce tradable edge.")
    else:
        passing.sort(key=lambda x: x["total"], reverse=True)
        for p in passing[:10]:
            tr, te = p["train"], p["test"]
            extra = f"gap={tr.get('gap_threshold','')}" if tr["mode"] != "corr" else f"corr<{tr.get('corr_threshold','')}"
            print(f"  cw={tr['cw']*4}h rw={tr['rw']*4}h {tr['mode']} {tr['direction_mode']} hold={tr['hold']*4}h [{extra}]")
            print(f"    train: n={tr['n']} ${tr['pnl']:.0f} avg={tr['avg']:+.1f} | test: n={te['n']} ${te['pnl']:.0f} avg={te['avg']:+.1f}")

        # Monte Carlo top 3
        print(f"\n  Monte Carlo (top 3):")
        for p in passing[:3]:
            tr = p["train"]
            sigs = compute_decorrelation(data, corr_window=tr["cw"], ret_window=tr["rw"])
            mc = monte_carlo(sigs, data, {
                "mode": tr["mode"], "gap_threshold": tr.get("gap_threshold", 1000),
                "corr_threshold": tr.get("corr_threshold", 0.3),
                "direction_mode": tr["direction_mode"], "hold": tr["hold"],
            })
            if mc:
                f = "✓" if mc["z"] >= 2.0 else "✗"
                print(f"    z={mc['z']:.2f} {f} (${mc['real_pnl']:.0f})")

    print("\nDone.")


if __name__ == "__main__":
    main()
