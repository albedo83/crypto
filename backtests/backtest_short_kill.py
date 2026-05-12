"""Walk-forward 4/4 strict — test agressifs sur les SHORTs problématiques.

User a observé sur 80 trades live : S5 SHORT et S9 SHORT cumulent −$79
(payoff 0.35x et 0.14x), tandis que S5 LONG seul fait +$37. Question :
peut-on serrer ou tuer ces 2 directions sans détruire la stratégie globale ?

Variantes testées :
1. SKIP S5 SHORT entièrement (nuclear)
2. SKIP S9 SHORT entièrement (n live=4, échantillon petit)
3. SKIP S5 SHORT + S9 SHORT
4. α=-0.8 sur S5 SHORT (vs -0.5 actuel) — modulator plus serré
5. α=-1.0 sur S5 SHORT — essentiellement zero en bull
6. α=-0.8 sur S9 (vs -0.5 actuel)
7. α=-1.0 sur S9
8. Combinaisons sweet spot

Note : v12.2.0 actuel = `ADAPTIVE_ALPHA={S1:+0.5, S8:-0.5, S9:-0.5}` +
`ADAPTIVE_ALPHA_DIR={("S5",-1):-0.5}`. S9 dampens ANY dir en bull; S5
seulement dir=-1.

Run: python3 -m backtests.backtest_short_kill
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
    get_adaptive_alpha,
)
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy,
)

CAP = 1000.0


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


def make_baseline_fn(btc_z_map: dict):
    """v12.2.0 baseline — α canonical."""
    def fn(cand, f, n_pos):
        z = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(f["t"], 0.0)))
        alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z))
    return fn


def make_alpha_override_fn(btc_z_map: dict, alpha_override: dict):
    """size_fn qui remplace l'alpha pour (strat, dir) spécifiques,
    fallback sur v12.2.0 canonical pour les autres."""
    def fn(cand, f, n_pos):
        z = max(-MACRO_Z_CLIP, min(MACRO_Z_CLIP, btc_z_map.get(f["t"], 0.0)))
        key = (cand["strat"], cand["dir"])
        if key in alpha_override:
            alpha = alpha_override[key]
        else:
            alpha = get_adaptive_alpha(cand["strat"], cand["dir"])
        return max(MACRO_MULT_MIN, min(MACRO_MULT_MAX, 1.0 + alpha * z))
    return fn


def make_skip_fn(skip_set: set):
    """skip si (strat, dir) ∈ skip_set."""
    def fn(coin, ts, strat, direction):
        return (strat, direction) in skip_set
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

    btc_z = compute_btc_z_rolling(data)
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
        apply_adaptive_modulator=False,  # explicit baseline via make_baseline_fn
    )

    WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    # Baseline v12.2.0
    print("\n" + "=" * 110)
    print(f"{'BASELINE — v12.2.0 modulator (α=-0.5 sur S5 SHORT et S9 toute dir)':^110}")
    print("=" * 110)
    baseline_fn = make_baseline_fn(btc_z)
    baseline = {}
    for label, start_ts in window_specs:
        r = run_window(features, data, start_ts_ms=start_ts, size_fn=baseline_fn, **common)
        baseline[label] = r
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%")

    print("\n" + "=" * 110)
    print(f"{'SHORT-KILL SWEEP':^110}")
    print("=" * 110)
    print("  ✓ = 4/4 strict pass (all Δpnl > 0 AND avg ΔDD ≤ +0.5pp)\n")
    print(f"  {'config':<44s}  {'Δ28m':>9s}  {'Δ12m':>8s}  {'Δ6m':>8s}  {'Δ3m':>8s}  "
          f"{'ΔDD avg':>8s}  pos")

    configs = []
    # SKIP variants
    configs.append(("SKIP S5 SHORT all conditions", "skip", {("S5", -1)}))
    configs.append(("SKIP S9 SHORT all conditions", "skip", {("S9", -1)}))
    configs.append(("SKIP S5+S9 SHORT all conditions", "skip", {("S5", -1), ("S9", -1)}))
    # ALPHA tightening variants
    configs.append(("α[S5,SHORT]=-0.8 (vs -0.5)", "alpha", {("S5", -1): -0.8}))
    configs.append(("α[S5,SHORT]=-1.0", "alpha", {("S5", -1): -1.0}))
    configs.append(("α[S5,SHORT]=-1.5 (super tight)", "alpha", {("S5", -1): -1.5}))
    configs.append(("α[S9,SHORT]=-0.8", "alpha", {("S9", -1): -0.8}))
    configs.append(("α[S9,SHORT]=-1.0", "alpha", {("S9", -1): -1.0}))
    configs.append(("α[S9,SHORT]=-1.5", "alpha", {("S9", -1): -1.5}))
    # Combined alpha
    configs.append(("α[S5,S9 SHORT]=-1.0", "alpha", {("S5", -1): -1.0, ("S9", -1): -1.0}))
    configs.append(("α[S5,S9 SHORT]=-1.5", "alpha", {("S5", -1): -1.5, ("S9", -1): -1.5}))

    results = []
    for name, kind, payload in configs:
        if kind == "skip":
            size_fn = baseline_fn  # keep v12.2.0 modulator on remaining trades
            skip_fn = make_skip_fn(payload)
            kwargs = dict(size_fn=size_fn, skip_fn=skip_fn)
        else:
            size_fn = make_alpha_override_fn(btc_z, payload)
            kwargs = dict(size_fn=size_fn)
        deltas, ddds = {}, {}
        for label, start_ts in window_specs:
            r = run_window(features, data, start_ts_ms=start_ts, **kwargs, **common)
            deltas[label] = r["pnl_pct"] - baseline[label]["pnl_pct"]
            ddds[label] = r["max_dd_pct"] - baseline[label]["max_dd_pct"]
        positives = sum(1 for v in deltas.values() if v > 0)
        avg_dd = sum(ddds.values()) / 4
        flag = "✓" if positives == 4 and avg_dd <= 0.5 else " "
        print(f"  {flag} {name:<42s}  {deltas['28m']:+8.1f}%  {deltas['12m']:+7.1f}%  "
              f"{deltas['6m']:+7.1f}%  {deltas['3m']:+7.1f}%  {avg_dd:+7.2f}pp  {positives}/4")
        results.append((name, deltas, avg_dd, positives))

    # Verdict
    print("\n" + "=" * 110)
    passers = [r for r in results if r[3] == 4 and r[2] <= 0.5]
    if passers:
        print(f"{len(passers)} config(s) pass 4/4 strict:")
        for name, deltas, avg_dd, _ in passers:
            print(f"  ✓ {name}: sum Δpnl = {sum(deltas.values()):+.1f}pp, ΔDD avg = {avg_dd:+.2f}pp")
        print("\nNext step: ship the most defensible passer, bump VERSION.")
    else:
        near_pass = [r for r in results if r[3] == 3]
        if near_pass:
            print(f"NO strict pass. {len(near_pass)} configs hit 3/4:")
            for name, deltas, avg_dd, _ in near_pass:
                tot = sum(deltas.values())
                neg = [k for k, v in deltas.items() if v < 0]
                print(f"  {name}: sum {tot:+.1f}pp, ΔDD {avg_dd:+.2f}pp, neg={neg}")
        else:
            print("NO strict pass, no 3/4 either. Patterns:")
            for name, deltas, avg_dd, pos in sorted(results, key=lambda r: -sum(r[1].values()))[:3]:
                print(f"  {pos}/4 {name}: sum {sum(deltas.values()):+.1f}pp, ΔDD {avg_dd:+.2f}pp")


if __name__ == "__main__":
    main()
