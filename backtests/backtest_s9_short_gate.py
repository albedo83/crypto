"""Walk-forward S9 SHORT bull-gate / dé-amp (2026-07-07).

Premise-EDA (`eda_s9_short.py`) : PASS — la perte S9 SHORT est 100 % en bull
(z>0.5, médiane −206), signe conforme au design. Ce sweep teste si intervenir
en bull survit strict 4/4.

Deux leviers :
  - DÉ-AMP : adaptive_alpha_dir {("S9",-1): a} — btc_z INTERNE exact, mais
    PLAFONNÉ (macro_mult_min=0.3) → levier faible (ne mord qu'en z∈[0.5,1.0]).
  - GATE : skip_fn skippe S9 SHORT quand btc_z>seuil — btc_z EXTERNE (répliqué,
    validé contre l'ancre BLUR), levier fort (retrait total + slot libéré).

Critère strict par fenêtre : end_capital ≥ base ET max_dd ≥ base (dd négatif :
≥ = moins profond). PASS = 4/4. Null sur la meilleure config (13 shuffles du
signe de la condition régime).

Usage : python3 -m backtests.backtest_s9_short_gate
"""
import dataclasses as dc
import os
import random
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from datetime import datetime
from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.eda_s9_short import build_btc_z
from alfred.settings import DEFAULT_PARAMS

WINDOWS = (("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3))


def main():
    print("Loading data…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in WINDOWS}

    zmap = build_btc_z(data)
    ts_sorted = np.array(sorted(zmap.keys()), dtype=np.int64)
    zarr = np.array([zmap[t] for t in ts_sorted])

    def z_at(ts_ms):
        i = np.searchsorted(ts_sorted, ts_ms, side="right") - 1
        return zarr[i] if i >= 0 else 0.0

    def gate_fn(threshold):
        def fn(coin, ts, strat, dr):
            return strat == "S9" and dr == -1 and z_at(ts) > threshold
        return fn

    def run(w, params=DEFAULT_PARAMS, skip_fn=None):
        br._P = params                       # <-- run_window lit le global _P
        try:
            r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                           start_capital=500.0, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True, skip_fn=skip_fn)
            return r["end_capital"], r.get("max_dd_pct", 0.0)
        finally:
            br._P = DEFAULT_PARAMS

    base = {w: run(w) for w, _ in WINDOWS}
    print("base :", {w: f"{base[w][0]:.0f}/{base[w][1]:.1f}%" for w, _ in WINDOWS}, flush=True)

    variants = []
    for a in (-0.75, -1.0, -1.5):
        variants.append((f"deamp α={a}",
                         dict(params=dc.replace(DEFAULT_PARAMS,
                              adaptive_alpha_dir={("S9", -1): a}))))
    for t in (0.5, 1.0):
        variants.append((f"gate z>{t}", dict(skip_fn=gate_fn(t))))

    print(f"\n{'variante':<14} {'fen':<5} {'end $':>8} {'Δ$':>9} {'DD':>7} {'ΔDD':>7} {'ok'}")
    results = {}
    for label, kw in variants:
        passes = 0
        for w, _ in WINDOWS:
            e, d = run(w, **kw)
            eb, db = base[w]
            ok = (e >= eb - 0.01) and (d >= db - 0.01)   # PnL≥base ET DD pas pire
            passes += ok
            print(f"{label:<14} {w:<5} {e:>8.0f} {e-eb:>+9.0f} {d:>6.1f}% "
                  f"{d-db:>+6.1f} {'✓' if ok else '·'}", flush=True)
        results[label] = passes
        print(f"  → {label} : {passes}/4" + ("  *** PASS STRICT ***" if passes == 4 else ""))

    # sanity : combien de S9 SHORT le gate skippe par fenêtre
    print("\n  Sanity — S9 SHORT skippés par le gate z>0.5 :")
    import json
    trades = json.load(open(os.path.join(os.path.dirname(__file__),
                       "output/exit_ablation_base_trades.json")))
    for w, _ in WINDOWS:
        s9s = [t for t in trades[w] if t["strat"] == "S9" and t["dir"] == -1]
        skipped = sum(1 for t in s9s if z_at(t["entry_t"]) > 0.5)
        print(f"    {w}: {skipped}/{len(s9s)} S9 SHORT en bull (skippés)")

    print("\nVerdicts :", results)
    best = max(results, key=results.get)
    if results[best] == 4:
        print(f"\n→ meilleure config PASS : {best} — lancer le null test avant tout ship.")
    else:
        print(f"\n→ aucune config strict 4/4 (best {best} {results[best]}/4). S9 SHORT "
              f"reste tel quel — rapport, pas de ship.")


if __name__ == "__main__":
    main()
