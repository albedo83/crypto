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


def trigger_price(pos, p: Params) -> float:
    """Prix du trigger hard-stop pour une position.

    `unrealized` live est BRUT (direction × Δprix), et le stop soft se
    déclenche à `effective_stop(pos)` sur ce brut — donc le trigger se pose
    au niveau brut `stop − buffer` reconverti en prix :
    LONG  → sous l'entrée (stop=−1250, buffer=200 → entry×0.855),
    SHORT → au-dessus (→ entry×1.145). Gère le S9 adaptatif via pos.stop_bps.
    """
    stop_bps = rules.effective_stop(pos, p)          # duck: strategy+stop_bps
    level_bps = stop_bps - p.hard_stop_buffer_bps    # plus loin que le soft
    return pos.entry_price * (1 + pos.direction * level_bps / 1e4)


def close_is_buy(direction: int) -> bool:
    """Sens de l'ordre qui FERME la position : SHORT se rachète (buy)."""
    return direction == -1
