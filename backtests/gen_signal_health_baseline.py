"""Attente de conception par signal — la baseline que la supervision compare au live.

Motivation (2026-07-31) : `strategy_review.detect_strat_drift` compare « 30
derniers jours » à « lifetime », or la base live ne contient que 31 trades depuis
le reset du 2026-07-09 et il n'existe aucune archive. « lifetime » ≈ « récent »,
donc `wr_drop ≈ 0` **par construction** : un test qui ne peut pas échouer ne teste
rien. Vérifié : `STRAT_DRIFT` n'a jamais déclenché en sept revues hebdomadaires,
et la dégradation supposée de S5 a tenu six jours sur des données fausses sans que
rien ne la contredise.

Le défaut n'est pas un seuil trop laxiste, **c'est la référence** : un signal déjà
dégradé au démarrage du bot paraît parfaitement stable. La référence doit venir du
BACKTEST — désormais à parité vérifiée (v1.17.2) et auto-décrit (v1.17.4).

Ce script dumpe, par signal :
  - la POPULATION des résultats par trade (net_bps, gagnant/perdant) → permet un
    test de calibration par rééchantillonnage plutôt qu'un seuil arbitraire ;
  - la CADENCE attendue (trades / 30 j) → un signal qui se tait est la signature
    live d'une dérive d'entrée, exactement la famille du bug de carte sectorielle
    (7 tokens tradés par S5 en live là où le backtest en voyait 11) ;
  - l'empreinte d'exécution, pour savoir sous quelles conditions elle a été
    produite.

Usage : python3 -m backtests.gen_signal_health_baseline
"""

from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.fingerprint import banner, fingerprint
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12)]
START_CAP = 500.0
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "analysis", "output", "signal_health_baseline.json")


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Chargement…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS
    print(banner(DEFAULT_PARAMS, data,
                 extra={"slippage_bps": br.BACKTEST_SLIPPAGE_BPS}), flush=True)

    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "fingerprint": fingerprint(DEFAULT_PARAMS, data,
                                      extra={"slippage_bps": br.BACKTEST_SLIPPAGE_BPS}),
           "windows": {}}

    for wlab, months in WINDOWS:
        s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        r = run_window(features, data, sectors, dxy, start_ts_ms=s_ms,
                       end_ts_ms=end_ms, start_capital=START_CAP, oi_data=oi,
                       funding_data=funding, apply_adaptive_modulator=True,
                       aligned=True, margin_check=True, mfe_on_close=True,
                       realistic_trail_booking=True)
        span_days = months * 30.44
        per = defaultdict(list)
        for t in r["trades"]:
            per[t["strat"]].append(t)
        w = {}
        print(f"\n{wlab} — {r['n_trades']} trades sur {span_days:.0f} jours")
        print(f"  {'strat':6s}{'n':>6s}{'WR':>8s}{'net moy':>10s}"
              f"{'net méd':>10s}{'trades/30j':>12s}")
        for s in sorted(per, key=lambda k: -len(per[k])):
            v = per[s]
            nets = [t["net"] for t in v]
            wins = sum(1 for t in v if t["pnl"] > 0)
            cad = len(v) / span_days * 30.0
            w[s] = {
                "n": len(v),
                "wr": round(wins / len(v) * 100, 2),
                "net_mean": round(st.mean(nets), 2),
                "net_median": round(st.median(nets), 2),
                "trades_per_30d": round(cad, 2),
                # population brute : c'est elle qui permet le rééchantillonnage
                "net_bps": [round(x, 2) for x in nets],
                "wins": [1 if t["pnl"] > 0 else 0 for t in v],
            }
            print(f"  {s:6s}{len(v):>6d}{w[s]['wr']:>7.1f}%{w[s]['net_mean']:>10.1f}"
                  f"{w[s]['net_median']:>10.1f}{cad:>12.2f}")
        out["windows"][wlab] = {"span_days": round(span_days, 1), "strats": w}

    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"), default=str)
    print(f"\nDump : {OUT}  ({os.path.getsize(OUT)/1024:.0f} ko)")
    print("\nÀ régénérer après TOUT changement de règles, de paramètres ou "
          "d'univers.\nLa supervision compare le live à cette population, pas à "
          "son propre historique.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
