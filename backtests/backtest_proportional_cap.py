"""Cap notionnel PROPORTIONNEL vs FIXE $500 (2026-07-07).

Origine du cap fixe = réaction de peur (notionnel qui partait mal, chiffre rond,
pas d'analyse), validé post-hoc 4/4 en juin. Fonction RÉELLE = prévenir la
cascade de marge (à préserver). Forme fixe jamais questionnée.

Premise-EDA (ci-dessus) : PASS — le cap fixe crée une concentration qui dérive
avec le capital (48-89 % à petit capital, throttle 6 % à gros), à contre-emploi
de l'intention (contrôle de concentration). Un cap k×equity la garde constante.

Sweep : baseline $500 fixe vs proportionnel k∈{0.3,0.5,0.83,1.2}, AVEC
margin_check (le garde-fou validé — sans lui le sweep est un mirage, cf.
notional_cap_walkforward juin). 4 fenêtres, ΔPnL + ΔDD + n_margin_skip.

Critère strict : PnL ≥ base ET DD ≤ base ET cascades ≤ base×1.2 sur 4/4.
Rien n'est auto-shippé — je rapporte, l'utilisateur tranche.

Usage : python3 -m backtests.backtest_proportional_cap
"""
import sys

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
    print("Loading…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in WINDOWS}

    br._P = DEFAULT_PARAMS

    def run(w, max_notional_fn=None):
        r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                       start_capital=500.0, oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True,
                       max_notional_fn=max_notional_fn)
        return r["end_capital"], r.get("max_dd_pct", 0.0), r.get("n_margin_skip", 0)

    base = {w: run(w) for w, _ in WINDOWS}
    print("base (fixe $500) :", {w: f"{base[w][0]:.0f}/{base[w][1]:.0f}%/{base[w][2]}casc"
                                  for w, _ in WINDOWS}, flush=True)

    print(f"\n{'variante':<14} {'fen':<5} {'end$':>8} {'Δ$':>9} {'DD':>7} {'ΔDD':>6} "
          f"{'casc':>5} {'Δcasc':>6} {'ok'}")
    results = {}
    for k in (0.3, 0.5, 0.83, 1.2):
        fn = (lambda kk: (lambda coin, ts, cap: kk * cap))(k)
        passes = 0
        for w, _ in WINDOWS:
            e, d, c = run(w, max_notional_fn=fn)
            eb, db, cb = base[w]
            ok = (e >= eb - 0.01) and (d >= db - 0.01) and (c <= cb * 1.2 + 1)
            passes += ok
            print(f"prop k={k:<9} {w:<5} {e:>8.0f} {e-eb:>+9.0f} {d:>6.0f}% "
                  f"{d-db:>+5.0f} {c:>5} {c-cb:>+6} {'✓' if ok else '·'}", flush=True)
        results[f"k={k}"] = passes
        print(f"  → prop k={k} : {passes}/4" + ("  *** PASS STRICT ***" if passes == 4 else ""))

    print("\nVerdicts :", results)
    best = max(results, key=results.get)
    if results[best] == 4:
        print(f"→ {best} PASS strict — candidat, null/robustesse avant tout ship.")
    else:
        print(f"→ aucun proportionnel strict 4/4 (best {best} {results[best]}/4). "
              f"Le cap fixe $500 tient — la forme fixe, même née d'une peur, est "
              f"dure à battre (throttle petit-agressif/gros-conservateur robuste).")


if __name__ == "__main__":
    main()
