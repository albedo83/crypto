# Conditionnement à la dispersion cross-sectionnelle

> **Diagnostic pur.** Aucun paramètre du moteur n'est touché, aucune règle n'est
> proposée.
>
> **Statut de méta-fit, dit en toutes lettres.** Cette étude a un second usage :
> désigner la variable de conception de la Mission 2. Le méta-fit effectivement
> contracté est **négatif et non paramétrique** — l'étude *élimine* la
> dispersion, elle ne choisit ni seuil ni horizon. C'est la forme la moins
> coûteuse de méta-fit, mais ce n'est pas zéro, et toute règle issue de M2
> devra passer le walk-forward comme n'importe quelle autre.

**Généré le** : 2026-08-01T16:25Z
**Empreinte** : config `d7a415020619` · git `c7dd5a2+dirty` · données jusqu'au
2026-08-01T16:00, 36 symboles, fichiers du 2026-08-01T16:10Z
**Base** : 1 308 trades, fenêtre 2024-04-01 → 2026-08-01, config **en service**,
booking réaliste des trails, parité secteurs
**Couverture** : **0 trade sans dispersion d'entrée, 0 sans `n_cands_at_open`**
**Plancher d'effectif** : 20 trades par cellule ; sous ce seuil, **NON ÉMISE**
**Source** : `backtests/dispersion_conditioning.py` ·
`analysis/output/dispersion_conditioning.json`

> *Le capital final de ce run est $13 292 contre $12 872 ce matin : les fichiers
> de données ont été rafraîchis entre-temps (12:10Z → 16:10Z). Phénomène déjà
> documenté au § 5 de `docs/dd_anatomy.md` — un trade d'écart, 28 mois de
> compounding. Sans effet sur une étude qui porte sur des moyennes par trade.*

## 1. Grille de lecture — ÉCRITE AVANT LES CHIFFRES

> - relation net/tercile **MONOTONE et de même sens sur ≥ 4 semestres sur 5**
>   ⇒ la dispersion est une jauge ⇒ entrée de conception valide pour M2
> - relation **plate ou instable** entre semestres ⇒ pas une jauge ⇒ consigné,
>   M2 se conçoit sur la seule agitation
> - corrélation dispersion/agitation **> 0,7** ⇒ une seule variable en M2
>   (l'agitation, déjà par-trade) ; **< 0,7** ⇒ deux variables CANDIDATES mais
>   M2 n'en prendra qu'UNE — le choix se fait sur cette étude, pas en testant
>   les deux en backtest
> - INVARIANT : cas non prévu nommé comme tel, clauses de repli conservatrices,
>   aucune conclusion opportuniste.

## 2. Mesure 1 et 2 — net, WR et effectif par tercile

### disp_24h — bornes T1 ≤ 238 < T2 ≤ 330 < T3

*(étendue observée : 95 à 1 439, médiane 280)*

| tercile | n | net moyen | ± erreur-type | WR | P&L |
|---|---:|---:|---:|---:|---:|
| T1 (faible) | 441 | +79,3 bps | 43,7 | 49,7 % | +$9 012 |
| T2 | 433 | +106,7 bps | 39,6 | 50,3 % | +$3 344 |
| T3 (forte) | 434 | +60,8 bps | 43,6 | 50,9 % | −$63 |

**Non monotone.** Les effectifs sont équilibrés — aucun tercile n'a l'air bon
faute d'y trader. Les erreurs-types (~40 bps) dépassent les écarts entre
terciles (~45 bps).

Corrélation continue : Pearson **+0,0057**, Spearman **−0,0151** sur 1 308
trades. Nulle, et les deux ne s'accordent même pas sur le signe.

### disp_7d — bornes T1 ≤ 639 < T2 ≤ 885 < T3

| tercile | n | net moyen | ± erreur-type | WR | P&L |
|---|---:|---:|---:|---:|---:|
| T1 (faible) | 437 | +159,3 bps | 47,3 | 52,4 % | +$9 395 |
| T2 | 438 | +33,4 bps | 35,3 | 49,3 % | +$114 |
| T3 (forte) | 433 | +53,8 bps | 43,4 | 49,2 % | +$2 782 |

**Non monotone** également. Pearson **−0,0278**, Spearman **−0,0308**.

### Stabilité temporelle — la clause décisive

Monotonie du net moyen sur les trois terciles, semestre par semestre :

| semestre | n | **disp_24h** (T1 / T2 / T3) | mono | **disp_7d** (T1 / T2 / T3) | mono |
|---|---:|---|---:|---|---:|
| 2024-S1 | 137 | −108,2 / +193,6 / +135,2 | 0 | +305,5 / +123,3 / +38,6 | **−1** |
| 2024-S2 | 292 | −71,2 / +94,9 / +98,1 | **+1** | +222,1 / −92,5 / +110,0 | 0 |
| 2025-S1 | 272 | +69,8 / +175,4 / +27,7 | 0 | +151,3 / +50,7 / +69,1 | 0 |
| 2025-S2 | 255 | +127,8 / +16,8 / +86,9 | 0 | +134,6 / +159,7 / −9,0 | 0 |
| 2026-S1 | 299 | +159,3 / +47,2 / −2,9 | **−1** | +147,8 / +14,8 / +37,1 | 0 |
| 2026-S2 | 53 | −12,1 / +89,8 / NON ÉMISE | 0 | +39,0 / −62,6 / NON ÉMISE | 0 |

**disp_24h : 2 semestres monotones sur 5 complets — et ils sont de sens
OPPOSÉS** (+1 en 2024-S2, −1 en 2026-S1).
**disp_7d : 1 sur 5.**

La clause exigeait ≥ 4 sur 5, de même sens. On en est très loin.

> **Ce que ce tableau reproduit.** En 2026-S1, disp_24h décroît proprement
> (+159 / +47 / −3) : c'est exactement le motif sur lequel le gate de
> dispersion v11.7.28 avait été construit, puis retiré en v12.8.0. En 2024-S2,
> le même motif est **inversé** (−71 / +95 / +98). L'étude retrouve donc, sur la
> base corrigée, l'instabilité qui a déjà fait échouer ce gate deux fois
> (`project_disp7d_gate_2026_05` : EDA 6 mois parfaite, walk-forward 28 mois en
> échec massif).

### Par signal

| signal | **disp_24h** T1 / T2 / T3 | mono | **disp_7d** T1 / T2 / T3 | mono |
|---|---|---:|---|---:|
| S1 | — / — / +608,7 (36) | 0 | — / — / +612,8 (45) | 0 |
| S5 | +25,5 / +15,5 / **−83,2** | **−1** | +105,3 / −22,5 / **−71,6** | **−1** |
| S8 | +209,3 / +184,8 / +343,4 | 0 | +212,7 / +182,9 / +322,2 | 0 |
| S9 | — / — / +112,1 (111) | 0 | +611,0 / +272,6 / −7,5 | **−1** |
| S10 | +12,6 / +79,6 / +11,4 | 0 | −3,2 / +44,2 / +121,3 | **+1** |

S1 et S9 ne peuplent qu'un tercile (leurs setups n'existent qu'en dispersion
élevée) : leurs cellules T1/T2 sont **NON ÉMISES**, pas nulles.

### Fenêtres de creux vs référence

| | n | T1 | T2 | T3 |
|---|---:|---:|---:|---:|
| **disp_24h** creux | 181 | −130,1 (52) | −129,6 (68) | −152,3 (61) |
| **disp_24h** référence | 1 127 | +107,3 (389) | +150,7 (365) | +95,7 (373) |
| **disp_7d** creux | 181 | −40,7 (50) | −82,3 (75) | −297,5 (56) |
| **disp_7d** référence | 1 127 | +185,2 (387) | +57,3 (363) | +106,0 (377) |

Dans les creux, **les trois terciles perdent**. La dispersion ne sépare pas les
trades perdants des autres là où ça compterait : sur disp_24h, l'écart entre le
meilleur et le pire tercile y vaut 23 bps, contre 152 bps de perte moyenne.

## 3. Mesure 3 — dispersion et agitation sont-elles la même information ?

`n_cands_at_open` et la dispersion sont deux propriétés du **scan**, pas du
trade : les compter par trade sur-pondère les scans ayant ouvert plusieurs
positions. La mesure principale est donc **par scan** (1 017 scans distincts
pour 1 308 trades) ; la version par trade est donnée en regard.

| | par scan (Pearson) | par scan (Spearman) | par trade (Pearson) |
|---|---:|---:|---:|
| disp_24h ↔ n_cands | **+0,3011** | +0,2929 | +0,2783 |
| disp_7d ↔ n_cands | **+0,3211** | +0,2305 | +0,2686 |

**Très en dessous de 0,70.** Ce sont bien **deux informations distinctes** — la
dispersion en explique moins de 10 % de la variance.

Par régime :

| | creux | référence |
|---|---:|---:|
| disp_24h ↔ n_cands | +0,0967 (142 scans) | +0,3112 (875 scans) |
| disp_7d ↔ n_cands | +0,0932 (142 scans) | +0,3340 (875 scans) |

**Dans les creux, le lien s'effondre à ~0,09** : les deux variables y sont
quasiment indépendantes. Le peu de recouvrement qu'elles ont en temps normal
disparaît précisément dans le régime qui pose problème.

*Restriction de population, déclarée* : `n_cands_at_open` n'est observable que
sur les scans ayant produit au moins une entrée. Les scans à candidats mais sans
ouverture (slots pleins, cooldown) sont hors échantillon. C'est aussi la
population dans laquelle la variable serait effectivement lue par une règle
d'entrée — la restriction est donc alignée sur l'usage, mais elle est réelle.

## 4. Application de la grille

### Clause 1 — la dispersion n'est PAS une jauge

Relation non monotone en agrégé sur les deux variables ; 2 semestres monotones
sur 5 pour disp_24h, **de sens opposés** ; 1 sur 5 pour disp_7d. Corrélations
continues nulles et de signe discordant entre Pearson et Spearman.

**Verdict : relation plate et instable.** La clause de repli s'applique :
**consigné, et M2 se conçoit sur la seule agitation.**

### Clause 3 — deux variables distinctes, une seule retenue

Corrélation de +0,30 à +0,32 par scan, très en deçà du seuil de 0,70. Deux
informations, donc deux variables candidates — et le choix devait se faire ici,
pas en backtest.

**Il est fait, et il est contraint** : la clause 1 ayant éliminé la dispersion,
il ne reste qu'un candidat.

### ⇒ Variable désignée pour la Mission 2 : `n_cands_at_open`

### Sous-motif nommé, et sur lequel rien n'est bâti

**S5 est le seul signal monotone décroissant sur les deux variables de
dispersion** (disp_24h : +25,5 / +15,5 / −83,2 ; disp_7d : +105,3 / −22,5 /
−71,6). C'est cohérent avec la mécanique du signal — S5 fade une divergence
sectorielle, et une dispersion élevée signale une distribution transversale déjà
disloquée.

**Aucune conclusion n'en est tirée**, conformément à l'invariant de grille :

1. la grille ne prévoyait pas de verdict par signal, et la stabilité par
   semestre — la seule chose qui aurait donné du poids à ce motif — n'a pas été
   mesurée à ce niveau de découpage ;
2. cette hypothèse précise a **déjà** été testée et rejetée deux fois en
   walk-forward (`backtest_disp7d_gate.py` 2/7, `backtest_disp7d_haircut.py`
   0/7) ;
3. le gate de dispersion construit sur ce motif a été déployé puis **retiré**
   (v11.7.28 → v12.8.0).

Le motif est consigné parce qu'il est réel. Il n'est pas re-testé.

### Écart de grille — nommé

La clause 1 demandait « ≥ 4 semestres sur 5 ». La fenêtre de 28 mois en produit
**six**, dont un tronçon final d'un mois (2026-S2, n = 53, une cellule non
émise). Le décompte a été fait sur les **5 semestres complets**, 2026-S2 étant
structurellement incapable d'être monotone avec une cellule manquante.

Le verdict est identique dans les deux lectures — 2/5 ou 2/6, de sens opposés,
n'atteint pas 4. L'écart est signalé parce qu'il relève de la même famille que
les trois précédents : une grille dont le vocabulaire ne colle pas exactement à
ce que les données produisent.

## 5. Ce que l'étude établit

1. **La dispersion cross-sectionnelle à l'entrée ne prédit pas le net d'Alfred**
   — ni en agrégé (|ρ| < 0,03 sur 1 308 trades), ni de façon stable dans le
   temps (2 semestres monotones sur 5, de sens opposés).
2. **Elle ne discrimine pas non plus à l'intérieur des creux** : les trois
   terciles y perdent, l'écart entre eux valant 15 % de la perte moyenne.
3. **Dispersion et agitation du scan sont deux informations distinctes**
   (ρ ≈ 0,30), et leur faible recouvrement **s'effondre à 0,09 dans les creux**.
4. **La variable de conception de la Mission 2 est `n_cands_at_open`**, par
   élimination et non par sélection du meilleur candidat.
5. Le seul motif propre est **S5 décroissant en dispersion** — déjà réfuté deux
   fois en walk-forward, consigné sans être re-testé.
