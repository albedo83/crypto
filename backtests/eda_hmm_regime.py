"""D3 — HMM régime EDA structure.

Train Gaussian HMM K=3 on BTC ret_30d series. Inspect:
- State priors / transition matrix
- Mean/std per state (should map to bear/neutral/bull)
- Median duration per state (regime persistence)
- Probability sequences over time
- Comparison with btc_z bucketing (bear z<-0.5, neutral, bull z>+0.5)

Output: state assignment per timestamp, transition probs, comparison stats.
"""
import json
import numpy as np
from hmmlearn.hmm import GaussianHMM

from backtests.backtest_genetic import load_3y_candles

print("Loading data...")
data = load_3y_candles()
btc = data["BTC"]
print(f"  BTC candles: {len(btc)}")

# Compute ret_30d sequence at every 4h candle
closes = np.array([c["c"] for c in btc])
ts = np.array([c["t"] for c in btc])
W = 180  # 30d on 4h grid
ret_30d = np.full(len(closes), np.nan)
for i in range(W, len(closes)):
    if closes[i - W] > 0:
        ret_30d[i] = (closes[i] / closes[i - W] - 1)

valid = ~np.isnan(ret_30d)
X = ret_30d[valid].reshape(-1, 1)
ts_valid = ts[valid]
print(f"  Valid observations: {len(X)} (= {len(X) * 4 / 24 / 30:.1f} months)")

# Train HMM K=3, multiple random restarts
best_model = None
best_score = -np.inf
print("\nTraining HMM K=3 with multi-restart...")
for seed in range(10):
    try:
        model = GaussianHMM(n_components=3, covariance_type="full",
                            n_iter=200, random_state=seed, tol=1e-4)
        model.fit(X)
        score = model.score(X)
        if score > best_score:
            best_score = score
            best_model = model
            best_seed = seed
    except Exception as e:
        continue
print(f"  Best seed: {best_seed}, log-likelihood: {best_score:.1f}")

# Inspect best model
print("\n=== HMM 3-state structure ===")
states = best_model.predict(X)
state_means = best_model.means_.flatten()
state_vars = best_model.covars_.flatten()

# Sort states by mean ret_30d to label bear/neutral/bull
order = np.argsort(state_means)
state_labels = ["bear", "neutral", "bull"]
state_map = {order[0]: "bear", order[1]: "neutral", order[2]: "bull"}
print(f"  state_map (raw → label): {state_map}")

for raw, lbl in state_map.items():
    print(f"  {lbl:>8}: μ ret_30d = {state_means[raw]:+.4f}  σ = {np.sqrt(state_vars[raw]):.4f}  "
          f"share = {(states == raw).mean() * 100:.1f}%")

print("\n  Transition matrix:")
trans = best_model.transmat_
print(f"  {'from\\to':<10}" + "".join(f"{state_map[i]:>10}" for i in range(3)))
for i in range(3):
    print(f"  {state_map[i]:<10}" + "".join(f"{trans[i, j]:>10.3f}" for j in range(3)))

# Median duration per state
print("\n  Median duration per state:")
for raw, lbl in state_map.items():
    runs = []
    current = 0
    for s in states:
        if s == raw:
            current += 1
        elif current > 0:
            runs.append(current)
            current = 0
    if current > 0:
        runs.append(current)
    if runs:
        # Candles → hours: each candle = 4h
        med_h = float(np.median(runs) * 4)
        max_h = float(max(runs) * 4)
        print(f"    {lbl:>8}: n_runs={len(runs)}  median={med_h:.0f}h  max={max_h:.0f}h")

# Compute btc_z (current bot mechanism) and compare regime assignments
print("\n=== Comparison with btc_z linear regime ===")
W_outer = 180  # 6m rolling window for z-score on ret_30d
btc_z = np.full(len(X), np.nan)
for i in range(W_outer, len(X)):
    window = X[i - W_outer: i].flatten()
    btc_z[i] = (X[i, 0] - window.mean()) / window.std() if window.std() > 0 else 0
valid_z = ~np.isnan(btc_z)
hmm_state = states[valid_z]
z_vals = btc_z[valid_z]
z_bucket = np.where(z_vals < -0.5, "bear", np.where(z_vals > 0.5, "bull", "neutral"))
hmm_label = np.array([state_map[s] for s in hmm_state])

# Confusion matrix
print(f"  {'HMM\\Z':<10}" + "".join(f"{lbl:>10}" for lbl in ["bear", "neutral", "bull"]))
for hl in ["bear", "neutral", "bull"]:
    row = [(hmm_label == hl) & (z_bucket == zl) for zl in ["bear", "neutral", "bull"]]
    print(f"  {hl:<10}" + "".join(f"{r.sum():>10}" for r in row))

# Per-state forward 24h return analysis
print("\n=== Forward 24h BTC return per HMM state ===")
# Compute forward 24h return at each ts
fwd_24h = np.full(len(closes), np.nan)
for i in range(len(closes) - 6):
    if closes[i] > 0:
        fwd_24h[i] = (closes[i + 6] / closes[i] - 1) * 1e4
fwd_valid = fwd_24h[valid]
for raw, lbl in state_map.items():
    mask = (states == raw) & (~np.isnan(fwd_valid))
    if mask.sum() > 0:
        f = fwd_valid[mask]
        print(f"  {lbl:>8}: n={mask.sum():>5}  μ fwd24h={f.mean():+6.1f}  median={np.median(f):+6.1f}  σ={f.std():.0f}")

# Save model params for production use
model_params = {
    "log_likelihood": float(best_score),
    "n_components": 3,
    "state_labels": state_map,
    "state_means": [float(x) for x in state_means.tolist()],
    "state_vars": [float(x) for x in state_vars.tolist()],
    "transmat": [[float(x) for x in row] for row in trans.tolist()],
    "startprob": [float(x) for x in best_model.startprob_.tolist()],
    "trained_on_seed": best_seed,
    "n_observations": len(X),
    "first_ts": int(ts_valid[0]),
    "last_ts": int(ts_valid[-1]),
}
with open("/home/crypto/backtests/output/hmm_3state_model.json", "w") as f:
    json.dump(model_params, f, indent=2)

print(f"\nModel params saved to backtests/output/hmm_3state_model.json")
print(f"\n=== EDA VERDICT ===")
# Sanity: regimes should not be all identical; transition diagonal high but not == 1; reasonable durations
durations_ok = all(state_means[order[0]] < -0.005 and state_means[order[2]] > 0.005 for _ in [None])  # bear ret<0, bull ret>0
trans_ok = all(trans[i, i] > 0.85 for i in range(3))  # persistent regimes
match_pct = float((hmm_label == z_bucket).mean() * 100)
print(f"  Bear mean < 0: {state_means[order[0]] < 0}  Bull mean > 0: {state_means[order[2]] > 0}")
print(f"  Transition diagonal > 0.85: {trans_ok}")
print(f"  Agreement HMM vs z-bucket: {match_pct:.1f}%")
if match_pct > 70:
    print(f"  → HMM rediscovers z-score classification — may not add edge by itself")
elif match_pct < 50:
    print(f"  → HMM differs substantially from z-score — potentially captures new info")
else:
    print(f"  → HMM partially differs from z-score — worth WF test")
