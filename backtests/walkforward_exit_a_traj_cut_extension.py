"""EXIT-A — Étendre traj_cut à autres stratégies que S5.

Actuellement: TRAJ_CUT_STRATEGIES = {"S5"} (config.py).
Test variants:
    V0 baseline = {"S5"}
    V1 = {"S5", "S1"}   — ajoute S1 LONG
    V2 = {"S5", "S9"}   — ajoute S9 LONG/SHORT
    V3 = {"S5", "S1", "S9"} — full
    V4 = {"S5", "S8"}   — ajoute S8 LONG

Pour chaque variant : WF 4 splits 6m vs V0. Strict 4/4 ΔPnL ≥ 0 ET ΔDD ≤ +2pp.
"""
import json
from datetime import datetime, timezone

import backtests.backtest_rolling as br
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
print("Done.")

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
    "V0_baseline_S5": {"S5"},
    "V1_S5_S1":       {"S5", "S1"},
    "V2_S5_S9":       {"S5", "S9"},
    "V3_S5_S1_S9":    {"S5", "S1", "S9"},
    "V4_S5_S8":       {"S5", "S8"},
}

# Monkey-patch helper: mutate the module-level set in place so engine sees update
def set_strats(strats: set):
    br.TRAJ_CUT_STRATEGIES.clear()
    br.TRAJ_CUT_STRATEGIES.update(strats)


def run(start_ts, end_ts):
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=1000.0,
                   oi_data=oi, early_exit_params=early,
                   runner_extension=runner, funding_data=funding,
                   apply_adaptive_modulator=True,
                   max_notional_per_trade=500.0, margin_check=True)
    return {"pnl_pct": r["pnl_pct"], "dd_pct": r["max_dd_pct"], "n_trades": r["n_trades"]}


print(f"\n{'='*100}")
print(f"{'Split':>20} | {'Variant':>16} | {'PnL%':>8} | {'DD%':>8} | {'N':>4} | {'ΔPnL':>8} | {'ΔDD':>8}")
print("-" * 100)

results = {label: {} for label, _, _ in splits}
for label, start_ts, end_ts in splits:
    for var_name, strats in variants.items():
        set_strats(strats)
        r = run(start_ts, end_ts)
        results[label][var_name] = r
    base = results[label]["V0_baseline_S5"]
    for var_name in variants:
        r = results[label][var_name]
        d_pnl = r["pnl_pct"] - base["pnl_pct"]
        d_dd = r["dd_pct"] - base["dd_pct"]
        print(f"{label:>20} | {var_name:>16} | {r['pnl_pct']:>+8.2f} | {r['dd_pct']:>+8.2f} | "
              f"{r['n_trades']:>4} | {d_pnl:>+8.2f} | {d_dd:>+8.2f}")
    print("-" * 100)

# Strict 4/4 verdict
print(f"\n=== STRICT 4/4 VERDICT (vs V0_baseline_S5) ===")
print(f"Criterion: ΔPnL ≥ 0 AND ΔDD ≤ +2pp on all 4 splits")
print(f"{'Variant':>16} | {'splits PnL✓':>12} | {'splits DD✓':>11} | {'Verdict':>20}")
print("-" * 70)
for var_name in variants:
    if var_name == "V0_baseline_S5":
        continue
    pnl_pass = sum(1 for sl in splits
                   if results[sl[0]][var_name]["pnl_pct"] - results[sl[0]]["V0_baseline_S5"]["pnl_pct"] >= 0)
    dd_pass = sum(1 for sl in splits
                  if results[sl[0]][var_name]["dd_pct"] - results[sl[0]]["V0_baseline_S5"]["dd_pct"] <= 2.0)
    verdict = "STRICT 4/4 PASS" if pnl_pass == 4 and dd_pass == 4 else f"FAIL ({pnl_pass}/4, {dd_pass}/4)"
    print(f"{var_name:>16} | {pnl_pass:>12} | {dd_pass:>11} | {verdict:>20}")

# Save
with open("/home/crypto/backtests/output/exit_a_results.json", "w") as f:
    json.dump(results, f, indent=2, default=float)
