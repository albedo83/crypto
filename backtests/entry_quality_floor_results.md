# Plancher de qualité à l'entrée — campagne complète (2026-07-30)

**Verdict : aucun ship. Trois hypothèses testées, trois réfutations en walk-forward.**

## Origine

Objection utilisateur : le backtest bookait les sorties par trail à des prix
indisponibles jusqu'au 2026-07-25 (v1.15.5) et c'est ce backtest-là qui a produit
le bot. Re-baseliner les chiffres ne re-valide pas les décisions.

La re-validation des signaux (`backtest_signal_rederivation.py`) a confirmé
S1/S8/S9/S10 (contribution 4/4) et isolé S5 comme hors phase : net négatif sur
les 4 fenêtres, dans les 2 directions, sur 537 trades.

En retirant S5 (`backtest_s5_removal_detail.py`), les 4 autres prennent **+153
trades et perdent $7215** (28m). D'où l'hypothèse testée ici : *le bot remplit ses
slots quoi qu'il arrive, et les candidats de bas de liste détruisent de la valeur.*

## Découverte structurelle

`entry_z` est une **constante par stratégie** (S1 6.5, S5 3.5, S8 7.0, S9 8.5,
S10 3.5 = `strat_z` des Params). Le tri `sort(key=(z, strength))` est donc un
**ordre de priorité entre stratégies**, puis par force décroissante à l'intérieur.

Conséquence : il n'existe **aucune dimension de qualité inter-signaux** dans le
classement. Un « plancher sur z » est mécaniquement impossible — z ne porte
aucune information intra-signal.

## Ce que l'EDA a montré (prémisses fortes)

### 1. L'effet de rang est un artefact de composition
Le rang 0 est le PIRE (net moyen +59 / −8 / +56 bps selon fenêtre), les rangs
suivants meilleurs. Explication : le rang 0 est composé à **48–51 % de S5**, le
seul signal perdant. Ce n'est pas un effet de qualité.

### 2. La force de S5 est INVERSEMENT liée à son P&L — consistant 3/3 fenêtres

| quartile de `strength` | Q1 (faible) | Q2 | Q3 | Q4 (forte) |
|---|---:|---:|---:|---:|
| net moyen 28m | **+187** | −33 | −21 | −44 |
| net moyen 12m | **+247** | −17 | −77 | −36 |
| net moyen 6m | **+148** | −40 | −37 | −186 |

Seul Q1 est positif, partout. Mécaniquement cohérent : S5 suit une divergence
sectorielle — une divergence énorme = mouvement déjà étendu = entrée tardive.

### 3. L'agitation du scan prédit le résultat — consistant 3/3 fenêtres

| candidats au scan | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| net moyen 28m | +35 | +99 | −38 | +185 | **+415** |
| net moyen 12m | +40 | +129 | −34 | +135 | **+695** |
| net moyen 6m | −20 | +110 | −246 | +60 | **+896** |

### 4. L'occupation du portefeuille n'a AUCUN effet consistant
Réfuté : les trades pris à 4 positions ouvertes font mieux (WR 70.6 %) que ceux
pris à portefeuille vide. Le mécanisme de blocage de S5 n'est pas la saturation.

## Ce que le walk-forward a répondu

Fenêtres OOS glissantes de 6 mois **non chevauchantes** (offsets 0/6/12/18),
critère strict 4/4 en P&L + DD non dégradé de plus de 2pp.

### A. Plafond sur la force de S5 — `backtest_s5_strength_cap.py`

| plafond | OOS-0 | OOS-6 | OOS-12 | OOS-18 | P&L + |
|---|---:|---:|---:|---:|---:|
| 2000 | +323 | −237 | +36 | −181 | 2/4 |
| 2500 | +181 | −117 | +144 | −51 | 2/4 |
| 3000 | +95 | −155 | +35 | +6 | 3/4 |
| 4000 | +23 | −20 | +70 | +64 | 3/4 |
| 5000 | +35 | +49 | +42 | +0 | 3/4 |

**REFUSÉ.** Aucun 4/4. Et les seuils les plus proches de ce que l'EDA suggérait
(2000–2500, ≈ frontière Q1) sont les **pires** (2/4). Les seuls seuils à 3/4
(5000–6000) ne mordent presque plus et dégradent le DD de 3pp sur OOS-0.

### B. Seuil d'agitation du scan — `backtest_scan_activity_gate.py`

| ≥ candidats | OOS-0 | OOS-6 | OOS-12 | OOS-18 | P&L + | trades restants |
|---|---:|---:|---:|---:|---:|---:|
| 2 | +120 | −550 | −124 | +292 | 2/4 | 77 % |
| 3 | +32 | −609 | −133 | −203 | 1/4 | 59 % |
| 4 | −170 | −531 | −60 | −219 | **0/4** | 43 % |
| 5 | +32 | −681 | +11 | −239 | 2/4 | 33 % |

**REFUSÉ nettement.** Destructeur de valeur à tous les seuils.

### C. Inverser l'ordre de force intra-stratégie

Pas un filtre — un arbitrage de slot (aucun trade coupé, seulement un candidat
différent quand plusieurs se disputent la place), donc peu de dépendance au chemin.

| fenêtre | force DESC (actuel) | force ASC | Δ$ |
|---|---:|---:|---:|
| OOS-0 | 800 | 626 | −173 |
| OOS-6 | 1387 | 1040 | −347 |
| OOS-12 | 818 | 788 | −30 |
| OOS-18 | 1036 | 738 | −298 |
| 12m | 2271 | 1333 | −937 |
| 28m | 13553 | 5456 | −8098 |

**REFUSÉ, 0/4 et 0/6.** L'ordre actuel est franchement supérieur.

Leçon méthodologique : le quartile de l'EDA mesure les trades qui **ont été
pris** ; le tri décide **lesquels sont pris**. Inverser le tri change la
population entière — ce ne sont pas la même question.

## Conclusion

Deux prémisses mécaniquement crédibles et statistiquement consistantes sur trois
fenêtres, plus un ré-ordonnancement à faible dépendance au chemin : les trois
s'évaporent hors échantillon. C'est la signature d'un **plateau** du côté entrée,
pas d'une inefficacité corrigeable.

Le bot n'a pas de dimension de qualité inter-signaux, et la campagne montre qu'en
ajouter une n'aide pas. Le S5 reste hors phase sans qu'aucun traitement testé ne
le répare : ni retrait (2/4), ni retrait par direction (1/4 et 2/4), ni plafond
de force (3/4 au mieux).

## Acquis conservés

Instrumentation permanente du moteur de backtest, opt-in et sans effet par
défaut :
- `entry_rank_all`, `entry_rank_taken`, `entry_z`, `entry_strength`,
  `n_cands_at_open` sur chaque trade ;
- `min_scan_candidates` (0 = no-op) ;
- `strength_sort` (`"desc"` = comportement historique) ;
- correctif de parité : `enabled_strategies` est désormais honoré par le
  backtest comme il l'était par le bot.

Cette classe de question est maintenant bon marché à re-poser.
