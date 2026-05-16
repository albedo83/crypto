"""2D sweep on slot allocation (macro × token × total).

Tests if a different slot mix beats the v12.6.3 baseline (M=3, T=4, P=6).
Also includes a directional/sectorial dimension sweep at the end.

Strict 4/4 acceptance: ΔPnL > 0 on each window AND avg ΔDD ≤ +1pp.
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


# (label, MAX_MACRO, MAX_TOKEN, MAX_POSITIONS, MAX_SAME_DIR, MAX_PER_SECTOR)
SLOT_CONFIGS = [
    # Slot allocation 2D — sub-cap variants
    ("baseline_v12.6.3",     3, 4, 6, 4, 2),  # ← current
    ("dense_macro",          4, 3, 7, 4, 2),  # more macro, fewer token
    ("dense_token",          3, 5, 8, 4, 2),  # more token, same macro
    ("balanced_4_4_8",       4, 4, 8, 4, 2),  # both up
    ("max_total_4_5_9",      4, 5, 9, 4, 2),  # aggressive
    ("rollback_v12.6.2",     2, 4, 6, 4, 2),  # rollback test (pre-v12.6.3)
    ("token_heavy_2_5_7",    2, 5, 7, 4, 2),
    ("loose_dir_5",          3, 4, 6, 5, 2),  # same slots, +1 same direction
    ("loose_sector_3",       3, 4, 6, 4, 3),  # same slots, +1 per sector
    ("loose_dir+sector",     3, 4, 6, 5, 3),  # both loosened
]


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
    print(f"Configs to test: {len(SLOT_CONFIGS)}", flush=True)

    results = {}
    for cfg in SLOT_CONFIGS:
        label, M, T, P, SD, PS = cfg
        br.MAX_MACRO_SLOTS = M
        br.MAX_TOKEN_SLOTS = T
        br.MAX_POSITIONS = P
        br.MAX_SAME_DIRECTION = SD
        br.MAX_PER_SECTOR = PS
        print(f"\n--- {label}: M={M} T={T} P={P} SameDir={SD} Sect={PS} ---", flush=True)
        for wl, sd in windows:
            r = br.run_window(
                features, data, sector_features, dxy_data,
                int(sd.timestamp() * 1000), latest_ts, start_capital=1000.0,
                oi_data=oi_data, funding_data=funding_data,
                early_exit_params=early_exit_params,
                runner_extension=runner_ext_cfg,
                apply_adaptive_modulator=True,
            )
            results[(label, wl)] = r
            ns1 = r["by_strat"].get("S1", {"n": 0}).get("n", 0)
            print(f"  {wl:<10} → pnl={r['pnl_pct']:+9.1f}% dd={r['max_dd_pct']:+6.1f}% "
                  f"trades={r['n_trades']:>4d}  S1: n={ns1:>3d}", flush=True)

    # Compute deltas vs baseline
    print(f"\n--- VERDICTS vs baseline_v12.6.3 ---")
    lines = []
    lines.append("# Slot allocation 2D sweep — résultats walk-forward 4/4\n")
    lines.append(f"_Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}._\n")
    lines.append("## Baseline (v12.6.3 — M=3, T=4, P=6, SD=4, PS=2)\n")
    lines.append("| Window | PnL % | DD % | Trades |")
    lines.append("|---|---:|---:|---:|")
    for label, _ in windows:
        b = results[("baseline_v12.6.3", label)]
        lines.append(f"| {label} | {b['pnl_pct']:+.1f}% | {b['max_dd_pct']:+.1f}% | {b['n_trades']} |")
    lines.append("")
    lines.append("## Sweep results (vs baseline)\n")
    lines.append("| Config | 28m ΔPnL | 12m ΔPnL | 6m ΔPnL | 3m ΔPnL | avg ΔDD | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")

    for cfg in SLOT_CONFIGS:
        if cfg[0] == "baseline_v12.6.3":
            continue
        label = cfg[0]
        d_pnls, d_dds, n_pass = [], [], 0
        for wl, _ in windows:
            b = results[("baseline_v12.6.3", wl)]
            v = results[(label, wl)]
            dpnl = v["pnl_pct"] - b["pnl_pct"]
            ddd = v["max_dd_pct"] - b["max_dd_pct"]
            if dpnl > 0: n_pass += 1
            d_pnls.append(dpnl); d_dds.append(ddd)
        avg_dpnl = sum(d_pnls) / len(d_pnls)
        avg_ddd = sum(d_dds) / len(d_dds)
        verdict = "✓ PASS 4/4" if (n_pass == 4 and avg_ddd <= 1.0) else f"✗ {n_pass}/4"
        lines.append(f"| {label} | {d_pnls[0]:+.0f}pp | {d_pnls[1]:+.1f}pp | {d_pnls[2]:+.1f}pp | {d_pnls[3]:+.1f}pp | {avg_ddd:+.2f}pp | {verdict} |")
        print(f"  {label:<22}: {n_pass}/4  avg_ΔPnL={avg_dpnl:+.0f}pp  avg_ΔDD={avg_ddd:+.2f}pp  {verdict}")

    lines.append("\n## Notes")
    lines.append("- `SD` = MAX_SAME_DIRECTION, `PS` = MAX_PER_SECTOR")
    lines.append("- Strict criterion: ΔPnL > 0 sur chaque fenêtre + avg ΔDD ≤ +1pp")
    lines.append("- Si un PASS apparaît, ship-eligible après revue mécanique (corrélation, slippage)")

    out = "/home/crypto/backtests/slots_2d_sweep_results.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
