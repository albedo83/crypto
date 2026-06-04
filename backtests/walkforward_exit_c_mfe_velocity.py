"""EXIT-C WF — early_dead_check (généralisation de s8_dead_in_water).

Test variants pour S1/S5/S9/S10 (S8 déjà couvert) :
    V1 S5 : (T+12h, mfe ≤ 100 bps)
    V2 S5 : (T+8h,  mfe ≤ 75 bps)
    V3 S9 : (T+12h, mfe ≤ 150 bps)
    V4 S10: (T+8h,  mfe ≤ 50 bps)
    V5 combined : V1 + V3 + V4
    V6 S1 : (T+16h, mfe ≤ 200 bps)
    V7 V1+V3+V4+V6

Strict 4/4 vs baseline (sans early_dead_check).
"""
import json
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
    "V1_S5_12h_100":    {"S5": (12.0, 100.0)},
    "V2_S5_8h_75":      {"S5": (8.0, 75.0)},
    "V3_S9_12h_150":    {"S9": (12.0, 150.0)},
    "V4_S10_8h_50":     {"S10": (8.0, 50.0)},
    "V5_S5_S9_S10":     {"S5": (12.0, 100.0), "S9": (12.0, 150.0), "S10": (8.0, 50.0)},
    "V6_S1_16h_200":    {"S1": (16.0, 200.0)},
    "V7_all":           {"S1": (16.0, 200.0), "S5": (12.0, 100.0), "S9": (12.0, 150.0), "S10": (8.0, 50.0)},
}


def run(start_ts, end_ts, edc):
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=1000.0,
                   oi_data=oi, early_exit_params=early,
                   runner_extension=runner, funding_data=funding,
                   apply_adaptive_modulator=True,
                   max_notional_per_trade=500.0, margin_check=True,
                   early_dead_check=edc)
    return {"pnl_pct": r["pnl_pct"], "dd_pct": r["max_dd_pct"], "n_trades": r["n_trades"]}


print(f"\n{'='*110}")
print(f"{'Split':>20} | {'Variant':>18} | {'PnL%':>8} | {'DD%':>8} | {'N':>4} | {'dPnL':>8} | {'dDD':>8}")
print("-" * 110)

results = {label: {} for label, _, _ in splits}
for label, start_ts, end_ts in splits:
    for var_name, edc in variants.items():
        r = run(start_ts, end_ts, edc)
        results[label][var_name] = r
    base = results[label]["V0_baseline"]
    for var_name in variants:
        r = results[label][var_name]
        d_pnl = r["pnl_pct"] - base["pnl_pct"]
        d_dd = r["dd_pct"] - base["dd_pct"]
        print(f"{label:>20} | {var_name:>18} | {r['pnl_pct']:>+8.2f} | {r['dd_pct']:>+8.2f} | "
              f"{r['n_trades']:>4} | {d_pnl:>+8.2f} | {d_dd:>+8.2f}")
    print("-" * 110)

print(f"\n=== STRICT 4/4 VERDICT ===")
print(f"Criterion: ΔPnL ≥ 0 AND ΔDD ≤ +2pp on all 4 splits")
print(f"{'Variant':>18} | {'PnL pass':>8} | {'DD pass':>8} | {'Verdict':>20}")
print("-" * 70)
verdicts = {}
for var_name in variants:
    if var_name == "V0_baseline":
        continue
    pnl_pass = sum(1 for sl in splits
                   if results[sl[0]][var_name]["pnl_pct"] - results[sl[0]]["V0_baseline"]["pnl_pct"] >= 0)
    dd_pass = sum(1 for sl in splits
                  if results[sl[0]][var_name]["dd_pct"] - results[sl[0]]["V0_baseline"]["dd_pct"] <= 2.0)
    verdict = "STRICT 4/4 PASS" if (pnl_pass == 4 and dd_pass == 4) else f"FAIL ({pnl_pass}/4, {dd_pass}/4)"
    verdicts[var_name] = (pnl_pass, dd_pass, verdict)
    print(f"{var_name:>18} | {pnl_pass:>8} | {dd_pass:>8} | {verdict:>20}")

with open("/home/crypto/backtests/output/exit_c_results.json", "w") as f:
    json.dump({"results": results, "verdicts": {k: {"pnl_pass": v[0], "dd_pass": v[1], "verdict": v[2]}
                                                  for k, v in verdicts.items()}},
              f, indent=2, default=float)
