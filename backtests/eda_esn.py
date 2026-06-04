"""D1 — Reservoir Computing (Echo State Network) EDA prédictivité.

Train ESN to predict forward 24h return sign per token from feature sequence.

Architecture:
- Input: per-token sequence of features (ret_24h, ret_42h, drawdown, vol_z, vol_ratio, range_pct)
- Reservoir: 200 hidden units, spectral radius 0.9, leak rate 0.3
- Output: forward 24h return sign (continuous, then thresholded)

Test: AUC on holdout split. PASS if AUC > 0.55 on all 4 splits.
"""
import json
import numpy as np
import time
from reservoirpy.nodes import Reservoir, Ridge

from backtests.backtest_genetic import load_3y_candles

print("Loading 4h candles...")
data = load_3y_candles()
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)} eligible tokens")

# Build feature sequence per (token, ts)
print("Building feature sequences...")
sym_to_arr = {sym: cs for sym, cs in eligible.items()}
sym_to_idx = {sym: {c["t"]: i for i, c in enumerate(cs)} for sym, cs in eligible.items()}

def compute_features(sym: str, ts: int):
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i < 180:
        return None
    closes = np.array([c["c"] for c in arr[max(0, i - 180): i + 1]])
    highs = np.array([c["h"] for c in arr[max(0, i - 180): i + 1]])
    volumes = np.array([c["v"] for c in arr[max(0, i - 180): i + 1]])
    if (closes <= 0).any() or len(closes) < 180:
        return None
    c0 = closes[-1]
    ret_24h = (c0 / closes[-7] - 1) * 1e4 if closes[-7] > 0 else 0
    ret_7d = (c0 / closes[-43] - 1) * 1e4 if closes[-43] > 0 else 0
    high_30d = float(np.max(highs))
    drawdown = (c0 / high_30d - 1) * 1e4 if high_30d > 0 else 0
    vol_mean = float(np.mean(volumes[:-1]))
    vol_std = float(np.std(volumes[:-1])) if len(volumes) > 2 else 1
    vol_z = (volumes[-1] - vol_mean) / vol_std if vol_std > 0 else 0
    rets_all = np.diff(closes) / closes[:-1]
    vol_7d = float(np.std(rets_all[-42:]))
    vol_30d = float(np.std(rets_all))
    vol_ratio = vol_7d / vol_30d if vol_30d > 0 else 1.0
    c = arr[i]
    range_pct = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0
    return [ret_24h, ret_7d, drawdown, vol_z, vol_ratio, range_pct]


def forward_return_24h(sym: str, ts: int):
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i + 6 >= len(arr):
        return None
    c_now = arr[i]["c"]
    c_fwd = arr[i + 6]["c"]
    if c_now <= 0 or c_fwd <= 0:
        return None
    return (c_fwd / c_now - 1) * 1e4


# Collect all (ts, sym, features, forward_ret) tuples
print("Collecting (ts, sym, X, y) records...")
ts_set = set()
for cs in eligible.values():
    ts_set |= {c["t"] for c in cs}
all_ts = sorted(ts_set)

records = []  # (ts, X[F], y_bps)
start = time.time()
last_print = 0
for it_idx, ts in enumerate(all_ts):
    if time.time() - last_print > 5:
        progress = it_idx / len(all_ts)
        print(f"  [{it_idx:>5}/{len(all_ts)}] {progress*100:.0f}% records={len(records)}")
        last_print = time.time()
    for sym in eligible:
        f = compute_features(sym, ts)
        fwd = forward_return_24h(sym, ts)
        if f is None or fwd is None:
            continue
        records.append((ts, f, fwd))
print(f"\nTotal records: {len(records)}")

# Convert to arrays
ts_arr = np.array([r[0] for r in records])
X_arr = np.array([r[1] for r in records], dtype=float)
y_arr = np.array([r[2] for r in records], dtype=float)
y_sign = (y_arr > 0).astype(float) * 2 - 1  # +1 if positive, -1 negative

# Normalize features (z-score per column, fit on training only later)
print("\nSplitting train/test windows (4 sequential splits)...")
latest_ts = ts_arr.max()
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000
splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]

def auc(y_true, y_pred):
    """Compute AUC for binary classification (y_true ∈ {-1, +1}, y_pred continuous)."""
    pos = y_pred[y_true > 0]
    neg = y_pred[y_true < 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Brute force U-statistic
    n_pos = len(pos)
    n_neg = len(neg)
    if n_pos * n_neg > 5_000_000:
        # Sample
        idx_p = np.random.choice(len(pos), size=min(2000, len(pos)), replace=False)
        idx_n = np.random.choice(len(neg), size=min(2000, len(neg)), replace=False)
        pos = pos[idx_p]
        neg = neg[idx_n]
    return float(np.mean(pos[:, None] > neg[None, :]))


print(f"\n=== Training ESN per split (train on data before split, test on split) ===")
print(f"{'Split':<22} {'n_train':>9} {'n_test':>9} {'AUC':>6} {'verdict':>8}")
print("-" * 70)

per_split_auc = {}
for label, s_ts, e_ts in splits:
    train_mask = ts_arr < s_ts
    test_mask = (ts_arr >= s_ts) & (ts_arr < e_ts)
    if train_mask.sum() < 1000 or test_mask.sum() < 100:
        print(f"{label:<22}  insufficient data (train={train_mask.sum()} test={test_mask.sum()})")
        per_split_auc[label] = None
        continue

    X_train = X_arr[train_mask]
    y_train_sign = y_sign[train_mask]
    X_test = X_arr[test_mask]
    y_test_sign = y_sign[test_mask]

    # Normalize features (z-score on training)
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0)
    sd = np.where(sd == 0, 1, sd)
    X_train_n = (X_train - mu) / sd
    X_test_n = (X_test - mu) / sd

    # Subsample training for speed (ESN is O(N²) in time for states)
    n_train = min(30000, len(X_train_n))
    idx = np.random.RandomState(42).choice(len(X_train_n), size=n_train, replace=False)
    X_train_sub = X_train_n[idx]
    y_train_sub = y_train_sign[idx]

    # ESN: each row is a "timestep". reservoir applies recurrence.
    reservoir = Reservoir(units=200, sr=0.9, lr=0.3, seed=42)
    readout = Ridge(ridge=1e-3)
    states_train = reservoir.run(X_train_sub)
    readout.fit(states_train, y_train_sub.reshape(-1, 1))

    states_test = reservoir.run(X_test_n)
    y_pred = readout.run(states_test).flatten()

    score = auc(y_test_sign, y_pred)
    verdict = "PASS" if score > 0.55 else "FAIL"
    print(f"{label:<22} {n_train:>9} {test_mask.sum():>9} {score:>6.3f} {verdict:>8}")
    per_split_auc[label] = float(score)

# Strict verdict
all_pass = all(v is not None and v > 0.55 for v in per_split_auc.values())
print(f"\n=== STRICT 4/4 VERDICT ===")
print(f"  Criterion: AUC > 0.55 on all 4 splits")
print(f"  Result: {'STRICT 4/4 PASS' if all_pass else 'FAIL'}")

with open("/home/crypto/backtests/output/eda_esn_results.json", "w") as f:
    json.dump({
        "n_records": len(records),
        "per_split_auc": per_split_auc,
        "verdict": "PASS" if all_pass else "FAIL",
    }, f, indent=2)
print(f"\nResults saved to backtests/output/eda_esn_results.json")
