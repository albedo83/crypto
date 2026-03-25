"""New Multi-Condition Signal Search — test S7, S8, S9, S10 + variants.

S7: BTC-Alt recouple (alt_vs_btc_7d < -1500 AND btc_7d > 300 → LONG)
S8: Capitulation flush (drawdown < -4000 AND vol_z > 1.5 AND ret_6h < -100 → LONG)
S9: Exhaustion reversal (consec_dn >= 5 AND drawdown > -2000 AND range_pct > 150 → LONG)
S10: Vol compression + recovery (vol_ratio < 0.7 AND recovery > 1500 → LONG)

Plus: sweep thresholds, combine with existing S1-S5, Monte Carlo validation.

Usage:
    python3 -m analysis.backtest_newcombos
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

# Reuse infrastructure from genetic backtest
from analysis.backtest_genetic import (
    load_3y_candles, build_features, backtest_strategy,
    quick_score, monte_carlo_validate,
    Strategy, Rule, COST_BPS, POSITION_SIZE,
    MAX_POSITIONS, MAX_SAME_DIR, STOP_LOSS_BPS,
    TRAIN_END, TEST_START, TOKENS,
)

# ── Multi-Condition Signal Definitions ──────────────────────────────

class MultiCondSignal:
    """Signal with multiple AND conditions on features."""
    def __init__(self, name: str, conditions: list[tuple], direction: int, hold: int = 18):
        """
        conditions: [(feature, op, threshold), ...]
        direction: 1=LONG, -1=SHORT
        hold: in 4h candles (18 = 72h)
        """
        self.name = name
        self.conditions = conditions
        self.direction = direction
        self.hold = hold

    def matches(self, f: dict) -> bool:
        for feat, op, thresh in self.conditions:
            val = f.get(feat, 0.0)
            if op == ">" and not (val > thresh):
                return False
            if op == "<" and not (val < thresh):
                return False
            if op == ">=" and not (val >= thresh):
                return False
        return True

    def to_strategy(self) -> Strategy:
        rules = [Rule(feat, op, thresh, self.direction) for feat, op, thresh in self.conditions]
        return Strategy(rules, hold=self.hold)

    def __repr__(self):
        conds = " AND ".join(f"{f}{o}{t}" for f, o, t in self.conditions)
        d = "LONG" if self.direction == 1 else "SHORT"
        return f"{self.name}: {conds} → {d} hold={self.hold*4}h"


# ── Signal Definitions with Threshold Variants ──────────────────────

def generate_signals():
    """Generate all signal variants to test."""
    signals = []

    # S7: BTC-Alt recouple
    for alt_thresh in [-2000, -1500, -1000]:
        for btc_thresh in [200, 300, 500]:
            for hold in [9, 12, 18]:  # 36h, 48h, 72h
                signals.append(MultiCondSignal(
                    f"S7_alt{alt_thresh}_btc{btc_thresh}_h{hold*4}",
                    [("alt_vs_btc_7d", "<", alt_thresh), ("btc_7d", ">", btc_thresh)],
                    direction=1, hold=hold,
                ))

    # S8: Capitulation flush
    for dd_thresh in [-5000, -4000, -3000]:
        for vz_thresh in [1.0, 1.5, 2.0]:
            for ret_thresh in [-200, -100, -50]:
                for hold in [6, 9, 12]:  # 24h, 36h, 48h
                    signals.append(MultiCondSignal(
                        f"S8_dd{dd_thresh}_vz{vz_thresh}_r{ret_thresh}_h{hold*4}",
                        [("drawdown", "<", dd_thresh), ("vol_z", ">", vz_thresh), ("ret_6h", "<", ret_thresh)],
                        direction=1, hold=hold,
                    ))

    # S9: Exhaustion reversal
    for consec in [4, 5, 6]:
        for dd_floor in [-3000, -2000, -1500]:
            for rng in [100, 150, 200]:
                for hold in [9, 12, 18]:
                    signals.append(MultiCondSignal(
                        f"S9_cd{consec}_dd{dd_floor}_rng{rng}_h{hold*4}",
                        [("consec_dn", ">=", consec), ("drawdown", ">", dd_floor), ("range_pct", ">", rng)],
                        direction=1, hold=hold,
                    ))

    # S10: Vol compression + recovery
    for vr_thresh in [0.5, 0.6, 0.7, 0.8]:
        for rec_thresh in [1000, 1500, 2000, 3000]:
            for hold in [9, 12, 18]:
                signals.append(MultiCondSignal(
                    f"S10_vr{vr_thresh}_rec{rec_thresh}_h{hold*4}",
                    [("vol_ratio", "<", vr_thresh), ("recovery", ">", rec_thresh)],
                    direction=1, hold=hold,
                ))

    # Bonus: BTC-ETH spread divergence (SHORT overheated alt)
    for spread in [500, 800, 1000]:
        for rank in [70, 80, 90]:
            for hold in [9, 12, 18]:
                signals.append(MultiCondSignal(
                    f"SX_spread{spread}_rank{rank}_h{hold*4}",
                    [("btc_eth_spread", ">", spread), ("alt_rank_7d", ">", rank)],
                    direction=-1, hold=hold,
                ))

    # Bonus: Dispersion warning SHORT
    for disp in [1000, 1500, 2000]:
        for aidx in [300, 500, 800]:
            for vr in [1.2, 1.5]:
                for hold in [12, 18]:
                    signals.append(MultiCondSignal(
                        f"SY_disp{disp}_idx{aidx}_vr{vr}_h{hold*4}",
                        [("dispersion_7d", ">", disp), ("alt_index_7d", ">", aidx), ("vol_ratio", ">", vr)],
                        direction=-1, hold=hold,
                    ))

    return signals


# ── Existing S1-S5 for combined portfolio test ──────────────────────

EXISTING_SIGNALS = [
    MultiCondSignal("S1", [("btc_30d", ">", 2000)], direction=1, hold=18),
    MultiCondSignal("S2", [("alt_index_7d", "<", -1000)], direction=1, hold=18),
    MultiCondSignal("S4", [("vol_ratio", "<", 1.0), ("range_pct", "<", 200)], direction=-1, hold=18),
    # S5 is sector-based, approximate with dispersion + vol
]


# ── Combined Multi-Signal Backtester ────────────────────────────────

def backtest_multi_signal(signal_list: list[MultiCondSignal], features: dict, data: dict,
                          period="all", cost=COST_BPS, max_pos=MAX_POSITIONS,
                          max_dir=MAX_SAME_DIR, stop_loss=STOP_LOSS_BPS,
                          size=POSITION_SIZE):
    """Backtest multiple signals simultaneously with shared position slots."""
    coins = [c for c in TOKENS if c in features and c in data]

    # Collect all timesteps
    all_ts = set()
    for coin in coins:
        for f in features[coin]:
            t = f["t"]
            if period == "train" and t >= TRAIN_END:
                continue
            if period == "test" and t < TEST_START:
                continue
            all_ts.add(t)

    # Index features by (coin, ts)
    feat_idx = {}
    for coin in coins:
        for f in features[coin]:
            feat_idx[(coin, f["t"])] = f

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted(all_ts):
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in data:
                continue
            candles = data[coin]
            idx = pos["entry_idx"]
            hold = pos["hold"]

            for ci in range(idx, min(idx + hold + 5, len(candles))):
                if candles[ci]["t"] == ts:
                    held = ci - idx
                    current = candles[ci]["c"]
                    if current == 0:
                        break

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

                    if held >= hold:
                        exit_reason = "timeout"

                    if exit_reason:
                        gross = pos["direction"] * (exit_price / pos["entry_price"] - 1) * 1e4
                        net = gross - cost
                        pnl = size * net / 1e4
                        trades.append({
                            "coin": coin,
                            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                            "strategy": pos["strategy"],
                            "entry_t": pos["entry_t"],
                            "exit_t": ts,
                            "hold": held,
                            "gross": round(gross, 1),
                            "net": round(net, 1),
                            "pnl": round(pnl, 2),
                            "reason": exit_reason,
                        })
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000
                    break

        # Entries (all signals compete for slots)
        n_long = sum(1 for p in positions.values() if p["direction"] == 1)
        n_short = sum(1 for p in positions.values() if p["direction"] == -1)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_idx.get((coin, ts))
            if not f:
                continue

            for sig in signal_list:
                if sig.matches(f):
                    d = sig.direction
                    if d == 1 and n_long >= max_dir:
                        continue
                    if d == -1 and n_short >= max_dir:
                        continue

                    idx = f["_idx"]
                    if idx + 1 >= len(data[coin]):
                        continue
                    entry_price = data[coin][idx + 1]["o"]
                    if entry_price <= 0:
                        continue

                    candidates.append({
                        "coin": coin,
                        "direction": d,
                        "strategy": sig.name,
                        "entry_price": entry_price,
                        "entry_idx": idx + 1,
                        "entry_t": data[coin][idx + 1]["t"],
                        "hold": sig.hold,
                        "strength": abs(f.get("ret_42h", 0)),
                    })
                    break  # one signal per coin per timestamp

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)
        for cand in candidates[:slots]:
            positions[cand["coin"]] = {
                "direction": cand["direction"],
                "strategy": cand["strategy"],
                "entry_price": cand["entry_price"],
                "entry_idx": cand["entry_idx"],
                "entry_t": cand["entry_t"],
                "hold": cand["hold"],
            }

    return trades


# ── Monte Carlo for multi-signal ────────────────────────────────────

def mc_multi(trades, data, n_sims=500):
    """Monte Carlo: shuffle timing per coin, keep direction counts."""
    actual_pnl = sum(t["pnl"] for t in trades)
    if len(trades) < 10:
        return {"actual": actual_pnl, "z": 0, "n": len(trades)}

    by_coin = defaultdict(lambda: {"long": 0, "short": 0, "holds": []})
    for t in trades:
        k = "long" if t["direction"] == "LONG" else "short"
        by_coin[t["coin"]][k] += 1
        by_coin[t["coin"]]["holds"].append(t["hold"])

    sim_pnls = []
    for _ in range(n_sims):
        sim_total = 0
        for coin, info in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            n_candles = len(candles)
            avg_hold = int(np.mean(info["holds"])) if info["holds"] else 18
            available = list(range(180, n_candles - avg_hold - 1))
            n_needed = info["long"] + info["short"]
            if len(available) < n_needed:
                continue

            sampled = random.sample(available, n_needed)
            for j, idx in enumerate(sampled):
                direction = 1 if j < info["long"] else -1
                entry = candles[idx + 1]["o"] if idx + 1 < n_candles else candles[idx]["c"]
                exit_idx = min(idx + 1 + avg_hold, n_candles - 1)
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

    return {
        "actual": round(actual_pnl, 2),
        "random_mean": round(sim_mean, 2),
        "z": round(z, 2),
        "n": len(trades),
    }


# ── Main ────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  NEW MULTI-CONDITION SIGNAL SEARCH")
    print("=" * 70)

    print("\n  Loading data...")
    data = load_3y_candles()
    print(f"  Loaded {len(data)} tokens")

    print("  Computing features...")
    features = build_features(data)
    print(f"  Features for {len(features)} tokens")

    # ── Phase 1: Scan all variants ──────────────────────────────
    signals = generate_signals()
    print(f"\n  Testing {len(signals)} signal variants...")

    results = []
    for i, sig in enumerate(signals):
        if i % 100 == 0:
            print(f"    {i}/{len(signals)}...")

        strat = sig.to_strategy()

        trades_train = backtest_strategy(strat, features, data, period="train")
        s_train = quick_score(trades_train)

        if s_train["pnl"] <= 0 or s_train["n"] < 8:
            continue

        trades_test = backtest_strategy(strat, features, data, period="test")
        s_test = quick_score(trades_test)

        if s_test["pnl"] <= 0 or s_test["n"] < 3:
            continue

        results.append({
            "signal": sig,
            "train": s_train,
            "test": s_test,
            "total_pnl": s_train["pnl"] + s_test["pnl"],
            "total_n": s_train["n"] + s_test["n"],
        })

    results.sort(key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n  ═══════════════════════════════════════════════════════")
    print(f"  {len(results)} signals profitable on BOTH train+test")
    print(f"  ═══════════════════════════════════════════════════════\n")

    print(f"  {'Signal':<55} {'Train':>15} {'Test':>15} {'Total':>8}")
    print(f"  {'-'*95}")

    for r in results[:30]:
        t, s = r["train"], r["test"]
        name = str(r["signal"])[:53]
        print(f"  {name:<55} "
              f"${t['pnl']:>+7.0f} ({t['n']:>3}t) "
              f"${s['pnl']:>+7.0f} ({s['n']:>3}t) "
              f"${r['total_pnl']:>+7.0f}")

    # ── Phase 2: Monte Carlo on top 10 ─────────────────────────
    if not results:
        print("\n  No signals found. All tested combinations fail train/test.")
        return

    print(f"\n\n  {'='*70}")
    print(f"  MONTE CARLO VALIDATION (top 10)")
    print(f"  {'='*70}\n")

    validated = []
    for r in results[:10]:
        sig = r["signal"]
        strat = sig.to_strategy()
        mc = monte_carlo_validate(strat, features, data)
        status = "PASS" if mc["z"] >= 2.0 else "FAIL"
        print(f"  {str(sig)[:60]}")
        print(f"    P&L: ${mc['actual']:+.0f} | Random: ${mc['random_mean']:+.0f} | z={mc['z']:.2f} | {status}")
        print()

        if mc["z"] >= 2.0:
            validated.append({"signal": sig, "mc": mc, "result": r})

    # ── Phase 3: Portfolio test with existing S1-S5 ────────────
    if validated:
        print(f"\n  {'='*70}")
        print(f"  PORTFOLIO TEST: New signals + existing S1/S2/S4")
        print(f"  {'='*70}\n")

        # Baseline: S1+S2+S4 only
        baseline_signals = EXISTING_SIGNALS[:3]  # S1, S2, S4
        baseline_trades = backtest_multi_signal(baseline_signals, features, data)
        bl_score = quick_score(baseline_trades)
        bl_mc = mc_multi(baseline_trades, data)
        print(f"  BASELINE (S1+S2+S4): ${bl_score['pnl']:+.0f} | {bl_score['n']} trades | z={bl_mc['z']:.2f}")

        # Add each validated signal one by one
        for v in validated:
            test_signals = baseline_signals + [v["signal"]]
            test_trades = backtest_multi_signal(test_signals, features, data)
            t_score = quick_score(test_trades)
            t_mc = mc_multi(test_trades, data)

            delta = t_score["pnl"] - bl_score["pnl"]
            arrow = "+" if delta > 0 else ""
            by_strat = defaultdict(int)
            by_strat_pnl = defaultdict(float)
            for t in test_trades:
                by_strat[t["strategy"]] += 1
                by_strat_pnl[t["strategy"]] += t["pnl"]

            print(f"\n  + {v['signal'].name}: ${t_score['pnl']:+.0f} ({arrow}{delta:.0f}) | "
                  f"{t_score['n']} trades | z={t_mc['z']:.2f}")
            for s_name in sorted(by_strat.keys()):
                print(f"      {s_name}: {by_strat[s_name]} trades, ${by_strat_pnl[s_name]:+.0f}")

        # All validated combined
        if len(validated) > 1:
            all_signals = baseline_signals + [v["signal"] for v in validated]
            all_trades = backtest_multi_signal(all_signals, features, data)
            a_score = quick_score(all_trades)
            a_mc = mc_multi(all_trades, data)

            delta = a_score["pnl"] - bl_score["pnl"]
            print(f"\n  ALL NEW: ${a_score['pnl']:+.0f} ({'+' if delta > 0 else ''}{delta:.0f}) | "
                  f"{a_score['n']} trades | z={a_mc['z']:.2f}")

    else:
        print("\n  No signals passed Monte Carlo validation (z >= 2.0).")

    print(f"\n  {'='*70}")
    print(f"  DONE")
    print(f"  {'='*70}")


if __name__ == "__main__":
    main()
