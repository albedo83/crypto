"""VERDICT S5 — les trois études décisionnelles, une seule passe.

Base : backtest corrigé (parité des secteurs v1.17.1 + parité des entrées v1.17.2).
Grille d'interprétation PRÉ-ENREGISTRÉE dans `rapport.md` § 11, committée le
2026-07-30 à 19h02 UTC, AVANT exécution. Ne pas la relire après coup.

Rappel de la grille :
  retrait 4/4                    → retrait, point final
  retrait 3/4                    → PAS de retrait, bascule sur le sizing
  retrait ≤ 2/4                  → S5 reste, décision au sizing
  sizing : réveil de v1.17.0 UNIQUEMENT si 4/4 en P&L ET DD meilleur partout
  DD meilleur mais P&L perdu ≥1 fenêtre → statu quo 3.0
  verdict différent entre 4 et 6 bps    → « cost-sensitive, non tranché » → statu quo
  quartiles de force : départagent un verdict serré SEULEMENT s'ils survivent

Sensibilité de coût obligatoire (§ 12) : le slippage réel mesuré sur les fills
est de +5.97 bps AR (IC95 [+0.88, +11.05]) contre 4.0 modélisés. 4.0 n'est pas
réfuté, mais le backtest sous-facture probablement et favorise donc les
configurations qui tradent le plus. On rejoue tout à 4 ET à 6.

Usage : python3 -m backtests.backtest_s5_verdict
"""

from __future__ import annotations

import dataclasses as dc
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS
import alfred.signals as A_SIG

START_CAP = 500.0
OFFSETS = [0, 6, 12, 18]
SLIPPAGES = [4.0, 6.0]
MULTS = [1.5, 1.0]

# ⚠ PIÈGE D'ÉTAT (rencontré le 2026-07-31, 1re exécution invalidée) :
# `DEFAULT_PARAMS.signal_mult["S5"]` vaut **1.0** dans le code depuis v1.17.0,
# alors que le bot EN SERVICE tourne à **3.0** (aucun restart, changement
# dormant). Prendre DEFAULT_PARAMS comme référence revenait donc à comparer
# v1.17.0 à lui-même — d'où un « mult 1.0 → +0 sur les 4 fenêtres » qui a
# révélé l'erreur. La référence doit être la config RÉELLEMENT en service.
BASELINE_S5_MULT = 3.0
DD_TOL = 2.0          # pp de tolérance sur la dégradation de drawdown
_ORIG_DETECT = A_SIG.detect_token_signals


def _drop_s5(dirs):
    def wrapped(*a, **k):
        return [s for s in _ORIG_DETECT(*a, **k)
                if not (s["strategy"] == "S5" and s["direction"] in dirs)]
    return wrapped


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Chargement…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()

    wins = []
    for off in OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        wins.append((f"OOS-{off}", int(s.timestamp() * 1000),
                     int(e.timestamp() * 1000), f"{s:%Y-%m}→{e:%Y-%m}"))
    _sm = dict(DEFAULT_PARAMS.signal_mult)
    _sm["S5"] = BASELINE_S5_MULT
    BASE_P = dc.replace(DEFAULT_PARAMS, signal_mult=_sm)
    print(f"Référence = config EN SERVICE : signal_mult['S5'] = {BASELINE_S5_MULT}"
          f"  (le code porte {DEFAULT_PARAMS.signal_mult['S5']}, dormant)")
    print(f"Données jusqu'au {end_dt:%Y-%m-%d}. Fenêtres OOS glissantes :")
    for n, _, _, lab in wins:
        print(f"  {n:8s} {lab}")

    def run(params, drop_dirs, keep_trades=False):
        br._P = params
        A_SIG.detect_token_signals = (_drop_s5(drop_dirs) if drop_dirs
                                      else _ORIG_DETECT)
        br._alf_signals.detect_token_signals = A_SIG.detect_token_signals
        out = {}
        for name, a, b, _l in wins:
            r = run_window(features, data, sectors, dxy, start_ts_ms=a,
                           end_ts_ms=b, start_capital=START_CAP, oi_data=oi,
                           funding_data=funding, apply_adaptive_modulator=True,
                           aligned=True, margin_check=True, mfe_on_close=True,
                           realistic_trail_booking=True)
            out[name] = {"end": r["end_capital"], "dd": r["max_dd_pct"],
                         "n": r["n_trades"]}
            if keep_trades:
                out[name]["trades"] = [
                    {k: t.get(k) for k in ("strat", "net", "pnl",
                                           "entry_strength")}
                    for t in r["trades"]]
        A_SIG.detect_token_signals = _ORIG_DETECT
        br._alf_signals.detect_token_signals = _ORIG_DETECT
        return out

    def score(base, var):
        """(fenêtres P&L gagnées, fenêtres DD non dégradées de plus de DD_TOL)."""
        npos = sum(1 for n, _, _, _ in wins if var[n]["end"] > base[n]["end"])
        # dd sont négatifs : var moins négatif = mieux
        nddok = sum(1 for n, _, _, _ in wins
                    if (var[n]["dd"] - base[n]["dd"]) >= -DD_TOL)
        nddbet = sum(1 for n, _, _, _ in wins if var[n]["dd"] > base[n]["dd"])
        return npos, nddok, nddbet

    results = {}
    t0 = time.time()
    for slip in SLIPPAGES:
        br.BACKTEST_SLIPPAGE_BPS = slip
        br.COST = br.TAKER_FEE_BPS + slip
        tag = f"slip{int(slip)}"
        print(f"\n{'='*78}\n### COÛT : taker {br.TAKER_FEE_BPS:.0f} + slippage "
              f"{slip:.0f} = {br.COST:.0f} bps AR", flush=True)

        base = run(BASE_P, None, keep_trades=(slip == SLIPPAGES[0]))
        print(f"  {'référence (S5 à ' + str(BASELINE_S5_MULT) + ')':26s}"
              + "".join(f"{base[n]['end']:>10.0f}" for n, _, _, _ in wins))

        print(f"\n  ÉTUDE 1 — retrait de S5")
        print(f"  {'':26s}" + "".join(f"{n:>10s}" for n, _, _, _ in wins)
              + f"{'P&L+':>7s}{'DDok':>7s}")
        r1 = {}
        for lab, dirs in (("retrait entier", {1, -1}),
                          ("retrait LONG", {1}),
                          ("retrait SHORT", {-1})):
            v = run(BASE_P, dirs)
            npos, nddok, nddbet = score(base, v)
            r1[lab] = {"windows": v, "npos": npos, "nddok": nddok,
                       "nddbet": nddbet}
            print(f"  {lab:26s}"
                  + "".join(f"{v[n]['end']-base[n]['end']:>+10.0f}"
                            for n, _, _, _ in wins)
                  + f"{npos:>5d}/4{nddok:>5d}/4")

        print(f"\n  ÉTUDE 2 — grille de sizing signal_mult['S5']")
        print(f"  {'':26s}" + "".join(f"{n:>10s}" for n, _, _, _ in wins)
              + f"{'P&L+':>7s}{'DDok':>7s}{'DDmieux':>9s}")
        r2 = {}
        for m in MULTS:
            sm = dict(DEFAULT_PARAMS.signal_mult)
            sm["S5"] = m
            v = run(dc.replace(DEFAULT_PARAMS, signal_mult=sm), None)
            npos, nddok, nddbet = score(base, v)
            r2[str(m)] = {"windows": v, "npos": npos, "nddok": nddok,
                          "nddbet": nddbet}
            print(f"  {'mult ' + str(m):26s}"
                  + "".join(f"{v[n]['end']-base[n]['end']:>+10.0f}"
                            for n, _, _, _ in wins)
                  + f"{npos:>5d}/4{nddok:>5d}/4{nddbet:>7d}/4")

        results[tag] = {"base": {n: {k: v for k, v in base[n].items()
                                     if k != "trades"} for n, _, _, _ in wins},
                        "retrait": r1, "sizing": r2}

    # ── Étude 3 : quartiles de force (descriptif, sur la base de référence) ──
    print(f"\n{'='*78}\n### ÉTUDE 3 — quartiles de force de S5 (base corrigée)")
    br.BACKTEST_SLIPPAGE_BPS, br.COST = 4.0, br.TAKER_FEE_BPS + 4.0
    q_out = {}
    for wlab, months in (("28m", 28), ("12m", 12), ("6m", 6)):
        s_ms = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        br._P = BASE_P
        r = run_window(features, data, sectors, dxy, start_ts_ms=s_ms,
                       end_ts_ms=end_ms, start_capital=START_CAP, oi_data=oi,
                       funding_data=funding, apply_adaptive_modulator=True,
                       aligned=True, margin_check=True, mfe_on_close=True,
                       realistic_trail_booking=True)
        s5 = [t for t in r["trades"] if t["strat"] == "S5"
              and t.get("entry_strength") is not None]
        if len(s5) < 40:
            continue
        vals = sorted(t["entry_strength"] for t in s5)
        cuts = [vals[int(len(vals) * q / 4)] for q in range(1, 4)]
        buckets = defaultdict(list)
        for t in s5:
            v = t["entry_strength"]
            q = "Q4"
            for i, c in enumerate(cuts):
                if v < c:
                    q = f"Q{i+1}"
                    break
            buckets[q].append(t)
        print(f"\n  {wlab} (n={len(s5)}, force {vals[0]:.0f}→{vals[-1]:.0f})")
        print(f"    {'quartile':10s}{'n':>6s}{'WR':>8s}{'net moy':>10s}{'P&L':>10s}")
        q_out[wlab] = {}
        for q in ("Q1", "Q2", "Q3", "Q4"):
            v = buckets.get(q, [])
            if not v:
                continue
            wr = sum(1 for t in v if t["pnl"] > 0) / len(v) * 100
            nm = st.mean(t["net"] for t in v)
            pl = sum(t["pnl"] for t in v)
            print(f"    {q:10s}{len(v):>6d}{wr:>7.1f}%{nm:>10.1f}{pl:>10.0f}")
            q_out[wlab][q] = {"n": len(v), "wr": round(wr, 1),
                              "net_mean": round(nm, 1), "pnl": round(pl, 2)}

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_verdict.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(),
                   "slippages": SLIPPAGES, "cost_sensitivity": results,
                   "strength_quartiles": q_out}, f, indent=1, default=str)
    print(f"\n[{time.time()-t0:.0f}s]  Dump : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
