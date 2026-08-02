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

## 9. Résultat

> *À compléter après exécution. Vide à ce jour.*
