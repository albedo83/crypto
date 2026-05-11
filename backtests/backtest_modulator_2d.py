"""Walk-forward 4/4 strict validation of a 2D modulator extension.

Tests whether `mult = 1 + α × btc_z + β × disp_z` beats the current
v12.2.0 modulator (`mult = 1 + α × btc_z`) on 4 windows: 28m / 12m / 6m / 3m.

α coefficients are fixed at the current v12.2.0 values (from
config.ADAPTIVE_ALPHA + ADAPTIVE_ALPHA_DIR). β is swept across:
  - per-strategy values (applied independently to S5, S9, S10 fades and
    optionally to S1 trend)
  - {-0.5, -0.3, -0.1, +0.1, +0.3, +0.5}

A config "passes" if all 4 windows show positive Δpnl AND average ΔDD ≤ +0.5pp.

This is the rigor missing from the earlier retrospective analyze_2d_regime.py.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    MACRO_Z_CLIP, MACRO_MULT_MIN, MACRO_MULT_MAX,
    ADAPTIVE_ALPHA, ADAPTIVE_ALPHA_DIR, get_adaptive_alpha,
    TRADE_SYMBOLS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy

CAP = 1000.0


def compute_disp_z_rolling(features: dict, btc_data: list,
                            lookback_days: int = 30,
                            z_window_days: int = 180) -> dict:
    """Cross-sectional dispersion z-score per timestamp, no look-ahead.

    disp_24h[ts] = std across all alts of feat["ret_6h"] (which is the 24h
    return on 4h candles). Then rolling z-score of disp_24h over the past
    z_window_days using only data ≤ ts.

    Mirrors the structure of compute_btc_z_rolling.
    """
    # Step 1: time series of disp_24h, indexed by ts
    disp_by_ts: dict[int, float] = {}
    # Build a {ts: [ret_6h per coin]} pre-aggregation
    by_ts: dict[int, list[float]] = {}
    for coin, feats in features.items():
        if coin == "BTC":
            continue
        for f in feats:
            if "ret_6h" in f:
                by_ts.setdefault(f["t"], []).append(f["ret_6h"])
    # disp at each ts = std across alts
    sorted_ts = sorted(by_ts.keys())
    for ts in sorted_ts:
        rets = by_ts[ts]
        if len(rets) >= 10:
            disp_by_ts[ts] = float(np.std(rets))

    # Step 2: rolling z-score on disp_by_ts time series
    n_z = z_window_days * 6
    out: dict[int, float] = {}
    ts_seq = sorted(disp_by_ts.keys())
    vals_seq = [disp_by_ts[t] for t in ts_seq]
    for j in range(len(ts_seq)):
        win_start = max(0, j - n_z)
        past = vals_seq[win_start:j + 1]
        if len(past) < 30:
            continue
        m = float(np.mean(past))
        s = float(np.std(past)) or 1.0
        out[ts_seq[j]] = (vals_seq[j] - m) / s
    return out


def compute_btc_z_rolling(data: dict, lookback_days: int = 30,
                           z_window_days: int = 180) -> dict:
    """Identical to backtest_adaptive_robustness — copied to keep this
    script self-contained."""
    btc = data["BTC"]
    n_lb = lookback_days * 6
    n_z = z_window_days * 6
    closes = np.array([c["c"] for c in btc])
    rets_history, ts_history = [], []
    for i in range(n_lb, len(btc)):
        ret = (closes[i] / closes[i - n_lb] - 1) if closes[i - n_lb] > 0 else 0
        rets_history.append(ret)
        ts_history.append(btc[i]["t"])
    out = {}
    for j in range(len(rets_history)):
        win_start = max(0, j - n_z)
        past = rets_history[win_start:j + 1]
        if len(past) < 30:
            continue
        m = float(np.mean(past))
        s = float(np.std(past)) or 1.0
        out[ts_history[j]] = (rets_history[j] - m) / s
    return out


def make_2d_fn(beta_vec: dict, btc_z_map: dict, disp_z_map: dict):
    """size_fn that applies BOTH the canonical 1D modulator (using
    ADAPTIVE_ALPHA / ADAPTIVE_ALPHA_DIR) AND a per-strategy β × disp_z term.

    Final multiplier: clip(1 + α × btc_z + β × disp_z, MIN, MAX)
    """
    def fn(cand, f, n_pos):
        ts = f["t"]
        z_btc = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(ts, 0.0)))
        z_disp = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, disp_z_map.get(ts, 0.0)))
        alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        beta = beta_vec.get(cand["strat"], 0.0)
        m = 1.0 + alpha * z_btc + beta * z_disp
        return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, m))
    return fn


def make_1d_baseline_fn(btc_z_map: dict):
    """size_fn that applies only the current v12.2.0 1D modulator —
    serves as the apples-to-apples baseline for our walk-forward Δ measure."""
    def fn(cand, f, n_pos):
        z_btc = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(f["t"], 0.0)))
        alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        m = 1.0 + alpha * z_btc
        return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, m))
    return fn


def main() -> None:
    print("Loading 3y candles...")
    t0 = time.time()
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()
    print(f"  loaded in {time.time() - t0:.1f}s")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    print("Computing btc_z_rolling and disp_z_rolling...")
    btc_z = compute_btc_z_rolling(data, lookback_days=30, z_window_days=180)
    disp_z = compute_disp_z_rolling(features, data["BTC"],
                                     lookback_days=30, z_window_days=180)
    print(f"  btc_z: {len(btc_z)} ts, disp_z: {len(disp_z)} ts")

    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    common = dict(
        sector_features=sector_features, dxy_data=dxy_data,
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
        end_ts_ms=latest_ts,
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    # ── Baseline: current v12.2.0 1D modulator ──
    print("\n" + "=" * 110)
    print(f"{'BASELINE — current v12.2.0 1D modulator (α × btc_z only)':^110}")
    print("=" * 110)
    print(f"  ADAPTIVE_ALPHA      = {ADAPTIVE_ALPHA}")
    print(f"  ADAPTIVE_ALPHA_DIR  = {ADAPTIVE_ALPHA_DIR}\n")
    baseline_fn = make_1d_baseline_fn(btc_z)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, size_fn=baseline_fn, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # ── β sweep ──
    print("\n" + "=" * 110)
    print(f"{'β SWEEP — 2D modulator mult = 1 + α × btc_z + β × disp_z':^110}")
    print("=" * 110)
    print("  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<48s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>8s}  pos")

    configs = [
        # β only on S5 SHORT (where the retrospective showed most signal)
        ("β[S5_SHORT]=+0.3 only",     {"S5": +0.3}, -1),   # only when direction=-1
        ("β[S5_SHORT]=+0.5 only",     {"S5": +0.5}, -1),
        ("β[S5_SHORT]=-0.3 only",     {"S5": -0.3}, -1),
        ("β[S5_SHORT]=-0.5 only",     {"S5": -0.5}, -1),
        # β on all S5 (both directions)
        ("β[S5]=+0.3 (both dirs)",    {"S5": +0.3}, None),
        ("β[S5]=-0.3 (both dirs)",    {"S5": -0.3}, None),
        # β on fade strats (S5 + S9 + S10)
        ("β[S5,S9,S10]=+0.3 (fades+)", {"S5": +0.3, "S9": +0.3, "S10": +0.3}, None),
        ("β[S5,S9,S10]=-0.3 (fades-)", {"S5": -0.3, "S9": -0.3, "S10": -0.3}, None),
        ("β[S5,S9,S10]=+0.5",          {"S5": +0.5, "S9": +0.5, "S10": +0.5}, None),
        ("β[S5,S9,S10]=-0.5",          {"S5": -0.5, "S9": -0.5, "S10": -0.5}, None),
        # Opposite on S1 (trend) vs S5/S9 (mean rev)
        ("β[S1]=-0.3 β[S5,S9]=+0.3",   {"S1": -0.3, "S5": +0.3, "S9": +0.3}, None),
        ("β[S1]=+0.3 β[S5,S9]=-0.3",   {"S1": +0.3, "S5": -0.3, "S9": -0.3}, None),
        # All strats same direction
        ("β[ALL]=+0.3",                {"S1": +0.3, "S5": +0.3, "S8": +0.3, "S9": +0.3, "S10": +0.3}, None),
        ("β[ALL]=-0.3",                {"S1": -0.3, "S5": -0.3, "S8": -0.3, "S9": -0.3, "S10": -0.3}, None),
    ]

    results = []
    for name, beta, dir_filter in configs:
        # Build directional β if requested
        if dir_filter is not None:
            def make_2d_fn_dir(beta_vec, dir_filter):
                def fn(cand, f, n_pos):
                    ts = f["t"]
                    z_btc = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z.get(ts, 0.0)))
                    z_disp = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, disp_z.get(ts, 0.0)))
                    alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
                    beta_eff = beta_vec.get(cand["strat"], 0.0) if cand["dir"] == dir_filter else 0.0
                    m = 1.0 + alpha * z_btc + beta_eff * z_disp
                    return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, m))
                return fn
            size_fn = make_2d_fn_dir(beta, dir_filter)
        else:
            size_fn = make_2d_fn(beta, btc_z, disp_z)

        deltas = {}
        ddds = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common)
            deltas[label] = r['pnl_pct'] - baseline[label]['pnl_pct']
            ddds[label] = r['max_dd_pct'] - baseline[label]['max_dd_pct']
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {flag} {name:<46s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+8.1f}%  "
              f"{deltas['6m']:+8.1f}%  {deltas['3m']:+8.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_dd, positives))

    # ── Summary ──
    print("\n" + "=" * 110)
    passers = [r for r in results if r[3] == 4 and r[2] <= 0.5]
    if passers:
        print(f"PASSING 4/4 strict ({len(passers)} config(s)):")
        for name, deltas, avg_dd, _ in passers:
            tot = sum(deltas.values())
            print(f"  ✓ {name}: sum Δpnl = {tot:+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
        print("\nNext step: ship the most defensible passer in a new modulator")
        print("version (update get_adaptive_beta or extend the formula in trading.py).")
    else:
        print("NO config passes 4/4 strict.")
        print("→ The 2D extension does NOT generalize across windows. Keep 1D modulator.")
        print("→ Log finding in BACKLOG.md, close the Option 1 item.")


if __name__ == "__main__":
    main()
