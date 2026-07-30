"""S5 est-il hors phase en entier, ou seulement dans une direction ?

Suite de `backtest_signal_rederivation.py` (2026-07-30) : S5 y ressort comme le
seul signal hors phase (contribution 2/4, autonome 2/4, et net NÉGATIF dans le
stack 28m malgré 537 trades). Le live depuis le reset dit la même chose mais plus
précisément : S5 LONG n=10 WR 40% −$9.66, S5 SHORT n=6 WR 67% +$11.45.

On teste donc trois retraits : S5 entier, S5 LONG seul, S5 SHORT seul.

Le filtre par direction n'existe pas dans Params (enabled_strategies est par nom
de stratégie) — on l'applique ici en enveloppant le détecteur de signaux, ce qui
garde le moteur intact.

ATTENTION : l'ablation 1-à-1 est path-dépendante. Elle DÉTECTE, elle ne décide
pas — un retrait demande un walk-forward dédié à dates glissantes.

Usage : python3 -m backtests.backtest_s5_direction_split
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS
import alfred.signals as _alf_signals

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
START_CAP = 500.0

_ORIG = _alf_signals.detect_token_signals


def _make_filter(drop):
    """drop = set de (strategy, direction) à retirer. direction: 1 LONG, -1 SHORT."""
    def wrapped(*a, **k):
        return [s for s in _ORIG(*a, **k)
                if (s["strategy"], s["direction"]) not in drop]
    return wrapped


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS

    def run_all(label, drop):
        _alf_signals.detect_token_signals = (_make_filter(drop) if drop else _ORIG)
        br._alf_signals.detect_token_signals = _alf_signals.detect_token_signals
        out, t0 = {}, time.time()
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
            if not drop:
                per = defaultdict(lambda: [0, 0.0])
                for t in r["trades"]:
                    if t.get("strat") != "S5":
                        continue
                    per[t.get("dir")][0] += 1
                    per[t.get("dir")][1] += t.get("pnl", 0.0)
                out[w]["s5_by_dir"] = {str(k): {"n": v[0], "pnl": round(v[1], 2)}
                                       for k, v in per.items()}
        print(f"  {label:26s} " + " ".join(
            f"{w}=${out[w]['end']:>8.0f}" for w, _ in WINDOWS)
            + "  DD " + " ".join(f"{out[w]['dd']:5.1f}" for w, _ in WINDOWS)
            + f"   [{time.time()-t0:.0f}s]", flush=True)
        return out

    print(f"Données jusqu'au {end_dt:%Y-%m-%d}. Booking trails : RÉALISTE.\n",
          flush=True)
    base = run_all("stack complet", None)
    variants = {
        "sans S5 entier":  {("S5", 1), ("S5", -1)},
        "sans S5 LONG":    {("S5", 1)},
        "sans S5 SHORT":   {("S5", -1)},
    }
    res = {k: run_all(k, v) for k, v in variants.items()}
    _alf_signals.detect_token_signals = _ORIG

    print("\n" + "=" * 74)
    print("Δ$ = variante − stack complet  (+ = le RETRAIT rapporte)")
    print(f"  {'retrait':18s}" + "".join(f"{w:>11s}" for w, _ in WINDOWS)
          + f"{'fen. +':>9s}" + "".join(f"{'ΔDD ' + w:>10s}" for w, _ in WINDOWS))
    for k, v in res.items():
        d = {w: v[w]["end"] - base[w]["end"] for w, _ in WINDOWS}
        dd = {w: base[w]["dd"] - v[w]["dd"] for w, _ in WINDOWS}
        pos = sum(1 for x in d.values() if x > 0)
        print(f"  {k:18s}" + "".join(f"{d[w]:>+11.0f}" for w, _ in WINDOWS)
              + f"{pos:>6d}/4" + "".join(f"{dd[w]:>+10.1f}" for w, _ in WINDOWS))

    print("\nS5 par direction DANS le stack complet")
    for w, _ in WINDOWS:
        s = base[w].get("s5_by_dir", {})
        txt = "  ".join(f"{'LONG' if k=='1' else 'SHORT'} n={v['n']:<4d}{v['pnl']:>+9.0f}"
                        for k, v in sorted(s.items(), reverse=True))
        print(f"  {w:5s} {txt}")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "output")
    path = os.path.join(out_dir, "s5_direction_split.json")
    with open(path, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "base": base, "variants": res}, f, indent=1, default=str)
    print(f"\nDump : {path}")
    print("\nL'ablation DÉTECTE, elle ne décide pas : tout retrait exige un"
          " walk-forward\nà dates glissantes avant décision.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
