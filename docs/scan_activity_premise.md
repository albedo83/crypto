# Phase A0 — la prémisse « agitation du scan » sur base propre

> **Diagnostic pur.** Aucun modulateur n'est simulé, aucun paramètre n'est
> ajusté. Ce document décide seulement si la Mission 2 a une prémisse.

**Généré le** : 2026-08-01T16:31Z
**Empreinte** : config `d7a415020619` · git `8c0de59` (arbre propre) · données
jusqu'au 2026-08-01T16:00, 36 symboles, fichiers du 2026-08-01T16:10Z
**Plancher d'effectif** : 20 trades par cellule ; sous ce seuil, **NON ÉMISE**
**Source** : `backtests/scan_activity_premise.py` ·
`analysis/output/scan_activity_premise.json`

## 1. Ce qui devait être re-vérifié

La prémisse publiée en juillet 2026 (`backtests/backtest_scan_activity_gate.py`) :

| candidats au scan | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| net moyen 28 m | +35 | +99 | −38 | +185 | **+415** |
| net moyen 12 m | +40 | +129 | −34 | +135 | **+695** |
| net moyen 6 m | −20 | +110 | −246 | +60 | **+896** |

**Sens de l'effet : le net croît avec le nombre de candidats.**

Ces chiffres portaient deux défauts, tous deux corrigés depuis :

1. **carte sectorielle périmée** (avant v1.17.1) — la population de trades était
   amputée de 8 tokens qui ne pouvaient émettre aucun signal S5 ;
2. **fenêtres emboîtées** — 6 m ⊂ 12 m ⊂ 28 m. Les « trois fenêtres » n'en
   faisaient qu'une, ce qui a déjà produit une fausse confirmation cette
   semaine.

## 2. Test de signe — FIXÉ AVANT EXÉCUTION

Committé en `8c0de59`, avant que le script ne tourne. Une fenêtre présente un
effet de même sens que juillet si **les deux** mesures sont strictement
positives :

1. **Spearman(n_cands, net)** sur les trades de la fenêtre — continu, insensible
   au découpage en buckets ;
2. **net moyen(≥5) − net moyen(1)** — la comparaison extrême, celle sur
   laquelle la prémisse de juillet avait été lue.

Exiger les deux empêche qu'un artefact de bucketisation ou une queue isolée
porte seul le verdict.

**Seuil de la grille : ≥ 3 fenêtres sur 4.**

## 3. Résultat — fenêtres de doctrine, exécutées indépendamment

| fenêtre | n | bucket 1 | bucket 2 | bucket 3-4 | **bucket ≥5** | ρ | Δ(≥5−1) | |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| OOS-0 · 2026-02→2026-08 | 313 | −2 (154) | +10 (72) | −38 (56) | **+597 (31)** | +0,054 | **+600** | ✓ |
| OOS-6 · 2025-08→2026-02 | 255 | +163 (128) | +11 (60) | +70 (47) | **+285 (20)** | +0,017 | **+122** | ✓ |
| OOS-12 · 2025-02→2025-08 | 276 | −52 (128) | +141 (55) | +96 (38) | **+262 (55)** | +0,119 | **+315** | ✓ |
| OOS-18 · 2024-08→2025-02 | 281 | +37 (98) | −253 (53) | −73 (43) | **+349 (87)** | +0,061 | **+312** | ✓ |
| **28 mois (référence)** | 1308 | +33 (593) | +16 (281) | +20 (220) | **+369 (214)** | +0,065 | **+336** | ✓ |

### ⇒ Effet de même sens sur **4 fenêtres sur 4**. Prémisse **CONFIRMÉE**.

L'effet survit à la correction de la parité sectorielle **et** au passage en
fenêtres glissantes non chevauchantes. Ce n'était pas acquis : c'est
exactement le test qui a fait tomber la prémisse du plafond de force S5.

## 4. Trois faits qui accompagnent la confirmation

### 4.1 C'est une MARCHE, pas une pente

Sur 28 mois, les buckets 1, 2 et 3-4 sont indiscernables : **+33 / +16 / +20 bps**.
Tout l'effet est porté par le seul bucket ≥5, à **+369 bps**.

Le même motif se retrouve dans chaque fenêtre : le bucket 2 est le pire d'OOS-18
(−253) et le meilleur d'OOS-12 (+141) — du bruit. Le bucket ≥5 est positif
partout.

Ce fait est consigné parce qu'il est **en tension avec la forme du modulateur
imposée par la Mission 2** (rampe linéaire de 1 à 5). Il ne la modifie pas : la
forme est figée par l'énoncé et le rester est le seul moyen que la grille
conserve sa valeur.

### 4.2 Le « 4/4 » porte sur le SIGNE, pas sur la significativité

| fenêtre | cellule ≥5 | net moyen | erreur-type | t |
|---|---:|---:|---:|---:|
| OOS-0 | n = 31 | +597 | 351 | **1,7** |
| OOS-6 | n = 20 | +285 | 206 | **1,4** |
| OOS-12 | n = 55 | +262 | 136 | **1,9** |
| OOS-18 | n = 87 | +349 | 133 | **2,6** |
| 28 mois | n = 214 | +369 | 86 | **4,3** |

Une seule fenêtre dépasse t = 2. Le test déclaré était un test de **signe**, et
il passe — mais « 4/4 » ne doit pas se lire comme quatre résultats
significatifs. C'est la fenêtre longue, à t = 4,3 sur 214 trades, qui porte la
puissance statistique.

Sur 28 mois, le bucket ≥5 représente 16,4 % des trades et **+$9 655 de P&L** —
soit environ 79 % du résultat total de la fenêtre.

### 4.3 Le régime agité est presque absent des creux

| fenêtre | bucket 1 | bucket 2 | bucket 3-4 | bucket ≥5 | part ≥5 |
|---|---:|---:|---:|---:|---:|
| OOS-0 | 154 | 72 | 56 | 31 | 9,9 % |
| OOS-6 | 128 | 60 | 47 | 20 | 7,8 % |
| OOS-12 | 128 | 55 | 38 | 55 | 19,9 % |
| OOS-18 | 98 | 53 | 43 | 87 | 31,0 % |
| 28 mois | 593 | 281 | 220 | 214 | 16,4 % |
| **creux A** | 68 | 35 | 29 | **11** | 7,7 % |
| **creux B** | 15 | 18 | 7 | **0** | **0,0 %** |
| **creux C** | 24 | 10 | 9 | 11 | 20,4 % |
| **creux D** | 20 | 5 | 9 | 4 | 10,5 % |

Dans les creux, la cellule ≥5 est **NON ÉMISE partout** (0 à 11 trades) et le
test de signe y échoue ou s'inverse (ρ = −0,159 / +0,217 / −0,218 / −0,043).

Contre-intuitivement, **les drawdowns d'Alfred ne sont pas des périodes
agitées** : la fenêtre B n'y compte aucun scan à 5 candidats ou plus. Un
modulateur d'agitation y serait donc presque inerte — ce qui rejoint la
consigne de l'énoncé selon laquelle le comportement dans les creux est rapporté
mais ne participe pas au verdict.

La part du régime agité varie par ailleurs de **7,8 % à 31,0 %** selon la
fenêtre : la morsure du modulateur dépendra fortement du régime.

## 5. Cas non prévu — nommé

La grille de l'amendement opposait « effet de même sens » et « effet absent ou
inversé ». Le résultat est de la première catégorie **sans réserve sur le
signe**, mais il s'en écarte sur deux points que la grille ne nommait pas : la
forme est une marche et non une pente (§ 4.1), et la confirmation est portée par
une seule cellule sur quatre buckets, à faible puissance par fenêtre (§ 4.2).

Conformément à l'invariant, ces deux faits sont **décrits et ne fondent aucune
conclusion opportuniste** : ils ne servent ni à modifier la forme du modulateur,
ni à restreindre son domaine, ni à anticiper le verdict de la Phase B.

## 6. Conséquence

La prémisse est confirmée sur base propre. **La Phase A est rédigée**
(`docs/scan_sizing_verdict.md`), avec des points d'ancrage pris sur les bornes
de buckets de cette étude.
