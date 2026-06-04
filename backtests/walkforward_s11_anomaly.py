"""Walk-forward Phase 1 — S11 anomaly signal predictivity.

S11 trigger conditions:
    1. IsolationForest score in Q1 (most anomalous, computed cross-sectional per ts)
    2. btc_z < -0.5 (bear regime)
    3. ret_24h < -1000 bps (dumping > 10% in 24h)
    4. NOT triggered by S8 (orthogonal: ¬(DD<-40% AND vol_z>1 AND ret_24h<-0.5% AND btc_7d<-3%))

Trade simulation:
    - LONG entry at next candle open
    - Exit at +24h (6 candles)
    - Cost: 26 bps RT (HL fees 9 bps + slippage)

4 splits 6m non-overlapping anchored on latest data:
    split_1: T-24m → T-18m
    split_2: T-18m → T-12m
    split_3: T-12m → T-6m
    split_4: T-6m → T

Strict criterion PASS:
    - net mean PnL > 0 on all 4 splits (after 26 bps cost)
    - WR > 50% on all 4 splits

If both criteria met → proceed to Phase 2 (full BT integration).
"""
import json
import time
import numpy as np
from datetime import datetime, timezone
from sklearn.ensemble import IsolationForest

from backtests.backtest_genetic import load_3y_candles

# Thresholds matching EDA #4b
ANOMALY_QUINTILE = 0.20
BTC_Z_BEAR = -0.5
DUMP_THRESHOLD = -1000
S8_DRAWDOWN_THRESH = -4000
S8_VOL_Z_MIN = 1.0
S8_RET_24H_THRESH = -50
S8_BTC_7D_THRESH = -300

COST_BPS_RT = 26  # 9 bps × 2 fees + 8 bps slippage
HOLD_CANDLES = 6  # 24h on 4h grid

print("Loading 4h candles...")
data = load_3y_candles()
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)} eligible tokens")

sym_to_arr = {sym: cs for sym, cs in eligible.items()}
sym_to_idx = {sym: {c["t"]: i for i, c in enumerate(cs)} for sym, cs in eligible.items()}
btc_arr = sym_to_arr.get("BTC")
btc_idx = sym_to_idx.get("BTC")
btc_closes = np.array([c["c"] for c in btc_arr]) if btc_arr else None

feat_cols = ["ret_24h", "ret_7d", "drawdown", "vol_z", "vol_ratio", "range_pct"]


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
        "vol_z": vol_z, "vol_ratio": vol_ratio, "range_pct": range_pct, "close": c0,
    }


def compute_btc_z(ts: int):
    if btc_idx is None:
        return None
    i = btc_idx.get(ts)
    if i is None or i < 210:
        return None
    if btc_closes[i - 180] <= 0:
        return None
    rets_30d = []
    for j in range(i - 180, i + 1):
        if j - 30 < 0 or btc_closes[j - 30] <= 0:
            continue
        rets_30d.append(btc_closes[j] / btc_closes[j - 30] - 1)
    if len(rets_30d) < 30:
        return None
    mean_30d = float(np.mean(rets_30d))
    std_30d = float(np.std(rets_30d))
    if std_30d == 0:
        return None
    return (rets_30d[-1] - mean_30d) / std_30d


def compute_btc_7d(ts: int):
    if btc_idx is None:
        return None
    i = btc_idx.get(ts)
    if i is None or i < 42 or btc_closes[i - 42] <= 0:
        return None
    return (btc_closes[i] / btc_closes[i - 42] - 1) * 1e4


# Build timestamps + sort by split
ts_set: set[int] = set()
for cs in eligible.values():
    ts_set |= {c["t"] for c in cs}
all_ts = sorted(ts_set)
latest_ts = all_ts[-1]

# Split boundaries (6m each = 1080 candles of 4h, latest 4 splits)
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000
splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]

# Pass 1: estimate Q1 anomaly threshold from sample
print("\nEstimating Q1 anomaly threshold (sample 500 timestamps)...")
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
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1
    X_norm = (X - X.mean(axis=0)) / X_std
    try:
        clf = IsolationForest(n_estimators=50, contamination="auto", random_state=42, n_jobs=1)
        clf.fit(X_norm)
        sample_scores.extend(clf.decision_function(X_norm).tolist())
    except Exception:
        continue
q1_threshold = float(np.percentile(sample_scores, 20))
print(f"  Q1 threshold: {q1_threshold:.4f}")

# Pass 2: scan all timestamps once, classify, save trades by split
print("\nScanning all timestamps for S11 triggers...")
trades_by_split = {label: [] for label, _, _ in splits}
start = time.time()
last_print = 0

for it_idx, ts in enumerate(all_ts):
    if time.time() - last_print > 5:
        elapsed = time.time() - start
        progress = it_idx / len(all_ts)
        total_trades = sum(len(t) for t in trades_by_split.values())
        print(f"  [{it_idx:>5}/{len(all_ts)}] {progress*100:.0f}%, "
              f"elapsed={elapsed:.0f}s, trades_collected={total_trades}")
        last_print = time.time()

    btc_z = compute_btc_z(ts)
    if btc_z is None or btc_z >= BTC_Z_BEAR:
        continue
    btc_7d = compute_btc_7d(ts)
    if btc_7d is None:
        continue

    feats_at_t = {}
    for sym in eligible:
        f = compute_features(sym, ts)
        if f is not None:
            feats_at_t[sym] = f
    if len(feats_at_t) < 15:
        continue

    syms_t = list(feats_at_t.keys())
    X = np.array([[feats_at_t[s][k] for k in feat_cols] for s in syms_t])
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1
    X_norm = (X - X.mean(axis=0)) / X_std
    try:
        clf = IsolationForest(n_estimators=50, contamination="auto", random_state=42, n_jobs=1)
        clf.fit(X_norm)
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
        if not (is_outlier_q1 and is_dumping and not s8_triggered):
            continue
        # S11 trigger fires — simulate LONG entry at next candle open, exit at +24h
        i_now = sym_to_idx[sym][ts]
        arr_sym = sym_to_arr[sym]
        if i_now + 1 >= len(arr_sym) or i_now + 1 + HOLD_CANDLES >= len(arr_sym):
            continue
        entry_px = arr_sym[i_now + 1]["o"]
        exit_px = arr_sym[i_now + 1 + HOLD_CANDLES - 1]["c"]
        if entry_px <= 0 or exit_px <= 0:
            continue
        gross_bps = (exit_px / entry_px - 1) * 1e4
        net_bps = gross_bps - COST_BPS_RT
        trade = {
            "ts": ts, "sym": sym, "btc_z": btc_z, "ret_24h": f["ret_24h"],
            "drawdown": f["drawdown"], "vol_z": f["vol_z"], "btc_7d": btc_7d,
            "anomaly_score": float(decision[j]),
            "gross_bps": gross_bps, "net_bps": net_bps,
        }
        # Assign to split
        for label, s_ts, e_ts in splits:
            if s_ts <= ts < e_ts:
                trades_by_split[label].append(trade)
                break

# Print per-split results
print("\n=== PER-SPLIT RESULTS ===")
print(f"{'Split':<22} {'n':>5} {'gross μ':>9} {'net μ':>8} {'net med':>9} {'WR':>6} {'std':>6}")
print("-" * 80)
verdict_lines = []
all_pass = True
for label, _, _ in splits:
    trades = trades_by_split[label]
    if not trades:
        print(f"{label:<22} {0:>5} (no trades)")
        verdict_lines.append((label, False, 0, 0, 0))
        all_pass = False
        continue
    nets = np.array([t["net_bps"] for t in trades])
    gross = np.array([t["gross_bps"] for t in trades])
    wr = float((nets > 0).mean() * 100)
    net_mu = float(nets.mean())
    net_med = float(np.median(nets))
    gross_mu = float(gross.mean())
    std_n = float(nets.std())
    pass_split = (net_mu > 0 and wr > 50)
    flag = "✓ PASS" if pass_split else "✗ FAIL"
    print(f"{label:<22} {len(trades):>5} {gross_mu:>+9.1f} {net_mu:>+8.1f} {net_med:>+9.1f} "
          f"{wr:>5.1f}% {std_n:>6.0f}  {flag}")
    if not pass_split:
        all_pass = False
    verdict_lines.append((label, pass_split, len(trades), net_mu, wr))

# Strict verdict
print("\n=== STRICT 4/4 VERDICT ===")
print(f"  Criterion: net mean > 0 AND WR > 50% on all 4 splits")
print(f"  Result: {'STRICT 4/4 PASS' if all_pass else 'FAIL'}")

if all_pass:
    print(f"\n  → Phase 2 recommended: full BT integration walk-forward")
else:
    print(f"\n  → Edge does not generalize across splits — stop here")

# Aggregate stats
all_trades = [t for ts in trades_by_split.values() for t in ts]
if all_trades:
    all_nets = np.array([t["net_bps"] for t in all_trades])
    print(f"\n=== AGGREGATE (24m) ===")
    print(f"  Total trades: {len(all_trades)}")
    print(f"  Net mean: {all_nets.mean():+.1f} bps  median: {np.median(all_nets):+.1f}")
    print(f"  WR: {(all_nets > 0).mean() * 100:.1f}%")
    print(f"  Total net bps: {all_nets.sum():+.0f}")
    print(f"  Annual: ~{len(all_trades)/2:.0f} trades/year")

# Save
with open("/home/crypto/backtests/output/walkforward_s11_results.json", "w") as f:
    json.dump({
        "thresholds": {
            "q1_anomaly": q1_threshold,
            "btc_z_bear": BTC_Z_BEAR,
            "dump": DUMP_THRESHOLD,
            "hold_candles": HOLD_CANDLES,
            "cost_rt": COST_BPS_RT,
        },
        "per_split": {label: {"n": len(t), "net_mu": float(np.mean([x["net_bps"] for x in t])) if t else 0,
                              "wr": float((np.array([x["net_bps"] for x in t]) > 0).mean() * 100) if t else 0}
                      for label, t in trades_by_split.items()},
        "verdict": "PASS" if all_pass else "FAIL",
        "n_total": len(all_trades),
    }, f, indent=2)

print(f"\nResults saved to backtests/output/walkforward_s11_results.json")
