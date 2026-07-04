"""Volet 3 — fragilité des seuils de la chaîne de sorties (±10 % / ±20 %)
+ degrés de liberté vs données + Sharpe déflaté (López de Prado).

Pour chaque scalaire perturbable de la chaîne (settings.Params), 4 runs
(×0.9/×1.1/×0.8/×1.2) × 4 fenêtres canoniques. FRAGILE = la contribution de
la règle (end_perturbé − end_ablaté, ablation du volet 2) tombe sous 50 % de
sa contribution de base à ±10 % — curve-fitting sur bruit probable.
Stops/holds (non ablatable) : sensibilité |Δ| vs base seulement.

Rien n'est auto-tuné : sortie descriptive pour le rapport, dump JSON.

Usage : python3 -m backtests.exit_fragility
"""
import dataclasses as dc
import json
import os
import statistics
import sys
import time
from datetime import datetime
from statistics import NormalDist

sys.path.insert(0, "/home/crypto")

from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
FACTORS = [0.9, 1.1, 0.8, 1.2]
START_CAP = 500.0
OUT = os.path.join(os.path.dirname(__file__), "output")


def _sc(field, rule=None):
    """Scalaire simple de Params."""
    return (field, rule, lambda f: {field: getattr(DEFAULT_PARAMS, field) * f})


def _s8_inlife(bucket, idx, rule="S8 in-life trail"):
    def build(f):
        d = {k: tuple(v) for k, v in DEFAULT_PARAMS.s8_inlife_params.items()}
        t = list(d[bucket]); t[idx] = t[idx] * f; d[bucket] = tuple(t)
        return {"s8_inlife_params": d}
    return (f"s8_inlife[{bucket}][{'act' if idx == 0 else 'off'}]", rule, build)


def _prop_trail(idx):
    def build(f):
        d = {s: dict(v) for s, v in DEFAULT_PARAMS.prop_trail_params.items()}
        cell = list(d["S9"]["bull"]); cell[idx] = cell[idx] * f
        d["S9"]["bull"] = tuple(cell)
        return {"prop_trail_params": d}
    return (f"prop_trail[S9.bull][{'arm' if idx == 0 else 'ratio'}]",
            "prop trail S9 bull", build)


def _hold(strat):
    def build(f):
        d = dict(DEFAULT_PARAMS.hold_hours); d[strat] = d[strat] * f
        return {"hold_hours": d}
    return (f"hold_hours[{strat}]", None, build)


SCALARS = [
    # (label, nom d'ablation volet 2 (None = pas ablatable), builder(factor))
    _sc("stop_loss_bps"), _sc("stop_loss_s8"),
    _sc("s9_early_exit_bps", "S9 early exit -500/8h"),
    _sc("s9_early_exit_hours", "S9 early exit -500/8h"),
    _sc("s10_trailing_trigger", "S10 trailing"),
    _sc("s10_trailing_offset", "S10 trailing"),
    _sc("s8_inlife_z_threshold", "S8 in-life trail"),
    _s8_inlife("bear", 0), _s8_inlife("bear", 1),
    _s8_inlife("neutral", 0), _s8_inlife("neutral", 1),
    _s8_inlife("bull", 0), _s8_inlife("bull", 1),
    _prop_trail(0), _prop_trail(1),
    _sc("prop_trail_z_threshold", "prop trail S9 bull"),
    _sc("s8_dead_t_h", "S8 dead-in-water"),
    _sc("s8_dead_mfe_max_bps", "S8 dead-in-water"),
    _sc("s9_early_dead_t_h", "S9 early dead 12h/150"),
    _sc("s9_early_dead_mfe_max_bps", "S9 early dead 12h/150"),
    _sc("btc_drop_cut_ret_4h_bps", "BTC drop cut LONG"),
    _sc("opp_floor_lock_ratio", "opp_floor 0.80"),
    _sc("opp_floor_min_gain_bps", "opp_floor 0.80"),
    _sc("traj_cut_btc_z_threshold", "traj cut S5"),
    _sc("traj_cut_decline_rate_min_bps_per_h", "traj cut S5"),
    _sc("traj_cut_time_since_mfe_min_h", "traj cut S5"),
    _sc("traj_cut_at_mae_slack_bps", "traj cut S5"),
    _sc("traj_cut_min_loss_bps", "traj cut S5"),
    _sc("runner_ext_hours", "runner ext S9"),
    _sc("runner_ext_min_mfe_bps", "runner ext S9"),
    _sc("runner_ext_min_cur_to_mfe", "runner ext S9"),
    _hold("S1"), _hold("S5"), _hold("S8"), _hold("S9"), _hold("S10"),
]
# DoF hors-Params (non perturbables ici, comptés au bilan) :
HARDCODED_DOF = 2   # S9 adaptive stop : base −500 et diviseur 8 (signals.py:184)


def main():
    print(f"{len(SCALARS)} scalaires perturbables × {len(FACTORS)} facteurs × "
          f"{len(WINDOWS)} fenêtres = {len(SCALARS)*len(FACTORS)*len(WINDOWS)} runs")
    print("Loading data…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in WINDOWS}

    abl = json.load(open(os.path.join(OUT, "exit_ablation.json")))
    base_end = {w: abl["base"][w]["end"] for w, _ in WINDOWS}
    abl_end = {}   # nom règle → {w: end sans la règle}
    for name, a in abl["ablations"].items():
        abl_end[name] = {w: base_end[w] - a["delta_usd"][w] for w, _ in WINDOWS}

    def run(params, w):
        br._P = params
        try:
            r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                           start_capital=START_CAP, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True)
            return r["end_capital"]
        finally:
            br._P = DEFAULT_PARAMS

    # Parité : défaut == base du volet 2
    for w, _ in WINDOWS[3:]:
        e = run(DEFAULT_PARAMS, w)
        assert abs(e - base_end[w]) < 0.01, f"parité cassée {w}: {e} vs {base_end[w]}"
    print("parité défaut vs volet 2 ✓", flush=True)

    results = {}
    t0 = time.time()
    for i, (label, rule, build) in enumerate(SCALARS):
        row = {}
        for f in FACTORS:
            try:
                kw = build(f)
            except Exception as e:
                row[f] = {"error": str(e)}; continue
            for w, _ in WINDOWS:
                e = run(dc.replace(DEFAULT_PARAMS, **kw), w)
                row.setdefault(f, {})[w] = round(e, 2)
        results[label] = {"rule": rule, "runs": row}
        print(f"  [{i+1}/{len(SCALARS)}] {label:<38} "
              f"[{time.time()-t0:.0f}s]", flush=True)

    # Analyse fragilité
    print("\n" + "=" * 100)
    print("FRAGILITÉ (contribution de la règle à ±10 % vs contribution de base ; "
          "base = volet 2)")
    print("=" * 100)
    fragile_flags = []
    for label, res in results.items():
        rule = res["rule"]
        if rule is None:
            # stop/hold : sensibilité brute
            sens = []
            for f in (0.9, 1.1):
                for w, _ in WINDOWS:
                    e = res["runs"].get(f, {}).get(w)
                    if e is not None:
                        sens.append(abs(e - base_end[w]) / base_end[w] * 100)
            print(f"  {label:<38} [stop/hold] sensibilité ±10% : "
                  f"max {max(sens):.1f}% de l'equity finale" if sens else label)
            continue
        flags = []
        for w, _ in WINDOWS:
            c_base = base_end[w] - abl_end[rule][w]
            if c_base <= 10:      # contribution de base déjà ≤ $10 : n/a
                continue
            for f in (0.9, 1.1):
                e = res["runs"].get(f, {}).get(w)
                if e is None:
                    continue
                c_pert = e - abl_end[rule][w]
                if c_pert < 0.5 * c_base:
                    flags.append(f"{w}@×{f}: {c_pert:.0f}$ vs base {c_base:.0f}$")
        verdict = "⚠ FRAGILE" if flags else "robuste"
        if flags:
            fragile_flags.append((label, flags))
        print(f"  {label:<38} {verdict}"
              + (f"  ({'; '.join(flags[:3])})" if flags else ""))

    # DoF vs données + Sharpe déflaté
    print("\n" + "=" * 100)
    print("HONNÊTETÉ STATISTIQUE")
    print("=" * 100)
    n_dof = len(SCALARS) + HARDCODED_DOF
    trades = json.load(open(os.path.join(OUT, "exit_ablation_base_trades.json")))
    nd = NormalDist()
    n_versions = 0
    for cl in ("/home/crypto/CHANGELOG.md", "/home/crypto/alfred/CHANGELOG.md"):
        try:
            n_versions += sum(1 for line in open(cl) if line.startswith("## v"))
        except FileNotFoundError:
            pass
    for w, _ in WINDOWS:
        tr = trades[w]
        n = len(tr)
        # concurrence moyenne → n effectif
        events = []
        for t in tr:
            if t.get("entry_t") and t.get("exit_t"):
                events.append((t["entry_t"], 1)); events.append((t["exit_t"], -1))
        events.sort()
        conc, cur, area, last = [], 0, 0.0, None
        for ts, dv in events:
            if last is not None and cur > 0:
                area += cur * (ts - last)
            cur += dv; last = ts
        span = (events[-1][0] - events[0][0]) if len(events) > 1 else 1
        avg_conc = max(1.0, area / span) if span else 1.0
        n_eff = n / avg_conc
        # Sharpe par trade + DSR
        rets = [t["net"] / 1e4 for t in tr if t.get("net") is not None]
        if len(rets) < 10 or statistics.pstdev(rets) == 0:
            print(f"  {w}: n={n} — trop peu pour un SR")
            continue
        mu, sd = statistics.mean(rets), statistics.pstdev(rets)
        sr = mu / sd
        m3 = sum((r - mu) ** 3 for r in rets) / len(rets) / sd ** 3
        m4 = sum((r - mu) ** 4 for r in rets) / len(rets) / sd ** 4
        T = len(rets)
        line = (f"  {w}: n={n} trades, concurrence moy {avg_conc:.1f} → "
                f"n_eff≈{n_eff:.0f} | DoF sortie={n_dof} "
                f"(ratio n_eff/DoF = {n_eff/n_dof:.1f}) | SR/trade={sr:.3f}")
        for N in (n_versions, 500, 2000):
            var_sr = (1 + 0.5 * sr * sr) / T
            emax = (var_sr ** 0.5) * ((1 - 0.5772) * nd.inv_cdf(1 - 1 / N)
                                      + 0.5772 * nd.inv_cdf(1 - 1 / (N * 2.71828)))
            denom = (1 - m3 * sr + (m4 - 1) / 4 * sr * sr) ** 0.5
            dsr = nd.cdf((sr - emax) * (T - 1) ** 0.5 / denom)
            line += f" | DSR(N={N})={dsr:.2f}"
        print(line)
    print(f"  (N essais : borne basse = {n_versions} versions shippées des 2 "
          f"CHANGELOG ; 500 ≈ 40 campagnes R&D × ~12 configs (mémoires) ; "
          f"2000 = majoration sweep-lourds. DSR = proba que le vrai SR > 0 "
          f"après correction du multiple-testing. DoF hors-Params : "
          f"{HARDCODED_DOF} (stop S9 adaptatif hardcodé signals.py).)")

    with open(os.path.join(OUT, "exit_fragility.json"), "w") as fh:
        json.dump({"generated": datetime.now().isoformat(),
                   "base_end": base_end, "results": results,
                   "fragile": [l for l, _ in fragile_flags]}, fh, indent=1)
    print(f"\nDump : {OUT}/exit_fragility.json — {len(fragile_flags)} seuil(s) fragile(s)")


if __name__ == "__main__":
    main()
