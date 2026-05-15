"""basket_haircut_eda: baseline run for 4 walk-forward windows.

Runs backtest_rolling with the current bot configuration (adaptive modulator +
dead-timeout + runner extension) on 28m / 12m / 6m / 3m windows. Dumps:
  - trades_{W}.jsonl     : full trades list with eff_n_{7d,14d,30d} at-open
  - basket_ts_{W}.jsonl  : per-ts (effective_n, basket_unrealized, capital)
  - baseline_summary.json: top-line PnL/DD/n_trades per window

Output dir: backtests/basket_haircut_eda_data/
"""
from __future__ import annotations
import os
import sys
import json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

sys.path.insert(0, "/home/crypto")
from backtests.backtest_rolling import (
    run_window, load_3y_candles, build_features,
    compute_sector_features, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS, RUNNER_EXT_MIN_MFE_BPS,
    RUNNER_EXT_MIN_CUR_TO_MFE,
)

OUTDIR = "/home/crypto/backtests/basket_haircut_eda_data"
WINDOW_LABELS = [
    ("28m", 28),
    ("12m", 12),
    ("6m",  6),
    ("3m",  3),
]


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    os.makedirs(OUTDIR, exist_ok=True)

    summary = []
    for label, months in WINDOW_LABELS:
        start_dt = end_dt - relativedelta(months=months)
        start_ts = int(start_dt.timestamp() * 1000)
        print(f"\nRunning {label} {start_dt.date()} → {end_dt.date()}")
        os.environ["BASKET_HAIRCUT_EDA_DUMP"] = f"{OUTDIR}/basket_ts_{label}.jsonl"
        r = run_window(features, data, sector_features, dxy_data, start_ts, latest_ts,
                       start_capital=1000.0,
                       oi_data=oi_data, early_exit_params=early_exit_params,
                       runner_extension=runner_ext_cfg,
                       funding_data=funding_data,
                       apply_adaptive_modulator=True)
        n = r["n_trades"]
        pnl = float(r["pnl"])
        dd = float(r["max_dd_pct"])
        print(f"  → trades={n} pnl={pnl:+.0f} ({r['pnl_pct']:+.1f}%) "
              f"dd={dd:.1f}% ts={len(r['basket_timeseries'])}")
        summary.append({"label": label, "n_trades": n,
                        "pnl": pnl, "dd_pct": dd,
                        "n_ts": len(r["basket_timeseries"])})
        trades_path = f"{OUTDIR}/trades_{label}.jsonl"
        with open(trades_path, "w") as fh:
            for t in r["trades"]:
                fh.write(json.dumps(t, default=float) + "\n")
        print(f"  wrote {trades_path}")

    with open(f"{OUTDIR}/baseline_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2, default=float)
    print(f"\nBaseline summary written to {OUTDIR}/baseline_summary.json")


if __name__ == "__main__":
    main()
