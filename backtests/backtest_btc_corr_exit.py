"""Walk-forward sweep — dynamic exit when BTC moves against the trade.

Hypothesis (from LIVE+PAPER post-hoc): the largest losses cluster when
BTC drops during a LONG hold (alts follow BTC), or when BTC rallies
during a SHORT hold. Exiting positions early when BTC moves X% against
their direction should clip the loss tail.

Different from already-rejected regime-gating-at-entry: this is a
DURING-hold exit rule, not an entry filter.

Tests grid:
  - trigger threshold (BTC move bps): {300, 500, 800, 1000, 1500}
  - lookback (hours of hold to consider): full hold OR first 24h only
  - scope: LONG only / SHORT only / both

The exit fires when BTC return-since-entry crosses the adverse threshold.
Uses the size_fn / extra_candidate_fn machinery is not enough for this —
needs an exit hook. Implements a custom in-script trade simulator that
mirrors run_window's open/close logic minus the BTC-correlation rule.

Cleaner: monkey-patch the exit logic. We add the rule as a "stop-equivalent"
by precomputing BTC returns and invoking skip_fn returning True if exit
condition met. But skip_fn is at ENTRY time. So we need a separate exit
mechanism.

For simplicity, we replicate the inner exit loop from run_window with the
new rule injected. See `simulate_with_btc_exit()` below.

Usage:
    python3 -m backtests.backtest_btc_corr_exit
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


def precompute_btc_arrays(data: dict) -> tuple[list[int], list[float]]:
    """Sorted (ts, close) arrays for BTC, for bisect lookup."""
    btc = sorted(data.get("BTC", []), key=lambda c: c["t"])
    return [c["t"] for c in btc], [c["c"] for c in btc]


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    btc_ts, btc_close = precompute_btc_arrays(data)
    btc_close_by_ts = {t: c for t, c in zip(btc_ts, btc_close)}
    print(f"  BTC candles: {len(btc_ts)}")
    import bisect

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

    print("\nBaseline (with v11.7.28 dispersion gate active, no BTC-corr exit):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # The cleanest way to test the exit is to add a `btc_corr_exit` parameter
    # to run_window. But to avoid touching the source for an experimental
    # sweep, we monkey-patch by wrapping the exit logic. Looking at the engine
    # code (backtest_rolling.py:run_window), the exit conditions are checked
    # in-place inside the inner loop. There's no clean injection point without
    # modifying source.
    #
    # Pragmatic approach: post-process the trade list. After running the
    # baseline backtest, for each trade, compute what would happen if a
    # BTC-corr exit fired at any candle during the hold — recompute pnl as
    # exit-at-trigger-price. This OVER-ESTIMATES the win (because in reality,
    # cutting a position would free a slot for another trade) but gives us a
    # first-order view of whether the rule helps.
    #
    # If first-order helps strongly → next iteration: extend run_window for
    # in-engine simulation.

    def simulate_btc_corr_exit(trades: list[dict],
                               threshold_bps: int,
                               max_lookback_h: int | None,
                               apply_long: bool,
                               apply_short: bool) -> tuple[float, int]:
        """For each trade in `trades`, simulate a BTC-correlated early exit.
        Returns (total_pnl, n_fired)."""
        new_pnl = 0.0
        fired = 0
        for t in trades:
            entry_ts = t["entry_t"]
            exit_ts = t.get("exit_t", entry_ts)
            direction = t["dir"]  # 1 / -1
            size = t["size"]
            entry_px = t.get("entry", 0)
            actual_pnl = t.get("pnl", 0)
            do_long = (direction == 1) and apply_long
            do_short = (direction == -1) and apply_short
            if not (do_long or do_short):
                new_pnl += actual_pnl
                continue
            entry_btc = btc_close_by_ts.get(entry_ts)
            if entry_btc is None:
                new_pnl += actual_pnl
                continue
            i_start = bisect.bisect_right(btc_ts, entry_ts)
            triggered = False
            for i in range(i_start, len(btc_ts)):
                ts = btc_ts[i]
                if ts > exit_ts: break
                hours = (ts - entry_ts) / 3600000
                if max_lookback_h is not None and hours > max_lookback_h:
                    break
                btc_ret_bps = (btc_close[i] / entry_btc - 1) * 1e4
                adverse = (direction == 1 and btc_ret_bps <= -threshold_bps) or \
                          (direction == -1 and btc_ret_bps >= threshold_bps)
                if adverse:
                    alt_unr = direction * btc_ret_bps  # beta=1 first-order
                    new_pnl_t = size * alt_unr / 1e4 - size * 10 / 1e4
                    new_pnl += new_pnl_t
                    fired += 1
                    triggered = True
                    break
            if not triggered:
                new_pnl += actual_pnl
        return new_pnl, fired

    print("\nSweep: BTC-corr exit (first-order post-hoc estimate, beta=1):")
    print(f"{'thr':>5s} {'lookback':>10s} {'scope':<8s}    Δ28m       Δ12m       Δ6m       Δ3m   fired/all  4/4?")
    candidates = []
    for thr in [300, 500, 800, 1000, 1500]:
        for lookback in [None, 24, 12]:
            for scope in [("LONG+SHORT", True, True), ("LONG", True, False), ("SHORT", False, True)]:
                candidates.append((thr, lookback, scope))

    results = {}
    for thr, lookback, (scope_name, do_long, do_short) in candidates:
        key = (thr, lookback, scope_name)
        per_window = {}
        for label, start_ts in window_specs:
            base = baseline[label]
            trades = base.get("trades", [])
            new_pnl, fired = simulate_btc_corr_exit(trades, thr, lookback, do_long, do_short)
            # Compute new pnl_pct from new_pnl. baseline started at $1000.
            new_pnl_pct = new_pnl / CAP * 100
            per_window[label] = (new_pnl_pct, fired, len(trades))
        results[key] = per_window
        d = {lab: per_window[lab][0] - baseline[lab]["pnl_pct"] for lab, _ in window_specs}
        positives = sum(1 for v in d.values() if v > 0)
        fired_total = sum(per_window[lab][1] for lab, _ in window_specs)
        all_total = sum(per_window[lab][2] for lab, _ in window_specs)
        lookback_str = "full" if lookback is None else f"{lookback}h"
        print(f"  {thr:>4d}  {lookback_str:>10s}  {scope_name:<10s}  {d['28m']:+8.1f}  {d['12m']:+8.1f}  "
              f"{d['6m']:+7.1f}  {d['3m']:+6.1f}   {fired_total}/{all_total}  {positives}/4")

    print(f"\n{'=' * 100}")
    print("Note: this is a FIRST-ORDER post-hoc estimate using beta=1 alt-to-BTC.")
    print("The actual alt move during the BTC drop may be larger or smaller. Also")
    print("doesn't account for the freed slot that would let another candidate enter.")
    print("If a robust 4/4 emerges, second iteration: in-engine implementation.")
    print(f"{'=' * 100}")

    found = []
    for key, ws in results.items():
        d_pnl = [ws[lab][0] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        if all(p > 0 for p in d_pnl):
            found.append((key, d_pnl))
    print(f"\nRobust 4/4 candidates:")
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for (thr, lookback, scope), d in found:
            lookback_str = "full" if lookback is None else f"{lookback}h"
            print(f"  thr={thr} lookback={lookback_str} scope={scope}: "
                  f"avg ΔPnL {sum(d)/4:+.1f}pp ({d[0]:+.1f}, {d[1]:+.1f}, {d[2]:+.1f}, {d[3]:+.1f})")


if __name__ == "__main__":
    main()
