"""EXIT-B WF — vol expansion mid-trade trigger.

Hypothèse : si vol_30d augmente significativement pendant le hold AND la position
est en perte, c'est un signal de stress du marché → cut early.

Mécanique :
- À l'entrée, store vol_30d_at_entry (déjà calculé dans features)
- À chaque candle du hold, compute vol_30d_now
- Si ratio = vol_30d_now / vol_30d_at_entry >= threshold AND unrealized < -X → cut

Test variants :
    V1 : ratio ≥ 1.5x AND ur < 0
    V2 : ratio ≥ 2.0x AND ur < -100
    V3 : ratio ≥ 1.3x AND ur < -200
    V4 : combiné avec régime bear seulement (split_3 protection)

Implementation : monkey-patch run_window via size_fn? Non, on a besoin de logique
mid-trade. Modifier backtest_rolling pour accepter vol_expansion_cut param.
"""
import json
from datetime import datetime, timezone

# Add vol_expansion_cut param to backtest engine
import backtests.backtest_rolling as br
print("Patching backtest_rolling for vol_expansion_cut...")

# Inject logic via a wrapper around run_window. Use existing infra:
# - feat_by_ts has vol_30d for every (ts, sym)
# - position has entry stored
# We need: store vol_30d at entry in pos dict + check during hold
#
# Simplest path: monkey-patch by adding handlers. But cleanest is to add
# a vol_expansion_cut param to run_window. Since we modified backtest_rolling
# earlier for early_dead_check, do same.

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

# Implementation strategy: use early_dead_check infrastructure but evaluate
# vol expansion via features access. Since BT already has feat_by_ts, we can
# implement vol_expansion_cut as a callable hook. Actually the cleanest is to
# replicate the early_dead_check pattern with vol-ratio param.

# For minimum effort, we'll use the inlife_exit_extra callback that already exists
# in run_window signature (accepts callable -> (should_exit, reason)).

# Pre-compute vol_30d at every (ts, sym) from features
print("Loading data...")
data = load_3y_candles()
features = build_features(data)
sector_features = compute_sector_features(features, data)
dxy = load_dxy()
oi = load_oi()
funding = load_funding()

# vol_30d already in features
def make_vol_expansion_hook(ratio_thresh: float, ur_thresh_bps: float, bear_only: bool):
    """Return an inlife_exit_extra callable.

    Snapshot schema (from backtest_rolling.py docstring):
        symbol, strat, dir, ur_bps, mae_bps, mfe_bps, hours_held, regime_z, ...
        entry_ts, ts (current ts)
    """
    def hook(snap):
        # Need vol_30d at entry vs current
        # Get features at entry ts and current ts
        sym = snap.get("symbol")
        cur_ts = snap.get("ts")
        entry_ts = snap.get("entry_ts")
        ur = snap.get("ur_bps", 0)
        regime_z = snap.get("regime_z", 0)
        if bear_only and regime_z >= -0.5:
            return (False, "")
        if ur >= ur_thresh_bps:
            return (False, "")
        # Look up vol_30d at entry and at current
        f_entry = features.get(sym, {}).get(entry_ts) if isinstance(features.get(sym), dict) else None
        f_cur = features.get(sym, {}).get(cur_ts) if isinstance(features.get(sym), dict) else None
        # features structure : dict[ts → dict[sym → features]] OR dict[sym → list]?
        # Check
        return (False, "")
    return hook


# Build map (sym, ts) → feature dict using _idx to lookup timestamps from data
print("\nBuilding (sym, ts) → feature map via _idx + data...")
sym_ts_to_feat = {}
for sym, feat_list in features.items():
    if feat_list is None:
        continue
    arr = data.get(sym)
    if arr is None:
        continue
    for f in feat_list:
        idx = f.get("_idx")
        if idx is not None and 0 <= idx < len(arr):
            ts = arr[idx]["t"]
            sym_ts_to_feat[(sym, ts)] = f
print(f"  Map size: {len(sym_ts_to_feat)}")
if sym_ts_to_feat:
    sample_key = next(iter(sym_ts_to_feat))
    print(f"  Sample feature for {sample_key}: vol_30d = {sym_ts_to_feat[sample_key].get('vol_30d')}")
else:
    print("  EMPTY map — abort"); raise SystemExit(1)

# Precompute per-symbol baseline vol_30d (use median across full history)
print("\nComputing per-symbol median vol_30d for expansion baseline...")
import numpy as np
sym_baseline_vol = {}
for sym, feat_list in features.items():
    if feat_list is None:
        continue
    vols = [f.get("vol_30d", 0) for f in feat_list if f.get("vol_30d", 0) > 0]
    if len(vols) >= 30:
        sym_baseline_vol[sym] = float(np.median(vols))
print(f"  Computed baselines for {len(sym_baseline_vol)} tokens. "
      f"Sample BTC = {sym_baseline_vol.get('BTC', 'n/a')}")


# Hook using snap fields: symbol, cur_bps, ts_ms, btc_z
def make_hook(ratio_thresh: float, ur_thresh_bps: float, bear_only: bool):
    def hook(snap):
        sym = snap.get("symbol")
        cur_ts = snap.get("ts_ms")
        ur = snap.get("cur_bps", 0)
        btc_z = snap.get("btc_z", 0)
        if bear_only and btc_z >= -0.5:
            return (False, "")
        if ur >= ur_thresh_bps:
            return (False, "")
        f_cur = sym_ts_to_feat.get((sym, cur_ts))
        if f_cur is None:
            return (False, "")
        v_cur = f_cur.get("vol_30d", 0)
        baseline = sym_baseline_vol.get(sym, 0)
        if baseline <= 0:
            return (False, "")
        ratio = v_cur / baseline
        if ratio >= ratio_thresh:
            return (True, "vol_expansion_cut")
        return (False, "")
    return hook


latest_ts = max(c["t"] for c in data["BTC"])
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000
splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]

early = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
runner = ({"strategies": RUNNER_EXT_STRATEGIES, "extra_candles": RUNNER_EXT_HOURS // 4,
           "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS, "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE}
          if RUNNER_EXT_STRATEGIES else None)

variants = {
    "V0_baseline":      None,
    "V1_1.5x_neg":      make_hook(1.5, 0, False),
    "V2_2.0x_n100":     make_hook(2.0, -100, False),
    "V3_1.3x_n200":     make_hook(1.3, -200, False),
    "V4_1.5x_n100_bear": make_hook(1.5, -100, True),
    "V5_2.5x_n0_bear":  make_hook(2.5, 0, True),
}


def run(start_ts, end_ts, hook):
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=1000.0,
                   oi_data=oi, early_exit_params=early,
                   runner_extension=runner, funding_data=funding,
                   apply_adaptive_modulator=True,
                   max_notional_per_trade=500.0, margin_check=True,
                   inlife_exit_extra=hook)
    return {"pnl_pct": r["pnl_pct"], "dd_pct": r["max_dd_pct"], "n_trades": r["n_trades"]}


print(f"\n{'='*110}")
print(f"{'Split':>20} | {'Variant':>20} | {'PnL%':>8} | {'DD%':>8} | {'N':>4} | {'dPnL':>8} | {'dDD':>8}")
print("-" * 110)

results = {label: {} for label, _, _ in splits}
for label, start_ts, end_ts in splits:
    for var_name, hook in variants.items():
        r = run(start_ts, end_ts, hook)
        results[label][var_name] = r
    base = results[label]["V0_baseline"]
    for var_name in variants:
        r = results[label][var_name]
        d_pnl = r["pnl_pct"] - base["pnl_pct"]
        d_dd = r["dd_pct"] - base["dd_pct"]
        print(f"{label:>20} | {var_name:>20} | {r['pnl_pct']:>+8.2f} | {r['dd_pct']:>+8.2f} | "
              f"{r['n_trades']:>4} | {d_pnl:>+8.2f} | {d_dd:>+8.2f}")
    print("-" * 110)

print(f"\n=== STRICT 4/4 VERDICT ===")
print(f"{'Variant':>20} | {'PnL pass':>8} | {'DD pass':>8} | {'Verdict':>20}")
print("-" * 70)
for var_name in variants:
    if var_name == "V0_baseline":
        continue
    pnl_pass = sum(1 for sl in splits
                   if results[sl[0]][var_name]["pnl_pct"] - results[sl[0]]["V0_baseline"]["pnl_pct"] >= 0)
    dd_pass = sum(1 for sl in splits
                  if results[sl[0]][var_name]["dd_pct"] - results[sl[0]]["V0_baseline"]["dd_pct"] <= 2.0)
    verdict = "STRICT 4/4 PASS" if (pnl_pass == 4 and dd_pass == 4) else f"FAIL ({pnl_pass}/4, {dd_pass}/4)"
    print(f"{var_name:>20} | {pnl_pass:>8} | {dd_pass:>8} | {verdict:>20}")
