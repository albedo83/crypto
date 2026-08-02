# Basis & différentiels de funding inter-venues — Phase 1

> **⚠ GRILLE ÉCRITE ET COMMITTÉE AVANT EXÉCUTION.** Aucune mesure de basis ni de
> différentiel n'a été produite au moment où ces lignes sont écrites.
> L'historique git en fait foi.
>
> **Rédigé le** : 2026-08-02 · amendement de Seb intégré · Phase 0 :
> `docs/basis_phase0_inventory.md`

## 0. Périmètre

Diagnostic. Aucune stratégie n'est simulée, aucun ordre n'est placé, aucune clé
n'est utilisée. Le projet est jugé sur ses propres mérites — Alfred n'entre dans
aucune clause et n'apparaît qu'en § 5, hors verdict.

**Univers** : les **32 tokens** établis exploitables en Phase 0.
**Venue de référence pour le verdict** : **Binance** — taker perp le plus bas
(5 bps contre 5,5) et historique de funding le plus profond. Bybit est mesuré et
publié en parallèle.

## 1. Normalisation — préalable à tout différentiel

> Amendement, point 1. Sans cette étape, le différentiel mesuré est un artefact
> d'unités.

1. **Tous les taux sont convertis en bps par heure.**
   - Hyperliquid publie un taux **horaire** : `bps/h = taux × 1e4`.
   - Venue B publie un taux **par période** : `bps/h = taux × 1e4 / intervalle_h`.
2. **L'intervalle est DÉRIVÉ des horodatages, jamais du champ de métadonnées.**
   Pour chaque enregistrement, l'intervalle en vigueur est l'écart avec
   l'enregistrement précédent, arrondi à la valeur canonique la plus proche
   (1, 2, 4, 8 h). Phase 0 a établi que `fundingInfo` ne renvoie que la valeur
   **courante**, alors que Binance a basculé des paires de 8 h à 4 h en cours
   d'historique : appliquer la valeur d'aujourd'hui à trois ans de données se
   tromperait d'un facteur 2 sur les segments concernés.
3. **Grille d'évaluation = la grille de règlement de la venue B.** Le
   différentiel n'est calculable qu'aux instants où la venue B règle son
   funding ; le taux HL est **intégré** sur l'intervalle correspondant. Cela
   supprime aussi le bruit horaire de HL, qui sinon fragmenterait artificiellement
   les épisodes du § 3.

**Convention de signe** : `diff = r_HL − r_B`, en bps/h. `diff > 0` ⇒ les longs
paient plus cher sur HL que sur la venue B ⇒ la structure **short HL perp /
long B perp** encaisse `diff` par heure.

## 2. Économie (a) — ARBITRAGE — **descriptive, hors verdict**

> Amendement, point 2a.

**Basis perpétuel-spot**, sur la grille 4 h (granularité du mark Hyperliquid ;
le spot de la venue B est pris à la même clôture) :

```
basis_bps(t) = (mark_HL(t) / spot_B(t) − 1) × 1e4
```

Par token : médiane, p5, p95, |basis| p50/p90/p99, et **fraction du temps
au-dessus du seuil** `2 × AR = 58 bps` (structure HL perp × Binance spot, cf.
Phase 0 § 7).

> **Pourquoi c'est descriptif et pas un verdict.** Un écart au-dessus du seuil ne
> se monétise que s'il **converge** avant qu'on ne débouclе. Cette mesure lit un
> niveau instantané, pas une convergence : elle **surestime** structurellement ce
> qui est capturable. Elle est rapportée pour cadrer l'ordre de grandeur, et
> c'est tout.

## 3. Économie (b) — CARRY — **elle porte le verdict**

> Amendement, point 2b.

**Définition d'un épisode** — sans paramètre libre : un épisode est une suite
maximale de règlements consécutifs de la venue B pour lesquels `sign(diff)` est
constant.

Par épisode :

| grandeur | définition |
|---|---|
| durée | span en heures entre le premier et le dernier règlement de la suite |
| différentiel cumulé | `Σ diff_bps/h × intervalle_h` sur l'épisode, en bps |
| **net** | `|différentiel cumulé| − AR complet de la structure` |

`AR complet` = **19 bps** pour HL perp × Binance perp, **20 bps** pour
HL perp × Bybit perp (Phase 0 § 7, quatre fills en taker).

### Clause de verdict

> **Net > 0 sur ≥ 3 tokens, avec persistance ≥ 24 h ⇒ phase de conception
> (mission distincte). Sinon ⇒ branche close, avec rapport comparatif au
> benchmark HLP.**

Un token **compte** s'il présente **au moins un épisode** de durée ≥ 24 h **et**
de net > 0.

### Cas ambigus, tranchés d'avance

| cas | verdict |
|---|---|
| net exactement 0 | ne compte pas — la clause dit « > 0 » |
| durée exactement 24 h | compte — la clause dit « ≥ 24 h » |
| un token ne compte que par **un seul** épisode qualifiant | **il compte** au sens littéral. Mais le rapport publie, par token, le **nombre d'épisodes qualifiants**, leur **net médian** et le **net agrégé** — pour que la clause « ≥ 3 tokens » ne puisse pas être satisfaite par trois accidents isolés sans que cela se voie |
| Bybit passe là où Binance échoue | **le verdict reste celui de Binance.** La divergence est nommée et publiée, elle ne convertit pas le verdict — choisir la venue après avoir vu les deux résultats serait une sélection *a posteriori* |
| couverture < 95 % sur une des deux jambes | cellule **NON ÉMISE**, le token ne compte ni pour ni contre |
| moins de 3 tokens émettent | **run NUL**, pas de verdict — défaut de données, pas échec de la piste |

## 4. Ce que le rapport publiera dans tous les cas

- par token : n épisodes, n épisodes ≥ 24 h, n qualifiants, durée médiane et
  maximale, net médian et agrégé, différentiel moyen en bps/jour ;
- les intervalles **dérivés** par segment temporel, avec les dates de bascule
  détectées — preuve que le point 1 de l'amendement a été appliqué ;
- la couverture par token et par jambe ;
- comparaison au **benchmark passif HLP** : +16,5 % sur 12 mois glissants, dont
  **+0,13 % sur les neuf derniers** (Phase 0 du projet market making,
  `docs/mm_phase0_economics.md` § 3).

## 5. Croisement avec les creux d'Alfred — **rapporté, HORS verdict**

> Amendement, point 3. Lecture seule.

Les épisodes datés sont croisés avec les quatre fenêtres de creux figées
(`docs/dd_anatomy.md`) : A 2024-08-03→2024-11-06, B 2024-09-22→2024-10-21,
C 2024-08-03→2024-09-01, D 2025-07-21→2025-08-19.

Question posée : **le différentiel s'inverse-t-il ou explose-t-il précisément
dans les dislocations ?** Mesuré par le différentiel moyen en bps/jour dans les
fenêtres contre hors fenêtres, et par la part d'épisodes qualifiants qui y
tombent.

Ce croisement **ne participe pas au verdict** et ne peut ni le sauver ni
l'aggraver.

## 6. Interdictions

1. **Aucun seuil ajusté** après lecture des chiffres — ni sur la durée, ni sur
   le net, ni sur le nombre de tokens.
2. **Aucune sélection de venue** *a posteriori* (cf. § 3).
3. **Aucune restriction d'univers** après coup — pas de « sur les 10 plus
   liquides », pas de « en excluant les 4 h ».
4. **Aucune variante de définition d'épisode** — la définition du § 3 est sans
   paramètre, précisément pour qu'il n'y ait rien à régler.
5. **Aucune lecture partielle** : les mesures (a), (b) et le croisement § 5 sont
   lus en une fois, après le run complet.
6. **Aucun ordre**, aucune clé, aucun testnet. La Phase 1 est un diagnostic sur
   données publiques.

## 7. Invalidation du run (≠ échec de la piste)

Gate de couverture en échec sur les deux venues, garde-fou `measure_guards` qui
lève, intervalle dérivé non résoluble sur un segment, ou moins de 3 tokens
émettant une cellule ⇒ **run NUL**, correction, re-run, les deux empreintes au
rapport.

## 8. Invariant

Tout cas non prévu par cette grille est **nommé comme tel**, décrit, et ne fonde
aucune conclusion opportuniste. Clauses de repli conservatrices par défaut.

---

> **⚠ LIRE LE § 10 AVANT LE § 9.** Le verdict « phase de conception » du § 9.1 a
> été **annulé** : la clause d'épisodes qui le produit a été jugée **mal formée**
> par Seb le 2026-08-02, et la mesure de clôture du § 10 ferme la branche
> définitivement.

## 9. Résultat

**Run de verdict** : 2026-08-02T14:46Z · empreinte config `d7a415020619` · git
`c37140f+dirty` · données HL jusqu'au 2026-08-02T12:00 · cache venue B :
120 729 règlements Binance, 121 116 Bybit, 221 943 bougies spot 4 h, 32 symboles.

### 9.0 Un run NUL, déclaré (§ 7)

Le **premier** passage a sorti 25 tokens sur 32 en « NON ÉMISE, couverture < 95 % »,
avec des valeurs suspectement identiques d'un token à l'autre (94,7 % côté
Binance, 95,9 % côté Bybit) et SAND à 49,2 %.

Diagnostic : la couverture était calculée sur **tout** l'historique de la venue
B, y compris la portion antérieure au démarrage du funding Hyperliquid. Elle ne
mesurait donc pas la qualité de la donnée mais le **recouvrement des deux
séries** — SAND à 49,2 % parce que son funding HL ne commence qu'au 2024-12-04.

Correction : la fenêtre d'étude est l'**intersection** des deux historiques, et
la couverture qualifie la donnée **dans** cette fenêtre. Le run NUL est archivé
(`analysis/output/basis_phase1_NUL.log`). Après correction : couverture minimale
**95,6 %**, médiane **97,2 %**, **32/32 tokens émettent**.

Fenêtres communes : la plus longue démarre au 2023-06-08, la plus courte au
2024-12-05 (SAND).

### 9.1 Verdict de la clause — **PHASE DE CONCEPTION**

> Net > 0 sur ≥ 3 tokens avec persistance ≥ 24 h.

**32 tokens sur 32** présentent au moins un épisode de durée ≥ 24 h et de net
positif, sur Binance comme sur Bybit. La clause est franchie très au-delà de son
seuil, et pas par des accidents isolés : chaque token compte **12 à 60 épisodes
qualifiants** (médiane 13,2 % des épisodes ≥ 24 h).

Net agrégé des épisodes qualifiants, par token : médiane **+2 114 bps**, de
+257 (SAND) à +3 849 (SEI).

**Le verdict de la grille est donc : PHASE DE CONCEPTION.**

### 9.2 ⚠ CAS NON PRÉVU — ce que la clause mesure réellement

> Nommé conformément à l'invariant du § 8. Ne modifie pas le verdict ; doit être
> lu avant toute décision d'ouvrir la phase de conception.

La clause sélectionne les épisodes **dont le net est positif**. Or ces épisodes
ne sont identifiables qu'**après coup** : leurs bornes sont définies par le
retournement du signe, qu'on ne connaît qu'une fois survenu. La clause mesure
donc une **sélection ex-post**, pas une économie exécutable.

Le contre-calcul, sur les mêmes données et la même structure — jouer **tous** les
épisodes de durée ≥ 24 h, sans savoir lesquels qualifieront :

| | médiane par token | total 32 tokens | annualisé (~3,25 ans) |
|---|---:|---:|---:|
| épisodes qualifiants seuls (ex-post) | **+2 114 bps** | **+66 200 bps** | **+6,51 %/an** de notionnel |
| **tous les épisodes ≥ 24 h** | **−1 748 bps** | **−60 551 bps** | **−5,38 %/an** de notionnel |

**Tokens net-positifs si tous les épisodes ≥ 24 h sont joués : 2 sur 32**
(AAVE +47 bps, SEI +360 bps). Les trente autres perdent.

La sélection ex-post vaut **+126 751 bps** — c'est-à-dire la totalité du
résultat, et davantage. Seuls 4,5 % à 19,1 % des épisodes ≥ 24 h couvrent
l'aller-retour de 19 bps ; les autres le paient sans le rembourser.

### 9.3 Une seconde lecture qui vient de la mesure (a)

Le basis mesuré en (a) donne l'ordre de grandeur du bruit de prix entre venues :
p5/p95 typiquement **−20 / +20 bps**, |basis| p99 jusqu'à **57 bps** (DYDX),
médiane par token entre −9,4 et +1,0 bps.

Ce bruit est du **même ordre** que l'aller-retour (19 bps) et que le net médian
d'un épisode qualifiant (~+20 bps). La structure est quasi neutre en prix par
construction — deux perpétuels du même token, sens opposés — mais son résidu de
basis n'est pas petit devant l'édge théorique qu'elle vise.

### 9.4 (a) ARBITRAGE — descriptif

Basis perp HL / spot Binance, grille 4 h, seuil 2×AR = 58 bps.

| | valeur |
|---|---|
| médiane par token | entre **−9,44** (DOT) et **+0,97** (AAVE) bps |
| |basis| p90 | 8,4 à 30,7 bps |
| |basis| p99 | 13,0 à 57,8 bps |
| **fraction du temps au-dessus de 58 bps** | **0,000 % à 0,909 %** |

Sept tokens ne dépassent jamais le seuil (ADA, BCH, DOT, UNI à 0,000 %). Le
maximum est IMX à 0,909 % du temps, soit environ 3 heures par quinzaine.

Rappel du § 2 : cette mesure lit un **niveau**, pas une convergence. Elle
**surestime** ce qui serait capturable.

### 9.5 (b) CARRY — détail

| | |
|---|---|
| épisodes par token | 584 à 1 857 |
| dont ≥ 24 h | 186 à 377 |
| dont qualifiants | 12 à 60 |
| durée médiane d'un épisode | 8 à 16 h selon token |
| différentiel moyen | **−1,15 à +5,18 bps/jour** selon token |

Le différentiel moyen est **positif sur 26 tokens sur 32** côté Binance : HL paie
structurellement plus cher ses longs que Binance. C'est cohérent avec le
funding HL mesuré la veille (+1,455 bps/jour de moyenne, 78,7 % d'échantillons
positifs, `docs/mm_phase0_economics.md` § 3).

Bybit donne le même verdict (32 tokens) avec des valeurs très proches. La clause
du § 3 sur la sélection de venue *a posteriori* n'a donc pas eu à jouer.

### 9.6 Croisement avec les creux d'Alfred — HORS VERDICT

| creux | différentiel moyen DANS | HORS | épisodes qualifiants démarrant dedans |
|---|---:|---:|---:|
| A · 2024-08-03→2024-11-06 | +0,61 bps/j | +1,36 | 63 |
| B · 2024-09-22→2024-10-21 | +0,96 bps/j | +1,31 | 22 |
| C · 2024-08-03→2024-09-01 | +0,52 bps/j | +1,32 | 13 |
| D · 2025-07-21→2025-08-19 | **+2,12 bps/j** | +1,27 | 14 |

**Ni inversion, ni explosion systématique.** Le différentiel est *réduit* de
moitié dans trois creux sur quatre et *doublé* dans le quatrième. Aucun motif
exploitable, et ce croisement ne participe de toute façon pas au verdict.

### 9.7 Comparaison au benchmark passif HLP

| | rendement |
|---|---:|
| HLP, 12 mois glissants | **+16,5 %** |
| HLP, neuf derniers mois | **+0,13 %** |
| carry inter-venues, sélection ex-post parfaite | +6,51 %/an de notionnel |
| carry inter-venues, tous épisodes ≥ 24 h joués | **−5,38 %/an** de notionnel |

Comparaison donnée en pourcentage de **notionnel**, pas de capital : la
structure immobilise du capital sur **deux** venues (Phase 0 § 3), ce qui dégrade
encore le rendement sur capital. Le chiffre le plus favorable reste inférieur au
benchmark passif sur 12 mois.

### 9.8 Ce que le run établit

1. **La clause de la grille est franchie, 32 tokens sur 32.** Verdict :
   phase de conception.
2. **Le différentiel de funding HL vs venue B est réel et de signe stable** —
   positif sur 26 tokens sur 32, cohérent avec le funding HL mesuré
   indépendamment la veille.
3. **La clause mesure une sélection ex-post.** Jouer tous les épisodes ≥ 24 h
   perd sur **30 tokens sur 32**, pour −5,38 %/an de notionnel.
4. **Le bruit de basis entre venues est du même ordre que l'édge visé**
   (±20 bps contre 19 bps d'aller-retour).
5. **L'arbitrage de basis est quasi inexistant** au seuil de 58 bps : 0 à 0,9 %
   du temps, jamais pour sept tokens.
6. **Les creux d'Alfred ne produisent ni inversion ni explosion** du
   différentiel.

Le point 1 est le verdict. Les points 3 à 5 sont ce qu'une phase de conception
devrait résoudre avant de valoir quoi que ce soit, et aucun d'eux n'est un
problème de réglage.

---

## 10. CLÔTURE DE BRANCHE — le portage permanent

> **Décision de Seb, 2026-08-02** : la clause d'épisodes du § 3 est jugée **mal
> formée** — elle sélectionne les épisodes après coup (§ 9.2). **Aucune phase de
> conception n'est ouverte sur cette base.** C'est le **5ᵉ écart de grille** de
> la série, consigné au même titre que les quatre précédents. Lecture
> conservatrice appliquée : une mesure sans degré de liberté ex-post tranche.

**Run** : 2026-08-02T14:54Z · empreinte config `d7a415020619` · git `a34af8a+dirty`
· `backtests/basis/permanent_carry.py`

### 10.1 La mesure

Aucun degré de liberté ex-post, contrairement à la clause d'épisodes :

| | |
|---|---|
| positions | **une entrée, une sortie** sur toute la fenêtre d'intersection |
| frais | **un seul aller-retour** de 19 bps, pas un par épisode |
| direction | **fixée a priori** — short HL perp / long Binance perp, sur le fait structurel que le funding HL est persistamment positif (78,7 % d'échantillons positifs), pas sur le signe observé du résultat |

Un token au différentiel cumulé négatif apparaît donc **en perte**. C'est le prix
de ne pas choisir la direction après coup — et trois tokens le paient (CRV
−3,58 %, GMX −1,95 %, SAND −1,17 %).

### 10.2 Résultat brut

| | |
|---|---|
| tokens à net > 0 | **29 / 32** |
| médiane | **+3,915 %/an de notionnel** |
| meilleur | NEAR +13,32 % · PENDLE +11,94 % · LDO +11,66 % |
| pire | CRV −3,58 % · GMX −1,95 % · SAND −1,17 % |
| fenêtres | 1,60 à 3,08 ans selon token |

**Le différentiel est réel.** 29 tokens sur 32 positifs sur trois ans, avec une
direction fixée d'avance : ce n'est pas du bruit.

### 10.3 Rendement sur CAPITAL — deux hypothèses

La structure immobilise de la marge sur **deux** venues. Les deux hypothèses sont
rendues pour que le verdict ne dépende pas de celle qu'on retient.

| hypothèse | capital | **médiane** | min | max | positifs |
|---|---|---:|---:|---:|---:|
| **favorable** — « notionnel/2 » | 0,5 × notionnel (levier 4× par jambe) | **+7,830 %/an** | −7,15 % | +26,63 % | 29/32 |
| **conservatrice** — levier d'Alfred | 1,0 × notionnel (levier 2× par jambe) | **+3,915 %/an** | −3,58 % | +13,32 % | 29/32 |

### 10.4 Application de la grille — les trois barres, aucune écartée

| barre | valeur | favorable (+7,83 %) | conservatrice (+3,92 %) |
|---|---:|---|---|
| **HLP, 12 mois glissants** | **+16,51 %** | **ÉCHOUE** | **ÉCHOUE** |
| **HLP, neuf derniers mois** (annualisé) | +0,17 % | passe | passe |
| **2 × sans-risque** (T-bill 3 mois 3,78 %, [2026-07-31](https://tradingeconomics.com/united-states/3-month-bill-yield)) | **+7,56 %** | passe de justesse | **ÉCHOUE** |

La clause est un **OU** : une seule barre franchie à la baisse ferme la branche.

**La barre HLP 12 mois échoue sous les deux hypothèses de capital.** Le verdict
ne dépend donc ni du choix d'hypothèse, ni du choix de régime HLP — et le régime
le plus commode (+0,17 %) n'a pas été retenu pour trancher, conformément à
l'énoncé.

### 10.5 Ce que ça vaut à la taille réelle

Le verdict parle de « taille/structure ». Le chiffrage :

- médiane **+3,9 %/an de notionnel**, soit **+3,9 %/an de capital** sous
  l'hypothèse conservatrice ;
- sur un capital de l'ordre de **$518**, cela fait **≈ $20/an** ;
- le même capital placé au sans-risque rapporte **≈ $19,6/an**.

**L'édge entier vaut un bon du Trésor** — avant tout risque d'exécution, avant
la divergence de basis entre les deux perpétuels (§ 9.3 : ±20 bps, du même ordre
que l'aller-retour), avant les frais de transfert entre venues, et avant le
risque de liquidation sur l'une des deux jambes.

Et ce chiffre suppose **32 positions simultanées sur deux venues**, ce qui
demanderait un notionnel de plusieurs milliers de dollars par côté. À la taille
disponible, la structure n'est pas déployable telle qu'elle est mesurée.

### 10.6 Pendant les creux d'Alfred

| creux | médiane | net médian | tokens positifs |
|---|---:|---:|---:|
| A · 2024-08-03→2024-11-06 | +1,86 %/an | +48,1 bps | 21/31 |
| B · 2024-09-22→2024-10-21 | +0,72 %/an | +5,7 bps | 17/31 |
| C · 2024-08-03→2024-09-01 | **−0,93 %/an** | −7,4 bps | 15/31 |
| D · 2025-07-21→2025-08-19 | **+5,20 %/an** | +40,8 bps | 28/32 |

Aucun motif : négatif dans un creux, deux fois meilleur que la moyenne dans un
autre. Le portage ne constitue pas une couverture des creux d'Alfred — ce qui
était de toute façon hors verdict.

---

# VERDICT FINAL — **BRANCHE CLOSE**

> **Différentiel réel, non exploitable à cette taille/structure.**

| | |
|---|---|
| le différentiel existe | **oui** — 29 tokens sur 32 positifs sur ~3 ans, direction fixée a priori, médiane +3,9 %/an de notionnel |
| il bat le benchmark passif | **non** — +7,83 % au mieux contre +16,51 % pour HLP sur 12 mois |
| il bat deux fois le sans-risque | **non** sous l'hypothèse de capital conservatrice ; de justesse sous la favorable |
| il est déployable à la taille disponible | **non** — ≈ $20/an sur $518, contre ≈ $19,6/an au sans-risque |

**La branche est close définitivement.** Aucune phase de conception n'est
ouverte, ni sur les épisodes ex-post (clause mal formée, § 9.2), ni sur le
portage permanent (grille du § 10.4).

## Ce que la branche laisse derrière elle

- **Un fait établi et daté** : Hyperliquid paie structurellement plus cher ses
  longs que Binance, sur 26 tokens sur 32, de façon persistante sur trois ans.
  Ce fait ne disparaît pas avec la clôture ; il est simplement trop petit pour
  être monétisé à cette taille avec cette structure.
- **Un cache de données réutilisable** : `backtests/output/basis_cache.db`,
  120 729 règlements Binance, 121 116 Bybit, 221 943 bougies spot 4 h, 32 tokens
  sur ~3 ans.
- **Un 5ᵉ écart de grille consigné** : une clause peut être franchie et
  néanmoins mal formée. Les quatre précédents portaient sur le vocabulaire
  (magnitude contre signe, invariance isolée contre combinée) ; celui-ci porte
  sur la **structure de la mesure** — une sélection ex-post déguisée en critère.
