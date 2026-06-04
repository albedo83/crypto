"""EDA #3 — Cointégration cross-perp.

Pour chaque paire (i, j) parmi 35 tokens HL :
1. OLS: close_i = α + β × close_j + ε
2. ADF test sur résidus → p-value
3. Si p < 0.05 → cointégrée
4. Stability check: split en 2 fenêtres 12m, refit chaque, comparer β.
   Si β stable (Δ < 30%) + cointégration sur les 2 fenêtres → ROBUSTE.

Output: top 15 paires cointégrées robustes + leur z-score actuel
(deviation from equilibrium → potentiel trade stat arb).

Données: candles 4h via backtests.backtest_genetic.load_3y_candles.
"""
import numpy as np
import json
import time
from itertools import combinations
from statsmodels.tsa.stattools import adfuller
from backtests.backtest_genetic import load_3y_candles

print("Loading 4h candles...")
data = load_3y_candles()
print(f"  {len(data)} tokens loaded")

# Build aligned closes matrix
# Use only tokens with >= 1080 candles (= 6m on 4h grid) to ensure split-half analysis
MIN_CANDLES = 2160  # 12m of 4h candles for 2 windows
HALF_M = 1080

# Filter tokens: only those with at least MIN_CANDLES history each
print("Filtering tokens by history length...")
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_CANDLES}
print(f"  {len(eligible)}/{len(data)} tokens with >= {MIN_CANDLES} candles")
excluded = [s for s in data if s not in eligible]
print(f"  Excluded (insufficient history): {sorted(excluded)}")

# Build per-token timestamp → close mapping (no cross-token intersection yet)
print("Building per-token close maps...")
closes_by_ts = {}
for sym, cs in eligible.items():
    closes_by_ts[sym] = {c["t"]: c["c"] for c in cs if c["c"] > 0}
    print(f"  {sym:6}: {len(closes_by_ts[sym])} candles, latest ts={max(closes_by_ts[sym].keys())}")

closes = closes_by_ts  # rename for downstream

# Run pairwise cointegration with per-pair aligned timestamps
syms = sorted(closes.keys())
pairs = list(combinations(syms, 2))
print(f"\nTesting {len(pairs)} pairs (using pair-wise intersection)...")

results = []
start = time.time()
for i, (a, b) in enumerate(pairs):
    if i > 0 and i % 100 == 0:
        elapsed = time.time() - start
        rate = i / elapsed
        eta = (len(pairs) - i) / rate
        print(f"  [{i:>3}/{len(pairs)}] {a}/{b} elapsed={elapsed:.0f}s rate={rate:.1f}/s eta={eta:.0f}s "
              f"(robust so far: {sum(1 for r in results if r.get('cointegrated_both_halves') and r.get('beta_drift_pct',999) < 30)})")

    common_ts = sorted(set(closes[a].keys()) & set(closes[b].keys()))
    if len(common_ts) < HALF_M * 2:
        continue
    common_ts = common_ts[-MIN_CANDLES:]  # latest MIN_CANDLES of intersection

    x = np.log(np.array([closes[a][t] for t in common_ts]))
    y = np.log(np.array([closes[b][t] for t in common_ts]))

    # Full window cointegration test
    try:
        X = np.column_stack([np.ones_like(x), x])
        beta_full, *_ = np.linalg.lstsq(X, y, rcond=None)
        residuals_full = y - X @ beta_full
        adf_full = adfuller(residuals_full, autolag="AIC")
        p_full = adf_full[1]
        beta_full_slope = float(beta_full[1])
    except Exception:
        continue

    if p_full > 0.10:  # not even close to cointegrated
        continue

    # Split-half stability
    half = len(x) // 2
    x1, y1 = x[:half], y[:half]
    x2, y2 = x[half:], y[half:]
    try:
        X1 = np.column_stack([np.ones_like(x1), x1])
        b1, *_ = np.linalg.lstsq(X1, y1, rcond=None)
        res1 = y1 - X1 @ b1
        p1 = adfuller(res1, autolag="AIC")[1]
        X2 = np.column_stack([np.ones_like(x2), x2])
        b2, *_ = np.linalg.lstsq(X2, y2, rcond=None)
        res2 = y2 - X2 @ b2
        p2 = adfuller(res2, autolag="AIC")[1]
    except Exception:
        continue

    # Stability: both halves cointegrated AND β stable (relative change < 30%)
    if abs(b1[1]) < 1e-6:
        beta_drift = 9999
    else:
        beta_drift = abs(b2[1] - b1[1]) / abs(b1[1])
    cointegrated_both = (p1 < 0.05 and p2 < 0.05)

    # Current z-score (where are we now in the spread)
    spread_std = float(np.std(residuals_full))
    current_z = float(residuals_full[-1] / spread_std) if spread_std > 0 else 0

    results.append({
        "pair": f"{a}/{b}",
        "a": a, "b": b,
        "p_full": float(p_full),
        "p_h1": float(p1),
        "p_h2": float(p2),
        "beta_full": beta_full_slope,
        "beta_h1": float(b1[1]),
        "beta_h2": float(b2[1]),
        "beta_drift_pct": float(beta_drift * 100),
        "cointegrated_both_halves": bool(cointegrated_both),
        "current_z": current_z,
        "spread_std": spread_std,
    })

print(f"\n  Tested {len(pairs)} pairs in {time.time() - start:.1f}s")
print(f"  {len(results)} pairs with p_full < 0.10")

# Filter: robust = cointegrated on both halves + β drift < 30%
robust = [r for r in results if r["cointegrated_both_halves"] and r["beta_drift_pct"] < 30]
robust.sort(key=lambda r: r["p_full"])

print(f"\n=== ROBUST COINTEGRATED PAIRS (both halves p<0.05, β drift <30%) ===")
print(f"  Count: {len(robust)}\n")
print(f"{'Pair':<14} {'p_full':>8} {'p_h1':>8} {'p_h2':>8} {'β_drift%':>9} {'curr_z':>7}")
for r in robust[:15]:
    print(f"{r['pair']:<14} {r['p_full']:>8.4f} {r['p_h1']:>8.4f} {r['p_h2']:>8.4f} "
          f"{r['beta_drift_pct']:>9.1f} {r['current_z']:>+7.2f}")

# All non-robust but cointegrated (for context)
loose = [r for r in results if r["p_full"] < 0.05 and r not in robust]
loose.sort(key=lambda r: r["p_full"])
print(f"\n=== Loose (p_full<0.05 mais pas robuste) — {len(loose)} pairs ===")
for r in loose[:5]:
    why = ""
    if not r["cointegrated_both_halves"]:
        why += f"halves split ({r['p_h1']:.3f}/{r['p_h2']:.3f}) "
    if r["beta_drift_pct"] >= 30:
        why += f"β drift {r['beta_drift_pct']:.0f}%"
    print(f"  {r['pair']:<14}  p={r['p_full']:.4f}  {why}")

# Save results
with open("/home/crypto/backtests/output/eda_cointegration_results.json", "w") as f:
    json.dump({
        "n_pairs_tested": len(pairs),
        "n_pairs_significant": len(results),
        "n_robust": len(robust),
        "robust_pairs": robust,
        "loose_pairs": loose[:20],  # top 20 loose for context
    }, f, indent=2)

print(f"\nResults saved to backtests/output/eda_cointegration_results.json")

# Verdict
print(f"\n=== VERDICT ===")
if len(robust) == 0:
    print("  No robust cointegrated pairs found → stat arb not viable on these tokens")
elif len(robust) <= 3:
    print(f"  {len(robust)} robust pairs found → niche edge possible, low capacity")
else:
    print(f"  {len(robust)} robust pairs found → meaningful candidate basket for stat arb")
print(f"  Note: cointégration empirique ≠ profitable trade (need fees, slippage, half-life)")
