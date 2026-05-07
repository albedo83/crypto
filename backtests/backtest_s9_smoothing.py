"""Walk-forward — S9 detection smoothing.

Hypothesis (from live vs backtest analysis 2026-05-07): S9 fires on a
single-candle ±20%/24h extreme. The trigger is fragile to sampling timing
(4h backtest vs 1h live scan): the same 24h move can register on the
backtest's 4h grid but be absent from live's hourly snapshot, or vice versa.
Requiring the trigger to PERSIST across N consecutive 4h candles before
firing should:
  - filter one-off extremes that immediately revert (noise)
  - improve cross-engine reproducibility
  - keep the high-conviction signals where the move is sustained

Sweep: persistence ∈ {1=baseline, 2, 3} consecutive candles where
abs(ret_24h) ≥ S9_RET_THRESH AND the SAME sign of ret_24h (so we don't
fire on a bounce in the opposite direction).

Implementation: pre-compute a (coin, ts) → bool map of "S9 trigger has
held for N candles in same direction", then a skip_fn that rejects S9
candidates whose ts is not in the smoothed set.

Usage:
    python3 -m backtests.backtest_s9_smoothing
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS, S9_RET_THRESH,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def precompute_s9_persistence(features: dict, persistence_n: int) -> set[tuple[str, int]]:
    """For each (coin, ts) where S9 would fire, return the subset where the
    trigger held for `persistence_n` consecutive 4h candles in the SAME
    direction.

    persistence_n=1 → baseline (no smoothing, all S9 candidates kept).
    persistence_n=2 → require previous 4h candle also above threshold same dir.
    """
    if persistence_n <= 1:
        return None  # no filter
    smoothed: set[tuple[str, int]] = set()
    for coin, fs in features.items():
        # fs is list of feature dicts ordered by ts; ret_6h represents 24h ret on 4h grid
        recent_dirs: list[int] = []  # +1 / -1 for each candle (0 if not above thresh)
        for f in fs:
            r = f.get("ret_6h", 0) or 0
            ts = f.get("ts") or f.get("t")
            if ts is None:
                continue
            if abs(r) >= S9_RET_THRESH:
                d = 1 if r > 0 else -1  # direction of the move (S9 fades, so trade dir is -d)
                recent_dirs.append(d)
                if len(recent_dirs) >= persistence_n and all(x == d for x in recent_dirs[-persistence_n:]):
                    smoothed.add((coin, int(ts)))
            else:
                recent_dirs.append(0)
            # cap the deque to persistence_n items
            if len(recent_dirs) > persistence_n:
                recent_dirs = recent_dirs[-persistence_n:]
    return smoothed


def make_s9_smooth_filter(smoothed_set):
    """Returns skip_fn: skip S9 candidates whose (coin, ts) is not in the
    smoothed set. All other strategies untouched."""
    if smoothed_set is None:
        return None  # no filter
    def f(coin, ts, strat, direction):
        if strat != "S9":
            return False
        return (coin, int(ts)) not in smoothed_set
    return f


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
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]
    end_ts = latest_ts
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=CAP, oi_data=oi_data, early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline (persistence=1, no smoothing):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        s9 = r["by_strat"].get("S9", {"n": 0, "pnl": 0, "wr": 0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%  "
              f"S9: n={s9['n']:3d} pnl=${s9['pnl']:+.0f} wr={s9['wr']:.0f}%")

    candidates_pers = [2, 3]
    print(f"\nSweep: {len(candidates_pers)} smoothings × {len(WINDOWS)} windows = {len(candidates_pers)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for n in candidates_pers:
        smoothed = precompute_s9_persistence(features, n)
        n_smoothed = len(smoothed) if smoothed else 0
        print(f"\nPersistence={n} ({n_smoothed} smoothed (coin,ts) pairs):")
        rs = {}
        skip_fn = make_s9_smooth_filter(smoothed)
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=skip_fn, **common)
            rs[label] = r
            s9 = r["by_strat"].get("S9", {"n": 0, "pnl": 0, "wr": 0})
            d_pnl = r["pnl_pct"] - baseline[label]["pnl_pct"]
            d_dd = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
            d_n = r["n_trades"] - baseline[label]["n_trades"]
            base_s9 = baseline[label]["by_strat"].get("S9", {"n": 0, "pnl": 0, "wr": 0})
            d_s9_n = s9["n"] - base_s9["n"]
            d_s9_pnl = s9["pnl"] - base_s9["pnl"]
            print(f"  {label}: ΔpnL={d_pnl:+7.1f}pp  ΔDD={d_dd:+5.1f}pp  Δtr={d_n:+3d}  "
                  f"S9: Δn={d_s9_n:+3d} ΔpnL=${d_s9_pnl:+5.0f} wr={s9['wr']:.0f}%")
        results[n] = rs

    print(f"\n{'=' * 100}\n{'4/4 PnL gain & DD intact':^100}\n{'=' * 100}")
    found = []
    for n in candidates_pers:
        d_pnl = [results[n][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [results[n][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((n, d_pnl, d_dd))
    if not found:
        print("  (none passed the 4/4 strict criterion)")
    else:
        for n, d_pnl, d_dd in found:
            print(f"  persistence={n}: avg ΔpnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.1f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # Also report 2/4+ candidates as informational (asymmetric risk/reward like v11.7.16)
    print(f"\n{'=' * 100}\n{'≥ 3/4 windows positive (informational)':^100}\n{'=' * 100}")
    for n in candidates_pers:
        d_pnl = [results[n][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [results[n][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
        positives = sum(1 for p in d_pnl if p > 0)
        if positives >= 3:
            print(f"  persistence={n}: {positives}/4 positive  avg ΔpnL {sum(d_pnl)/4:+.1f}pp  "
                  f"avg ΔDD {sum(d_dd)/4:+.1f}pp")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
