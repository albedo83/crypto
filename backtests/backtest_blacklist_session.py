"""Walk-forward — blacklist expansion + session filter sweep.

Diagnostic 2026-05-07 (paper+live, last 60d):
  - 5 tokens net-perdants : DYDX (-$93/10), BLUR (-$78/15), LDO (-$56/7),
    GALA (-$53/5), PENDLE (-$42/5). Cumul -$321 sur 42 trades.
  - WE session = trou noir : S5 sur WE = 27 trades / -$208 / 33% WR.
  - Night session aussi négatif : 8 trades / -$71. Sample petit.

Test exhaustif (blacklist + session + combinaisons) au walk-forward 4/4
sur 28m / 12m / 6m / 3m. Précédent v11.4.10 (blacklist SUI/IMX/LINK) a
passé +91/63/34/18% sur les 4 windows — donc le pattern "blacklist
de tokens net-perdants" peut survivre la validation long-terme.

Usage:
    python3 -m backtests.backtest_blacklist_session
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations

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

# Candidates from the diagnostic
BLACKLIST_CANDIDATES = ["DYDX", "BLUR", "LDO", "GALA", "PENDLE"]


def get_session(ts_ms: int) -> str:
    """Mirror bot.py session computation (UTC hours)."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if dt.weekday() >= 5:
        return "WE"
    h = dt.hour
    if h < 8: return "Asia"
    if h < 14: return "EU"
    if h < 21: return "US"
    return "Night"


def make_blacklist_skip(extra_blacklist: set[str]):
    """Skip candidates on tokens in the extra blacklist set."""
    if not extra_blacklist:
        return None
    def f(coin, ts, strat, direction):
        return coin in extra_blacklist
    return f


def make_session_skip(skip_combos: set[tuple[str, str]]):
    """Skip when (strat, session) ∈ skip_combos."""
    if not skip_combos:
        return None
    def f(coin, ts, strat, direction):
        return (strat, get_session(ts)) in skip_combos
    return f


def make_combined_skip(extra_blacklist, skip_combos):
    """Combine both filters."""
    def f(coin, ts, strat, direction):
        if coin in extra_blacklist:
            return True
        if (strat, get_session(ts)) in skip_combos:
            return True
        return False
    return f


def fmt_row(label, deltas_pnl, deltas_dd, baseline):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {label:55s}  "
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
        start_capital=CAP, oi_data=oi_data, early_exit_params=early_exit,
        funding_data=funding_data,
    )

    print("\nBaseline:")
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        baseline[label] = r
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def run_and_record(name, skip_fn):
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, skip_fn=skip_fn, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"results": rs, "d_pnl": d_pnl, "d_dd": d_dd,
                             "positives": positives}
        return positives, d_pnl, d_dd, rs

    # ── (1) Blacklist sweep ────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(1) BLACKLIST SWEEP — extra additions on top of {SUI, IMX, LINK}':^100}")
    print("=" * 100)

    # Singles
    print("\n  Singles (each token added alone):")
    for tok in BLACKLIST_CANDIDATES:
        positives, d_pnl, d_dd, _ = run_and_record(
            f"BL +{{{tok}}}", make_blacklist_skip({tok}))
        print(fmt_row(f"BL +{{{tok}}}", d_pnl, d_dd, baseline))

    # Top 2 pairs
    print("\n  Pairs (top 5 candidates choose 2):")
    for combo in combinations(BLACKLIST_CANDIDATES, 2):
        name = f"BL +{{{','.join(combo)}}}"
        positives, d_pnl, d_dd, _ = run_and_record(name, make_blacklist_skip(set(combo)))
        if positives >= 3:
            print(fmt_row(name, d_pnl, d_dd, baseline))

    # Triples
    print("\n  Triples (top 5 choose 3):")
    for combo in combinations(BLACKLIST_CANDIDATES, 3):
        name = f"BL +{{{','.join(combo)}}}"
        positives, d_pnl, d_dd, _ = run_and_record(name, make_blacklist_skip(set(combo)))
        if positives >= 3:
            print(fmt_row(name, d_pnl, d_dd, baseline))

    # All 5
    name = f"BL +ALL5 {{{','.join(BLACKLIST_CANDIDATES)}}}"
    positives, d_pnl, d_dd, _ = run_and_record(
        name, make_blacklist_skip(set(BLACKLIST_CANDIDATES)))
    print(f"\n  All 5 candidates:")
    print(fmt_row(name, d_pnl, d_dd, baseline))

    # ── (2) Session filter sweep ───────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(2) SESSION FILTER SWEEP':^100}")
    print("=" * 100)

    session_tests = [
        ("Skip S5/WE", {("S5", "WE")}),
        ("Skip S5/Night", {("S5", "Night")}),
        ("Skip S5/WE+Night", {("S5", "WE"), ("S5", "Night")}),
        ("Skip S9/WE", {("S9", "WE")}),
        ("Skip S9/Night", {("S9", "Night")}),
        ("Skip S9/WE+Night", {("S9", "WE"), ("S9", "Night")}),
        ("Skip S5+S9 on WE", {("S5", "WE"), ("S9", "WE")}),
        ("Skip S5+S9 on WE+Night",
         {("S5", "WE"), ("S5", "Night"), ("S9", "WE"), ("S9", "Night")}),
        ("Skip ALL on WE",
         {(s, "WE") for s in ("S1", "S5", "S8", "S9", "S10")}),
        ("Skip S5 on WE+EU", {("S5", "WE"), ("S5", "EU")}),
        ("Skip S10/WE", {("S10", "WE")}),
        ("Skip S10/Night", {("S10", "Night")}),
    ]
    for name, combos in session_tests:
        positives, d_pnl, d_dd, _ = run_and_record(name, make_session_skip(combos))
        print(fmt_row(name, d_pnl, d_dd, baseline))

    # ── (3) Combined blacklist + session ───────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(3) COMBINED — top blacklist + best session filter':^100}")
    print("=" * 100)

    # Take the best blacklist subset and combine with WE filters
    # (we'll judge "best" from sum of d_pnl)
    bl_only_results = [(name, all_results[name]) for name in all_results
                        if name.startswith("BL ")]
    bl_only_results.sort(key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    print(f"\n  Top 3 blacklist configs by sum(ΔpnL) :")
    for name, info in bl_only_results[:3]:
        print(f"    {name}: sum ΔpnL = {sum(info['d_pnl'].values()):+.1f}, "
              f"{info['positives']}/4")

    top_bl_names = [name for name, _ in bl_only_results[:3]]
    # Map name to set
    def bl_set_from_name(name):
        # extract content between { } or after +ALL5 token list
        s = name
        if "+ALL5" in s:
            return set(BLACKLIST_CANDIDATES)
        start = s.index("{") + 1
        end = s.rindex("}")
        return set(t.strip() for t in s[start:end].split(","))

    print(f"\n  Combining top-3 blacklists × {{S5/WE skip}}:")
    for bl_name in top_bl_names:
        bl_set = bl_set_from_name(bl_name)
        combo_name = f"{bl_name} + skip S5/WE"
        positives, d_pnl, d_dd, _ = run_and_record(
            combo_name, make_combined_skip(bl_set, {("S5", "WE")}))
        print(fmt_row(combo_name, d_pnl, d_dd, baseline))

    # ── 4/4 winners ────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^100}")
    print("=" * 100)
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
        for name, d_pnl, d_dd in found:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    print(f"\nRuntime: {time.time()-t0:.0f}s  ({len(all_results)} configs tested)")


if __name__ == "__main__":
    main()
