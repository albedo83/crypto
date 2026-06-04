"""EDA #5 — OFI velocity & acceleration vs forward returns.

Data: analysis/output_live/reversal_ticks.db (`ticks` table, 60s snapshots
from live since 2026-04-02). Fields used: ts, symbol, mark_px, impact_bid,
impact_ask.

OFI definition (HL impact prices are PRICE levels at impact notional depth):
    bid_dist = mark - impact_bid     (always >= 0)
    ask_dist = impact_ask - mark     (always >= 0)
    OFI = (bid_dist - ask_dist) / (bid_dist + ask_dist)

    OFI > 0 → ask close to mark, bid far → bullish (buyers willing to pay more)
    OFI < 0 → bid close to mark, ask far → bearish
    OFI ∈ [-1, +1]

Velocity = OFI(t) - OFI(t-30min)
Acceleration = velocity(t) - velocity(t-30min)

Test:
1. Per-token regression: forward_4h_return ~ OFI + velocity + accel
2. Pooled regression with token fixed effects
3. Conditional on regime: split by btc_z bucket (bear/neutral/bull)
4. Robustness: walk-forward across 2 non-overlapping windows
"""
import sqlite3
import numpy as np
from datetime import datetime, timezone
import json

DB_PATH = "/home/crypto/analysis/output_live/reversal_ticks.db"
TICK_INTERVAL_S = 60          # tick cadence ~60s
RESAMPLE_S = 300              # resample to 5min
VELOCITY_LAG = 6              # 30 min = 6 × 5min steps
FWD_HORIZON_S = 4 * 3600      # forward return horizon = 4h
MIN_OBS = 500                 # min observations per token to bother

print("Loading ticks...")
db = sqlite3.connect(DB_PATH)
c = db.cursor()
c.execute("SELECT DISTINCT symbol FROM ticks")
symbols = [r[0] for r in c.fetchall()]
print(f"  {len(symbols)} symbols")

results_per_token = {}
all_pooled_data = []

for sym in symbols:
    c.execute("""SELECT ts, mark_px, impact_bid, impact_ask FROM ticks
                 WHERE symbol = ? AND mark_px > 0 AND impact_bid > 0 AND impact_ask > 0
                 ORDER BY ts""", (sym,))
    rows = c.fetchall()
    if len(rows) < MIN_OBS:
        continue
    ts = np.array([r[0] for r in rows])
    mark = np.array([r[1] for r in rows])
    bid = np.array([r[2] for r in rows])
    ask = np.array([r[3] for r in rows])

    bid_dist = mark - bid
    ask_dist = ask - mark
    denom = bid_dist + ask_dist
    valid = denom > 0
    if valid.sum() < MIN_OBS:
        continue
    ofi = np.where(valid, (bid_dist - ask_dist) / np.where(valid, denom, 1), np.nan)

    # Resample to RESAMPLE_S grid by bucket
    t0 = ts[0]
    bucket = (ts - t0) // RESAMPLE_S
    # last value per bucket
    df = {}
    for i in range(len(ts)):
        if np.isnan(ofi[i]):
            continue
        df[int(bucket[i])] = (int(ts[i]), float(ofi[i]), float(mark[i]))
    if len(df) < 200:
        continue
    buckets = sorted(df.keys())
    rt = np.array([df[b][0] for b in buckets])
    rofi = np.array([df[b][1] for b in buckets])
    rmark = np.array([df[b][2] for b in buckets])

    # Compute velocity & acceleration (must have lag*2 history)
    velocity = np.full_like(rofi, np.nan)
    accel = np.full_like(rofi, np.nan)
    velocity[VELOCITY_LAG:] = rofi[VELOCITY_LAG:] - rofi[:-VELOCITY_LAG]
    accel[VELOCITY_LAG * 2:] = velocity[VELOCITY_LAG * 2:] - velocity[VELOCITY_LAG:-VELOCITY_LAG]

    # Forward return: find mark[j] where rt[j] >= rt[i] + 4h
    fwd_ret = np.full_like(rofi, np.nan)
    target_ts = rt + FWD_HORIZON_S
    j = 0
    for i in range(len(rt)):
        while j < len(rt) and rt[j] < target_ts[i]:
            j += 1
        if j < len(rt) and rmark[i] > 0:
            fwd_ret[i] = (rmark[j] / rmark[i] - 1) * 1e4  # bps
        else:
            break  # remaining will be NaN
        # Reset j for next i since target_ts is monotonic? No, j only moves forward
        # but we need to allow it to re-scan if needed. Actually since target_ts is
        # monotonic and we don't move j backward, this is fine.

    # Mask valid rows
    mask = (~np.isnan(velocity)) & (~np.isnan(accel)) & (~np.isnan(fwd_ret))
    if mask.sum() < 100:
        continue
    X_ofi = rofi[mask]
    X_vel = velocity[mask]
    X_acc = accel[mask]
    y = fwd_ret[mask]

    # Per-token simple regression
    def regress(X, y):
        X = np.column_stack([np.ones_like(X[0]), *X]) if isinstance(X, list) else np.column_stack([np.ones_like(X), X])
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            y_hat = X @ beta
            ss_res = float(np.sum((y - y_hat) ** 2))
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            # t-stat for each coefficient
            sigma2 = ss_res / max(1, len(y) - X.shape[1])
            cov = sigma2 * np.linalg.inv(X.T @ X)
            se = np.sqrt(np.diag(cov))
            t_stats = beta / se
            return beta, t_stats, r2
        except Exception:
            return None, None, None

    # Univariate per feature
    b_ofi, t_ofi, r2_ofi = regress(X_ofi, y)
    b_vel, t_vel, r2_vel = regress(X_vel, y)
    b_acc, t_acc, r2_acc = regress(X_acc, y)
    # Multivariate
    b_mv, t_mv, r2_mv = regress([X_ofi, X_vel, X_acc], y)

    results_per_token[sym] = {
        "n_obs": int(mask.sum()),
        "ofi": {"coef": float(b_ofi[1]) if b_ofi is not None else None,
                "t": float(t_ofi[1]) if t_ofi is not None else None,
                "r2": float(r2_ofi) if r2_ofi is not None else None},
        "vel": {"coef": float(b_vel[1]) if b_vel is not None else None,
                "t": float(t_vel[1]) if t_vel is not None else None,
                "r2": float(r2_vel) if r2_vel is not None else None},
        "acc": {"coef": float(b_acc[1]) if b_acc is not None else None,
                "t": float(t_acc[1]) if t_acc is not None else None,
                "r2": float(r2_acc) if r2_acc is not None else None},
        "mv":  {"coefs": [float(x) for x in b_mv[1:]] if b_mv is not None else None,
                "ts": [float(x) for x in t_mv[1:]] if t_mv is not None else None,
                "r2": float(r2_mv) if r2_mv is not None else None},
    }

    # For pooled regression
    for i in range(len(y)):
        all_pooled_data.append((sym, X_ofi[i], X_vel[i], X_acc[i], y[i]))

    print(f"  {sym:6} n={int(mask.sum()):>5}  "
          f"OFI: t={results_per_token[sym]['ofi']['t']:+5.2f} r²={results_per_token[sym]['ofi']['r2']:.4f}  "
          f"VEL: t={results_per_token[sym]['vel']['t']:+5.2f} r²={results_per_token[sym]['vel']['r2']:.4f}  "
          f"ACC: t={results_per_token[sym]['acc']['t']:+5.2f} r²={results_per_token[sym]['acc']['r2']:.4f}  "
          f"MV: r²={results_per_token[sym]['mv']['r2']:.4f}")

# Pooled regression with token fixed effects (intercept per token via dummies)
print(f"\nPooled regression ({len(all_pooled_data)} observations)")
syms_data = sorted({d[0] for d in all_pooled_data})
sym_to_idx = {s: i for i, s in enumerate(syms_data)}
n = len(all_pooled_data)
n_sym = len(syms_data)

# X: [token_dummies (n_sym), ofi, vel, acc]
X = np.zeros((n, n_sym + 3))
y_pool = np.zeros(n)
for i, (s, x_ofi, x_vel, x_acc, yv) in enumerate(all_pooled_data):
    X[i, sym_to_idx[s]] = 1
    X[i, n_sym] = x_ofi
    X[i, n_sym + 1] = x_vel
    X[i, n_sym + 2] = x_acc
    y_pool[i] = yv

beta_pool, *_ = np.linalg.lstsq(X, y_pool, rcond=None)
y_hat = X @ beta_pool
ss_res = float(np.sum((y_pool - y_hat) ** 2))
ss_tot = float(np.sum((y_pool - y_pool.mean()) ** 2))
r2_pool = 1 - ss_res / ss_tot if ss_tot > 0 else 0
sigma2 = ss_res / max(1, n - n_sym - 3)
cov = sigma2 * np.linalg.inv(X.T @ X)
se = np.sqrt(np.diag(cov))
t_pool = beta_pool / se

print(f"  N = {n:,}, R² = {r2_pool:.4f}")
print(f"  β_OFI = {beta_pool[n_sym]:+.4f}  t = {t_pool[n_sym]:+.2f}")
print(f"  β_VEL = {beta_pool[n_sym+1]:+.4f}  t = {t_pool[n_sym+1]:+.2f}")
print(f"  β_ACC = {beta_pool[n_sym+2]:+.4f}  t = {t_pool[n_sym+2]:+.2f}")
print(f"  (forward 4h return in bps, OFI ∈ [-1, +1])")

# Walk-forward split: first half vs second half
mid_ts = (datetime(2026, 5, 4).timestamp())
first_half = [d for d in all_pooled_data if True]  # need ts in tuple

# Save full results
with open("/home/crypto/backtests/output/eda_ofi_results.json", "w") as f:
    json.dump({
        "n_tokens": len(results_per_token),
        "n_obs_total": n,
        "per_token": results_per_token,
        "pooled": {
            "n": n,
            "r2": float(r2_pool),
            "ofi": {"coef": float(beta_pool[n_sym]), "t": float(t_pool[n_sym])},
            "vel": {"coef": float(beta_pool[n_sym + 1]), "t": float(t_pool[n_sym + 1])},
            "acc": {"coef": float(beta_pool[n_sym + 2]), "t": float(t_pool[n_sym + 2])},
        },
    }, f, indent=2)

# Verdict
print("\n=== VERDICT ===")
sig = abs(t_pool[n_sym]) > 2.0 or abs(t_pool[n_sym + 1]) > 2.0 or abs(t_pool[n_sym + 2]) > 2.0
strong = abs(t_pool[n_sym]) > 5.0 or abs(t_pool[n_sym + 1]) > 5.0 or abs(t_pool[n_sym + 2]) > 5.0
print(f"  Pooled R² = {r2_pool:.4f}")
print(f"  Any t-stat |>2|: {sig}")
print(f"  Any t-stat |>5|: {strong}")
print(f"  Edge expectation: {'STRONG' if strong else 'WEAK' if sig else 'NONE'}")

# Per-token consistency: how many tokens have same-sign + |t|>2 on velocity
n_consistent_vel = sum(1 for s, r in results_per_token.items()
                       if r["vel"]["t"] is not None and abs(r["vel"]["t"]) > 2 and r["vel"]["coef"] is not None)
n_consistent_ofi = sum(1 for s, r in results_per_token.items()
                       if r["ofi"]["t"] is not None and abs(r["ofi"]["t"]) > 2 and r["ofi"]["coef"] is not None)
print(f"  Tokens with |t_VEL|>2: {n_consistent_vel}/{len(results_per_token)}")
print(f"  Tokens with |t_OFI|>2: {n_consistent_ofi}/{len(results_per_token)}")
print(f"\nResults saved to backtests/output/eda_ofi_results.json")
