# Vague 1 « à l'envers » — grille pré-enregistrée (2026-10-19)

**Écrite et commitée avant tout calcul.** Une exécution par test ; toute
modification après un premier résultat = nouveau test, compté.

Constat de départ (réel, depuis le 2026-07-09) : les stops catastrophe et
hard-stops coûtent −434 $ sur 22 trades au paper (−291 $ sur 22 au live), quand
tout le reste rapporte +447 $ (paper). Le moteur gagne sur ~90 % des trades et
perd tout sur ~10 %.

## Cadre commun
- Moteur testé = **configuration live actuelle** : S1, S8, S9, S10 (S5 coupé
  depuis la v1.31.0), backtest aligned, `margin_check`, coûts et funding réels.
- Période 2024-04-19 → 2026-10-19 (10 trimestres de 3 mois).
- **Découverte** = 2024-04-19 → 2025-12-31 ; **validation** = 2026-01-01 →
  2026-10-19, lue une seule fois par test, sur une règle figée en découverte.
- Aucun paramètre ajusté sur la validation.

## Test 1a — Anatomie des stops catastrophe
Features d'entrée du backtest (`entry_feats` + stratégie, sens, btc_z).
Découverte : taux de stop catastrophe par quintile de chaque feature. Feature
retenue = la plus discriminante (rapport pire quintile / reste), **seulement si**
χ² p < 0,01 / k (Bonferroni, k = nombre de features). Règle figée : ne pas
entrer dans le pire quintile (seuil de découverte).
**Validé si**, sur 2026 : P&L ≥ référence, moins de stops catastrophe, DD max pas
pire de plus de 2 pp.

## Test 1b — Taille inverse à la volatilité
Multiplicateur de taille = clip(médiane cross-section du `vol_7d` / `vol_7d` du
token, 0,5, 1,0) : on ne fait que **réduire** les tokens plus volatils que la
médiane (pas d'agrandissement, le cap 0,3 × equity reste la limite de risque).
Aucun paramètre ajusté.
**Validé si** : sur les 10 trimestres (capital remis à 1 000 $), somme des P&L
/ |pire DD| meilleure que la référence, **et** P&L 2026 ≥ 90 % de la référence.

## Test 2 — La courbe de la stratégie comme signal
Série journalière à notionnel fixe (P&L de chaque trade en bps × 150 $) du moteur
sur toute la période. Filtre : on n'ouvre que si la courbe cumulée est au-dessus
de sa moyenne mobile 20 jours à la clôture de la veille (10 et 40 rapportés à
titre de sensibilité, pas de sélection). Approximation documentée : les trades
écartés sont retirés sans redistribuer leurs slots.
**Validé si** (N = 20) : DD max réduit d'au moins 25 %, P&L total ≥ 80 % de la
référence, P&L 2026 ≥ 80 % de la référence.

## Test 5 — Heures de règlement du funding Binance (0 h, 8 h, 16 h UTC)
Bougies Binance 1 min, 33 alts d'Alfred, funding Binance du règlement précédent
(connu ex ante). Si |funding| ≥ 1 bp : position = −signe(funding). 8 variantes :
entrée T−60 ou T−30 min × sortie T, T+30 ou T+60 min (6) + entrée T sortie
T+60 et T+120 (2). Coût 13 bps aller-retour.
**Validé si** : t ≥ 3 en découverte (clusterisé par jour, janvier → juin 2026) ET
validation (juillet → octobre) moyenne nette > 0 avec t ≥ 1,5. Le brut est aussi
rapporté.

## Test 6 — Jours macro (FOMC, CPI)
Dates officielles : 22 décisions FOMC (federalreserve.gov) et 33 publications
CPI (bls.gov), 2024 → 2026. Trade « exposé » si sa détention couvre 12:00 → 21:00
UTC d'un jour d'événement. Règle : aucune entrée dans les 24 h qui précèdent un
événement.
**Validé si** : P&L ≥ référence sur la découverte **et** sur la validation, avec
moins de stops catastrophe.

## Test 7 — HLP comme destination du capital
Historique du coffre HLP (`vaultDetails`, API publique). Allocation statique
0 / 25 / 50 % dans HLP, le reste dans le moteur (rendements journaliers).
**Validé si** : à 25 % ou 50 %, le rapport rendement / |DD max| bat le moteur seul
sur 2026 **et** sur toute la période commune.

## Collecteur (pistes 3 et 4, prospectif)
Positions publiques (`clearinghouseState`) de 300 comptes du classement public :
150 plus gros comptes actifs, 150 perdants persistants actifs (P&L total < 0,
P&L du mois < 0, volume de la semaine > 0). Instantané toutes les 15 min.
Pas de verdict avant **au moins 4 semaines** de données ; grille d'analyse
(carte des liquidations, indice des perdants) écrite avant la première lecture.

## Addendum 2026-10-19 — carnet d'ordres à la bonne échelle (avant calcul)

Le carnet n'avait été testé qu'en moyennes horaires dans un modèle à 24 h.
Données : Binance `bookDepth` (instantané par minute, notionnel cumulé à ±1 %),
bougies 1 min, 33 alts d'Alfred, 2026-01-01 → 2026-10-18. Déséquilibre
imb = (bid₁ − ask₁) / (bid₁ + ask₁).

**Test 8A — le carnet prédit-il les minutes suivantes ?** Rendement à 1, 5, 15,
60 min selon les déciles d'imb (déciles par token, calculés en découverte
janvier → juin). Mesure : écart décile haut − décile bas, et rendement d'une
position qui suit le signe d'imb aux déciles extrêmes. t clusterisé par jour.
- « Prédictif » si la validation (juillet → octobre) a le même signe que la
  découverte avec t ≥ 3.
- « Exploitable en taker » si, en validation, cette position nette de 13 bps est
  > 0 avec t ≥ 2.

**Test 8B — le carnet pour choisir le moment d'entrée.** Entrées du moteur (S1,
S8, S9, S10, backtest aligned) en 2026. Le sens de l'effet est celui de 8A en
découverte, à 15 min. Si le carnet annonce un mouvement dans notre sens, on entre
à la minute ; sinon on attend, minute par minute, au plus 30 min. Mesure : gain sur
le prix d'entrée en bps par rapport à une entrée immédiate. Témoin : délai fixe de
15 min. **Validé si**, en validation (entrées juillet → octobre), gain moyen
≥ +3 bps avec t ≥ 2 et meilleur que le témoin.

## Addendum 2026-10-19 (2) — test 1c, frein de contagion (avant calcul)

Les stops catastrophe arrivent-ils en grappes ? Mesure descriptive : part des stops
survenus à moins de 24 h d'un autre stop dans le même sens.
Règle, seuils fixés a priori, aucun ajustement : **pas de nouvelle entrée dans le
sens d si au moins 2 positions ouvertes dans le sens d sont à −500 bps ou pire**
(clôture 4h de l'entrée). Évaluation par reconstruction sur le backtest continu du
moteur (prix d'entrée et latents depuis les bougies 4h, notionnel fixe 150 $,
trades écartés retirés sans redistribuer leurs slots — même approximation que le
test 2).
**Validé si**, sur la découverte ET sur la validation : P&L ≥ référence et moins de
stops catastrophe.

## Addendum 2026-10-19 (3) — test 1d, plafond de positions dans le même sens (avant calcul)

Le test 1c montre des stops en grappes (56 % à < 24 h d'un autre stop du même
sens) issues de positions ouvertes ensemble, pas d'ajouts en pleine chute.
Hypothèse : abaisser `max_same_direction` (4 actuellement ; son relèvement a été
rejeté en septembre, son abaissement jamais testé). Variantes 3 et 2, aucune autre.
**Validé si** (même critère que 1b) : sur les 10 trimestres, somme des P&L / |pire
DD| meilleure que la référence, **et** P&L 2026 ≥ 90 % de la référence.
