"""Test: flip S5 SHORT → LONG when BTC in bull regime.

Supervisor hypothesis (2026-05-11): the v12.2.0 adaptive modulator reduces
S5 SHORT size in bull but doesn't fix the DIRECTIONAL issue — shorting
outperformers fails when bull momentum continues. Maybe we should INVERT
the direction in bull: when S5 detects div>+X (token outperforming sector)
in bull, go LONG instead of SHORT (trend-follow rather than fade).

This file tests several variants:
  A) Skip S5 SHORT when btc_z > threshold (already tested but with v12.2.0 baseline)
  B) Flip S5 SHORT to LONG when btc_z > threshold (NEW)
  C) Flip S5 LONG to SHORT when btc_z < -threshold (symmetric inversion)
  D) Conditional: flip only if div magnitude > X (signal strength filter)
  E) Combine flip + adaptive modulator (current baseline)

Walk-forward 4/4 strict on 28m/12m/6m/3m.

Baseline = current production v12.2.0 (S1/S8/S9 + S5 SHORT modulator).

Usage:
    python3 -m backtests.backtest_s5_directional_flip
"""
from __future__ import annotations

import time
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


def main() -> None:
    print("Loading...")
    data = load_3y_candles()
    features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); fund = load_funding()

    # Compute btc_z map (rolling 30d/180d)
    btc = data["BTC"]
    n_lb = 30 * 6; n_zw = 180 * 6
    closes = np.array([c['c'] for c in btc])
    rets = []; ts_list = []
    for i in range(n_lb, len(btc)):
        if closes[i - n_lb] > 0:
            rets.append(closes[i] / closes[i - n_lb] - 1)
            ts_list.append(btc[i]['t'])
    btc_z_map = {}
    for j in range(len(rets)):
        past = rets[max(0, j - n_zw):j+1]
        if len(past) < 30: continue
        m = np.mean(past); s = np.std(past) or 1.0
        btc_z_map[ts_list[j]] = (rets[j] - m) / s

    latest_ts = max(c["t"] for c in btc)
    end_dt = datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc)
    early_exit = dict(exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS, mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS)
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp()*1000))
                    for lab, m in WINDOWS]
    common = dict(sector_features=sf, dxy_data=dxy, end_ts_ms=latest_ts,
                  start_capital=CAP, oi_data=oi, funding_data=fund)

    # Baseline = current production (apply_adaptive_modulator=True picks up
    # ADAPTIVE_ALPHA + ADAPTIVE_ALPHA_DIR from config.py = v12.2.0 setup)
    print("\nBaseline (v12.2.0 = S1/S8/S9 + S5 SHORT modulator):")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts,
                       early_exit_params=early_exit,
                       apply_adaptive_modulator=True, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        if "early_exit_params" not in kwargs:
            kwargs["early_exit_params"] = early_exit
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # ── (A) HARD SKIP S5 SHORT when btc_z > threshold ──────────────
    # Re-test on v12.2.0 baseline (before tests used v11.x baseline)
    print("\n" + "=" * 110)
    print(f"{'(A) HARD SKIP S5 SHORT when btc_z > X (on v12.2.0 baseline)':^110}")
    print("=" * 110)
    for thr in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]:
        def make_skip(t):
            def skip(coin, ts, strat, dir):
                return strat == "S5" and dir == -1 and btc_z_map.get(ts, 0) > t
            return skip
        name = f"SKIP S5 SHORT if z > {thr:.1f}"
        positives, d_pnl, d_dd = run_and_record(name,
            skip_fn=make_skip(thr), apply_adaptive_modulator=True)
        print(fmt_row(name, d_pnl, d_dd))

    # ── (B) FLIP S5 SHORT → LONG when btc_z > threshold ────────────
    # This requires injecting flipped candidates via extra_candidate_fn
    # AND skipping the original SHORT via skip_fn. The flip logic needs
    # to know what S5 SHORT WOULD have triggered (i.e. div < -threshold)
    # and inject a LONG candidate for the same token.
    print("\n" + "=" * 110)
    print(f"{'(B) FLIP S5 SHORT → LONG when btc_z > X':^110}")
    print("=" * 110)
    # We can't easily flip via skip_fn alone (would just skip).
    # Use extra_candidate_fn to add LONG S5 candidates when bull AND div < 0.
    # Then skip_fn to remove original SHORT.
    # extra_candidate_fn signature: (ts, coins, feat_by_ts, data, sector_features, oi_data) -> list[cand]
    from analysis.bot.config import STRAT_Z, S5_DIV_THRESHOLD, S5_VOL_Z_MIN
    HOLD_CANDLES_S5 = 12  # 48h / 4h

    for thr_z in [0.5, 0.8, 1.0, 1.2, 1.5]:
        def make_skip(t):
            def skip(coin, ts, strat, dir):
                return strat == "S5" and dir == -1 and btc_z_map.get(ts, 0) > t
            return skip
        def make_extra(t):
            # In bull (z > t), when S5 would normally fire SHORT (div < -threshold),
            # inject a flipped LONG candidate instead. sf is closed-over.
            def add_flipped(ts, coins, feat_by_ts, data_, coin_by_ts_, positions_, cooldown_):
                z = btc_z_map.get(ts, 0)
                if z <= t:
                    return []
                out = []
                for coin in coins:
                    sf_v = sf.get((ts, coin))
                    if not sf_v:
                        continue
                    # Original SHORT condition: |div| ≥ threshold, vol_z ≥ min, div < 0
                    if (abs(sf_v["divergence"]) >= S5_DIV_THRESHOLD
                            and sf_v["vol_z"] >= S5_VOL_Z_MIN
                            and sf_v["divergence"] < 0):
                        # Flip to LONG: same setup, opposite direction
                        out.append({
                            "coin": coin, "dir": 1, "strat": "S5",
                            "z": STRAT_Z["S5"], "hold": HOLD_CANDLES_S5,
                            "strength": abs(sf_v["divergence"]),
                        })
                return out
            return add_flipped
        name = f"FLIP S5 SHORT→LONG if z > {thr_z:.1f}"
        positives, d_pnl, d_dd = run_and_record(name,
            skip_fn=make_skip(thr_z),
            extra_candidate_fn=make_extra(thr_z),
            apply_adaptive_modulator=True)
        print(fmt_row(name, d_pnl, d_dd))

    # ── (C) Symmetric: also flip S5 LONG → SHORT in deep bear ──────
    print("\n" + "=" * 110)
    print(f"{'(C) SYMMETRIC: flip S5 LONG→SHORT in bear AND SHORT→LONG in bull':^110}")
    print("=" * 110)
    for thr in [0.8, 1.0, 1.2]:
        def make_skip_sym(t):
            def skip(coin, ts, strat, dir):
                if strat != "S5":
                    return False
                z = btc_z_map.get(ts, 0)
                # Skip original SHORT in bull (will flip)
                if dir == -1 and z > t:
                    return True
                # Skip original LONG in bear (will flip)
                if dir == 1 and z < -t:
                    return True
                return False
            return skip
        def make_extra_sym(t):
            def add(ts, coins, feat_by_ts, data_, coin_by_ts_, positions_, cooldown_):
                z = btc_z_map.get(ts, 0)
                if abs(z) <= t:
                    return []
                out = []
                bull = z > t
                bear = z < -t
                for coin in coins:
                    sf_v = sf.get((ts, coin))
                    if not sf_v: continue
                    if (abs(sf_v["divergence"]) >= S5_DIV_THRESHOLD
                            and sf_v["vol_z"] >= S5_VOL_Z_MIN):
                        # Original SHORT (div<0) flipped to LONG in bull
                        if bull and sf_v["divergence"] < 0:
                            out.append({"coin": coin, "dir": 1, "strat": "S5",
                                "z": STRAT_Z["S5"], "hold": HOLD_CANDLES_S5,
                                "strength": abs(sf_v["divergence"])})
                        # Original LONG (div>0) flipped to SHORT in bear
                        elif bear and sf_v["divergence"] > 0:
                            out.append({"coin": coin, "dir": -1, "strat": "S5",
                                "z": STRAT_Z["S5"], "hold": HOLD_CANDLES_S5,
                                "strength": abs(sf_v["divergence"])})
                return out
            return add
        name = f"SYMMETRIC FLIP if |z| > {thr:.1f}"
        positives, d_pnl, d_dd = run_and_record(name,
            skip_fn=make_skip_sym(thr),
            extra_candidate_fn=make_extra_sym(thr),
            apply_adaptive_modulator=True)
        print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 strict winners ─────────────────────────────────────────
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
        for name, d_pnl, d_dd in found[:15]:
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # ── Top 15 by sum ──────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 15 by sum(ΔPnL)':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(), key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:15]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:55s}  sum={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    # ── Recent windows focus ──
    print("\n" + "=" * 110)
    print(f"{'Recent 90j focus (test on recent regime only)':^110}")
    print("=" * 110)
    w90 = int((end_dt - relativedelta(days=90)).timestamp()*1000)
    cap500 = dict(common); cap500['start_capital'] = 500.0
    b90 = run_window(features, data, start_ts_ms=w90, early_exit_params=early_exit,
                     apply_adaptive_modulator=True, **cap500)
    print(f"  Baseline 90j (v12.2.0): pnl={b90['pnl_pct']:+.1f}%  DD={b90['max_dd_pct']:.1f}%")
    for thr in [0.8, 1.0, 1.2]:
        def make_extra(t):
            def add_flipped(ts, coins, feat_by_ts, data_, coin_by_ts_, positions_, cooldown_):
                z = btc_z_map.get(ts, 0)
                if z <= t: return []
                out = []
                for coin in coins:
                    sf_v = sf.get((ts, coin))
                    if not sf_v: continue
                    if (abs(sf_v["divergence"]) >= S5_DIV_THRESHOLD
                            and sf_v["vol_z"] >= S5_VOL_Z_MIN
                            and sf_v["divergence"] < 0):
                        out.append({"coin": coin, "dir": 1, "strat": "S5",
                            "z": STRAT_Z["S5"], "hold": HOLD_CANDLES_S5,
                            "strength": abs(sf_v["divergence"])})
                return out
            return add_flipped
        def make_skip(t):
            def skip(coin, ts, strat, dir):
                return strat == "S5" and dir == -1 and btc_z_map.get(ts, 0) > t
            return skip
        r = run_window(features, data, start_ts_ms=w90,
                       early_exit_params=early_exit,
                       skip_fn=make_skip(thr), extra_candidate_fn=make_extra(thr),
                       apply_adaptive_modulator=True, **cap500)
        dp = r['pnl_pct'] - b90['pnl_pct']
        dd = r['max_dd_pct'] - b90['max_dd_pct']
        print(f"    FLIP if z > {thr:.1f}: pnl={r['pnl_pct']:+.1f}%  Δ={dp:+.1f}pp  ΔDD={dd:+.1f}pp")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
