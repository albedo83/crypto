"""S5 reinforcement sweep — find what cuts catastrophes without killing edge.

Live observation (43 days, 38 S5 trades): WR 53%, R/R 0.93, sum +$4.53.
Wins are big (~$15-25), losses are also big (~$15-21). Need filters that
cut the catastrophes without losing the big winners.

Tests, all walk-forward 4/4 strict on 28m / 12m / 6m / 3m:

  A) BLOW-OFF TOP filter — skip S5 entries with vz>X AND |div|>Y AND OI1h>Z
     Hypothesis: vz=5.6 + OI surge = late buyers piling in = exhaustion.

  B) TIGHTER S5 STOP — sweep stop_override["S5"] from -1250 to -400.

  C) S5 EARLY-MFE EXIT — exit if MFE never crossed X after Yh.

  D) S5 DEAD-TIMEOUT TIGHTER — earlier lead, looser MFE cap, S5-only override.

  E) S5 TRAILING STOP — when MFE > N, lock at MFE-K.

  F) BTC counter-trend gate — skip S5 LONG when BTC 7d is in bearish range.

  G) Combos of best individual configs.

Usage:
    python3 -m backtests.backtest_s5_reinforce
"""
from __future__ import annotations

import time
import re
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


def build_vz_oi_map(features: dict, oi_data: dict | None) -> dict:
    """Per-(ts, coin): {vz, oi_1h_pct} for blow-off filter."""
    feat_by_ts: dict = defaultdict(dict)
    for coin, flist in features.items():
        for f in flist:
            feat_by_ts[f["t"]][coin] = f
    out: dict[tuple[int, str], dict] = {}
    for ts, fmap in feat_by_ts.items():
        for coin, f in fmap.items():
            vz = float(f.get("vol_z", 0))
            # OI 1h proxy: not in 4h features, fall back to 0 for backtest simplicity
            # (live bot has it via oi_history, backtest doesn't track 1h granularity).
            # Use ret_1c (1 candle return = ~4h proxy of OI surge by price)
            ret_1c = float(f.get("ret_1c", 0))
            out[(ts, coin)] = {"vz": vz, "ret_1c": ret_1c}
    return out


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    vz_oi_map = build_vz_oi_map(features, oi_data)
    print(f"vz/oi map built: {len(vz_oi_map)} (ts, coin) entries")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    early_exit_default = dict(
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
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts,
                       early_exit_params=early_exit_default, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, **kwargs):
        # kwargs may include early_exit_params (override). If absent, default it.
        if "early_exit_params" not in kwargs:
            kwargs["early_exit_params"] = early_exit_default
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # ── (A) BLOW-OFF TOP filter ────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(A) BLOW-OFF TOP — skip S5 if vz > X (high-vol exhaustion proxy)':^110}")
    print("=" * 110)
    # vz alone first (no OI 1h available cleanly in backtest)
    for vz_max in [3.0, 3.5, 4.0, 4.5, 5.0, 6.0]:
        def make_skip(vmax):
            def skip(coin, ts, strat, dir):
                if strat != "S5":
                    return False
                d = vz_oi_map.get((ts, coin), {})
                return d.get("vz", 0) > vmax
            return skip
        name = f"S5 skip if vz > {vz_max}"
        positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(vz_max))
        print(fmt_row(name, d_pnl, d_dd))

    # ── (B) TIGHTER S5 STOP ───────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) TIGHTER S5 STOP — override stop_loss_bps for S5 only':^110}")
    print("=" * 110)
    for s5_stop in [-1250, -1000, -900, -800, -700, -600, -500, -400]:
        name = f"S5 stop = {s5_stop} bps"
        positives, d_pnl, d_dd = run_and_record(name, stop_override={"S5": s5_stop})
        print(fmt_row(name, d_pnl, d_dd))

    # ── (C) S5 EARLY-MFE EXIT ─────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) S5 EARLY-MFE EXIT — exit if MFE never crossed K after Hh':^110}")
    print("=" * 110)
    for h in [12, 16, 20, 24, 32]:
        for mfe_min in [50, 100, 150, 200, 300]:
            cfg = {"check_after_candles": int(h // 4), "mfe_min_bps": mfe_min,
                   "strategies": {"S5"}}
            name = f"S5 early_mfe @h{h:2d} mfe<{mfe_min}"
            positives, d_pnl, d_dd = run_and_record(name, early_mfe_exit=cfg)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (D) S5-TIGHTER DEAD-TIMEOUT ───────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(D) DEAD-TIMEOUT GLOBAL VARIANTS (impacts S5 + others)':^110}")
    print("=" * 110)
    for lead in [12, 16, 24, 36]:
        for mfe_cap in [100, 200, 300, 500]:
            cfg = dict(
                exit_lead_candles=int(lead // 4),
                mfe_cap_bps=mfe_cap,
                mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
                slack_bps=DEAD_TIMEOUT_SLACK_BPS,
            )
            name = f"DT lead={lead}h mfe_cap≤{mfe_cap}"
            positives, d_pnl, d_dd = run_and_record(name, early_exit_params=cfg)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (E) S5 TRAILING STOP ──────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(E) S5 TRAILING STOP — lock at MFE-K when MFE > T':^110}")
    print("=" * 110)
    for trigger in [600, 800, 1000, 1200, 1500]:
        for offset in [150, 200, 300, 400]:
            cfg = {"strategy": "S5", "trigger_bps": trigger, "offset_bps": offset}
            name = f"S5 trail trig={trigger} off={offset}"
            positives, d_pnl, d_dd = run_and_record(name, trailing_extra=cfg)
            if positives >= 3:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (F) BTC counter-trend gate ────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(F) BTC COUNTER-TREND GATE — skip S5 LONG when BTC weak (or vice versa)':^110}")
    print("=" * 110)
    btc = data["BTC"]
    closes = np.array([c["c"] for c in btc])
    # Build BTC 7d return per ts
    btc_7d_by_ts: dict = {}
    for i in range(42, len(btc)):
        if closes[i - 42] > 0:
            btc_7d_by_ts[btc[i]["t"]] = (closes[i] / closes[i - 42] - 1) * 1e4

    for thr in [-300, -500, -800, -1000]:
        def make_skip(t):
            def skip(coin, ts, strat, dir):
                if strat != "S5" or dir != 1:
                    return False
                btc_7d = btc_7d_by_ts.get(ts, 0)
                return btc_7d < t
            return skip
        name = f"S5 LONG skip if BTC7d < {thr}bps"
        positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(thr))
        print(fmt_row(name, d_pnl, d_dd))

    # And the inverse: skip S5 SHORT when BTC strong
    for thr in [+300, +500, +800, +1000]:
        def make_skip(t):
            def skip(coin, ts, strat, dir):
                if strat != "S5" or dir != -1:
                    return False
                btc_7d = btc_7d_by_ts.get(ts, 0)
                return btc_7d > t
            return skip
        name = f"S5 SHORT skip if BTC7d > {thr}bps"
        positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(thr))
        print(fmt_row(name, d_pnl, d_dd))

    # ── (G) Best combos ───────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(G) COMBOS — best individual configs stacked':^110}")
    print("=" * 110)
    # Hand-picked combos based on what passes individual sweeps
    combos = [
        ("S5 stop -800 + trail 1000/200",
         {"stop_override": {"S5": -800},
          "trailing_extra": {"strategy": "S5", "trigger_bps": 1000, "offset_bps": 200}}),
        ("S5 stop -1000 + trail 800/200",
         {"stop_override": {"S5": -1000},
          "trailing_extra": {"strategy": "S5", "trigger_bps": 800, "offset_bps": 200}}),
        ("S5 stop -800 + skip vz>4",
         {"stop_override": {"S5": -800},
          "skip_fn": lambda c, t, s, d: s == "S5" and vz_oi_map.get((t, c), {}).get("vz", 0) > 4.0}),
        ("S5 stop -800 + early_mfe@20h<150",
         {"stop_override": {"S5": -800},
          "early_mfe_exit": {"check_after_candles": 5, "mfe_min_bps": 150, "strategies": {"S5"}}}),
    ]
    for name, kw in combos:
        positives, d_pnl, d_dd = run_and_record(name, **kw)
        print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 strict winners ────────────────────────────────────────────
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
        for name, d_pnl, d_dd in found[:25]:
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # ── Top 25 ────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 25 by sum(ΔPnL) — even if not 4/4':^110}")
    print("=" * 110)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:25]:
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 and sum(d_dd)/4 <= 0.5 else " "
        print(f"  {sign} {name:55s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
