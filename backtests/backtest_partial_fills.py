"""Walk-forward — partial fill simulation + thin-token haircut.

Empirical observation (2026-05-08): real live trades show 39% of trades hit
partial fills (≥$30 gap from typical), but those partials saved more loss
than they capped wins (−$25 actual vs −$72 projected at full size on 31
trades). Hypothesis: the slippage cap acts as an implicit "thin liquidity =
probably bad signal" filter.

Two questions tested side-by-side:

  A) PARTIAL FILL SIMULATION — is full-fill (relaxed cap) better than current
     partial-fill behavior on S5/S9? Backtest assumes 100% fills, so we
     simulate current state by scaling DOWN S5/S9 sizes and compare to
     baseline (= relaxed-cap world).

  B) THIN-TOKEN HAIRCUT — formalize what partial fills do accidentally:
     apply a deliberate size haircut to bottom-volume tokens. Tests if a
     volume-based filter outperforms the implicit slippage-cap filter.

Walk-forward 4/4 strict on 28m / 12m / 6m / 3m.

Usage:
    python3 -m backtests.backtest_partial_fills
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


def compute_token_volume_ranks(data: dict) -> dict[str, float]:
    """Compute static avg-volume rank [0..1] per token across all 3y of candles.

    1.0 = highest volume (BTC, ETH...), 0.0 = lowest. Used as proxy for HL
    order book depth (real fills aren't tracked in candles, but daily volume
    is the cleanest available proxy).
    """
    avg_vol = {}
    for coin, candles in data.items():
        if not candles:
            continue
        # Volume in quote currency proxy: candles have v field (vol in coin units)
        # multiply by close price for USD-equivalent volume
        vols = [c.get("v", 0) * c.get("c", 0) for c in candles[-2000:]]
        avg_vol[coin] = sum(vols) / max(1, len(vols))
    sorted_coins = sorted(avg_vol.items(), key=lambda kv: kv[1])
    n = len(sorted_coins)
    rank = {coin: i / (n - 1) if n > 1 else 0.5 for i, (coin, _) in enumerate(sorted_coins)}
    return rank


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
        early_exit_params=early_exit,
    )

    # Token volume ranks (for Test B)
    vol_rank = compute_token_volume_ranks(data)
    print("\nToken volume ranks (static, 0=thinnest, 1=thickest):")
    for c in sorted(vol_rank, key=lambda x: vol_rank[x]):
        print(f"  {c:8s} : {vol_rank[c]:.2f}")

    print("\nBaseline (full fills, no haircut):")
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

    # ── (A) Partial-fill simulation: scale DOWN S5/S9 ──────────────────
    # If smaller (= simulated current state) outperforms baseline (= relaxed cap),
    # current cap is protective. If baseline wins, relaxing the cap would help.
    print("\n" + "=" * 110)
    print(f"{'(A) PARTIAL FILL SIM — scale S5/S9 size by factor':^110}")
    print(f"{'IF Δ < 0 (worse than baseline) → partial fills HURT, RELAX cap':^110}")
    print(f"{'IF Δ > 0 (better than baseline) → partial fills HELP, KEEP cap':^110}")
    print("=" * 110)
    for s5_factor in [0.70, 0.85, 1.00, 1.15, 1.30]:
        for s9_factor in [0.50, 0.70, 0.85, 1.00]:
            mult = {"S5": s5_factor, "S9": s9_factor}
            name = f"S5×{s5_factor:.2f} S9×{s9_factor:.2f}"
            positives, d_pnl, d_dd = run_and_record(name, size_multiplier=mult)
            if abs(sum(d_pnl.values())) > 100:  # significant
                print(fmt_row(name, d_pnl, d_dd))

    # ── (B) Thin-token volume haircut ───────────────────────────────────
    # Apply a multiplier based on token volume rank. size_fn signature:
    #   (cand, feature_dict, n_positions) → multiplier
    print("\n" + "=" * 110)
    print(f"{'(B) THIN-TOKEN HAIRCUT — bottom N% of volume tokens get size×K':^110}")
    print("=" * 110)
    for thin_frac in [0.20, 0.30, 0.40, 0.50]:
        # Tokens in bottom thin_frac of volume rank get the haircut
        thin_tokens = {c for c, r in vol_rank.items() if r < thin_frac}
        for haircut in [0.5, 0.7, 0.85]:
            def make_fn(thin, h):
                def fn(cand, f, n):
                    return h if cand["coin"] in thin else 1.0
                return fn
            size_fn = make_fn(thin_tokens, haircut)
            name = f"bottom-{int(thin_frac*100):2d}%-vol × {haircut:.2f}  ({len(thin_tokens)} tokens)"
            positives, d_pnl, d_dd = run_and_record(name, size_fn=size_fn)
            if positives >= 3 or sum(d_pnl.values()) > 50:
                print(fmt_row(name, d_pnl, d_dd))

    # ── (C) Combo: thin-token haircut + S5/S9 boost ─────────────────────
    # Best of both worlds? Apply haircut to thin tokens AND boost S5/S9 size
    print("\n" + "=" * 110)
    print(f"{'(C) COMBOS — thin-token haircut + S5/S9 boost':^110}")
    print("=" * 110)
    thin_tokens = {c for c, r in vol_rank.items() if r < 0.30}
    for hc in [0.5, 0.7]:
        for s_boost in [1.15, 1.30]:
            def make_fn(thin, h):
                def fn(cand, f, n):
                    return h if cand["coin"] in thin else 1.0
                return fn
            mult = {"S5": s_boost, "S9": s_boost}
            name = f"thin×{hc:.1f} + S5/S9×{s_boost:.2f}"
            positives, d_pnl, d_dd = run_and_record(
                name, size_multiplier=mult, size_fn=make_fn(thin_tokens, hc))
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
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found[:15]:
            print(f"  {name:55s}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

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
