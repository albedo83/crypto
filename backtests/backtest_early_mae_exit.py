"""Walk-forward 4/4 strict test of an EARLY-MAE exit mechanism.

Hypothesis (user observation 2026-05-12): when a trade crashes fast
(deep MAE within the first few hours), historical recovery rate is
much lower than baseline WR. Test: close the position immediately at
the MAE threshold price if the threshold is breached within `max_candles`
candles since entry.

Different from WR auto-close (which was rejected in walk-forward):
- WR auto-close used the live WR estimate (slow, stat-noisy)
- Early-MAE uses the SPEED OF THE CRASH as an exit signal (fast, mechanical)

Configurations swept:
- strats: {S5}, {S5 LONG only}, {S5, S9}
- mae_threshold: -700, -800, -900, -1000 bps
- max_candles: 1 (4h), 2 (8h), 3 (12h)

Pass criterion: 4/4 strict (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp).
Also tracked: "DD-friendly" config (significant DD reduction with mild PnL drag).

Run: python3 -m backtests.backtest_early_mae_exit
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy,
)

CAP = 1000.0


def main() -> None:
    print("Loading 3y candles...")
    t0 = time.time()
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()
    print(f"  loaded in {time.time() - t0:.1f}s")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
        end_ts_ms=latest_ts,
        apply_adaptive_modulator=True,  # canonical v12.2.0 baseline
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    # Baseline (no early-MAE exit)
    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 adaptive modulator (no early-MAE exit)':^110}")
    print("=" * 110)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # Sweep configs
    print("\n" + "=" * 110)
    print(f"{'EARLY-MAE EXIT SWEEP':^110}")
    print("=" * 110)
    print("  ✓ = 4/4 strict pass  |  ✓DD = significant DD reduction with mild PnL drag\n")
    print(f"  {'config':<52s}  {'Δ28m':>9s}  {'Δ12m':>8s}  {'Δ6m':>7s}  {'Δ3m':>7s}  "
          f"{'ΔPnL avg':>9s}  {'ΔDD avg':>8s}  pos")

    configs = []
    # S5 LONG only (the case the user flagged)
    for thr in [-700, -800, -900, -1000]:
        for mc in [1, 2, 3]:
            configs.append((
                f"S5 LONG, MAE≤{thr}, ≤{mc}c ({mc*4}h)",
                {"strats": {"S5"}, "dirs": {1}, "mae_threshold": thr, "max_candles": mc},
            ))
    # S5 both directions
    for thr in [-800, -1000]:
        configs.append((
            f"S5 both dirs, MAE≤{thr}, ≤2c (8h)",
            {"strats": {"S5"}, "dirs": None, "mae_threshold": thr, "max_candles": 2},
        ))
    # S5+S9 LONG
    for thr in [-800, -1000]:
        configs.append((
            f"S5+S9 LONG, MAE≤{thr}, ≤2c",
            {"strats": {"S5", "S9"}, "dirs": {1}, "mae_threshold": thr, "max_candles": 2},
        ))

    results = []
    for name, cfg in configs:
        deltas, ddds = {}, {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           early_mae_exit=cfg, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_pnl = sum(deltas.values()) / 4
        avg_dd = sum(ddds.values()) / 4
        strict = positives == 4 and avg_dd <= 0.5
        dd_friendly = avg_dd <= -1.0 and avg_pnl >= -5.0
        if strict:
            flag = "✓✓"
        elif dd_friendly:
            flag = "✓DD"
        else:
            flag = "  "
        print(f"  {flag} {name:<50s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+7.1f}%  "
              f"{deltas['6m']:+6.1f}%  {deltas['3m']:+6.1f}%  {avg_pnl:+8.1f}pp  "
              f"{avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_pnl, avg_dd, positives, strict, dd_friendly))

    # Verdict
    print("\n" + "=" * 110)
    strict_pass = [r for r in results if r[5]]
    dd_pass = [r for r in results if r[6] and not r[5]]
    if strict_pass:
        print(f"STRICT 4/4 PASS ({len(strict_pass)}):")
        for name, _, avg_pnl, avg_dd, *_ in strict_pass:
            print(f"  ✓✓ {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp")
        print("\nNext step: pick most defensible passer + ship in trading.py.")
    elif dd_pass:
        print(f"DD-FRIENDLY ({len(dd_pass)} — DD reduced with tolerable PnL drag):")
        for name, _, avg_pnl, avg_dd, *_ in dd_pass:
            print(f"  ✓DD {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp")
    else:
        print("NO config passes strict OR DD-friendly.")
        results.sort(key=lambda r: r[3])
        print("Top 3 by ΔDD improvement:")
        for name, _, avg_pnl, avg_dd, pos, *_ in results[:3]:
            print(f"  {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp ({pos}/4)")
        print("→ Early-MAE exit rejected on this strat-set.")


if __name__ == "__main__":
    main()
