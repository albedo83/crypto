"""D2 — Lead-lag dynamics EDA.

For each pair (i, j) and lag τ ∈ {1..6 candles}, compute lagged correlation:
    C[i,j,τ] = corr(ret_i(t), ret_j(t-τ))

If C[i,j,τ] > threshold and significantly higher than C[i,j,-τ], token j leads token i.

Tests:
1. STRUCTURE: identify top-3 leaders + top-3 followers in 30d rolling window. Are they stable over time?
2. PREDICTIVITY: when leader signals (extreme ret), does follower's forward 4h return systematically follow?
3. REGIME: do leader/follower roles change between bear/neutral/bull regimes?
"""
import json
import numpy as np
import time
from itertools import product

from backtests.backtest_genetic import load_3y_candles

print("Loading 4h candles...")
data = load_3y_candles()
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)} eligible tokens")

# Build returns matrix on common time grid (last ~24m)
print("Building returns matrix...")
ts_sets = {sym: set(c["t"] for c in cs[-MIN_HIST:]) for sym, cs in eligible.items()}
common_ts = sorted(set.intersection(*ts_sets.values()))
if len(common_ts) < 500:
    print(f"  Too few common timestamps ({len(common_ts)})")
    exit(1)
print(f"  {len(common_ts)} aligned ts ({len(common_ts) * 4 / 24 / 30:.1f}m)")

# Build matrix of 1-candle returns (4h returns)
ts_to_idx = {t: i for i, t in enumerate(common_ts)}
closes_mat = np.full((len(common_ts), len(eligible)), np.nan)
sym_list = sorted(eligible.keys())
sym_to_col = {s: i for i, s in enumerate(sym_list)}
for sym, cs in eligible.items():
    col = sym_to_col[sym]
    for c in cs:
        if c["t"] in ts_to_idx:
            closes_mat[ts_to_idx[c["t"]], col] = c["c"]

# Drop rows with any NaN
valid_rows = ~np.isnan(closes_mat).any(axis=1) & (closes_mat > 0).all(axis=1)
closes_clean = closes_mat[valid_rows]
ts_clean = np.array(common_ts)[valid_rows]
print(f"  Clean rows: {len(closes_clean)}")

# Compute 1-candle log returns
rets = np.diff(np.log(closes_clean), axis=0)
print(f"  Returns shape: {rets.shape} (T × N)")

T, N = rets.shape

# Lagged correlation matrix C[lag, i, j] = corr(rets[t-lag, i], rets[t, j])
# We want C such that if rets[i, t-lag] correlates with rets[j, t], then i leads j by lag
print("\nComputing lagged cross-correlations...")
LAGS = [1, 2, 3, 6]  # 4h, 8h, 12h, 24h
C_lagged = np.zeros((len(LAGS), N, N))
for li, lag in enumerate(LAGS):
    x = rets[:-lag, :]  # leader returns at t
    y = rets[lag:, :]   # follower returns at t+lag
    # corr(x[:, i], y[:, j]) computed in vectorized form
    x_mean = x.mean(axis=0)
    y_mean = y.mean(axis=0)
    x_std = x.std(axis=0)
    y_std = y.std(axis=0)
    # Avoid division by zero
    x_std = np.where(x_std == 0, 1, x_std)
    y_std = np.where(y_std == 0, 1, y_std)
    x_n = (x - x_mean) / x_std
    y_n = (y - y_mean) / y_std
    C_lagged[li] = (x_n.T @ y_n) / len(x)
    print(f"  lag={lag}: max C = {C_lagged[li].max():.3f}, "
          f"mean|C|={np.abs(C_lagged[li]).mean():.3f}")

# Instantaneous correlation (lag=0) for comparison
C_instant = np.corrcoef(rets.T)

# Identify pairs where forward C >> backward C (asymmetric → lead-lag)
print("\n=== Lead-lag asymmetry analysis ===")
print(f"{'Pair (leader → follower)':<35} {'lag':>4} {'C_fwd':>8} {'C_back':>8} {'asym':>8} {'instant':>8}")
print("-" * 90)

# For each pair (i, j), compare C_lagged[lag, i, j] (i leads j) vs C_lagged[lag, j, i] (j leads i)
results_pairs = []
for li, lag in enumerate(LAGS):
    C = C_lagged[li]
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            c_fwd = C[i, j]
            c_back = C[j, i]
            asym = c_fwd - c_back
            if c_fwd > 0.1 and asym > 0.05:  # i leads j with meaningful margin
                results_pairs.append({
                    "leader": sym_list[i],
                    "follower": sym_list[j],
                    "lag": lag,
                    "c_fwd": float(c_fwd),
                    "c_back": float(c_back),
                    "asym": float(asym),
                    "instant": float(C_instant[i, j]),
                })

results_pairs.sort(key=lambda r: r["asym"], reverse=True)
for r in results_pairs[:20]:
    print(f"{r['leader']:>5} → {r['follower']:<15} {r['lag']:>4}  {r['c_fwd']:>8.3f} "
          f"{r['c_back']:>8.3f} {r['asym']:>+8.3f} {r['instant']:>8.3f}")

print(f"\nTotal pairs with asymmetric lead-lag: {len(results_pairs)}")

# Stability check: split into 2 halves and re-compute top leaders
print("\n=== Stability check (first half vs second half) ===")
half = T // 2
rets_h1 = rets[:half]
rets_h2 = rets[half:]

leaders_h1 = {}
leaders_h2 = {}

for li, lag in enumerate(LAGS):
    if half < lag * 5:
        continue
    x1 = rets_h1[:-lag, :]
    y1 = rets_h1[lag:, :]
    x2 = rets_h2[:-lag, :]
    y2 = rets_h2[lag:, :]
    x1n = (x1 - x1.mean(axis=0)) / np.where(x1.std(axis=0) == 0, 1, x1.std(axis=0))
    y1n = (y1 - y1.mean(axis=0)) / np.where(y1.std(axis=0) == 0, 1, y1.std(axis=0))
    x2n = (x2 - x2.mean(axis=0)) / np.where(x2.std(axis=0) == 0, 1, x2.std(axis=0))
    y2n = (y2 - y2.mean(axis=0)) / np.where(y2.std(axis=0) == 0, 1, y2.std(axis=0))
    C1 = (x1n.T @ y1n) / len(x1)
    C2 = (x2n.T @ y2n) / len(x2)
    # Top 5 leaders by mean asym in each half
    asym1 = C1 - C1.T  # asymmetry matrix
    asym2 = C2 - C2.T
    # Leader strength = avg(asym1[i, :]) across all j
    leader_score_1 = asym1.mean(axis=1)
    leader_score_2 = asym2.mean(axis=1)
    top1_idx = np.argsort(leader_score_1)[::-1][:5]
    top2_idx = np.argsort(leader_score_2)[::-1][:5]
    print(f"  lag={lag}:")
    print(f"    H1 top leaders: {[sym_list[i] for i in top1_idx]}")
    print(f"    H2 top leaders: {[sym_list[i] for i in top2_idx]}")
    # Set similarity
    overlap = len(set([sym_list[i] for i in top1_idx]) & set([sym_list[i] for i in top2_idx]))
    print(f"    Overlap top-5: {overlap}/5")
    leaders_h1[lag] = [sym_list[i] for i in top1_idx]
    leaders_h2[lag] = [sym_list[i] for i in top2_idx]

# Predictivity test: if leader's prior return is extreme, does follower's forward return systematically follow?
# For top pairs, simulate "trade follower direction of leader's extreme move"
print("\n=== Predictivity test on top lead-lag pairs ===")
COST_BPS = 26
top_pairs = results_pairs[:10]
print(f"{'Leader → Follower':<22} {'lag':>4} {'n_events':>9} {'WR':>6} {'net μ bps':>10}")
print("-" * 70)
for pair in top_pairs:
    leader = pair["leader"]
    follower = pair["follower"]
    lag = pair["lag"]
    i_lead = sym_to_col[leader]
    i_foll = sym_to_col[follower]
    # When leader's return at t is extreme (>p95 absolute), trade follower direction at t+lag
    leader_rets = rets[:-lag, i_lead]
    follower_rets = rets[lag:, i_foll]
    threshold = float(np.percentile(np.abs(leader_rets), 95))
    extreme_mask = np.abs(leader_rets) >= threshold
    if extreme_mask.sum() < 10:
        continue
    # Trade direction = sign of leader's move
    direction = np.sign(leader_rets[extreme_mask])
    # follower's return at t+lag in basis points
    foll_ret_bps = follower_rets[extreme_mask] * 1e4
    # PnL signed by direction
    pnl_bps = direction * foll_ret_bps - COST_BPS
    wr = float((pnl_bps > 0).mean() * 100)
    net_mu = float(pnl_bps.mean())
    print(f"{leader:>5} → {follower:<15} {lag:>4} {extreme_mask.sum():>9} {wr:>5.1f}% {net_mu:>+10.1f}")

# Save
with open("/home/crypto/backtests/output/eda_lead_lag.json", "w") as f:
    json.dump({
        "n_eligible_tokens": len(eligible),
        "n_aligned_ts": len(common_ts),
        "n_clean_returns": int(T),
        "lags_tested": LAGS,
        "top_pairs": results_pairs[:30],
        "leaders_per_half": {f"lag_{k}": {"h1": leaders_h1.get(k, []), "h2": leaders_h2.get(k, [])} for k in LAGS if k in leaders_h1},
    }, f, indent=2)

print(f"\nResults saved to backtests/output/eda_lead_lag.json")

print("\n=== EDA VERDICT ===")
if not results_pairs:
    print("  No significant lead-lag pairs found — direction dead")
elif len(results_pairs) < 5:
    print(f"  Only {len(results_pairs)} lead-lag pairs found — weak structure")
else:
    print(f"  {len(results_pairs)} lead-lag pairs found, top asymmetry = {results_pairs[0]['asym']:.3f}")
    avg_top_overlap = np.mean([
        len(set(leaders_h1.get(k, [])) & set(leaders_h2.get(k, [])))
        for k in LAGS if k in leaders_h1
    ]) if leaders_h1 else 0
    print(f"  Top-5 leader stability across halves: {avg_top_overlap:.1f}/5 average overlap")
    if avg_top_overlap >= 3:
        print(f"  → Stable enough for WF test")
    else:
        print(f"  → Leaders drift across halves — fragile")
