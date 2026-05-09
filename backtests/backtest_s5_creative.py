"""S5 creative reinforcement — directional + token-specific filters.

KEY DISCOVERY (2026-05-09):
  S5 LONG  : 285 trades over 28m, sum +$59 768, avg +$210
  S5 SHORT : 170 trades over 28m, sum  −$6 647, avg  −$39
  S5 SHORT is a NET LOSER. The edge is EXCLUSIVELY long-side.

Per-token analysis on 28m also shows MINA, DOGE, LDO drain S5 by -$30k+.
The existing TRADE_BLACKLIST is global; S5 may need its own.

Tests:
  A) S5 LONG-only (kill all S5 SHORT entries)
  B) S5-specific per-token blacklist
  C) Per-(token, direction) blacklist (most granular)
  D) Combos

Walk-forward strict 4/4 on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_s5_creative
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


def main() -> None:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

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
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts,
                       early_exit_params=early_exit, **common)
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

    # ── (A) S5 LONG-ONLY ────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(A) S5 LONG-ONLY — kill all S5 SHORT entries':^110}")
    print("=" * 110)
    def make_skip_s5_short(coin, ts, strat, dir):
        return strat == "S5" and dir == -1
    name = "S5 LONG-only (skip all S5 SHORT)"
    positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip_s5_short)
    print(fmt_row(name, d_pnl, d_dd))

    # ── (B) S5 per-token blacklist ──────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) S5-SPECIFIC TOKEN BLACKLIST — block specific tokens for S5 only':^110}")
    print("=" * 110)
    blacklists = [
        ("MINA only",                       {"MINA"}),
        ("MINA + DOGE",                     {"MINA", "DOGE"}),
        ("MINA + DOGE + LDO",               {"MINA", "DOGE", "LDO"}),
        ("MINA + DOGE + LDO + AAVE",        {"MINA", "DOGE", "LDO", "AAVE"}),
        ("MINA + DOGE + LDO + AAVE + SNX",  {"MINA", "DOGE", "LDO", "AAVE", "SNX"}),
    ]
    for name_part, blacklist in blacklists:
        def make_skip(bl):
            def skip(coin, ts, strat, dir):
                return strat == "S5" and coin in bl
            return skip
        name = f"S5 BLACKLIST: {name_part}"
        positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(blacklist))
        print(fmt_row(name, d_pnl, d_dd))

    # ── (C) Per-(token, direction) blacklist ────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) PER-(token,direction) BLACKLIST — surgical':^110}")
    print("=" * 110)
    # From the 28m analysis, kill specific (token, direction) combinations
    # that are net losers
    surgical_lists = [
        ("Kill MINA-LONG only",
         {("MINA", 1)}),
        ("Kill MINA-LONG + DOGE-SHORT",
         {("MINA", 1), ("DOGE", -1)}),
        ("Kill MINA-LONG + DOGE-SHORT + LDO-LONG + LDO-SHORT",
         {("MINA", 1), ("DOGE", -1), ("LDO", 1), ("LDO", -1)}),
        ("Kill MINA-LONG + DOGE-SHORT + SNX-SHORT",
         {("MINA", 1), ("DOGE", -1), ("SNX", -1)}),
        ("Kill all losers (token,dir): MINA-L, DOGE-S, LDO-both, SNX-S, AAVE-both",
         {("MINA", 1), ("DOGE", -1), ("LDO", 1), ("LDO", -1), ("SNX", -1),
          ("AAVE", 1), ("AAVE", -1)}),
    ]
    for name_part, kill_set in surgical_lists:
        def make_skip(ks):
            def skip(coin, ts, strat, dir):
                return strat == "S5" and (coin, dir) in ks
            return skip
        name = f"SURGICAL: {name_part[:50]}"
        positives, d_pnl, d_dd = run_and_record(name, skip_fn=make_skip(kill_set))
        print(fmt_row(name, d_pnl, d_dd))

    # ── (D) COMBOS ──────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(D) COMBOS — LONG-only + token blacklist + direction-specific':^110}")
    print("=" * 110)
    combos = [
        ("LONG-only + MINA blacklist",
         lambda c, t, s, d: s == "S5" and (d == -1 or c == "MINA")),
        ("LONG-only + MINA + LDO blacklist",
         lambda c, t, s, d: s == "S5" and (d == -1 or c in {"MINA", "LDO"})),
        ("LONG-only + MINA + DOGE-LONG blacklist",
         lambda c, t, s, d: s == "S5" and (d == -1 or c == "MINA" or (c == "DOGE" and d == 1))),
    ]
    for name, skip_fn in combos:
        positives, d_pnl, d_dd = run_and_record(f"COMBO: {name}", skip_fn=skip_fn)
        print(fmt_row(f"COMBO: {name}", d_pnl, d_dd))

    # ── 4/4 strict winners ──────────────────────────────────────────
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
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # ── Top by sum ──────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'Top 15 by sum(ΔPnL)':^110}")
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
