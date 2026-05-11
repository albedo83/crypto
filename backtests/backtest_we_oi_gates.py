"""Walk-forward 4/4 strict validation of two entry gates suggested by
`analyze_obs_features.py` on live data:

Gate A — Skip weekend entries (Sat/Sun UTC)
  - blanket: any entry on Sat/Sun → skip
  - S5 only: skip just S5 entries on Sat/Sun
  - S5 LONG: skip only S5 LONG on Sat/Sun (narrow killer)
  - LONGs: skip any LONG on Sat/Sun

Gate B — Skip SHORT when OI is rising (mirror of v11.4.9 LONG OI gate)
  - +5% / +10% / +15% / +20% / +25% thresholds

Each config measured on 28m / 12m / 6m / 3m windows against the canonical
v12.2.0 baseline (1D adaptive modulator). Pass condition: all 4 Δpnl > 0
AND average ΔDD ≤ +0.5pp.

Run: python3 -m backtests.backtest_we_oi_gates
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy, oi_delta_24h_pct,
)

CAP = 1000.0


def is_weekend(ts_ms: int) -> bool:
    """True if ts (epoch ms) is Saturday or Sunday in UTC."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).weekday() >= 5


def make_we_skip(strat_filter=None, dir_filter=None):
    """skip_fn that skips WE entries. Optional filters narrow the gate."""
    def fn(coin, ts, strat, dir_):
        if not is_weekend(ts):
            return False
        if strat_filter is not None and strat != strat_filter:
            return False
        if dir_filter is not None and dir_ != dir_filter:
            return False
        return True
    return fn


def make_oi_short_skip(threshold_bps: float, oi_data):
    """skip_fn that skips SHORTs when OI 24h delta > threshold_bps (positive)."""
    def fn(coin, ts, strat, dir_):
        if dir_ != -1:
            return False
        oi_d = oi_delta_24h_pct(oi_data, coin, ts)
        if oi_d is None:
            return False
        return oi_d > threshold_bps
    return fn


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

    # Baseline: v12.2.0 adaptive modulator, no extra gate
    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 adaptive modulator (no extra gate)':^110}")
    print("=" * 110)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # ── Gate A : Weekend skip ──
    print("\n" + "=" * 110)
    print(f"{'GATE A — Weekend skip variants':^110}")
    print("=" * 110)
    print(f"  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<40s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>8s}  pos")

    we_configs = [
        ("WE skip ALL",                make_we_skip()),
        ("WE skip S5 LONG only",       make_we_skip(strat_filter="S5", dir_filter=1)),
        ("WE skip S5 (any dir)",       make_we_skip(strat_filter="S5")),
        ("WE skip all LONGs",          make_we_skip(dir_filter=1)),
        ("WE skip all SHORTs",         make_we_skip(dir_filter=-1)),
    ]

    results_a = []
    for name, fn in we_configs:
        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=fn, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {flag} {name:<38s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+8.1f}%  "
              f"{deltas['6m']:+8.1f}%  {deltas['3m']:+8.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results_a.append((name, deltas, avg_dd, positives))

    # ── Gate B : OI SHORT gate ──
    print("\n" + "=" * 110)
    print(f"{'GATE B — OI rising → skip SHORT (mirror v11.4.9 LONG gate)':^110}")
    print("=" * 110)
    print(f"  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<40s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>8s}  pos")

    oi_thresholds = [500, 1000, 1500, 2000, 2500]  # bps = +5%, +10%, +15%, +20%, +25%
    results_b = []
    for thr in oi_thresholds:
        fn = make_oi_short_skip(thr, oi_data)
        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=fn, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        name = f"OI > +{thr / 100:.0f}% in 24h → skip SHORT"
        print(f"  {flag} {name:<38s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+8.1f}%  "
              f"{deltas['6m']:+8.1f}%  {deltas['3m']:+8.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results_b.append((name, deltas, avg_dd, positives))

    # ── Combined ──
    print("\n" + "=" * 110)
    print(f"{'COMBINED — best-of-A + best-of-B if any pass':^110}")
    print("=" * 110)
    best_a = max(results_a, key=lambda x: sum(x[1].values()))
    best_b = max(results_b, key=lambda x: sum(x[1].values()))
    print(f"  best A: {best_a[0]}  (pos={best_a[3]}/4, ΔDD avg {best_a[2]:+.2f}pp)")
    print(f"  best B: {best_b[0]}  (pos={best_b[3]}/4, ΔDD avg {best_b[2]:+.2f}pp)")

    # ── Verdict ──
    print("\n" + "=" * 110)
    passers_a = [r for r in results_a if r[3] == 4 and r[2] <= 0.5]
    passers_b = [r for r in results_b if r[3] == 4 and r[2] <= 0.5]

    if passers_a:
        print(f"GATE A PASSING 4/4 strict ({len(passers_a)} config(s)):")
        for name, deltas, avg_dd, _ in passers_a:
            tot = sum(deltas.values())
            print(f"  ✓ {name}: sum Δpnl = {tot:+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
    else:
        print("GATE A: NO config passes 4/4 strict.")

    if passers_b:
        print(f"\nGATE B PASSING 4/4 strict ({len(passers_b)} config(s)):")
        for name, deltas, avg_dd, _ in passers_b:
            tot = sum(deltas.values())
            print(f"  ✓ {name}: sum Δpnl = {tot:+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
    else:
        print("\nGATE B: NO config passes 4/4 strict.")

    if not (passers_a or passers_b):
        print("\n→ No gate ships. Retrospective signals don't survive walk-forward.")
        print("→ Log findings in BACKLOG.md, document the rejected hypotheses.")
    else:
        print("\n→ At least one gate passes 4/4 strict. Pick the most defensible")
        print("  config, implement in trading.py (gate inside rank_and_enter),")
        print("  bump VERSION, document in CHANGELOG and docs/synthese.md.")


if __name__ == "__main__":
    main()
