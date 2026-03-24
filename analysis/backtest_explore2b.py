"""Exploration 2B — Deep dive on best findings.

1. Calendar effect with proper position limits + train/test
2. Momentum factor with position limits + Monte Carlo
3. ML Random Forest + Gradient Boosting
4. Hyperliquid funding (correct API format)
5. Combined: calendar + momentum + our existing signals

Usage:
    python3 -m analysis.backtest_explore2b
"""

from __future__ import annotations

import json, os, time, random, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier

from analysis.backtest_genetic import (
    load_3y_candles, build_features, Rule, Strategy, backtest_strategy,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS, MAX_SAME_DIR,
    STOP_LOSS_BPS, TRAIN_END, TEST_START,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")


# ══════════════════════════════════════════════════════════════════════
# 1. Calendar Effect — Proper Backtest
# ══════════════════════════════════════════════════════════════════════

def calendar_backtest(features, data):
    """Test calendar signals with proper position management."""
    print("\n" + "=" * 70)
    print("  CALENDAR EFFECT — Proper Backtest")
    print("  Position limits, train/test, Monte Carlo")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    configs = [
        # (name, entry_dow, direction, hold)
        ("LONG Tue", 1, 1, 6),         # Tuesday → LONG, hold 24h
        ("SHORT Sun", 6, -1, 6),        # Sunday → SHORT, hold 24h
        ("SHORT Mon", 0, -1, 6),        # Monday → SHORT
        ("LONG Fri", 4, 1, 6),          # Friday → LONG
        ("LONG Tue hold3d", 1, 1, 18),  # Tuesday → LONG, hold 72h
        ("SHORT Sun hold3d", 6, -1, 18),
        # Combined: LONG Tue+Fri, SHORT Sun+Mon
        ("COMBO Tue+Fri L / Sun+Mon S", None, None, 6),
    ]

    results = []

    for config in configs:
        name = config[0]
        if config[1] is not None:
            # Single day signal
            entry_dow = config[1]
            direction = config[2]
            hold = config[3]

            trades = _calendar_trades(coins, features, data, entry_dow, direction, hold)
        else:
            # Combo: multiple days
            hold = config[3]
            trades_l = _calendar_trades(coins, features, data, 1, 1, hold)  # Tue LONG
            trades_l += _calendar_trades(coins, features, data, 4, 1, hold)  # Fri LONG
            trades_s = _calendar_trades(coins, features, data, 6, -1, hold)  # Sun SHORT
            trades_s += _calendar_trades(coins, features, data, 0, -1, hold)  # Mon SHORT
            trades = trades_l + trades_s

        if len(trades) < 20:
            continue

        # Position-limited replay
        trades_limited = _apply_position_limits(trades, max_pos=6, max_dir=4)

        n = len(trades_limited)
        pnl = sum(t["pnl"] for t in trades_limited)
        avg = float(np.mean([t["net"] for t in trades_limited]))
        wins = sum(1 for t in trades_limited if t["net"] > 0)

        # Train/test
        train_pnl = sum(t["pnl"] for t in trades_limited if t["entry_t"] < TRAIN_END)
        test_pnl = sum(t["pnl"] for t in trades_limited if t["entry_t"] >= TEST_START)
        train_n = sum(1 for t in trades_limited if t["entry_t"] < TRAIN_END)
        test_n = n - train_n

        results.append({
            "name": name, "n": n, "pnl": pnl, "avg": avg,
            "win": wins / n * 100,
            "train_pnl": train_pnl, "test_pnl": test_pnl,
            "train_n": train_n, "test_n": test_n,
            "trades": trades_limited,
        })

        marker = "✓" if train_pnl > 0 and test_pnl > 0 else ""
        print(f"  {name:<35} ${pnl:>+7.0f} ({n:>4}t, avg={avg:>+5.1f}bp, "
              f"win={wins/n*100:.0f}%) train=${train_pnl:>+.0f}({train_n}t) "
              f"test=${test_pnl:>+.0f}({test_n}t) {marker}")

    # Monte Carlo on best
    best = max(results, key=lambda r: r["pnl"]) if results else None
    if best and best["trades"]:
        print(f"\n  Monte Carlo on '{best['name']}':")
        mc = _monte_carlo(best["trades"], data, n_sims=500)
        print(f"    Actual: ${best['pnl']:>+.0f} | Random: ${mc['mean']:>+.0f} "
              f"(std: ${mc['std']:.0f}) | Z: {mc['z']:+.2f}")
        if mc["z"] > 2:
            print(f"    → ✓ SIGNIFICANT")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    # Monthly breakdown of best
    if best and best["trades"]:
        _monthly_breakdown(best["trades"], best["name"])

    return results


def _calendar_trades(coins, features, data, entry_dow, direction, hold):
    """Generate trades for a calendar signal (no position limits)."""
    trades = []
    for coin in coins:
        candles = data[coin]
        for f in features.get(coin, []):
            idx = f["_idx"]
            if idx + hold + 1 >= len(candles):
                continue
            dt = datetime.fromtimestamp(f["t"] / 1000, tz=timezone.utc)
            if dt.weekday() != entry_dow:
                continue

            entry = candles[idx + 1]["o"]
            exit_p = candles[idx + hold]["c"]
            if entry <= 0:
                continue
            gross = direction * (exit_p / entry - 1) * 1e4
            net = gross - COST_BPS

            # Stop loss check
            sl_hit = False
            for ci in range(idx + 1, idx + hold + 1):
                if ci >= len(candles):
                    break
                if direction == 1:
                    worst = (candles[ci]["l"] / entry - 1) * 1e4
                    if worst < STOP_LOSS_BPS:
                        exit_p_sl = entry * (1 + STOP_LOSS_BPS / 1e4)
                        gross = (exit_p_sl / entry - 1) * 1e4
                        net = gross - COST_BPS
                        sl_hit = True
                        break
                else:
                    worst = -(candles[ci]["h"] / entry - 1) * 1e4
                    if worst < STOP_LOSS_BPS:
                        exit_p_sl = entry * (1 - STOP_LOSS_BPS / 1e4)
                        gross = -(exit_p_sl / entry - 1) * 1e4
                        net = gross - COST_BPS
                        sl_hit = True
                        break

            pnl = POSITION_SIZE * net / 1e4
            trades.append({
                "coin": coin,
                "direction": "LONG" if direction == 1 else "SHORT",
                "dir": direction,
                "entry_t": candles[idx + 1]["t"],
                "exit_t": candles[min(idx + hold, len(candles) - 1)]["t"],
                "hold": hold,
                "net": round(net, 1),
                "pnl": round(pnl, 2),
                "reason": "stop" if sl_hit else "timeout",
            })
    return trades


def _apply_position_limits(trades, max_pos=6, max_dir=4):
    """Filter trades to respect position limits."""
    trades.sort(key=lambda t: t["entry_t"])
    active = []  # list of (exit_t, direction)
    result = []

    for t in trades:
        # Remove expired positions
        active = [(et, d) for et, d in active if et > t["entry_t"]]

        n_long = sum(1 for _, d in active if d == 1)
        n_short = sum(1 for _, d in active if d == -1)

        if len(active) >= max_pos:
            continue
        if t["dir"] == 1 and n_long >= max_dir:
            continue
        if t["dir"] == -1 and n_short >= max_dir:
            continue

        active.append((t["exit_t"], t["dir"]))
        result.append(t)

    return result


def _monte_carlo(trades, data, n_sims=500):
    """Direction-matched Monte Carlo."""
    actual_pnl = sum(t["pnl"] for t in trades)
    by_coin = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        by_coin[t["coin"]]["long" if t["direction"] == "LONG" else "short"] += 1

    avg_hold = int(np.mean([t["hold"] for t in trades]))
    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        for coin, counts in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            nc = len(candles)
            available = list(range(180, nc - avg_hold - 1))
            n_needed = counts["long"] + counts["short"]
            if len(available) < n_needed:
                continue
            sampled = random.sample(available, n_needed)
            for j, idx in enumerate(sampled):
                direction = 1 if j < counts["long"] else -1
                entry = candles[min(idx + 1, nc - 1)]["o"]
                exit_idx = min(idx + 1 + avg_hold, nc - 1)
                exit_p = candles[exit_idx]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim_total += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim_total)

    mean = float(np.mean(sim_pnls))
    std = float(np.std(sim_pnls))
    z = (actual_pnl - mean) / std if std > 0 else 0
    return {"mean": mean, "std": std, "z": z}


def _monthly_breakdown(trades, label):
    """Quick monthly breakdown."""
    by_month = defaultdict(lambda: {"pnl": 0, "n": 0})
    for t in trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        m = dt.strftime("%Y-%m")
        by_month[m]["pnl"] += t["pnl"]
        by_month[m]["n"] += 1

    months = sorted(by_month)
    winning = sum(1 for m in months if by_month[m]["pnl"] > 0)
    print(f"\n  {label} — {winning}/{len(months)} winning months")
    cum = 0
    for m in months:
        d = by_month[m]
        cum += d["pnl"]
        marker = "✓" if d["pnl"] > 0 else "✗"
        print(f"    {m}: ${d['pnl']:>+7.1f} ({d['n']:>3}t) cum=${cum:>+.0f} {marker}")


# ══════════════════════════════════════════════════════════════════════
# 2. Momentum Factor — Proper Backtest
# ══════════════════════════════════════════════════════════════════════

def momentum_backtest(features, data):
    """Momentum factor with position limits + Monte Carlo."""
    print("\n" + "=" * 70)
    print("  MOMENTUM FACTOR — Proper Backtest")
    print("  Buy top 3 performers (14d), sell bottom 3")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # Build time-indexed feature data
    coin_feat_by_ts = defaultdict(dict)
    all_ts = set()
    for coin in coins:
        for f in features[coin]:
            coin_feat_by_ts[f["t"]][coin] = f
            all_ts.add(f["t"])

    sorted_ts = sorted(all_ts)

    configs = [
        ("Mom 14d top3 hold3d", "ret_84h", 3, 18),
        ("Mom 7d top3 hold2d", "ret_42h", 3, 12),
        ("Mom 7d top3 hold3d", "ret_42h", 3, 18),
        ("Mom 14d top5 hold2d", "ret_84h", 5, 12),
        ("Mom 30d top3 hold3d", "ret_180h", 3, 18),
        # Relative strength vs BTC
        ("RelStr BTC 7d top3 hold2d", "alt_vs_btc_7d", 3, 12),
    ]

    for name, feat_key, top_n, hold in configs:
        positions = {}
        trades = []
        cooldown = {}
        rebalance_counter = 0

        for ts in sorted_ts:
            # Exit
            for coin in list(positions.keys()):
                pos = positions[coin]
                if coin not in data:
                    continue
                candles = data[coin]
                for ci in range(pos["entry_idx"], min(pos["entry_idx"] + hold + 2, len(candles))):
                    if candles[ci]["t"] == ts:
                        held = ci - pos["entry_idx"]
                        if held >= hold:
                            current = candles[ci]["c"]
                            if current > 0:
                                gross = pos["dir"] * (current / pos["entry_price"] - 1) * 1e4
                                net = gross - COST_BPS
                                pnl = POSITION_SIZE * net / 1e4
                                trades.append({
                                    "coin": coin,
                                    "direction": "LONG" if pos["dir"] == 1 else "SHORT",
                                    "dir": pos["dir"],
                                    "entry_t": pos["entry_t"],
                                    "exit_t": ts,
                                    "hold": held,
                                    "net": round(net, 1),
                                    "pnl": round(pnl, 2),
                                })
                            del positions[coin]
                            cooldown[coin] = ts + 4 * 3600 * 1000
                        break

            # Rebalance when all positions closed
            if len(positions) > 0:
                continue

            rebalance_counter += 1
            if rebalance_counter % max(1, hold // 6) != 0:
                continue

            available = coin_feat_by_ts.get(ts, {})
            if len(available) < top_n * 2 + 2:
                continue

            ranked = [(c, f.get(feat_key, 0), f) for c, f in available.items()
                      if f.get(feat_key, 0) != 0]
            if len(ranked) < top_n * 2:
                continue

            ranked.sort(key=lambda x: x[1])

            # LONG top (momentum), SHORT bottom
            longs = ranked[-top_n:]
            shorts = ranked[:top_n]

            n_long = 0
            n_short = 0

            for coin, val, f in longs:
                if coin in positions or coin in cooldown and ts < cooldown.get(coin, 0):
                    continue
                if n_long >= MAX_SAME_DIR:
                    continue
                idx = f["_idx"]
                if idx + 1 >= len(data[coin]):
                    continue
                entry = data[coin][idx + 1]["o"]
                if entry <= 0:
                    continue
                positions[coin] = {
                    "dir": 1, "entry_price": entry,
                    "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
                }
                n_long += 1

            for coin, val, f in shorts:
                if coin in positions or coin in cooldown and ts < cooldown.get(coin, 0):
                    continue
                if n_short >= MAX_SAME_DIR:
                    continue
                idx = f["_idx"]
                if idx + 1 >= len(data[coin]):
                    continue
                entry = data[coin][idx + 1]["o"]
                if entry <= 0:
                    continue
                positions[coin] = {
                    "dir": -1, "entry_price": entry,
                    "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
                }
                n_short += 1

        if len(trades) < 20:
            print(f"  {name}: only {len(trades)} trades, skip")
            continue

        n = len(trades)
        pnl = sum(t["pnl"] for t in trades)
        avg = float(np.mean([t["net"] for t in trades]))
        wins = sum(1 for t in trades if t["net"] > 0)
        train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
        test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

        marker = "✓" if train_pnl > 0 and test_pnl > 0 else ""
        print(f"  {name:<35} ${pnl:>+7.0f} ({n:>4}t, avg={avg:>+5.1f}bp, "
              f"win={wins/n*100:.0f}%) train=${train_pnl:>+.0f} test=${test_pnl:>+.0f} {marker}")


# ══════════════════════════════════════════════════════════════════════
# 3. Machine Learning
# ══════════════════════════════════════════════════════════════════════

def ml_analysis(features, data):
    """Walk-forward ML with proper validation."""
    print("\n" + "=" * 70)
    print("  MACHINE LEARNING — Walk-Forward Validation")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features]

    feat_names = [
        "ret_6h", "ret_42h", "ret_84h", "ret_180h",
        "vol_7d", "vol_30d", "vol_ratio",
        "drawdown", "recovery", "range_pct",
        "consec_up", "consec_dn",
        "btc_7d", "btc_30d", "eth_7d",
        "btc_eth_spread", "alt_vs_btc_7d", "alt_vs_btc_30d",
        "alt_index_7d", "dispersion_7d", "alt_rank_7d",
        "vol_z",
    ]

    # Add calendar features
    feat_names_ext = feat_names + ["dow", "hour", "month"]

    rows = []
    for coin in coins:
        candles = data[coin]
        for f in features[coin]:
            idx = f["_idx"]
            if idx + 19 >= len(candles):
                continue

            # Target: 72h forward return direction
            fwd_ret = (candles[idx + 18]["c"] / candles[idx + 1]["o"] - 1) * 1e4
            # Binary: profitable after costs?
            target = 1 if fwd_ret > COST_BPS else 0

            dt = datetime.fromtimestamp(f["t"] / 1000, tz=timezone.utc)
            row = [f.get(fn, 0) for fn in feat_names]
            row.extend([dt.weekday(), dt.hour, dt.month])
            row.append(f["t"])
            row.append(target)
            row.append(fwd_ret)
            row.append(coin)
            rows.append(row)

    rows.sort(key=lambda r: r[len(feat_names_ext)])
    n_feat = len(feat_names_ext)

    X = np.array([r[:n_feat] for r in rows], dtype=float)
    X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)
    timestamps = np.array([r[n_feat] for r in rows])
    targets = np.array([r[n_feat + 1] for r in rows])
    returns = np.array([r[n_feat + 2] for r in rows])

    print(f"  Dataset: {len(rows):,} samples, {n_feat} features")
    print(f"  Target: {sum(targets==1):,} up ({sum(targets==1)/len(targets)*100:.1f}%) | "
          f"{sum(targets==0):,} down ({sum(targets==0)/len(targets)*100:.1f}%)")

    # Walk-forward: train 12mo, test 3mo, roll 3mo
    train_size = 12 * 30 * 6   # ~12 months in 4h candles
    test_size = 3 * 30 * 6     # ~3 months

    models = {
        "RandomForest": lambda: RandomForestClassifier(
            n_estimators=200, max_depth=5, min_samples_leaf=50,
            random_state=42, n_jobs=-1),
        "GradientBoosting": lambda: GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            min_samples_leaf=50, random_state=42),
    }

    for model_name, model_fn in models.items():
        print(f"\n  --- {model_name} ---")
        all_preds = []
        all_rets = []
        all_ts = []
        feature_imp = np.zeros(n_feat)
        n_folds = 0

        for start in range(0, len(rows) - train_size - test_size, test_size):
            end_train = start + train_size
            end_test = min(end_train + test_size, len(rows))

            X_tr = X[start:end_train]
            y_tr = targets[start:end_train]
            X_te = X[end_train:end_test]

            if len(set(y_tr)) < 2:
                continue

            clf = model_fn()
            clf.fit(X_tr, y_tr)
            proba = clf.predict_proba(X_te)

            feature_imp += clf.feature_importances_
            n_folds += 1

            # Get probability of class 1 (up)
            cls_idx = list(clf.classes_).index(1) if 1 in clf.classes_ else -1
            if cls_idx < 0:
                continue

            for j in range(len(X_te)):
                prob_up = proba[j][cls_idx]
                idx_global = end_train + j
                all_preds.append(prob_up)
                all_rets.append(returns[idx_global])
                all_ts.append(timestamps[idx_global])

        if not all_preds or n_folds == 0:
            print("  No valid folds!")
            continue

        feature_imp /= n_folds

        # Test different confidence thresholds
        print(f"\n  Trading by confidence threshold:")
        print(f"  {'Threshold':<15} {'LONG$':>8} {'SHORT$':>8} {'Total$':>8} "
              f"{'#L':>5} {'#S':>5} {'W%':>5}")
        print(f"  {'-'*55}")

        for high_thresh, low_thresh in [(0.55, 0.45), (0.60, 0.40), (0.65, 0.35),
                                         (0.55, 0.50), (0.60, 0.50)]:
            long_pnl = 0
            short_pnl = 0
            n_long = 0
            n_short = 0
            n_wins = 0

            for i in range(len(all_preds)):
                if all_preds[i] > high_thresh:
                    # LONG
                    net = all_rets[i] - COST_BPS
                    long_pnl += POSITION_SIZE * net / 1e4
                    n_long += 1
                    if net > 0:
                        n_wins += 1
                elif all_preds[i] < low_thresh:
                    # SHORT
                    net = -all_rets[i] - COST_BPS
                    short_pnl += POSITION_SIZE * net / 1e4
                    n_short += 1
                    if net > 0:
                        n_wins += 1

            total = long_pnl + short_pnl
            n_total = n_long + n_short
            wr = n_wins / n_total * 100 if n_total > 0 else 0

            # Train/test split
            train_end_ts = TRAIN_END
            long_train = sum(POSITION_SIZE * (all_rets[i] - COST_BPS) / 1e4
                           for i in range(len(all_preds))
                           if all_preds[i] > high_thresh and all_ts[i] < train_end_ts)
            long_test = sum(POSITION_SIZE * (all_rets[i] - COST_BPS) / 1e4
                          for i in range(len(all_preds))
                          if all_preds[i] > high_thresh and all_ts[i] >= train_end_ts)

            marker = "✓" if long_train > 0 and long_test > 0 else ""
            print(f"  >{high_thresh:.2f} / <{low_thresh:.2f}  "
                  f"${long_pnl:>+7.0f} ${short_pnl:>+7.0f} ${total:>+7.0f} "
                  f"{n_long:>4} {n_short:>4} {wr:>4.0f}% "
                  f"(trn=${long_train:>+.0f} tst=${long_test:>+.0f}) {marker}")

        # Feature importance
        fi = sorted(zip(feat_names_ext, feature_imp), key=lambda x: x[1], reverse=True)
        print(f"\n  Feature importance (top 10):")
        for fname, imp in fi[:10]:
            bar = "█" * int(imp * 200)
            print(f"    {fname:<20} {imp:.4f} {bar}")


# ══════════════════════════════════════════════════════════════════════
# 4. Funding Rate from Hyperliquid
# ══════════════════════════════════════════════════════════════════════

def fetch_hl_funding():
    """Fetch funding history using correct Hyperliquid API format."""
    print("\n" + "=" * 70)
    print("  HYPERLIQUID FUNDING RATES")
    print("=" * 70)

    # Try different API formats
    test_coin = "BTC"
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - 90 * 86400 * 1000  # 90 days

    formats = [
        {"type": "fundingHistory", "coin": test_coin,
         "startTime": start_ts, "endTime": end_ts},
        {"type": "fundingHistory", "coin": test_coin,
         "startTime": start_ts},
        {"type": "fundingHistory", "req": {
            "coin": test_coin, "startTime": start_ts, "endTime": end_ts}},
        {"type": "userFundingHistory", "user": "0x0000000000000000000000000000000000000000",
         "startTime": start_ts, "endTime": end_ts},
    ]

    for i, payload_dict in enumerate(formats):
        try:
            payload = json.dumps(payload_dict).encode()
            req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                         data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            print(f"  Format {i+1}: OK — got {type(result).__name__}")
            if isinstance(result, list) and len(result) > 0:
                print(f"    First item: {json.dumps(result[0])[:200]}")
                print(f"    Total: {len(result)} records")
                return result
            elif isinstance(result, dict):
                print(f"    Keys: {list(result.keys())[:10]}")
        except Exception as e:
            err_str = str(e)[:100]
            print(f"  Format {i+1}: FAILED — {err_str}")

    # Try getting current funding from metaAndAssetCtxs
    print(f"\n  Falling back to metaAndAssetCtxs for current rates...")
    try:
        payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                     data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if isinstance(result, list) and len(result) >= 2:
            meta = result[0]
            ctxs = result[1]
            print(f"\n  Current funding rates (top 10 by |rate|):")
            rates = []
            for i, asset in enumerate(meta["universe"]):
                name = asset["name"]
                if i < len(ctxs):
                    rate = float(ctxs[i].get("funding", 0))
                    rates.append((name, rate))

            rates.sort(key=lambda x: abs(x[1]), reverse=True)
            for name, rate in rates[:15]:
                bps = rate * 1e4
                print(f"    {name:<8} {bps:>+6.2f} bps/8h "
                      f"({rate*3*365*100:>+6.1f}% APR)")
    except Exception as e:
        print(f"  metaAndAssetCtxs failed: {e}")

    return None


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  EXPLORATION 2B — Deep Dive")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # 1. Calendar
    cal_results = calendar_backtest(features, data)

    # 2. Momentum
    momentum_backtest(features, data)

    # 3. ML
    ml_analysis(features, data)

    # 4. Funding
    fetch_hl_funding()

    # ── Summary ──
    print(f"\n\n{'█'*70}")
    print(f"  EXPLORATION 2B — FINDINGS")
    print(f"{'█'*70}")


if __name__ == "__main__":
    main()
