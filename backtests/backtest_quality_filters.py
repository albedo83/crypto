"""Walk-forward — confluence filter + S9 tighter stop sweep.

Live observation (43 days, 80 trades): WR=58.8% global but S5 isolated at
52.6% (R/R 0.93) and S9 catastrophic 25% WR / R/R 0.14 / −$39 P&L on 4 trades.
Looking for filters that lift WR or R/R without destroying compounding.

Two angles tested side-by-side:

  A) CONFLUENCE FILTER on S5/S9 — compute a confluence score per (ts,coin)
     using 3 per-token features: |drawdown|>3000, vol_z>1.5, |ret_24h|>200
     (plus cross-sectional n_stress >= 5). Skip S5/S9 if conf < threshold.
     Hypothesis: stronger setups have better edge → filter wins WR.

  B) S9 TIGHTER STOP — sweep stop_override["S9"] from current adaptive
     down to -800/-600/-400 bps. Hypothesis: tighter stop crystallizes
     small losses earlier, avoids the catastrophic timeout-at-MAE.

Walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_quality_filters
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

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


def build_confluence_map(features: dict, data: dict) -> dict:
    """Precompute confluence score per (ts, coin) using bot's 4 features.

    bot.py:193-197 logic, omitting the 5th OI-1h check (no 1h OI in backtest):
      conf = sum([
        abs(drawdown) > 3000,
        vol_z > 1.5,
        abs(ret_24h) > 200,
        n_stress_global >= 5,
      ])
    """
    feat_by_ts: dict = defaultdict(dict)
    for coin, flist in features.items():
        for f in flist:
            feat_by_ts[f["t"]][coin] = f

    # Compute n_stress_global per ts (count of tokens with extreme abs(ret_6h))
    n_stress_by_ts: dict[int, int] = {}
    for ts, fmap in feat_by_ts.items():
        stress = sum(1 for f in fmap.values() if abs(f.get("ret_6h", 0)) > 500)
        n_stress_by_ts[ts] = stress

    conf_map: dict[tuple[int, str], int] = {}
    for ts, fmap in feat_by_ts.items():
        nstress = n_stress_by_ts[ts]
        for coin, f in fmap.items():
            conf = sum([
                abs(f.get("drawdown", 0)) > 3000,
                f.get("vol_z", 0) > 1.5,
                abs(f.get("ret_6h", 0)) > 200,
                nstress >= 5,
            ])
            conf_map[(ts, coin)] = conf
    return conf_map


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    print("Building confluence map...")
    conf_map = build_confluence_map(features, data)
    print(f"  {len(conf_map)} (ts, coin) entries")

    # Confluence distribution to understand thresholds
    from collections import Counter
    dist = Counter(conf_map.values())
    print(f"  Confluence distribution: {dict(sorted(dist.items()))}")

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

    print("\nBaseline (no quality filter, current S9 stop):")
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
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives,
                             "n_trades": {l: rs[l]["n_trades"] for l, _ in window_specs}}
        return positives, d_pnl, d_dd

    # ── (A) Confluence filter on S5 and S9 ──────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(A) CONFLUENCE FILTER — skip S5/S9 entries with conf < threshold':^110}")
    print("=" * 110)
    for strats in [("S5",), ("S9",), ("S5", "S9")]:
        strat_set = set(strats)
        for thr in [1, 2, 3, 4]:
            def make_skip(threshold, strats):
                def skip(coin, ts, strat, dir):
                    if strat in strats and conf_map.get((ts, coin), 0) < threshold:
                        return True
                    return False
                return skip
            label = "+".join(strats)
            name = f"conf≥{thr} on {label:6s}"
            positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(thr, strat_set))
            print(fmt_row(name, d_pnl, d_dd))

    # ── (B) S9 stop tighter ────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) S9 TIGHTER STOP — override S9 stop_bps':^110}")
    print("=" * 110)
    for s9_stop in [-1250, -1000, -800, -600, -500, -400, -300]:
        name = f"S9 stop = {s9_stop} bps"
        positives, d_pnl, d_dd = run_and_record(
            name, stop_override={"S9": s9_stop})
        print(fmt_row(name, d_pnl, d_dd))

    # ── (C) Combine: best confluence + best S9 stop ─────────────────────
    # Pick top 2 from each and try combos
    print("\n" + "=" * 110)
    print(f"{'(C) COMBOS — confluence + S9 stop':^110}")
    print("=" * 110)
    for thr in [2, 3]:
        for s9_stop in [-800, -600, -400]:
            def make_skip(threshold):
                def skip(coin, ts, strat, dir):
                    if strat in {"S5", "S9"} and conf_map.get((ts, coin), 0) < threshold:
                        return True
                    return False
                return skip
            name = f"conf≥{thr} S5+S9 + S9 stop {s9_stop}"
            positives, d_pnl, d_dd = run_and_record(
                name, skip_fn=make_skip(thr), stop_override={"S9": s9_stop})
            print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^110}")
    print("=" * 110)
    found = []
    for name, info in all_results.items():
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd, info["n_trades"]))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd, n_trades in found[:15]:
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  "
                  f"n_trades 28m={n_trades['28m']}")

    # ── Top 15 by sum_pnl ──────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 15 by sum(ΔPnL) — even if not 4/4':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:15]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:55s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
