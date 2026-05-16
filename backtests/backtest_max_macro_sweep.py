"""Sweep MAX_MACRO_SLOTS ∈ {2, 3, 4, 5} on the 4 rolling windows.

Tests whether opening more macro slots (currently 2) lets the bot capture
more S1 trades without degrading drawdown. Mirrors the user's frustration
with the live bot missing PENDLE/COMP S1 (slot full) on 2026-05-04.

Strict 4/4 acceptance criterion (mirror v11.10.0 / v12.5.30 ship gates):
  - ΔPnL_pct > 0 on EACH of {28m, 12m, 6m, 3m}
  - avg ΔDD across the 4 windows ≤ +1pp

Outputs: backtests/max_macro_sweep_results.md
"""
from __future__ import annotations

import datetime as dt
import sys
from datetime import datetime, timezone

from backtests import backtest_rolling as br
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)


SWEEP_VALUES = [2, 3, 4, 5]  # 2 = current baseline


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

    # Rolling window definitions (28m / 12m / 6m / 3m anchored to today)
    end = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = [
        ("28 mois", end - dt.timedelta(days=28 * 30)),
        ("12 mois", end - dt.timedelta(days=12 * 30)),
        ("6 mois",  end - dt.timedelta(days=6 * 30)),
        ("3 mois",  end - dt.timedelta(days=3 * 30)),
    ]
    print(f"Windows: {[w[0] for w in windows]}", flush=True)
    print(f"Sweep MAX_MACRO_SLOTS: {SWEEP_VALUES}", flush=True)

    # Run each sweep value × each window
    results = {}  # (slots, window_label) → dict
    for slots in SWEEP_VALUES:
        br.MAX_MACRO_SLOTS = slots  # monkey-patch the module constant
        print(f"\n--- MAX_MACRO_SLOTS = {slots} ---", flush=True)
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
            n_s1 = r["by_strat"].get("S1", {"n": 0, "pnl": 0}).get("n", 0)
            pnl_s1 = r["by_strat"].get("S1", {"n": 0, "pnl": 0}).get("pnl", 0)
            results[(slots, label)] = r
            print(f"  {label:<10} → pnl={r['pnl_pct']:+8.1f}% dd={r['max_dd_pct']:+6.1f}% "
                  f"trades={r['n_trades']:>4d}  S1: n={n_s1:>3d} ${pnl_s1:+.0f}", flush=True)

    # Compute deltas vs baseline (slots=2)
    print(f"\n--- VERDICTS vs baseline (slots=2) ---")
    lines = []
    lines.append("# MAX_MACRO_SLOTS sweep — résultats walk-forward 4/4\n")
    lines.append(f"_Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}._\n")
    lines.append(f"Data ends: {end_dt.isoformat()}\n")
    lines.append(f"Baseline: MAX_MACRO_SLOTS = 2 (production). "
                 f"Sweep tests {SWEEP_VALUES} on 28m/12m/6m/3m. "
                 f"Capital $1000. apply_adaptive_modulator=True.\n")
    lines.append("## Critère d'acceptation strict 4/4")
    lines.append("- ΔPnL_pct > 0 sur **chacune** des 4 fenêtres")
    lines.append("- avg ΔDD_pct ≤ +1pp\n")

    lines.append("## Baseline (MAX_MACRO_SLOTS=2)\n")
    lines.append("| Window | PnL % | DD % | Trades | S1 n | S1 pnl $ |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label, _ in windows:
        b = results[(2, label)]
        s1 = b["by_strat"].get("S1", {"n": 0, "pnl": 0})
        lines.append(f"| {label} | {b['pnl_pct']:+.1f}% | {b['max_dd_pct']:+.1f}% | {b['n_trades']} | {s1.get('n',0)} | {s1.get('pnl',0):+.0f} |")

    for slots in SWEEP_VALUES:
        if slots == 2:
            continue
        lines.append(f"\n## MAX_MACRO_SLOTS = {slots}\n")
        lines.append("| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | S1 n | ΔS1 n | S1 pnl $ | ΔS1 pnl |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        d_pnls, d_dds, n_pass = [], [], 0
        for label, _ in windows:
            b = results[(2, label)]
            v = results[(slots, label)]
            dpnl = v["pnl_pct"] - b["pnl_pct"]
            ddd = v["max_dd_pct"] - b["max_dd_pct"]
            dtr = v["n_trades"] - b["n_trades"]
            bs1 = b["by_strat"].get("S1", {"n": 0, "pnl": 0})
            vs1 = v["by_strat"].get("S1", {"n": 0, "pnl": 0})
            ds1n = vs1.get("n", 0) - bs1.get("n", 0)
            ds1p = vs1.get("pnl", 0) - bs1.get("pnl", 0)
            if dpnl > 0:
                n_pass += 1
            d_pnls.append(dpnl)
            d_dds.append(ddd)
            lines.append(
                f"| {label} | {v['pnl_pct']:+.1f}% | **{dpnl:+.1f}pp** | "
                f"{v['max_dd_pct']:+.1f}% | {ddd:+.1f}pp | "
                f"{v['n_trades']} | {dtr:+d} | {vs1.get('n',0)} | {ds1n:+d} | "
                f"{vs1.get('pnl',0):+.0f} | {ds1p:+.0f} |"
            )
        avg_dpnl = sum(d_pnls) / len(d_pnls)
        avg_ddd = sum(d_dds) / len(d_dds)
        verdict = "**✓ PASS 4/4 strict**" if (n_pass == 4 and avg_ddd <= 1.0) else (
            f"**✗ FAIL ({n_pass}/4 ΔPnL > 0, avg ΔDD = {avg_ddd:+.2f}pp)**")
        lines.append(f"\nAvg ΔPnL: {avg_dpnl:+.1f}pp  ·  Avg ΔDD: {avg_ddd:+.2f}pp  ·  {verdict}")
        print(f"  slots={slots}: pass={n_pass}/4  avg_ΔPnL={avg_dpnl:+.1f}pp  avg_ΔDD={avg_ddd:+.2f}pp  {verdict}")

    lines.append("\n## Lecture\n")
    lines.append("Augmenter MAX_MACRO_SLOTS au-delà de 2 fait passer plus de signaux S1 — "
                 "mais ces trades supplémentaires consomment des slots qui auraient pu aller à "
                 "des trades token (S5/S8/S9/S10). Le critère 4/4 strict détermine si le compromis "
                 "est favorable sur les 4 fenêtres simultanément.\n")
    lines.append("**Si PASS** : pré-register un ship (modifier `MAX_MACRO_SLOTS` dans `config.py`, "
                 "bump VERSION, restart). Backtest a déjà validé le delta net.\n")
    lines.append("**Si FAIL** : la frustration sur le S1 raté (PENDLE 04-05) est une "
                 "observation court-terme. Sur 28m+12m+6m+3m de backtest, les slots macro "
                 "à 2 sont déjà l'optimum (ou très proche).\n")

    out = "/home/crypto/backtests/max_macro_sweep_results.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
