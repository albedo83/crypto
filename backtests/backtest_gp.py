"""Genetic Programming — Evolve mathematical trading formulas.

Instead of testing (feature > threshold → direction), evolve free-form
expression trees like:
    (btc_7d * vol_ratio) / (dispersion + 50) → sign = direction

The GP discovers its own indicators from raw features.

Tree structure:
    - Terminals: features (22) or random constants
    - Operators: +, -, *, /, max, min, abs, neg, sqrt
    - Output: scalar → LONG if > 0, SHORT if < 0, skip if |x| < 0.5

Usage:
    python3 -m analysis.backtest_gp
"""

from __future__ import annotations

import random
import time
import copy
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, MAX_POSITIONS, MAX_SAME_DIR,
    POSITION_SIZE, TRAIN_END, TEST_START,
)

# Features available to the GP
FEATURES = [
    "ret_6h", "ret_42h", "ret_84h", "ret_180h",
    "vol_7d", "vol_30d", "vol_ratio",
    "drawdown", "recovery", "range_pct",
    "consec_up", "consec_dn",
    "btc_7d", "btc_30d", "eth_7d",
    "btc_eth_spread", "alt_vs_btc_7d", "alt_vs_btc_30d",
    "alt_index_7d", "dispersion_7d", "alt_rank_7d",
    "vol_z",
]

# Operators
OPS_BINARY = ["+", "-", "*", "/", "max", "min"]
OPS_UNARY = ["abs", "neg", "sqrt", "inv"]


# ═══════════════════════════════════════════════════════════════════
# Expression Tree
# ═══════════════════════════════════════════════════════════════════

class Node:
    """Expression tree node."""
    __slots__ = ("kind", "value", "left", "right")

    def __init__(self, kind, value=None, left=None, right=None):
        self.kind = kind      # "feature", "const", "binary", "unary"
        self.value = value    # feature name, constant, or operator
        self.left = left
        self.right = right

    def eval(self, f: dict) -> float:
        """Evaluate expression given feature dict."""
        if self.kind == "feature":
            return f.get(self.value, 0.0)
        elif self.kind == "const":
            return self.value
        elif self.kind == "unary":
            x = self.left.eval(f) if self.left else 0.0
            if self.value == "abs":
                return abs(x)
            elif self.value == "neg":
                return -x
            elif self.value == "sqrt":
                return np.sqrt(abs(x))
            elif self.value == "inv":
                return 1.0 / x if abs(x) > 0.001 else 0.0
        elif self.kind == "binary":
            a = self.left.eval(f) if self.left else 0.0
            b = self.right.eval(f) if self.right else 0.0
            if self.value == "+":
                return a + b
            elif self.value == "-":
                return a - b
            elif self.value == "*":
                return a * b / 1000  # scale down to prevent explosion
            elif self.value == "/":
                return a / b if abs(b) > 0.001 else 0.0
            elif self.value == "max":
                return max(a, b)
            elif self.value == "min":
                return min(a, b)
        return 0.0

    def depth(self) -> int:
        d = 1
        if self.left:
            d = max(d, 1 + self.left.depth())
        if self.right:
            d = max(d, 1 + self.right.depth())
        return d

    def size(self) -> int:
        s = 1
        if self.left:
            s += self.left.size()
        if self.right:
            s += self.right.size()
        return s

    def __repr__(self):
        if self.kind == "feature":
            return self.value
        elif self.kind == "const":
            return f"{self.value:.1f}"
        elif self.kind == "unary":
            return f"{self.value}({self.left})"
        elif self.kind == "binary":
            return f"({self.left} {self.value} {self.right})"
        return "?"

    def copy(self):
        return copy.deepcopy(self)


def random_terminal():
    if random.random() < 0.7:
        return Node("feature", random.choice(FEATURES))
    else:
        return Node("const", round(random.gauss(0, 500), 1))


def random_tree(max_depth=4):
    if max_depth <= 1 or random.random() < 0.3:
        return random_terminal()
    if random.random() < 0.2:
        return Node("unary", random.choice(OPS_UNARY), left=random_tree(max_depth - 1))
    return Node("binary", random.choice(OPS_BINARY),
                left=random_tree(max_depth - 1), right=random_tree(max_depth - 1))


def get_all_nodes(tree: Node) -> list:
    """Flat list of all nodes."""
    result = [tree]
    if tree.left:
        result.extend(get_all_nodes(tree.left))
    if tree.right:
        result.extend(get_all_nodes(tree.right))
    return result


def random_subtree(tree: Node) -> Node:
    """Pick a random node from the tree."""
    nodes = get_all_nodes(tree)
    return random.choice(nodes)


def replace_subtree(tree: Node, old: Node, new: Node) -> Node:
    """Replace old node with new in tree (by identity)."""
    if tree is old:
        return new
    result = Node(tree.kind, tree.value)
    if tree.left:
        result.left = replace_subtree(tree.left, old, new)
    if tree.right:
        result.right = replace_subtree(tree.right, old, new)
    return result


# ═══════════════════════════════════════════════════════════════════
# GP Operators
# ═══════════════════════════════════════════════════════════════════

def crossover(p1: Node, p2: Node) -> Node:
    """Swap a random subtree from p2 into p1."""
    child = p1.copy()
    target = random_subtree(child)
    donor = random_subtree(p2.copy())
    return replace_subtree(child, target, donor)


def mutate(tree: Node) -> Node:
    """Mutate a random part of the tree."""
    result = tree.copy()
    mutation = random.choice(["replace_subtree", "change_op", "change_const", "change_feature"])

    if mutation == "replace_subtree":
        target = random_subtree(result)
        new = random_tree(max_depth=3)
        return replace_subtree(result, target, new)

    elif mutation == "change_op":
        nodes = [n for n in get_all_nodes(result) if n.kind in ("binary", "unary")]
        if nodes:
            n = random.choice(nodes)
            if n.kind == "binary":
                n.value = random.choice(OPS_BINARY)
            else:
                n.value = random.choice(OPS_UNARY)

    elif mutation == "change_const":
        nodes = [n for n in get_all_nodes(result) if n.kind == "const"]
        if nodes:
            n = random.choice(nodes)
            n.value = round(n.value + random.gauss(0, 100), 1)

    elif mutation == "change_feature":
        nodes = [n for n in get_all_nodes(result) if n.kind == "feature"]
        if nodes:
            n = random.choice(nodes)
            n.value = random.choice(FEATURES)

    return result


# ═══════════════════════════════════════════════════════════════════
# Backtester
# ═══════════════════════════════════════════════════════════════════

def evaluate_tree(tree: Node, features: dict, data: dict, period="train",
                  hold=18, cost=COST_BPS, threshold=0.5,
                  max_pos=MAX_POSITIONS, max_dir=MAX_SAME_DIR, size=POSITION_SIZE):
    """Evaluate a GP tree as a trading strategy.

    tree output > +threshold → LONG
    tree output < -threshold → SHORT
    """
    coins = [c for c in TOKENS if c in features and c in data]

    # Collect events
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

    by_ts = defaultdict(list)
    for t, coin, f in events:
        by_ts[t].append((coin, f))

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted(by_ts.keys()):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in data:
                continue
            candles = data[coin]
            for ci in range(pos["idx"], min(pos["idx"] + hold + 2, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - pos["idx"]
                    if held >= hold:
                        current = candles[ci]["c"]
                        if current > 0:
                            gross = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                            net = gross - cost
                            trades.append({"pnl": size * net / 1e4, "net": net})
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000
                    break

        # Entries
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)

        candidates = []
        for coin, f in by_ts[ts]:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue

            # Evaluate tree
            try:
                score = tree.eval(f)
            except:
                continue

            if not np.isfinite(score):
                continue

            if abs(score) < threshold:
                continue

            direction = 1 if score > 0 else -1
            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            idx = f["_idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx + 1]["o"]
            if entry <= 0:
                continue

            candidates.append({
                "coin": coin, "dir": direction, "entry": entry,
                "idx": idx + 1, "t": data[coin][idx + 1]["t"],
                "strength": abs(score),
            })

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)
        for c in candidates[:slots]:
            positions[c["coin"]] = {"dir": c["dir"], "entry": c["entry"],
                                     "idx": c["idx"], "t": c["t"]}
            if c["dir"] == 1:
                n_long += 1
            else:
                n_short += 1

    return trades


# ═══════════════════════════════════════════════════════════════════
# GP Search
# ═══════════════════════════════════════════════════════════════════

def gp_search(features, data, n_pop=200, n_gen=60):
    """Genetic Programming search for trading formulas."""
    print("\n" + "=" * 70)
    print(f"  GENETIC PROGRAMMING — Evolving trading formulas")
    print(f"  Population: {n_pop} | Generations: {n_gen}")
    print(f"  Operators: {OPS_BINARY + OPS_UNARY}")
    print(f"  Features: {len(FEATURES)}")
    print("=" * 70)

    # Initialize population
    population = [random_tree(max_depth=5) for _ in range(n_pop)]
    best_ever = None
    best_fitness = -1e9
    stagnant = 0
    generation_log = []

    for gen in range(n_gen):
        # Evaluate on TRAIN
        scored = []
        for tree in population:
            if tree.depth() > 8:
                scored.append((-1e8, tree))
                continue

            trades = evaluate_tree(tree, features, data, period="train")
            n_trades = len(trades)

            if n_trades < 10:
                fitness = -1e6
            else:
                pnl = sum(t["pnl"] for t in trades)
                avg = float(np.mean([t["net"] for t in trades]))
                # Fitness: P&L with parsimony penalty
                depth_penalty = max(0, tree.depth() - 5) * 50
                low_trade_penalty = max(0, 30 - n_trades) * 10
                fitness = pnl - depth_penalty - low_trade_penalty

            scored.append((fitness, tree))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Track best
        if scored[0][0] > best_fitness:
            best_fitness = scored[0][0]
            best_ever = (scored[0][0], scored[0][1].copy())
            stagnant = 0
        else:
            stagnant += 1

        # Progress
        top = scored[0]
        top_trades = evaluate_tree(top[1], features, data, period="train")
        top_n = len(top_trades)
        top_pnl = sum(t["pnl"] for t in top_trades)
        top_depth = top[1].depth()

        if gen % 5 == 0 or gen == n_gen - 1:
            formula = str(top[1])[:80]
            print(f"  Gen {gen:>3}: fit={top[0]:>+8.0f} pnl=${top_pnl:>+7.0f} "
                  f"({top_n:>3}t) d={top_depth} | {formula}")

        generation_log.append({
            "gen": gen, "fitness": top[0], "pnl": top_pnl,
            "n": top_n, "depth": top_depth, "formula": str(top[1])[:100],
        })

        if stagnant >= 20:
            print(f"  → Stagnant for 20 gen, stopping at gen {gen}")
            break

        # Selection: tournament
        def tournament(k=5):
            contestants = random.sample(scored, k)
            return max(contestants, key=lambda x: x[0])[1]

        # Next generation
        elite_n = max(5, n_pop // 10)
        new_pop = [s[1].copy() for s in scored[:elite_n]]

        while len(new_pop) < n_pop * 0.5:
            p1 = tournament()
            p2 = tournament()
            child = crossover(p1, p2)
            if child.depth() <= 8:
                new_pop.append(child)

        while len(new_pop) < n_pop * 0.8:
            parent = tournament()
            child = mutate(parent)
            if child.depth() <= 8:
                new_pop.append(child)

        while len(new_pop) < n_pop:
            new_pop.append(random_tree(max_depth=5))

        population = new_pop

    # ═══════════════════════════════════════════════════════════
    # Validate top formulas on TEST
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'▓'*70}")
    print(f"  VALIDATION — Top formulas on TEST data")
    print(f"{'▓'*70}")

    # Deduplicate top formulas by string representation
    seen = set()
    top_formulas = []
    for fitness, tree in scored[:50]:
        s = str(tree)
        if s in seen:
            continue
        seen.add(s)
        top_formulas.append((fitness, tree))
        if len(top_formulas) >= 20:
            break

    validated = []
    for fitness, tree in top_formulas:
        trades_train = evaluate_tree(tree, features, data, period="train")
        trades_test = evaluate_tree(tree, features, data, period="test")

        train_pnl = sum(t["pnl"] for t in trades_train)
        test_pnl = sum(t["pnl"] for t in trades_test)
        train_n = len(trades_train)
        test_n = len(trades_test)

        if train_pnl <= 0 or train_n < 10:
            continue

        formula = str(tree)[:70]
        valid = "✓" if test_pnl > 0 else "✗"
        print(f"  d={tree.depth()} {formula:<72}")
        print(f"       Train: ${train_pnl:>+7.0f} ({train_n:>3}t) | "
              f"Test: ${test_pnl:>+7.0f} ({test_n:>3}t) {valid}")

        if test_pnl > 0:
            validated.append({
                "formula": str(tree),
                "tree": tree,
                "train_pnl": train_pnl, "test_pnl": test_pnl,
                "train_n": train_n, "test_n": test_n,
                "depth": tree.depth(),
                "total": train_pnl + test_pnl,
            })

    validated.sort(key=lambda x: x["total"], reverse=True)

    # Monte Carlo on top validated
    if validated:
        print(f"\n{'▓'*70}")
        print(f"  MONTE CARLO — Top validated formulas")
        print(f"{'▓'*70}")

        for r in validated[:5]:
            tree = r["tree"]
            trades_all = evaluate_tree(tree, features, data, period="all")
            actual_pnl = sum(t["pnl"] for t in trades_all)
            n_trades = len(trades_all)

            if n_trades < 10:
                continue

            # Count directions per coin
            # (we need to re-run with coin tracking for MC)
            # Simplified: use overall long/short ratio
            n_long = sum(1 for t in trades_all if t.get("net", 0) > 0)  # approximation
            n_total = len(trades_all)

            sim_pnls = []
            for _ in range(500):
                sim_total = 0
                for coin in TOKENS:
                    if coin not in data:
                        continue
                    candles = data[coin]
                    nc = len(candles)
                    if nc < 200:
                        continue
                    available = list(range(180, nc - 19))
                    n_per_coin = n_total // len(TOKENS)
                    if n_per_coin < 1 or len(available) < n_per_coin:
                        continue
                    sampled = random.sample(available, n_per_coin)
                    for idx in sampled:
                        direction = 1 if random.random() < n_long / n_total else -1
                        entry = candles[min(idx+1, nc-1)]["o"]
                        exit_p = candles[min(idx+19, nc-1)]["c"]
                        if entry <= 0:
                            continue
                        gross = direction * (exit_p / entry - 1) * 1e4
                        net = gross - COST_BPS
                        sim_total += POSITION_SIZE * net / 1e4
                sim_pnls.append(sim_total)

            sim_mean = float(np.mean(sim_pnls))
            sim_std = float(np.std(sim_pnls))
            z = (actual_pnl - sim_mean) / sim_std if sim_std > 0 else 0

            formula = r["formula"][:65]
            print(f"\n  {formula}")
            print(f"    All: ${actual_pnl:>+.0f} ({n_trades}t) | "
                  f"Random: ${sim_mean:>+.0f} (σ=${sim_std:.0f}) | Z: {z:+.2f}")
            if z > 2.5:
                print(f"    → ✓✓ STRONGLY SIGNIFICANT")
            elif z > 2.0:
                print(f"    → ✓ SIGNIFICANT")
            elif z > 1.5:
                print(f"    → ⚠ MARGINAL")
            else:
                print(f"    → ✗ NOT SIGNIFICANT")

    # Summary
    print(f"\n\n{'█'*70}")
    print(f"  GP SUMMARY")
    print(f"{'█'*70}")

    if validated:
        print(f"\n  {len(validated)} formulas profitable on train+test:")
        for r in validated[:10]:
            f = r["formula"][:60]
            print(f"    d={r['depth']} ${r['total']:>+7.0f} "
                  f"(trn=${r['train_pnl']:>+.0f} tst=${r['test_pnl']:>+.0f}) | {f}")
    else:
        print(f"\n  No formulas survived train+test validation.")
        print(f"  GP did not find anything better than our existing rules.")

    return validated


def main():
    print("=" * 70)
    print("  GENETIC PROGRAMMING — Formula Evolution")
    print("  Evolving mathematical expressions for trading")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    t0 = time.time()
    features = build_features(data)
    print(f"Built features in {time.time()-t0:.1f}s")

    # Run GP
    results = gp_search(features, data, n_pop=200, n_gen=60)

    # Compare with our existing strategies
    print(f"\n{'='*70}")
    print(f"  COMPARISON — GP vs existing S1+S2+S4")
    print(f"{'='*70}")

    from backtests.backtest_genetic import Rule, Strategy, backtest_strategy, quick_score

    existing = {
        "S1 btc_rip": Strategy([Rule("btc_30d", ">", 2000, 1)], hold=18),
        "S2 alt_crash": Strategy([Rule("alt_index_7d", "<", -1000, 1)], hold=18),
        "S4 vol_short": Strategy([Rule("vol_ratio", "<", 1.0, -1),
                                   Rule("range_pct", "<", 200, -1)], hold=18),
    }

    print(f"\n  Existing strategies:")
    for name, strat in existing.items():
        t_all = backtest_strategy(strat, features, data, period="all")
        s = quick_score(t_all)
        print(f"    {name:<20} ${s['pnl']:>+7.0f} ({s['n']:>3}t, avg={s['avg']:>+5.1f}bp)")

    if results:
        print(f"\n  Best GP formula:")
        best = results[0]
        print(f"    {best['formula'][:60]}")
        print(f"    ${best['total']:>+7.0f} (trn=${best['train_pnl']:>+.0f} tst=${best['test_pnl']:>+.0f})")


if __name__ == "__main__":
    main()
