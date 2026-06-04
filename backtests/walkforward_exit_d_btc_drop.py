"""EXIT-D WF — BTC drop trigger sur LONG positions.

Hypothèse : si BTC chute fort pendant le hold, les positions LONG perdent leur edge
(la cross-correlation alts/BTC est dominante). Cut LONGs preemptively.

Test variants :
    V1 : BTC ret_24h < -500 bps (-5%) AND dir = LONG
    V2 : BTC ret_24h < -1000 bps (-10%) AND dir = LONG
    V3 : BTC ret_4h < -300 (-3% / candle) AND dir = LONG AND ur < 0
    V4 : BTC ret_24h < -500 AND dir = LONG AND strat ∈ {S1, S5, S8}  (LONG-biased)
    V5 : combiné V1 + early_dead_check V3 S9 PASS
"""
import json
import numpy as np
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

# Precompute BTC returns at every ts
btc_closes = np.array([c["c"] for c in data["BTC"]])
btc_ts = np.array([c["t"] for c in data["BTC"]])
btc_idx = {t: i for i, t in enumerate(btc_ts)}

def btc_ret_at(ts: int, candles_back: int) -> float | None:
    i = btc_idx.get(ts)
    if i is None or i < candles_back:
        return None
    c0 = btc_closes[i]
    c_prev = btc_closes[i - candles_back]
    if c_prev <= 0 or c0 <= 0:
        return None
    return (c0 / c_prev - 1) * 1e4  # bps


def make_hook(btc_ret_thresh_bps: float, candles_back: int, long_only: bool = True,
              ur_thresh: float | None = None, strat_allow: set[str] | None = None):
    def hook(snap):
        if long_only and snap.get("dir") != 1:
            return (False, "")
        if strat_allow is not None and snap.get("strat") not in strat_allow:
            return (False, "")
        if ur_thresh is not None and snap.get("cur_bps", 0) >= ur_thresh:
            return (False, "")
        cur_ts = snap.get("ts_ms")
        btc_r = btc_ret_at(cur_ts, candles_back)
        if btc_r is None:
            return (False, "")
        if btc_r <= btc_ret_thresh_bps:
            return (True, "btc_drop_cut")
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
    "V0_baseline":              None,
    "V1_btc_24h_n500_long":     make_hook(-500, 6, long_only=True),
    "V2_btc_24h_n1000_long":    make_hook(-1000, 6, long_only=True),
    "V3_btc_4h_n300_long_neg":  make_hook(-300, 1, long_only=True, ur_thresh=0),
    "V4_btc_24h_n500_LONGstrats": make_hook(-500, 6, long_only=True, strat_allow={"S1", "S5", "S8"}),
    "V5_btc_24h_n800_long":     make_hook(-800, 6, long_only=True),
    "V6_btc_24h_n500_long_neg": make_hook(-500, 6, long_only=True, ur_thresh=-100),
}


def run(start_ts, end_ts, hook, edc=None):
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=1000.0,
                   oi_data=oi, early_exit_params=early,
                   runner_extension=runner, funding_data=funding,
                   apply_adaptive_modulator=True,
                   max_notional_per_trade=500.0, margin_check=True,
                   inlife_exit_extra=hook,
                   early_dead_check=edc)
    return {"pnl_pct": r["pnl_pct"], "dd_pct": r["max_dd_pct"], "n_trades": r["n_trades"]}


print(f"\n{'='*110}")
print(f"{'Split':>20} | {'Variant':>30} | {'PnL%':>8} | {'DD%':>8} | {'N':>4} | {'dPnL':>8} | {'dDD':>8}")
print("-" * 120)

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
        print(f"{label:>20} | {var_name:>30} | {r['pnl_pct']:>+8.2f} | {r['dd_pct']:>+8.2f} | "
              f"{r['n_trades']:>4} | {d_pnl:>+8.2f} | {d_dd:>+8.2f}")
    print("-" * 120)

print(f"\n=== STRICT 4/4 VERDICT ===")
print(f"{'Variant':>30} | {'PnL pass':>8} | {'DD pass':>8} | {'Verdict':>20}")
print("-" * 80)
for var_name in variants:
    if var_name == "V0_baseline":
        continue
    pnl_pass = sum(1 for sl in splits
                   if results[sl[0]][var_name]["pnl_pct"] - results[sl[0]]["V0_baseline"]["pnl_pct"] >= 0)
    dd_pass = sum(1 for sl in splits
                  if results[sl[0]][var_name]["dd_pct"] - results[sl[0]]["V0_baseline"]["dd_pct"] <= 2.0)
    verdict = "STRICT 4/4 PASS" if (pnl_pass == 4 and dd_pass == 4) else f"FAIL ({pnl_pass}/4, {dd_pass}/4)"
    print(f"{var_name:>30} | {pnl_pass:>8} | {dd_pass:>8} | {verdict:>20}")
