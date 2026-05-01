"""Walk-forward sweep — session/weekend filter on entries.

Hypothesis from LIVE+PAPER 123-trade post-hoc: S5 entries on the weekend
have WR 28% / avg -$8.59 / total -$215 across the combined sample, while
S5 on weekday sessions is uniformly positive (WR 57-100%). Other strats
on the weekend are neutral. Pattern is S5-specific.

Tests several session-based skip rules to see if any survives the 4
walk-forward reference windows.

Note: the existing rejection log mentions "Sessions Asia/EU/US — rejete"
but that was a generic split, not a strategy-specific filter. This sweep
is more surgical.

Usage:
    python3 -m backtests.backtest_session_filter
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
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


# ── Session classifiers (UTC) ────────────────────────────────────────

def _weekday_hour(ts_ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.weekday(), dt.hour  # weekday: Mon=0 .. Sun=6


def is_weekend(ts_ms: int) -> bool:
    """Sat (5) + Sun (6) = full weekend in UTC."""
    return _weekday_hour(ts_ms)[0] >= 5


def is_thin_window(ts_ms: int) -> bool:
    """Fri 21:00 UTC → Sun 23:59 UTC. Captures Friday evening US close + full weekend."""
    wd, h = _weekday_hour(ts_ms)
    if wd >= 5:  # Sat / Sun
        return True
    if wd == 4 and h >= 21:  # Fri ≥ 21h
        return True
    return False


def is_saturday(ts_ms: int) -> bool:
    return _weekday_hour(ts_ms)[0] == 5


def is_sunday(ts_ms: int) -> bool:
    return _weekday_hour(ts_ms)[0] == 6


def is_night(ts_ms: int) -> bool:
    """21h UTC to 8h UTC = "Night" session label used in entry_session."""
    h = _weekday_hour(ts_ms)[1]
    return h >= 21 or h < 8


# ── Skip-fn factories ────────────────────────────────────────────────

def skip_strat_when(strat_target: str, when_fn):
    def f(coin, ts, strat, direction):
        return strat == strat_target and when_fn(ts)
    f.__name__ = f"skip_{strat_target}_{when_fn.__name__}"
    return f


def skip_all_when(when_fn):
    def f(coin, ts, strat, direction):
        return when_fn(ts)
    f.__name__ = f"skip_all_{when_fn.__name__}"
    return f


# ── Main ─────────────────────────────────────────────────────────────

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
    print(f"Data ends at {end_dt.isoformat()}")

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    window_specs = []
    for label, months in WINDOWS:
        start_dt = end_dt - relativedelta(months=months)
        window_specs.append((label, int(start_dt.timestamp() * 1000)))
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

    print("\nBaseline (no session filter):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  best={r['best_strat']}")

    # All combinations to test
    candidates = [
        ("skip S5 weekend", skip_strat_when("S5", is_weekend)),
        ("skip S5 thin (Fri21+WE)", skip_strat_when("S5", is_thin_window)),
        ("skip S5 Sat only", skip_strat_when("S5", is_saturday)),
        ("skip S5 Sun only", skip_strat_when("S5", is_sunday)),
        ("skip S5 Night (21-8h UTC)", skip_strat_when("S5", is_night)),
        ("skip ALL weekend", skip_all_when(is_weekend)),
        ("skip ALL thin", skip_all_when(is_thin_window)),
        ("skip S8 weekend", skip_strat_when("S8", is_weekend)),
        ("skip S9 weekend", skip_strat_when("S9", is_weekend)),
        ("skip S10 weekend", skip_strat_when("S10", is_weekend)),
    ]

    print(f"\nSweep: {len(candidates)} filters × {len(WINDOWS)} windows = {len(candidates)*len(WINDOWS)} runs")
    t0 = time.time()
    results = {}
    for name, fn in candidates:
        results[name] = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, skip_fn=fn, **common)
            results[name][label] = r
        d = {lab: results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        print(f"  {name:35s}  Δ28m={d['28m']:+7.1f}  Δ12m={d['12m']:+7.1f}  "
              f"Δ6m={d['6m']:+6.1f}  Δ3m={d['3m']:+5.1f}  {positives}/4")

    # Final report
    print(f"\n{'=' * 100}")
    print(f"{'PER-FILTER COMPARISON (vs baseline)':^100}")
    print(f"{'=' * 100}")
    print(f"  {'filter':<35s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>9s}  verdict")
    for name, _ in candidates:
        d_pnl = {lab: results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs}
        d_dd = {lab: results[name][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        avg_dd = sum(d_dd.values()) / len(d_dd)
        tag = "4/4 ✓" if positives == 4 else f"{positives}/4"
        if positives == 4 and avg_dd <= 0.5:
            tag = "4/4 ★"
        print(f"  {name:<35s}  {d_pnl['28m']:+8.1f}  {d_pnl['12m']:+8.1f}  "
              f"{d_pnl['6m']:+8.1f}  {d_pnl['3m']:+8.1f}  {avg_dd:+8.1f}  {tag}")

    # Robust (4/4) summary
    print(f"\nRobust 4/4 filters:")
    robust = [name for name, _ in candidates
              if all(results[name][lab]["pnl_pct"] > baseline[lab]["pnl_pct"]
                     for lab, _ in window_specs)]
    if not robust:
        print("  (none — no session filter passes the walk-forward)")
    else:
        for name in robust:
            d_pnl = [results[name][lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
            d_dd = [results[name][lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
            avg_pnl = sum(d_pnl) / 4
            avg_dd = sum(d_dd) / 4
            print(f"  {name}: avg ΔPnL {avg_pnl:+.1f}pp / avg ΔDD {avg_dd:+.1f}pp")
    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
