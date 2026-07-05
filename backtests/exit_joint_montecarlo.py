"""Monte Carlo JOINT des seuils de la chaîne de sorties (revue 2026-07-05, pt 3).

La fragilité du volet 3 (exit_fragility.py) était one-at-a-time : elle mesure
chaque scalaire isolément et rate les INTERACTIONS entre règles. Ici : chaque
draw perturbe TOUS les scalaires actifs ensemble (facteur i.i.d. U(1−δ, 1+δ)
par scalaire), et on regarde la DISTRIBUTION du P&L et du max DD.

Lecture :
  - base ≈ médiane de sa distribution perturbée → PLATEAU (les seuils sont des
    choix raisonnables dans une région plate — robuste).
  - base ≥ p90 de la distribution → PIC (la config exacte sur-performe presque
    toutes ses voisines jointes = curve-fitting probable, les interactions
    cassent en dehors du point exact).
  - queue basse (p5) → ce qu'on risque si les seuils sont « faux de ±δ ».

Rien n'est auto-tuné. Sortie : JSON + résumé console.
Usage : python3 -m backtests.exit_joint_montecarlo
"""
import dataclasses as dc
import json
import os
import random
import sys
import time
from datetime import datetime

sys.path.insert(0, "/home/crypto")

from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

OUT = os.path.join(os.path.dirname(__file__), "output")
SEED = 42

# Scalaires SIMPLES perturbés (actifs seulement — btc_drop_cut/dead_timeout
# retirées ; kill-switches et bools exclus).
SIMPLE_FIELDS = [
    "stop_loss_bps", "stop_loss_s8",
    "s9_early_exit_bps", "s9_early_exit_hours",
    "s10_trailing_trigger", "s10_trailing_offset",
    "s8_inlife_z_threshold",
    "prop_trail_z_threshold",
    "s8_dead_t_h", "s8_dead_mfe_max_bps",
    "s9_early_dead_t_h", "s9_early_dead_mfe_max_bps",
    "opp_floor_lock_ratio", "opp_floor_min_gain_bps",
    "traj_cut_btc_z_threshold", "traj_cut_decline_rate_min_bps_per_h",
    "traj_cut_time_since_mfe_min_h", "traj_cut_at_mae_slack_bps",
    "traj_cut_min_loss_bps",
    "runner_ext_hours", "runner_ext_min_mfe_bps", "runner_ext_min_cur_to_mfe",
]


def n_scalars():
    n = len(SIMPLE_FIELDS)
    n += 2 * len(DEFAULT_PARAMS.s8_inlife_params)           # (act, off) × buckets
    n += 2                                                   # prop_trail S9.bull
    n += len(DEFAULT_PARAMS.hold_hours)                      # holds par strat
    return n


def draw_params(rng: random.Random, delta: float):
    """Un Params avec TOUS les scalaires perturbés jointement."""
    u = lambda: rng.uniform(1 - delta, 1 + delta)
    kw = {f: getattr(DEFAULT_PARAMS, f) * u() for f in SIMPLE_FIELDS}
    kw["s8_inlife_params"] = {
        b: (v[0] * u(), v[1] * u())
        for b, v in DEFAULT_PARAMS.s8_inlife_params.items()}
    pt = {s: dict(v) for s, v in DEFAULT_PARAMS.prop_trail_params.items()}
    if "S9" in pt and "bull" in pt["S9"]:
        c = list(pt["S9"]["bull"])
        pt["S9"]["bull"] = (c[0] * u(), c[1] * u())
    kw["prop_trail_params"] = pt
    kw["hold_hours"] = {s: h * u() for s, h in DEFAULT_PARAMS.hold_hours.items()}
    return dc.replace(DEFAULT_PARAMS, **kw)


def main():
    plan = [("12m", 0.10, 200), ("12m", 0.20, 200),
            ("3m", 0.10, 200), ("3m", 0.20, 200),
            ("28m", 0.20, 100)]
    print(f"{n_scalars()} scalaires perturbés JOINTEMENT — plan :", plan, flush=True)

    print("Loading data…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in (("28m", 28), ("12m", 12), ("3m", 3))}

    def run(params, w):
        br._P = params
        try:
            r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                           start_capital=500.0, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True)
            return r["end_capital"], r.get("max_dd", 0.0)
        finally:
            br._P = DEFAULT_PARAMS

    base = {w: run(DEFAULT_PARAMS, w) for w in ("28m", "12m", "3m")}
    for w, (e, d) in base.items():
        print(f"base {w}: end={e:.2f} maxDD={d:.1f}%", flush=True)

    results = {"seed": SEED, "n_scalars": n_scalars(),
               "base": {w: {"end": e, "dd": d} for w, (e, d) in base.items()},
               "sweeps": {}}
    t0 = time.time()
    for w, delta, n in plan:
        rng = random.Random(SEED)          # même séquence de draws par sweep
        ends, dds = [], []
        for i in range(n):
            p = draw_params(rng, delta)
            e, d = run(p, w)
            ends.append(e); dds.append(d)
            if (i + 1) % 25 == 0:
                el = time.time() - t0
                print(f"  [{w} ±{int(delta*100)}%] {i+1}/{n} "
                      f"({el:.0f}s)", flush=True)
        se = sorted(ends)
        pct = lambda q: se[min(n - 1, int(q * n))]
        b = base[w][0]
        rank = sum(1 for e in ends if e < b) / n * 100
        results["sweeps"][f"{w}_d{int(delta*100)}"] = {
            "n": n, "delta": delta,
            "end_p5": round(pct(0.05), 2), "end_p25": round(pct(0.25), 2),
            "end_med": round(pct(0.50), 2), "end_p75": round(pct(0.75), 2),
            "end_p95": round(pct(0.95), 2),
            "base_end": round(b, 2),
            "base_rank_pct": round(rank, 1),
            "frac_losing": round(sum(1 for e in ends if e < 500.0) / n * 100, 1),
            "dd_med": round(sorted(dds)[n // 2], 1),
            "dd_p95": round(sorted(dds)[min(n - 1, int(0.95 * n))], 1),
            "base_dd": round(base[w][1], 1),
        }
        r = results["sweeps"][f"{w}_d{int(delta*100)}"]
        print(f"{w} ±{int(delta*100)}% : base={b:.0f} (rang p{r['base_rank_pct']:.0f}) "
              f"| perturbé p5={r['end_p5']:.0f} med={r['end_med']:.0f} "
              f"p95={r['end_p95']:.0f} | perdants {r['frac_losing']}% "
              f"| DD med {r['dd_med']}% p95 {r['dd_p95']}%", flush=True)

    out = os.path.join(OUT, "exit_joint_mc.json")
    json.dump(results, open(out, "w"), indent=1)
    print(f"\n→ {out}  ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
