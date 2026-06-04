"""D4 — Portfolio-level descriptive EDA.

For each timestamp in the backtest history, compute portfolio metrics:
- effective_n = (Σ |w_i|)² / Σ w_i² (Herfindahl-inverse, concentration)
- portfolio_corr = mean |corr(returns)| across held pairs (30d rolling)
- portfolio_beta_BTC = Σ w_i × β_i_BTC (rolling 30d beta)

Question: Are LOW effective_n / HIGH correlation periods coincident with subsequent DD?

This guides design of a "portfolio gate" — refuse entry if adding it would push
metrics into the bad zone.

Methodology: simulate baseline backtest, record portfolio metrics at each ts +
forward 24h portfolio PnL change. Correlate.
"""
import json
import numpy as np
import time
from itertools import combinations
from datetime import datetime, timezone

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

print("Loading data...")
data = load_3y_candles()
features = build_features(data)
sector_features = compute_sector_features(features, data)
dxy = load_dxy()
oi = load_oi()
funding = load_funding()
print(f"  {len(data)} tokens")

# Run baseline backtest on last 12m to get trades + basket_timeseries
latest_ts = max(c["t"] for c in data["BTC"])
TWELVE_M = 12 * 30 * 24 * 3600 * 1000
start_ts = latest_ts - TWELVE_M

early = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
runner = ({"strategies": RUNNER_EXT_STRATEGIES, "extra_candles": RUNNER_EXT_HOURS // 4,
           "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS, "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE}
          if RUNNER_EXT_STRATEGIES else None)

print(f"\nRunning baseline BT on last 12m...")
r = run_window(features, data, sector_features, dxy,
               start_ts, latest_ts, start_capital=1000.0,
               oi_data=oi, early_exit_params=early,
               runner_extension=runner, funding_data=funding,
               apply_adaptive_modulator=True,
               max_notional_per_trade=500.0, margin_check=True)

basket_ts = r.get("basket_timeseries", [])
print(f"  {r['n_trades']} trades, {len(basket_ts)} basket snapshots")
print(f"  End capital: ${r['end_capital']:.0f}, DD={r['max_dd_pct']:.2f}%")

# Analyze basket timeseries
print("\n=== Basket concentration analysis ===")
n_pos_arr = np.array([b["n_pos"] for b in basket_ts])
print(f"  Position count: median={int(np.median(n_pos_arr))} max={int(n_pos_arr.max())} "
      f"share with >=3 pos: {(n_pos_arr >= 3).mean() * 100:.1f}%")

effn_7d = np.array([b.get("eff_n_7d") for b in basket_ts])
effn_7d_v = effn_7d[~np.isnan(effn_7d.astype(float))] if effn_7d.dtype != float else effn_7d[~np.isnan(effn_7d)]
print(f"  Effective n (7d): median={float(np.median(effn_7d_v)):.2f}  "
      f"p10={float(np.percentile(effn_7d_v, 10)):.2f}  p90={float(np.percentile(effn_7d_v, 90)):.2f}")

# basket_unreal is already in there
unreal_arr = np.array([b.get("basket_unreal", 0) for b in basket_ts])
print(f"  Basket unrealized: min={unreal_arr.min():.1f} max={unreal_arr.max():.1f} std={unreal_arr.std():.1f}")

# Correlation analysis: does low effective_n precede unrealized drawdown?
print("\n=== Concentration vs forward unrealized drawdown ===")
# For each ts, forward window = next 6 snapshots (= 24h)
# Forward DD = unreal_drop = max(unreal[t]) - min(unreal[t..t+6])
# Then test: low effective_n correlates with high forward DD?

n = len(basket_ts)
fwd_dd = np.full(n, np.nan)
HORIZON = 12  # 4 candles? actually each basket snap is per scan, hourly
# Convert to bps DD as % of position basket size... easier: just measure unreal drop
for i in range(n):
    if i + HORIZON >= n:
        break
    cur = unreal_arr[i]
    forward = unreal_arr[i:i + HORIZON]
    fwd_dd[i] = max(0, cur - forward.min())

valid = ~np.isnan(fwd_dd) & ~np.isnan(effn_7d_v[:len(fwd_dd)]) if False else ~np.isnan(fwd_dd)
# Quartile of effective_n
effn_sorted = np.sort(effn_7d_v)
q1_thresh = effn_sorted[int(len(effn_sorted) * 0.25)]
q4_thresh = effn_sorted[int(len(effn_sorted) * 0.75)]

# Compare forward DD in Q1 (most concentrated) vs Q4 (most diversified) effective_n
# Need to align indices
if len(effn_7d_v) != len(fwd_dd):
    # Recompute on common index
    effn_for_corr = effn_7d.astype(float)
    valid_both = ~np.isnan(effn_for_corr) & ~np.isnan(fwd_dd)
    effn_used = effn_for_corr[valid_both]
    fwd_used = fwd_dd[valid_both]
else:
    valid_both = ~np.isnan(effn_7d_v) & ~np.isnan(fwd_dd)
    effn_used = effn_7d_v[valid_both]
    fwd_used = fwd_dd[valid_both]

q1_mask = effn_used <= q1_thresh
q4_mask = effn_used >= q4_thresh
print(f"  Concentrated (eff_n ≤ {q1_thresh:.2f}): n={q1_mask.sum()} forward 24h DD μ={fwd_used[q1_mask].mean():.1f}")
print(f"  Diversified  (eff_n ≥ {q4_thresh:.2f}): n={q4_mask.sum()} forward 24h DD μ={fwd_used[q4_mask].mean():.1f}")
delta = fwd_used[q1_mask].mean() - fwd_used[q4_mask].mean()
print(f"  Δ (concentrated - diversified): {delta:+.1f}")

if delta > 5:
    print(f"  → CONCENTRATION precedes DD → portfolio gate may help")
elif delta < -5:
    print(f"  → COUNTERINTUITIVE: concentration is associated with LESS DD")
else:
    print(f"  → No clear signal from concentration alone")

# Number of times in concentrated state
print(f"\nFrequency of low concentration:")
print(f"  eff_n < 2 (only 1 dominant position): {(effn_used < 2).mean() * 100:.1f}% of time")
print(f"  eff_n < 1.5: {(effn_used < 1.5).mean() * 100:.1f}% of time")
print(f"  eff_n < 1.2: {(effn_used < 1.2).mean() * 100:.1f}% of time")

# Save
with open("/home/crypto/backtests/output/eda_portfolio_descriptive.json", "w") as f:
    json.dump({
        "n_baseline_trades": int(r["n_trades"]),
        "baseline_pnl_pct": float(r["pnl_pct"]),
        "baseline_dd_pct": float(r["max_dd_pct"]),
        "effective_n_median": float(np.median(effn_used)) if len(effn_used) else None,
        "effective_n_p10": float(np.percentile(effn_used, 10)) if len(effn_used) else None,
        "concentrated_fwd_dd_mean": float(fwd_used[q1_mask].mean()) if q1_mask.sum() else None,
        "diversified_fwd_dd_mean": float(fwd_used[q4_mask].mean()) if q4_mask.sum() else None,
        "delta": float(delta),
    }, f, indent=2)

print(f"\nResults saved to backtests/output/eda_portfolio_descriptive.json")
