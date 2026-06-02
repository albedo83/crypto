"""Walk-forward strict — S9 in-life MFE trail.

Mirror of v12.5.30 S8 in-life trail, applied to S9.
Rule: when pos.mfe_bps >= trigger AND unrealized <= mfe - offset → exit.

Grid sweep on (trigger, offset). Walk-forward 4 splits 6m non-overlapping.
PASS = strict 4/4 splits where (ΔPnL > 0 AND ΔDD >= -2pp) per config.

Usage: python3 -m backtests.backtest_s9_inlife_trail
"""
from __future__ import annotations

from datetime import datetime, timezone

import backtests.backtest_genetic as bg
import analysis.bot.config as bc


SPLITS = [
    ("split_1  2024-06 → 2024-12", datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    ("split_2  2024-12 → 2025-06", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    ("split_3  2025-06 → 2025-12", datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    ("split_4  2025-12 → 2026-06", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]

START_CAP = 500.0
DD_TOL = 2.0  # ΔDD tolerance (pp)

# Grid: 5 × 5 = 25 configs
TRIGGERS = [600, 800, 1000, 1200, 1500]   # bps MFE threshold to arm the trail
OFFSETS  = [200, 400, 600, 800, 1000]      # bps drawdown from MFE that exits


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding, trail_cfg=None):
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
        trailing_extra=trail_cfg,
    )


def main():
    data, features, sector_feats, dxy, oi, funding = reload_data()

    # --- baseline (no S9 trail) ---
    print("=" * 84)
    print("BASELINE  (no S9 in-life trail)")
    print("=" * 84)
    baseline = {}
    for name, sd, ed in SPLITS:
        r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, trail_cfg=None)
        baseline[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                          "pnl": r["pnl"]}
        print(f"  {name}  ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  n={r['n_trades']:4d}  $={r['pnl']:+,.0f}", flush=True)

    # --- grid sweep ---
    results = []  # one entry per config
    total = len(TRIGGERS) * len(OFFSETS)
    idx = 0
    for trigger in TRIGGERS:
        for offset in OFFSETS:
            idx += 1
            cfg = {"strategy": "S9", "trigger_bps": trigger, "offset_bps": offset}
            per_split = {}
            roi_pass = 0
            dd_pass = 0
            sum_dpnl = 0.0
            sum_ddd = 0.0
            print(f"\n[{idx}/{total}] S9 trail trigger={trigger} offset={offset}", flush=True)
            for name, sd, ed in SPLITS:
                r = run_split(sd, ed, features, data, sector_feats, dxy, oi, funding, trail_cfg=cfg)
                b = baseline[name]
                d_roi = r["pnl_pct"] - b["roi"]
                d_dd = r["max_dd_pct"] - b["dd"]
                d_pnl = r["pnl"] - b["pnl"]
                per_split[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                                    "pnl": r["pnl"], "d_roi": d_roi, "d_dd": d_dd, "d_pnl": d_pnl}
                roi_ok = d_pnl > 0  # dollar-PnL improvement (path-dependence safer than ROI%)
                dd_ok = d_dd >= -DD_TOL
                if roi_ok: roi_pass += 1
                if dd_ok: dd_pass += 1
                sum_dpnl += d_pnl
                sum_ddd += d_dd
                tag = "✓" if (roi_ok and dd_ok) else "✗"
                print(f"   {name}  ROI={r['pnl_pct']:+8.1f}%  ΔPnL=${d_pnl:+9.0f}  ΔDD={d_dd:+6.2f}pp  {tag}", flush=True)
            results.append({
                "trigger": trigger, "offset": offset,
                "per_split": per_split,
                "roi_pass": roi_pass, "dd_pass": dd_pass,
                "sum_dpnl": sum_dpnl, "avg_ddd": sum_ddd / 4,
            })

    # --- ranking ---
    print()
    print("=" * 84)
    print("RANKING — sorted by (4/4 PASS first, then sum ΔPnL)")
    print("=" * 84)
    results.sort(key=lambda r: (-(r["roi_pass"] == 4 and r["dd_pass"] == 4), -r["sum_dpnl"]))
    print(f"{'Trigger':>7} {'Offset':>7} {'ROI 4/4':>9} {'DD 4/4':>9} {'sum ΔPnL':>12} {'avg ΔDD':>10}  Verdict")
    for r in results:
        strict_pass = (r["roi_pass"] == 4 and r["dd_pass"] == 4)
        verdict = "STRICT PASS 4/4" if strict_pass else f"FAIL ({r['roi_pass']}/4 ROI, {r['dd_pass']}/4 DD)"
        print(f"{r['trigger']:>7} {r['offset']:>7} {r['roi_pass']:>9}/4 {r['dd_pass']:>9}/4 {r['sum_dpnl']:>+11,.0f}$ {r['avg_ddd']:>+9.2f}pp  {verdict}")

    # --- top-3 detail ---
    print()
    print("=" * 84)
    print("TOP-3 DETAIL")
    print("=" * 84)
    for r in results[:3]:
        print(f"\n  S9 trail trigger={r['trigger']} offset={r['offset']}  sum_ΔPnL=${r['sum_dpnl']:+,.0f}  avg_ΔDD={r['avg_ddd']:+.2f}pp")
        for name, _, _ in SPLITS:
            s = r["per_split"][name]
            print(f"    {name}  ROI={s['roi']:+8.1f}%  ΔPnL=${s['d_pnl']:+9.0f}  ΔDD={s['d_dd']:+6.2f}pp  n={s['n']}")


if __name__ == "__main__":
    main()
