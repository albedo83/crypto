"""Filet hard-stop exchange-side (v1.7.1) — math pure du trigger.

Trigger orders reduce-only résidents sur Hyperliquid, miroir du
catastrophe_stop à `effective_stop − buffer`. Couvre les downtimes du
process (crash, restart, gap watchdog ~5 min + boot) : la chaîne de sorties
20s reste l'exécuteur primaire, le trigger n'exécute que si le process est
mort ou si le marché va plus vite que 20s.

**PAS une règle de trading** : `rules.py` et le backtest sont inchangés.
Buffer calibré 2026-07-02 : 200 bps = p99.99 des excursions 60s (194) et
au-delà du pire overshoot soft observé en live (162). Divergence assumée
tracée dans docs/alfred_divergences.md.

Orchestration (pose/cancel/sweep) dans botinstance ; exécution dans
hl.HLAccount.place_stop_order / cancel_order / open_trigger_orders.
"""

from __future__ import annotations

from alfred import rules
from alfred.settings import Params


def protective_level_bps(pos, p: Params) -> float:
    """Niveau BRUT (bps vs entrée) du plancher soft le plus serré actif.

    Deux planchers possibles, on prend le plus haut :
    - catastrophe : `effective_stop` (S8 serré, S9 adaptatif via pos.stop_bps) ;
    - `manual_stop_usdt` (posé par l'utilisateur) : plancher $ sur le pnl NET
      → brut = usdt/size×1e4 + cost_bps (sémantique de rules.manual_stop_rule).

    Aucun mécanisme de PRISE DE PROFIT n'est miroité — ni les trails
    dynamiques (s10/s8_inlife/prop_trail), ni `opp_floor` (retiré le
    2026-09-06). Périmètre acté le 2026-07-02, `opp_floor` y contrevenait :
    c'est un cliquet à 0,80×gain armé dès +300 bps, donc du profit-taking, pas
    de la sécurité.

    Pourquoi ça comptait : `trail_eval_4h_close=True` fait que ces règles ne
    sont évaluées QU'AUX CLÔTURES 4h (v1.8.0 — l'évaluation intra-bougie était
    la cause n°1 de gagnants coupés). Un trigger résident, lui, se déclenche
    sur n'importe quelle mèche. Sur ARB S1 LONG (02→04/09) le trigger a été
    touché en mèche 4 fois alors qu'AUCUNE clôture 4h n'est passée dessous :
    sortie à +13,00 $ contre +38,04 $ au timeout naturel, soit −25,04 $.
    Symétriquement, `opp_floor` évalué normalement est positif partout
    (paper +71,64 $/n=5 · junior +34,08 $/n=6 · baby +10,46 $/n=1) tandis que
    SENIOR n'en enregistrait AUCUN : le miroir tirait avant et bookait
    `exchange_stop`. Échantillon mince (n=2) — ce qui justifie le retrait est
    l'argument mécanique, pas ces deux trades.
    """
    level = rules.effective_stop(pos, p)             # duck: strategy+stop_bps
    ms = getattr(pos, "manual_stop_usdt", None)
    if ms is not None and pos.size_usdt > 0:
        level = max(level, ms / pos.size_usdt * 1e4 + p.cost_bps)
    return level


def trigger_price(pos, p: Params) -> float:
    """Prix du trigger hard-stop pour une position.

    `unrealized` live est BRUT (direction × Δprix), et les planchers soft se
    déclenchent sur ce brut — donc le trigger se pose au niveau brut
    `plancher_le_plus_serré − buffer` reconverti en prix :
    LONG  → sous le niveau (stop=−1250, buffer=200 → entry×0.855),
    SHORT → au-dessus (→ entry×1.145).
    """
    level_bps = protective_level_bps(pos, p) - p.hard_stop_buffer_bps
    return pos.entry_price * (1 + pos.direction * level_bps / 1e4)


def close_is_buy(direction: int) -> bool:
    """Sens de l'ordre qui FERME la position : SHORT se rachète (buy)."""
    return direction == -1
