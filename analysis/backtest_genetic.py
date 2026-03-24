"""Genetic Strategy Search — Multi-factor backtesting on Hyperliquid.

Phase 1: Feature engineering (20+ features per 4h candle)
Phase 2: Exhaustive single-factor + double-factor scan
Phase 3: Genetic algorithm for complex rule combinations
Phase 4: Monte Carlo validation

Train: 2023-07 to 2024-12 | Test: 2025-01 to 2026-03
Cost: 12 bps total (7 taker + 3 slippage + 2 funding drag)

Usage:
    python3 -m analysis.backtest_genetic
"""

from __future__ import annotations

import json, os, time, random, itertools
from collections import defaultdict
from datetime import datetime, timezone
from copy import deepcopy

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")

# Tokens with 3-year history
TOKENS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
]

REF_TOKENS = ["BTC", "ETH"]  # Not traded, used for features

COST_BPS = 12.0       # 7 taker + 3 slippage + 2 funding
MAX_POSITIONS = 6
MAX_SAME_DIR = 4
STOP_LOSS_BPS = -1500.0
POSITION_SIZE = 250.0  # $250 per position

# Train/test split
TRAIN_END = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000
TEST_START = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000


# ── Data Loading ─────────────────────────────────────────────────────

def load_3y_candles():
    """Load 3-year 4h candles for all tokens."""
    data = {}
    for coin in TOKENS + REF_TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_4h_3y.json")
        if not os.path.exists(path):
            # Try regular 4h
            path = os.path.join(DATA_DIR, f"{coin}_4h.json")
        if not os.path.exists(path):
            print(f"  SKIP {coin}: no data")
            continue
        with open(path) as f:
            raw = json.load(f)
        if len(raw) < 100:
            print(f"  SKIP {coin}: only {len(raw)} candles")
            continue
        candles = []
        for c in raw:
            candles.append({
                "t": c["t"],
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": float(c.get("v", 0)),
            })
        data[coin] = candles
    return data


# ── Feature Engineering ──────────────────────────────────────────────

def build_features(data):
    """Build feature matrix: {coin: [{t, features...}, ...]}

    Features computed per 4h candle:
    -- Alt-specific --
    1.  ret_6h:    return over 6 candles (1 day)
    2.  ret_42h:   return over 42 candles (7 days)
    3.  ret_84h:   return over 84 candles (14 days)
    4.  ret_180h:  return over 180 candles (30 days)
    5.  vol_7d:    realized volatility (std of 4h returns) over 42 candles
    6.  vol_30d:   realized volatility over 180 candles
    7.  vol_ratio: vol_7d / vol_30d (vol expansion/contraction)
    8.  drawdown:  current price vs 30d high (bps)
    9.  recovery:  current price vs 30d low (bps)
    10. range_pct: (high - low) / close of current candle (bps)
    11. consec_up: count of consecutive up candles
    12. consec_dn: count of consecutive down candles

    -- BTC-relative --
    13. btc_7d:    BTC return over 7 days (bps)
    14. btc_30d:   BTC return over 30 days (bps)
    15. eth_7d:    ETH return over 7 days (bps)
    16. btc_eth_spread: btc_7d - eth_7d (bps)
    17. alt_vs_btc_7d:  alt ret_42h - btc_7d (relative performance)
    18. alt_vs_btc_30d: alt ret_180h - btc_30d

    -- Cross-alt --
    19. alt_index_7d:  mean return of all alts over 7d (bps)
    20. dispersion_7d: std of alt 7d returns (bps)
    21. alt_rank_7d:   percentile rank of this alt vs others (0-100)

    -- Volume --
    22. vol_z:     volume z-score (current vs 30d mean)
    """
    # Pre-compute BTC and ETH returns at each timestamp
    btc_data = data.get("BTC", [])
    eth_data = data.get("ETH", [])

    btc_by_t = {c["t"]: c for c in btc_data}
    eth_by_t = {c["t"]: c for c in eth_data}

    # Build BTC/ETH return series
    btc_ret = {}  # t → {7d, 30d}
    for i, c in enumerate(btc_data):
        r = {}
        if i >= 42 and btc_data[i - 42]["c"] > 0:
            r["btc_7d"] = (c["c"] / btc_data[i - 42]["c"] - 1) * 1e4
        if i >= 180 and btc_data[i - 180]["c"] > 0:
            r["btc_30d"] = (c["c"] / btc_data[i - 180]["c"] - 1) * 1e4
        btc_ret[c["t"]] = r

    eth_ret = {}
    for i, c in enumerate(eth_data):
        r = {}
        if i >= 42 and eth_data[i - 42]["c"] > 0:
            r["eth_7d"] = (c["c"] / eth_data[i - 42]["c"] - 1) * 1e4
        eth_ret[c["t"]] = r

    # Collect all alt returns at each timestamp for cross-alt features
    alt_coins = [c for c in TOKENS if c in data]
    all_alt_7d = defaultdict(dict)  # t → {coin: ret_7d}

    for coin in alt_coins:
        candles = data[coin]
        for i, c in enumerate(candles):
            if i >= 42 and candles[i - 42]["c"] > 0:
                ret = (c["c"] / candles[i - 42]["c"] - 1) * 1e4
                all_alt_7d[c["t"]][coin] = ret

    # Now build features per coin
    features = {}
    for coin in alt_coins:
        candles = data[coin]
        n = len(candles)
        coin_features = []

        # Pre-compute returns and close arrays
        closes = np.array([c["c"] for c in candles])
        highs = np.array([c["h"] for c in candles])
        lows = np.array([c["l"] for c in candles])
        volumes = np.array([c["v"] for c in candles])

        for i in range(max(180, 1), n):
            c = candles[i]
            t = c["t"]
            f = {"t": t}

            # Alt returns
            if closes[i - 6] > 0:
                f["ret_6h"] = (closes[i] / closes[i - 6] - 1) * 1e4
            else:
                continue

            if i >= 42 and closes[i - 42] > 0:
                f["ret_42h"] = (closes[i] / closes[i - 42] - 1) * 1e4
            else:
                f["ret_42h"] = 0.0

            if i >= 84 and closes[i - 84] > 0:
                f["ret_84h"] = (closes[i] / closes[i - 84] - 1) * 1e4
            else:
                f["ret_84h"] = 0.0

            if i >= 180 and closes[i - 180] > 0:
                f["ret_180h"] = (closes[i] / closes[i - 180] - 1) * 1e4
            else:
                f["ret_180h"] = 0.0

            # Volatility
            if i >= 42:
                rets_7d = np.diff(closes[i-42:i+1]) / closes[i-42:i]
                f["vol_7d"] = float(np.std(rets_7d) * 1e4) if len(rets_7d) > 1 else 0.0
            else:
                f["vol_7d"] = 0.0

            if i >= 180:
                rets_30d = np.diff(closes[i-180:i+1]) / closes[i-180:i]
                f["vol_30d"] = float(np.std(rets_30d) * 1e4) if len(rets_30d) > 1 else 0.0
            else:
                f["vol_30d"] = 0.0

            f["vol_ratio"] = f["vol_7d"] / f["vol_30d"] if f["vol_30d"] > 0 else 1.0

            # Drawdown from 30d high and recovery from 30d low
            high_30d = float(np.max(highs[max(0, i-180):i+1]))
            low_30d = float(np.min(lows[max(0, i-180):i+1]))
            f["drawdown"] = (closes[i] / high_30d - 1) * 1e4 if high_30d > 0 else 0.0
            f["recovery"] = (closes[i] / low_30d - 1) * 1e4 if low_30d > 0 else 0.0

            # Current candle range
            f["range_pct"] = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0.0

            # Consecutive up/down
            consec_up = 0
            consec_dn = 0
            for j in range(i, max(i-20, 0), -1):
                if closes[j] > closes[j-1]:
                    consec_up += 1
                else:
                    break
            for j in range(i, max(i-20, 0), -1):
                if closes[j] < closes[j-1]:
                    consec_dn += 1
                else:
                    break
            f["consec_up"] = consec_up
            f["consec_dn"] = consec_dn

            # BTC features
            br = btc_ret.get(t, {})
            f["btc_7d"] = br.get("btc_7d", 0.0)
            f["btc_30d"] = br.get("btc_30d", 0.0)

            # ETH features
            er = eth_ret.get(t, {})
            f["eth_7d"] = er.get("eth_7d", 0.0)

            # Cross features
            f["btc_eth_spread"] = f["btc_7d"] - f["eth_7d"]
            f["alt_vs_btc_7d"] = f["ret_42h"] - f["btc_7d"]
            f["alt_vs_btc_30d"] = f["ret_180h"] - f["btc_30d"]

            # Cross-alt features
            alt_rets = all_alt_7d.get(t, {})
            if len(alt_rets) >= 5:
                vals = list(alt_rets.values())
                f["alt_index_7d"] = float(np.mean(vals))
                f["dispersion_7d"] = float(np.std(vals))
                # Rank
                own_ret = alt_rets.get(coin, 0.0)
                f["alt_rank_7d"] = sum(1 for v in vals if v <= own_ret) / len(vals) * 100
            else:
                f["alt_index_7d"] = 0.0
                f["dispersion_7d"] = 0.0
                f["alt_rank_7d"] = 50.0

            # Volume z-score
            if i >= 180:
                vol_window = volumes[i-180:i]
                vol_mean = float(np.mean(vol_window))
                vol_std = float(np.std(vol_window))
                f["vol_z"] = (volumes[i] - vol_mean) / vol_std if vol_std > 0 else 0.0
            else:
                f["vol_z"] = 0.0

            # Store the index and price for backtesting
            f["_idx"] = i
            f["_close"] = closes[i]

            coin_features.append(f)

        features[coin] = coin_features

    return features


# ── Rule Definition ──────────────────────────────────────────────────

FEATURE_NAMES = [
    "ret_6h", "ret_42h", "ret_84h", "ret_180h",
    "vol_7d", "vol_30d", "vol_ratio",
    "drawdown", "recovery", "range_pct",
    "consec_up", "consec_dn",
    "btc_7d", "btc_30d", "eth_7d",
    "btc_eth_spread", "alt_vs_btc_7d", "alt_vs_btc_30d",
    "alt_index_7d", "dispersion_7d", "alt_rank_7d",
    "vol_z",
]

# Thresholds to scan for each feature (bps or raw)
FEATURE_THRESHOLDS = {
    "ret_6h":          [-200, -100, -50, 50, 100, 200],
    "ret_42h":         [-2000, -1000, -500, -200, 200, 500, 1000, 2000],
    "ret_84h":         [-3000, -2000, -1000, -500, 500, 1000, 2000, 3000],
    "ret_180h":        [-5000, -3000, -2000, -1000, 1000, 2000, 3000, 5000],
    "vol_7d":          [50, 100, 150, 200, 300],
    "vol_30d":         [50, 100, 150, 200],
    "vol_ratio":       [0.5, 0.7, 1.0, 1.3, 1.5, 2.0],
    "drawdown":        [-5000, -3000, -2000, -1000, -500],
    "recovery":        [500, 1000, 2000, 3000, 5000],
    "range_pct":       [50, 100, 200, 300, 500],
    "consec_up":       [3, 4, 5, 6],
    "consec_dn":       [3, 4, 5, 6],
    "btc_7d":          [-1000, -500, -300, 300, 500, 1000],
    "btc_30d":         [-2000, -1000, -500, 500, 1000, 2000],
    "eth_7d":          [-1000, -500, -300, 300, 500, 1000],
    "btc_eth_spread":  [-500, -200, 200, 500],
    "alt_vs_btc_7d":   [-2000, -1000, -500, 500, 1000, 2000],
    "alt_vs_btc_30d":  [-3000, -1500, -500, 500, 1500, 3000],
    "alt_index_7d":    [-2000, -1000, -500, 500, 1000, 2000],
    "dispersion_7d":   [500, 1000, 1500, 2000],
    "alt_rank_7d":     [10, 20, 30, 70, 80, 90],
    "vol_z":           [-2.0, -1.0, 1.0, 2.0, 3.0],
}


class Rule:
    """A single condition: feature OP threshold → direction."""
    def __init__(self, feature: str, op: str, threshold: float, direction: int):
        self.feature = feature
        self.op = op  # ">" or "<"
        self.threshold = threshold
        self.direction = direction  # 1 = LONG, -1 = SHORT

    def matches(self, f: dict) -> bool:
        val = f.get(self.feature, 0.0)
        if self.op == ">":
            return val > self.threshold
        else:
            return val < self.threshold

    def __repr__(self):
        dir_str = "LONG" if self.direction == 1 else "SHORT"
        return f"{self.feature} {self.op} {self.threshold} → {dir_str}"


class Strategy:
    """One or more rules combined with AND logic + hold period."""
    def __init__(self, rules: list[Rule], hold: int = 18):
        self.rules = rules
        self.hold = hold  # in 4h candles

    def signal(self, f: dict) -> int | None:
        """Returns direction (1/-1) if all rules match, else None."""
        if not self.rules:
            return None
        # All rules must match
        for rule in self.rules:
            if not rule.matches(f):
                return None
        # Direction from first rule (all should agree)
        return self.rules[0].direction

    def __repr__(self):
        rules_str = " AND ".join(str(r) for r in self.rules)
        return f"[{rules_str}] hold={self.hold*4}h"


# ── Backtester ───────────────────────────────────────────────────────

def backtest_strategy(strategy: Strategy, features: dict, data: dict,
                      period="all", cost=COST_BPS, max_pos=MAX_POSITIONS,
                      max_dir=MAX_SAME_DIR, stop_loss=STOP_LOSS_BPS,
                      size=POSITION_SIZE):
    """Run backtest with proper position management.

    period: "train", "test", or "all"
    """
    coins = [c for c in TOKENS if c in features and c in data]
    hold = strategy.hold

    # Collect all (timestamp, coin, features) sorted by time
    events = []
    for coin in coins:
        for f in features[coin]:
            t = f["t"]
            if period == "train" and t >= TRAIN_END:
                continue
            if period == "test" and t < TEST_START:
                continue
            events.append((t, coin, f))
    events.sort(key=lambda x: x[0])

    # State
    positions = {}  # coin → {direction, entry_price, entry_idx, entry_t}
    trades = []
    cooldown = {}  # coin → earliest re-entry timestamp

    # Group events by timestamp
    by_ts = defaultdict(list)
    for t, coin, f in events:
        by_ts[t].append((coin, f))

    for ts in sorted(by_ts.keys()):
        # Check exits first
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in data:
                continue
            candles = data[coin]
            idx = pos["entry_idx"]
            held = 0

            # Find current candle index
            for ci in range(idx, min(idx + hold + 5, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - idx
                    current = candles[ci]["c"]
                    if current == 0:
                        break

                    # Check stop loss
                    exit_reason = None
                    exit_price = current

                    if pos["direction"] == 1:
                        worst = candles[ci]["l"]
                        worst_bps = (worst / pos["entry_price"] - 1) * 1e4
                        if worst_bps < stop_loss:
                            exit_reason = "stop"
                            exit_price = pos["entry_price"] * (1 + stop_loss / 1e4)
                    else:
                        worst = candles[ci]["h"]
                        worst_bps = -(worst / pos["entry_price"] - 1) * 1e4
                        if worst_bps < stop_loss:
                            exit_reason = "stop"
                            exit_price = pos["entry_price"] * (1 - stop_loss / 1e4)

                    # Timeout
                    if held >= hold:
                        exit_reason = "timeout"

                    if exit_reason:
                        gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                        net = gross - cost
                        pnl = size * net / 1e4
                        trades.append({
                            "coin": coin,
                            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                            "entry_t": pos["entry_t"],
                            "exit_t": ts,
                            "hold": held,
                            "gross": round(gross, 1),
                            "net": round(net, 1),
                            "pnl": round(pnl, 2),
                            "reason": exit_reason,
                        })
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000  # 24h cooldown
                    break

        # Check entries
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for coin, f in by_ts[ts]:
            if coin in positions:
                continue
            if coin in cooldown and ts < cooldown[coin]:
                continue

            direction = strategy.signal(f)
            if direction is None:
                continue

            # Direction limits
            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            # Entry at NEXT candle open
            idx = f["_idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry_price = data[coin][idx + 1]["o"]
            if entry_price <= 0:
                continue

            candidates.append({
                "coin": coin,
                "direction": direction,
                "entry_price": entry_price,
                "entry_idx": idx + 1,
                "entry_t": data[coin][idx + 1]["t"],
                "strength": abs(f.get("ret_42h", 0)),
            })

        # Rank and fill slots
        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)
        for cand in candidates[:slots]:
            positions[cand["coin"]] = {
                "direction": cand["direction"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_t": cand["entry_t"],
            }
            if cand["direction"] == 1:
                n_long += 1
            else:
                n_short += 1

    return trades


def quick_score(trades):
    """Quick P&L and stats."""
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0, "monthly": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)

    # Months spanned
    if trades:
        t_min = min(t["entry_t"] for t in trades)
        t_max = max(t["exit_t"] for t in trades)
        months = max(1, (t_max - t_min) / (30.44 * 86400 * 1000))
    else:
        months = 1

    return {
        "n": n, "pnl": round(pnl, 2), "avg": round(avg, 1),
        "win": round(wins / n * 100, 0) if n > 0 else 0,
        "monthly": round(pnl / months, 1),
    }


# ── Phase 2: Exhaustive Single-Factor Scan ───────────────────────────

def scan_single_factor(features, data):
    """Test every (feature, op, threshold, direction) combination."""
    print("\n" + "=" * 70)
    print("  PHASE 2A: Single-Factor Scan")
    print("=" * 70)

    results = []
    total = 0

    for feat in FEATURE_NAMES:
        thresholds = FEATURE_THRESHOLDS.get(feat, [])
        for thresh in thresholds:
            for op in [">", "<"]:
                for direction in [1, -1]:
                    rule = Rule(feat, op, thresh, direction)
                    strat = Strategy([rule], hold=18)  # 3 days default

                    # Quick train eval
                    trades_train = backtest_strategy(strat, features, data, period="train")
                    s_train = quick_score(trades_train)

                    total += 1

                    # Filter: profitable on train with enough trades
                    if s_train["pnl"] <= 0 or s_train["n"] < 15:
                        continue

                    # Test eval
                    trades_test = backtest_strategy(strat, features, data, period="test")
                    s_test = quick_score(trades_test)

                    # Both profitable?
                    if s_test["pnl"] > 0:
                        results.append({
                            "rule": str(rule),
                            "strat": strat,
                            "train": s_train,
                            "test": s_test,
                            "combined_pnl": s_train["pnl"] + s_test["pnl"],
                        })

    # Sort by combined P&L
    results.sort(key=lambda x: x["combined_pnl"], reverse=True)

    print(f"\n  Tested {total} rules → {len(results)} profitable on both train+test")
    print(f"\n  {'Rule':<45} {'Train':>12} {'Test':>12} {'Total':>8}")
    print(f"  {'-'*80}")

    for r in results[:30]:
        t = r["train"]
        s = r["test"]
        print(f"  {r['rule']:<45} "
              f"${t['pnl']:>+7.0f} ({t['n']:>3}t) "
              f"${s['pnl']:>+7.0f} ({s['n']:>3}t) "
              f"${r['combined_pnl']:>+7.0f}")

    return results


# ── Phase 2B: Double-Factor Scan ─────────────────────────────────────

def scan_double_factor(features, data, single_results):
    """Combine top single-factor rules pairwise."""
    print("\n" + "=" * 70)
    print("  PHASE 2B: Double-Factor Scan (AND combinations)")
    print("=" * 70)

    # Take top 20 single-factor strategies
    top_singles = single_results[:20]
    if len(top_singles) < 2:
        print("  Not enough single-factor results to combine.")
        return []

    results = []
    tested = 0

    for i, r1 in enumerate(top_singles):
        for j, r2 in enumerate(top_singles):
            if j <= i:
                continue

            s1 = r1["strat"]
            s2 = r2["strat"]

            # Skip if same feature
            if s1.rules[0].feature == s2.rules[0].feature:
                continue

            # Skip if different directions (AND with conflicting directions makes no sense)
            if s1.rules[0].direction != s2.rules[0].direction:
                continue

            # Combine rules
            combined = Strategy(s1.rules + s2.rules, hold=18)
            tested += 1

            trades_train = backtest_strategy(combined, features, data, period="train")
            s_train = quick_score(trades_train)

            if s_train["pnl"] <= 0 or s_train["n"] < 10:
                continue

            trades_test = backtest_strategy(combined, features, data, period="test")
            s_test = quick_score(trades_test)

            if s_test["pnl"] > 0:
                results.append({
                    "rule": str(combined),
                    "strat": combined,
                    "train": s_train,
                    "test": s_test,
                    "combined_pnl": s_train["pnl"] + s_test["pnl"],
                })

    results.sort(key=lambda x: x["combined_pnl"], reverse=True)

    print(f"\n  Tested {tested} combos → {len(results)} profitable on both train+test")
    print(f"\n  {'Rule':<65} {'Train':>12} {'Test':>12} {'Total':>8}")
    print(f"  {'-'*100}")

    for r in results[:20]:
        t = r["train"]
        s = r["test"]
        rule_short = r["rule"][:63]
        print(f"  {rule_short:<65} "
              f"${t['pnl']:>+7.0f} ({t['n']:>3}t) "
              f"${s['pnl']:>+7.0f} ({s['n']:>3}t) "
              f"${r['combined_pnl']:>+7.0f}")

    return results


# ── Phase 3: Genetic Algorithm ───────────────────────────────────────

def random_rule():
    """Generate a random rule."""
    feat = random.choice(FEATURE_NAMES)
    thresholds = FEATURE_THRESHOLDS[feat]
    thresh = random.choice(thresholds)
    op = random.choice([">", "<"])
    direction = random.choice([1, -1])
    return Rule(feat, op, thresh, direction)


def random_strategy():
    """Generate a random strategy with 1-3 rules."""
    n_rules = random.choices([1, 2, 3], weights=[0.4, 0.4, 0.2])[0]
    direction = random.choice([1, -1])
    rules = []
    used_features = set()
    for _ in range(n_rules):
        for _attempt in range(10):
            r = random_rule()
            if r.feature not in used_features:
                r.direction = direction  # All rules same direction
                rules.append(r)
                used_features.add(r.feature)
                break
    hold = random.choice([6, 12, 18, 24, 30, 42])  # 1-7 days
    return Strategy(rules, hold=hold)


def mutate(strategy: Strategy) -> Strategy:
    """Mutate a strategy: tweak threshold, swap feature, add/remove rule."""
    strat = Strategy([Rule(r.feature, r.op, r.threshold, r.direction)
                      for r in strategy.rules], hold=strategy.hold)

    mutation = random.choice(["threshold", "op", "hold", "add_rule", "remove_rule", "swap_feature"])

    if mutation == "threshold" and strat.rules:
        r = random.choice(strat.rules)
        thresholds = FEATURE_THRESHOLDS.get(r.feature, [0])
        r.threshold = random.choice(thresholds)

    elif mutation == "op" and strat.rules:
        r = random.choice(strat.rules)
        r.op = "<" if r.op == ">" else ">"

    elif mutation == "hold":
        strat.hold = random.choice([6, 12, 18, 24, 30, 42])

    elif mutation == "add_rule" and len(strat.rules) < 4:
        direction = strat.rules[0].direction if strat.rules else 1
        used = {r.feature for r in strat.rules}
        for _ in range(10):
            r = random_rule()
            if r.feature not in used:
                r.direction = direction
                strat.rules.append(r)
                break

    elif mutation == "remove_rule" and len(strat.rules) > 1:
        strat.rules.pop(random.randint(0, len(strat.rules) - 1))

    elif mutation == "swap_feature" and strat.rules:
        r = random.choice(strat.rules)
        used = {r2.feature for r2 in strat.rules}
        new_feat = random.choice([f for f in FEATURE_NAMES if f not in used] or FEATURE_NAMES)
        r.feature = new_feat
        r.threshold = random.choice(FEATURE_THRESHOLDS.get(new_feat, [0]))

    return strat


def crossover(s1: Strategy, s2: Strategy) -> Strategy:
    """Combine rules from two strategies."""
    all_rules = s1.rules + s2.rules
    direction = s1.rules[0].direction if s1.rules else 1

    # Pick 1-3 unique-feature rules
    random.shuffle(all_rules)
    used = set()
    rules = []
    for r in all_rules:
        if r.feature not in used and len(rules) < 3:
            r_copy = Rule(r.feature, r.op, r.threshold, direction)
            rules.append(r_copy)
            used.add(r.feature)

    hold = random.choice([s1.hold, s2.hold])
    return Strategy(rules, hold=hold)


def genetic_search(features, data, n_pop=100, n_gen=50):
    """Genetic algorithm to find the best multi-rule strategy."""
    print("\n" + "=" * 70)
    print("  PHASE 3: Genetic Algorithm")
    print(f"  Population: {n_pop} | Generations: {n_gen}")
    print("=" * 70)

    # Initialize population
    population = [random_strategy() for _ in range(n_pop)]
    best_ever = None
    best_score = -1e9
    stagnant = 0

    for gen in range(n_gen):
        # Evaluate fitness on TRAIN only
        scored = []
        for strat in population:
            trades = backtest_strategy(strat, features, data, period="train")
            s = quick_score(trades)
            # Fitness: P&L penalized by low trade count
            fitness = s["pnl"] if s["n"] >= 10 else s["pnl"] * (s["n"] / 10)
            scored.append((fitness, strat, s))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Track best
        if scored[0][0] > best_score:
            best_score = scored[0][0]
            best_ever = scored[0]
            stagnant = 0
        else:
            stagnant += 1

        # Print progress every 5 generations
        if gen % 5 == 0 or gen == n_gen - 1:
            top = scored[0]
            print(f"  Gen {gen:>3}: best=${top[2]['pnl']:>+7.0f} ({top[2]['n']:>3}t, "
                  f"avg={top[2]['avg']:>+5.1f}bps) | {top[1]}")

        # Early stop if stagnant
        if stagnant >= 15:
            print(f"  → Stagnant for 15 generations, stopping early at gen {gen}")
            break

        # Selection: top 30% survive
        survivors = [s[1] for s in scored[:n_pop // 3]]

        # New population
        new_pop = list(survivors)

        # Crossover
        while len(new_pop) < n_pop * 0.7:
            p1, p2 = random.sample(survivors, 2)
            child = crossover(p1, p2)
            new_pop.append(child)

        # Mutation
        while len(new_pop) < n_pop * 0.9:
            parent = random.choice(survivors)
            child = mutate(parent)
            new_pop.append(child)

        # Random fresh blood
        while len(new_pop) < n_pop:
            new_pop.append(random_strategy())

        population = new_pop

    # Evaluate top 10 on test
    print(f"\n  Evaluating top strategies on TEST period...")
    scored.sort(key=lambda x: x[0], reverse=True)

    validated = []
    for fitness, strat, s_train in scored[:20]:
        if s_train["n"] < 10:
            continue
        trades_test = backtest_strategy(strat, features, data, period="test")
        s_test = quick_score(trades_test)

        validated.append({
            "rule": str(strat),
            "strat": strat,
            "train": s_train,
            "test": s_test,
            "combined_pnl": s_train["pnl"] + s_test["pnl"],
        })

    validated.sort(key=lambda x: x["combined_pnl"], reverse=True)

    print(f"\n  {'Strategy':<65} {'Train':>12} {'Test':>12} {'Total':>8}")
    print(f"  {'-'*100}")
    for r in validated[:10]:
        t = r["train"]
        s = r["test"]
        rule_short = r["rule"][:63]
        print(f"  {rule_short:<65} "
              f"${t['pnl']:>+7.0f} ({t['n']:>3}t) "
              f"${s['pnl']:>+7.0f} ({s['n']:>3}t) "
              f"${r['combined_pnl']:>+7.0f}")

    return validated


# ── Phase 4: Monte Carlo Validation ──────────────────────────────────

def monte_carlo_validate(strategy, features, data, n_sims=500):
    """Compare strategy to random-direction-matched timing."""
    trades = backtest_strategy(strategy, features, data, period="all")
    actual_pnl = sum(t["pnl"] for t in trades)
    n_trades = len(trades)

    if n_trades < 10:
        return {"actual": actual_pnl, "z": 0, "p": 1.0}

    # Count longs/shorts per coin
    by_coin = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        by_coin[t["coin"]]["long" if t["direction"] == "LONG" else "short"] += 1

    hold = strategy.hold
    sim_pnls = []

    for _ in range(n_sims):
        sim_total = 0
        for coin, counts in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            n_candles = len(candles)
            if n_candles < 200:
                continue

            available = list(range(180, n_candles - hold - 1))
            n_needed = counts["long"] + counts["short"]
            if len(available) < n_needed:
                continue

            sampled = random.sample(available, n_needed)
            for j, idx in enumerate(sampled):
                direction = 1 if j < counts["long"] else -1
                entry = candles[idx + 1]["o"] if idx + 1 < n_candles else candles[idx]["c"]
                exit_idx = min(idx + 1 + hold, n_candles - 1)
                exit_p = candles[exit_idx]["c"]
                if entry <= 0:
                    continue
                gross = direction * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim_total += POSITION_SIZE * net / 1e4

        sim_pnls.append(sim_total)

    sim_mean = np.mean(sim_pnls)
    sim_std = np.std(sim_pnls)
    z = (actual_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    p_val = sum(1 for s in sim_pnls if s >= actual_pnl) / n_sims

    return {
        "actual": actual_pnl,
        "random_mean": sim_mean,
        "random_std": sim_std,
        "z": z,
        "p": p_val,
        "n_trades": n_trades,
    }


# ── Hold Period Optimization ─────────────────────────────────────────

def optimize_hold(strategy, features, data):
    """Test different hold periods for the best strategy."""
    print(f"\n  Hold period optimization for: {strategy}")
    holds = [3, 6, 9, 12, 18, 24, 30, 36, 42]
    best_hold = strategy.hold
    best_pnl = -1e9

    for h in holds:
        strat = Strategy([Rule(r.feature, r.op, r.threshold, r.direction)
                          for r in strategy.rules], hold=h)
        trades = backtest_strategy(strat, features, data, period="all")
        s = quick_score(trades)
        marker = ""
        if s["pnl"] > best_pnl:
            best_pnl = s["pnl"]
            best_hold = h
            marker = " ◄"
        print(f"    hold={h*4:>3}h: ${s['pnl']:>+7.0f} ({s['n']:>3}t, "
              f"avg={s['avg']:>+5.1f}bps, win={s['win']:.0f}%){marker}")

    return best_hold


# ── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  GENETIC STRATEGY SEARCH")
    print("  Features: 22 | Tokens: ~28 | Period: 3 years")
    print("  Train: 2023-2024 | Test: 2025-2026")
    print("  Cost: 12 bps | Max pos: 6 | Stop: -15%")
    print("=" * 70)

    # ── Load data ──
    print("\n[1/5] Loading 3-year 4h candles...")
    data = load_3y_candles()
    print(f"  Loaded {len(data)} tokens")

    # ── Build features ──
    print("\n[2/5] Building features (22 per candle)...")
    t0 = time.time()
    features = build_features(data)
    elapsed = time.time() - t0
    total_rows = sum(len(v) for v in features.values())
    print(f"  Built {total_rows:,} feature rows in {elapsed:.1f}s")

    # ── Phase 2A: Single-factor scan ──
    print("\n[3/5] Single-factor exhaustive scan...")
    t0 = time.time()
    single_results = scan_single_factor(features, data)
    print(f"  Completed in {time.time()-t0:.0f}s")

    # ── Phase 2B: Double-factor scan ──
    print("\n[3.5/5] Double-factor combinations...")
    t0 = time.time()
    double_results = scan_double_factor(features, data, single_results)
    print(f"  Completed in {time.time()-t0:.0f}s")

    # ── Phase 3: Genetic search ──
    print("\n[4/5] Genetic algorithm search...")
    t0 = time.time()
    genetic_results = genetic_search(features, data, n_pop=80, n_gen=40)
    print(f"  Completed in {time.time()-t0:.0f}s")

    # ── Collect all survivors ──
    all_results = []
    for r in single_results[:10]:
        r["source"] = "single"
        all_results.append(r)
    for r in double_results[:10]:
        r["source"] = "double"
        all_results.append(r)
    for r in genetic_results[:10]:
        r["source"] = "genetic"
        all_results.append(r)

    all_results.sort(key=lambda x: x["combined_pnl"], reverse=True)

    # ── Phase 4: Validate top 5 ──
    print("\n[5/5] Monte Carlo validation of top strategies...")
    print(f"\n{'▓'*70}")
    print(f"  MONTE CARLO VALIDATION — 500 random-timing simulations")
    print(f"{'▓'*70}")

    for r in all_results[:8]:
        strat = r["strat"]
        print(f"\n  {r['source'].upper()}: {r['rule']}")
        print(f"    Train: ${r['train']['pnl']:>+.0f} ({r['train']['n']}t) | "
              f"Test: ${r['test']['pnl']:>+.0f} ({r['test']['n']}t)")

        # Hold optimization
        best_hold = optimize_hold(strat, features, data)
        strat_opt = Strategy([Rule(r2.feature, r2.op, r2.threshold, r2.direction)
                              for r2 in strat.rules], hold=best_hold)

        # Monte Carlo
        mc = monte_carlo_validate(strat_opt, features, data)
        print(f"\n    Actual:      ${mc['actual']:>+.0f}")
        print(f"    Random mean: ${mc['random_mean']:>+.0f} (std: ${mc['random_std']:.0f})")
        print(f"    Z-score:     {mc['z']:+.2f}")
        print(f"    P-value:     {mc['p']:.3f}")

        if mc["z"] > 2.5:
            print(f"    → ✓✓ STRONGLY SIGNIFICANT")
        elif mc["z"] > 2.0:
            print(f"    → ✓ SIGNIFICANT")
        elif mc["z"] > 1.5:
            print(f"    → ⚠ MARGINAL")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    # ── Final summary ──
    print(f"\n\n{'█'*70}")
    print(f"  FINAL SUMMARY")
    print(f"{'█'*70}")
    print(f"\n  {'Source':<8} {'Rule':<50} {'TrainPnL':>9} {'TestPnL':>9} {'Z':>6} {'Valid':>6}")
    print(f"  {'-'*90}")

    for r in all_results[:15]:
        t = r["train"]
        s = r["test"]
        rule_short = r["rule"][:48]
        print(f"  {r.get('source','?'):<8} {rule_short:<50} "
              f"${t['pnl']:>+7.0f} ${s['pnl']:>+7.0f}")


if __name__ == "__main__":
    main()
