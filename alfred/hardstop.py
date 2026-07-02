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

    Trois planchers possibles, on prend le plus haut (étape B, v1.7.3) :
    - catastrophe : `effective_stop` (S8 serré, S9 adaptatif via pos.stop_bps) ;
    - `manual_stop_usdt` (posé par l'utilisateur OU par le LOCK de l'arbitre
      IA) : plancher $ sur le pnl NET → brut = usdt/size×1e4 + cost_bps
      (sémantique exacte de rules.manual_stop_rule) ;
    - `opp_floor_bps` : plancher cliquet armé par signal opposé (brut direct).
    Les trails dynamiques (s10/s8_inlife/prop_trail) ne sont PAS miroités
    (profit-taking, pas sécurité — périmètre acté 2026-07-02).
    """
    level = rules.effective_stop(pos, p)             # duck: strategy+stop_bps
    ms = getattr(pos, "manual_stop_usdt", None)
    if ms is not None and pos.size_usdt > 0:
        level = max(level, ms / pos.size_usdt * 1e4 + p.cost_bps)
    of = getattr(pos, "opp_floor_bps", None)
    if of is not None:
        level = max(level, of)
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
