# Rapport — Enquête sur les fondations du moteur

**2026-07-30** · Alfred v1.16.5 · SENIOR (live, argent réel)

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

## 7. La décision à prendre

Trois faits établis, en backtest **et** en live :

1. S5 a gagné pendant quatre semestres, perdu sur les deux derniers
2. La dégradation est **continue** (50 % → 41 % → 33 %), pas un accident isolé
3. Le live la confirme indépendamment, sur argent réel

Aucun backtest ne tranchera : le walk-forward ne sait pas distinguer une rupture de
régime d'un sur-ajustement.

**Recommandation : `signal_mult["S5"] = 1.5`** — présenté pour ce que c'est, une
décision de risque prise sur une dégradation observée, pas une amélioration d'edge
validée.

Les alternatives défendables :
- **ne rien faire** — la position par défaut de la doctrine ; on attend que S5 se
  répare ou que la dégradation devienne indiscutable
- **1.0** — dé-risquage plus franc, drawdown bien meilleur (jusqu'à −7 points), coût
  de −11 % sur la période saine

> **Aucun changement de trading n'a été appliqué.** Le bot tourne exactement comme
> avant. Tout ce qui a été livré aujourd'hui ne touche que le backtest, la
> journalisation et la documentation.

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
