"""Walk-forward test of Target Volatility Sizing on top of v12.2.0.

Hypothesis: scale each position's size inversely to the coin's CURRENT 30-day
realized volatility — when vol explodes, size shrinks; when vol normalises,
size returns. Risk-parity by token.

Formula:
    vol_mult = clip(vol_ref[coin] / max(vol_30d_current, VOL_FLOOR),
                    VOL_MULT_MIN, VOL_MULT_MAX)
    size = base × adaptive_modulator(btc_z) × vol_mult

Where:
- vol_ref[coin] = median vol_30d over the coin's history (its "normal" vol)
- VOL_FLOOR = 30 bps (clamp denominator — prevents division-by-near-zero
  blow-up when a coin is in deep consolidation)
- VOL_MULT_MIN/MAX = same clamping discipline as the macro modulator

**Success criterion (per reviewer)** : this is a DD-reduction tool, not a
PnL booster. We track both metrics and report:
  - Strict 4/4 pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp) — the usual rule
  - DD-friendly: avg ΔDD < -1pp AND avg ΔPnL > -5pp (tolerate slight PnL
    drop in exchange for significant DD compression)

Run: python3 -m backtests.backtest_target_vol_sizing
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
    get_adaptive_alpha, TRADE_SYMBOLS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy,
)

CAP = 1000.0

# Clamp on the denominator — avoids dividing by near-zero when a token is
# in deep consolidation (per reviewer's caveat).
VOL_FLOOR_BPS = 30.0


def compute_btc_z_rolling(data: dict, lookback_days: int = 30,
                           z_window_days: int = 180) -> dict:
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


def compute_vol_reference(features: dict) -> dict:
    """Per-coin median vol_30d over its full history — the 'normal' vol level
    against which we scale. Static (computed once on the whole dataset), so
    the size multiplier is purely a function of the current vol relative to
    the coin's typical regime."""
    out = {}
    for coin, feats in features.items():
        if coin == "BTC":
            continue
        vols = [f.get("vol_30d", 0) for f in feats if f.get("vol_30d", 0) > 0]
        if vols:
            out[coin] = float(np.median(vols))
    return out


def make_target_vol_fn(btc_z_map: dict, vol_ref: dict,
                       vol_mult_min: float, vol_mult_max: float,
                       strat_filter=None):
    """size_fn applying both the v12.2.0 1D modulator AND the target-vol scaling.

    If strat_filter is a set (e.g. {"S5", "S9"}), vol scaling only applies to
    those strats; the others get the baseline 1D modulator only.
    """
    def fn(cand, f, n_pos):
        ts = f["t"]
        coin = cand["coin"]
        # 1D macro modulator (v12.2.0)
        z_btc = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(ts, 0.0)))
        alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        macro_mult = max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z_btc))

        # Target-vol scaling (only if strat is in filter, or filter is None)
        vol_mult = 1.0
        if strat_filter is None or cand["strat"] in strat_filter:
            vol_cur = max(f.get("vol_30d", 0), VOL_FLOOR_BPS)
            vol_target = vol_ref.get(coin)
            if vol_target and vol_target > 0:
                raw = vol_target / vol_cur
                vol_mult = max(vol_mult_min, min(vol_mult_max, raw))

        return macro_mult * vol_mult
    return fn


def make_baseline_fn(btc_z_map: dict):
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

    print("Computing btc_z_rolling and vol_reference...")
    btc_z = compute_btc_z_rolling(data)
    vol_ref = compute_vol_reference(features)
    print(f"  btc_z: {len(btc_z)} ts")
    print(f"  vol_ref: {len(vol_ref)} coins, "
          f"median across coins = {np.median(list(vol_ref.values())):.0f} bps "
          f"(p10={np.percentile(list(vol_ref.values()), 10):.0f}, "
          f"p90={np.percentile(list(vol_ref.values()), 90):.0f})")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
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
        apply_adaptive_modulator=False,
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 1D modulator (α × btc_z only)':^110}")
    print("=" * 110)
    baseline_fn = make_baseline_fn(btc_z)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, size_fn=baseline_fn, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    print("\n" + "=" * 110)
    print(f"{'TARGET-VOL SWEEP — size = base × macro × clip(vol_ref / max(vol_cur, 30))':^110}")
    print("=" * 110)
    print("  ✓ strict = 4/4 pos AND avg ΔDD ≤ +0.5pp  |  ✓ DD-friendly = avg ΔDD ≤ -1.0pp AND avg ΔPnL ≥ -5pp\n")
    print(f"  {'config':<46s}  {'Δ28m':>8s}  {'Δ12m':>8s}  {'Δ6m':>7s}  {'Δ3m':>7s}  "
          f"{'ΔPnL avg':>9s}  {'ΔDD avg':>8s}  flag")

    # Sweep: clamp range × strat filter
    # CLAMP variants: aggressive (full), moderate, conservative (barely scales)
    # FILTER variants: all strats, mean-reversion only (S5/S9), S9 only
    configs = [
        ("clip [0.3-2.5] all strats",          0.3, 2.5, None),
        ("clip [0.5-2.0] all strats",          0.5, 2.0, None),
        ("clip [0.7-1.5] all strats",          0.7, 1.5, None),
        ("clip [0.8-1.3] all strats",          0.8, 1.3, None),
        ("clip [0.3-2.5] S5/S9 only",          0.3, 2.5, {"S5", "S9"}),
        ("clip [0.5-2.0] S5/S9 only",          0.5, 2.0, {"S5", "S9"}),
        ("clip [0.7-1.5] S5/S9 only",          0.7, 1.5, {"S5", "S9"}),
        ("clip [0.5-2.0] S5/S9/S10 (fades)",   0.5, 2.0, {"S5", "S9", "S10"}),
        ("clip [0.5-2.0] S1/S8 (trend/cap)",   0.5, 2.0, {"S1", "S8"}),
        ("clip [0.7-1.5] S5 only",             0.7, 1.5, {"S5"}),
        ("clip [0.7-1.5] S9 only",             0.7, 1.5, {"S9"}),
        ("clip [0.7-1.5] S8 only",             0.7, 1.5, {"S8"}),
    ]

    results = []
    for name, vmin, vmax, sfilter in configs:
        size_fn = make_target_vol_fn(btc_z, vol_ref, vmin, vmax, sfilter)
        deltas, ddds = {}, {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, size_fn=size_fn, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_pnl = sum(deltas.values()) / 4
        avg_dd = sum(ddds.values()) / 4
        strict = positives == 4 and avg_dd <= 0.5
        dd_friendly = avg_dd <= -1.0 and avg_pnl >= -5.0
        if strict:
            flag = "✓✓"
        elif dd_friendly:
            flag = "✓DD"
        else:
            flag = "  "
        print(f"  {flag} {name:<43s}  {deltas['28m']:+7.1f}%  {deltas['12m']:+7.1f}%  "
              f"{deltas['6m']:+6.1f}%  {deltas['3m']:+6.1f}%  "
              f"{avg_pnl:+8.1f}pp  {avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_pnl, avg_dd, positives, strict, dd_friendly))

    # Verdict
    print("\n" + "=" * 110)
    strict_pass = [r for r in results if r[5]]
    dd_pass = [r for r in results if r[6] and not r[5]]
    if strict_pass:
        print(f"STRICT 4/4 PASS ({len(strict_pass)}):")
        for name, _, avg_pnl, avg_dd, *_ in strict_pass:
            print(f"  ✓✓ {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp")
    if dd_pass:
        print(f"\nDD-FRIENDLY ({len(dd_pass)} — DD significantly reduced with tolerable PnL drag):")
        for name, deltas, avg_pnl, avg_dd, pos, *_ in dd_pass:
            print(f"  ✓DD {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp (pos {pos}/4)")
    if not strict_pass and not dd_pass:
        print("NO config passes either criterion.")
        # Show top 3 by ΔDD improvement
        results.sort(key=lambda r: r[3])
        print("Top 3 by ΔDD improvement (any sign of PnL):")
        for name, _, avg_pnl, avg_dd, pos, *_ in results[:3]:
            print(f"  {name}: avg ΔPnL {avg_pnl:+.1f}pp, avg ΔDD {avg_dd:+.2f}pp ({pos}/4)")


if __name__ == "__main__":
    main()
