"""Walk-forward 4/4 strict tests for crowding and confluence skip gates.

Closes the section 1 cleanup: the two remaining observation features
(entry_crowding, entry_confluence) had WEAK retrospective signal but
weren't run through walk-forward. This script tests skip gates that
match the worst retrospective bucket (crowding=0, confluence=1).

Pass condition: all 4 Δpnl > 0 AND average ΔDD ≤ +0.5pp.

Note on premium: live bot's compute_crowding_score uses premium too
(15-point contribution), but premium isn't loaded in the backtest data.
Crowding scores here are at worst 15 points below the live calculation
when premium would have been negative — pattern should be preserved.

Run: python3 -m backtests.backtest_crowding_confluence_gates
"""

from __future__ import annotations

import time
from collections import defaultdict
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


def _oi_delta_1h_pct(oi_data, coin, ts_ms):
    """OI delta over 1h in pct (1 4h-candle granularity → approximation:
    we use the 4h delta divided by 4 to estimate 1h, since that's what
    market_snapshots stores hourly in live)."""
    pts = oi_data.get(coin)
    if not pts or len(pts) < 2:
        return None
    times = [p[0] for p in pts]
    from bisect import bisect_right
    i = bisect_right(times, ts_ms) - 1
    if i < 1:
        return None
    oi_now = pts[i][1]
    oi_prev = pts[i - 1][1]  # 4h before
    if oi_prev <= 0:
        return None
    # 4h % change; live measures 1h but 4h is what we have at candle granularity
    return (oi_now / oi_prev - 1) * 100


def _funding_at(funding_data, coin, ts_ms):
    """Funding rate (fraction, e.g. -0.00005) at ts_ms. None if no data."""
    if coin not in funding_data:
        return None
    ts_arr, rate_arr = funding_data[coin]
    i = np.searchsorted(ts_arr, ts_ms, side="right") - 1
    if i < 0:
        return None
    return float(rate_arr[i])


def _crowding_score(funding, oi_delta_1h, vol_z):
    """Mirror of analysis.bot.features.compute_crowding_score, omitting
    the premium component (not loaded in backtest)."""
    s = 0
    if oi_delta_1h is not None:
        if oi_delta_1h < -1.0:
            s += 30
        if oi_delta_1h < -3.0:
            s += 20
    if funding is not None and funding < -0.00005:
        s += 20
    if vol_z is not None and vol_z > 1.5:
        s += 15
    return min(100, s)


def _confluence(f, n_stress, oi_delta_1h):
    """Mirror of bot._scan_and_trade confluence computation."""
    if f is None:
        return 0
    return int(sum([
        abs(f.get("drawdown", 0)) > 3000,
        f.get("vol_z", 0) > 1.5,
        abs(f.get("ret_24h", f.get("ret_6h", 0))) > 200,
        n_stress >= 5,
        (oi_delta_1h or 0) < -1.0,
    ]))


def precompute_crowding_confluence(features, oi_data, funding_data):
    """Return dict[(coin, ts)] = (crowding, confluence) for all (coin, ts)
    pairs available in features."""
    # First compute n_stress per ts (cross-sectional)
    by_ts: dict[int, list] = defaultdict(list)
    for coin, feats in features.items():
        if coin == "BTC":
            continue
        for f in feats:
            by_ts[f["t"]].append((coin, f))

    n_stress_by_ts: dict[int, int] = {}
    for ts, coin_feats in by_ts.items():
        n_stress = 0
        for coin, f in coin_feats:
            vol_z = f.get("vol_z", 0)
            drawdown = abs(f.get("drawdown", 0))
            if vol_z > 1.5 and drawdown > 1500:
                n_stress += 1
        n_stress_by_ts[ts] = n_stress

    # Now compute crowding/confluence per (coin, ts)
    result = {}
    for ts, coin_feats in by_ts.items():
        n_stress = n_stress_by_ts[ts]
        for coin, f in coin_feats:
            oi_d = _oi_delta_1h_pct(oi_data, coin, ts)
            fund = _funding_at(funding_data, coin, ts)
            vol_z = f.get("vol_z", 0)
            crowd = _crowding_score(fund, oi_d, vol_z)
            conf = _confluence(f, n_stress, oi_d)
            result[(coin, ts)] = (crowd, conf)
    return result


def make_skip_fn(maps, gate_kind: str, threshold):
    """Skip_fn factory. gate_kind in {'crowd_lt', 'crowd_eq', 'conf_eq',
    'conf_lt'} with the threshold meaning the comparator's right side."""
    def fn(coin, ts, strat, dir_):
        v = maps.get((coin, ts))
        if v is None:
            return False
        crowd, conf = v
        if gate_kind == "crowd_lt":
            return crowd < threshold
        if gate_kind == "crowd_eq":
            return crowd == threshold
        if gate_kind == "conf_eq":
            return conf == threshold
        if gate_kind == "conf_lt":
            return conf < threshold
        return False
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

    print("Precomputing crowding/confluence maps...")
    t1 = time.time()
    maps = precompute_crowding_confluence(features, oi_data, funding_data)
    print(f"  {len(maps)} (coin, ts) pairs in {time.time() - t1:.1f}s")

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
        apply_adaptive_modulator=True,
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    # Baseline
    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 adaptive modulator (no extra gate)':^110}")
    print("=" * 110)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # Test configs
    configs = [
        # Crowding gates
        ("crowding < 1   (skip crowd=0)",   "crowd_lt", 1),
        ("crowding < 20",                    "crowd_lt", 20),
        ("crowding < 30",                    "crowd_lt", 30),
        # Confluence gates
        ("confluence == 0 (skip)",           "conf_eq", 0),
        ("confluence == 1 (skip worst)",     "conf_eq", 1),
        ("confluence < 2",                   "conf_lt", 2),
    ]

    print("\n" + "=" * 110)
    print(f"{'WALK-FORWARD — crowding & confluence skip gates':^110}")
    print("=" * 110)
    print(f"  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<40s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>8s}  pos")

    results = []
    for name, kind, thr in configs:
        skip_fn = make_skip_fn(maps, kind, thr)
        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=skip_fn, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {flag} {name:<38s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+8.1f}%  "
              f"{deltas['6m']:+8.1f}%  {deltas['3m']:+8.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_dd, positives))

    # Verdict
    print("\n" + "=" * 110)
    passers = [r for r in results if r[3] == 4 and r[2] <= 0.5]
    if passers:
        print(f"{len(passers)} config(s) pass 4/4 strict:")
        for name, deltas, avg_dd, _ in passers:
            print(f"  ✓ {name}: sum Δpnl = {sum(deltas.values()):+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
    else:
        print("NO config passes 4/4 strict.")
        print("→ Crowding and confluence gates: rejected. Section 1 closed.")


if __name__ == "__main__":
    main()
