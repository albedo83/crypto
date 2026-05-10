"""Backtest: would auto-closing positions on low estimated WR improve PnL?

Tests the hypothesis: when an open position's historical-pattern WR estimate
drops into the alarm zone (e.g., < 25%), close it immediately instead of
waiting for stop_loss / dead_timeout. Compares to baseline (no auto-close).

Walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Trade-off:
  - Save losses on trades that would have hit catastrophe stop
  - Cut some winners that would have recovered (the X% of low-WR trades that win)

Usage:
    python3 -m backtests.backtest_wr_autoclose
"""
from __future__ import annotations

import time
from collections import defaultdict
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
    return (f"  {sign} {name:55s}  "
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
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
    )

    print("\nBaseline (no auto-close):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts,
                       early_exit_params=early_exit, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        if "early_exit_params" not in kwargs:
            kwargs["early_exit_params"] = early_exit
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # Auto-close approximation: use early_mfe_exit which exits when MFE hasn't
    # crossed a threshold by hour H. This is the closest proxy in the existing
    # backtest engine to "low WR = exit" since both fire on dead trades.
    # We sweep across various (hours_held, mfe_threshold, mae_threshold) tuples
    # that approximate the "WR < X" intuition.
    print("\n" + "=" * 110)
    print(f"{'AUTO-CLOSE PROXY — exit when MAE deep AND MFE low (like WR alarm)':^110}")
    print("=" * 110)

    # The existing early_mfe_exit only checks MFE. We need a custom approach.
    # Since run_window doesn't have a direct "exit on WR" hook, we approximate
    # via early_mfe_exit + tighter dead_timeout combos.
    print("\n--- (A) Tighter dead_timeout (proxy for catching low-WR trades) ---")
    for lead_h in [12, 16, 20, 24, 30]:
        for mfe_cap in [100, 150, 200, 300]:
            for mae_floor in [-400, -500, -600, -800]:
                cfg = dict(
                    exit_lead_candles=int(lead_h // 4),
                    mfe_cap_bps=mfe_cap,
                    mae_floor_bps=mae_floor,
                    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
                )
                name = f"DT lead={lead_h}h mfe≤{mfe_cap} mae≤{mae_floor}"
                positives, d_pnl, d_dd = run_and_record(
                    name, early_exit_params=cfg)
                if positives >= 3 or sum(d_pnl.values()) > 100:
                    print(fmt_row(name, d_pnl, d_dd))

    print("\n--- (B) Aggressive early_mfe_exit on losing strategies (S5, S9) ---")
    for h in [8, 12, 16, 20]:
        for mfe_min in [0, 50, 100, 150]:
            for strats_label, strats in [("S5", {"S5"}), ("S9", {"S9"}), ("S5+S9", {"S5", "S9"})]:
                cfg = {"check_after_candles": int(h // 4), "mfe_min_bps": mfe_min,
                       "strategies": strats}
                name = f"early_mfe @h{h:2d} mfe<{mfe_min} on {strats_label:5s}"
                positives, d_pnl, d_dd = run_and_record(name, early_mfe_exit=cfg)
                if positives >= 3:
                    print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 strict winners ──────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^110}")
    print("=" * 110)
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
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # ── Top by sum ──────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 15 by sum(ΔPnL)':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:15]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:55s}  sum={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
