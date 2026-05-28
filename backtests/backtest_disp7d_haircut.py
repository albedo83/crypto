"""Walk-forward validation of option B — soft haircut on S5 LONG when disp_7d ≥ 700.

Rationale: the hard skip variant (backtests/backtest_disp7d_gate.py) failed 5/7
windows on 12m-max grid. Hypothesis: instead of skipping entirely, halve the
position size — preserve some upside on the few S5 LONG winners above 700 while
limiting loss exposure on the many losers.

Mechanism: size_fn that returns 0.5 × adaptive_modulator_factor for S5 LONG
entries with disp_7d ≥ 700, otherwise 1.0 × modulator_factor.

Run: .venv/bin/python3 -m backtests.backtest_disp7d_haircut
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import numpy as np

from backtests.backtest_rolling import (
    load_3y_candles, build_features, compute_sector_features,
    load_dxy, load_oi, load_funding, run_window,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    MACRO_LOOKBACK_DAYS, MACRO_Z_WINDOW_DAYS,
    MACRO_Z_CLIP, MACRO_MULT_MIN, MACRO_MULT_MAX, get_adaptive_alpha,
)

DISP_7D_THRESHOLD = 700.0
HAIRCUT_FACTOR = 0.5    # halve size on losing-candidate entries


def precompute_disp_7d(features) -> dict[int, float]:
    from collections import defaultdict
    by_ts = defaultdict(list)
    for coin, fl in features.items():
        for f in fl:
            if "ret_42h" in f:
                by_ts[f["t"]].append(f["ret_42h"])
    return {ts: float(np.std(rets)) for ts, rets in by_ts.items() if len(rets) > 4}


def precompute_btc_z(data, interval_hours: int = 4) -> dict[int, float]:
    """Mirror backtest_rolling lines 449-469."""
    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    cpd = max(1, 24 // max(1, interval_hours))
    n_lb = MACRO_LOOKBACK_DAYS * cpd
    n_zw = MACRO_Z_WINDOW_DAYS * cpd
    btc_z_map: dict[int, float] = {}
    if len(btc_closes) < n_lb + 30:
        return btc_z_map
    rets_history: list[float] = []
    for i in range(n_lb, len(btc_closes)):
        if btc_closes[i - n_lb] > 0:
            rets_history.append(float(btc_closes[i] / btc_closes[i - n_lb] - 1))
        else:
            rets_history.append(0.0)
    for j in range(len(rets_history)):
        past = rets_history[max(0, j - n_zw):j + 1]
        if len(past) < 30:
            continue
        past_arr = np.array(past)
        mean = float(past_arr.mean())
        std = float(past_arr.std()) or 1.0
        ts_j = btc_candles[n_lb + j]["t"]
        btc_z_map[ts_j] = (rets_history[j] - mean) / std
    return btc_z_map


def make_haircut_size_fn(disp_7d_by_ts: dict[int, float], btc_z_map: dict[int, float],
                          threshold: float, haircut: float):
    """Returns a size_fn that composes (adaptive modulator) × (haircut if S5 LONG over disp threshold).

    We must compose the modulator ourselves because passing size_fn=... to
    run_window disables the built-in modulator branch (run_window lines 1015-1024).
    """
    def size_fn(cand, f, n_positions):
        ts = f.get("t") if f else None
        # Step 1: adaptive modulator (mirror of backtest_rolling logic)
        m_mod = 1.0
        alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        if alpha != 0 and ts is not None:
            z = btc_z_map.get(ts, 0.0)
            z_clip = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, z))
            m_mod = max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z_clip))
        # Step 2: haircut if S5 LONG and disp_7d ≥ threshold
        m_hair = 1.0
        if cand["strat"] == "S5" and cand["dir"] == 1 and ts is not None:
            d7 = disp_7d_by_ts.get(ts, 0.0)
            if d7 >= threshold:
                m_hair = haircut
        return m_mod * m_hair
    return size_fn


def main():
    print("=" * 76)
    print(f"  Option B — soft haircut S5 LONG × {HAIRCUT_FACTOR} when disp_7d ≥ {DISP_7D_THRESHOLD:.0f}")
    print("=" * 76)

    print("\nLoading data + features...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Precomputing disp_7d + btc_z per ts...")
    disp_7d_by_ts = precompute_disp_7d(features)
    btc_z_map = precompute_btc_z(data)
    print(f"  disp_7d ts entries: {len(disp_7d_by_ts)}")
    print(f"  btc_z   ts entries: {len(btc_z_map)}")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    end_ts = latest_ts
    windows_cfg = [
        ("12m", end_dt - timedelta(days=365)),
        ("9m",  end_dt - timedelta(days=274)),
        ("6m",  end_dt - timedelta(days=182)),
        ("4m",  end_dt - timedelta(days=122)),
        ("3m",  end_dt - timedelta(days=91)),
        ("2m",  end_dt - timedelta(days=61)),
        ("1m",  end_dt - timedelta(days=30)),
    ]

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

    candidate_size_fn = make_haircut_size_fn(disp_7d_by_ts, btc_z_map,
                                             DISP_7D_THRESHOLD, HAIRCUT_FACTOR)

    common_kwargs = dict(
        start_capital=1000.0,
        oi_data=oi_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=True,
    )

    results = []
    print(f"\nEnd of data: {end_dt.date()}\n")
    for label, start_dt in windows_cfg:
        start_ts = int(start_dt.timestamp() * 1000)
        print(f"  Window {label} ({start_dt.date()} → {end_dt.date()})")
        baseline = run_window(features, data, sector_features, dxy_data,
                              start_ts, end_ts, **common_kwargs)
        candidate = run_window(features, data, sector_features, dxy_data,
                               start_ts, end_ts, size_fn=candidate_size_fn, **common_kwargs)

        d_pnl = candidate["pnl_pct"] - baseline["pnl_pct"]
        d_dd = candidate["max_dd_pct"] - baseline["max_dd_pct"]
        d_trades = candidate["n_trades"] - baseline["n_trades"]
        d_pnl_usdt = candidate["pnl"] - baseline["pnl"]

        s5l_base = [t for t in baseline.get("trades", [])
                    if t.get("strat") == "S5" and t.get("dir") == 1]
        s5l_cand = [t for t in candidate.get("trades", [])
                    if t.get("strat") == "S5" and t.get("dir") == 1]
        s5l_pnl_base = sum(t.get("pnl", 0) for t in s5l_base)
        s5l_pnl_cand = sum(t.get("pnl", 0) for t in s5l_cand)

        results.append({
            "label": label, "start": start_dt.date().isoformat(),
            "base": baseline, "cand": candidate,
            "d_pnl_pct": d_pnl, "d_dd_pct": d_dd, "d_trades": d_trades,
            "d_pnl_usdt": d_pnl_usdt,
            "s5l_base_n": len(s5l_base), "s5l_cand_n": len(s5l_cand),
            "s5l_pnl_base": s5l_pnl_base, "s5l_pnl_cand": s5l_pnl_cand,
        })

        print(f"    baseline:  pnl={baseline['pnl_pct']:+.1f}% (${baseline['pnl']:+.0f}), "
              f"DD {baseline['max_dd_pct']:.1f}%, {baseline['n_trades']} trades")
        print(f"    candidate: pnl={candidate['pnl_pct']:+.1f}% (${candidate['pnl']:+.0f}), "
              f"DD {candidate['max_dd_pct']:.1f}%, {candidate['n_trades']} trades")
        print(f"    Δpnl: {d_pnl:+.1f}pp (${d_pnl_usdt:+.0f}) | ΔDD: {d_dd:+.2f}pp | "
              f"Δtrades: {d_trades:+d} | S5 LONG: base={len(s5l_base)} (${s5l_pnl_base:+.0f}) "
              f"→ cand={len(s5l_cand)} (${s5l_pnl_cand:+.0f})")
        print()

    print("=" * 76)
    print("  SUMMARY")
    print("=" * 76)
    print(f"  {'Window':6} {'ΔPnL%':>10} {'ΔPnL$':>12} {'ΔDD pp':>10} {'Δtrades':>9} "
          f"{'ΔS5L pnl$':>11} {'verdict':>8}")
    print("-" * 76)
    n_total = len(results)
    n_pass = 0
    dd_sum = 0.0
    for r in results:
        verdict = "PASS" if r["d_pnl_pct"] > 0 else "FAIL"
        if verdict == "PASS":
            n_pass += 1
        dd_sum += r["d_dd_pct"]
        d_s5l = r["s5l_pnl_cand"] - r["s5l_pnl_base"]
        print(f"  {r['label']:6} {r['d_pnl_pct']:+10.1f} {r['d_pnl_usdt']:+12.0f} "
              f"{r['d_dd_pct']:+10.2f} {r['d_trades']:+9d} {d_s5l:+11.0f} {verdict:>8}")
    dd_avg = dd_sum / n_total
    print("-" * 76)
    print(f"  ΔDD avg: {dd_avg:+.2f}pp (target: ≤ +2.0pp)")
    print()
    if n_pass == n_total and dd_avg <= 2.0:
        print(f"  ✅ STRICT {n_total}/{n_total} PASS — haircut validates walk-forward up to 12m.")
    elif n_pass >= n_total * 0.7:
        print(f"  ⚠ {n_pass}/{n_total} PASS — partial. Not strict, weak signal.")
    else:
        print(f"  ❌ {n_total - n_pass}/{n_total} windows FAIL. Haircut rejected.")
    print()


if __name__ == "__main__":
    main()
