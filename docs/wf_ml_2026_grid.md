# Apprentissage walk-forward 2026 — grille pré-enregistrée

**Écrite et committée le 2026-10-05, pendant le téléchargement des données,
avant toute agrégation et toute ligne de modèle.** Une seule exécution. Toute
modification après le premier résultat = nouvelle étude, comptée comme essai
supplémentaire.

## Question
Un modèle de combinaisons (prix, flux taker, OI, positionnement des gros
traders, profondeur de carnet, funding), réappris chaque semaine sur le passé
seul, **ajoute-t-il quelque chose à Alfred** hors échantillon en 2026 ?

## Données — uniquement des fichiers bulk
- Binance USDⓈ-M, data.binance.vision : `klines` 1 min (dont volume taker
  acheteur), `metrics` 5 min (OI, ratios long/short gros traders comptes et
  positions, global, ratio taker), `bookDepth` 1 min (notionnel cumulé à ±1, 2,
  5 %), `fundingRate`. **Aucune donnée issue de l'API REST** (bornée à 30 j).
- Funding HL horaire de l'archive locale (`load_funding`) pour le coût de
  portage.
- Univers : les 34 alts d'Alfred présents sur Binance ; BTC en contexte.
- **Contrôle de profondeur** : couverture horaire de chaque famille depuis le
  2026-01-01 publiée avant le modèle. Famille < 95 % → retirée par la règle,
  retrait journalisé. Aucune famille ne peut borner silencieusement la fenêtre.

## Alignement temporel
- Les barres horaires sont indexées sur leur **ouverture** ; la barre 14:00 n'est
  connue qu'à 15:00. Toutes les features sont **décalées d'une barre** : à
  l'instant de décision t, on n'utilise que des barres fermées ≤ t.
- `metrics` et `bookDepth` : valeurs horodatées ≤ t.
- Décisions toutes les 4 h aux clôtures d'Alfred (00, 04, …, 20 h UTC).
- Cible : rendement de t à t + 24 h (clôture Binance), démoyennée
  cross-sectionnellement pour l'entraînement.
- **Purge + embargo de 24 h** : aucun échantillon d'entraînement dont la fenêtre
  cible chevauche la semaine testée.

## Protocole
- Réentraînement **hebdomadaire** (lundi 00:00 UTC) sur les 8 semaines
  précédentes. Première semaine testée : la première dont l'historique de
  features (7 j) et d'entraînement (8 semaines) est complet. Dernière : fin des
  données.
- Modèle principal : `HistGradientBoostingRegressor(max_depth=3,
  learning_rate=0.05, max_iter=200, l2_regularization=1.0,
  min_samples_leaf=200)`. Second modèle : Ridge (alpha=10) sur features
  standardisées. **Hyperparamètres figés, aucun réglage.**
- Chaque modèle hebdomadaire porte une empreinte sha256 (code + liste de
  features + paramètres + bornes de la fenêtre d'entraînement) consignée dans
  un manifeste.

## Portefeuille évalué
À chaque décision : long les 3 meilleures prédictions si prédiction > 13 bps,
short les 3 pires si < −13 bps ; détention 24 h ; une position par token ;
notionnel égal 1/6 du capital ; coût 13 bps par aller-retour + funding HL réel.

## Témoins
1. **Alfred** : backtest canonique aligned, mêmes tokens, même modèle de coûts,
   mêmes semaines testées. C'est la vraie barre.
2. **Cibles mélangées** : même chaîne, cibles permutées au sein de chaque instant
   de décision. Résultat attendu ≤ 0. **Si t > 1 → fuite dans la chaîne, étude
   invalide**, quel que soit le reste.

## Critères de succès (tous requis)
1. Modèle seul : rendement hebdomadaire moyen > 0 hors échantillon, t ≥ 2.
2. Positif sur au moins 60 % des mois testés.
3. Témoin cibles mélangées : t < 1.
4. Combinaison 50/50 Alfred + modèle : Sharpe hebdomadaire > Alfred seul.

Sinon : **rejet**, sans boucle de réglage. L'importance des features est
rapportée à titre descriptif, jamais pour sélectionner.

## Journal

| Date | Événement |
|---|---|
| 2026-10-05 | Grille commitée (`7f77ef0`) pendant le téléchargement. |
| 2026-10-05 | Couverture : prix, flux, OI, positionnement, funding 100 % ; carnet 99,9 %. Aucune famille retirée. Univers appliqué : les 33 tokens d'Alfred (« 34 » dans la grille = erreur de compte). |
| 2026-10-05 | **Verdict : REJETÉ.** GBM −3,97 %/sem t −3,36 ; Ridge t −3,88 ; −27 bp/trade ; 14 % de mois positifs ; cibles mélangées t −0,57 (pas de fuite) ; Sharpe 50/50 −0,21 contre Alfred +0,16. Détail : `docs/rapport_2026_10_05.md`. |
