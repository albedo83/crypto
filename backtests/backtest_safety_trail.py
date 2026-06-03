"""Backtest — safety trailing stop at X% of MFE, armed after 8h.

User request: try to save positions that "go off the rails" mid-life.
Trail logic: once hold_h >= 8, if mfe_bps >= 100 (some real upside reached),
exit when cur_bps <= mfe_bps × trail_ratio.

trail_ratio = 0.30 → cut when 70% of peak gain has been given back (laxiste)
trail_ratio = 0.50 → cut when 50% given back (médian)
trail_ratio = 0.70 → cut when 30% given back (restrictif, sort vite)

Window: 12 last months (2025-06-01 → 2026-06-01).
Capital: $1000 (matches /backtest skill).

Usage: python3 -m backtests.backtest_safety_trail
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

import backtests.backtest_genetic as bg


START_DT = datetime(2025, 6, 1, tzinfo=timezone.utc)
END_DT = datetime(2026, 6, 1, tzinfo=timezone.utc)
START_CAP = 1000.0
ARM_HOURS = 8.0
MIN_MFE_BPS = 100.0  # need real upside before trail arms
TRAIL_RATIOS = [0.30, 0.50, 0.70]


def make_hook(trail_ratio: float, counter: dict):
    """Build an inlife_exit_extra hook with closure over trail_ratio.

    Mutates `counter` to track fires per strategy.
    """
    def hook(snap):
        if snap["hold_h"] < ARM_HOURS:
            return None
        mfe = snap["mfe_bps"]
        if mfe < MIN_MFE_BPS:
            return None
        cur = snap["cur_bps"]
        threshold = mfe * trail_ratio
        if cur <= threshold:
            counter[snap["strat"]] = counter.get(snap["strat"], 0) + 1
            counter["_total"] = counter.get("_total", 0) + 1
            return (True, f"safety_trail_{int(trail_ratio*100)}")
        return None
    return hook


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run(features, data, sector_feats, dxy, oi, funding, hook=None):
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(START_DT.timestamp() * 1000),
        end_ts_ms=int(END_DT.timestamp() * 1000),
        start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
        inlife_exit_extra=hook,
    )


def summarize_by_strategy(trades):
    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1
    return dict(by_strat)


def count_reasons(trades):
    by_reason = defaultdict(int)
    by_reason_pnl = defaultdict(float)
    for t in trades:
        r = t.get("exit_reason", "?")
        by_reason[r] += 1
        by_reason_pnl[r] += t["pnl"]
    return dict(by_reason), dict(by_reason_pnl)


def main():
    print(f"\nWindow: {START_DT.date()} → {END_DT.date()}  (12 months)")
    print(f"Start capital: ${START_CAP:.0f}")
    print(f"Armed at hold_h >= {ARM_HOURS}h, MFE floor = {MIN_MFE_BPS} bps\n")

    data, features, sector_feats, dxy, oi, funding = reload_data()

    print("=" * 84)
    print("BASELINE (no safety trail)")
    print("=" * 84)
    base = run(features, data, sector_feats, dxy, oi, funding, hook=None)
    base_by_strat = summarize_by_strategy(base["trades"])
    base_reasons, base_reasons_pnl = count_reasons(base["trades"])
    print(f"  ROI={base['pnl_pct']:+8.1f}%  DD={base['max_dd_pct']:6.1f}%  "
          f"n={base['n_trades']:4d}  $={base['pnl']:+,.0f}")
    print(f"\n  By strategy:")
    for strat, s in sorted(base_by_strat.items()):
        wr = 100 * s["wins"] / max(1, s["n"])
        print(f"    {strat:>3}  n={s['n']:>3}  ${s['pnl']:>+8.0f}  WR={wr:5.1f}%")

    print(f"\n  Top exit reasons (baseline):")
    for r, n in sorted(base_reasons.items(), key=lambda x: -x[1])[:8]:
        print(f"    {r:<22} n={n:>3}  ${base_reasons_pnl[r]:>+9.0f}")

    print()
    results = []
    for ratio in TRAIL_RATIOS:
        counter = {}
        hook = make_hook(ratio, counter)
        print("=" * 84)
        print(f"SAFETY TRAIL — exit when cur <= {int(ratio*100)}% × MFE  "
              f"(give back <= {int((1-ratio)*100)}%)")
        print("=" * 84)
        r = run(features, data, sector_feats, dxy, oi, funding, hook=hook)
        by_strat = summarize_by_strategy(r["trades"])
        reasons, reasons_pnl = count_reasons(r["trades"])
        d_pnl = r["pnl"] - base["pnl"]
        d_roi = r["pnl_pct"] - base["pnl_pct"]
        d_dd = r["max_dd_pct"] - base["max_dd_pct"]
        d_n = r["n_trades"] - base["n_trades"]
        print(f"  ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  "
              f"n={r['n_trades']:4d}  $={r['pnl']:+,.0f}")
        tag_pnl = "OK" if d_pnl > 0 else "KO"
        tag_dd = "OK" if d_dd >= -2.0 else "KO"
        print(f"  vs baseline:  dPnL={d_pnl:+,.0f}$ [{tag_pnl}]  "
              f"dROI={d_roi:+.1f}pp  dDD={d_dd:+.2f}pp [{tag_dd}]  dn={d_n:+d}")
        print(f"  safety_trail fires: {counter.get('_total', 0)} total")
        for strat in sorted([k for k in counter if k != "_total"]):
            print(f"    {strat:>3}: {counter[strat]} fires")
        print(f"\n  By strategy (with trail):")
        for strat, s in sorted(by_strat.items()):
            base_s = base_by_strat.get(strat, {"n": 0, "pnl": 0.0, "wins": 0})
            wr = 100 * s["wins"] / max(1, s["n"])
            d = s["pnl"] - base_s["pnl"]
            print(f"    {strat:>3}  n={s['n']:>3}  ${s['pnl']:>+8.0f}  "
                  f"WR={wr:5.1f}%  dvsBase=${d:>+8.0f}")
        print()
        results.append({
            "ratio": ratio, "r": r, "d_pnl": d_pnl, "d_dd": d_dd,
            "fires": counter.get('_total', 0), "by_strat": by_strat,
        })

    print("=" * 84)
    print("SUMMARY")
    print("=" * 84)
    print(f"{'Trail':>8} {'ROI':>10} {'DD':>8} {'dPnL':>10} {'dDD':>8} "
          f"{'fires':>7}  Verdict")
    for x in [{"ratio": 0.0, "r": base, "d_pnl": 0.0, "d_dd": 0.0, "fires": 0}] + results:
        verdict = ""
        if x["ratio"] > 0:
            verdict = "KEEP" if (x["d_pnl"] > 0 and x["d_dd"] >= -2.0) else "REJECT"
        label = "baseline" if x["ratio"] == 0 else f"{int(x['ratio']*100)}%"
        print(f"{label:>8} {x['r']['pnl_pct']:>+9.1f}% {x['r']['max_dd_pct']:>+7.1f}% "
              f"{x['d_pnl']:>+9,.0f}$ {x['d_dd']:>+7.2f}pp {x['fires']:>7d}  {verdict}")


if __name__ == "__main__":
    main()
