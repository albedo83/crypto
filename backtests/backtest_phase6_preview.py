"""Phase 6 preview — nouveaux chiffres de référence (mode aligned).

Produit le dossier de la remise à zéro AVANT de l'acter : compare sur les
4 fenêtres canoniques :

  legacy         — la sémantique BT actuelle (chiffres de docs/backtests.md)
  aligned        — divergences #1/2/3/5/6/7/8/9/10 alignées sur le live :
                   exits canoniques (ordre live, prix synthétiques,
                   prop_trail simulé), sizing live (cap $500 post-modulateur,
                   arrondi, floor $10), force S10 live, btc_z fenêtre bot +
                   sémantique None
  aligned_noMKR  — aligned + retrait MKR (mort depuis 09/2025, rebranding
                   SKY) = LA CIBLE PHASE 6

Écrit docs/alfred_phase6_preview.md. Ne touche PAS à docs/backtests.md ni
au flag par défaut — l'acte de la phase 6 reste une décision explicite.

Usage : python3 -m backtests.backtest_phase6_preview
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import (
    run_window, load_oi, load_funding, load_dxy,
)
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

WINDOWS = [
    ("28m", "2024-02-04"),
    ("12m", "2025-06-04"),
    ("6m",  "2025-12-04"),
    ("3m",  "2026-03-04"),
]
START_CAP = 500.0
OUT_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "alfred_phase6_preview.md")


def _ts_ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def main() -> int:
    print("=" * 78)
    print("  Phase 6 preview — legacy vs aligned vs aligned_noMKR")
    print("=" * 78)

    print("\n  Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi = load_oi()
    funding = load_funding()
    dxy = load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_iso = datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat()[:16]
    print(f"  Data through : {end_iso}")

    # Univers sans MKR (retrait phase 6) — coins est filtré sur la présence
    # dans features/data, donc retirer des dicts suffit.
    feat_nomkr = {k: v for k, v in features.items() if k != "MKR"}
    data_nomkr = {k: v for k, v in data.items() if k != "MKR"}

    modes = [
        ("legacy",        dict(aligned=False), features, data),
        ("aligned",       dict(aligned=True),  features, data),
        ("aligned_noMKR", dict(aligned=True),  feat_nomkr, data_nomkr),
    ]

    results: dict[str, dict[str, dict]] = {}
    for mode_name, kw, feats, dat in modes:
        results[mode_name] = {}
        for win, start in WINDOWS:
            t0 = time.time()
            r = run_window(
                feats, dat, sectors, dxy,
                start_ts_ms=_ts_ms(start), end_ts_ms=end_ms,
                start_capital=START_CAP,
                oi_data=oi, funding_data=funding,
                apply_adaptive_modulator=True,
                **kw)
            results[mode_name][win] = {
                "pnl": r["pnl"], "pnl_pct": r["pnl_pct"],
                "dd": r.get("max_dd_pct", 0),
                "n": r.get("n_trades", 0), "wr": r.get("win_rate", 0),
                "by_strat": r.get("by_strat", {}),
            }
            print(f"  {mode_name:14} | {win:3} | pnl {r['pnl_pct']:+9.1f}% "
                  f"dd {r.get('max_dd_pct', 0):6.1f}% n={r.get('n_trades', 0):4d} "
                  f"wr={r.get('win_rate', 0):4.1f}% ({time.time()-t0:.0f}s)")

    # ── Rapport ──
    lines = [
        "# Phase 6 preview — nouveaux chiffres de référence (mode aligned)",
        "",
        f"_Généré {datetime.now(timezone.utc).isoformat()[:16]}Z — data through {end_iso} "
        f"— capital ${START_CAP:.0f} — `backtests/backtest_phase6_preview.py`_",
        "",
        "**Statut : PREVIEW.** La sémantique legacy reste celle de `docs/backtests.md` ",
        "tant que la phase 6 n'est pas actée. Ce dossier chiffre l'impact de l'acte.",
        "",
        "Alignements inclus dans `aligned` (cf. `docs/alfred_divergences.md`) : ",
        "#1 prix d'exit synthétiques · #2 stop-first · #3 prop_trail simulé · ",
        "#5/6/7 sizing live (cap $500 post-modulateur, arrondi, floor $10) · ",
        "#8 force S10 = 1000/range · #9 btc_z manquant → None · #10 fenêtre z = bot.",
        "`aligned_noMKR` ajoute le retrait de MKR (mort depuis 2025-09, rebranding SKY).",
        "",
        "## Résumé par fenêtre",
        "",
        "| Fenêtre | Mode | P&L % | DD max | Trades | WR |",
        "|---|---|---|---|---|---|",
    ]
    for win, _ in WINDOWS:
        for mode_name, *_ in modes:
            r = results[mode_name][win]
            lines.append(f"| {win} | {mode_name} | {r['pnl_pct']:+.1f}% | "
                         f"{r['dd']:.1f}% | {r['n']} | {r['wr']:.0f}% |")
    lines += [
        "",
        "## Δ (aligned_noMKR − legacy) — l'impact de l'acte phase 6",
        "",
        "| Fenêtre | ΔP&L (pp) | ΔDD (pp) | ΔTrades |",
        "|---|---|---|---|",
    ]
    for win, _ in WINDOWS:
        a = results["aligned_noMKR"][win]
        l = results["legacy"][win]
        lines.append(f"| {win} | {a['pnl_pct']-l['pnl_pct']:+.1f} | "
                     f"{a['dd']-l['dd']:+.1f} | {a['n']-l['n']:+d} |")
    lines += [
        "",
        "## Breakdown par stratégie — cible (aligned_noMKR, 28m)",
        "",
        "| Strat | Trades | WR | P&L |",
        "|---|---|---|---|",
    ]
    for s, st in sorted(results["aligned_noMKR"]["28m"]["by_strat"].items()):
        if isinstance(st, dict):
            lines.append(f"| {s} | {st.get('n', '?')} | {st.get('wr', 0):.0f}% | "
                         f"${st.get('pnl', 0):+,.0f} |")
    lines += [
        "",
        "## Acte de la phase 6 (quand décidé)",
        "",
        "1. `aligned=True` devient le défaut du run officiel (`main()` de backtest_rolling)",
        "2. Retrait MKR de `Params.trade_symbols` + `Params.sectors` (alfred/settings.py)",
        "3. Re-run `docs/backtests.md` + archivage des anciens chiffres",
        "4. Mise à jour de `docs/alfred_divergences.md` (statut : alignées)",
        "",
    ]
    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(lines))
    print(f"\n  Dossier écrit : {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
