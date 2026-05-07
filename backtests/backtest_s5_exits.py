"""Walk-forward — S5 exit improvements (trailing + runner ext + combined).

Hypothesis (from live data analysis 2026-05-07): S5 wins reach avg MFE
+1086 bps but exit at +569 bps — half the gain is given back over the
48h hold. 9 of 37 live trades hit MFE > +300 bps but exited under +100.
The most extreme case (LDO LONG) hit MFE +575 bps then crashed all the
way to a -1262 bps catastrophe stop — a +1837 bps swing wasted.

Three families to test:

  A) Runner extension on S5 (mirror v11.7.32 from S9): at natural
     timeout, if MFE >= X and current ratio of MFE >= Y, push hold +Nh.
  B) Trailing stop on S5 (mirror v11.4.0 from S10): exit at MFE - offset
     once MFE crosses trigger.
  C) Combined: top-A × top-B.

Walk-forward criterion: 4/4 windows positive ΔP&L AND avg ΔDD <= +0.5pp.

Usage:
    python3 -m backtests.backtest_s5_exits
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


def fmt_row(name, deltas_pnl, deltas_dd, baseline, results, label_prefix=""):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {label_prefix}{name:55s}  "
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

    print("\nBaseline (current production config — runner extension on S9 only):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        s5 = r["by_strat"].get("S5", {"n": 0, "pnl": 0, "wr": 0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  S5 n={s5['n']:3d} pnl=${s5['pnl']:+8.0f} wr={s5['wr']:.0f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    # ── (A) Runner extension on S5 ────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(A) Runner extension on S5':^100}")
    print("=" * 100)
    A_candidates = []
    for extra_h in [12, 24, 48]:
        for min_mfe in [300, 500, 800, 1200]:
            for min_ratio in [0.3, 0.5, 0.7]:
                A_candidates.append((
                    f"S5 RX +{extra_h:2d}h MFE>={min_mfe:4d} cur/mfe>={min_ratio}",
                    {"extra_candles": extra_h // 4,
                     "min_mfe_bps": min_mfe,
                     "min_cur_to_mfe": min_ratio,
                     "strategies": {"S5"}},
                ))

    A_results = {}
    for name, cfg in A_candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           runner_extension=cfg, **common)
            rs[label] = r
        A_results[name] = rs
        d_pnl = {lab: rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                 for lab, _ in window_specs}
        d_dd = {lab: rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        if positives >= 2:  # only print decent ones
            print(fmt_row(name, d_pnl, d_dd, baseline, rs))
    all_results.update({f"A:{k}": v for k, v in A_results.items()})

    # ── (B) Trailing stop on S5 ────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(B) Trailing stop on S5':^100}")
    print("=" * 100)
    B_candidates = []
    for trigger in [300, 500, 700, 1000, 1500]:
        for offset in [100, 150, 200, 300]:
            B_candidates.append((
                f"S5 TR trigger>={trigger:4d} offset={offset:3d}",
                {"strategy": "S5", "trigger_bps": trigger, "offset_bps": offset},
            ))

    B_results = {}
    for name, cfg in B_candidates:
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts,
                           trailing_extra=cfg, **common)
            rs[label] = r
        B_results[name] = rs
        d_pnl = {lab: rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                 for lab, _ in window_specs}
        d_dd = {lab: rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                for lab, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        if positives >= 2:
            print(fmt_row(name, d_pnl, d_dd, baseline, rs))
    all_results.update({f"B:{k}": v for k, v in B_results.items()})

    # ── (C) Combined: top-3 of A × top-3 of B ──────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(C) Combined runner_ext + trailing on S5':^100}")
    print("=" * 100)
    def score(rs):
        d = [rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        return sum(d) - 5 * sum(1 for x in d if x < 0)  # penalize negatives
    A_sorted = sorted(A_results.items(), key=lambda kv: -score(kv[1]))[:3]
    B_sorted = sorted(B_results.items(), key=lambda kv: -score(kv[1]))[:3]
    print(f"  top A: {[k for k, _ in A_sorted]}")
    print(f"  top B: {[k for k, _ in B_sorted]}")

    C_results = {}
    for a_name, a_cfg in A_sorted:
        # extract A's cfg from the candidate list
        a_dict = next(c[1] for c in A_candidates if c[0] == a_name)
        for b_name, b_cfg in B_sorted:
            b_dict = next(c[1] for c in B_candidates if c[0] == b_name)
            name = f"A:{a_name[:30]} + B:{b_name[:25]}"
            rs = {}
            for label, start_ts in window_specs:
                r = run_window(features, data, start_ts_ms=start_ts,
                               runner_extension=a_dict, trailing_extra=b_dict, **common)
                rs[label] = r
            C_results[name] = rs
            d_pnl = {lab: rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"]
                     for lab, _ in window_specs}
            d_dd = {lab: rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"]
                    for lab, _ in window_specs}
            print(fmt_row(name, d_pnl, d_dd, baseline, rs))
    all_results.update({f"C:{k}": v for k, v in C_results.items()})

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp)':^100}")
    print("=" * 100)
    found = []
    for name, rs in all_results.items():
        d_pnl = [rs[lab]["pnl_pct"] - baseline[lab]["pnl_pct"] for lab, _ in window_specs]
        d_dd = [rs[lab]["max_dd_pct"] - baseline[lab]["max_dd_pct"] for lab, _ in window_specs]
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

    print(f"\nRuntime: {time.time()-t0:.0f}s  ({len(all_results)} configs tested)")


if __name__ == "__main__":
    main()
