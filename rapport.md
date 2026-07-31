# Rapport — Enquête sur les fondations du moteur

**2026-07-30 → 07-31** · Alfred v1.17.6 · SENIOR (live, argent réel)

> ### ⚠ À lire avant les sections 2 à 9
>
> Les mesures des **§ 2 à 9 ont été produites avant** la découverte documentée au
> **§ 10** : le backtest calculait la divergence sectorielle de S5 sur une carte de
> secteurs périmée, et ne simulait donc pas la stratégie du bot.
>
> Ce qui **tient** : les réfutations du § 4 (elles portent sur des mécanismes de
> classement, pas sur la valeur absolue du P&L), la découverte que `entry_z` est une
> constante, et le diagnostic de path-dépendance du § 3.
>
> Ce qui **tombe** : la table par semestre du § 5, la calibration du tripwire du
> § 8, et la justification de la décision du § 7. Voir § 10.
>
> Les sections sont conservées en l'état — le chemin de raisonnement, erreurs
> comprises, fait partie du résultat.

---

## 1. Le point de départ

Le 25 juillet, on a découvert que le backtest **bookait les sorties par trail à des
prix qui n'étaient pas disponibles** : il créditait le niveau théorique de la règle
alors que le marché avait déjà traversé ce niveau à l'intérieur de la bougie. Le
biais valait environ **la moitié du P&L annoncé** sur chaque fenêtre.

Le backtest a été corrigé et la documentation re-baselinée. Mais l'objection de
fond restait :

> *Si le BT mentait et qu'il est à l'origine du moteur, j'ai de sérieux doutes sur
> le moteur — il aurait dû être fait avec un BT fonctionnel. On ne prend pas le
> problème à la racine.*

C'est juste. **Re-baseliner les chiffres ne re-valide pas les décisions.** Toutes
les règles, tous les seuils, tous les signaux du bot ont été sélectionnés en
comparant des P&L calculés avec ce biais. Ce rapport documente ce qu'on a trouvé
en remontant à cette racine.

---

## 2. Première vérification : les signaux tiennent-ils encore ?

**Question** : si on refaisait aujourd'hui la sélection des signaux avec un
backtest honnête, retomberait-on sur ces cinq-là ?

Deux mesures par signal, sur 4 fenêtres :
- **contribution** — stack complet moins le stack sans lui → *le garderait-on ?*
- **autonome** — le signal tout seul → *l'aurait-on découvert ?*

| signal | contribution | autonome | verdict |
|---|---:|---:|---|
| S1 | 4/4 | 4/4 | en phase |
| S8 | 4/4 | 3/4 | en phase |
| S9 | 4/4 | 3/4 | en phase |
| S10 | 4/4 | 3/4 | en phase |
| **S5** | **2/4** | **2/4** | **hors phase** |

> **Bug trouvé au passage.** La première exécution a renvoyé 1250 trades pour
> « S1 seul ». Le moteur de backtest **ignorait la liste des stratégies activées**,
> que seul le bot respectait. La question n'était donc pas posable jusque-là.
> Corrigé (v1.16.4), sans impact sur les chiffres publiés.

**Le bot ressort largement confirmé — sauf sur S5.**

Réserve importante : la convergence de S1/S8/S9/S10 ne prouve pas grand-chose. Même
données, même optimum in-sample : qu'ils repassent était l'issue attendue. **Seule
la divergence de S5 est une information.**

---

## 3. Le paradoxe S5

S5 perd de l'argent, sans ambiguïté, sur toutes les fenêtres et dans les deux
directions :

| | S5 LONG | S5 SHORT | total |
|---|---:|---:|---:|
| 28 mois | −1753 | −532 | **−2286** |
| 12 mois | −245 | −114 | −359 |
| 6 mois | −97 | −100 | −197 |
| 3 mois | −101 | −42 | −143 |

537 trades — **43 % de toute l'activité du bot**. Aucun autre signal ne fait ça.

Mais le retirer ne passe pas non plus. Et surtout : **quand on le retire, les
quatre autres signaux prennent +153 trades et perdent $7215.**

| 28 mois | trades avec S5 | sans S5 | Δ P&L |
|---|---:|---:|---:|
| S1 | 63 → 65 | +2 | −2286 |
| S8 | 160 → 180 | +20 | −1265 |
| S9 | 132 → 190 | **+58** | −1695 |
| S10 | 358 → 431 | **+73** | −1970 |

Vrai aussi sur 3 mois, où le compounding est négligeable : S8 ajoute 5 trades et
perd $45, S9 en ajoute 10 et perd $28.

**Lecture** : le bot classe ses candidats et remplit ses slots. S5 occupant de la
place, les autres ne peuvent prendre que leurs meilleurs setups. Libérez les slots,
ils descendent dans leur liste. Le trade marginal est perdant.

D'où l'hypothèse suivante : **et si le bot devait parfois ne rien prendre du tout ?**

---

## 4. La campagne « plancher de qualité » — trois réfutations

### Découverte structurelle

Pour mesurer la qualité d'un candidat, il a fallu instrumenter le moteur. Résultat
inattendu : **`entry_z`, la variable de classement, est une constante par
stratégie** — S9 = 8.5, S8 = 7.0, S1 = 6.5, S5 = 3.5, S10 = 3.5.

Le tri `(z, force)` n'est donc pas un classement de qualité. C'est un **ordre de
priorité fixe entre stratégies**, puis la force à l'intérieur de chacune.

> **Il n'existe aucune mesure de qualité inter-signaux dans le moteur.**
> L'hypothèse de départ était mécaniquement inapplicable telle que formulée.

### Les prémisses trouvées — toutes fortes

**S5 : plus la divergence est forte, pire c'est.** Cohérent sur 3 fenêtres.

| quartile de force | Q1 (faible) | Q2 | Q3 | Q4 (forte) |
|---|---:|---:|---:|---:|
| net moyen 28m | **+187** | −33 | −21 | −44 |
| net moyen 12m | **+247** | −17 | −77 | −36 |
| net moyen 6m | **+148** | −40 | −37 | −186 |

Seul le quartile faible est positif, partout. Mécaniquement logique : S5 suit une
divergence sectorielle, une grosse divergence signifie un mouvement déjà étendu,
donc une entrée tardive. Et le moteur trie par force **décroissante** — il priorise
exactement les pires candidats.

**L'agitation du scan prédit fortement.** 5 candidats simultanés ou plus → net
+415 / +695 / +896 selon la fenêtre. Un seul candidat → nul ou négatif. Cohérent
sur 3 fenêtres.

### Les trois tests — walk-forward, fenêtres glissantes non chevauchantes

| test | résultat |
|---|---|
| plafonner la force de S5 | **3/4 au mieux**, et les seuils proches de l'optimum EDA sont les **pires** (2/4) |
| n'entrer que si le scan est agité | **0 à 2/4**, destructeur à tous les seuils |
| inverser le tri de force | **0/4 et 0/6** — l'ordre actuel est nettement meilleur (−$8098 sur 28m) |

Le troisième était le plus prometteur : ce n'est pas un filtre, il ne coupe aucun
trade, il change seulement quel candidat gagne un slot disputé. Donc très peu de
dépendance au chemin. Il perd sur les six fenêtres.

**Leçon méthodologique** : le quartile mesure les trades **qui ont été pris** ; le
tri décide **lesquels sont pris**. Inverser le tri change toute la population — ce
ne sont pas la même question.

### Aussi réfuté

L'occupation du portefeuille n'a **aucun effet** : les trades pris à 4 positions
ouvertes font mieux (WR 70,6 %) que ceux pris à portefeuille vide. Et l'effet
« rang 0 = pire » est un simple artefact de composition — le rang 0 est constitué à
48–51 % de S5.

---

## 5. La faille dans mon propre test — et la vraie découverte

Conclusion intermédiaire présentée : *« S5 perd et rien ne le répare »*. Réaction :

> *C'est une blague ? On a un signal perdant et on est incapable de le compenser si
> on l'enlève.*

Réaction justifiée. En relisant, **deux manques dans mon protocole** :

1. Le retrait de S5 n'avait été testé que sur des fenêtres **emboîtées**
   (28m/12m/6m/3m, même date de fin, chacune contenant les suivantes). J'avais
   appliqué le protocole glissant aux trois autres hypothèses, mais pas à celle-là.
2. **Personne n'avait regardé si S5 se dégradait dans le temps.**

### S5 ne perd pas — il a cassé

| semestre | trades | WR | net moyen | P&L $ | cumulé |
|---|---:|---:|---:|---:|---:|
| 2024-S1 | 66 | 50,0 % | +93 | +127 | +127 |
| 2024-S2 | 101 | 48,5 % | −9 | −61 | +65 |
| 2025-S1 | 103 | 50,5 % | +18 | +75 | +140 |
| 2025-S2 | 103 | 51,5 % | +76 | **+640** | **+780** |
| **2026-S1** | 143 | **41,3 %** | +7 | **−1316** | −537 |
| **2026-S2** | 21 | **33,3 %** | −192 | **−1749** | **−2286** |

**S5 rapporte +$780 sur quatre semestres, puis perd $3065 en six mois et demi.**
Le taux de réussite décroît continûment : 50 % → 41 % → 33 %.

Pendant ce temps les autres signaux ne bougent pas : +172, +143, +191, +182, +186
de net moyen. Stables.

**Le total négatif sur 28 mois, c'est entièrement 2026.**

Et le live confirme indépendamment : depuis le reset du 9 juillet, **S5 LONG fait
40 % de réussite sur 10 trades** — le même chiffre que le backtest sur 2026-S1.

### Le retrait, testé correctement

| retrait | 2026-01→07 | 2025-07→2026-01 | 2025-01→07 | 2024-07→2025-01 |
|---|---:|---:|---:|---:|
| S5 entier | **+148** ✓ | −251 | −189 | −90 |
| S5 SHORT | **+78** ✓ | −117 | −12 | −55 |
| S5 LONG | −14 | −170 | −53 | −281 |

Le retrait gagne dans **la seule fenêtre où S5 est cassé** et perd dans les trois où
il fonctionnait. Parfaitement cohérent.

**Ma conclusion précédente était fausse.** Le walk-forward ne disait pas « S5 est
irremplaçable », il disait « S5 marchait avant ». Je moyennais une rupture.

### Le vrai problème est méthodologique

**Le walk-forward est structurellement incapable de trancher ce cas.** Il est conçu
pour rejeter ce qui ne fonctionne que sur une fenêtre récente — c'est sa fonction et
c'est ce qui nous protège depuis des mois. Mais une **vraie rupture de régime est
indiscernable d'un sur-ajustement** de son point de vue.

Sur ce cas précis, notre garde-fou nous aveugle.

---

## 6. Le levier testé : réduire la mise plutôt que retirer le signal

### Correction préalable

J'avais avancé que « S5 porte le coefficient de taille le plus élevé du bot »
(`signal_mult` : S1 1.0, S8 1.25, S9 2.0, S10 2.0, S5 3.0). C'est vrai sur le
papier, mais **le plafond proportionnel `0.3 × capital` égalise déjà** :

| mult | taille brute | taille réelle |
|---:|---:|---:|
| **3.0** (actuel) | 472 | **300** ← plafonné |
| 2.5 | 394 | **300** ← plafonné |
| 2.0 | 315 | **300** ← plafonné |
| 1.5 | 236 | 236 |
| 1.0 | 158 | 158 |

Passer de 3.0 à 2.0 ne change **rien** — le backtest le confirme, résultats
identiques au dollar près sur les six fenêtres. Le dé-risquage réel ne commence
qu'à **1.5**.

### Résultats

**P&L** (Δ vs actuel) :

| mult | 2026 (cassé) | OOS-6 | OOS-12 | OOS-18 | pré-2026 | P&L OOS |
|---:|---:|---:|---:|---:|---:|---:|
| 1.5 | **+33** | −73 | +7 | +1 | −341 | **3/4** |
| 1.0 | **+72** | −165 | +12 | −1 | −864 | 2/4 |
| 0.5 | **+109** | −259 | +13 | −7 | −1480 | 2/4 |

**Drawdown maximum** :

| mult | 2026 | OOS-6 | OOS-12 | OOS-18 | pré-2026 |
|---:|---:|---:|---:|---:|---:|
| 3.0 (actuel) | −29,2 % | −17,7 % | −26,0 % | −35,8 % | −35,8 % |
| **1.5** | **−26,0** | **−15,4** | **−22,2** | **−32,9** | **−32,9** |
| 1.0 | −22,0 | −13,9 | −17,4 | −31,1 | −31,3 |

### Lecture honnête

À **1.5**, le drawdown s'améliore de **2,3 à 3,8 points sur toutes les fenêtres,
sans exception**. Le P&L gagne dans la fenêtre cassée et dans deux autres, et perd
$73 dans OOS-6 — précisément le semestre où S5 était à son meilleur (+$640). Le coût
sur toute la période saine est de **−$341, soit −4 %**.

**Ça ne passe pas le 4/4 strict en P&L.** Ce n'est pas un résultat validé.

Mais sa nature est différente de tout le reste de la campagne : ce n'est pas un pari
sur un edge, c'est **un échange explicite — 4 % de P&L sur la période saine contre
3 points de drawdown partout**. Sur un capital de $527 avec des drawdowns à 30 %,
ces 3 points ne sont pas décoratifs.

**Pour** : le levier ne coupe aucun trade et ne casse pas le chemin de compounding
— le seul de la journée dans ce cas. Réversible en changeant une constante.

**Contre** : si S5 se remet à fonctionner, on aura sous-misé. Et 3/4 reste 3/4.

---

## 7. La décision prise

Trois faits établis, en backtest **et** en live :

1. S5 a gagné pendant quatre semestres, perdu sur les deux derniers
2. La dégradation est **continue** (50 % → 41 % → 33 %), pas un accident isolé
3. Le live la confirme indépendamment, sur argent réel

Aucun backtest ne tranchera : le walk-forward ne sait pas distinguer une rupture de
régime d'un sur-ajustement.

### `signal_mult["S5"] = 3.0 → 1.0`

Au capital actuel de SENIOR ($527.60), la taille d'une position S5 passe de
**$158.28 à $83.10** — une réduction réelle de **47 %**, puisque 3.0 était déjà
plafonné par le cap `0.3 × equity`.

C'est une **décision de risque**, pas une amélioration d'edge validée. Elle est
prise sur une dégradation observée dans deux sources indépendantes, en assumant que
l'outil de validation habituel est structurellement muet sur cette question.

> **Ce qui a été écarté** : `1.5` (dé-risquage à moitié, drawdown amélioré de 2,3 à
> 3,8 pp) et le **statu quo**. Le retrait complet est réservé au franchissement du
> plancher défini ci-dessous.

---

## 8. Réversibilité — critères fixés à froid

Le vrai risque d'une décision comme celle-ci n'est pas de se tromper : c'est de
**re-litiger le dossier tous les mois avec les mêmes données**, en cherchant à
chaque fois la lecture qui arrange l'humeur du moment. Les seuils ci-dessous sont
donc fixés **maintenant, avant d'observer la suite**, et ne doivent pas être
re-négociés a posteriori.

### Calibration

| semestre | n | WR | ROI notionnel |
|---|---:|---:|---:|
| 2024-S1 | 66 | 50,0 % | +99,0 bps |
| 2024-S2 | 101 | 48,5 % | −22,4 |
| 2025-S1 | 103 | 50,5 % | +10,7 |
| 2025-S2 | 103 | 51,5 % | +52,9 |
| **2026-S1** | 143 | **41,3 %** | −24,7 |
| **2026-S2** | 21 | **33,3 %** | −196,9 |

Bande saine **48,5–51,5 %**, bande cassée **41,3 % et 33,3 %** : séparation de
7,2 points, **aucun recouvrement**.

Le **ROI notionnel ne sépare pas** les deux régimes (2024-S2 sain à −22,4 contre
2026-S1 cassé à −24,7). C'est le **taux de réussite** qui discrimine. Il est employé
ici comme **indicateur de santé d'un signal**, pas comme objectif d'optimisation —
la doctrine « le WR n'est pas l'objectif » reste valable pour le sizing. Le ROI sert
de **second verrou** (condition ET) pour ne pas ré-armer sur du bruit ni retirer sur
un accident.

### Les deux seuils

Fenêtre glissante **120 jours**, trades S5 clos de SENIOR, **n ≥ 50** requis (en
dessous, l'IC95 du WR dépasse ±14 points — mesurer serait se raconter une histoire).

| | condition | action pré-enregistrée |
|---|---|---|
| **Ré-armement** | WR ≥ **48 %** ET ROI ≥ **0 bps** | remonter `signal_mult["S5"]` à 3.0 |
| **Plancher** | WR ≤ **35 %** ET ROI ≤ **−100 bps** | retirer S5 de `enabled_strategies` |
| entre les deux | — | ne rien faire, mise à 1.0 |

Le plancher à 35 % est **sous le pire semestre plein mesuré** (41,3 %) ; les 33,3 %
de 2026-S2 ne portent que sur 21 trades.

### Surveillance

Câblé dans `analysis/strategy_review.py` (cron hebdomadaire, lundi 8h UTC),
constantes `S5_TRIPWIRE_*`. Le rapport Telegram affiche à chaque passage l'état du
tripwire : n, WR avec son intervalle de confiance, ROI, distance aux deux seuils.

**Aucune action automatique** — l'alerte informe, la décision reste humaine, comme
tout le reste de la surveillance. Au rythme actuel (16 trades S5 en 21 jours), la
première évaluation possible tombe vers **mi-septembre 2026**.

Complément à plus forte puissance statistique : re-jouer
`backtests/backtest_s5_oos_and_decay.py` trimestriellement — le backtest donne
~140 trades S5 par semestre là où le live en donne ~90 par trimestre.

---

## 9. La question causale — le meilleur critère de retour

Un seuil statistique dit *quand* revenir. Il ne dit pas *pourquoi*. Si on parvient à
formuler **ce que 2026 a cassé**, on obtient un critère de retour bien supérieur :
on saura surveiller la cause plutôt que le symptôme.

S5 suit une **divergence sectorielle** : il entre quand un token décroche de son
secteur, en pariant sur la continuation. Quatre hypothèses, classées par testabilité :

1. **Corrélations intra-sectorielles écrasées.** Si tous les alts d'un secteur
   bougent ensemble, une « divergence sectorielle » n'est plus qu'un bruit de
   mesure. *Mesurable* : corrélation moyenne par paire à l'intérieur de chaque
   secteur, par trimestre, et dispersion cross-sectionnelle.
2. **Divergences qui se referment plus vite.** L'edge était dans la continuation ;
   si le marché est devenu plus rapide, le hold de 48h est désormais trop long.
   *Mesurable* : distribution de `mfe_at_h` (heure du pic) des trades S5 dans le
   temps — l'instrumentation existe déjà.
3. **Composition d'univers.** L'ajout de 6 tokens et de 2 secteurs
   (`L1-major`, `Privacy`) a modifié la structure sur laquelle S5 calcule ses
   divergences. *Mesurable* : P&L S5 sur les tokens historiques vs les ajoutés.
   Cause potentiellement **auto-infligée** — à vérifier en premier, c'est la moins
   chère et la plus actionnable.
4. **Régime directionnel.** 2026 est un bull marqué (`btc_z` +1.06). Une stratégie
   de suivi de divergence peut être structurellement pénalisée quand la corrélation
   au marché domine les écarts sectoriels.

> **Aucune de ces hypothèses n'est instruite à ce jour.** Elles sont listées pour
> que le retour sur S5 ne se joue pas uniquement sur un franchissement de seuil.

---

## 10. ⚠ Renversement — la base de preuve était fausse

**Quelques heures après la décision du § 7, en instruisant les diagnostics
suivants, on a découvert que le backtest ne simulait pas la stratégie du bot.**

`backtests/backtest_sector.py` portait une carte des secteurs **codée en dur et
figée depuis v11.0.0** : 5 secteurs au lieu de 7, MKR fantôme, et **8 tokens
tradés en production sans aucun secteur** (ADA, BCH, DOT, ENA, GMX, TON, UNI, XMR).
Or `compute_sector_features` est l'**unique** source de la divergence que consomme
S5. Ces tokens ne pouvaient donc émettre **aucun** signal S5 en backtest — alors
que le live en tradait : **7 des 19 trades S5** depuis le reset.

### Impact sur les chiffres publiés

Deux arms dans le même process, mêmes données :

| fenêtre | avant | après | Δ | DD avant | DD après |
|---|---:|---:|---:|---:|---:|
| 28m | $13 209 | $9 563 | **−27,6 %** | −31,3 % | **−40,5 %** |
| 12m | $2 180 | $1 640 | −24,8 % | −22,0 % | −22,7 % |
| 6m | $872 | $795 | −8,8 % | −22,0 % | −22,7 % |
| 3m | $652 | $694 | +6,5 % | −22,0 % | −22,0 % |

138 trades S5 apparaissent sur les tokens auparavant invisibles. S5 passe de 537 à
637 trades et **évince les autres signaux**, dont les trades supprimés étaient
rentables — d'où la dégradation.

`docs/backtests.md` régénéré ; anciens chiffres annotés dans
`docs/backtests_pre_sector_parity.md`.

### La table par semestre s'effondre

| semestre | WR **avant** (faux) | WR **après** (réel) | P&L après |
|---|---:|---:|---:|
| 2024-S1 | 50,0 % | 53,2 % | +67 |
| 2024-S2 | 48,5 % | **44,2 %** | −126 |
| 2025-S1 | 50,5 % | 47,0 % | −53 |
| 2025-S2 | 51,5 % | 46,9 % | +244 |
| 2026-S1 | 41,3 % | 42,3 % | −1184 |
| **2026-S2** | **33,3 %** | **48,1 %** | **+69** |

**Il n'y a plus de séparation.** La « bande saine » va de 44,2 à 53,2 %, et 2026-S1
à 42,3 % est à peine sous 2024-S2. Surtout, **le dernier semestre est revenu à
48,1 % et en P&L positif**.

Le récit « S5 a marché quatre semestres puis a cassé » **était un artefact de la
carte périmée**. La lecture correcte est différente et moins dramatique : **S5 est
un signal chroniquement faible** (cumulé −983 sur 28 mois, négatif dès 2024-S2),
pas un signal qui s'est rompu.

### Conséquences

1. **Le tripwire du § 8 est invalidé.** Ses deux seuils reposaient sur une
   séparation qui n'existe pas. Il n'est **pas re-réglé** — ce serait exactement la
   re-négociation qu'il devait empêcher, et il n'y a rien à calibrer. Drapeau
   `S5_TRIPWIRE_VALID = False` : le détecteur mesure et rapporte, sans prescrire.
2. **La décision du § 7 perd sa justification.** Réduire la mise de S5 se défendait
   par « il a cassé en 2026 ». Ce motif est mort. Il reste un argument plus faible :
   S5 est le signal le plus faible et occupe 43 % des slots — mais réduire la
   **taille** ne libère aucun slot.
3. **Le restart est gelé** (décision utilisateur). Le bot tourne toujours avec
   **S5 à 3.0** ; v1.17.0 est committé mais dormant.

### Ce que ça dit du processus

`backtests/test_feature_parity.py` ne couvrait **aucune** feature sectorielle. Une
règle partagée ne garantit rien si ses **entrées** divergent — c'est le second biais
de mesure majeur trouvé en une semaine, après le booking des trails.

---

## 11. Grille de décision S5 — PRÉ-ENREGISTRÉE

> **Écrite et committée le 2026-07-30 à 19h UTC, AVANT exécution des re-runs.**
> Aucune ligne de cette section ne doit être modifiée après lecture des
> résultats. C'est la seule protection contre le fait de se re-négocier soi-même
> devant un 3/4 ambigu.

### Protocole

Trois études, **une seule passe, une seule lecture, aucune itération** — chaque
aller-retour supplémentaire sur ces 28 mois ré-entame le budget statistique que
la correction de parité vient de rembourser.

1. **Retrait de S5** — 4 fenêtres OOS glissantes non chevauchantes (offsets
   0/6/12/18), variantes : entier, LONG seul, SHORT seul.
2. **Grille de sizing** — `signal_mult["S5"]` ∈ {3.0, 1.5, 1.0}, mêmes fenêtres.
3. **Quartiles de force** — « seul le quartile faible gagne » survit-il ?

**Sensibilité de coût obligatoire** : chaque étude tourne à
`BACKTEST_SLIPPAGE_BPS` = **4** (modèle actuel) **et 6** (estimation ponctuelle
mesurée sur les fills réels, § 12). Verdict identique aux deux → l'incertitude de
coût n'est pas décisive. Verdict qui bascule → **on le dit, on ne choisit pas**.

### La nature de la décision a changé

Hier c'était du **dé-risquage sur rupture** : défensif, un 3/4 suffisait. La
rupture n'existe pas (§ 10). Aujourd'hui c'est de l'**allocation** — garder ou non
un signal chroniquement médiocre qui consomme 43 % de l'activité. C'est une
décision d'edge, donc **walk-forward strict**.

### La grille

| résultat du retrait | décision |
|---|---|
| **4/4** en P&L, DD non dégradé | **Retrait de S5.** Point final. Le sizing devient sans objet. |
| **3/4** | **Pas de retrait.** On bascule sur le sizing (ligne suivante). Un 3/4 sur une décision d'allocation ne franchit pas la barre — c'est précisément le cas piège, et il est tranché ici, à froid. |
| **≤ 2/4** | S5 reste. Décision reportée au sizing. |

| et alors, pour le sizing | décision |
|---|---|
| une valeur (1.5 ou 1.0) **gagne 4/4** en P&L **et** améliore le DD partout | v1.17.0 **se réveille** à cette valeur — restart à demander. |
| une valeur améliore le **DD sur 4/4** mais perd en P&L sur ≥ 1 fenêtre | **statu quo à 3.0.** On n'achète pas du confort de drawdown avec du rendement sur une décision d'allocation ; c'était admissible pour un dé-risquage sur rupture, plus maintenant. |
| aucune valeur ne domine | **statu quo à 3.0, dossier fermé** jusqu'aux données prospectives de septembre. |

**Verrou** : si le retrait sort 4/4 à 4 bps mais pas à 6 bps (ou l'inverse), le
verdict est « **cost-sensitive, non tranché** » et le statu quo s'applique.

### Le rôle des quartiles de force

Si « seul le quartile faible gagne » **survit** à la base propre, l'argument
structurel contre S5 tient **même sans rupture** : le moteur trie par force
décroissante, donc il priorise systématiquement les pires candidats S5. C'est cet
argument qui départagera un verdict serré — et seulement dans ce cas.

S'il ne survit pas, il ne compte pas : il ne sert pas à sauver un verdict qui
aurait échoué par ailleurs.

---

## 12. Audit du modèle de coûts (avant re-runs)

**Pourquoi ce détour** : la décision S5 compare un stack à 637 trades à un stack
qui en a 100 à 150 de moins. Une erreur du modèle de coûts **par trade** ne biaise
pas le P&L uniformément — elle biaise **contre ou pour le signal le plus actif**,
soit exactement le verdict à rendre. Et les marges sont fines.

**Première méthode, écartée.** Comparer le prix de fill à la clôture de la bougie
4h (méthode de `measure_live_slippage.py`) donne un écart-type de **258 bps** :
cette référence est *postérieure* à la décision, donc la mesure est dominée par la
dérive intra-bougie. IC95 obtenu : **[−47, +135] bps** — aucune puissance à n=31.
Elle n'avait fonctionné en juin que grâce à n=119.

**Méthode retenue** : le **mark au tick le plus proche de l'ordre** (±180 s), soit
le prix que le bot voyait en envoyant l'ordre. La table `ticks` l'enregistre à la
minute depuis le 2026-06-10.

| | |
|---|---:|
| slippage d'exécution aller-retour | **+5,97 bps** (médiane 3,80) |
| écart-type | **14,4** (contre 258 pour la méthode écartée) |
| IC95 | **[+0,88 , +11,05]** |
| modèle du backtest | 4,0 bps |
| par stratégie | S5 6,77 · S10 5,11 — **pas de biais par signal** |
| funding réel | −2,42 bps moyen (le bot **encaisse**) — le backtest l'intègre déjà |

**Verdict : NON RÉFUTÉ.** 4,0 est dans l'IC95. L'estimation ponctuelle est plus
haute, donc le backtest sous-facture probablement et **favorise les configurations
qui tradent le plus** — mais re-caler sur n=31 serait du refit, et refit dans le
sens de la conclusion attendue : le pire des cas.

D'où la sensibilité 4/6 bps imposée au § 11 plutôt qu'un choix.

---

## 13. VERDICT — application de la grille pré-enregistrée

**Exécuté le 2026-07-31 sur la base corrigée (parité secteurs + parité entrées),
une seule passe, six configurations.**

### Incident : première exécution invalidée

`DEFAULT_PARAMS.signal_mult["S5"]` portait déjà **1.0** (v1.17.0, dormant) alors
que le bot en service tourne à **3.0**. La « référence » comparait donc v1.17.0 à
lui-même. Le symptôme l'a trahi : `mult 1.0 → +0` sur les quatre fenêtres. Corrigé
(référence forcée à la config **en service**) et relancé. Ce n'est pas une
itération pour obtenir un meilleur résultat — c'est un baseline mal étiqueté.

### Étude 1 — retrait de S5

| variante | slip 4 bps | slip 6 bps | DD non dégradé |
|---|---:|---:|---:|
| S5 entier | **2/4** | **2/4** | 4/4 |
| S5 LONG | 1/4 | 1/4 | 2/4 |
| S5 SHORT | **3/4** | **3/4** | 3/4 |

Détail du retrait entier (slip 4) : **+282 / −110 / −204 / +185**.

### Étude 2 — grille de sizing

| `signal_mult["S5"]` | P&L | DD non dégradé | DD **meilleur** |
|---|---:|---:|---:|
| 1.5 | **2/4** | 4/4 | **4/4** |
| 1.0 | **2/4** | 4/4 | **4/4** |

Détail de 1.0 (slip 4) : **+129 / −163 / −7 / +169**.

### Application de la grille — sans re-négociation

- retrait entier **2/4** → *« ≤ 2/4 : S5 reste, décision au sizing »*
- retrait SHORT **3/4** → *« 3/4 : pas de retrait, bascule sur le sizing »*
- sizing : **DD meilleur sur 4/4 mais P&L perdu sur 2 fenêtres** → *« DD meilleur
  mais P&L perdu sur ≥ 1 fenêtre → statu quo à 3.0 »*
- verdicts **identiques à 4 et 6 bps** → le verrou « cost-sensitive » ne s'applique
  pas, l'incertitude de coût n'est pas décisive

> ## VERDICT : statu quo à `signal_mult["S5"] = 3.0`.
> **Dossier fermé jusqu'aux données prospectives de septembre.**

**v1.17.0 est annulée dans le code** (retour à 3.0). Laisser `1.0` dormant armait
un piège : le prochain restart aurait appliqué silencieusement une décision que la
grille venait de rejeter.

### Étude 3 — quartiles de force : l'argument structurel SURVIT

| quartile | net moyen 28m | 12m | 6m |
|---|---:|---:|---:|
| **Q1 (force faible)** | **+99,3** | **+78,4** | **+91,3** |
| Q2 | −8,9 | +104,6 | −82,5 |
| Q3 | −49,1 | −130,8 | −148,1 |
| Q4 (force élevée) | −72,0 | −18,3 | −94,8 |

Q1 est **positif sur les trois fenêtres**, Q3 et Q4 **négatifs sur les trois**. Le
fait structurel tient sur la base propre : **plus la divergence sectorielle est
forte, pire est le trade** — et le moteur trie par force **décroissante**, donc il
priorise systématiquement les pires candidats S5.

Conformément à la grille, cet argument ne sert **qu'à départager un verdict
serré**. Le verdict n'est pas serré. Il ne modifie donc rien aujourd'hui, et il ne
sert pas à sauver une décision qui a échoué par ailleurs.

**Il devient en revanche la piste n°1 pour la suite** : le plafond de force testé
le 2026-07-30 (3/4, refusé) l'avait été sur la base **cassée**. Ce test mérite
d'être rejoué sur la base propre — mais comme étude neuve, avec sa propre grille
pré-enregistrée, pas comme prolongement de celle-ci.

---

## 14. Le défaut de fond — et son correctif structurel

Quatre incidents en une semaine, tous le même :

| # | incident | ce qui n'était pas déclaré |
|---|---|---|
| 1 | carte sectorielle figée (v1.17.1) | quels secteurs le backtest utilisait |
| 2 | univers copié à la main (v1.17.2) | quels tokens il simulait |
| 3 | baseline mal étiquetée (v1.17.3) | quelle config la « référence » portait |
| 4 | `docs/backtests.md` régénéré sous config dormante | idem, dans le document publié |

**Une mesure qui ne déclare pas ses conditions finit par mentir sans que personne
le voie.** Le correctif suit la même philosophie que celui de l'univers : rendre
le défaut impossible à commettre silencieusement.

`backtests/fingerprint.py` — chaque run imprime et persiste :

```
┌ config d7a415020619 · git f2f7636 · run 2026-07-31T06:36Z
│ données jusqu'au 2026-07-31T04:00 (36 symboles, fichiers 2026-07-31T04:10Z)
└ signal_mult={'S1': 1.0, 'S5': 3.0, 'S8': 1.25, 'S9': 2.0, 'S10': 2.0}
```

Le hash porte sur la config **RÉSOLUE** — les valeurs après défauts et overrides,
pas le fichier. Un `signal_mult` dormant change le hash (`d7a4…` à 3.0 contre
`c102…` à 1.0). L'incident n°3 aurait sauté aux yeux ; l'incident n°4 a été
détecté par ce mécanisme dans l'heure qui a suivi sa mise en place.

L'empreinte figure désormais en tête de `docs/backtests.md`, dans le harnais
principal et dans les études décisionnelles.

### Chiffres publiés corrigés

`docs/backtests.md` d'hier avait été généré avec la config dormante. Régénéré
sur la config **en service** :

| | publié hier (S5 = 1.0) | réel (S5 = 3.0) |
|---|---:|---:|
| 28 mois | $19 127 · DD −40,5 % | **$15 463 · DD −51,4 %** |
| 12 mois | $3 280 | $3 425 · DD −31,3 % |
| 6 mois | $1 590 | $1 339 |
| 3 mois | $1 389 | $1 215 |

**Le drawdown réel de la configuration déployée est de −51,4 % sur 28 mois.**
Onze points de plus qu'annoncé — cohérent avec le verdict du § 13 (le sizing
réduit améliorait le DD sur 4/4), mais ça n'avait rien à faire dans un document
publié.

---

## 15. Décision de risque global — ÉCHÉANCE : 2026-08-02

> **Question ouverte, à trancher avant le lundi 3 août.** « À froid » sans
> échéance devient « jamais », et un chiffre pareil qui traîne sans verdict finit
> par se faire re-négocier un soir de drawdown à −35 %, c'est-à-dire au pire
> moment possible.

**Le fait, isolé de tout le reste :**

> La configuration qui tourne actuellement avec de l'argent réel a un drawdown de
> **−51,4 % sur 28 mois** de backtest honnête (§ 14). Et un drawdown de backtest
> est un **plancher optimiste** du pire cas live, pas un plafond.

**Ce que cette question n'est PAS** : une réouverture du verdict S5. La grille du
§ 11 jugeait du *relatif* — le sizing réduit améliore le DD partout mais perd du
rendement sur deux fenêtres — et le relatif est **inchangé**. Le dossier edge est
clos.

**Ce qu'elle est** : une question de **tolérance au risque**, hors du périmètre de
toute grille de validation. Elle ne se tranche pas avec un walk-forward. Les
leviers, s'il y en a un à tirer, sont d'une autre nature : taille du capital
engagé, plafond d'equity, rythme de retrait.

**Format du verdict attendu** — trois lignes, datées, quel qu'en soit le contenu :

```
2026-08-0X — décision de risque global
  fait retenu : DD backtest honnête de la config en service = −51,4 % / 28m
  décision    : …
  motif       : …
```

« Aucun changement, assumé en connaissance de cause » est un verdict parfaitement
valable. Ne pas décider n'en est pas un.

---

## 16. État d'application

| | |
|---|---|
| bot en service | **S5 à 3.0** — inchangé, aucun restart effectué |
| `signal_mult["S5"]` dans le code | **3.0** — v1.17.0 annulée (v1.17.3), code et bot cohérents |
| tripwire | câblé mais **invalidé** (`S5_TRIPWIRE_VALID = False`) : mesure sans prescription |
| parité des secteurs | **corrigée** (v1.17.1), `docs/backtests.md` régénéré, anciens chiffres archivés |
| parité des entrées | **auditée** (v1.17.2), 0 divergence, gate `audit_input_parity.py` |
| modèle de coûts | **vérifié** sur les fills réels — non réfuté, sensibilité 4/6 bps appliquée |
| **décision S5** | **TRANCHÉE : statu quo à 3.0**, dossier fermé jusqu'en septembre |

Aucune modification de trading n'est en service. Tout ce qui a été livré ce jour ne
touche que le backtest, la journalisation et la documentation.

---

## Annexe — ce qui a été livré aujourd'hui

**Correctifs**
- `v1.16.1` — l'auditeur système IA ne relit plus que les trades produits par le
  code en service (il resignalait chaque matin une anomalie déjà corrigée)
- `v1.16.2` — la table « Impact des interventions » attribuait à l'humain des
  sorties décidées par l'IA (7 sur 7 depuis le reset)
- `v1.16.3` — journalisation des verdicts HOLD de l'arbitre de sortie (55 examens
  de positions mortes ne laissaient aucune trace)
- `v1.16.4` — parité : le backtest ignorait `enabled_strategies`

**Instrumentation permanente du backtest** (opt-in, sans effet par défaut)
`entry_rank_all`, `entry_rank_taken`, `entry_z`, `entry_strength`,
`n_cands_at_open` sur chaque trade · `min_scan_candidates` · `strength_sort`

**Études**
`backtest_signal_rederivation.py` · `backtest_s5_direction_split.py` ·
`backtest_s5_removal_detail.py` · `eda_entry_quality_floor.py` ·
`eda_entry_quality_floor2.py` · `backtest_s5_strength_cap.py` ·
`backtest_scan_activity_gate.py` · `backtest_s5_oos_and_decay.py` ·
`backtest_s5_sizing_derisk.py`

Rapport détaillé de la campagne : `backtests/entry_quality_floor_results.md`
