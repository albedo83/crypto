"""S5 downsize (not skip) creative — preserve slot occupancy.

Insight: skipping bad S5 trades frees a slot for worse alternatives.
Instead, DOWNSIZE bad (token, direction) combos to ~30%, keeping the
slot busy but reducing exposure.

Tests:
  A) Downsize S5 SHORT × {0.3, 0.5, 0.7}
  B) Downsize per-token (MINA, DOGE, LDO, AAVE) × {0.3, 0.5}
  C) Per-(token, direction) downsize × {0.3, 0.5}
  D) Combine: downsize + size_multiplier on good signals to compensate

Walk-forward 4/4 strict.
"""
from __future__ import annotations
import time
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
    end_dt = datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc)
    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]
    common = dict(sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=latest_ts,
                  start_capital=CAP, oi_data=oi_data, funding_data=funding_data)

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
        if "early_exit_params" not in kwargs: kwargs["early_exit_params"] = early_exit
        rs = {}
        for lab, st in window_specs:
            r = run_window(features, data, start_ts_ms=st, **kwargs, **common)
            rs[lab] = r
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives}
        return positives, d_pnl, d_dd

    # ── (A) Downsize S5 SHORT ────────────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(A) DOWNSIZE S5 SHORT — keep slot, shrink exposure':^110}")
    print("=" * 110)
    for f in [0.3, 0.5, 0.7, 0.85]:
        def make_fn(factor):
            def fn(cand, feat, n_pos):
                if cand["strat"] == "S5" and cand["dir"] == -1:
                    return factor
                return 1.0
            return fn
        name = f"S5 SHORT × {f:.2f}"
        positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(f))
        print(fmt_row(name, d_pnl, d_dd))

    # ── (B) Downsize specific tokens ─────────────────────────
    print("\n" + "=" * 110)
    print(f"{'(B) DOWNSIZE specific tokens for S5':^110}")
    print("=" * 110)
    target_tokens_sets = [
        ("MINA",                {"MINA"}),
        ("MINA+DOGE",           {"MINA", "DOGE"}),
        ("MINA+DOGE+LDO",       {"MINA", "DOGE", "LDO"}),
        ("MINA+DOGE+LDO+AAVE",  {"MINA", "DOGE", "LDO", "AAVE"}),
    ]
    for name_part, tokens in target_tokens_sets:
        for f in [0.3, 0.5]:
            def make_fn(toks, factor):
                def fn(cand, feat, n_pos):
                    if cand["strat"] == "S5" and cand["coin"] in toks:
                        return factor
                    return 1.0
                return fn
            name = f"S5 {name_part} × {f:.2f}"
            positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(tokens, f))
            print(fmt_row(name, d_pnl, d_dd))

    # ── (C) Per-(token, direction) downsize ──────────────────
    print("\n" + "=" * 110)
    print(f"{'(C) PER-(token,direction) DOWNSIZE × 0.3':^110}")
    print("=" * 110)
    surgical = [
        ("MINA-L+DOGE-S", {("MINA", 1), ("DOGE", -1)}),
        ("MINA-L+DOGE-S+LDO-both", {("MINA", 1), ("DOGE", -1), ("LDO", 1), ("LDO", -1)}),
        ("All worst (token,dir)", {("MINA", 1), ("DOGE", -1), ("LDO", 1), ("LDO", -1),
                                    ("SNX", -1), ("AAVE", 1), ("AAVE", -1)}),
    ]
    for name_part, kill_set in surgical:
        for f in [0.3, 0.5]:
            def make_fn(ks, factor):
                def fn(cand, feat, n_pos):
                    if cand["strat"] == "S5" and (cand["coin"], cand["dir"]) in ks:
                        return factor
                    return 1.0
                return fn
            name = f"S5 surgical {name_part[:30]} × {f:.2f}"
            positives, d_pnl, d_dd = run_and_record(name, size_fn=make_fn(kill_set, f))
            print(fmt_row(name, d_pnl, d_dd))

    # ── (D) Combine downsize bad + UPSIZE good ─────────────────
    print("\n" + "=" * 110)
    print(f"{'(D) COMBO — downsize bad S5 + amplify good S5 tokens':^110}")
    print("=" * 110)
    # Top 5 best S5 tokens from analysis: OP, GALA, NEAR, STX, COMP
    good_tokens = {"OP", "GALA", "NEAR", "STX", "COMP", "CRV"}
    bad_tokens = {"MINA", "DOGE", "LDO", "AAVE"}
    for good_factor in [1.2, 1.5]:
        for bad_factor in [0.3, 0.5]:
            def make_fn(g, b, gf, bf):
                def fn(cand, feat, n_pos):
                    if cand["strat"] != "S5":
                        return 1.0
                    if cand["coin"] in g: return gf
                    if cand["coin"] in b: return bf
                    return 1.0
                return fn
            name = f"S5 good×{good_factor:.1f} bad×{bad_factor:.1f}"
            positives, d_pnl, d_dd = run_and_record(name,
                size_fn=make_fn(good_tokens, bad_tokens, good_factor, bad_factor))
            print(fmt_row(name, d_pnl, d_dd))

    # ── 4/4 winners ──
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

    # ── Top 15 ──
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

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
