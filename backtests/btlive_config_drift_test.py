"""Quick config-drift test: re-run the live deployment BT with the actual
live-period config to quantify how much of the gap is methodological artifact.

Live deployment = 2026-03-26 → 2026-05-30 (65d).
During this period, the *actual* live bot ran with (chronologically):
  - disp_gate ON      (until v12.8.0 today, ~64/65 days)
  - 29 TRADE_SYMBOLS  (until v12.7.0 on 2026-05-16, ~51/65 days)
  - traj_cut OFF      (until v12.7.1 on 2026-05-20, ~55/65 days)

The current btlive uses today's config (disp_gate OFF, 35 tokens, traj_cut OFF
since no hook passed). This script runs THREE configurations:

  A. Today's config (matches current btlive)           → +$277 expected
  B. Live config "early period"  (disp_gate ON, 29 tok) → reflects months 1-2
  C. Live config "late period"   (disp_gate ON, 35 tok, traj_cut ON) → last 10d

Compare each against live PnL = $-38.49. The closer B comes to live, the more
of the $316 gap is config drift (not bot underperformance).
"""
from __future__ import annotations

import time
import datetime as dt
from pathlib import Path

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, load_oi, load_funding,
)
from backtests import backtest_rolling as br
from backtests.backtest_trajectory_cut_v2 import make_hook as make_traj_hook, TRAJ_PARAMS
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

# v12.7.0 added these 6 tokens — to simulate pre-v12.7.0 live, we exclude them.
TOKENS_V12_7_0_ADDED = {"BCH", "DOT", "ADA", "XMR", "ENA", "UNI"}

# Live deployment window
LIVE_START = "2026-03-26"
START_CAPITAL = 500.0

EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
RUNNER_EXT_DICT = {
    "strategies": RUNNER_EXT_STRATEGIES,
    "extra_candles": RUNNER_EXT_HOURS // 4,
    "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
    "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
}


def load_all():
    print("Loading data...")
    t0 = time.time()
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    # disp_by_ts for traj_cut hook
    feat_by_ts: dict[int, list[float]] = {}
    for coin, flist in features.items():
        for f in flist:
            ts = f["t"]
            r = f.get("ret_6h")
            if r is not None:
                feat_by_ts.setdefault(ts, []).append(r)
    disp_by_ts: dict[int, float] = {}
    for ts, rets in feat_by_ts.items():
        if len(rets) > 4:
            disp_by_ts[ts] = float(np.std(rets))
    print(f"  loaded in {time.time()-t0:.1f}s")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts, disp_by_ts=disp_by_ts)


def run_config(ctx, name, *, disp_on, traj_on, exclude_new_tokens):
    """Run one config over the live deployment window."""
    print(f"\n── Config {name} ──")
    print(f"     disp_gate={'ON' if disp_on else 'OFF'},  "
          f"traj_cut={'ON' if traj_on else 'OFF'},  "
          f"new_tokens={'EXCLUDED' if exclude_new_tokens else 'INCLUDED'}")

    # Filter features/data to exclude new tokens if requested
    feats = ctx["features"]
    data = ctx["data"]
    if exclude_new_tokens:
        feats = {k: v for k, v in feats.items() if k not in TOKENS_V12_7_0_ADDED}
        data = {k: v for k, v in data.items() if k not in TOKENS_V12_7_0_ADDED}
        # Re-import TOKENS guard
        import backtests.backtest_genetic as bg
        saved_tokens = bg.TOKENS
        bg.TOKENS = [t for t in saved_tokens if t not in TOKENS_V12_7_0_ADDED]

    # Monkey-patch disp_gate
    saved_disp = br.DISP_GATE_BPS
    if disp_on:
        br.DISP_GATE_BPS = 700.0
    else:
        br.DISP_GATE_BPS = 99999.0

    # Build hook for traj_cut
    hook = None
    if traj_on:
        hook, _ = make_traj_hook(
            **TRAJ_PARAMS,
            strategies={"S5"},
            regime_check=lambda bz, dp: bz < -0.5,
            disp_by_ts=ctx["disp_by_ts"],
        )

    try:
        start_ms = int(dt.datetime.fromisoformat(LIVE_START).timestamp() * 1000)
        end_ms = ctx["end_ts"]
        t0 = time.time()
        res = run_window(
            feats, data, ctx["sec"], ctx["dxy"],
            start_ms, end_ms, start_capital=START_CAPITAL,
            oi_data=ctx["oi"], funding_data=ctx["funding"],
            early_exit_params=EARLY_EXIT,
            apply_adaptive_modulator=True,
            inlife_exit_extra=hook,
            runner_extension=RUNNER_EXT_DICT,
        )
        elapsed = time.time() - t0
    finally:
        br.DISP_GATE_BPS = saved_disp
        if exclude_new_tokens:
            bg.TOKENS = saved_tokens

    final_balance = START_CAPITAL * (1 + res["pnl_pct"] / 100)
    pnl_usdt = final_balance - START_CAPITAL
    n = res["n_trades"]
    print(f"  → PnL=${pnl_usdt:+8.2f} ({res['pnl_pct']:+.1f}%)  "
          f"DD={res['max_dd_pct']:.2f}%  trades={n}  WR={res['win_rate']:.1f}%  ({elapsed:.1f}s)")
    return res, pnl_usdt


def main():
    ctx = load_all()
    LIVE_PNL = -38.49  # from btlive output
    print(f"\n[Live deployment ref] {LIVE_START} → today, ${START_CAPITAL} start cap")
    print(f"  Live actual PnL: ${LIVE_PNL:+.2f}")
    print(f"\n[BT configurations to test]")

    # A. Today's config — should reproduce btlive's +$277
    a_res, a_pnl = run_config(ctx, "A_today_config",
                               disp_on=False, traj_on=False,
                               exclude_new_tokens=False)
    # B. Live config most of period: disp_gate ON, 29 tokens, traj_cut OFF
    b_res, b_pnl = run_config(ctx, "B_live_period_dominant",
                               disp_on=True, traj_on=False,
                               exclude_new_tokens=True)
    # C. Live config end-of-period: disp_gate ON, 35 tokens, traj_cut ON
    #    (live had this for ~10 days at the end)
    c_res, c_pnl = run_config(ctx, "C_live_period_endgame",
                               disp_on=True, traj_on=True,
                               exclude_new_tokens=False)

    print("\n\n══ COMPARISON vs Live ══")
    print(f"{'config':28s}  {'BT PnL':>10s}  {'gap vs Live':>12s}  {'fraction of $316 gap':>22s}")
    headline = 316.0
    for label, pnl in [("Live actual",        LIVE_PNL),
                       ("A: today's config",  a_pnl),
                       ("B: live-period dominant", b_pnl),
                       ("C: live-period endgame", c_pnl)]:
        gap = pnl - LIVE_PNL
        frac = gap / headline * 100 if gap != LIVE_PNL else 0
        if label == "Live actual":
            print(f"  {label:28s}  ${pnl:>+9.2f}  {'(ref)':>12s}  {'':>22s}")
        else:
            print(f"  {label:28s}  ${pnl:>+9.2f}  ${gap:>+11.2f}  {frac:>+21.1f}%")

    print("\n══ Interpretation ══")
    print(f"Today's btlive shows gap = $316 (BT $+277 − Live $-38)")
    print(f"If config B (closest to actual live-period config) shows BT close to live,")
    print(f"then most of the $316 gap is methodological artifact (config drift), not bot underperformance.")
    print(f"  Config B gap vs live: ${b_pnl - LIVE_PNL:+.2f}  ({(b_pnl - LIVE_PNL)/headline*100:+.1f}% of headline)")
    print(f"  → fraction attributable to config drift: {(1 - (b_pnl - LIVE_PNL)/headline)*100:+.1f}%")


if __name__ == "__main__":
    main()
