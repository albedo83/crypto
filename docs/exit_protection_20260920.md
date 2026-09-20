# Protection des gains — EXIT-PROTECT-20260920-v1

## Décision du 20 septembre

Ne pas activer de nouvelle sortie dans Live. Une règle de protection simple
présente un effet trop faible et trop peu documenté pour modifier le moteur.
La campagne prospective shadow-v1 reste intacte ; aucun redémarrage requis.

## Hypothèse et protocole

Sur S1/S5, armer une protection après un mouvement favorable de 1 000 bps
(+10 % du prix d'entrée, sans multiplicateur de levier), puis sortir lorsque
le rendement brut retombe à 50 % du meilleur rendement observé jusque-là.
Exécution au tick suivant, avec 2 bps de glissement adverse. Jamais de fill
au pic historique ni au seuil franchi. Les sorties réelles restent la borne
terminale : cette étude ne simule que des sorties anticipées.

Une seule règle testée, sans recherche de meilleurs seuils. Elle a été choisie
après inspection des trades récents : ce n'est pas une validation indépendante.
Les ticks sont échantillonnés environ chaque minute. Tout intervalle supérieur
à 120 secondes entre entrée et sortie exclut le trade entier, y compris un
trou après une sortie hypothétique. Critère conservateur, avec biais de sélection
possible : ne pas extrapoler aux trades exclus. Aucune interpolation.

## Résultats

| Période | Trades examinés | Couverture complète | Sorties modifiées | Delta de prix USDT |
|---|---:|---:|---:|---:|
| Clos avant le 17 septembre | 79 | 54 | 5 | +3,52 |
| Entrés depuis le 17 septembre | 4 | 1 | 1 | +10,24 |
| À cheval sur le 17 septembre | 3 | 0 | 0 | indisponible |

86 trades examinés, 31 exclus. Avant le 17 septembre : trois sorties améliorées,
deux dégradées. La règle aurait notamment retiré 7,45 USDT au gain d'un trade MINA.
Le seul trade récent entièrement couvert est AVAX : environ +10,24 USDT de
différence de prix. NEAR et UNI récents ne permettent pas de conclure dans ce
protocole strict : leur couverture intégrale est insuffisante.

Il s'agit d'un delta de sortie aux entrées et tailles réelles constantes,
**pas d'un gain net portefeuille**. Différence de funding omise ; même taux de
frais de sortie supposé. Places libérées, nouvelles entrées, marge et sizing
ultérieurs non rejoués. Positions encore ouvertes exclues. Aucun rendement
mensuel, drawdown portefeuille ou bénéfice futur ne peut être déduit.

## Contrôle complémentaire Live/Paper

L'audit des entrées du 17 septembre au 20 septembre à 12 h UTC retrouve
+100,59 USDT réalisés pour Live, +84,25 pour Paper. Sur trois paires closes,
la composante rendement est -0,66 USDT, la composante taille -12,36 USDT ;
les trades non appariés apportent +29,36 USDT d'écart. Identité comptable
vérifiée, sans attribution causale à l'IA. Ce périmètre par date d'entrée est
différent du bilan par date de clôture communiqué auparavant.

## Livraison et reproduction

7 tests comportementaux passent : LONG/SHORT, tick suivant, absence de lecture
du futur, lacunes, ordre des ticks, absence de fill après la clôture réelle.
Contre-calcul indépendant des 86 couvertures, des déclenchements et des deltas.
Les entrées figées sont archivées avec leur hash et les scripts.

Depuis le dépôt : `python3 -m unittest test_exit_protection -q`.
Nouvel audit : `python3 -m backtests.audit_exit_protection --data-dir alfred/data --out /tmp/exit_review.json --snapshot-out /tmp/exit_inputs.json.gz`.
L'audit ouvre uniquement les bases en lecture seule, sans broker ni API IA.
Pour reproduire le contrôle archivé : extraire `exit_protection_20260920.tar.gz`
puis exécuter `python3 verify.py` dans le répertoire extrait.

Version de recherche uniquement ; Alfred reste en 1.24.0. Prochaine étape :
examiner les déclenchements des variantes existantes au bilan du 24 septembre,
puis décider si cette nouvelle piste justifie un véritable rejeu portefeuille.
