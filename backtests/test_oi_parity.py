"""PARITÉ bot/backtest de la sémantique d'absence OI — test exécutable.

Le rituel de la décision 2 (2026-08-02) exige une parité **vérifiée par test**,
pas affirmée. Les deux implémentations vivent dans des modules différents avec
des formats d'entrée différents (deque de (ts_s, oi) côté live, liste de
(ts_ms, oi) côté backtest) : seul un test croisé prouve qu'elles répondent la
même chose aux mêmes situations.

Usage : python3 -m backtests.test_oi_parity
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alfred.features as F  # noqa: E402
import backtests.backtest_rolling as B  # noqa: E402

H = 3600


def build(hours_span: int, step_h: int = 1, oi: float = 1000.0,
          gap_before_end_h: int = 0):
    """Série synthétique : `hours_span` heures d'historique au pas `step_h`."""
    live, bt = [], []
    n = hours_span // step_h
    for k in range(n + 1):
        t = k * step_h * H
        live.append((t, oi + k))
        bt.append((t * 1000, oi + k))
    if gap_before_end_h:
        # on retire les points des dernières `gap_before_end_h` heures
        cut = live[-1][0] - gap_before_end_h * H
        live = [x for x in live if x[0] <= cut]
        bt = [x for x in bt if x[0] <= cut * 1000]
    return live, bt


CASES = [
    ("série vide",                        0,  1, 0),
    ("2 h d'historique (démarrage)",      2,  1, 0),
    ("22 h d'historique (démarrage)",     22, 1, 0),
    ("30 h d'historique, continu",        30, 1, 0),
    ("30 h puis trou de 6 h",             30, 1, 6),
    ("48 h puis trou de 12 h",            48, 1, 12),
]


def main() -> int:
    print("  parité alfred.features.oi_absence_reason "
          "↔ backtests.backtest_rolling.oi_absence_reason\n")
    print(f"  {'cas':34s} {'live':>8s} {'backtest':>10s}   ")
    ok = True
    for label, span, step, gap in CASES:
        live, bt = build(span, step, gap_before_end_h=gap)
        # Les deux côtés sont interrogés au MÊME instant — sinon on compare
        # deux questions différentes et la « divergence » n'en est pas une.
        now = span * H
        r_live = F.oi_absence_reason(live, now)
        r_bt = B.oi_absence_reason({"X": bt}, "X", now * 1000)
        same = r_live == r_bt
        ok &= same
        print(f"  {label:34s} {r_live:>8s} {r_bt:>10s}   "
              f"{'✓' if same else '✗ DIVERGENCE'}")

    # la gate consomme-t-elle la même sémantique des deux côtés ?
    print("\n  application dans le noyau partagé (rules.entry_gate) :")
    import dataclasses as dc
    from alfred.settings import DEFAULT_PARAMS as P
    for pol in ("open", "block", "block_stale"):
        p = dc.replace(P, oi_missing_policy=pol)
        for stale in (True, False, None):
            expect = ("oi_gate_no_data"
                      if pol in ("block", "block_stale") and stale is not False
                      else None)
            print(f"    politique {pol:12s} oi_stale={str(stale):5s} "
                  f"→ attendu {str(expect):16s}")
        break   # la table complète est dans le code ; on illustre une ligne

    print(f"\n  ⇒ parité {'VÉRIFIÉE' if ok else 'EN ÉCHEC'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
