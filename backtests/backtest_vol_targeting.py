"""Vol-targeting sweep (2026-07-05) — le « désigné coupable » du MC joint.

Premise-EDA (`eda_vol_targeting.py`) : P1 ✓ (vol respire, p90/p10 2.9×),
P2 ✓ (le sizing actuel ne compense pas, Spearman(size, vol) ≈ 0, dispersion
de risque 3×), MAIS P3 RETOURNE l'attente naïve : le tercile volatil est le
plus rentable (+182.8 bps vs +82.6, 48 % du PnL) malgré 4.5× plus de
catastrophe stops. La vol est le carburant du fade.

Prédiction PRÉ-ENREGISTRÉE : le VT plein PERD du PnL (il rabote le segment
qui paie) ; il ne peut gagner sa place que par le DD/Calmar en strict 4/4.
Le harnais est corrigé (size_fn_keep_modulator=True) pour que size_fn
s'applique EN PLUS du modulateur macro, pas à sa place.

Variantes (mult = f(vol_7d du token à l'entrée), médiane globale = pivot) :
  VT_full   : med/vol            clip [0.5, 2.0]  (égalisation complète)
  VT_half   : sqrt(med/vol)      clip [0.6, 1.5]  (demi-teinte)
  VT_shrink : min(1, med/vol)    clip [0.5, 1.0]  (rabote le volatil, ne
              booste jamais le calme — la variante « assurance pure »)

Usage : python3 -m backtests.backtest_vol_targeting
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, "/home/crypto")

from datetime import datetime
from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
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

    all_vols = [row["vol_7d"] for rows in features.values() for row in rows
                if row.get("vol_7d")]
    med = float(np.median(all_vols))
    print(f"pivot : médiane vol_7d globale = {med:.4f}  (n={len(all_vols)})")

    def make_fn(kind):
        def fn(cand, f, n_pos):
            v = f.get("vol_7d") or med
            r = med / max(v, 1e-9)
            if kind == "full":
                return min(2.0, max(0.5, r))
            if kind == "half":
                return min(1.5, max(0.6, math.sqrt(r)))
            return min(1.0, max(0.5, r))          # shrink
        return fn

    def run(w, fn):
        r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                       start_capital=500.0, oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True,
                       size_fn=fn, size_fn_keep_modulator=fn is not None)
        return r["end_capital"], r.get("max_dd_pct", 0.0)

    base = {w: run(w, None) for w, _ in WINDOWS}
    print("parité base :", {w: round(base[w][0]) for w, _ in WINDOWS}, flush=True)

    print(f"\n{'variante':<10} {'fen':<5} {'end $':>8} {'Δ$ vs base':>11} "
          f"{'Δ%':>7} {'DD':>7} {'ΔDD pp':>7}")
    verdicts = {}
    for kind in ("full", "half", "shrink"):
        fn = make_fn(kind)
        passes = 0
        for w, _ in WINDOWS:
            e, d = run(w, fn)
            eb, db = base[w]
            dpnl, ddd = e - eb, d - db     # ΔDD > 0 = DD moins profond (dd négatif)
            ok = (e >= eb * 0.98) and (d >= db)   # PnL quasi-tenu ET DD pas pire
            passes += ok
            print(f"VT_{kind:<7} {w:<5} {e:>8.0f} {dpnl:>+11.0f} "
                  f"{(e/eb-1)*100:>+6.1f}% {d:>6.1f}% {ddd:>+6.1f}", flush=True)
        verdicts[kind] = passes
        print(f"  → VT_{kind} : {passes}/4 fenêtres (critère : PnL ≥ −2 % ET DD ≤ base)")
    print("\nVerdicts :", verdicts)


if __name__ == "__main__":
    main()
