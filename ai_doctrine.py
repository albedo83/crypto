"""Digest condensé des stratégies & sorties du bot, pour les outils IA.

Remplace l'envoi de `docs/bot.md` complet (~22 000 tokens) à chaque appel par un
résumé (~800 tokens) : même valeur de jugement (le modèle a juste besoin de
connaître les stratégies, le régime et les sorties déjà en place), ~10× moins
cher. Partagé par entry_judge.py et position_review.py (bloc caché ephemeral).
"""

DOCTRINE_DIGEST = """\
Bot de trading Hyperliquid (altcoins perp, levier 2×, holds ~24-48h, ~35 tokens).
Frais ~9 bps aller-retour taker (floor structurel) → un edge brut < ~50 bps est fragile.

STRATÉGIES (5) :
- S1 : momentum BTC fort → LONG alts (suit la tendance). Amplifiée en bull.
- S5 : divergence sectorielle → suit la divergence (LONG leader / SHORT laggard).
  Mean-reversion sensible au régime : SHORT réduit en bull, amplifié en bear.
- S8 : capitulation / flush → LONG (rebond post-liquidations). Favorisée en bear.
- S9 : fade des moves extrêmes ±20%/24h → contre-tendance (mean-reversion). Réduite en bull.
- S10 : squeeze + faux breakout → SHORT-only, whitelist, trailing.

RÉGIME : btc_z = z-score (6 mois) du rendement BTC 30j. >0 bull, <0 bear. Un
modulateur adaptatif scale DÉJÀ le sizing par régime (S1 en bull ; S5-SHORT/S8/S9
en bear). Une position déjà ouverte tient donc compte du régime.

SORTIES AUTOMATIQUES déjà en place (NE PAS suggérer de doublon) :
- stop catastrophe (stop_bps, ordre au repos, ~-1250 bps selon strat) ;
- timeout (fin du hold) ;
- traj_cut : cut S5 en bear si trajectoire cassée (pinned au MAE + chute depuis le MFE) ;
- dead_timeout / s8_dead_in_water / s9 early-dead : cut des positions sans pouls ;
- s8_inlife / s10 trailing / runner_ext : gestion MFE des gagnants (S8 et S10 seulement) ;
- opp_floor : signal opposé sur un gagnant → plancher cliquet du gain ;
- manual_stop (🎯) : stop $ manuel posé par l'admin.

⚠️ RETIRÉ le 2026-07-25 (v1.15.6) : `prop_trail`, le verrou proportionnel du gain sur
les gagnants S5 et S9-bull. Il a été mesuré destructeur de valeur : ces règles ne sont
évaluées qu'aux clôtures 4h, donc la sortie se fait au MARCHÉ (souvent loin sous le
niveau visé). Un verrou MÉCANIQUE ne distingue pas un repli sain d'un vrai retournement :
il coupait des gagnants qui se récupéraient. Conséquence directe pour toi :
**S5 et S9 n'ont plus AUCUN verrou de gain automatique** — entre le stop catastrophe
(très loin) et le timeout, rien ne protège le profit latent d'un gagnant S5/S9.
C'est précisément là que ton jugement a de la valeur : tu vois ce que la formule ne
voit pas (choc BTC sur la bougie, breadth de capitulation, OI qui s'effondre,
concentration du book). Un LOCK sur un gagnant S5/S9 qui montre un vrai signe de
retournement CONTEXTUEL est désormais utile — mais reste rare et justifié : couper un
repli ordinaire reproduirait l'erreur qu'on vient de retirer.

Le moteur a un EDGE PROUVÉ en agrégat (walk-forward). L'asymétrie du compounding fait
que couper un gagnant coûte plus que laisser passer un perdant → biais HOLD/GO par défaut.
Mesuré : le mode de sortie qui gagne le plus SOUVENT rapporte le MOINS (verrouiller tôt
monte le taux de réussite et divise le P&L par deux). Le taux de réussite n'est PAS
l'objectif ; la queue des gros gagnants porte le P&L.

LECTURE DU CONTEXTE : mae_bps / mfe_bps = pires / meilleures excursions (PAS des pertes
réalisées) ; unrealized_bps = P&L latent actuel ; stop_bps = niveau du stop catastrophe.
"""
