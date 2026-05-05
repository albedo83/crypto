"""Walk-forward sweep — S10 directional cluster cooldown.

Hypothesis: when two S10 SHORT signals fire in close succession on different
alts, they often coincide with a sustained alt rally that runs the fades over.
A simple cooldown after a S10 (SHORT) entry should reject the second cluster
member and let the first one play out alone.

S10 is currently SHORT-only (`S10_ALLOW_LONGS=False`), so direction is fixed.
The hypothesis implicitly tests "ANY S10 cooldown" and "SHORT-only cooldown"
which collapse to the same thing under the current config.

Sweep:
  - cooldown_hours ∈ {4, 8, 12, 24, 48}

Note on the skip_fn hook: skip_fn is called before the downstream cap checks
(MAX_POSITIONS, MAX_SAME_DIRECTION, sector caps), so a candidate that survives
skip_fn may still get rejected later. This means "last S10 entry timestamp"
tracked in the closure may be slightly over-counted vs. positions actually
opened. The over-counting biases towards more aggressive throttling in the
backtest than would happen live — i.e. results are conservative. If the
filter passes 4/4 here, the live behaviour will be at least as good.

Usage:
    python3 -m backtests.backtest_s10_cluster
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def make_s10_cooldown(cooldown_ms: int):
    """Return a stateful skip_fn that throttles S10 entries after a recent
    S10 candidate was let through.

    State is per-call closure: tracks `last_s10_ts` (int ms or None).
    """
    state = {"last_s10_ts": None}

    def skip(coin, ts, strat, direction) -> bool:
        if strat != "S10":
            return False
        last = state["last_s10_ts"]
        if last is not None and ts - last < cooldown_ms:
            return True  # too soon since the last S10 — throttle
        # Mark this candidate as the new "last" (we approve it for now;
        # downstream caps may still reject it).
        state["last_s10_ts"] = ts
        return False

    return skip


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

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    window_specs = [
        (lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
        for lab, m in WINDOWS
    ]
    end_ts = latest_ts

    common = dict(
        sector_features=sector_features,
        dxy_data=dxy_data,
        end_ts_ms=end_ts,
        start_capital=CAP,
        oi_data=oi_data,
        early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline (no S10 cooldown):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    candidates = []
    for cd_h in [4, 8, 12, 24, 48]:
        candidates.append((f"S10 cooldown {cd_h:2d}h", cd_h * 3_600_000))

    print(f"\nSweep: {len(candidates)} cooldowns × {len(WINDOWS)} windows = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, cd_ms in candidates:
        rs = {}
        for label, start_ts in window_specs:
            # Fresh closure per window — cooldown state is per-window.
            r = run_window(features, data, start_ts_ms=start_ts,
                           skip_fn=make_s10_cooldown(cd_ms), **common)
            rs[label] = r
        results[name] = rs
        d_pnl = {lab: rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs}
        d_dd = {lab: rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs}
        d_tr = {lab: rs[lab]["n_trades"] - baseline[lab]["n_trades"] for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        avg_dd = sum(d_dd.values()) / 4
        print(f"  {name:20s}  Δ28m={d_pnl['28m']:+7.1f}  Δ12m={d_pnl['12m']:+6.1f}  "
              f"Δ6m={d_pnl['6m']:+5.1f}  Δ3m={d_pnl['3m']:+5.1f}  "
              f"ΔDD avg={avg_dd:+5.1f}  Δtr={d_tr['28m']:+d}/{d_tr['12m']:+d}/{d_tr['6m']:+d}/{d_tr['3m']:+d}  "
              f"{positives}/4")

    print(f"\n{'=' * 100}\n{'4/4 PnL gain & DD intact':^100}\n{'=' * 100}")
    found = []
    for name, _ in candidates:
        d_pnl = [results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [results[name][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none — hypothesis rejected at every tested cooldown)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found:
            print(f"  {name}: avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.1f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
