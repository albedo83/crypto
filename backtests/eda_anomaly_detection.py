"""EDA #4 — Anomaly detection cross-sectional multivariée.

Approche: à chaque timestamp t, compute features pour les ~35 tokens, run
IsolationForest sur cette matrice cross-sectionnelle pour identifier les
tokens "outliers" dans l'espace features actuel.

Test: les tokens outliers ont-ils des forward returns différents ?
- Forward horizons: 4h, 24h, 72h
- Conditionné par régime (btc_z bucket bear/neutral/bull)
- Conditionné par direction (anomalie LONG-side vs SHORT-side)

Si edge → ajoute un classifier comme FILTRE additionnel sur signaux existants
(pas remplacement), e.g. "S5 LONG fire SI anomaly_score < seuil".

Math: IsolationForest is a tree-based anomaly detector. Each tree isolates
points by random splits; anomalies are isolated in fewer splits. Score in
[-1, +1], lower = more anomalous. Standard sklearn, no hyperparameter risk.
"""
import json
import time
import numpy as np
from sklearn.ensemble import IsolationForest
from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles
# btc_z computed inline below (no helper exported in backtest_rolling)

print("Loading 4h candles...")
data = load_3y_candles()
print(f"  {len(data)} tokens")

# Build aligned features per (ts, token).
# Use only tokens with >= 2160 candles (= 12m) to ensure analysis window.
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)}/{len(data)} eligible tokens")


def safe_log_ratio(num: float, den: float) -> float:
    if den > 0 and num > 0:
        return float(np.log(num / den))
    return 0.0


print("\nBuilding features per (ts, token)...")

# Pre-compute timestamp-indexed candle map per token
# Each token has its own 4h grid (mostly aligned but some gaps)
ts_set: set[int] = set()
for sym, cs in eligible.items():
    ts_set |= {c["t"] for c in cs}
all_ts = sorted(ts_set)
print(f"  Total distinct timestamps: {len(all_ts)}")

# For computational efficiency, build candles_by_idx per token
sym_to_arr = {}
sym_to_idx = {}
for sym, cs in eligible.items():
    arr = []
    idx = {}
    for i, c in enumerate(cs):
        arr.append(c)
        idx[c["t"]] = i
    sym_to_arr[sym] = arr
    sym_to_idx[sym] = idx


def compute_features(sym: str, ts: int):
    """Compute features at timestamp ts for sym. Returns dict or None."""
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
    c0 = closes[-1]  # current close at t
    # ret_24h = 6 candles ago
    ret_24h = (c0 / closes[-7] - 1) * 1e4 if closes[-7] > 0 else 0
    # ret_7d = 42 candles ago
    ret_7d = (c0 / closes[-43] - 1) * 1e4 if closes[-43] > 0 else 0
    # drawdown vs 30d high
    high_30d = float(np.max(highs))
    drawdown = (c0 / high_30d - 1) * 1e4 if high_30d > 0 else 0
    # vol_z (volume z-score over 180 candles)
    vol_mean = float(np.mean(volumes[:-1])) if len(volumes) > 1 else 0
    vol_std = float(np.std(volumes[:-1])) if len(volumes) > 2 else 1
    vol_z = (volumes[-1] - vol_mean) / vol_std if vol_std > 0 else 0
    # vol_ratio = std(returns_7d) / std(returns_30d)
    rets_all = np.diff(closes) / closes[:-1]
    rets_7d = rets_all[-42:]
    rets_30d = rets_all
    vol_7d = float(np.std(rets_7d)) if len(rets_7d) > 1 else 0
    vol_30d = float(np.std(rets_30d)) if len(rets_30d) > 1 else 0
    vol_ratio = vol_7d / vol_30d if vol_30d > 0 else 1.0
    # range of current candle
    c = arr[i]
    range_pct = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0
    return {
        "ret_24h": ret_24h,
        "ret_7d": ret_7d,
        "drawdown": drawdown,
        "vol_z": vol_z,
        "vol_ratio": vol_ratio,
        "range_pct": range_pct,
        "close": c0,
    }


# Compute btc_z map for regime conditioning
print("\nComputing btc_z map...")
btc_arr = sym_to_arr.get("BTC")
btc_idx = sym_to_idx.get("BTC")
btc_z_map = {}
if btc_arr and btc_idx:
    btc_closes = np.array([c["c"] for c in btc_arr])
    for ts in all_ts:
        i = btc_idx.get(ts)
        if i is None or i < 210:
            continue
        # BTC ret_30d at every candle for past 180 days
        if i - 180 < 30:
            continue
        rets_30d = []
        for j in range(i - 180, i + 1):
            if j - 30 < 0 or btc_closes[j - 30] <= 0:
                continue
            r = btc_closes[j] / btc_closes[j - 30] - 1
            rets_30d.append(r)
        if len(rets_30d) < 30:
            continue
        ret_30d_now = rets_30d[-1]
        mean_30d = float(np.mean(rets_30d))
        std_30d = float(np.std(rets_30d))
        if std_30d > 0:
            btc_z_map[ts] = (ret_30d_now - mean_30d) / std_30d
print(f"  btc_z computed for {len(btc_z_map)} timestamps")


# Now iterate timestamps and build per-ts feature matrix
print("\nRunning IsolationForest per timestamp...")
print(f"  Iterating {len(all_ts)} candles...")
feat_cols = ["ret_24h", "ret_7d", "drawdown", "vol_z", "vol_ratio", "range_pct"]

# We'll accumulate (ts, sym, feat_dict, anomaly_score, fwd_4h, fwd_24h, fwd_72h, btc_z)
records = []
start = time.time()
last_print = 0
for it_idx, ts in enumerate(all_ts):
    if time.time() - last_print > 5:
        elapsed = time.time() - start
        progress = it_idx / len(all_ts)
        eta = elapsed * (1 - progress) / max(progress, 0.001)
        print(f"  [{it_idx:>5}/{len(all_ts)}] {progress*100:.0f}%, elapsed={elapsed:.0f}s, eta={eta:.0f}s, records={len(records)}")
        last_print = time.time()

    # Build feature matrix for this ts
    feats_at_t = {}
    for sym in eligible:
        f = compute_features(sym, ts)
        if f is not None:
            feats_at_t[sym] = f
    if len(feats_at_t) < 15:  # need enough tokens for cross-sectional anomaly
        continue

    syms_t = list(feats_at_t.keys())
    X = np.array([[feats_at_t[s][k] for k in feat_cols] for s in syms_t])
    # Standardize per timestamp to make cross-sectional anomaly more meaningful
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1
    X_norm = (X - X_mean) / X_std

    # IsolationForest with n_estimators small for speed
    try:
        clf = IsolationForest(n_estimators=50, contamination="auto", random_state=42, n_jobs=1)
        scores = clf.fit_predict(X_norm)  # +1 normal, -1 outlier
        decision = clf.decision_function(X_norm)  # higher = more normal
    except Exception:
        continue

    btc_z = btc_z_map.get(ts)

    # Compute forward returns for each token
    for j, sym in enumerate(syms_t):
        idx_map = sym_to_idx[sym]
        i_now = idx_map[ts]
        arr_sym = sym_to_arr[sym]
        c_now = arr_sym[i_now]["c"]
        if c_now <= 0:
            continue
        fwd_4h = None
        fwd_24h = None
        fwd_72h = None
        if i_now + 1 < len(arr_sym):
            c_next = arr_sym[i_now + 1]["c"]
            if c_next > 0:
                fwd_4h = (c_next / c_now - 1) * 1e4
        if i_now + 6 < len(arr_sym):
            c_24h = arr_sym[i_now + 6]["c"]
            if c_24h > 0:
                fwd_24h = (c_24h / c_now - 1) * 1e4
        if i_now + 18 < len(arr_sym):
            c_72h = arr_sym[i_now + 18]["c"]
            if c_72h > 0:
                fwd_72h = (c_72h / c_now - 1) * 1e4

        records.append({
            "ts": ts,
            "sym": sym,
            "anomaly_score": float(decision[j]),
            "is_outlier": int(scores[j] == -1),
            "fwd_4h": fwd_4h,
            "fwd_24h": fwd_24h,
            "fwd_72h": fwd_72h,
            "btc_z": btc_z,
            "ret_24h_at_t": float(feats_at_t[sym]["ret_24h"]),
        })

print(f"\nTotal records: {len(records)}")

# Analysis: quintile binning of anomaly score → forward return distribution
print("\n=== Anomaly score quintile analysis ===")
scores_all = np.array([r["anomaly_score"] for r in records])
quintiles = np.percentile(scores_all, [0, 20, 40, 60, 80, 100])
print(f"  Score quintiles: {[f'{q:.4f}' for q in quintiles]}")

for horizon in ("fwd_4h", "fwd_24h", "fwd_72h"):
    print(f"\n  Horizon {horizon}:")
    for q_idx in range(5):
        q_low = quintiles[q_idx]
        q_high = quintiles[q_idx + 1] if q_idx < 4 else float("inf")
        bucket = [r[horizon] for r in records
                  if r["anomaly_score"] >= q_low and r["anomaly_score"] < q_high
                  and r[horizon] is not None]
        if not bucket:
            continue
        arr = np.array(bucket)
        label = f"Q{q_idx + 1} ({'most outlier' if q_idx == 0 else 'most normal' if q_idx == 4 else 'mid'})"
        print(f"    {label:>30}: n={len(arr):>5}  mean={arr.mean():+7.1f} bps  "
              f"median={np.median(arr):+7.1f} bps  std={arr.std():.0f}  "
              f"P(>+200bps)={(arr > 200).mean()*100:.1f}%  "
              f"P(<-200bps)={(arr < -200).mean()*100:.1f}%")

# Regime-conditioned analysis (only for 24h horizon as it's most balanced)
print("\n=== Regime-conditioned (forward 24h, anomaly quintile Q1 = most outlier) ===")
for regime_name, regime_filter in [
    ("bear (btc_z < -0.5)", lambda r: r["btc_z"] is not None and r["btc_z"] < -0.5),
    ("neutral (|btc_z|≤0.5)", lambda r: r["btc_z"] is not None and abs(r["btc_z"]) <= 0.5),
    ("bull (btc_z > 0.5)", lambda r: r["btc_z"] is not None and r["btc_z"] > 0.5),
]:
    regime_records = [r for r in records if regime_filter(r)]
    if not regime_records:
        continue
    s_arr = np.array([r["anomaly_score"] for r in regime_records])
    q1_threshold = np.percentile(s_arr, 20)
    outliers = [r["fwd_24h"] for r in regime_records
                if r["anomaly_score"] <= q1_threshold and r["fwd_24h"] is not None]
    normals = [r["fwd_24h"] for r in regime_records
               if r["anomaly_score"] > q1_threshold and r["fwd_24h"] is not None]
    if outliers and normals:
        out_arr = np.array(outliers)
        nor_arr = np.array(normals)
        # t-test for difference of means
        diff = out_arr.mean() - nor_arr.mean()
        se = np.sqrt(out_arr.var() / len(out_arr) + nor_arr.var() / len(normals))
        t = diff / se if se > 0 else 0
        print(f"  {regime_name:>26}: "
              f"outliers n={len(outliers):>4} μ={out_arr.mean():+6.1f}  "
              f"normals n={len(normals):>5} μ={nor_arr.mean():+6.1f}  "
              f"Δ={diff:+5.1f}  t={t:+5.2f}")

# Direction conditional: filter outliers with positive vs negative ret_24h
print("\n=== Direction-conditional (Q1 outliers split by ret_24h sign) ===")
out_records = [r for r in records if r["anomaly_score"] <= np.percentile(scores_all, 20)]
print(f"  Total Q1 outliers: {len(out_records)}")
for sign_name, sign_filter in [
    ("ret_24h > +1000 (pumping)", lambda r: r["ret_24h_at_t"] > 1000),
    ("|ret_24h| <= 500 (calm)", lambda r: abs(r["ret_24h_at_t"]) <= 500),
    ("ret_24h < -1000 (dumping)", lambda r: r["ret_24h_at_t"] < -1000),
]:
    bucket = [r["fwd_24h"] for r in out_records if sign_filter(r) and r["fwd_24h"] is not None]
    if bucket:
        b = np.array(bucket)
        print(f"    {sign_name:>26}: n={len(b):>4}  μ={b.mean():+6.1f}  median={np.median(b):+6.1f}")

# Save
with open("/home/crypto/backtests/output/eda_anomaly_results.json", "w") as f:
    json.dump({
        "n_records": len(records),
        "n_outliers": int(sum(1 for r in records if r["is_outlier"])),
        "quintile_thresholds": [float(q) for q in quintiles],
        "ts_range": [int(min(r["ts"] for r in records)), int(max(r["ts"] for r in records))],
    }, f, indent=2)

print(f"\nResults saved to backtests/output/eda_anomaly_results.json")

# Verdict
print("\n=== VERDICT ===")
# Compute aggregate edge across regimes for outlier quintile
out_24h = np.array([r["fwd_24h"] for r in records
                    if r["anomaly_score"] <= np.percentile(scores_all, 20)
                    and r["fwd_24h"] is not None])
nor_24h = np.array([r["fwd_24h"] for r in records
                    if r["anomaly_score"] > np.percentile(scores_all, 20)
                    and r["fwd_24h"] is not None])
diff_24h = out_24h.mean() - nor_24h.mean()
se_24h = np.sqrt(out_24h.var() / len(out_24h) + nor_24h.var() / len(nor_24h))
t_24h = diff_24h / se_24h if se_24h > 0 else 0
print(f"  Forward 24h: outliers μ={out_24h.mean():+.1f}  normals μ={nor_24h.mean():+.1f}  "
      f"Δ={diff_24h:+.1f}  t={t_24h:+.2f}")
print(f"  Edge strength: {'STRONG' if abs(t_24h) > 5 else 'MODEST' if abs(t_24h) > 2 else 'NONE'}")
print(f"  Cost floor: 50 bps gross/trade needed. Edge {abs(diff_24h):.0f} bps {'>=' if abs(diff_24h) >= 50 else '<'} 50 bps")
