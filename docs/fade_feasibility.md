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

## 9. Résultat

> *À compléter après exécution. Vide à ce jour.*
