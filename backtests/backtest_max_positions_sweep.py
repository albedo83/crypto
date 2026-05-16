"""Sweep MAX_POSITIONS ∈ {6, 7, 8, 9} on the 4 rolling windows.

Tests whether opening more total simultaneous positions improves PnL
without degrading drawdown. v12.6.3 set MAX_MACRO_SLOTS=3 keeping
MAX_TOKEN_SLOTS=4, so MAX_POSITIONS=6 is now the binding cap (3+3=6).
This sweep loosens that cap.

NOTE on degenerate cases: with macro=3 and token=4 sub-caps fixed,
the effective cap is min(MAX_POSITIONS, 3+4=7). So results for
{7, 8, 9} should be identical (sub-caps bind once MAX_POSITIONS ≥ 7).
That's expected — the sweep verifies it empirically and confirms 7 is
the new theoretical max under current sub-caps.

Strict 4/4 acceptance:
  - ΔPnL_pct > 0 on EACH of {28m, 12m, 6m, 3m}
  - avg ΔDD across the 4 windows ≤ +1pp
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone

from backtests import backtest_rolling as br
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)


SWEEP_VALUES = [6, 7, 8, 9]  # 6 = current baseline


def main():
    print("Loading backtest data...", flush=True)
    data = br.load_3y_candles()
    features = br.build_features(data)
    sector_features = br.compute_sector_features(features, data)
    dxy_data = br.load_dxy()
    oi_data = br.load_oi()
    funding_data = br.load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data ends {end_dt.isoformat()}", flush=True)

    # Mirror v12.6.3 sub-caps
    br.MAX_MACRO_SLOTS = 3
    br.MAX_TOKEN_SLOTS = 4
    print(f"Sub-caps fixed: MAX_MACRO_SLOTS=3, MAX_TOKEN_SLOTS=4", flush=True)

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    end = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = [
        ("28 mois", end - dt.timedelta(days=28 * 30)),
        ("12 mois", end - dt.timedelta(days=12 * 30)),
        ("6 mois",  end - dt.timedelta(days=6 * 30)),
        ("3 mois",  end - dt.timedelta(days=3 * 30)),
    ]
    print(f"Windows: {[w[0] for w in windows]}", flush=True)
    print(f"Sweep MAX_POSITIONS: {SWEEP_VALUES}", flush=True)

    results = {}
    for cap in SWEEP_VALUES:
        br.MAX_POSITIONS = cap
        print(f"\n--- MAX_POSITIONS = {cap} ---", flush=True)
        for label, start_dt in windows:
            start_ms = int(start_dt.timestamp() * 1000)
            r = br.run_window(
                features, data, sector_features, dxy_data,
                start_ms, latest_ts, start_capital=1000.0,
                oi_data=oi_data, funding_data=funding_data,
                early_exit_params=early_exit_params,
                runner_extension=runner_ext_cfg,
                apply_adaptive_modulator=True,
            )
            results[(cap, label)] = r
            n_s1 = r["by_strat"].get("S1", {"n": 0, "pnl": 0}).get("n", 0)
            print(f"  {label:<10} → pnl={r['pnl_pct']:+9.1f}% dd={r['max_dd_pct']:+6.1f}% "
                  f"trades={r['n_trades']:>4d}  S1: n={n_s1:>3d}", flush=True)

    print(f"\n--- VERDICTS vs baseline (MAX_POSITIONS=6) ---")
    lines = []
    lines.append("# MAX_POSITIONS sweep — résultats walk-forward 4/4\n")
    lines.append(f"_Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}._\n")
    lines.append(f"Sub-caps fixed: MAX_MACRO_SLOTS=3, MAX_TOKEN_SLOTS=4. ")
    lines.append(f"Effective cap = min(MAX_POSITIONS, 3+4=7), so results for ≥7 should be identical.\n")
    lines.append("## Critère 4/4 strict")
    lines.append("- ΔPnL_pct > 0 sur chaque fenêtre")
    lines.append("- avg ΔDD ≤ +1pp\n")

    lines.append("## Baseline (MAX_POSITIONS=6)\n")
    lines.append("| Window | PnL % | DD % | Trades | S1 n |")
    lines.append("|---|---:|---:|---:|---:|")
    for label, _ in windows:
        b = results[(6, label)]
        s1 = b["by_strat"].get("S1", {"n": 0})
        lines.append(f"| {label} | {b['pnl_pct']:+.1f}% | {b['max_dd_pct']:+.1f}% | {b['n_trades']} | {s1.get('n',0)} |")

    for cap in SWEEP_VALUES:
        if cap == 6:
            continue
        lines.append(f"\n## MAX_POSITIONS = {cap}\n")
        lines.append("| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        d_pnls, d_dds, n_pass = [], [], 0
        for label, _ in windows:
            b = results[(6, label)]
            v = results[(cap, label)]
            dpnl = v["pnl_pct"] - b["pnl_pct"]
            ddd = v["max_dd_pct"] - b["max_dd_pct"]
            dtr = v["n_trades"] - b["n_trades"]
            if dpnl > 0: n_pass += 1
            d_pnls.append(dpnl); d_dds.append(ddd)
            lines.append(f"| {label} | {v['pnl_pct']:+.1f}% | **{dpnl:+.1f}pp** | "
                         f"{v['max_dd_pct']:+.1f}% | {ddd:+.1f}pp | "
                         f"{v['n_trades']} | {dtr:+d} |")
        avg_dpnl = sum(d_pnls) / len(d_pnls)
        avg_ddd = sum(d_dds) / len(d_dds)
        verdict = "**✓ PASS 4/4 strict**" if (n_pass == 4 and avg_ddd <= 1.0) else (
            f"**✗ FAIL ({n_pass}/4 ΔPnL > 0, avg ΔDD = {avg_ddd:+.2f}pp)**")
        lines.append(f"\nAvg ΔPnL: {avg_dpnl:+.1f}pp · Avg ΔDD: {avg_ddd:+.2f}pp · {verdict}")
        print(f"  cap={cap}: {n_pass}/4 pass, avg_ΔPnL={avg_dpnl:+.1f}pp, avg_ΔDD={avg_ddd:+.2f}pp  {verdict}")

    out = "/home/crypto/backtests/max_positions_sweep_results.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
