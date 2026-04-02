"""S11 — OI-Price Divergence Signal Backtest.

Hypothesis: When OI rises significantly but price stagnates or drops,
leveraged longs are accumulating in a losing position → liquidation cascade likely.
Conversely, when OI drops while price rises → distribution, potential reversal.

Variants tested:
  A) OI up + price down → SHORT (longs about to be liquidated)
  B) OI up + price up   → LONG momentum (confirmed by leverage)
  C) OI down + price up → SHORT (distribution, smart money exiting)
  D) OI up + price down → LONG (contrarian: longs will push harder)

Data: Bybit 4h OI (24 months) + Hyperliquid 4h candles.
Period: ~Apr 2024 to Apr 2026 (overlap of both datasets).

Usage:
    python3 -m analysis.backtest_oi_divergence
"""

from __future__ import annotations
import json, os
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, TRAIN_END, TEST_START,
)
from backtests.backtest_sector import TOKEN_SECTOR

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

LEVERAGE = 2.0
COST_EFFECTIVE = (COST_BPS + (LEVERAGE - 1) * 2) * LEVERAGE


def load_oi_data():
    """Load Bybit 4h OI for all tokens. Returns {token: {timestamp_ms: oi_coins}}."""
    oi = {}
    for token in TOKENS + ["BTC", "ETH"]:
        path = os.path.join(DATA_DIR, f"{token}_oi_4h.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            records = json.load(f)
        oi[token] = {r["t"]: r["oi"] for r in records}
    return oi


def compute_oi_features(oi_data, data):
    """Compute OI delta features aligned to candle timestamps.

    Returns {(timestamp, coin): {"oi_delta_6h": ..., "oi_delta_24h": ..., "oi_delta_7d": ...}}
    """
    result = {}
    for coin in TOKENS:
        if coin not in oi_data or coin not in data:
            continue

        candle_ts = [c["t"] for c in data[coin]]
        oi_ts_sorted = sorted(oi_data[coin].keys())

        if not oi_ts_sorted:
            continue

        # Build OI array aligned to 4h intervals
        oi_by_ts = oi_data[coin]

        for ts in candle_ts:
            # Find closest OI timestamp (within 2h tolerance)
            best_oi_ts = None
            for ot in oi_ts_sorted:
                if abs(ot - ts) < 2 * 3600 * 1000:
                    best_oi_ts = ot
                    break
                if ot > ts + 2 * 3600 * 1000:
                    break

            if best_oi_ts is None:
                # Binary search for closest
                lo, hi = 0, len(oi_ts_sorted) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    if oi_ts_sorted[mid] < ts:
                        lo = mid + 1
                    else:
                        hi = mid
                if lo > 0 and abs(oi_ts_sorted[lo-1] - ts) < abs(oi_ts_sorted[lo] - ts):
                    best_oi_ts = oi_ts_sorted[lo-1]
                elif lo < len(oi_ts_sorted):
                    best_oi_ts = oi_ts_sorted[lo]

                if best_oi_ts is None or abs(best_oi_ts - ts) > 4 * 3600 * 1000:
                    continue

            oi_now = oi_by_ts[best_oi_ts]

            # Find OI at various lookbacks
            def get_oi_at(lookback_ms):
                target = best_oi_ts - lookback_ms
                best = None
                best_dist = float("inf")
                for ot in oi_ts_sorted:
                    d = abs(ot - target)
                    if d < best_dist:
                        best_dist = d
                        best = ot
                    if ot > target + 8 * 3600 * 1000:
                        break
                if best and best_dist < 8 * 3600 * 1000:
                    return oi_by_ts[best]
                return None

            oi_6h = get_oi_at(6 * 3600 * 1000)
            oi_24h = get_oi_at(24 * 3600 * 1000)
            oi_7d = get_oi_at(7 * 24 * 3600 * 1000)

            feat = {}
            if oi_6h and oi_6h > 0:
                feat["oi_delta_6h"] = (oi_now / oi_6h - 1) * 100
            if oi_24h and oi_24h > 0:
                feat["oi_delta_24h"] = (oi_now / oi_24h - 1) * 100
            if oi_7d and oi_7d > 0:
                feat["oi_delta_7d"] = (oi_now / oi_7d - 1) * 100

            if feat:
                result[(ts, coin)] = feat

    return result


def backtest_s11(features, data, oi_features, config):
    """Backtest OI-Price divergence signal."""
    hold = config.get("hold", 12)  # candles
    oi_lookback = config.get("oi_lookback", "oi_delta_24h")
    oi_threshold = config.get("oi_threshold", 5.0)  # OI up by X%
    price_threshold = config.get("price_threshold", -200)  # price down by X bps
    direction = config.get("direction", -1)  # -1=SHORT, +1=LONG
    variant = config.get("variant", "A")
    stop_bps = config.get("stop_bps", -2500)
    max_pos = config.get("max_pos", 4)
    start_capital = 1000

    coins = [c for c in TOKENS if c in features and c in data]

    # Build lookups
    coin_by_ts = {}
    all_ts = set()
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    effective_stop = stop_bps / LEVERAGE

    for ts in sorted(all_ts):
        # EXITS
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

            exit_reason = None
            exit_price = current

            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 + effective_stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < effective_stop:
                    exit_reason = "stop"
                    exit_price = pos["entry"] * (1 - effective_stop / 1e4)

            if held >= hold:
                exit_reason = "timeout"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4 * LEVERAGE
                net = gross - COST_EFFECTIVE
                pnl = pos["size"] * net / 1e4
                capital += pnl
                trades.append({"pnl": pnl, "net": net, "strat": "S11",
                    "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts,
                    "oi_delta": pos["oi_delta"], "ret": pos["ret"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # SIGNALS
        if len(positions) >= max_pos:
            continue

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue

            f = feat_by_ts.get(ts, {}).get(coin)
            oi_f = oi_features.get((ts, coin))
            if not f or not oi_f:
                continue

            oi_delta = oi_f.get(oi_lookback, 0)
            ret_24h = f.get("ret_6h", 0)  # ret_6h = 6 candles × 4h = 24h

            # Variant conditions
            trigger = False
            sig_dir = direction

            if variant == "A":
                # OI up + price down → SHORT
                trigger = oi_delta >= oi_threshold and ret_24h <= price_threshold
                sig_dir = -1
            elif variant == "B":
                # OI up + price up → LONG (momentum confirmed)
                trigger = oi_delta >= oi_threshold and ret_24h >= abs(price_threshold)
                sig_dir = 1
            elif variant == "C":
                # OI down + price up → SHORT (distribution)
                trigger = oi_delta <= -oi_threshold and ret_24h >= abs(price_threshold)
                sig_dir = -1
            elif variant == "D":
                # OI up + price down → LONG (contrarian)
                trigger = oi_delta >= oi_threshold and ret_24h <= price_threshold
                sig_dir = 1
            elif variant == "E":
                # OI extreme rise → FADE (direction based on price)
                trigger = oi_delta >= oi_threshold
                sig_dir = -1 if ret_24h > 0 else 1
            elif variant == "F":
                # OI drop + price drop → LONG (panic selling exhaustion)
                trigger = oi_delta <= -oi_threshold and ret_24h <= price_threshold
                sig_dir = 1

            if trigger:
                candidates.append({
                    "coin": coin, "dir": sig_dir,
                    "strength": abs(oi_delta),
                    "oi_delta": oi_delta, "ret": ret_24h,
                })

        # Rank by OI delta strength
        candidates.sort(key=lambda x: x["strength"], reverse=True)

        for cand in candidates:
            if len(positions) >= max_pos:
                break
            coin = cand["coin"]
            if coin in positions:
                continue

            # Sector limit
            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= 2:
                    continue

            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            idx_f = f["_idx"]
            if idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            size = capital * 0.12

            positions[coin] = {
                "dir": cand["dir"], "entry": entry,
                "idx": idx_f + 1, "entry_t": data[coin][idx_f + 1]["t"],
                "hold": hold, "size": size, "coin": coin,
                "oi_delta": cand["oi_delta"], "ret": cand["ret"],
            }

    if not trades:
        return {"pnl": 0, "n": 0, "avg": 0, "win": 0, "train": 0, "test": 0}

    n = len(trades)
    pnl = capital - start_capital
    wins = sum(1 for t in trades if t["net"] > 0)
    avg = float(np.mean([t["net"] for t in trades]))
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    return {
        "pnl": round(pnl, 0), "n": n,
        "avg": round(avg, 1), "win": round(wins/n*100, 0),
        "train": round(train_pnl, 0), "test": round(test_pnl, 0),
    }


def main():
    print("=" * 100)
    print("  S11 — OI-Price Divergence Signal Backtest")
    print("  Bybit OI (4h, 24 months) × Hyperliquid candles")
    print("=" * 100)

    data = load_3y_candles()
    features = build_features(data)
    oi_data = load_oi_data()
    print(f"Price data: {len(data)} tokens")
    print(f"OI data: {len(oi_data)} tokens")

    print("\nComputing OI features (aligned to candle timestamps)...")
    oi_features = compute_oi_features(oi_data, data)
    print(f"OI features: {len(oi_features):,} (token, timestamp) pairs")

    # Check overlap
    sample_coin = "ARB"
    oi_ts = set(ts for (ts, c) in oi_features if c == sample_coin)
    candle_ts = set(c["t"] for c in data.get(sample_coin, []))
    overlap = oi_ts & candle_ts
    if overlap:
        min_dt = datetime.fromtimestamp(min(overlap)/1000, tz=timezone.utc)
        max_dt = datetime.fromtimestamp(max(overlap)/1000, tz=timezone.utc)
        print(f"Overlap period ({sample_coin}): {min_dt.date()} → {max_dt.date()} ({len(overlap)} candles)")

    # ═══════════════════════════════════════════════════════════
    # PHASE 1: Test all 6 variants with various thresholds
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*100}")
    print("  PHASE 1: Signal Variants × Thresholds")
    print("=" * 100)

    variants = [
        ("A", "OI↑ + Price↓ → SHORT"),
        ("B", "OI↑ + Price↑ → LONG (momentum)"),
        ("C", "OI↓ + Price↑ → SHORT (distribution)"),
        ("D", "OI↑ + Price↓ → LONG (contrarian)"),
        ("E", "OI extreme → FADE price dir"),
        ("F", "OI↓ + Price↓ → LONG (exhaustion)"),
    ]

    oi_thresholds = [3, 5, 8, 10, 15]
    price_thresholds = [-100, -200, -500, -1000]
    holds = [6, 12, 18]  # 24h, 48h, 72h

    print(f"\n  {'Variant':<35} {'OI%':>4} {'Ret':>6} {'Hold':>4} | {'N':>5} {'W%':>4} {'Avg':>6} "
          f"{'P&L':>7} {'Train':>6} {'Test':>6} | Valid")
    print(f"  {'-'*105}")

    all_results = []

    for var_code, var_label in variants:
        for oi_t in oi_thresholds:
            for price_t in price_thresholds:
                for h in holds:
                    cfg = {
                        "variant": var_code,
                        "oi_lookback": "oi_delta_24h",
                        "oi_threshold": oi_t,
                        "price_threshold": price_t,
                        "hold": h,
                        "max_pos": 4,
                        "stop_bps": -2500,
                    }
                    r = backtest_s11(features, data, oi_features, cfg)

                    if r["n"] < 10:
                        continue

                    valid = r["train"] > 0 and r["test"] > 0
                    all_results.append((var_code, var_label, oi_t, price_t, h, r, valid))

    # Sort by avg bps
    all_results.sort(key=lambda x: x[5]["avg"], reverse=True)

    # Show top 20
    shown = 0
    for var_code, var_label, oi_t, price_t, h, r, valid in all_results[:30]:
        marker = " ✓" if valid else ""
        print(f"  {var_label:<35} {oi_t:>3}% {price_t:>+5} {h*4:>3}h | "
              f"{r['n']:>4} {r['win']:>3.0f}% {r['avg']:>+5.0f} "
              f"${r['pnl']:>+6,} ${r['train']:>+5,} ${r['test']:>+5,} |{marker}")
        shown += 1

    # ═══════════════════════════════════════════════════════════
    # PHASE 2: Focus on valid results (train+test > 0)
    # ═══════════════════════════════════════════════════════════
    valid_results = [x for x in all_results if x[6]]

    print(f"\n{'='*100}")
    print(f"  PHASE 2: Valid results (train > 0 AND test > 0): {len(valid_results)}/{len(all_results)}")
    print("=" * 100)

    if valid_results:
        valid_results.sort(key=lambda x: x[5]["avg"], reverse=True)
        print(f"\n  TOP 15 by avg bps/trade (valid only):")
        print(f"  {'Variant':<35} {'OI%':>4} {'Ret':>6} {'Hold':>4} | {'N':>5} {'W%':>4} {'Avg':>6} "
              f"{'P&L':>7} {'Train':>6} {'Test':>6}")
        print(f"  {'-'*100}")
        for var_code, var_label, oi_t, price_t, h, r, valid in valid_results[:15]:
            print(f"  {var_label:<35} {oi_t:>3}% {price_t:>+5} {h*4:>3}h | "
                  f"{r['n']:>4} {r['win']:>3.0f}% {r['avg']:>+5.0f} "
                  f"${r['pnl']:>+6,} ${r['train']:>+5,} ${r['test']:>+5,}")

        # Also test with 7d OI lookback for best variants
        print(f"\n  Testing best variants with 7d OI lookback...")
        best_var = valid_results[0]
        for lookback in ["oi_delta_6h", "oi_delta_24h", "oi_delta_7d"]:
            cfg = {
                "variant": best_var[0],
                "oi_lookback": lookback,
                "oi_threshold": best_var[2],
                "price_threshold": best_var[3],
                "hold": best_var[4],
                "max_pos": 4,
                "stop_bps": -2500,
            }
            r = backtest_s11(features, data, oi_features, cfg)
            valid = "✓" if r["train"] > 0 and r["test"] > 0 else ""
            print(f"    {lookback:<16}: {r['n']:>4} trades, {r['win']:.0f}% win, "
                  f"{r['avg']:>+.0f} bps/t, ${r['pnl']:>+,} (train ${r['train']:>+,} test ${r['test']:>+,}) {valid}")
    else:
        print("  No valid results found!")

    # Summary
    print(f"\n{'='*100}")
    print("  SUMMARY")
    print("=" * 100)
    total_tested = len(all_results)
    print(f"  Tested: {total_tested} configurations")
    print(f"  Valid (train+test > 0): {len(valid_results)}")
    if valid_results:
        best = valid_results[0]
        print(f"  Best: {best[1]} | OI>{best[2]}% | ret<{best[3]}bps | hold={best[4]*4}h")
        print(f"         {best[5]['n']} trades, {best[5]['avg']:+.0f} bps/t, ${best[5]['pnl']:>+,}")
    else:
        print(f"  ⚠ No variant passes train+test validation")


if __name__ == "__main__":
    main()
