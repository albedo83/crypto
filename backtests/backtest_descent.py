"""Walk-forward — sweep "descent detection" exits.

User observation 2026-05-07: DOGE trajectory bps over time:
  h=12: +180 (peak)  →  h=14: -31  →  h=16: -120  →  h=18: -116  →  h=19: -129
A clear, regular descent over ~7 candles. Currently no mechanism uses
this trajectory shape — only level-based (trailing, give-back, dead_timeout).

Two related but distinct mechanics tested here :

  A) Reversal exit WITHOUT the min_gain_bps filter (the existing one
     requires the trade to be currently profitable to fire). Drops
     this requirement → fires even when the descent continues into red.

  B) "Slope" exit: track bps history per pos, exit if the last K
     candles show a continuous decline > threshold (= what the user
     described visually for DOGE).

Walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_descent
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

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
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

    # ── (A) Reversal exit, allow firing in red ─────────────────────────
    # Use min_gain_bps=-99999 to disable the "must be profitable" filter
    print("\n" + "=" * 100)
    print(f"{'(A) Pure descent detection (reversal_exit no gain floor)':^100}")
    print("=" * 100)
    for K in [1, 2, 3, 4, 6]:
        for adv in [300, 500, 800, 1200]:
            for scope in [("ALL", None), ("S5", ["S5"]), ("S5+S9", ["S5", "S9"])]:
                cfg = dict(lookback_candles=K, adverse_bps=adv, min_gain_bps=-99999)
                if scope[1]: cfg["strategies"] = scope[1]
                name = f"REV {scope[0]:5s} K={K} adv={adv:4d}bps"
                positives, d_pnl, d_dd = run_and_record(name, reversal_exit=cfg)
                if positives >= 3:
                    print(fmt_row(name, d_pnl, d_dd))

    # ── (B) Reversal exit, profit gate at low threshold ────────────────
    print("\n" + "=" * 100)
    print(f"{'(B) Reversal exit with low gain gate (must have shown +X)':^100}")
    print("=" * 100)
    for K in [1, 2, 3, 4]:
        for adv in [300, 500, 800]:
            for gain in [50, 100, 200]:  # require pos was at least +50 to fire
                for scope in [("S5", ["S5"]), ("S5+S9", ["S5", "S9"])]:
                    cfg = dict(lookback_candles=K, adverse_bps=adv,
                               min_gain_bps=gain, strategies=scope[1])
                    name = f"REV {scope[0]:5s} K={K} adv={adv:4d} gain≥{gain}"
                    positives, d_pnl, d_dd = run_and_record(name, reversal_exit=cfg)
                    if positives >= 3:
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
        for name, d_pnl, d_dd in found[:20]:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # Top 10 by sum_pnl
    print("\n" + "=" * 100)
    print(f"{'Top 10 by sum(ΔPnL) — even if not 4/4':^100}")
    print("=" * 100)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:10]:
        d_pnl = list(info["d_pnl"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 else " "
        print(f"  {sign} {name:48s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
