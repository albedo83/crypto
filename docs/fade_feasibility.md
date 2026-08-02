# Fade des perdants persistants — étape 1 : est-ce fadable ?

> **⚠ GRILLE ÉCRITE ET COMMITTÉE AVANT TOUT CHIFFRE.** Aucune des trois mesures
> n'a été calculée au moment où ces lignes sont écrites. L'historique git en
> fait foi.
>
> **Rédigé le** : 2026-08-02 · **in-data** : panels roulants de la Phase 1
> (`docs/wallet_persistence.md`), **aucune donnée nouvelle**, aucun appel
> supplémentaire à l'API.

## 0. La question

La Phase 1 a établi que les perdants **persistent en rang** — quatre strates sur
quatre, t de +2,91 à +6,68 — là où les gagnants ne persistent pas du tout.

Une persistance de **rang** n'est pas une persistance d'**argent**, et une perte
n'est pas forcément fadable : un compte qui meurt de ses frais ne rapporte rien à
qui prend la position inverse, puisque celle-ci paie ses propres frais.

Cette étape pose donc deux questions dans cet ordre :

1. **Perdent-ils de l'argent** en T+1, pas seulement du rang ?
2. **De quoi meurent-ils** — d'une exposition directionnelle (fadable en
   principe) ou d'une dérive de friction (non fadable) ?

## 1. Population — la même qu'en Phase 1, sélectionnée ex-ante

**Décile inférieur des perdants du panel T** : parmi les comptes du panel T dont
la métrique en T est négative, les 10 % les plus mal classés.

La sélection reste décidée **au seul trimestre T**, avec le filtre d'éligibilité
inchangé (valeur au début de T ≥ $500, ≥ 1 semaine de P&L non nul dans T). Rien
de ce qui suit ne consulte T+1 pour sélectionner.

Les trois mesures portent sur **cette même population**, évaluée sur ses
semaines de **T+1**.

## 2. Mesure 1 — P&L absolu forward

Pour chaque compte du décile, sur T+1 :

```
rendement_forward = pnl(T+1) / valeur de compte au début de T+1
```

Dénominateur = valeur au **début de T+1** (et non la médiane du trimestre) :
c'est le capital qu'un opérateur verrait au moment de prendre la position
inverse.

Rendu **par strate et par paire de trimestres**, avec **intervalle de confiance
à 95 %** (moyenne ± 1,96 × erreur-type).

**Cellule sous-dotée (< 10 comptes) : NON ÉMISE.**

## 3. Mesure 2 — décomposition de la perte

Pour chaque compte du décile, régression de ses rendements par intervalle sur
les deux facteurs de marché, sur ses intervalles de T+1 mis en commun :

```
r_compte = α + β_btc · r_btc + β_alt · r_alt + ε
```

- `r_compte` = variation du P&L cumulé sur l'intervalle, divisée par la valeur
  de compte au début de l'intervalle ;
- `r_btc` = rendement de BTC sur **exactement le même intervalle** ;
- `r_alt` = rendement de l'indice équipondéré des alts sur le même intervalle.

Les points `portfolio` sont hebdomadaires mais **d'espacement variable** — les
facteurs sont donc calculés sur l'intervalle réel de chaque point, pas sur une
semaine calendaire supposée.

### La décomposition, exacte par construction

L'identité des moindres carrés donne, sur la moyenne :

```
r̄_compte = α + β_btc · r̄_btc + β_alt · r̄_alt
```

d'où :

| composante | expression | fadable ? |
|---|---|---|
| **directionnelle** | `β_btc · r̄_btc + β_alt · r̄_alt` | **oui en principe** — c'est une exposition |
| **friction** | `α` | **non** — frais, funding, slippage. La position inverse paie les siens |

```
part_friction = |α| / ( |α| + |directionnelle| )
```

**Compte avec moins de 10 intervalles exploitables : exclu de la mesure 2.**
**Part indéfinie (α et directionnelle tous deux nuls) : cellule NON ÉMISE.**

> **Limite nommée d'avance** : l'indice alt est construit sur
> `Params.trade_symbols` (34 tokens), alors que les wallets du panel tradent
> l'univers entier d'Hyperliquid. C'est un proxy, pas l'univers réel de ces
> comptes. Le biais joue contre la part directionnelle mesurée — un facteur
> incomplet laisse de l'exposition dans le résidu, donc **surestime la
> friction**. La clause de friction est donc lue comme un plafond, et sa
> réserve est publiée avec le chiffre.

## 4. Mesure 3 — stabilité de la part directionnelle

Pour les comptes du décile apparaissant dans **au moins deux paires**, les
`β_btc` sont estimés **par trimestre** et comparés d'un trimestre au suivant :

- **corrélation de rang de Spearman** des β entre trimestres adjacents ;
- **part des comptes à signe de β stable** d'un trimestre au suivant.

**Estimation par compte-trimestre avec moins de 10 intervalles : écartée.**
**Moins de 30 comptes appariés : cellule NON ÉMISE.**

## 5. GRILLE DE VERDICT — pré-enregistrée

Lue **dans cet ordre**, la première qui se déclenche tranche.

| clause | condition | verdict |
|---|---|---|
| **C1** | P&L forward du décile **non négatif** — IC à 95 % contenant 0 sur **≥ la moitié** des paires | **BRANCHE CLOSE** — la persistance de rang ne se convertit pas en argent |
| **C2** | P&L forward négatif **mais** part de friction **> 60 %** (médiane du panel) | **BRANCHE CLOSE** — verdict *« ils meurent de leurs frais, pas de leur direction »* |
| **C3** | P&L forward négatif **et** part directionnelle substantielle **et** stable | **ÉTAPE 2** : spécification d'un collecteur de positions **live** sur panel pré-enregistré (mission distincte) |

### Cas ambigus, tranchés d'avance

| cas | verdict |
|---|---|
| exactement la moitié des paires à IC contenant 0 | **C1 se déclenche** — la clause dit « ≥ la moitié » |
| part de friction exactement 60,0 % | **C2 ne se déclenche pas** — la clause dit « > 60 % » |
| P&L forward négatif, friction ≤ 60 %, mais part directionnelle **instable** | **BRANCHE CLOSE.** C3 exige « substantielle **et** stable » ; à défaut des deux, repli conservateur, conformément à l'invariant |
| strates en désaccord (l'une ferme, l'autre non) | le verdict se lit sur **l'agrégat** ; le désaccord est **nommé et publié**, il ne convertit pas le verdict |
| moins de 3 paires émettent | **run NUL**, pas de verdict |

**« Substantielle »** est fixé ici, avant les chiffres : part directionnelle
**≥ 40 %** de la perte (le complément exact du seuil de friction de C2, pour
qu'il n'y ait aucune zone grise entre les deux clauses).
**« Stable »** est fixé ici : Spearman des β entre trimestres adjacents avec
**|t| ≥ 2** sur les β mis en commun, **et** part de comptes à signe stable
**> 50 %**.

## 6. Interdictions

1. Aucun seuil ajusté après lecture — ni 60 %, ni 40 %, ni le décile, ni les
   planchers d'effectif.
2. Aucune redéfinition de la population — le décile inférieur des perdants du
   panel T, point.
3. Aucun facteur de marché ajouté après coup pour faire baisser la friction.
4. Aucune lecture partielle : les trois mesures sont lues en une fois.
5. Aucune donnée nouvelle — l'étape 1 est strictement in-data.

## 7. Invalidation du run (≠ verdict)

Garde-fou `measure_guards` qui lève, série de facteurs constante, moins de
3 paires émettant une cellule, ou colonne temporelle dégénérée ⇒ **run NUL**,
correction, re-run, les deux empreintes au rapport.

## 8. Invariant

Tout cas non prévu est **nommé comme tel**, décrit, et ne fonde aucune
conclusion opportuniste. Clauses de repli conservatrices par défaut.

---

## 9. Résultat — **BRANCHE CLOSE**

**Run** : 2026-08-02T15:32Z · empreinte git `fce59d0+dirty` ·
`backtests/wallets/fade_step1.py` · 997 comptes, 11 trimestres avec décile
constitué, **183 comptes-décile** au total.

### 9.0 ⚠ Mesure 1 NON ÉVALUABLE — et pourquoi

La mesure 1 telle que spécifiée divise le P&L de T+1 par la **valeur de compte
au début de T+1**. Or ce dénominateur vaut quelques centimes pour un compte
ruiné. Le calcul littéral produit :

```
2024Q3→2024Q4  moyenne +264 186 115 469,59 %  IC [−91 754 391 990 , +620 126 622 929]
2025Q4→2026Q1  moyenne    +289 848 419,98 %
```

**Ce ne sont pas des rendements, ce sont des divisions par zéro déguisées.**
La clause C1 se déclenchait sur ces chiffres (8 paires sur 9 avec un IC
contenant 0) — et le verdict qu'elle produisait allait dans le sens de la
clôture. **Je ne le retiens pas** : une clause conservatrice rendue sur une
statistique explosée reste une clause rendue sur une statistique explosée.

**Mesure 1 : NON ÉVALUABLE.** C1 n'est pas rendue.

### 9.1 Le cas non prévu — la population est majoritairement infadable

L'explosion n'est pas un défaut de code, c'est une propriété de la population,
et elle est mesurable proprement :

| sur les 183 comptes-décile | n | part |
|---|---:|---:|
| sans aucune donnée en T+1 | 5 | 3 % |
| **valeur de compte < $10 au début de T+1** | **91** | **50 %** |
| **P&L exactement nul en T+1** (arrêt d'activité) | **34** | **19 %** |
| **solvables (≥ $500) ET actifs en T+1** | **32** | **17 %** |

> **On ne fade pas quelqu'un qui est ruiné, et on ne fade pas quelqu'un qui a
> arrêté de trader.** Quatre comptes sur cinq du décile inférieur sont, en T+1,
> hors d'état d'être fadés — non pas parce qu'ils gagnent, mais parce qu'ils ne
> sont plus là.

Ce fait n'était prévu par aucune clause. Il est **nommé** et il ne fonde à lui
seul aucun verdict — mais il explique pourquoi la mesure 1 ne pouvait pas
fonctionner, et il devra figurer dans toute reprise du sujet.

### 9.2 Mesure 2 — la clause C2 se déclenche

Décomposition sur **95 régressions** retenues (≥ 10 intervalles chacune) :

| strate | n | **friction** | direction | α médian (/intervalle) | β_btc médian |
|---|---:|---:|---:|---:|---:|
| petit | 44 | **75,4 %** | 24,6 % | −0,287 % | +0,022 |
| moyen | 28 | **71,7 %** | 28,3 % | −2,945 % | −0,567 |
| gros | 23 | **66,4 %** | 33,6 % | +0,004 % | +0,034 |
| **agrégat** | **95** | **73,6 %** | **26,4 %** | −0,040 % | +0,011 |

**Friction médiane 73,6 % > 60 % ⇒ C2 SE DÉCLENCHE**, et sur **les trois
strates** prises séparément (75,4 / 71,7 / 66,4). Aucun désaccord entre strates
à signaler.

Par compte, **68 % des régressions** ont une part de friction supérieure à 60 %.

#### Contrôle de robustesse — le ratio n'est pas un artefact du dénominateur

La part de friction est un **rapport** `|α| / (|α| + |directionnelle|)` : un
dénominateur trop petit gonfle α et la part directionnelle **dans la même
proportion**, et le rapport en est largement protégé. Vérifié :

| population | n | friction médiane | p25 | p75 | part > 60 % |
|---|---:|---:|---:|---:|---:|
| toutes les régressions | 95 | **73,6 %** | 49,8 % | 87,9 % | 68 % |
| **restreinte aux comptes solvables** (AV ≥ $500 au début de T+1) | 17 | **73,0 %** | 50,7 % | 83,1 % | 65 % |

Le chiffre ne bouge pas de 0,6 point. Ce contrôle est **rapporté comme
sensibilité**, pas comme redéfinition de la population de verdict (§ 6.2).

> **Rappel de la réserve inscrite au § 3** : l'indice alt est un proxy sur
> 34 tokens quand les wallets tradent tout Hyperliquid. Un facteur incomplet
> laisse de l'exposition dans le résidu et **surestime donc la friction**. La
> valeur de 73,6 % est à lire comme un **plafond**. Elle reste au-dessus du
> seuil de 60 % même en accordant une marge substantielle à cette réserve —
> mais le plafond est nommé, pas dissimulé.

### 9.3 Mesure 3 — NON ÉMISE

**0 compte apparié** sur les 30 requis. Aucun compte du décile n'apparaît dans
deux trimestres consécutifs avec ≥ 10 intervalles exploitables de chaque côté —
conséquence directe du § 9.1 : ils disparaissent avant d'avoir un second
trimestre.

C'est en soi un fait : **la population du décile ne se reconduit pas**.

### 9.4 Verdict

| clause | état | résultat |
|---|---|---|
| **C1** — P&L forward non négatif | **NON ÉVALUABLE** (§ 9.0) | non rendue |
| **C2** — friction > 60 % de la perte | **ÉVALUABLE et ROBUSTE** | **SE DÉCLENCHE** (73,6 %) |
| **C3** — direction substantielle et stable | direction à 26,4 % < 40 % ; stabilité NON ÉMISE | ne peut pas se déclencher |

> # **BRANCHE CLOSE**
>
> **Motif C2 — « ils meurent de leurs frais, pas de leur direction ».**

Le verdict repose sur la **mesure 2 seule**, la seule des trois qui soit
évaluable et robuste. Les deux autres pointent dans le même sens sans porter la
décision : la mesure 1 est cassée par la population, la mesure 3 est vide faute
de comptes reconductibles.

### 9.5 Ce que l'étape 1 établit

1. **La persistance de rang des perdants ne se convertit pas en cible fadable.**
   Trois quarts de leur perte est une dérive de friction — frais, funding,
   slippage — que la position inverse **paie aussi**.
2. **Quatre comptes sur cinq du décile inférieur sont hors d'état d'être fadés
   en T+1** : ruinés (50 % sous $10) ou inactifs (19 %).
3. **Le décile ne se reconduit pas** d'un trimestre au suivant — aucun compte
   apparié sur deux trimestres consécutifs.
4. La part directionnelle existe (**26,4 %**) mais reste **sous le seuil de
   40 %** fixé avant les chiffres, et sa stabilité n'a pas pu être mesurée.

Aucune étape 2 n'est ouverte. Aucun collecteur de positions live n'est
spécifié.
