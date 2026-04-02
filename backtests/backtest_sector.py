"""Sector Divergence Strategy — Volume-confirmed intra-sector trades.

Idea: tokens in the same sector are correlated. When one diverges:
  - Low volume divergence = noise → fade it (mean-revert)
  - High volume divergence = real signal → follow it (momentum on sector)

Sectors:
  L1:     SOL, AVAX, SUI, APT, NEAR, SEI
  DeFi:   AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO
  Gaming: GALA, IMX, SAND
  Infra:  LINK, PYTH, STX, INJ, ARB, OP
  Meme:   DOGE, WLD, BLUR, MINA

Usage:
    python3 -m analysis.backtest_sector
"""

from __future__ import annotations

import json, os, time, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE, MAX_POSITIONS, MAX_SAME_DIR,
    TRAIN_END, TEST_START,
)

SECTORS = {
    "L1":     ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi":   ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO"],
    "Gaming": ["GALA", "IMX", "SAND"],
    "Infra":  ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":   ["DOGE", "WLD", "BLUR", "MINA"],
}

# Reverse: token → sector
TOKEN_SECTOR = {}
for sector, tokens in SECTORS.items():
    for t in tokens:
        TOKEN_SECTOR[t] = sector


def compute_sector_features(features, data):
    """Compute sector-level features at each timestamp.

    For each token at each timestamp:
    - sector_mean_ret: average 7d return of its sector (excluding itself)
    - divergence: token ret_42h - sector_mean_ret (how far from the group)
    - vol_z_token: volume z-score of the token (current vs 30d avg)
    - sector_dispersion: std of returns within the sector
    """
    coins = [c for c in TOKENS if c in features and c in data]

    # Build {timestamp: {coin: features}} lookup
    by_ts = defaultdict(dict)
    all_ts = set()
    for coin in coins:
        for f in features[coin]:
            by_ts[f["t"]][coin] = f
            all_ts.add(f["t"])

    # Compute sector features
    sector_features = {}  # {(ts, coin): {divergence, vol_z, sector_mean, ...}}

    for ts in sorted(all_ts):
        available = by_ts[ts]

        # Compute sector averages
        sector_rets = defaultdict(list)
        for coin, f in available.items():
            sector = TOKEN_SECTOR.get(coin)
            if sector and "ret_42h" in f:
                sector_rets[sector].append((coin, f["ret_42h"]))

        for coin, f in available.items():
            sector = TOKEN_SECTOR.get(coin)
            if not sector or "ret_42h" not in f:
                continue

            # Sector mean excluding self
            peers = [(c, r) for c, r in sector_rets.get(sector, []) if c != coin]
            if len(peers) < 2:
                continue

            peer_rets = [r for _, r in peers]
            sector_mean = float(np.mean(peer_rets))
            sector_std = float(np.std(peer_rets)) if len(peer_rets) > 1 else 1.0

            divergence = f["ret_42h"] - sector_mean
            div_z = divergence / sector_std if sector_std > 0 else 0

            sector_features[(ts, coin)] = {
                "divergence": divergence,
                "div_z": div_z,
                "sector_mean": sector_mean,
                "sector_std": sector_std,
                "token_ret": f["ret_42h"],
                "vol_z": f.get("vol_z", 0),
                "vol_ratio": f.get("vol_ratio", 1.0),
                "sector": sector,
                "idx": f["_idx"],
            }

    return sector_features


def backtest_sector(sector_features, data, config):
    """Backtest sector divergence strategy.

    config:
        div_threshold: min divergence in bps to trigger
        vol_z_high: volume z-score threshold for "high volume"
        vol_z_low: volume z-score threshold for "low volume"
        hold: hold period in 4h candles
        mode: "both", "fade_only", "follow_only"
    """
    div_thresh = config.get("div_threshold", 500)
    vol_z_high = config.get("vol_z_high", 1.5)
    vol_z_low = config.get("vol_z_low", 0.5)
    hold = config.get("hold", 18)
    cost = config.get("cost", COST_BPS)
    size = config.get("size", POSITION_SIZE)
    max_pos = config.get("max_pos", MAX_POSITIONS)
    max_dir = config.get("max_dir", MAX_SAME_DIR)
    mode = config.get("mode", "both")
    period = config.get("period", "all")

    # Group by timestamp
    by_ts = defaultdict(list)
    for (ts, coin), sf in sector_features.items():
        if period == "train" and ts >= TRAIN_END:
            continue
        if period == "test" and ts < TEST_START:
            continue
        by_ts[ts].append((coin, sf))

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
                            trades.append({
                                "coin": coin, "pnl": size * net / 1e4,
                                "net": net, "dir": pos["dir"],
                                "entry_t": pos["entry_t"],
                                "exit_t": ts, "hold": held,
                                "signal": pos["signal"],
                            })
                        del positions[coin]
                        cooldown[coin] = ts + 24 * 3600 * 1000
                    break

        # Entries
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)

        candidates = []
        for coin, sf in by_ts[ts]:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            if abs(sf["divergence"]) < div_thresh:
                continue

            vol_z = sf["vol_z"]
            div = sf["divergence"]

            # Decision: fade or follow based on volume
            if vol_z < vol_z_low and mode in ("both", "fade_only"):
                # Low volume → noise → FADE the divergence
                direction = -1 if div > 0 else 1  # diverged up → short, diverged down → long
                signal = f"FADE {sf['sector']} div={div:+.0f} vz={vol_z:.1f}"
            elif vol_z > vol_z_high and mode in ("both", "follow_only"):
                # High volume → real → FOLLOW the divergence
                direction = 1 if div > 0 else -1
                signal = f"FOLLOW {sf['sector']} div={div:+.0f} vz={vol_z:.1f}"
            else:
                continue

            if direction == 1 and n_long >= max_dir:
                continue
            if direction == -1 and n_short >= max_dir:
                continue

            idx = sf["idx"]
            if idx + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx + 1]["o"]
            if entry <= 0:
                continue

            candidates.append({
                "coin": coin, "dir": direction, "entry": entry,
                "idx": idx + 1, "entry_t": data[coin][idx + 1]["t"],
                "strength": abs(sf["div_z"]),
                "signal": signal,
            })

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        slots = max_pos - len(positions)
        for c in candidates[:slots]:
            positions[c["coin"]] = c
            if c["dir"] == 1:
                n_long += 1
            else:
                n_short += 1

    return trades


def analyze(trades, label):
    if not trades:
        print(f"  {label}: 0 trades")
        return {"pnl": 0, "n": 0}

    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    wr = wins / n * 100

    # Train/test
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

    # Fade vs follow breakdown
    fades = [t for t in trades if "FADE" in t.get("signal", "")]
    follows = [t for t in trades if "FOLLOW" in t.get("signal", "")]

    valid = "✓" if train_pnl > 0 and test_pnl > 0 else ""
    print(f"  {label:<50} ${pnl:>+7.0f} ({n:>4}t, avg={avg:>+5.1f}bp, win={wr:.0f}%) "
          f"trn=${train_pnl:>+.0f} tst=${test_pnl:>+.0f} {valid}")

    if fades:
        fp = sum(t["pnl"] for t in fades)
        fa = float(np.mean([t["net"] for t in fades]))
        print(f"    FADE:   ${fp:>+7.0f} ({len(fades):>3}t, avg={fa:>+5.1f}bp)")
    if follows:
        fp = sum(t["pnl"] for t in follows)
        fa = float(np.mean([t["net"] for t in follows]))
        print(f"    FOLLOW: ${fp:>+7.0f} ({len(follows):>3}t, avg={fa:>+5.1f}bp)")

    return {"pnl": pnl, "n": n, "avg": avg, "wr": wr,
            "train": train_pnl, "test": test_pnl, "trades": trades}


def monte_carlo(trades, data, n_sims=500):
    """Direction-matched Monte Carlo."""
    actual_pnl = sum(t["pnl"] for t in trades)
    by_coin = defaultdict(lambda: {"long": 0, "short": 0})
    for t in trades:
        by_coin[t["coin"]]["long" if t["dir"] == 1 else "short"] += 1

    avg_hold = int(np.mean([t["hold"] for t in trades]))
    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        for coin, counts in by_coin.items():
            if coin not in data:
                continue
            candles = data[coin]
            nc = len(candles)
            available = list(range(180, nc - avg_hold - 1))
            needed = counts["long"] + counts["short"]
            if len(available) < needed:
                continue
            sampled = random.sample(available, needed)
            for j, idx in enumerate(sampled):
                d = 1 if j < counts["long"] else -1
                entry = candles[min(idx+1, nc-1)]["o"]
                exit_p = candles[min(idx+1+avg_hold, nc-1)]["c"]
                if entry <= 0:
                    continue
                gross = d * (exit_p / entry - 1) * 1e4
                net = gross - COST_BPS
                sim += POSITION_SIZE * net / 1e4
        sim_pnls.append(sim)

    mean = float(np.mean(sim_pnls))
    std = float(np.std(sim_pnls))
    z = (actual_pnl - mean) / std if std > 0 else 0
    return {"actual": actual_pnl, "mean": mean, "std": std, "z": z}


def main():
    print("=" * 70)
    print("  SECTOR DIVERGENCE STRATEGY")
    print("  Volume-confirmed intra-sector trading")
    print("=" * 70)

    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    features = build_features(data)
    print(f"Built features")

    # Show sectors
    print(f"\n  Sectors:")
    for name, tokens in SECTORS.items():
        available = [t for t in tokens if t in data]
        print(f"    {name:<8} ({len(available)}): {', '.join(available)}")

    print(f"\n  Computing sector features...")
    t0 = time.time()
    sf = compute_sector_features(features, data)
    print(f"  {len(sf):,} sector-feature rows in {time.time()-t0:.1f}s")

    # Show divergence stats
    divs = [v["divergence"] for v in sf.values()]
    vol_zs = [v["vol_z"] for v in sf.values()]
    print(f"  Divergence: mean={np.mean(divs):+.0f} std={np.std(divs):.0f} "
          f"p5/p95=[{np.percentile(divs,5):.0f}, {np.percentile(divs,95):.0f}]")
    print(f"  Volume z:   mean={np.mean(vol_zs):.2f} std={np.std(vol_zs):.2f}")

    # ═══════════════════════════════════════════════════════════════
    # Test 1: Full parameter sweep
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  PARAMETER SWEEP")
    print(f"{'='*70}")

    results = []

    for div_thresh in [300, 500, 750, 1000, 1500]:
        for vol_z_high in [1.0, 1.5, 2.0]:
            for vol_z_low in [0.0, 0.5, 1.0]:
                if vol_z_low >= vol_z_high:
                    continue
                for hold in [6, 12, 18]:
                    for mode in ["both", "fade_only", "follow_only"]:
                        config = {
                            "div_threshold": div_thresh,
                            "vol_z_high": vol_z_high,
                            "vol_z_low": vol_z_low,
                            "hold": hold,
                            "mode": mode,
                        }
                        trades = backtest_sector(sf, data, config)
                        if len(trades) < 15:
                            continue
                        pnl = sum(t["pnl"] for t in trades)
                        avg = float(np.mean([t["net"] for t in trades]))
                        train_pnl = sum(t["pnl"] for t in trades if t["entry_t"] < TRAIN_END)
                        test_pnl = sum(t["pnl"] for t in trades if t["entry_t"] >= TEST_START)

                        results.append({
                            "config": config, "pnl": pnl, "n": len(trades),
                            "avg": avg, "train": train_pnl, "test": test_pnl,
                            "trades": trades,
                        })

    # Sort by total P&L
    results.sort(key=lambda r: r["pnl"], reverse=True)

    print(f"\n  Tested {len(results)} configs")

    # Show those profitable on BOTH train and test
    valid = [r for r in results if r["train"] > 0 and r["test"] > 0 and r["n"] >= 20]
    print(f"  Profitable on train+test: {len(valid)}")

    if valid:
        print(f"\n  Top configs (train+test profitable):")
        print(f"  {'Config':<55} {'P&L':>7} {'N':>5} {'Avg':>6} {'Train':>7} {'Test':>7}")
        print(f"  {'-'*95}")
        for r in valid[:20]:
            c = r["config"]
            label = f"div>{c['div_threshold']} vhi={c['vol_z_high']} vlo={c['vol_z_low']} h={c['hold']*4}h {c['mode']}"
            print(f"  {label:<55} ${r['pnl']:>+6.0f} {r['n']:>4} {r['avg']:>+5.1f} "
                  f"${r['train']:>+6.0f} ${r['test']:>+6.0f} ✓")

    # Show worst too
    print(f"\n  Worst configs:")
    for r in results[-5:]:
        c = r["config"]
        label = f"div>{c['div_threshold']} vhi={c['vol_z_high']} vlo={c['vol_z_low']} h={c['hold']*4}h {c['mode']}"
        print(f"  {label:<55} ${r['pnl']:>+6.0f} {r['n']:>4}")

    # ═══════════════════════════════════════════════════════════════
    # Detailed analysis of best
    # ═══════════════════════════════════════════════════════════════
    if valid:
        print(f"\n{'='*70}")
        print(f"  DETAILED ANALYSIS — Best config")
        print(f"{'='*70}")

        best = valid[0]
        c = best["config"]
        label = f"div>{c['div_threshold']} vhi={c['vol_z_high']} vlo={c['vol_z_low']} h={c['hold']*4}h {c['mode']}"
        r = analyze(best["trades"], label)

        # By sector
        by_sector = defaultdict(list)
        for t in best["trades"]:
            sector = TOKEN_SECTOR.get(t["coin"], "?")
            by_sector[sector].append(t)

        print(f"\n  By sector:")
        for sector in sorted(by_sector):
            st = by_sector[sector]
            sp = sum(t["pnl"] for t in st)
            sa = float(np.mean([t["net"] for t in st]))
            print(f"    {sector:<8} ${sp:>+7.0f} ({len(st):>3}t, avg={sa:>+5.1f}bp)")

        # Monthly
        by_month = defaultdict(float)
        for t in best["trades"]:
            dt = datetime.fromtimestamp(t["entry_t"] / 1000, tz=timezone.utc)
            by_month[dt.strftime("%Y-%m")] += t["pnl"]

        months = sorted(by_month)
        winning = sum(1 for m in months if by_month[m] > 0)
        print(f"\n  Monthly ({winning}/{len(months)} winning):")
        cum = 0
        for m in months:
            cum += by_month[m]
            marker = "✓" if by_month[m] > 0 else "✗"
            print(f"    {m}: ${by_month[m]:>+7.1f} cum=${cum:>+.0f} {marker}")

        # Monte Carlo
        print(f"\n  Monte Carlo (500 sims)...")
        mc = monte_carlo(best["trades"], data)
        print(f"    Actual:      ${mc['actual']:>+.0f}")
        print(f"    Random mean: ${mc['mean']:>+.0f} (std: ${mc['std']:.0f})")
        print(f"    Z-score:     {mc['z']:+.2f}")
        if mc["z"] > 2.5:
            print(f"    → ✓✓ STRONGLY SIGNIFICANT")
        elif mc["z"] > 2.0:
            print(f"    → ✓ SIGNIFICANT")
        elif mc["z"] > 1.5:
            print(f"    → ⚠ MARGINAL")
        else:
            print(f"    → ✗ NOT SIGNIFICANT")

    # ═══════════════════════════════════════════════════════════════
    # Also test: fade-only vs follow-only analysis
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  FADE vs FOLLOW — Which mechanism works?")
    print(f"{'='*70}")

    for mode in ["fade_only", "follow_only"]:
        mode_valid = [r for r in results
                      if r["config"]["mode"] == mode and r["train"] > 0 and r["test"] > 0 and r["n"] >= 20]
        if mode_valid:
            best_mode = mode_valid[0]
            c = best_mode["config"]
            label = f"{mode}: div>{c['div_threshold']} vhi={c['vol_z_high']} vlo={c['vol_z_low']} h={c['hold']*4}h"
            analyze(best_mode["trades"], label)
        else:
            print(f"  {mode}: no config profitable on train+test")


if __name__ == "__main__":
    main()
