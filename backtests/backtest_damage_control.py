"""Walk-forward — comprehensive damage-control sweep.

Last attempt to find ANY mechanism that amortizes timeout/catastrophe losses
without destroying the long-term edge. Tests 4 families :

  A) BTC-correlation exit — exit if BTC moves N bps adverse within Yh
  B) Dead-timeout aggressive variants — earlier lead, looser MFE_CAP
  C) S5-specific tighter catastrophe stop
  D) Early-MFE-absence exit on S5 — exit at hour N if no positive bps

All sweeps walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_damage_control
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def fmt_row(name, deltas_pnl, deltas_dd):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {name:50s}  "
            f"Δ28m={deltas_pnl['28m']:+8.1f}  Δ12m={deltas_pnl['12m']:+7.1f}  "
            f"Δ6m={deltas_pnl['6m']:+6.1f}  Δ3m={deltas_pnl['3m']:+5.1f}  "
            f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit_default = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]
    end_ts = latest_ts
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts,
                       early_exit_params=early_exit_default, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # ── (A) BTC-correlation exit ───────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(A) BTC-correlation exit — cut if BTC moves N bps adverse within Yh':^100}")
    print("=" * 100)
    for thr in [400, 600, 800, 1000, 1200]:
        for lb in [4, 8, 12, 24, None]:
            cfg = {"threshold_bps": thr, "lookback_h": lb,
                   "apply_long": True, "apply_short": True}
            name = f"BTC corr ≥{thr}bps lb={lb if lb else 'all'}h"
            positives, d_pnl, d_dd = run_and_record(
                name, btc_corr_exit=cfg, early_exit_params=early_exit_default)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (B) Dead-timeout aggressive ────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(B) Dead-timeout aggressive — earlier lead OR looser MFE cap':^100}")
    print("=" * 100)
    for lead_h in [12, 18, 24, 36, 48]:
        for mfe_cap in [150, 250, 400, 600]:
            cfg = dict(
                exit_lead_candles=int(lead_h // 4),
                mfe_cap_bps=mfe_cap,
                mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
                slack_bps=DEAD_TIMEOUT_SLACK_BPS,
            )
            name = f"DT lead={lead_h:2d}h mfe_cap≤{mfe_cap}"
            positives, d_pnl, d_dd = run_and_record(
                name, early_exit_params=cfg)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (C) S5-specific tighter stop ──────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(C) S5-specific tighter catastrophe stop':^100}")
    print("=" * 100)
    for s5_stop in [-1000, -800, -600, -500, -400]:
        name = f"S5 stop = {s5_stop} bps"
        positives, d_pnl, d_dd = run_and_record(
            name, stop_override={"S5": s5_stop},
            early_exit_params=early_exit_default)
        print(fmt_row(name, d_pnl, d_dd))

    # ── (D) Early-MFE-absence on S5 ───────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(D) Early-MFE-absence — exit S5 at h=N if MFE never crossed X':^100}")
    print("=" * 100)
    for check_h in [12, 16, 20, 24]:
        for mfe_min in [50, 100, 150, 200]:
            cfg = {"check_after_candles": int(check_h // 4),
                   "mfe_min_bps": mfe_min,
                   "strategies": {"S5"}}
            name = f"S5 early_mfe @h{check_h:2d} mfe<{mfe_min}"
            positives, d_pnl, d_dd = run_and_record(
                name, early_mfe_exit=cfg, early_exit_params=early_exit_default)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (E) Combined: dead_timeout + S5 stop + S5 early_mfe ────────────
    print("\n" + "=" * 100)
    print(f"{'(E) Combined top candidates (if any reasonable from A-D)':^100}")
    print("=" * 100)
    # Try a few combos of best-looking params
    combos = [
        # All currently-used params + tighter S5 stop + early_mfe_exit
        ("S5 stop -800 + early_mfe@h20 mfe<100",
         {"stop_override": {"S5": -800},
          "early_mfe_exit": {"check_after_candles": 5, "mfe_min_bps": 100, "strategies": {"S5"}},
          "early_exit_params": early_exit_default}),
        ("DT lead=24h + S5 stop -1000",
         {"stop_override": {"S5": -1000},
          "early_exit_params": dict(exit_lead_candles=6, mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
                                     mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
                                     slack_bps=DEAD_TIMEOUT_SLACK_BPS)}),
        ("BTC corr 800@8h + S5 stop -1000",
         {"btc_corr_exit": {"threshold_bps": 800, "lookback_h": 8,
                            "apply_long": True, "apply_short": True},
          "stop_override": {"S5": -1000},
          "early_exit_params": early_exit_default}),
    ]
    for name, kwargs in combos:
        positives, d_pnl, d_dd = run_and_record(name, **kwargs)
        print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^100}")
    print("=" * 100)
    found = []
    for name, info in all_results.items():
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found[:15]:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # Also print top configs by sum_pnl regardless of 4/4
    print("\n" + "=" * 100)
    print(f"{'Top 10 by sum(ΔPnL) — even if not 4/4':^100}")
    print("=" * 100)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:10]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:48s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs tested)")


if __name__ == "__main__":
    main()
