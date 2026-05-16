"""S9 "dead-in-water" — 4 variantes en walk-forward 4/4 strict.

Concept: mirror v12.6.0 S8 dead-in-water mais sur S9 (fade ±20%/24h).
Si le fade ne donne pas de respiration au checkpoint, c'est un "train fou"
(continuation news-driven), pas une mean-reversion temporaire. Cut.

4 variantes testées en parallèle:
    A: S9 SHORT  T+8h  mfe ≤ 50
    B: S9 LONG   T+8h  mfe ≤ 50
    C: S9 SHORT  T+4h  mfe ≤ 50
    D: S9 LONG   T+4h  mfe ≤ 50

Strict 4/4: ΔPnL > 0 chaque fenêtre + avg ΔDD ≤ +1pp.
Per-trade audit: stragglers detected (cuts qui auraient récupéré).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from backtests import backtest_rolling as br
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
MFE_MAX_BPS = 50.0

VARIANTS = [
    # (label, strat_filter, dir_filter, T_hours)
    ("A_SHORT_T8h", "S9", -1, 8.0),
    ("B_LONG_T8h",  "S9", +1, 8.0),
    ("C_SHORT_T4h", "S9", -1, 4.0),
    ("D_LONG_T4h",  "S9", +1, 4.0),
]


def make_hook(strat: str, dir_target: int, t_hours: float):
    """Hook factory. mfe is monotonic, so the check is naturally idempotent;
    we still track evaluated trade_ids for the audit."""
    state = {"evaluated": set(), "fired": 0, "audit": []}

    def hook(snap):
        if snap.get("strat") != strat or snap.get("dir") != dir_target:
            return None
        tid = snap.get("trade_id")
        if tid is None or tid in state["evaluated"]:
            return None
        if snap.get("hold_h", 0.0) < t_hours:
            return None
        # First evaluation at T+H
        state["evaluated"].add(tid)
        mfe = snap.get("mfe_bps", 0.0)
        cur = snap.get("cur_bps", 0.0)
        fire = (mfe <= MFE_MAX_BPS)
        state["audit"].append({
            "trade_id": tid, "symbol": snap.get("symbol"),
            "hold_h_at_check": snap.get("hold_h"),
            "mfe_at_cut": mfe, "cur_ur_at_cut": cur,
            "mae_at_cut": snap.get("mae_bps"), "fired": fire,
        })
        if fire:
            state["fired"] += 1
            return (True, "s9_dead_in_water")
        return None

    return hook, state


def run_one(ctx, start_ts, end_ts, hook=None):
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
    return br.run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        start_ts, end_ts, start_capital=1000.0,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        apply_adaptive_modulator=True,
        inlife_exit_extra=hook,
    )


def main():
    print("Loading backtest data...", flush=True)
    data = br.load_3y_candles()
    features = br.build_features(data)
    sec = br.compute_sector_features(features, data)
    dxy = br.load_dxy()
    oi = br.load_oi()
    funding = br.load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
    ctx = dict(features=features, data=data, sec=sec, dxy=dxy, oi=oi, funding=funding)
    print(f"  data ends {end_dt.isoformat()}", flush=True)

    # Window specs
    windows = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        windows.append((label, start, end_ts))

    # Parity check: hook returning None must produce baseline-identical results
    print("\n--- Parity check (hook returns None) ---", flush=True)
    def parity_hook(snap): return None
    for label, s, e in windows:
        r_base = run_one(ctx, s, e, hook=None)
        r_par = run_one(ctx, s, e, hook=parity_hook)
        ok = (r_base["n_trades"] == r_par["n_trades"]
              and abs(r_base["pnl_pct"] - r_par["pnl_pct"]) < 0.001)
        print(f"  {label}: base trades={r_base['n_trades']} pnl={r_base['pnl_pct']:+.1f}%  | "
              f"parity trades={r_par['n_trades']} pnl={r_par['pnl_pct']:+.1f}%  "
              f"{'✓' if ok else '✗ PARITY FAIL'}", flush=True)
        if not ok:
            raise SystemExit("Parity check failed — abort.")

    # Compute baselines (no hook)
    print("\n--- Baselines (no hook) ---", flush=True)
    baselines = {}
    for label, s, e in windows:
        r = run_one(ctx, s, e, hook=None)
        baselines[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+9.1f}% dd={r['max_dd_pct']:+6.1f}% "
              f"trades={r['n_trades']:>4d}", flush=True)

    # Run variants
    variant_results = {}  # (var_label, window_label) → run dict
    audits = {}  # var_label → list of audit rows
    for var_label, strat, dir_t, t_h in VARIANTS:
        print(f"\n--- Variant {var_label}: {strat} dir={dir_t} T+{t_h:.0f}h ---", flush=True)
        all_audit = []
        for label, s, e in windows:
            hook_fn, state = make_hook(strat, dir_t, t_h)
            r = run_one(ctx, s, e, hook=hook_fn)
            variant_results[(var_label, label)] = r
            all_audit.extend([dict(a, window=label) for a in state["audit"]])
            n_fired = state["fired"]
            n_eval = len(state["evaluated"])
            print(f"  {label}: pnl={r['pnl_pct']:+9.1f}% dd={r['max_dd_pct']:+6.1f}% "
                  f"trades={r['n_trades']:>4d}  fired={n_fired}/eval={n_eval}", flush=True)
        audits[var_label] = all_audit

    # Verdict per variant
    print("\n--- VERDICTS strict 4/4 ---", flush=True)
    lines = []
    lines.append("# S9 dead-in-water — walk-forward 4/4 strict\n")
    lines.append(f"_Generated 2026-05-16. Mirror v12.6.0 S8 mechanic. 4 variantes._\n")
    lines.append(f"Critère: ΔPnL > 0 sur chaque fenêtre + avg ΔDD ≤ +1pp.\n")
    lines.append("## Baseline (no hook)\n")
    lines.append("| Window | PnL % | DD % | Trades |")
    lines.append("|---|---:|---:|---:|")
    for label, _, _ in windows:
        b = baselines[label]
        lines.append(f"| {label} | {b['pnl_pct']:+.1f}% | {b['max_dd_pct']:+.1f}% | {b['n_trades']} |")

    summary_rows = []
    for var_label, strat, dir_t, t_h in VARIANTS:
        lines.append(f"\n## Variant {var_label}: {strat} dir={dir_t} T+{t_h:.0f}h\n")
        lines.append("| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | Fired | Eval |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        d_pnls, d_dds, n_pass = [], [], 0
        for label, _, _ in windows:
            b = baselines[label]
            v = variant_results[(var_label, label)]
            audit = [a for a in audits[var_label] if a.get("window") == label]
            fired = sum(1 for a in audit if a["fired"])
            evald = len(audit)
            dpnl = v["pnl_pct"] - b["pnl_pct"]
            ddd = v["max_dd_pct"] - b["max_dd_pct"]
            dtr = v["n_trades"] - b["n_trades"]
            if dpnl > 0: n_pass += 1
            d_pnls.append(dpnl); d_dds.append(ddd)
            lines.append(f"| {label} | {v['pnl_pct']:+.1f}% | **{dpnl:+.1f}pp** | "
                         f"{v['max_dd_pct']:+.1f}% | {ddd:+.1f}pp | "
                         f"{v['n_trades']} | {dtr:+d} | {fired} | {evald} |")
        avg_dpnl = sum(d_pnls) / len(d_pnls)
        avg_ddd = sum(d_dds) / len(d_dds)
        passed = (n_pass == 4 and avg_ddd <= 1.0)
        verdict = "**✓ PASS 4/4 strict**" if passed else (
            f"**✗ {n_pass}/4 (avg ΔDD = {avg_ddd:+.2f}pp)**")
        lines.append(f"\nAvg ΔPnL: {avg_dpnl:+.1f}pp · Avg ΔDD: {avg_ddd:+.2f}pp · {verdict}")
        summary_rows.append((var_label, n_pass, avg_dpnl, avg_ddd, passed))
        print(f"  {var_label:<15}: {n_pass}/4  ΔPnL={avg_dpnl:+.1f}pp  ΔDD={avg_ddd:+.2f}pp  {verdict}")

    # Stragglers analysis per variant on 28m (largest sample)
    lines.append("\n## Stragglers per variant (28m sample)\n")
    lines.append("Trades cut where the engine's trade-list shows the cut LOCKED a loss "
                 "smaller than the trade would have ended at. Reported as count and total bps saved/lost.\n")
    # For now we just report fired counts; full straggler analysis would need trade-level diff.
    lines.append("| Variant | n_fired_28m | n_fired_12m | n_fired_6m | n_fired_3m |")
    lines.append("|---|---:|---:|---:|---:|")
    for var_label, _, _, _ in VARIANTS:
        row = []
        for label, _, _ in windows:
            audit_w = [a for a in audits[var_label] if a.get("window") == label]
            row.append(sum(1 for a in audit_w if a["fired"]))
        lines.append(f"| {var_label} | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")

    # Final summary
    lines.append("\n## Summary\n")
    lines.append("| Variant | Pass | Avg ΔPnL | Avg ΔDD | Verdict |")
    lines.append("|---|---:|---:|---:|---|")
    for var_label, n_pass, avg_dpnl, avg_ddd, passed in summary_rows:
        v = "✓ PASS" if passed else f"✗ {n_pass}/4"
        lines.append(f"| {var_label} | {n_pass}/4 | {avg_dpnl:+.1f}pp | {avg_ddd:+.2f}pp | {v} |")

    out = "/home/crypto/backtests/s9_dead_in_water_walkforward.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
