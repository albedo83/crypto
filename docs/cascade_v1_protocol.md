# CASCADE-v1 : protocole figé avant mesure des rendements

But : déterminer si les baisses accompagnées de clôtures massives de positions
à levier annoncent un rebond long, au-delà d'une simple bougie baissière.
Étude locale, aucun ordre, aucune modification Live/Paper.

Univers : les 34 tokens du moteur, cache horaire phase0 existant (janvier–juin
2026). OI et funding locaux. Pas de nouvelle collecte ni de sélection de tokens.
Signal à la clôture d'une bougie 1h : close < open, variation absolue OI négative
≤ percentile 5, volume ≥ percentile 95 et amplitude (high-low)/close ≥ percentile
95. Chaque percentile utilise UNIQUEMENT les 720 heures précédentes ; historique
complet requis. Il s'agit d'amplitude totale, pas de mèche ni de liquidations
confirmées. OI as-of aux deux bornes, ancienneté < 1h ; pas de saut sur une lacune.

Entrée long à l'open t+2h : une heure entière après la clôture de détection pour
éviter de supposer une disponibilité/exécution instantanée. Dé-clustering 24h
par token, décidé sans voir le rendement. Horizons 4h/24h/72h, principal 24h.
Sortie à l'open exact entrée+horizon ; aucune interpolation. Tous les pas de
prix et funding doivent être présents ; sinon résultat manquant publié.
Frais+slippage aller-retour 13 bps, stress 25 bps ; funding horaire payé sur
notionnel d'entrée constant, instants [entrée,sortie). Ce modèle est une étude
de rendement, pas un carnet ni un portefeuille. Pas de stop ni de levier supposé.

Référence : bougies baissières calculables du même token et même mois, hors
signaux bruts, avec les mêmes horizons/coûts. Différence de rendement vs moyenne
de cette référence. Présenter aussi les rendements absolus, chaque mois et
les journées d'entrée groupées à poids égal pour éviter de compter les tokens
corrélés comme autant d'expériences indépendantes. Les horizons longs se
chevauchent : pas de test statistique d'indépendance ni de p-value.

Gate exploratoire fixé : ≥30 journées distinctes résolues au principal ; rendement
net moyen et médian par journée >0 au coût stress25 ; excès moyen vs référence >0 ;
excès positif sur au moins 2/3 des mois avec événements résolus. Échec d'un point
= pas de signal candidat à déployer. Réussite = mérite seulement un rejeu de
portefeuille et une validation prospective. Pas de sweep ou seconde définition
si v1 échoue. Échantillon court et ancien, survivorship de l'univers actuel,
seuils déjà inspirés d'une recherche passée : aucune prétention hors sélection.
