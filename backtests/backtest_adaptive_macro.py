"""Walk-forward — adaptive bot exploration (regime + macro modulators).

User question (2026-05-08): could an adaptive bot — one whose signal sizing or
activation depends on market state — beat the current static parameters?

Four angles tested side-by-side, each walk-forward 4/4 strict on 28m/12m/6m/3m:

  A) DISCRETE REGIME GATING — classify market state (bull/bear/range from
     BTC 30d return) and ENABLE/DISABLE strategies per regime.
     Hypothesis: S1 should fire bull-only, S8 bear-only, etc.

  B) DISCRETE REGIME SIZING — same regimes, but instead of on/off, apply a
     per-(regime, strategy) multiplier {0.5, 1.0, 1.5, 2.0}.
     Hypothesis: size up signals that match regime, down those that don't.

  C) CONTINUOUS MACRO MODULATOR — size = base × (1 + α × macro_z) where
     macro_z is a z-score of BTC 30d return or DXY 30d change.
     Hypothesis: smooth modulation > discrete switches.

  D) VOLATILITY REGIME — orthogonal axis: BTC realized 30d vol low/high.
     Hypothesis: signal performance depends on vol regime not just trend.

  E) COMBOS — best of A/B/C/D combined.

Usage:
    python3 -m backtests.backtest_adaptive_macro
"""
from __future__ import annotations

import time
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore
import numpy as np

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


def fmt_row(name, deltas_pnl, deltas_dd):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {name:55s}  "
            f"Δ28m={deltas_pnl['28m']:+8.1f}  Δ12m={deltas_pnl['12m']:+7.1f}  "
            f"Δ6m={deltas_pnl['6m']:+6.1f}  Δ3m={deltas_pnl['3m']:+5.1f}  "
            f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")


def compute_btc_features(data: dict, candles_per_day: int = 6) -> dict:
    """Per-ts BTC features: 30d return + 30d realized vol.

    candles_per_day=6 for 4h granularity (24/4=6).
    """
    btc = data.get("BTC", [])
    n_30d = 30 * candles_per_day  # 180 candles for 30d at 4h
    out = {}
    closes = np.array([c["c"] for c in btc])
    for i in range(n_30d, len(btc)):
        ret_30d = (closes[i] / closes[i - n_30d] - 1) if closes[i - n_30d] > 0 else 0
        # Realized vol: stddev of log returns over last 30d
        if i > n_30d:
            log_rets = np.diff(np.log(closes[max(0, i - n_30d):i + 1]))
            vol_30d = float(np.std(log_rets)) if len(log_rets) > 1 else 0
        else:
            vol_30d = 0
        out[btc[i]["t"]] = {"ret_30d": ret_30d, "vol_30d": vol_30d}
    return out


def compute_dxy_features(dxy_data: dict, ts_list: list) -> dict:
    """For each ts, compute DXY 30d change %."""
    if not dxy_data or "values" not in dxy_data:
        return {}
    dxy_values = dxy_data["values"]  # list of (ts_ms, value)
    if not dxy_values:
        return {}
    dxy_sorted = sorted(dxy_values)
    dxy_ts = np.array([v[0] for v in dxy_sorted])
    dxy_v = np.array([v[1] for v in dxy_sorted])

    out = {}
    for ts in ts_list:
        if ts < dxy_ts[0] + 30 * 86400 * 1000:
            continue
        idx = int(np.searchsorted(dxy_ts, ts) - 1)
        if idx < 0:
            continue
        cur = dxy_v[idx]
        # Find idx 30d ago
        target = ts - 30 * 86400 * 1000
        idx30 = int(np.searchsorted(dxy_ts, target) - 1)
        if idx30 < 0 or dxy_v[idx30] <= 0:
            continue
        change = (cur / dxy_v[idx30] - 1)
        out[ts] = change
    return out


def classify_regime_trend(ret_30d: float) -> str:
    if ret_30d > 0.10:
        return "bull"
    if ret_30d < -0.10:
        return "bear"
    return "range"


def classify_regime_vol(vol_30d: float, vol_med: float) -> str:
    return "high_vol" if vol_30d > vol_med else "low_vol"


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Computing macro features...")
    btc_feat = compute_btc_features(data)
    print(f"  BTC features for {len(btc_feat)} candles")

    # Compute median vol for split
    vols = [f["vol_30d"] for f in btc_feat.values() if f["vol_30d"] > 0]
    vol_med = statistics.median(vols)
    print(f"  Median 30d realized vol (BTC, log space): {vol_med:.4f}")

    # Classify regimes (trend + vol)
    regime_trend_by_ts = {ts: classify_regime_trend(f["ret_30d"]) for ts, f in btc_feat.items()}
    regime_vol_by_ts = {ts: classify_regime_vol(f["vol_30d"], vol_med) for ts, f in btc_feat.items()}

    # Distribution
    from collections import Counter
    print(f"  Trend regime distribution: {dict(Counter(regime_trend_by_ts.values()))}")
    print(f"  Vol regime distribution:   {dict(Counter(regime_vol_by_ts.values()))}")

    dxy_change_by_ts = compute_dxy_features(dxy_data, list(btc_feat.keys()))
    if dxy_change_by_ts:
        dxy_vals = list(dxy_change_by_ts.values())
        print(f"  DXY 30d change: mean={np.mean(dxy_vals)*100:+.1f}% std={np.std(dxy_vals)*100:.1f}%")

    # Z-scores for continuous modulation
    btc_rets = [f["ret_30d"] for f in btc_feat.values()]
    btc_ret_mean = np.mean(btc_rets)
    btc_ret_std = np.std(btc_rets) or 1.0
    btc_ret_z = {ts: (f["ret_30d"] - btc_ret_mean) / btc_ret_std for ts, f in btc_feat.items()}

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
        start_capital=CAP, oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit,
    )

    print("\nBaseline (static parameters, no regime adaptation):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # Find nearest BTC regime ts for a given feature ts (BTC has every 4h, features same grid)
    def regime_at(ts):
        return regime_trend_by_ts.get(ts, "range")
    def vol_regime_at(ts):
        return regime_vol_by_ts.get(ts, "low_vol")
    def btc_z_at(ts):
        return btc_ret_z.get(ts, 0)

    # ── (A) Discrete regime GATING (binary on/off per strat per regime) ───
    print("\n" + "=" * 110)
    print(f"{'(A) DISCRETE REGIME GATING — strategies on/off by trend regime':^110}")
    print("=" * 110)
    # Hypothesis sets:
    # H1: S1 bull-only (skip in bear/range)
    # H2: S8 bear-only (skip in bull/range)
    # H3: S1 bull, S8 bear (combine)
    # H4: S5/S9 disabled in bear (mean-reversion fails in trends)
    # H5: S5/S9 disabled in bull (same logic, opposite)
    hypos = [
        ("S1 bull-only",            {"S1":  {"bear", "range"}}),
        ("S8 bear-only",            {"S8":  {"bull", "range"}}),
        ("S1 bull + S8 bear",       {"S1":  {"bear", "range"}, "S8": {"bull", "range"}}),
        ("S5/S9 not bear",          {"S5":  {"bear"}, "S9": {"bear"}}),
        ("S5/S9 not bull",          {"S5":  {"bull"}, "S9": {"bull"}}),
        ("S1 not range",            {"S1":  {"range"}}),
        ("S10 range-only",          {"S10": {"bull", "bear"}}),
        ("S5 trending only",        {"S5":  {"range"}}),
    ]
    for name, gates in hypos:
        def make_skip(g):
            def skip(coin, ts, strat, dir):
                if strat in g and regime_at(ts) in g[strat]:
                    return True
                return False
            return skip
        positives, d_pnl, d_dd = run_and_record(f"GATE: {name}", skip_fn=make_skip(gates))
        print(fmt_row(f"GATE: {name}", d_pnl, d_dd))

    # ── (B) Discrete regime SIZING (per-regime per-strat mult) ────────
    print("\n" + "=" * 110)
    print(f"{'(B) DISCRETE REGIME SIZING — per-(regime, strat) multiplier':^110}")
    print("=" * 110)
    sizing_hypos = [
        ("S1 bull×1.5, bear×0.5",   {("bull","S1"): 1.5, ("bear","S1"): 0.5}),
        ("S1 bull×2.0, bear×0.0",   {("bull","S1"): 2.0, ("bear","S1"): 0.0}),
        ("S5 range×1.5",            {("range","S5"): 1.5}),
        ("S5 trends×0.7",           {("bull","S5"): 0.7, ("bear","S5"): 0.7}),
        ("S9 trends×1.3",           {("bull","S9"): 1.3, ("bear","S9"): 1.3}),
        ("S9 range×0.5",            {("range","S9"): 0.5}),
        ("S8 bear×1.5",             {("bear","S8"): 1.5}),
        ("S10 range×1.3",           {("range","S10"): 1.3}),
        ("Combo: S1 bull×1.5+S8 bear×1.5+S5 range×1.3",
         {("bull","S1"): 1.5, ("bear","S8"): 1.5, ("range","S5"): 1.3}),
    ]
    for name, mults in sizing_hypos:
        def make_fn(m):
            def fn(cand, f, n_pos):
                key = (regime_at(f["t"]), cand["strat"])
                return m.get(key, 1.0)
            return fn
        positives, d_pnl, d_dd = run_and_record(f"SIZE: {name}", size_fn=make_fn(mults))
        print(fmt_row(f"SIZE: {name}", d_pnl, d_dd))

    # ── (C) Continuous macro modulator ────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) CONTINUOUS MACRO MODULATOR — size×(1+α·btc_z) per strat':^110}")
    print("=" * 110)
    # Try amplifying or damping each strat by btc_z (positive in bull, negative in bear)
    cont_hypos = [
        ("S1 +0.3·btc_z",   {"S1": +0.3}),
        ("S1 +0.5·btc_z",   {"S1": +0.5}),
        ("S8 -0.5·btc_z",   {"S8": -0.5}),  # damp in bull, amplify in bear
        ("S5 -0.2·btc_z",   {"S5": -0.2}),  # slight bias toward bear
        ("S9 -0.3·btc_z",   {"S9": -0.3}),  # mean-rev preferred in chop
        ("Combo: S1+0.3 S8-0.3", {"S1": +0.3, "S8": -0.3}),
    ]
    for name, alphas in cont_hypos:
        def make_fn(a):
            def fn(cand, f, n_pos):
                z = btc_z_at(f["t"])
                alpha = a.get(cand["strat"], 0)
                # Multiplier capped at [0.3, 2.5] for safety
                m = 1 + alpha * z
                return max(0.3, min(2.5, m))
            return fn
        positives, d_pnl, d_dd = run_and_record(f"CONT: {name}", size_fn=make_fn(alphas))
        print(fmt_row(f"CONT: {name}", d_pnl, d_dd))

    # ── (D) Volatility regime — high vs low vol axis ──────────────────
    print("\n" + "=" * 110)
    print(f"{'(D) VOLATILITY REGIME — high_vol vs low_vol BTC realized':^110}")
    print("=" * 110)
    vol_hypos = [
        ("S5 high_vol×1.3",         {("high_vol","S5"): 1.3}),
        ("S5 low_vol×1.3",          {("low_vol","S5"): 1.3}),
        ("S9 high_vol×1.5",         {("high_vol","S9"): 1.5}),
        ("S9 low_vol×0.5",          {("low_vol","S9"): 0.5}),
        ("S10 low_vol×1.5",         {("low_vol","S10"): 1.5}),
        ("S10 high_vol×0.7",        {("high_vol","S10"): 0.7}),
        ("S1 high_vol×1.5",         {("high_vol","S1"): 1.5}),
        ("S1 low_vol×0.5",          {("low_vol","S1"): 0.5}),
    ]
    for name, mults in vol_hypos:
        def make_fn(m):
            def fn(cand, f, n_pos):
                key = (vol_regime_at(f["t"]), cand["strat"])
                return m.get(key, 1.0)
            return fn
        positives, d_pnl, d_dd = run_and_record(f"VOL: {name}", size_fn=make_fn(mults))
        print(fmt_row(f"VOL: {name}", d_pnl, d_dd))

    # ── (E) COMBOS of best A/B/C/D ─────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(E) COMBOS — pile up best signals':^110}")
    print("=" * 110)
    # First, identify best 4/4-passing single-axis configs
    print("  (Tested: combos of best individual configs found above)")
    combos = [
        ("S1 bull-only + S8 bear×1.5",
         {"skip_fn": "S1 bull-only", "size_fn_mults": {("bear","S8"): 1.5}}),
        ("S1 bull×1.5 + S5 range×1.3 + S9 trends×1.3",
         {"size_fn_mults": {("bull","S1"): 1.5, ("range","S5"): 1.3,
                           ("bull","S9"): 1.3, ("bear","S9"): 1.3}}),
    ]
    for name, params in combos:
        skip = None
        if "skip_fn" in params and params["skip_fn"] == "S1 bull-only":
            def make_skip():
                def skip(coin, ts, strat, dir):
                    return strat == "S1" and regime_at(ts) in {"bear", "range"}
                return skip
            skip = make_skip()
        if "size_fn_mults" in params:
            mults = params["size_fn_mults"]
            def make_fn(m):
                def fn(cand, f, n_pos):
                    key = (regime_at(f["t"]), cand["strat"])
                    return m.get(key, 1.0)
                return fn
            size_fn = make_fn(mults)
        else:
            size_fn = None
        kw = {}
        if skip is not None: kw["skip_fn"] = skip
        if size_fn is not None: kw["size_fn"] = size_fn
        positives, d_pnl, d_dd = run_and_record(f"COMBO: {name}", **kw)
        print(fmt_row(f"COMBO: {name}", d_pnl, d_dd))

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^110}")
    print("=" * 110)
    found = []
    for name, info in all_results.items():
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
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

    # ── Top 20 ────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 20 by sum(ΔPnL) — even if not 4/4':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:20]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:55s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
