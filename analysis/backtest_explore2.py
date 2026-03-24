"""Exploration Round 2 — New strategy families.

1. Cross-sectional factors (momentum, mean-reversion, relative strength)
2. Machine Learning (Random Forest with walk-forward validation)
3. Calendar effects (day of week, hour of day)
4. Volatility regime switching
5. Funding rate fetch from Hyperliquid + test as feature

Usage:
    python3 -m analysis.backtest_explore2
"""

from __future__ import annotations

import json, os, time, random, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")


# ══════════════════════════════════════════════════════════════════════
# 0. Fetch Hyperliquid funding history
# ══════════════════════════════════════════════════════════════════════

def fetch_funding_history(coin, days=1100):
    """Try to fetch funding history from Hyperliquid."""
    cache = os.path.join(DATA_DIR, f"{coin}_funding_hl.json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - days * 86400 * 1000

    try:
        payload = json.dumps({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_ts,
            "endTime": end_ts
        }).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                     data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if data and len(data) > 10:
            with open(cache, "w") as f:
                json.dump(data, f)
            return data
    except Exception as e:
        print(f"  Funding fetch failed for {coin}: {e}")
    return []


def load_funding_data():
    """Load funding history for all tokens."""
    funding = {}
    for coin in TOKENS + ["BTC", "ETH"]:
        data = fetch_funding_history(coin)
        if data:
            # Parse: [{coin, fundingRate, time}, ...]
            parsed = {}
            for d in data:
                try:
                    ts = int(datetime.fromisoformat(
                        d["time"].replace("Z", "+00:00")).timestamp() * 1000)
                    rate = float(d["fundingRate"])
                    parsed[ts] = rate
                except:
                    continue
            if parsed:
                funding[coin] = parsed
        time.sleep(0.1)
    return funding


# ══════════════════════════════════════════════════════════════════════
# 1. Cross-Sectional Factors
# ══════════════════════════════════════════════════════════════════════

def cross_sectional_factors(data, features):
    """Test cross-sectional ranking strategies.

    At each rebalance point:
    - Rank all tokens by some metric
    - Go LONG top N, SHORT bottom N
    - Hold for fixed period, then rebalance
    """
    print("\n" + "=" * 70)
    print("  CROSS-SECTIONAL FACTOR ANALYSIS")
    print("  Rank tokens → LONG top, SHORT bottom")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # Build time series of features per timestamp
    all_ts = set()
    coin_feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features[coin]:
            t = f["t"]
            all_ts.add(t)
            coin_feat_by_ts[t][coin] = f

    sorted_ts = sorted(all_ts)

    # Rebalance every N candles
    factors = {
        "Momentum 7d": ("ret_42h", "momentum"),      # buy winners, short losers
        "MeanRev 7d": ("ret_42h", "reversal"),        # buy losers, short winners
        "Momentum 14d": ("ret_84h", "momentum"),
        "MeanRev 14d": ("ret_84h", "reversal"),
        "Momentum 30d": ("ret_180h", "momentum"),
        "MeanRev 30d": ("ret_180h", "reversal"),
        "RelStr vs BTC 7d": ("alt_vs_btc_7d", "momentum"),  # buy outperformers
        "RelWeak vs BTC 7d": ("alt_vs_btc_7d", "reversal"),  # buy underperformers
        "Low vol": ("vol_7d", "reversal"),            # buy lowest vol
        "High vol": ("vol_7d", "momentum"),           # buy highest vol
        "Deep drawdown": ("drawdown", "reversal"),    # buy most drawn down
        "High recovery": ("recovery", "momentum"),    # buy most recovered
        "Low rank": ("alt_rank_7d", "reversal"),      # buy lowest ranked
        "High rank": ("alt_rank_7d", "momentum"),     # buy highest ranked
    }

    hold_periods = [6, 12, 18]  # 1, 2, 3 days
    top_n_values = [3, 5]

    results = []

    for factor_name, (feat_key, direction) in factors.items():
        for hold in hold_periods:
            for top_n in top_n_values:
                trades = []
                positions = {}  # coin → {direction, entry_price, entry_idx, entry_t}

                rebalance_interval = hold
                next_rebalance = 0

                for ti, ts in enumerate(sorted_ts):
                    # Exit positions that have been held long enough
                    for coin in list(positions.keys()):
                        pos = positions[coin]
                        if coin not in data:
                            continue
                        candles = data[coin]
                        # Find current candle
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
                                            "dir": "LONG" if pos["dir"] == 1 else "SHORT",
                                            "entry_t": pos["entry_t"],
                                            "exit_t": ts,
                                            "hold": held,
                                            "net": round(net, 1),
                                            "pnl": round(pnl, 2),
                                        })
                                    del positions[coin]
                                break

                    # Rebalance check
                    if ti < next_rebalance:
                        continue
                    if len(positions) > 0:
                        continue  # Wait until all positions closed

                    next_rebalance = ti + rebalance_interval

                    # Rank tokens
                    available = coin_feat_by_ts.get(ts, {})
                    if len(available) < top_n * 2 + 2:
                        continue

                    ranked = []
                    for coin, f in available.items():
                        val = f.get(feat_key, 0)
                        if val == 0 and feat_key not in ("alt_rank_7d",):
                            continue
                        ranked.append((coin, val, f))

                    if len(ranked) < top_n * 2:
                        continue

                    ranked.sort(key=lambda x: x[1])

                    if direction == "momentum":
                        # LONG top, SHORT bottom
                        longs = ranked[-top_n:]
                        shorts = ranked[:top_n]
                    else:
                        # LONG bottom, SHORT top (mean reversion)
                        longs = ranked[:top_n]
                        shorts = ranked[-top_n:]

                    for coin, val, f in longs:
                        idx = f["_idx"]
                        if idx + 1 >= len(data[coin]):
                            continue
                        entry_price = data[coin][idx + 1]["o"]
                        if entry_price <= 0:
                            continue
                        positions[coin] = {
                            "dir": 1, "entry_price": entry_price,
                            "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
                        }

                    for coin, val, f in shorts:
                        if coin in positions:
                            continue
                        idx = f["_idx"]
                        if idx + 1 >= len(data[coin]):
                            continue
                        entry_price = data[coin][idx + 1]["o"]
                        if entry_price <= 0:
                            continue
                        positions[coin] = {
                            "dir": -1, "entry_price": entry_price,
                            "entry_idx": idx + 1, "entry_t": data[coin][idx + 1]["t"]
                        }

                if not trades or len(trades) < 20:
                    continue

                n = len(trades)
                pnl = sum(t["pnl"] for t in trades)
                avg = float(np.mean([t["net"] for t in trades]))
                wins = sum(1 for t in trades if t["net"] > 0)
                wr = wins / n * 100

                # Train/test split
                train_end_ts = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000
                train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < train_end_ts)
                test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= train_end_ts)
                train_n = sum(1 for t in trades if t["entry_t"] < train_end_ts)
                test_n = sum(1 for t in trades if t["entry_t"] >= train_end_ts)

                results.append({
                    "name": f"{factor_name} top{top_n} hold{hold*4}h",
                    "n": n, "pnl": pnl, "avg": avg, "wr": wr,
                    "train_pnl": train_pnl, "test_pnl": test_pnl,
                    "train_n": train_n, "test_n": test_n,
                })

    # Sort by total P&L
    results.sort(key=lambda x: x["pnl"], reverse=True)

    # Show all results
    print(f"\n  {'Factor':<40} {'N':>5} {'P&L':>8} {'Avg':>7} {'W%':>5} "
          f"{'Train$':>8} {'Test$':>8}")
    print(f"  {'-'*85}")
    for r in results[:30]:
        marker = "✓" if r["train_pnl"] > 0 and r["test_pnl"] > 0 else ""
        print(f"  {r['name']:<40} {r['n']:>5} ${r['pnl']:>+7.0f} "
              f"{r['avg']:>+5.1f}bp {r['wr']:>4.0f}% "
              f"${r['train_pnl']:>+7.0f} ${r['test_pnl']:>+7.0f} {marker}")

    # Bottom
    print(f"\n  ... worst:")
    for r in results[-5:]:
        print(f"  {r['name']:<40} {r['n']:>5} ${r['pnl']:>+7.0f} "
              f"{r['avg']:>+5.1f}bp {r['wr']:>4.0f}% "
              f"${r['train_pnl']:>+7.0f} ${r['test_pnl']:>+7.0f}")

    return results


# ══════════════════════════════════════════════════════════════════════
# 2. Machine Learning (Random Forest)
# ══════════════════════════════════════════════════════════════════════

def ml_random_forest(features, data):
    """Walk-forward Random Forest for return prediction."""
    print("\n" + "=" * 70)
    print("  MACHINE LEARNING — Random Forest")
    print("  Walk-forward: train 6mo, predict next 1mo, roll")
    print("=" * 70)

    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.metrics import accuracy_score
    except ImportError:
        print("  sklearn not installed. Skipping ML.")
        return None

    coins = [c for c in TOKENS if c in features]

    # Build feature matrix
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

    rows = []
    for coin in coins:
        candles = data[coin]
        for f in features[coin]:
            idx = f["_idx"]
            # Target: return over next 18 candles (72h)
            if idx + 19 >= len(candles):
                continue
            future_ret = (candles[idx + 18]["c"] / candles[idx + 1]["o"] - 1) * 1e4
            target = 1 if future_ret > COST_BPS else (-1 if future_ret < -COST_BPS else 0)

            row = [f.get(fn, 0) for fn in feat_names]
            row.append(f["t"])
            row.append(target)
            row.append(future_ret)
            row.append(coin)
            rows.append(row)

    X_cols = feat_names
    rows.sort(key=lambda r: r[len(feat_names)])  # sort by timestamp

    X = np.array([r[:len(feat_names)] for r in rows])
    timestamps = [r[len(feat_names)] for r in rows]
    targets = [r[len(feat_names) + 1] for r in rows]
    returns = [r[len(feat_names) + 2] for r in rows]
    coins_list = [r[len(feat_names) + 3] for r in rows]

    print(f"  Dataset: {len(rows)} samples, {len(feat_names)} features")
    print(f"  Target distribution: "
          f"LONG={sum(1 for t in targets if t==1)} "
          f"SHORT={sum(1 for t in targets if t==-1)} "
          f"FLAT={sum(1 for t in targets if t==0)}")

    # Walk-forward validation
    # Train on 6 months, predict next 1 month, roll forward
    train_window = 6 * 30 * 6  # ~6 months in 4h candles
    test_window = 30 * 6       # ~1 month in 4h candles

    all_predictions = []
    all_actuals = []
    all_returns_pred = []
    all_timestamps_pred = []
    feature_importances = np.zeros(len(feat_names))
    n_folds = 0

    for start in range(0, len(rows) - train_window - test_window, test_window):
        train_end = start + train_window
        test_end = min(train_end + test_window, len(rows))

        X_train = X[start:train_end]
        y_train = np.array(targets[start:train_end])
        X_test = X[train_end:test_end]
        y_test = np.array(targets[train_end:test_end])

        # Skip if not enough variety
        if len(set(y_train)) < 2:
            continue

        # Replace NaN/inf
        X_train = np.nan_to_num(X_train, nan=0, posinf=0, neginf=0)
        X_test = np.nan_to_num(X_test, nan=0, posinf=0, neginf=0)

        clf = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_leaf=20,
            random_state=42,
            n_jobs=-1,
        )
        clf.fit(X_train, y_train)

        preds = clf.predict(X_test)
        all_predictions.extend(preds)
        all_actuals.extend(y_test)
        all_returns_pred.extend(returns[train_end:test_end])
        all_timestamps_pred.extend(timestamps[train_end:test_end])

        feature_importances += clf.feature_importances_
        n_folds += 1

    if n_folds == 0:
        print("  No valid folds!")
        return None

    feature_importances /= n_folds

    # Analyze predictions
    preds = np.array(all_predictions)
    actuals = np.array(all_actuals)
    rets = np.array(all_returns_pred)
    ts_pred = np.array(all_timestamps_pred)

    # Accuracy
    acc = accuracy_score(actuals, preds)
    print(f"\n  Walk-forward accuracy: {acc:.3f} ({n_folds} folds)")

    # Feature importance
    fi_sorted = sorted(zip(feat_names, feature_importances),
                       key=lambda x: x[1], reverse=True)
    print(f"\n  Feature importance (top 10):")
    for fname, imp in fi_sorted[:10]:
        bar = "█" * int(imp * 100)
        print(f"    {fname:<20} {imp:.3f} {bar}")

    # P&L if we traded on predictions
    # Only trade when RF predicts LONG or SHORT (not FLAT)
    hold = 18
    trade_signals = [(ts_pred[i], preds[i], rets[i])
                     for i in range(len(preds)) if preds[i] != 0]

    if trade_signals:
        # Simple P&L: direction × actual return - cost
        rf_pnl = 0
        rf_trades = 0
        rf_wins = 0
        for ts, pred, actual_ret in trade_signals:
            net = pred * actual_ret / abs(pred) - COST_BPS  # normalize direction
            if net > 0:
                rf_wins += 1
            rf_pnl += POSITION_SIZE * net / 1e4
            rf_trades += 1

        # Train/test split
        train_end_ts = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000
        train_pnl = sum(POSITION_SIZE * (pred * ret / abs(pred) - COST_BPS) / 1e4
                        for ts, pred, ret in trade_signals if ts < train_end_ts)
        test_pnl = sum(POSITION_SIZE * (pred * ret / abs(pred) - COST_BPS) / 1e4
                       for ts, pred, ret in trade_signals if ts >= train_end_ts)

        print(f"\n  RF Trading results:")
        print(f"    Trades: {rf_trades} | Win: {rf_wins/rf_trades*100:.0f}%")
        print(f"    Total P&L: ${rf_pnl:+.0f}")
        print(f"    Train P&L: ${train_pnl:+.0f}")
        print(f"    Test P&L:  ${test_pnl:+.0f}")
        print(f"    Avg/trade: {rf_pnl/rf_trades*1e4/POSITION_SIZE:+.1f} bps")

        # Compare: what if we only traded HIGH CONFIDENCE predictions?
        if hasattr(clf, 'predict_proba'):
            print(f"\n  Note: RF predict_proba not available in walk-forward mode.")
            print(f"  Would need to re-run with probability thresholds.")

    # Gradient Boosting comparison
    print(f"\n  --- Gradient Boosting comparison ---")
    gb_pnl = 0
    gb_trades = 0
    gb_wins = 0
    gb_importances = np.zeros(len(feat_names))
    gb_folds = 0

    for start in range(0, len(rows) - train_window - test_window, test_window):
        train_end = start + train_window
        test_end = min(train_end + test_window, len(rows))

        X_train = np.nan_to_num(X[start:train_end], nan=0, posinf=0, neginf=0)
        y_train = np.array(targets[start:train_end])
        X_test = np.nan_to_num(X[train_end:test_end], nan=0, posinf=0, neginf=0)

        if len(set(y_train)) < 2:
            continue

        gb = GradientBoostingClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            min_samples_leaf=20,
            random_state=42,
        )
        gb.fit(X_train, y_train)
        preds_gb = gb.predict(X_test)

        for j in range(len(preds_gb)):
            if preds_gb[j] != 0:
                idx = train_end + j
                if idx >= len(returns):
                    continue
                net = preds_gb[j] * returns[idx] / abs(preds_gb[j]) - COST_BPS
                if net > 0:
                    gb_wins += 1
                gb_pnl += POSITION_SIZE * net / 1e4
                gb_trades += 1

        gb_importances += gb.feature_importances_
        gb_folds += 1

    if gb_trades > 0:
        print(f"    GB Trades: {gb_trades} | Win: {gb_wins/gb_trades*100:.0f}%")
        print(f"    GB Total P&L: ${gb_pnl:+.0f}")
        print(f"    GB Avg/trade: {gb_pnl/gb_trades*1e4/POSITION_SIZE:+.1f} bps")

        if gb_folds > 0:
            gb_importances /= gb_folds
            fi_gb = sorted(zip(feat_names, gb_importances),
                           key=lambda x: x[1], reverse=True)
            print(f"\n  GB Feature importance (top 10):")
            for fname, imp in fi_gb[:10]:
                bar = "█" * int(imp * 100)
                print(f"    {fname:<20} {imp:.3f} {bar}")

    return {
        "rf_pnl": rf_pnl if trade_signals else 0,
        "gb_pnl": gb_pnl,
        "feature_importance": fi_sorted,
    }


# ══════════════════════════════════════════════════════════════════════
# 3. Calendar Effects
# ══════════════════════════════════════════════════════════════════════

def calendar_effects(features, data):
    """Test day-of-week and time-of-day effects."""
    print("\n" + "=" * 70)
    print("  CALENDAR EFFECTS")
    print("  Day of week, time of day, month")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # For each candle, compute forward return
    by_dow = defaultdict(list)    # day of week → returns
    by_hour = defaultdict(list)   # hour → returns
    by_month = defaultdict(list)  # month → returns

    for coin in coins:
        candles = data[coin]
        for f in features[coin]:
            idx = f["_idx"]
            if idx + 7 >= len(candles):
                continue
            # Forward return: next 6 candles (24h)
            fwd_ret = (candles[idx + 6]["c"] / candles[idx + 1]["o"] - 1) * 1e4

            dt = datetime.fromtimestamp(f["t"] / 1000, tz=timezone.utc)
            by_dow[dt.weekday()].append(fwd_ret)
            by_hour[dt.hour].append(fwd_ret)
            by_month[dt.month].append(fwd_ret)

    # Day of week
    print(f"\n  Day of week (24h forward return, bps):")
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for dow in range(7):
        rets = by_dow[dow]
        if not rets:
            continue
        mean = float(np.mean(rets))
        median = float(np.median(rets))
        pos_pct = sum(1 for r in rets if r > 0) / len(rets) * 100
        se = float(np.std(rets)) / np.sqrt(len(rets))
        t_stat = mean / se if se > 0 else 0
        sig = "**" if abs(t_stat) > 2 else "*" if abs(t_stat) > 1.5 else ""
        print(f"    {dow_names[dow]}: mean={mean:>+6.1f}bp  median={median:>+6.1f}bp  "
              f"up={pos_pct:.0f}%  t={t_stat:>+5.2f} {sig}  (n={len(rets)})")

    # Hour of day (4h slots)
    print(f"\n  Hour of day (24h forward return, bps):")
    for hour in sorted(by_hour.keys()):
        rets = by_hour[hour]
        if not rets:
            continue
        mean = float(np.mean(rets))
        se = float(np.std(rets)) / np.sqrt(len(rets))
        t_stat = mean / se if se > 0 else 0
        sig = "**" if abs(t_stat) > 2 else "*" if abs(t_stat) > 1.5 else ""
        print(f"    {hour:>2}h: mean={mean:>+6.1f}bp  t={t_stat:>+5.2f} {sig}  (n={len(rets)})")

    # Month
    print(f"\n  Month (24h forward return, bps):")
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for month in range(1, 13):
        rets = by_month.get(month, [])
        if not rets:
            continue
        mean = float(np.mean(rets))
        se = float(np.std(rets)) / np.sqrt(len(rets))
        t_stat = mean / se if se > 0 else 0
        sig = "**" if abs(t_stat) > 2 else "*" if abs(t_stat) > 1.5 else ""
        print(f"    {month_names[month]}: mean={mean:>+6.1f}bp  t={t_stat:>+5.2f} {sig}  (n={len(rets)})")

    # Strategy: trade only on best/worst days
    print(f"\n  Strategy test: LONG on best days, SHORT on worst days")
    # Find significant days
    best_dow = max(range(7), key=lambda d: np.mean(by_dow.get(d, [0])))
    worst_dow = min(range(7), key=lambda d: np.mean(by_dow.get(d, [0])))

    print(f"    Best day: {dow_names[best_dow]} ({np.mean(by_dow[best_dow]):+.1f} bps)")
    print(f"    Worst day: {dow_names[worst_dow]} ({np.mean(by_dow[worst_dow]):+.1f} bps)")

    # Backtest: LONG on best day, SHORT on worst day
    for day, dir_str, direction in [(best_dow, "LONG", 1), (worst_dow, "SHORT", -1)]:
        pnl = 0
        n = 0
        wins = 0
        for coin in coins:
            for f in features[coin]:
                idx = f["_idx"]
                if idx + 7 >= len(data[coin]):
                    continue
                dt = datetime.fromtimestamp(f["t"] / 1000, tz=timezone.utc)
                if dt.weekday() != day:
                    continue
                entry = data[coin][idx + 1]["o"]
                exit_p = data[coin][idx + 6]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                pnl += POSITION_SIZE * net / 1e4
                n += 1
                if net > 0:
                    wins += 1

        if n > 0:
            print(f"    {dir_str} {dow_names[day]}: ${pnl:+.0f} ({n} trades, "
                  f"win={wins/n*100:.0f}%, avg={pnl/n*1e4/POSITION_SIZE:+.1f} bps)")


# ══════════════════════════════════════════════════════════════════════
# 4. Volatility Regime Switching
# ══════════════════════════════════════════════════════════════════════

def volatility_regimes(features, data):
    """Test if strategies work differently in high/low vol regimes."""
    print("\n" + "=" * 70)
    print("  VOLATILITY REGIME ANALYSIS")
    print("  Do strategies change behavior in high vs low vol?")
    print("=" * 70)

    coins = [c for c in TOKENS if c in features and c in data]

    # Classify each timestamp as high/low vol based on BTC 30d vol
    # Use BTC vol_30d as regime indicator
    btc_vols = []
    for f in features.get("BTC", features.get(coins[0], [])):
        btc_vols.append((f["t"], f.get("vol_30d", 0)))

    if not btc_vols:
        # Use alt-index dispersion instead
        for coin in coins[:1]:
            for f in features[coin]:
                btc_vols.append((f["t"], f.get("dispersion_7d", 0)))

    # Compute median vol for regime split
    vol_values = [v for _, v in btc_vols if v > 0]
    if not vol_values:
        print("  No vol data available")
        return

    vol_median = float(np.median(vol_values))
    vol_by_ts = {t: v for t, v in btc_vols}

    print(f"  Regime split: vol_30d median = {vol_median:.1f} bps")

    # For each regime, compute average forward returns
    regimes = {"LOW vol": [], "HIGH vol": []}
    for coin in coins:
        for f in features[coin]:
            idx = f["_idx"]
            if idx + 19 >= len(data[coin]):
                continue
            vol = vol_by_ts.get(f["t"], 0)
            if vol <= 0:
                continue
            fwd_ret = (data[coin][idx + 18]["c"] / data[coin][idx + 1]["o"] - 1) * 1e4
            regime = "LOW vol" if vol < vol_median else "HIGH vol"
            regimes[regime].append(fwd_ret)

    for regime, rets in regimes.items():
        if not rets:
            continue
        mean = float(np.mean(rets))
        pos = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"  {regime}: mean={mean:+.1f}bp, up={pos:.0f}%, n={len(rets)}")

    # Strategy: LONG in low vol, SHORT in high vol
    print(f"\n  Strategy: LONG low-vol candles, SHORT high-vol candles")
    for regime, direction, dir_name in [("LOW vol", 1, "LONG"), ("HIGH vol", -1, "SHORT")]:
        pnl = 0
        n = 0
        wins = 0
        train_pnl = 0
        test_pnl = 0
        train_end = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000

        for coin in coins:
            for f in features[coin]:
                idx = f["_idx"]
                if idx + 19 >= len(data[coin]):
                    continue
                vol = vol_by_ts.get(f["t"], 0)
                if vol <= 0:
                    continue
                is_regime = (vol < vol_median) if regime == "LOW vol" else (vol >= vol_median)
                if not is_regime:
                    continue

                entry = data[coin][idx + 1]["o"]
                exit_p = data[coin][idx + 18]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                trade_pnl = POSITION_SIZE * net / 1e4
                pnl += trade_pnl
                n += 1
                if net > 0:
                    wins += 1
                if f["t"] < train_end:
                    train_pnl += trade_pnl
                else:
                    test_pnl += trade_pnl

        if n > 0:
            print(f"    {dir_name} {regime}: ${pnl:+.0f} ({n}t, win={wins/n*100:.0f}%, "
                  f"avg={pnl/n*1e4/POSITION_SIZE:+.1f}bp) "
                  f"train=${train_pnl:+.0f} test=${test_pnl:+.0f}")

    # What about: use vol regime to filter existing strategies?
    print(f"\n  Vol regime × existing signals:")
    from analysis.backtest_genetic import Rule, Strategy, backtest_strategy

    strategies = {
        "S1_btc_rip": Strategy([Rule("btc_30d", ">", 2000, 1)], hold=42),
        "S2_alt_crash": Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=18),
        "S4_vol_short": Strategy([Rule("vol_ratio", "<", 1.0, -1),
                                  Rule("range_pct", "<", 200, -1)], hold=18),
    }

    for sname, strat in strategies.items():
        trades_all = backtest_strategy(strat, features, data, period="all")
        if not trades_all:
            continue

        # Split by vol regime at entry
        low_pnl = sum(t["pnl"] for t in trades_all
                       if vol_by_ts.get(t["entry_t"], vol_median) < vol_median)
        high_pnl = sum(t["pnl"] for t in trades_all
                        if vol_by_ts.get(t["entry_t"], vol_median) >= vol_median)
        low_n = sum(1 for t in trades_all
                    if vol_by_ts.get(t["entry_t"], vol_median) < vol_median)
        high_n = len(trades_all) - low_n

        print(f"    {sname:<20} LOW vol: ${low_pnl:>+7.0f} ({low_n}t) | "
              f"HIGH vol: ${high_pnl:>+7.0f} ({high_n}t)")


# ══════════════════════════════════════════════════════════════════════
# 5. Funding Rate Analysis
# ══════════════════════════════════════════════════════════════════════

def funding_analysis(data, features):
    """Analyze Hyperliquid funding rates as trading signals."""
    print("\n" + "=" * 70)
    print("  FUNDING RATE ANALYSIS (Hyperliquid)")
    print("=" * 70)

    print("\n  Fetching funding history from Hyperliquid...")
    funding = load_funding_data()

    if not funding:
        print("  No funding data available!")
        return

    print(f"  Loaded funding for {len(funding)} tokens")
    for coin in list(funding.keys())[:5]:
        n = len(funding[coin])
        rates = list(funding[coin].values())
        mean_bps = float(np.mean(rates)) * 1e4
        print(f"    {coin}: {n} records, mean={mean_bps:+.2f} bps")

    coins = [c for c in TOKENS if c in funding and c in data]
    if not coins:
        print("  No overlap between funding and price data!")
        return

    # Build funding features per 4h candle
    # For each candle, find the closest funding rate
    print(f"\n  Testing funding-based signals...")

    results = []

    for signal_name, condition_fn, direction in [
        ("High funding → SHORT", lambda rate: rate > 0.0001, -1),   # > 1 bps
        ("High funding → LONG", lambda rate: rate > 0.0001, 1),
        ("Low funding → LONG", lambda rate: rate < -0.0001, 1),     # < -1 bps
        ("Low funding → SHORT", lambda rate: rate < -0.0001, -1),
        ("Very high → SHORT", lambda rate: rate > 0.0003, -1),      # > 3 bps
        ("Very low → LONG", lambda rate: rate < -0.0003, 1),
        ("Positive → SHORT", lambda rate: rate > 0, -1),
        ("Negative → LONG", lambda rate: rate < 0, 1),
    ]:
        pnl = 0
        n = 0
        wins = 0

        for coin in coins:
            candles = data[coin]
            fund_ts = sorted(funding[coin].keys())

            for fi, f in enumerate(features.get(coin, [])):
                idx = f["_idx"]
                if idx + 19 >= len(candles):
                    continue

                # Find closest funding rate before this candle
                t = f["t"]
                # Binary search for closest timestamp
                closest_rate = None
                for ft in reversed(fund_ts):
                    if ft <= t:
                        closest_rate = funding[coin][ft]
                        break
                    if ft < t - 86400 * 1000:  # older than 1 day
                        break

                if closest_rate is None:
                    continue
                if not condition_fn(closest_rate):
                    continue

                entry = candles[idx + 1]["o"]
                exit_p = candles[idx + 18]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                pnl += POSITION_SIZE * net / 1e4
                n += 1
                if net > 0:
                    wins += 1

        if n > 50:
            avg = pnl / n * 1e4 / POSITION_SIZE
            wr = wins / n * 100
            results.append((signal_name, pnl, n, avg, wr))

    if results:
        print(f"\n  {'Signal':<30} {'P&L':>8} {'N':>6} {'Avg':>7} {'W%':>5}")
        print(f"  {'-'*60}")
        for name, pnl, n, avg, wr in sorted(results, key=lambda x: x[1], reverse=True):
            marker = "✓" if pnl > 0 else "✗"
            print(f"  {name:<30} ${pnl:>+7.0f} {n:>5}t {avg:>+5.1f}bp {wr:>4.0f}% {marker}")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  EXPLORATION ROUND 2")
    print("  Cross-sectional • ML • Calendar • Vol regimes • Funding")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    print("\nBuilding features...")
    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # Run all analyses
    cross_results = cross_sectional_factors(data, features)
    ml_results = ml_random_forest(features, data)
    calendar_effects(features, data)
    volatility_regimes(features, data)
    funding_analysis(data, features)

    # ── Final summary ──
    print(f"\n\n{'█'*70}")
    print(f"  EXPLORATION 2 — SUMMARY")
    print(f"{'█'*70}")

    print(f"\n  Cross-sectional: top 5 profitable on train+test:")
    for r in cross_results[:10]:
        if r["train_pnl"] > 0 and r["test_pnl"] > 0:
            print(f"    {r['name']:<40} ${r['pnl']:>+7.0f} "
                  f"(train=${r['train_pnl']:>+.0f}, test=${r['test_pnl']:>+.0f})")

    if ml_results:
        print(f"\n  ML: RF=${ml_results['rf_pnl']:+.0f} | GB=${ml_results['gb_pnl']:+.0f}")
        print(f"  Top features: {', '.join(f[0] for f in ml_results['feature_importance'][:5])}")


if __name__ == "__main__":
    main()
