"""EDA #4b — Anomaly outliers vs S8 triggers overlap analysis.

Question: les outliers Q1 × bear × dumping ré-découvrent-ils S8 (μ +158 bps
fwd 24h serait juste S8 sous un autre nom), ou ajoutent-ils une couche
orthogonale ?

S8 conditions (config.py):
    drawdown < -4000 bps (-40% from 30d high)
    vol_z > 1.0
    ret_24h < -50 bps (-0.5%)
    btc_7d < -300 bps (-3% over 7d)

On compare:
A) Anomaly Q1 × bear × ret_24h<-1000 ∩ S8 triggered  → "redundant"
B) Anomaly Q1 × bear × ret_24h<-1000 ∩ ¬S8 triggered → "orthogonal"

Mesurer forward 24h pour chaque groupe + comparer.

Si μ(B) > 0 → anomaly capture quelque chose au-delà de S8.
Si μ(B) ~ 0 → recouvrement quasi total, jeter.
"""
import json
import time
import numpy as np
from sklearn.ensemble import IsolationForest

from backtests.backtest_genetic import load_3y_candles

# S8 thresholds from config.py
S8_DRAWDOWN_THRESH = -4000
S8_VOL_Z_MIN = 1.0
S8_RET_24H_THRESH = -50
S8_BTC_7D_THRESH = -300

# Anomaly conditions
ANOMALY_QUINTILE = 0.20  # Q1
BTC_Z_BEAR = -0.5
DUMP_THRESHOLD = -1000  # ret_24h < -10%

print("Loading 4h candles...")
data = load_3y_candles()
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)} eligible tokens")

# Build sym_to_arr and sym_to_idx
sym_to_arr = {}
sym_to_idx = {}
for sym, cs in eligible.items():
    sym_to_arr[sym] = cs
    sym_to_idx[sym] = {c["t"]: i for i, c in enumerate(cs)}

# Pre-compute btc series
btc_arr = sym_to_arr.get("BTC")
btc_idx = sym_to_idx.get("BTC")
btc_closes = np.array([c["c"] for c in btc_arr]) if btc_arr else None


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
    return {
        "ret_24h": ret_24h, "ret_7d": ret_7d, "drawdown": drawdown,
        "vol_z": vol_z, "vol_ratio": vol_ratio, "range_pct": range_pct,
        "close": c0,
    }


def compute_btc_features(ts: int):
    """Return (btc_30d, btc_7d, btc_z) at ts. None if insufficient history."""
    if btc_idx is None:
        return None
    i = btc_idx.get(ts)
    if i is None or i < 210:
        return None
    if btc_closes[i - 180] <= 0 or btc_closes[i - 42] <= 0:
        return None
    btc_30d = (btc_closes[i] / btc_closes[i - 180] - 1) * 1e4
    btc_7d = (btc_closes[i] / btc_closes[i - 42] - 1) * 1e4
    # btc_z = z-score of ret_30d over 180d window
    rets_30d = []
    for j in range(i - 180, i + 1):
        if j - 30 < 0 or btc_closes[j - 30] <= 0:
            continue
        r = btc_closes[j] / btc_closes[j - 30] - 1
        rets_30d.append(r)
    if len(rets_30d) < 30:
        return None
    mean_30d = float(np.mean(rets_30d))
    std_30d = float(np.std(rets_30d))
    if std_30d == 0:
        return None
    btc_z = (rets_30d[-1] - mean_30d) / std_30d
    return {"btc_30d": btc_30d, "btc_7d": btc_7d, "btc_z": btc_z}


# Iterate all timestamps
ts_set: set[int] = set()
for cs in eligible.values():
    ts_set |= {c["t"] for c in cs}
all_ts = sorted(ts_set)
print(f"  Total timestamps: {len(all_ts)}")

feat_cols = ["ret_24h", "ret_7d", "drawdown", "vol_z", "vol_ratio", "range_pct"]

# First pass: collect ALL anomaly scores so we can compute global Q1 threshold
# To avoid two-pass, we estimate Q1 threshold from a sample first.
print("\nPass 1: estimate global Q1 threshold from 500 random timestamps...")
rng = np.random.default_rng(seed=42)
sample_ts = rng.choice(all_ts, size=min(500, len(all_ts)), replace=False)
sample_scores = []
for ts in sample_ts:
    feats_at_t = {s: compute_features(s, ts) for s in eligible}
    feats_at_t = {s: f for s, f in feats_at_t.items() if f is not None}
    if len(feats_at_t) < 15:
        continue
    syms_t = list(feats_at_t.keys())
    X = np.array([[feats_at_t[s][k] for k in feat_cols] for s in syms_t])
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1
    X_norm = (X - X_mean) / X_std
    try:
        clf = IsolationForest(n_estimators=50, contamination="auto", random_state=42, n_jobs=1)
        clf.fit(X_norm)
        sample_scores.extend(clf.decision_function(X_norm).tolist())
    except Exception:
        continue
q1_threshold = float(np.percentile(sample_scores, 20))
print(f"  Q1 threshold estimated: {q1_threshold:.4f}")

# Pass 2: full scan, collect events that match the joint condition
print("\nPass 2: scan all timestamps + classify each event...")
records_redundant = []   # outlier Q1 × bear × dump × S8 triggered
records_orthogonal = []  # outlier Q1 × bear × dump × NOT S8
records_s8_only = []     # NOT outlier × S8 triggered (control: pure S8)

start = time.time()
last_print = 0
for it_idx, ts in enumerate(all_ts):
    if time.time() - last_print > 5:
        elapsed = time.time() - start
        progress = it_idx / len(all_ts)
        eta = elapsed * (1 - progress) / max(progress, 0.001)
        print(f"  [{it_idx:>5}/{len(all_ts)}] {progress*100:.0f}%, elapsed={elapsed:.0f}s, "
              f"eta={eta:.0f}s, redund={len(records_redundant)}, "
              f"orth={len(records_orthogonal)}, s8_only={len(records_s8_only)}")
        last_print = time.time()

    btc_f = compute_btc_features(ts)
    if btc_f is None:
        continue
    btc_z = btc_f["btc_z"]
    btc_7d = btc_f["btc_7d"]
    if btc_z >= BTC_Z_BEAR:
        continue  # not bear regime — skip entirely

    feats_at_t = {}
    for sym in eligible:
        f = compute_features(sym, ts)
        if f is not None:
            feats_at_t[sym] = f
    if len(feats_at_t) < 15:
        continue

    syms_t = list(feats_at_t.keys())
    X = np.array([[feats_at_t[s][k] for k in feat_cols] for s in syms_t])
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1
    X_norm = (X - X_mean) / X_std
    try:
        clf = IsolationForest(n_estimators=50, contamination="auto", random_state=42, n_jobs=1)
        scores = clf.fit(X_norm)
        decision = clf.decision_function(X_norm)
    except Exception:
        continue

    for j, sym in enumerate(syms_t):
        f = feats_at_t[sym]
        is_outlier_q1 = decision[j] <= q1_threshold
        is_dumping = f["ret_24h"] < DUMP_THRESHOLD
        s8_triggered = (
            f["drawdown"] < S8_DRAWDOWN_THRESH
            and f["vol_z"] > S8_VOL_Z_MIN
            and f["ret_24h"] < S8_RET_24H_THRESH
            and btc_7d < S8_BTC_7D_THRESH
        )

        # Compute forward 24h return
        idx_map = sym_to_idx[sym]
        i_now = idx_map[ts]
        arr_sym = sym_to_arr[sym]
        c_now = arr_sym[i_now]["c"]
        if i_now + 6 >= len(arr_sym):
            continue
        c_fwd = arr_sym[i_now + 6]["c"]
        if c_now <= 0 or c_fwd <= 0:
            continue
        fwd_24h = (c_fwd / c_now - 1) * 1e4

        if is_outlier_q1 and is_dumping and s8_triggered:
            records_redundant.append({"ts": ts, "sym": sym, "fwd_24h": fwd_24h,
                                       "anomaly_score": float(decision[j]),
                                       "drawdown": f["drawdown"], "ret_24h": f["ret_24h"],
                                       "vol_z": f["vol_z"], "btc_7d": btc_7d, "btc_z": btc_z})
        elif is_outlier_q1 and is_dumping and not s8_triggered:
            records_orthogonal.append({"ts": ts, "sym": sym, "fwd_24h": fwd_24h,
                                        "anomaly_score": float(decision[j]),
                                        "drawdown": f["drawdown"], "ret_24h": f["ret_24h"],
                                        "vol_z": f["vol_z"], "btc_7d": btc_7d, "btc_z": btc_z})
        elif not is_outlier_q1 and s8_triggered:
            records_s8_only.append({"ts": ts, "sym": sym, "fwd_24h": fwd_24h,
                                     "drawdown": f["drawdown"], "ret_24h": f["ret_24h"],
                                     "vol_z": f["vol_z"], "btc_7d": btc_7d, "btc_z": btc_z})

print(f"\n=== RESULTS (forward 24h returns) ===\n")

groups = [
    ("REDUNDANT (Q1×bear×dump ∩ S8)", records_redundant),
    ("ORTHOGONAL (Q1×bear×dump ∩ ¬S8)", records_orthogonal),
    ("S8-only (NOT outlier ∩ S8)", records_s8_only),
]
results_summary = {}
for label, recs in groups:
    if not recs:
        print(f"{label:<40}  n=0")
        continue
    arr = np.array([r["fwd_24h"] for r in recs])
    mean = float(arr.mean())
    median = float(np.median(arr))
    std = float(arr.std())
    wr_500 = float((arr > 500).mean() * 100)
    wr_0 = float((arr > 0).mean() * 100)
    after_cost = mean - 26  # 26 bps RT
    print(f"{label:<40}  n={len(arr):>5}  μ={mean:+7.1f}  median={median:+7.1f}  "
          f"std={std:.0f}  P(>0)={wr_0:.1f}%  P(>500bps)={wr_500:.1f}%  net(μ-26)={after_cost:+.1f}")
    results_summary[label] = {
        "n": int(len(arr)),
        "mean": mean,
        "median": median,
        "std": std,
        "wr_pct": wr_0,
        "wr_500_pct": wr_500,
        "net_after_cost": after_cost,
    }

# Save
with open("/home/crypto/backtests/output/eda_anomaly_vs_s8.json", "w") as f:
    json.dump({
        "thresholds": {
            "q1_anomaly": q1_threshold,
            "btc_z_bear": BTC_Z_BEAR,
            "dump": DUMP_THRESHOLD,
            "s8_dd": S8_DRAWDOWN_THRESH,
            "s8_vol_z": S8_VOL_Z_MIN,
            "s8_ret24h": S8_RET_24H_THRESH,
            "s8_btc_7d": S8_BTC_7D_THRESH,
        },
        "summary": results_summary,
        "n_redundant": len(records_redundant),
        "n_orthogonal": len(records_orthogonal),
        "n_s8_only": len(records_s8_only),
    }, f, indent=2)

print(f"\n=== VERDICT ===")
if records_orthogonal:
    orth_arr = np.array([r["fwd_24h"] for r in records_orthogonal])
    print(f"Orthogonal group (anomaly capture sans S8 trigger):")
    print(f"  n={len(orth_arr)} events")
    print(f"  mean fwd24h = {orth_arr.mean():+.1f} bps, net = {orth_arr.mean() - 26:+.1f} bps")
    print(f"  median fwd24h = {float(np.median(orth_arr)):+.1f} bps")
    if orth_arr.mean() - 26 > 0:
        print(f"  → ORTHOGONAL EDGE CONFIRMED (anomaly capture quelque chose au-delà de S8)")
    else:
        print(f"  → Mean positif mais sub-cost, marginal")
else:
    print(f"Aucun orthogonal event — anomaly = sous-ensemble de S8")
print(f"\nResults saved to backtests/output/eda_anomaly_vs_s8.json")
