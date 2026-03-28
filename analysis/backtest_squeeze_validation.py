"""Squeeze S10 — 5 validation checks before integration.

1. Distribution par token (concentration risk)
2. Stabilité temporelle (par trimestre, rolling)
3. Stress-test coûts (taker/maker/slippage)
4. Sensibilité paramètres (crête robuste ou fragile?)
5. Corrélation avec signaux existants (S1-S9)

Usage:
    python3 -m analysis.backtest_squeeze_validation
"""

from __future__ import annotations

import json, os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE,
    TRAIN_END, TEST_START,
)
from analysis.backtest_squeeze import detect_squeeze_signals, backtest_squeeze, score


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)

    # Best config: Mode B, sw=12h(3), vm=0.9, bo=0.5, rc=2, hold=24h(6)
    BEST = {"squeeze_window": 3, "vol_ratio_max": 0.9, "breakout_pct": 0.5, "reintegration_candles": 2}
    BEST_BT = {"mode": "B", "hold": 6, "period": "all"}

    signals = detect_squeeze_signals(data, features, BEST)
    all_trades = backtest_squeeze(signals, data, BEST_BT)
    print(f"Best config: {len(all_trades)} trades total\n")

    # ═══════════════════════════════════════════════════════
    # CHECK 1: Distribution par token
    # ═══════════════════════════════════════════════════════
    print("=" * 60)
    print("CHECK 1: DISTRIBUTION PAR TOKEN")
    print("=" * 60)

    by_coin = defaultdict(list)
    for t in all_trades:
        by_coin[t["coin"]].append(t)

    coin_stats = []
    for coin in sorted(by_coin.keys()):
        trades = by_coin[coin]
        s = score(trades)
        coin_stats.append({"coin": coin, **s})

    coin_stats.sort(key=lambda x: x["pnl"], reverse=True)
    total_pnl = sum(c["pnl"] for c in coin_stats)

    print(f"\n  {'Token':>6} {'N':>4} {'P&L':>8} {'%Total':>7} {'Avg':>7} {'Win%':>5}")
    cum_pct = 0
    for c in coin_stats:
        pct = c["pnl"] / total_pnl * 100 if total_pnl != 0 else 0
        cum_pct += pct
        marker = " ←" if cum_pct <= 70 and pct > 0 else ""
        print(f"  {c['coin']:>6} {c['n']:>4} ${c['pnl']:>7.0f} {pct:>+6.1f}% {c['avg']:>+7.1f} {c['win']:>4.0f}%{marker}")

    # Concentration stats
    top5_pnl = sum(c["pnl"] for c in coin_stats[:5])
    top5_pct = top5_pnl / total_pnl * 100 if total_pnl != 0 else 0
    n_positive = sum(1 for c in coin_stats if c["pnl"] > 0)
    n_negative = sum(1 for c in coin_stats if c["pnl"] <= 0)

    print(f"\n  Top 5 = ${top5_pnl:.0f} ({top5_pct:.0f}% du total)")
    print(f"  Positifs: {n_positive} | Négatifs: {n_negative}")

    # Without top 3
    without_top3 = [c for c in coin_stats[3:]]
    rest_pnl = sum(c["pnl"] for c in without_top3)
    rest_avg = float(np.mean([c["avg"] for c in without_top3])) if without_top3 else 0
    print(f"  Sans top 3: ${rest_pnl:.0f} (avg {rest_avg:+.1f} bps) — {'rentable' if rest_pnl > 0 else 'PERTE'}")

    # ═══════════════════════════════════════════════════════
    # CHECK 2: Stabilité temporelle
    # ═══════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    print("CHECK 2: STABILITÉ TEMPORELLE")
    print("=" * 60)

    # Par trimestre
    by_quarter = defaultdict(list)
    for t in all_trades:
        dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
        q = f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        by_quarter[q].append(t)

    print(f"\n  {'Quarter':>8} {'N':>5} {'P&L':>8} {'Avg':>7} {'Win%':>5}")
    for q in sorted(by_quarter.keys()):
        s = score(by_quarter[q])
        flag = "✓" if s["avg"] > 0 else "✗"
        print(f"  {q:>8} {s['n']:>5} ${s['pnl']:>7.0f} {s['avg']:>+7.1f} {s['win']:>4.0f}% {flag}")

    # Count positive/negative quarters
    q_scores = [score(by_quarter[q]) for q in sorted(by_quarter.keys())]
    q_pos = sum(1 for s in q_scores if s["avg"] > 0)
    q_neg = sum(1 for s in q_scores if s["avg"] <= 0)
    print(f"\n  Trimestres positifs: {q_pos}/{q_pos + q_neg}")

    # Rolling 6-month window
    print(f"\n  Rolling 6-month windows:")
    sorted_trades = sorted(all_trades, key=lambda t: t["entry_t"])
    if sorted_trades:
        t_min = sorted_trades[0]["entry_t"]
        t_max = sorted_trades[-1]["entry_t"]
        window_ms = 6 * 30.44 * 86400 * 1000
        step_ms = 3 * 30.44 * 86400 * 1000  # 3-month step

        cursor = t_min
        while cursor + window_ms <= t_max:
            window_trades = [t for t in sorted_trades
                            if cursor <= t["entry_t"] < cursor + window_ms]
            if len(window_trades) >= 10:
                s = score(window_trades)
                dt_start = datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).strftime("%Y-%m")
                dt_end = datetime.fromtimestamp((cursor + window_ms) / 1000, tz=timezone.utc).strftime("%Y-%m")
                flag = "✓" if s["avg"] > 0 else "✗"
                print(f"    {dt_start}→{dt_end}: n={s['n']:>4} avg={s['avg']:>+6.1f} win={s['win']:.0f}% ${s['pnl']:>6.0f} {flag}")
            cursor += step_ms

    # ═══════════════════════════════════════════════════════
    # CHECK 3: Stress-test coûts
    # ═══════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    print("CHECK 3: STRESS-TEST COÛTS")
    print("=" * 60)

    cost_scenarios = [
        ("Baseline (12 bps)", 12),
        ("Taker only (14 bps = 7×2)", 14),
        ("Taker + slippage (20 bps)", 20),
        ("Pessimiste (25 bps)", 25),
        ("Extreme (30 bps)", 30),
    ]

    # Recompute with different costs
    gross_bps_list = []
    for t in all_trades:
        gross = t["net"] + COST_BPS  # recover gross from net
        gross_bps_list.append(gross)

    avg_gross = float(np.mean(gross_bps_list))
    n = len(gross_bps_list)

    print(f"\n  Avg gross (before costs): {avg_gross:+.1f} bps")
    print(f"\n  {'Scenario':>25} {'Cost':>5} {'Net bps':>8} {'P&L':>8} {'Still works?':>12}")
    for label, cost in cost_scenarios:
        net = avg_gross - cost
        pnl = n * POSITION_SIZE * net / 1e4
        flag = "✓" if net > 0 else "✗ DEAD"
        print(f"  {label:>25} {cost:>5} {net:>+8.1f} ${pnl:>7.0f} {flag}")

    # ═══════════════════════════════════════════════════════
    # CHECK 4: Sensibilité paramètres
    # ═══════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    print("CHECK 4: SENSIBILITÉ PARAMÈTRES (crête robuste?)")
    print("=" * 60)

    # Sweep around best config: sw=3(12h), vm=0.9, bo=0.5, rc=2, hold=6(24h)
    param_results = []

    for sw in [2, 3, 4, 6]:
        for vm in [0.7, 0.8, 0.9, 1.0]:
            for bo in [0.3, 0.4, 0.5, 0.6, 0.7]:
                for rc in [1, 2, 3]:
                    for hold in [3, 6, 9, 12]:
                        sigs = detect_squeeze_signals(data, features, {
                            "squeeze_window": sw, "vol_ratio_max": vm,
                            "breakout_pct": bo, "reintegration_candles": rc,
                        })
                        if len(sigs) < 30:
                            continue
                        trades = backtest_squeeze(sigs, data, {"mode": "B", "hold": hold, "period": "all"})
                        s = score(trades)
                        if s["n"] >= 20:
                            param_results.append({
                                "sw": sw * 4, "vm": vm, "bo": bo, "rc": rc,
                                "hold": hold * 4, **s,
                            })

    total_configs = len(param_results)
    positive = sum(1 for r in param_results if r["avg"] > 0)
    strong = sum(1 for r in param_results if r["avg"] > 20)

    print(f"\n  Total configs tested: {total_configs}")
    print(f"  Positive (avg > 0): {positive} ({positive/total_configs*100:.0f}%)")
    print(f"  Strong (avg > 20): {strong} ({strong/total_configs*100:.0f}%)")

    # Heatmap: hold × squeeze_window
    print(f"\n  Avg bps by hold × squeeze_window (vm=0.9, bo=0.5, rc=2):")
    print(f"  {'':>6}", end="")
    for hold in [12, 24, 36, 48]:
        print(f"  {hold}h", end="")
    print()
    for sw in [8, 12, 16, 24]:
        print(f"  sw={sw:>2}h", end="")
        for hold in [12, 24, 36, 48]:
            matches = [r for r in param_results if r["sw"] == sw and r["hold"] == hold
                       and r["vm"] == 0.9 and r["bo"] == 0.5 and r["rc"] == 2]
            if matches:
                avg = matches[0]["avg"]
                marker = "+" if avg > 0 else "-"
                print(f"  {avg:>+5.0f}", end="")
            else:
                print(f"  {'n/a':>5}", end="")
        print()

    # Heatmap: vol_ratio_max × breakout_pct
    print(f"\n  Avg bps by vm × bo (sw=12h, rc=2, hold=24h):")
    print(f"  {'':>6}", end="")
    for bo in [0.3, 0.4, 0.5, 0.6, 0.7]:
        print(f"  bo={bo}", end="")
    print()
    for vm in [0.7, 0.8, 0.9, 1.0]:
        print(f"  vm={vm}", end="")
        for bo in [0.3, 0.4, 0.5, 0.6, 0.7]:
            matches = [r for r in param_results if r["sw"] == 12 and r["hold"] == 24
                       and r["vm"] == vm and r["bo"] == bo and r["rc"] == 2]
            if matches:
                print(f"  {matches[0]['avg']:>+5.0f}", end="")
            else:
                print(f"  {'n/a':>5}", end="")
        print()

    # ═══════════════════════════════════════════════════════
    # CHECK 5: Corrélation avec signaux existants
    # ═══════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    print("CHECK 5: CORRÉLATION AVEC S1-S9")
    print("=" * 60)

    # Detect when existing signals would fire
    from analysis.backtest_genetic import build_features as bf
    from analysis.backtest_sector import compute_sector_features, SECTORS, TOKEN_SECTOR

    # For each squeeze trade, check if any existing signal was also active
    # We need features at the entry timestamp
    feat_by_ts_coin = {}
    for coin in TOKENS:
        if coin not in features:
            continue
        for f in features[coin]:
            feat_by_ts_coin[(f["t"], coin)] = f

    # BTC features for S1/S8
    btc_feats = {}
    if "BTC" in features:
        for f in features["BTC"]:
            btc_feats[f["t"]] = f

    overlap_counts = defaultdict(int)
    total_sq = len(all_trades)
    trades_with_overlap = 0

    for t in all_trades:
        coin = t["coin"]
        # Find closest feature timestamp
        entry_t = t["entry_t"]
        f = feat_by_ts_coin.get((entry_t, coin))
        if not f:
            # Find nearest
            for dt in range(-14400000, 14400001, 14400000):
                f = feat_by_ts_coin.get((entry_t + dt, coin))
                if f:
                    break
        if not f:
            continue

        btc_f = btc_feats.get(entry_t) or btc_feats.get(entry_t - 14400000) or {}

        has_overlap = False

        # S1: btc_30d > 2000
        if btc_f.get("ret_180h", 0) > 2000:
            overlap_counts["S1"] += 1
            has_overlap = True

        # S2: alt_index < -1000 (approximate)
        all_rets = [feat_by_ts_coin.get((entry_t, c), {}).get("ret_42h", 0) for c in TOKENS
                    if (entry_t, c) in feat_by_ts_coin]
        if all_rets:
            alt_idx = float(np.mean(all_rets))
            if alt_idx < -1000:
                overlap_counts["S2"] += 1
                has_overlap = True

        # S4: vol_ratio < 1.0 (vol contraction — loose check)
        if f.get("vol_ratio", 2) < 1.0:
            overlap_counts["S4_approx"] += 1
            # Don't count as overlap — too loose

        # S5: sector divergence (approximate)
        sector = TOKEN_SECTOR.get(coin)
        if sector:
            peer_rets = [feat_by_ts_coin.get((entry_t, c), {}).get("ret_42h", 0)
                         for c in SECTORS.get(sector, []) if c != coin and (entry_t, c) in feat_by_ts_coin]
            if peer_rets:
                sector_mean = float(np.mean(peer_rets))
                div = f.get("ret_42h", 0) - sector_mean
                if abs(div) > 1000:
                    overlap_counts["S5"] += 1
                    has_overlap = True

        # S8: drawdown < -4000 + btc_7d < -300
        if f.get("drawdown", 0) < -4000 and btc_f.get("ret_42h", 0) < -300:
            overlap_counts["S8"] += 1
            has_overlap = True

        # S9: abs(ret_24h) > 2000
        if abs(f.get("ret_6h", 0)) > 2000:
            overlap_counts["S9"] += 1
            has_overlap = True

        if has_overlap:
            trades_with_overlap += 1

    print(f"\n  Total squeeze trades: {total_sq}")
    print(f"  Trades with ANY existing signal overlap: {trades_with_overlap} ({trades_with_overlap/total_sq*100:.0f}%)")
    print(f"  Trades UNIQUE to squeeze: {total_sq - trades_with_overlap} ({(total_sq - trades_with_overlap)/total_sq*100:.0f}%)")
    print(f"\n  Overlap by signal:")
    for sig in ["S1", "S2", "S5", "S8", "S9"]:
        n = overlap_counts.get(sig, 0)
        print(f"    {sig}: {n} trades ({n/total_sq*100:.1f}%)")

    # P&L of overlapping vs unique trades
    overlap_trades = []
    unique_trades = []
    for t in all_trades:
        coin = t["coin"]
        entry_t = t["entry_t"]
        f = feat_by_ts_coin.get((entry_t, coin))
        if not f:
            for dt in range(-14400000, 14400001, 14400000):
                f = feat_by_ts_coin.get((entry_t + dt, coin))
                if f:
                    break
        if not f:
            unique_trades.append(t)
            continue

        btc_f = btc_feats.get(entry_t) or btc_feats.get(entry_t - 14400000) or {}
        has = False
        if btc_f.get("ret_180h", 0) > 2000:
            has = True
        all_rets = [feat_by_ts_coin.get((entry_t, c), {}).get("ret_42h", 0) for c in TOKENS
                    if (entry_t, c) in feat_by_ts_coin]
        if all_rets and float(np.mean(all_rets)) < -1000:
            has = True
        if abs(f.get("ret_6h", 0)) > 2000:
            has = True
        if f.get("drawdown", 0) < -4000 and btc_f.get("ret_42h", 0) < -300:
            has = True
        sector = TOKEN_SECTOR.get(coin)
        if sector:
            peer_rets = [feat_by_ts_coin.get((entry_t, c), {}).get("ret_42h", 0)
                         for c in SECTORS.get(sector, []) if c != coin and (entry_t, c) in feat_by_ts_coin]
            if peer_rets and abs(f.get("ret_42h", 0) - float(np.mean(peer_rets))) > 1000:
                has = True

        if has:
            overlap_trades.append(t)
        else:
            unique_trades.append(t)

    s_overlap = score(overlap_trades)
    s_unique = score(unique_trades)
    print(f"\n  P&L overlapping: n={s_overlap['n']} avg={s_overlap['avg']:+.1f} ${s_overlap['pnl']:.0f}")
    print(f"  P&L UNIQUE:      n={s_unique['n']} avg={s_unique['avg']:+.1f} ${s_unique['pnl']:.0f}")
    print(f"\n  → {'Le signal apporte du flux NOUVEAU' if s_unique['n'] > total_sq * 0.5 and s_unique['avg'] > 0 else 'ATTENTION: trop de chevauchement ou unique non rentable'}")

    # ═══════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    print("VERDICT FINAL")
    print("=" * 60)

    checks = []
    # Check 1: concentration
    c1 = top5_pct < 70 and n_positive >= 15
    checks.append(("1. Concentration", c1, f"top5={top5_pct:.0f}%, {n_positive} positifs"))

    # Check 2: temporal stability
    c2 = q_pos >= q_neg
    checks.append(("2. Stabilité", c2, f"{q_pos}/{q_pos+q_neg} trimestres positifs"))

    # Check 3: cost resilience
    c3 = avg_gross > 25  # survives 25 bps costs
    checks.append(("3. Coûts", c3, f"gross={avg_gross:+.1f} bps, meurt à {avg_gross:.0f} bps de coûts"))

    # Check 4: parameter robustness
    c4 = positive / total_configs > 0.4 if total_configs > 0 else False
    checks.append(("4. Paramètres", c4, f"{positive}/{total_configs} ({positive/total_configs*100:.0f}%) positifs"))

    # Check 5: uniqueness
    unique_pct = (total_sq - trades_with_overlap) / total_sq * 100
    c5 = unique_pct > 50 and s_unique["avg"] > 0
    checks.append(("5. Unicité", c5, f"{unique_pct:.0f}% unique, avg unique={s_unique['avg']:+.1f}"))

    for name, passed, detail in checks:
        flag = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {name:>20}: {flag} — {detail}")

    all_pass = all(c for _, c, _ in checks)
    print(f"\n  {'→ INTÉGRER S10' if all_pass else '→ NE PAS INTÉGRER — vérifier les checks qui échouent'}")

    print("\nDone.")


if __name__ == "__main__":
    main()
