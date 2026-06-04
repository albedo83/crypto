"""Walk-forward strict 4/4 — per-trade notional cap sweep with margin simulation.

Configs tested:
  A = cap $500   (current live, v12.13.9)
  B = cap $1000
  C = cap $1500
  D = no cap (None)

All run with margin_check=True (utility 0.95) to honor HL margin constraint
that the live bot actually hits. Without margin_check the BT inflates PnL
artificially by simulating impossible margin utilization.

Strict criterion: ≥ baseline on ALL 4 splits (PnL ΔP ≥ 0 AND ΔDD ≤ +2pp).

4 splits 6m non-overlapping anchored on the latest data:
  split_1: T−24m → T−18m
  split_2: T−18m → T−12m
  split_3: T−12m → T−6m
  split_4: T−6m  → T
"""
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
print("Done.")

latest_ts = max(c["t"] for c in data["BTC"])
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000

splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]

configs = [
    ("A_cap500",   500.0),
    ("B_cap1000", 1000.0),
    ("C_cap1500", 1500.0),
    ("D_no_cap",  None),
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


def run(start_ts, end_ts, cap):
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=1000.0,
                   oi_data=oi, early_exit_params=early,
                   runner_extension=runner, funding_data=funding,
                   apply_adaptive_modulator=True,
                   max_notional_per_trade=cap, margin_check=True)
    return {
        "pnl_pct": r["pnl_pct"],
        "dd_pct": r["max_dd_pct"],
        "n_trades": r["n_trades"],
        "margin_skips": r["n_margin_skip"],
    }


print(f"\n{'Split':>20} | {'Config':>10} | {'PnL%':>8} | {'DD%':>7} | {'Trades':>6} | {'MgnSkip':>7} | {'ΔPnL pp':>8} | {'ΔDD pp':>7}")
print("-" * 110)

results = {split[0]: {} for split in splits}
for split_label, start_ts, end_ts in splits:
    for cfg_label, cap in configs:
        r = run(start_ts, end_ts, cap)
        results[split_label][cfg_label] = r
    baseline = results[split_label]["A_cap500"]
    for cfg_label, cap in configs:
        r = results[split_label][cfg_label]
        delta_pnl = r["pnl_pct"] - baseline["pnl_pct"]
        delta_dd = r["dd_pct"] - baseline["dd_pct"]
        print(f"{split_label:>20} | {cfg_label:>10} | {r['pnl_pct']:>+8.2f} | "
              f"{r['dd_pct']:>+7.2f} | {r['n_trades']:>6} | {r['margin_skips']:>7} | "
              f"{delta_pnl:>+8.2f} | {delta_dd:>+7.2f}")
    print("-" * 110)

# Strict 4/4 verdict per config
print("\n=== STRICT 4/4 VERDICT (vs A_cap500 baseline) ===")
print("Criterion: ΔPnL ≥ 0 AND ΔDD ≤ +2pp on ALL 4 splits\n")
print(f"{'Config':>10} | {'Splits ΔPnL+':>13} | {'Splits ΔDD≤+2pp':>17} | {'Total skips':>11} | {'Verdict':>15}")
print("-" * 90)
for cfg_label, _ in configs:
    if cfg_label == "A_cap500":
        continue
    pnl_pass = sum(1 for sl in splits
                   if results[sl[0]][cfg_label]["pnl_pct"] - results[sl[0]]["A_cap500"]["pnl_pct"] >= 0)
    dd_pass = sum(1 for sl in splits
                  if results[sl[0]][cfg_label]["dd_pct"] - results[sl[0]]["A_cap500"]["dd_pct"] <= 2.0)
    skips = sum(results[sl[0]][cfg_label]["margin_skips"] for sl in splits)
    verdict = "STRICT 4/4 PASS" if pnl_pass == 4 and dd_pass == 4 else f"FAIL ({pnl_pass}/4 ΔPnL, {dd_pass}/4 ΔDD)"
    print(f"{cfg_label:>10} | {pnl_pass:>13} | {dd_pass:>17} | {skips:>11} | {verdict:>15}")
