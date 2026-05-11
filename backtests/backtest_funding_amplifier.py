"""Walk-forward 4/4 strict validation of a funding-rate amplifier on top
of the v12.2.0 1D modulator.

Hypothesis: when the funding rate of a coin is anomalously high (longs paying
shorts heavily), shorting is favored — mean-reversion + favorable carry.
Symmetrically when funding is very negative.

Test formula:
    mult = clip(1 + α × btc_z + γ × funding_z × (-direction_sign), 0.3, 2.5)

Where:
- α is the existing per-(strat, dir) coefficient (v12.2.0 baseline).
- γ is the new per-strat coefficient swept here.
- funding_z is a **robust** rolling z-score (median + MAD), per the reviewer's
  hint: a single funding spike on Hyperliquid would otherwise produce delirious
  classical z-scores and crash size to the $10 floor.
- The `-direction_sign` term means high funding amplifies SHORTs and dampens
  LONGs (and symmetrically) — encodes "going against the paying crowd".

Pass condition: all 4 Δpnl > 0 AND average ΔDD ≤ +0.5pp on 28m/12m/6m/3m.

Run: python3 -m backtests.backtest_funding_amplifier
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    MACRO_Z_CLIP, MACRO_MULT_MIN, MACRO_MULT_MAX,
    get_adaptive_alpha, TRADE_SYMBOLS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy,
)

CAP = 1000.0

# Z-score windows (mirrors btc_z to keep coefficients comparable)
FUND_LOOKBACK_DAYS = 30        # "current" funding = avg of last 30d hourly samples (smoother than spot)
FUND_Z_WINDOW_DAYS = 180

# Funding spikes can be huge — robust z-score using MAD already protects, but
# clip the z value just in case to keep the multiplier bounded with the std α.
FUNDING_Z_CLIP = 2.5


def compute_btc_z_rolling(data: dict, lookback_days: int = 30,
                           z_window_days: int = 180) -> dict:
    """Copy of the canonical compute_btc_z_rolling — kept here so the script
    is self-contained."""
    btc = data["BTC"]
    n_lb = lookback_days * 6
    closes = np.array([c["c"] for c in btc])
    rets_history, ts_history = [], []
    for i in range(n_lb, len(btc)):
        ret = (closes[i] / closes[i - n_lb] - 1) if closes[i - n_lb] > 0 else 0
        rets_history.append(ret)
        ts_history.append(btc[i]["t"])
    n_z = z_window_days * 6
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


def compute_funding_z_robust(funding_data: dict, btc_candles: list,
                              lookback_days: int = FUND_LOOKBACK_DAYS,
                              z_window_days: int = FUND_Z_WINDOW_DAYS) -> dict:
    """Robust rolling z-score of per-coin funding rate, using median + MAD.

    Returns dict[(coin, ts_ms)] = z_value.

    For each 4h candle ts, computes:
      current_rate = mean of hourly funding rates over the past `lookback_days`
      window       = list of similar 30d averages over the past `z_window_days`
      z            = (current_rate - median(window)) / (1.4826 × MAD(window))

    The 1.4826 factor makes MAD comparable to std for normal distributions.
    median + MAD instead of mean + std: a single funding spike doesn't
    contaminate the scale, so the multiplier doesn't crash to the $10 floor
    on noise.
    """
    ms_per_day = 86400 * 1000
    lookback_ms = lookback_days * ms_per_day
    window_ms = z_window_days * ms_per_day

    candle_ts = [c["t"] for c in btc_candles]
    out = {}
    for coin in TRADE_SYMBOLS:
        if coin not in funding_data:
            continue
        ts_arr, rate_arr = funding_data[coin]
        if len(ts_arr) < 100:
            continue

        # For each candle ts, build "current rate" = mean over (ts - lookback, ts],
        # then z-score against history of similar means over (ts - window, ts].
        # We iterate sequentially through candle_ts (sorted), but for each ts
        # we still need to slice the funding array — use binary search.
        for ts in candle_ts:
            # Current rate = mean of funding over last lookback_days
            lo_lb = np.searchsorted(ts_arr, ts - lookback_ms, side="left")
            hi_lb = np.searchsorted(ts_arr, ts, side="right")
            if hi_lb - lo_lb < 10:
                continue
            current_rate = float(np.mean(rate_arr[lo_lb:hi_lb]))

            # History of past `lookback_days`-window means at past timestamps
            # Compute it cheap: just use the daily funding rates over the window
            lo_w = np.searchsorted(ts_arr, ts - window_ms, side="left")
            hi_w = hi_lb
            past = rate_arr[lo_w:hi_w]
            if len(past) < 100:
                continue
            med = float(np.median(past))
            mad = float(np.median(np.abs(past - med)))
            if mad <= 0:
                continue
            z = (current_rate - med) / (mad * 1.4826)
            out[(coin, ts)] = max(-FUNDING_Z_CLIP, min(FUNDING_Z_CLIP, z))
    return out


def make_funding_fn(gamma_vec: dict, btc_z_map: dict, funding_z_map: dict):
    """Size_fn applying both the canonical 1D modulator and the funding
    amplifier. Formula:

        mult = clip(1 + α × btc_z + γ × funding_z × (-dir_sign), 0.3, 2.5)

    γ > 0 amplifies "trading against the paying crowd":
      - High funding + SHORT  → mult > 1  (longs paying, mean-reversion bet)
      - Low funding  + LONG   → mult > 1  (shorts paying, mean-reversion bet)
      - High funding + LONG   → mult < 1  (joining the paying crowd, dampen)
      - Low funding  + SHORT  → mult < 1  (joining the paying crowd, dampen)
    """
    def fn(cand, f, n_pos):
        ts = f["t"]
        coin = cand["coin"]
        dir_sign = cand["dir"]
        z_btc = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(ts, 0.0)))
        z_fund_raw = funding_z_map.get((coin, ts), 0.0)
        z_fund_signed = z_fund_raw * (-dir_sign)
        alpha = get_adaptive_alpha(cand["strat"], dir_sign)
        gamma = gamma_vec.get(cand["strat"], 0.0)
        m = 1.0 + alpha * z_btc + gamma * z_fund_signed
        return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, m))
    return fn


def make_baseline_fn(btc_z_map: dict):
    """v12.2.0 baseline — α × btc_z only."""
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

    print("Computing btc_z_rolling and funding_z_robust...")
    t1 = time.time()
    btc_z = compute_btc_z_rolling(data)
    print(f"  btc_z: {len(btc_z)} ts ({time.time()-t1:.1f}s)")
    t1 = time.time()
    funding_z = compute_funding_z_robust(funding_data, data["BTC"])
    print(f"  funding_z: {len(funding_z)} (coin, ts) pairs ({time.time()-t1:.1f}s)")

    # Distribution sanity on funding_z
    fund_vals = list(funding_z.values())
    if fund_vals:
        print(f"  funding_z distrib: min={min(fund_vals):+.2f} med={np.median(fund_vals):+.2f} "
              f"max={max(fund_vals):+.2f} p10={np.percentile(fund_vals,10):+.2f} "
              f"p90={np.percentile(fund_vals,90):+.2f}")

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
        apply_adaptive_modulator=False,  # explicit baseline via make_baseline_fn
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    # Baseline (v12.2.0 1D modulator)
    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 1D modulator (α × btc_z only)':^110}")
    print("=" * 110)
    baseline_fn = make_baseline_fn(btc_z)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, size_fn=baseline_fn, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    # γ sweep
    print("\n" + "=" * 110)
    print(f"{'γ SWEEP — mult = 1 + α × btc_z + γ × funding_z × (-dir_sign)':^110}")
    print("=" * 110)
    print(f"  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<46s}  {'Δ28m':>9s}  {'Δ12m':>9s}  {'Δ6m':>9s}  {'Δ3m':>9s}  {'ΔDD avg':>8s}  pos")

    configs = [
        # S9 alone — the canonical mean-reversion fade where funding intuition is strongest
        ("γ[S9]=+0.3",                 {"S9": +0.3}),
        ("γ[S9]=+0.5",                 {"S9": +0.5}),
        ("γ[S9]=+0.7",                 {"S9": +0.7}),
        ("γ[S9]=-0.3",                 {"S9": -0.3}),  # control: opposite sign
        # S9 + S5 (other mean-reversion fade)
        ("γ[S9,S5]=+0.3",              {"S9": +0.3, "S5": +0.3}),
        ("γ[S9,S5]=+0.5",              {"S9": +0.5, "S5": +0.5}),
        # All fade strats (S5, S9, S10)
        ("γ[S5,S9,S10]=+0.3",          {"S5": +0.3, "S9": +0.3, "S10": +0.3}),
        ("γ[S5,S9,S10]=+0.5",          {"S5": +0.5, "S9": +0.5, "S10": +0.5}),
        # All strats (including momentum S1)
        ("γ[ALL]=+0.3",                {"S1": +0.3, "S5": +0.3, "S8": +0.3, "S9": +0.3, "S10": +0.3}),
        ("γ[ALL]=+0.5",                {"S1": +0.5, "S5": +0.5, "S8": +0.5, "S9": +0.5, "S10": +0.5}),
        # S8 specifically (capitulation LONG — funding very negative when shorts overcrowded)
        ("γ[S8]=+0.5",                 {"S8": +0.5}),
        ("γ[S8]=+0.7",                 {"S8": +0.7}),
    ]

    results = []
    for name, gamma in configs:
        size_fn = make_funding_fn(gamma, btc_z, funding_z)
        deltas, ddds = {}, {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {flag} {name:<44s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+8.1f}%  "
              f"{deltas['6m']:+8.1f}%  {deltas['3m']:+8.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_dd, positives))

    # Verdict
    print("\n" + "=" * 110)
    passers = [r for r in results if r[3] == 4 and r[2] <= 0.5]
    if passers:
        print(f"{len(passers)} config(s) pass 4/4 strict:")
        for name, deltas, avg_dd, _ in passers:
            print(f"  ✓ {name}: sum Δpnl = {sum(deltas.values()):+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
        print("\nNext step: pick most defensible passer + implement in trading.py + bump VERSION.")
    else:
        print("NO config passes 4/4 strict.")
        # 3/4 honorable mention
        near = sorted(results, key=lambda r: (-r[3], -sum(r[1].values())))[:3]
        print("Top 3 by positives × magnitude:")
        for name, deltas, avg_dd, pos in near:
            print(f"  {pos}/4  {name}: sum Δpnl={sum(deltas.values()):+.1f}pp, ΔDD avg={avg_dd:+.2f}pp")
        print("→ Funding amplifier rejected. Move to #1 (target vol sizing).")


if __name__ == "__main__":
    main()
