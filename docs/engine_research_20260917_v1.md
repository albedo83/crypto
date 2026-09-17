# Recherche moteur — ENGINE-20260917-v1

Trois agents demandés par l’utilisateur, trois hypothèses fixées avant résultats,
contre-vérification séparée par l’agent principal. **Une piste exploratoire sur
les sorties, mais aucune modification de trading validée pour déploiement.**
Version de recherche ENGINE-20260917-v1 ; Alfred Live reste en 1.23.2.

## Méthode commune

Capital initial 500 USD dans chaque semestre indépendant (ne pas additionner
les gains comme une trajectoire de compte). Quatre périodes disjointes :
16 septembre 2024 →16 mars 2025 →16 septembre 2025 →16 mars 2026 →16 septembre
2026, bornes UTC12h, fin exclue. Paramètres actuels, coûts aller-retour13/15bps,
funding historique suivant le calcul canonique, mêmes snapshots bougies/OI.
Mode aligned, margin_check, mfe_on_close et realistic_trail_booking activés.
Données historiques réutilisées : ni nouvelle validation hors sélection, ni
promesse de rendement, ni conversion en EUR des coûts d’abonnement assistant.

## Résultats nets supplémentaires par rapport au témoin

Coût13bps hors funding (funding inclus dans les résultats), montants USD :

| Hypothèse | Sept24–mars25 | Mars25–sept25 | Sept25–mars26 | Mars26–sept26 |
|---|---:|---:|---:|---:|
| S1 : une extension24h si prix encore en gain au timeout72h | +44,44 | +27,59 | 0,00 | +12,17 |
| Classement à priorité égale par funding passé moins coûteux | −203,90 | +18,69 | +0,47 | −120,16 |
| Maximum3 positions de même stratégie ET direction | +86,22 | +13,16 | −113,14 | +41,90 |
| Maximum2 positions de même stratégie ET direction | −129,93 | −77,53 | −418,82 | +117,35 |

Le profil reste le même au coût15bps. Le semestre sans effet de l’extension ne
contient aucun S1 : ce n’est pas une validation positive de cette règle.

### Ce que montre réellement la piste S1

Le témoin termine le semestre récent à524,06 ; la variante à536,23. Le gain
supplémentaire est réel dans cette simulation, mais petit face à100EUR/mois
d’abonnement. La condition additionnelle « BTCz favorable » ne change AUCUN
trade : variante conditionnelle et contrôle sans condition sont identiques.
Il n’y a donc pas de justification à ajouter ce filtre macro à partir de ce test.

Le drawdown réalisé récent passe de−32,62 % à−35,89 %. Le proxy aux clôtures4h,
avec réserve de coûts et funding latent, passe de−32,75 % à−36,02 %. Le gate
préétabli (pas plus de2points de dégradation) échoue. Ce proxy ne mesure pas
le pire risque intrabougie ; une perte maximale acceptée de50 % ne vaut pas
validation d’une stratégie affichant un chiffre inférieur en backtest.

Plus important : sur les entrées communes du semestre récent, la composante
rendement est **−28,60USD**, la composante taille **+5,20USD** et les trades
non communs **+35,57USD**, donnant le delta+12,17. C’est une identité comptable,
pas une attribution causale. Elle montre néanmoins qu’on ne peut pas raconter
que « laisser courir les gagnants améliore directement les sorties » sur cette
période : le gain portefeuille dépend des entrées que les slots occupés changent.

### Portefeuille

Le plafond3 est un compromis à étudier, pas un gagnant universel : il améliore
3semestres et réduit le risque sur le récent, mais perd113,14USD sur un semestre
porteur. Le plafond2 ampute trop de gains. Aucun réglage choisi après résultats.

## Contre-vérification et incident de mesure

- 10tests sorties (dont36combinaisons d’identité),5tests classement,4tests
  portefeuille et3tests audit parent : **22tests passent**.
- **64résultats** de variantes/témoins contrôlés :24sorties+16entrées+24portefeuille.
- Huit témoins séparés du parent ; les **24ledgers témoins des agents sont
  identiques intégralement** à ceux du parent, pas seulement en PNL.
- Sommes PNL, chronologie, nombre de positions par bougie et capital réalisé
  reconstruits ; aucune valorisation ouverte sans bougie parmi ces64runs.
- Proxy4h de risque et exposition recalculé ; décomposition des entrées communes
  revue par un second agent. Toutes les fenêtres et variantes conservées.
- Défaut trouvé dans le backtest historique : au dernier scan, un ordre pouvait
  prendre l’open de la bougie suivante, hors fenêtre, puis être marqué clôturé
  avant son entrée. Impact témoin observé inférieur à0,40USD par semestre.
  **Correction dans les copies de recherche**, archives initiales invalidées,
  puis toute la matrice relancée. Pas de réglage des hypothèses entre les runs.

## Limites du moteur encore présentes

Le funding canonique emploie la moyenne des taux disponibles multipliée par la
durée, pas une intégrale horaire exacte. OI TON périmé depuis juin2026. Le DD
natif porte sur les clôtures et omet le MTM final dans son calcul ; le proxy
pré-sortie peut valoriser au close une position stoppée intrabougie. Décalages
d’horodatage bougie/funding et précision centimes subsistent. Aucun indicateur
ici ne garantit le drawdown Live. Les variantes restent intégralement isolées.

## Décision

Écarter le classement par funding et le plafond2. Conserver S1+24h et plafond3
comme pistes avec compromis explicités, **sans déploiement et sans fusionner les
deux variantes** sur ce même échantillon. Avant argent réel : vérifier la mesure
sur une période nouvelle et l’impact portefeuille ; éviter une boucle de réglages
qui finit par gagner sur l’historique connu. Le bug de borne a son patch conservé,
mais le moteur de production n’a pas été modifié par cette campagne.

## Artefacts et reproduction

[Archive complète](engine_research_20260917_v1.tar.gz) : protocoles, scripts,
patches des moteurs isolés, tests, rapports, ledgers corrigés et premiers runs
invalidés. [Manifest et hashes](engine_research_20260917_v1_manifest.json).
[Audit indépendant](engine_research_20260917_v1_validation.json).

Restaurer une copie isolée du commit source indiqué au manifest pour chaque
agent, puis appliquer son `engine.patch` et superposer ses scripts/docs.
Les scripts conservent leurs chemins `/tmp/alfred-research-…-20260917` afin de
reproduire exactement la session. Les entrées figées sont également conservées
hors Git dans `backtests/output/engine_research_20260917_v1/` (bougies/OI et
funding public) ; les fournir aux chemins SNAP/DB explicités dans les scripts,
puis comparer leurs hashes. Ne jamais exécuter ces patches sur le moteur Live.

Notes de recherche v1 : trois hypothèses, correction chronologique commune,
matrice complète et contre-vérification indépendante. Aucun restart requis.
