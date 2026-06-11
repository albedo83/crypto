"""EDA — S5 même scan, même secteur : le rang 2 du ranking performe-t-il
structurellement moins que le rang 1 ?

Question (2026-06-11, après l'entrée simultanée CRV(+)/COMP(~) de SENIOR) :
quand deux entrées S5 du même secteur partent dans le même scan (le quota
MAX_PER_SECTOR=2 le permet), le 2e choix du ranking (divergence plus faible)
est-il un contributeur net ? Si oui → ne rien changer. Si non → candidat
MAX_PER_SECTOR=1 ou haircut de sizing rang 2 (à valider walk-forward strict
4/4 AVANT tout ship — doctrine premise-gate).

Méthode : run_window(aligned=True) sur 28m avec un skip_fn OBSERVATEUR
(retourne toujours False, n'altère pas le run) — l'ordre d'appel de skip_fn
= l'ordre du ranking (candidats triés par (z, strength) desc). On matche
ensuite les trades S5 entrés, on groupe par (scan_ts, secteur) et on compare
rang 1 vs rang 2 sur les groupes ≥ 2.

Usage : python3 -m backtests.eda_s5_sector_rank
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

START = "2024-02-04"
START_CAP = 500.0


def main() -> int:
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors_f = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])

    tok_sector = {t: s for s, toks in DEFAULT_PARAMS.sectors.items() for t in toks}

    # ── Observateur du ranking : skip_fn(coin, ts, strat, dir) est appelé
    # dans l'ordre des candidats triés ; on enregistre sans rien filtrer.
    order: dict[int, list[tuple[str, str, int]]] = {}   # ts → [(coin, strat, dir)]

    def recorder(coin, ts, strat, direction):
        order.setdefault(ts, []).append((coin, strat, direction))
        return False

    start_ms = int(datetime.fromisoformat(START + "T00:00:00+00:00").timestamp() * 1000)
    print(f"Running aligned 28m ({START} → …) with ranking recorder…")
    t0 = time.time()
    r = run_window(features, data, sectors_f, dxy,
                   start_ts_ms=start_ms, end_ts_ms=end_ms,
                   start_capital=START_CAP, skip_fn=recorder,
                   oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True)
    print(f"  → {r['end_capital']:.0f} ({len(r['trades'])} trades) en "
          f"{time.time() - t0:.0f}s, {len(order)} scans observés")

    # ── Match trades S5 ↔ rang dans le scan d'origine.
    # entry_t du trade = soit le ts du signal, soit la bougie suivante —
    # on tente les deux offsets.
    s5 = [t for t in r["trades"] if t["strat"] == "S5"]
    PERIOD = 14_400_000

    def rank_of(tr) -> tuple[int, int] | None:
        """(scan_ts, rang 1-based parmi les S5 du même secteur de ce scan)."""
        for off in (0, -PERIOD):
            ts = tr["entry_t"] + off
            cands = order.get(ts)
            if not cands:
                continue
            sect = tok_sector.get(tr["coin"])
            same = [c for c, s, d in cands
                    if s == "S5" and tok_sector.get(c) == sect]
            if tr["coin"] in same:
                return ts, same.index(tr["coin"]) + 1
        return None

    groups: dict[tuple[int, str], list[tuple[int, dict]]] = {}
    unmatched = 0
    for tr in s5:
        rk = rank_of(tr)
        if rk is None:
            unmatched += 1
            continue
        ts, rank = rk
        groups.setdefault((ts, tok_sector.get(tr["coin"], "?")), []).append((rank, tr))

    multi = {k: sorted(v) for k, v in groups.items() if len(v) >= 2}
    print(f"\nS5 trades: {len(s5)} | unmatched: {unmatched} | "
          f"scans même-secteur ≥2 entrées: {len(multi)}")

    def stats(label, xs_bps, xs_pnl):
        if not xs_bps:
            print(f"  {label}: n=0")
            return
        wr = sum(1 for x in xs_bps if x > 0) / len(xs_bps) * 100
        print(f"  {label}: n={len(xs_bps)}  net moy {statistics.mean(xs_bps):+7.1f} bps  "
              f"méd {statistics.median(xs_bps):+7.1f}  WR {wr:4.1f}%  "
              f"P&L cumulé ${sum(xs_pnl):+9.2f}")

    r1_bps, r1_pnl, r2_bps, r2_pnl, diffs = [], [], [], [], []
    pairs_detail = []
    for (ts, sect), members in sorted(multi.items()):
        first, second = members[0][1], members[1][1]
        r1_bps.append(first["net"]); r1_pnl.append(first["pnl"])
        r2_bps.append(second["net"]); r2_pnl.append(second["pnl"])
        diffs.append(first["net"] - second["net"])
        pairs_detail.append((ts, sect, first, second))

    print("\n── Paires même scan / même secteur (l'objet de la question) ──")
    stats("rang 1", r1_bps, r1_pnl)
    stats("rang 2", r2_bps, r2_pnl)
    if diffs:
        better = sum(1 for d in diffs if d > 0)
        print(f"  rang1 > rang2 dans {better}/{len(diffs)} paires "
              f"({better / len(diffs) * 100:.0f}%) | Δnet moyen "
              f"{statistics.mean(diffs):+.1f} bps (apparié)")
        try:
            from scipy.stats import wilcoxon
            stat, p = wilcoxon(diffs)
            print(f"  Wilcoxon apparié : p={p:.4f}")
        except Exception:
            pass

    # Baseline : tous les S5 (référence de calibration)
    print("\n── Baseline S5 (tous) ──")
    stats("S5 28m", [t["net"] for t in s5], [t["pnl"] for t in s5])

    # Le rang 2 est-il au moins un contributeur net positif ?
    if r2_pnl:
        print(f"\n  → Contribution nette des rangs 2 : ${sum(r2_pnl):+,.2f} "
              f"sur {len(r2_pnl)} trades "
              f"({'POSITIVE — les garder' if sum(r2_pnl) > 0 else 'NÉGATIVE — piste à creuser'})")

    print("\n── Détail des 12 dernières paires ──")
    for ts, sect, f, s in pairs_detail[-12:]:
        d = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%y-%m-%d %H:%M")
        print(f"  {d} {sect:12s} r1 {f['coin']:6s} {f['net']:+8.1f} bps (${f['pnl']:+7.2f}) | "
              f"r2 {s['coin']:6s} {s['net']:+8.1f} bps (${s['pnl']:+7.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
