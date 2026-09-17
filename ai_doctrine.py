"""Factual IA reference generated from pure Alfred Params; no I/O.

Changing defaults also changes the consumers' existing prompt hashes.
Runtime values and per-bot overrides explicitly supplied take precedence.
"""

import json

from alfred.settings import DEFAULT_PARAMS, Params


def build_doctrine(p: Params = DEFAULT_PARAMS) -> str:
    settings = {
        "scope": "core defaults; explicit per-bot runtime values take precedence",
        "trade_symbol_count": len(p.trade_symbols),
        "enabled_strategies": sorted(p.enabled_strategies),
        "hold_hours": {s: p.hold_hours_for(s) for s in sorted(p.enabled_strategies)},
        "leverage": p.leverage,
        "notional_cap_fraction": p.max_notional_frac,
        "taker_fee_round_trip_bps": p.taker_fee_bps,
        "adaptive_alpha_by_direction": {
            f"{s}_{side}": p.get_adaptive_alpha(s, direction)
            for s in sorted(p.enabled_strategies)
            for side, direction in (("LONG", 1), ("SHORT", -1))
        },
        "macro_multiplier_bounds": [p.macro_mult_min, p.macro_mult_max],
        "macro_z_clip": p.macro_z_clip,
        "stop_loss_bps": p.stop_loss_bps,
        "stop_loss_s8_bps": p.stop_loss_s8,
        "trail_eval_4h_close": p.trail_eval_4h_close,
        "prop_trail_params": p.prop_trail_params,
        "s8_inlife_params": p.s8_inlife_params,
        "runner_ext_strategies": sorted(p.runner_ext_strategies),
        "opp_floor_lock_ratio": p.opp_floor_lock_ratio,
        "opp_floor_min_gain_bps": p.opp_floor_min_gain_bps,
        "dead_timeout_mfe_cap_bps": p.dead_timeout_mfe_cap_bps,
        "traj_cut_strategies": sorted(p.traj_cut_strategies),
        "traj_cut_long_only": p.traj_cut_long_only,
    }
    return """\
Moteur de règles Hyperliquid : signaux sur bougies 4h, sorties selon leurs cadences.

STRATÉGIES :
- S1 : momentum BTC fort → LONG alts, suivi de tendance.
- S5 : suit la divergence sectorielle : LONG du leader, SHORT du retardataire.
  C'est un suivi de divergence, pas un fade ni un signal de retour à la moyenne.
- S8 : capitulation / flush → LONG, recherche d'un rebond.
- S9 : fade des mouvements extrêmes ±20%/24h, contre-tendance.
- S10 : faux breakout après squeeze, puis réintégration ; SHORT selon les filtres.

SIZING : les coefficients effectifs sont dans adaptive_alpha_by_direction ci-dessous.
Coefficient nul = aucune modulation macro pour cette stratégie/direction.
Sinon : multiplicateur = 1 + alpha × btc_z écrêté, borné, puis plafond notionnel.
Le plafond peut absorber la modulation. Le btc_z courant ne dit pas quel était
le régime à l'entrée d'une position, et ne redimensionne pas une position ouverte.

SORTIES :
- stop catastrophe ; filet exchange distinct, dont la présence dépend du bot ;
- stop manuel s'il existe, timeout et prolongation runner selon la stratégie ;
- traj_cut : stratégies et restriction LONG déclarées ci-dessous ;
- dead_timeout : désactivé si dead_timeout_mfe_cap_bps est négatif (MFE ≥ 0) ;
- règles S8 et S9 : invalidations propres au setup ;
- s10_trail / s8_inlife : gestion des gagnants selon les règles de la stratégie ;
- opp_floor : plancher conditionnel en présence d'un signal opposé et d'un gain
  suffisant ; armement désactivé si opp_floor_lock_ratio ≤ 0. Ne pas affirmer
  qu'aucun plancher n'existe sur S5 ou S9 ;
- prop_trail : actif uniquement si prop_trail_params contient une configuration.
Un dictionnaire vide signifie désactivé, pas une invitation à le remplacer par l'IA.
Les trails gatés sur 4h ne sont pas des ordres exchange continus garantissant
leur prix théorique. Un LOCK peut avancer la sortie et modifier les futures entrées.

ÉVIDENCE : les backtests décrivent une performance historique, pas un avantage
futur prouvé. Les interventions IA doivent être comparées aux règles seules,
sur la même opportunité et à risque comparable. GO/HOLD reste le défaut en
l'absence d'information suffisante ; ne pas inventer une confirmation manquante.
Une baisse de taille peut réduire pertes ET gains ; son effet net doit être mesuré.
Le taux de réussite seul ne mesure pas la rentabilité. Les gros gagnants comptent.

LIMITES DE L'INFORMATION :
- Ne cite un incident, délisting, unlock ou nouvelle que si le contexte fournit
  une source datée, disponible au moment de la décision. Sinon : information inconnue.
- N'infère pas une pente persistante depuis les seuls MAE/MFE : sans série
  temporelle suffisante, la trajectoire n'est pas observée.
- confidence est un jugement non calibré, pas une probabilité de gain mesurée.
- mae_bps / mfe_bps = excursions, pas pertes/gains réalisés ; le stop_usdt porte
  sur le P&L net ; size_usdt est le notionnel, sans seconde multiplication du levier.

PARAMÈTRES FACTUELS DU NOYAU :
""" + json.dumps(settings, ensure_ascii=False, sort_keys=True, indent=2)


DOCTRINE_DIGEST = build_doctrine()
