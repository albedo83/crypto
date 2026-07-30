"""Reprise des SIGNAUX depuis zéro sous booking honnête — sommes-nous en phase ?

Motivation (2026-07-30, demande utilisateur) : le backtest bookait les sorties
par trail à leur niveau théorique jusqu'au 2026-07-25 (v1.15.5), ce qui gonflait
le P&L d'environ moitié sur chaque fenêtre OOS. Or TOUTES les validations qui ont
produit le bot actuel — sélection des signaux comprise — ont tourné sur ce
backtest-là. Re-baseliner les CHIFFRES (fait) ne re-valide pas les DÉCISIONS.

Question posée ici : si on refaisait aujourd'hui la sélection des signaux avec un
backtest honnête, retomberait-on sur les 5 signaux du bot actuel ?

Deux lectures complémentaires par signal :
  CONTRIBUTION  = stack complet − stack sans le signal
                  → le garderait-on dans le stack ? (effet marginal, portefeuille)
  AUTONOME      = le signal seul, sans les autres
                  → l'aurait-on découvert ? (edge propre, hors interaction)

Un signal négatif sur les DEUX n'aurait jamais dû être shippé. Négatif en
autonome mais positif en contribution = effet de portefeuille (légitime mais
fragile : il dépend des autres signaux). Positif sur les deux = solide.

ASYMÉTRIE À GARDER EN TÊTE — la convergence ne prouve pas grand-chose (même
données, même optimum in-sample), la DIVERGENCE prouve beaucoup. Cet audit peut
réfuter le bot actuel, il ne peut pas le certifier.

Usage : python3 -m backtests.backtest_signal_rederivation
"""

from __future__ import annotations

import dataclasses as dc
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
START_CAP = 500.0
SIGNALS = ["S1", "S5", "S8", "S9", "S10"]


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    print(f"Données jusqu'au {end_dt:%Y-%m-%d}. Booking trails : RÉALISTE.\n",
          flush=True)

    def run_all(params, label, keep_trades=False):
        br._P = params
        out = {}
        t0 = time.time()
        for w, months in WINDOWS:
            s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=s_ms, end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            out[w] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                      "n": r["n_trades"]}
            if keep_trades:
                per = defaultdict(lambda: [0, 0.0])
                for t in r["trades"]:
                    k = per[t.get("strat")]
                    k[0] += 1
                    k[1] += t.get("pnl", 0.0)
                out[w]["per_strat"] = {k: {"n": v[0], "pnl": round(v[1], 2)}
                                       for k, v in per.items()}
        print(f"  {label:22s} " + " ".join(
            f"{w}=${out[w]['end']:>8.0f}" for w, _ in WINDOWS)
            + f"   [{time.time()-t0:.0f}s]", flush=True)
        return out

    print("=== socle : stack complet (les 5 signaux)")
    base = run_all(DEFAULT_PARAMS, "stack complet", keep_trades=True)

    print("\n=== CONTRIBUTION : stack complet − stack SANS le signal")
    without = {}
    for s in SIGNALS:
        p = dc.replace(DEFAULT_PARAMS,
                       enabled_strategies=frozenset(x for x in SIGNALS if x != s))
        without[s] = run_all(p, f"sans {s}")

    print("\n=== AUTONOME : le signal SEUL")
    alone = {}
    for s in SIGNALS:
        p = dc.replace(DEFAULT_PARAMS, enabled_strategies=frozenset({s}))
        alone[s] = run_all(p, f"{s} seul")

    # ── verdicts ────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("CONTRIBUTION MARGINALE  (Δ$ = stack − sans le signal ; + = il rapporte)")
    print(f"  {'signal':8s}" + "".join(f"{w:>12s}" for w, _ in WINDOWS)
          + f"{'fenêtres +':>12s}")
    verdicts = {}
    for s in SIGNALS:
        d = {w: base[w]["end"] - without[s][w]["end"] for w, _ in WINDOWS}
        pos = sum(1 for v in d.values() if v > 0)
        print(f"  {s:8s}" + "".join(f"{d[w]:>+12.0f}" for w, _ in WINDOWS)
              + f"{pos:>9d}/4")
        verdicts[s] = {"contrib": d, "contrib_pos": pos}

    print("\nEDGE AUTONOME  (capital final du signal seul, départ $500)")
    print(f"  {'signal':8s}" + "".join(f"{w:>12s}" for w, _ in WINDOWS)
          + f"{'fenêtres +':>12s}  n(28m)")
    for s in SIGNALS:
        a = alone[s]
        pos = sum(1 for w, _ in WINDOWS if a[w]["end"] > START_CAP)
        print(f"  {s:8s}" + "".join(f"{a[w]['end']:>12.0f}" for w, _ in WINDOWS)
              + f"{pos:>9d}/4  {a['28m']['n']:>5d}")
        verdicts[s]["alone"] = {w: a[w]["end"] for w, _ in WINDOWS}
        verdicts[s]["alone_pos"] = pos

    print("\nEN PHASE AVEC LE BOT ACTUEL ?")
    for s in SIGNALS:
        v = verdicts[s]
        c, a = v["contrib_pos"], v["alone_pos"]
        if c >= 3 and a >= 3:
            verdict = "SOLIDE       — on le re-choisirait sur les deux critères"
        elif c >= 3:
            verdict = "PORTEFEUILLE — utile dans le stack, pas d'edge propre"
        elif a >= 3:
            verdict = "REDONDANT    — edge propre mais n'ajoute rien au stack"
        else:
            verdict = "HORS PHASE   — ni edge propre ni apport : à instruire"
        v["verdict"] = verdict.split("—")[0].strip()
        print(f"  {s:8s} contrib {c}/4 · autonome {a}/4  →  {verdict}")

    print("\nP&L par stratégie DANS le stack complet (28m)")
    for k, v in sorted(base["28m"]["per_strat"].items(),
                       key=lambda x: -x[1]["pnl"]):
        print(f"  {k:6s} n={v['n']:5d}  {v['pnl']:>+12.2f}")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "signal_rederivation.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "windows_end": end_dt.isoformat(),
                   "start_cap": START_CAP,
                   "realistic_trail_booking": True,
                   "base": base, "without": without, "alone": alone,
                   "verdicts": verdicts}, f, indent=1, default=str)
    print(f"\nDump : {path}")
    print("\nRAPPEL : la convergence ne certifie pas (même données, même optimum"
          " in-sample).\n          Seule la DIVERGENCE est une preuve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
