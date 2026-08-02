# Persistance de performance des wallets Hyperliquid — Phase 0 : faisabilité

> **Phase 0 = faisabilité des données, avant toute conception.** Ce document
> n'évalue aucune performance, ne classe aucun wallet et ne mesure aucune
> persistance. Il répond à quatre questions et s'arrête.
>
> **⛔ STOP après ce document. GO explicite de Seb requis pour la Phase 1.**

**Étude menée le** : 2026-08-02 · **empreinte** : git `c37a5ba+dirty` ·
run 2026-08-02T15:04Z
**Discipline** : lectures publiques uniquement — classement public
`stats-data.hyperliquid.xyz` et endpoint `info` d'Hyperliquid. **Aucune clé,
aucun ordre, aucune donnée privée.** Les adresses sont publiques par
construction.
**Sources** : `backtests/wallets/phase0_feasibility.py` ·
`analysis/output/wallet_phase0.json`

---

## 1. Le panel — constituable, et bien au-delà de 200

| | |
|---|---|
| classement public | **40 960 adresses** |
| accountValue max | $13 578 752 164 |
| accountValue médian | $662 |
| panel échantillonné | **250 adresses**, 250/250 exploitables |

**L'échantillonnage se fait à pas régulier dans le classement par TAILLE de
compte, pas par performance.** Ce choix est délibéré : prélever le sommet du
classement reviendrait à sélectionner sur la performance passée, c'est-à-dire à
introduire d'entrée le biais que la Phase 1 doit précisément éviter.

## 2. Profondeur récupérable — **GATE FRANCHI**

| percentile | span |
|---|---:|
| p0 | 18 j |
| p10 | 81 j |
| p25 | 270 j |
| **p50** | **494 j** |
| p75 | 620 j |
| p90 | 921 j |
| p100 | **1 166 j** |

**Médiane 487 jours (≈ 16 mois) ≥ 365 jours ⇒ GO.**

| seuil | adresses qualifiées |
|---|---|
| ≥ 180 j (2 trimestres) | **210/250 — 84,0 %** |
| ≥ 270 j (3 trimestres) | 182/250 — 72,8 % |
| ≥ 365 j (4 trimestres) | **160/250 — 64,0 %** |
| ≥ 540 j (6 trimestres) | 111/250 — 44,4 % |
| ≥ 730 j (8 trimestres) | 47/250 — 18,8 % |

Trimestres complets par adresse : **médiane 5,3**, p25 3,0, p75 6,8.

**Il n'y a pas de mur d'API.** La date de début la plus ancienne du panel est le
**2023-05-24**, et les spans s'étalent continûment jusqu'à 1 166 jours. Un
premier sondage sur 10 adresses avait suggéré un plafond vers 420 jours : c'était
un artefact d'échantillon — ces dix comptes avaient simplement été ouverts après
mi-2025.

## 3. Granularité — hebdomadaire

| | |
|---|---|
| points par adresse | min 32 · **médiane 72** · max 117 |
| pas médian | **7,00 jours** |

Le nombre de points est borné, donc **le pas s'élargit avec l'âge du compte**.
Un trimestre représente environ 13 points pour un compte récent, moins pour un
compte ancien. C'est une contrainte de résolution à porter dans la grille de
Phase 1, pas un obstacle.

## 4. Les fills historiques ne sont PAS servis

| endpoint | résultat |
|---|---|
| `userFills` | renvoie les ~2 000 derniers fills — soit **51 minutes** d'historique sur un compte actif |
| `userFillsByTime` | **n = 0 sur quatre fenêtres testées** (2025-09, 2026-01, 2026-05, 2026-07), sur l'adresse du panel à l'historique le plus profond |

**Conséquence sur l'espace de conception.** La mission envisageait
« userFills / positions par adresse ». Cette route est **fermée** pour une
adresse tierce : il ne reste que la série `portfolio`. La Phase 1 ne pourra donc
mesurer qu'un **rendement par période**, sans détail au trade, sans attribution
par token, sans horodatage d'entrée ou de sortie.

Ce fait est décrit ; il ne fonde aucune conclusion sur l'intérêt de la piste.

## 5. La bonne série est `pnlHistory`, pas `accountValueHistory`

Sur 12 adresses tirées au hasard : **12/12 disposent d'un `pnlHistory` non
constant**, aligné point à point sur `accountValueHistory`.

La distinction n'est pas cosmétique — `accountValue` bouge avec les **dépôts et
retraits**, `pnlHistory` est le P&L de trading. Mesurer une performance sur la
première confondrait un virement avec un gain. C'est exactement le piège relevé
sur HLP en Phase 0 du projet market making, où la valeur de compte a chuté de
$264 M à $222 M sur un mois **positif** en P&L.

## 6. Survivorship — le risque est réel mais faible, et mesuré

Une étude de persistance est ruinée si les perdants disparaissent du panel. Ce
n'est pas le cas ici :

| | |
|---|---|
| comptes à moins de $10 | **15 129 — 36,9 %** du classement |
| comptes à moins de $1 | 11 980 |
| p25 de l'accountValue | **$0,42** |
| sur 12 adresses tirées au hasard | **8 sont en P&L cumulé négatif** (jusqu'à −$548 562) |

Le classement public **conserve les comptes vidés et les comptes ruinés**. Il
n'est pas une liste de survivants.

**Risque résiduel, nommé sans être quantifié** : un compte entièrement fermé et
retiré du registre pourrait ne plus figurer au classement. Rien dans les données
publiques ne permet de mesurer cette population — c'est une réserve à porter au
rapport de Phase 1, pas un obstacle à la lancer.

## 7. Débit

| | |
|---|---|
| 250 appels `portfolio` | **109 s**, soit 2,3/s avec une pause volontaire de 0,12 s |
| latence | médiane 248 ms · p90 459 ms |
| erreurs 429 / 503 | **aucune** |
| extrapolation | un panel de 1 000 adresses ≈ **7 minutes** |

Aucune contrainte de débit ne s'oppose à la Phase 1.

## 8. Contrôle hérité — colonne temporelle

Le contrôle inscrit dans le cadre commun (min == max == date du run ⇒ mesure
fausse, incident `startTime=0` du projet basis) :

| colonne | valeurs distinctes | plage |
|---|---:|---|
| `first` | **114** | 2023-05-24 → 2026-07-15 |
| `last` | 1 | 2026-08-02 → 2026-08-02 |

**`first` n'est pas dégénérée** — c'est la colonne qui aurait trahi une API
renvoyant systématiquement la date du jour.

`last` vaut uniformément la date du run, et **c'est attendu** : toutes les séries
`portfolio` se terminent à l'instant de la requête. Ce n'est pas le mode de
défaillance visé par le contrôle, qui portait sur une **origine** faussement
récente. La distinction est écrite ici pour qu'un relecteur ne confonde pas les
deux.

## 9. Cas non prévu par la mission — nommé

La mission demandait « userFills / positions par adresse via API info ». Le § 4
établit que **les fills historiques tiers ne sont pas servis** — ni par
`userFills` (51 minutes de profondeur sur un compte actif), ni par
`userFillsByTime` (vide sur quatre fenêtres, y compris sur l'adresse la plus
ancienne du panel).

La Phase 1, si elle est lancée, portera donc sur des **rendements par période
issus de `pnlHistory`**, à granularité hebdomadaire, et pas sur le comportement
de trading. Conformément à l'invariant, ce fait est décrit et ne fonde aucune
conclusion opportuniste — ni sur l'intérêt de la piste, ni sur son abandon.

## 10. Verdict de faisabilité

| question | réponse |
|---|---|
| un panel de 200+ adresses est-il constituable ? | **oui** — 40 960 disponibles, 250 échantillonnées, 250 exploitables |
| la profondeur atteint-elle 12 mois ? | **oui** — médiane **487 j**, 64 % des adresses ≥ 365 j |
| les fills historiques sont-ils accessibles ? | **non** — seule la série `portfolio` l'est |
| la bonne série de performance existe-t-elle ? | **oui** — `pnlHistory`, non constante sur 12/12 |
| le panel est-il biaisé par la survie ? | **peu** — 36,9 % des comptes sont sous $10, les perdants sont présents |
| le débit est-il un obstacle ? | **non** — 250 adresses en 109 s, zéro erreur |

### **GATE : GO.**

Ce verdict porte uniquement sur la **disponibilité des données**. Rien dans ce
document ne dit que la Phase 1 trouvera une persistance, ni qu'elle n'en
trouvera pas.

**⛔ STOP.**

---

# Phase 1 — GRILLE PRÉ-ENREGISTRÉE

> **⚠ ÉCRITE ET COMMITTÉE AVANT TOUT CHIFFRE.** Aucune performance n'a été
> calculée, aucun wallet classé, aucune corrélation mesurée au moment où ces
> lignes sont écrites. L'historique git en fait foi.
>
> **Rédigé le** : 2026-08-02 · les trois amendements de Seb sont intégrés.

## P1.0 Principe directeur — anti-lookahead strict

**La sélection d'un wallet sur une période ne peut être évaluée que sur la
période SUIVANTE.** Panels roulants : sélection sur le trimestre T, mesure sur
T+1. Jamais de sélection sur l'historique complet.

Chaque clause quantifie sur des objets **décidables ex-ante** — c'est la leçon
directe du projet basis, où une clause franchie 32 fois sur 32 s'est révélée
n'être qu'une sélection ex-post.

## P1.1 Métrique de classement — amendement 1

> Le P&L brut en dollars classe la **taille**, pas le talent : un gros compte
> médiocre bat un petit compte brillant.

```
métrique(compte, T) = pnl(T) / médiane( accountValue observée dans T )
```

- `pnl(T)` = `pnlHistory` cumulé au dernier point de T **moins** le cumulé au
  dernier point précédant T. La série est **cumulative depuis l'origine** —
  vérifié sur échantillon.
- Dénominateur = **médiane**, pas moyenne : robuste aux dépôts ponctuels.
- **Le premier point de chaque série est un remplissage synthétique**
  (`accountValue = 0`, `pnl = 0`) et est écarté. Le conserver tirerait la
  médiane vers le bas.

**Biais résiduel, nommé au rapport** : les flux de capital polluent le
dénominateur. Un compte qui double sa mise en milieu de trimestre voit sa
métrique diluée sans que son talent change. Aucune correction n'est possible à
cette granularité — `pnlHistory` ne distingue pas un gain d'un virement dans la
valeur de compte, et les mouvements de dépôt ne sont pas exposés par l'API
publique.

## P1.2 Éligibilité — amendement 2, décidable ex-ante par panel roulant

Un compte entre dans le panel du trimestre **T** si, et seulement si :

| condition | évaluée sur |
|---|---|
| valeur de compte **au début de T** ≥ **$500** | le premier point de T |
| **≥ 1 semaine de P&L non nul** dans T | les points de T |

**INTERDICTIONS** — ce sont elles qui font tenir la grille :

1. **Interdit de filtrer sur la valeur ACTUELLE.** Écarter « les comptes sous
   $10 aujourd'hui » sélectionnerait sur le futur : c'est le vice exact de la
   clause basis, version wallets.
2. **Interdit de filtrer sur le P&L cumulé à ce jour.**
3. **Interdit d'exiger la présence dans le panel de T+1.** L'appartenance se
   décide **au seul trimestre T** ; la métrique de T+1 est ensuite calculée
   pour **tout** compte du panel T disposant de données en T+1, **quelle que
   soit sa valeur ou son activité en T+1** — y compris s'il a été ruiné.
   Exiger la survie en T+1 exclurait mécaniquement les comptes qui explosent,
   et gonflerait la persistance du côté des perdants.

Les comptes du panel T sans aucune donnée en T+1 sont comptés comme
**attrition** et publiés comme tels.

## P1.3 Stratification par taille — amendement 3, ex-ante

Trois strates, définies par la **valeur de compte au début de T**, en
**terciles du panel T lui-même** : petit / moyen / gros.

Les terciles sont préférés à des seuils en dollars parce qu'ils n'introduisent
aucune constante inventée et s'adaptent au panel de chaque trimestre — tout en
n'utilisant que de l'information disponible **au début de T**.

Effectifs affichés par strate. Persistance mesurée **par strate ET en agrégé** —
pour que le verdict ne soit pas celui des seuls gros comptes.

**Cellule de strate sous-dotée (< 30 comptes appariés) : NON ÉMISE.**

## P1.4 Mesure

Pour chaque paire de trimestres adjacents (T, T+1), pour chaque strate, et pour
chacun des trois groupes :

| groupe | définition |
|---|---|
| **tous** | tous les comptes du panel T ayant une métrique en T+1 |
| **gagnants** | ceux dont la métrique en T est **> 0** |
| **perdants** | ceux dont la métrique en T est **< 0** |

Statistique : **autocorrélation de rang de Spearman** entre la métrique en T et
la métrique en T+1, au sein du groupe.

C'est le test du spectre de persistance (`docs/persistence_spectrum.md`)
appliqué aux traders au lieu des tokens.

## P1.5 Statistique de verdict — mise en commun, pour maîtriser la multiplicité

Le verdict ne se lit **pas** sur une paire de trimestres isolée : avec
3 groupes × 4 strates (3 + agrégat) = **12 cellules**, et une dizaine de paires,
un |t| ≥ 2 isolé est attendu par pur hasard.

Pour chaque cellule (groupe × strate) :

```
rho_poolé = moyenne des rho sur toutes les paires de trimestres
SE        = écart-type des rho / racine(nombre de paires)
t         = rho_poolé / SE
```

Les rho par paire sont **publiés** pour que la dispersion soit lisible.

## P1.6 CLAUSE DE VERDICT

> **|t| < 2 dans TOUTES les cellules ⇒ ni copier ni fader n'a de base ⇒
> BRANCHE CLOSE.**
>
> **|t| ≥ 2 dans au moins une cellule ⇒ phase de conception** (mission
> distincte, sa propre grille), le rapport nommant précisément la ou les
> cellules concernées.

### Cas ambigus, tranchés d'avance

| cas | verdict |
|---|---|
| |t| exactement 2,00 | **compte comme significatif** — la clause dit « < 2 » pour clore |
| une seule cellule sur 12 franchit le seuil | **la clause littérale s'applique** : phase de conception. Mais le rapport publie le **nombre de cellules à \|t\| ≥ 2** face à l'**espérance sous l'hypothèse nulle (12 × 0,05 ≈ 0,6)**, pour que la multiplicité soit visible sans qu'on déplace le poteau |
| rho poolé significatif mais de **signe négatif** | **compte comme significatif** — une anti-persistance est une base pour fader, ce que la clause envisage explicitement |
| moins de 3 paires de trimestres dans une cellule | **NON ÉMISE** — SE non estimable |
| moins de 30 comptes appariés dans une cellule | **NON ÉMISE** (§ P1.3) |
| aucune cellule émise | **run NUL**, pas de verdict |

## P1.7 Panel et exécution

| | |
|---|---|
| taille du panel | **1 000 adresses**, échantillonnées à pas régulier dans le classement par **taille de compte** — jamais par performance |
| trimestres | trimestres civils UTC, tous ceux que couvrent les données |
| source | `portfolio` / `allTime`, granularité hebdomadaire (Phase 0 § 3) |
| coût | ≈ 7 minutes (Phase 0 § 7) |
| exécution | **unique**, lecture **unique** |

## P1.8 Interdictions

1. Aucun seuil ajusté après lecture des chiffres — ni $500, ni 30 comptes, ni
   |t| = 2.
2. Aucune variante de métrique après coup.
3. Aucune restriction de panel *a posteriori* — ni « en excluant les comptes
   ruinés », ni « sur les 100 plus gros ».
4. Aucune lecture partielle : toutes les cellules sont lues en une fois.
5. Aucun ordre, aucune clé, aucune donnée privée.

## P1.9 Invalidation du run (≠ verdict)

Colonne temporelle dégénérée, garde-fou `measure_guards` qui lève, aucune
cellule émise, ou panel non constituable ⇒ **run NUL**, correction, re-run, les
deux empreintes au rapport.

## P1.10 Invariant

Tout cas non prévu est **nommé comme tel**, décrit, et ne fonde aucune
conclusion opportuniste. Clauses de repli conservatrices par défaut.

---

## P1.11 Résultat — **PHASE DE CONCEPTION**

**Run** : 2026-08-02T15:17Z · empreinte git `6d873cf+dirty` ·
`backtests/wallets/phase1_persistence.py` · panel **1 000 adresses**, dont
**997 exploitables** · 15 trimestres couverts, 2023Q1 → 2026Q3.

Exécution unique, lecture unique. Aucun paramètre modifié entre la grille et ce
résultat.

### Attrition — le dispositif anti-lookahead ne fuit pas

Rappel : l'appartenance se décide au seul trimestre T, et la métrique de T+1 est
calculée pour tout compte du panel T disposant de données, **y compris ruiné**.

| paire | panel | appariés | perdus | attrition |
|---|---:|---:|---:|---:|
| 2024Q4→2025Q1 | 192 | 191 | 1 | 0,5 % |
| 2025Q1→2025Q2 | 331 | 325 | 6 | 1,8 % |
| 2025Q3→2025Q4 | 448 | 442 | 6 | 1,3 % |
| 2025Q4→2026Q1 | 489 | 474 | 15 | 3,1 % |
| 2026Q2→2026Q3 | 546 | 537 | 9 | 1,6 % |

Attrition de **0,5 % à 6,2 %** selon la paire. La quasi-totalité des comptes
sélectionnés en T est retrouvée en T+1 : la mesure ne repose pas sur un
sous-ensemble de survivants.

### La table de verdict

| groupe | strate | paires | rho poolé | SE | **t** | |
|---|---|---:|---:|---:|---:|---|
| tous | petit | 8 | +0,1333 | 0,0294 | **+4,54** | ◄ |
| tous | moyen | 8 | +0,0430 | 0,0774 | +0,55 | |
| tous | gros | 8 | +0,0268 | 0,1037 | +0,26 | |
| tous | agrégat | 10 | +0,0900 | 0,0550 | +1,64 | |
| **gagnants** | petit | 6 | −0,0079 | 0,0743 | **−0,11** | |
| **gagnants** | moyen | 6 | +0,0117 | 0,1124 | **+0,10** | |
| **gagnants** | gros | 6 | +0,0792 | 0,1427 | **+0,56** | |
| **gagnants** | agrégat | 9 | +0,0567 | 0,0694 | **+0,82** | |
| **perdants** | petit | 6 | **+0,1729** | 0,0259 | **+6,68** | ◄ |
| **perdants** | moyen | 6 | **+0,1802** | 0,0453 | **+3,97** | ◄ |
| **perdants** | gros | 6 | **+0,1318** | 0,0453 | **+2,91** | ◄ |
| **perdants** | agrégat | 10 | **+0,1397** | 0,0380 | **+3,68** | ◄ |

**5 cellules sur 12 à |t| ≥ 2**, contre **0,6 attendue** sous l'hypothèse nulle.
La clause du § P1.6 est franchie ⇒ **PHASE DE CONCEPTION**.

### Le résultat n'est pas dispersé, il est structuré

Ce n'est pas cinq cellules au hasard parmi douze. C'est une **asymétrie nette** :

> **Les perdants persistent — les quatre cellules, sans exception.
> Les gagnants ne persistent pas — aucune des quatre, t entre −0,11 et +0,82.**

Et le signe tient paire par paire :

| cellule | rho par paire | positifs |
|---|---|---:|
| perdants · petit | +0,279 / +0,147 / +0,155 / +0,122 / +0,218 / +0,116 | **6/6** |
| perdants · moyen | +0,074 / +0,236 / +0,225 / +0,250 / +0,010 / +0,287 | **6/6** |
| perdants · gros | +0,262 / +0,177 / +0,229 / −0,022 / +0,106 / +0,039 | 5/6 |
| perdants · agrégat | ... 8 positifs sur 10 ; les deux négatifs sont les paires de 2024 à faible effectif (n = 45 et 62) | 8/10 |
| gagnants · agrégat | +0,304 / +0,086 / +0,138 / +0,012 / +0,180 / **−0,245** / +0,149 / +0,203 / **−0,317** | 7/9 mais dispersion double |

À partir de 2025Q1, où les effectifs passent de 132 à 376 comptes appariés, les
**six dernières paires consécutives** sont positives pour les perdants :
+0,244 · +0,196 · +0,208 · +0,174 · +0,125 · +0,256.

La cellule « tous · agrégat » n'est **pas** significative (t = +1,64) : mélanger
des gagnants sans persistance et des perdants qui persistent dilue exactement ce
qu'on cherche. C'est cohérent avec le reste, pas contradictoire.

### ⚠ Explication alternative — nommée, non tranchée

Chez les perdants, toutes les métriques sont négatives : **classer par métrique,
c'est classer par ampleur de perte**. Or l'ampleur de perte suit au moins autant
l'**appétit pour le risque** (levier, taille de position) que la qualité des
décisions. Une persistance de rang chez les perdants pourrait donc mesurer une
**persistance de comportement de risque**, pas une persistance d'incompétence.

Un élément va contre cette lecture, sans la réfuter : **si c'était uniquement du
levier persistant, les gagnants le montreraient aussi** — un compte à fort levier
qui gagne aurait une métrique de forte amplitude deux trimestres de suite. Or les
gagnants n'exhibent aucune persistance. L'asymétrie n'est pas expliquée par le
seul appétit pour le risque.

Départager les deux demanderait le détail au trade — que la Phase 0 § 4 a établi
**inaccessible** pour une adresse tierce.

### Ce que le résultat ne dit pas

1. **L'ampleur est faible.** rho ≈ +0,14 explique environ **2 % de la variance
   de rang**. Statistiquement robuste, économiquement modeste.
2. **La métrique porte le biais nommé au § P1.1** : les flux de capital polluent
   le dénominateur, sans correction possible à cette granularité.
3. **Persistance de rang ≠ rentabilité d'un fade.** Savoir qu'un perdant a des
   chances de rester mal classé **parmi les perdants** ne dit rien du rendement
   d'une position opposée, ni de son coût d'exécution, ni de sa capacité.
4. **Les trimestres antérieurs à 2025 sont peu dotés** (3 à 192 comptes) ; le
   signal est porté par les six dernières paires.

### Ce qu'une phase de conception devrait résoudre

Énoncé factuellement, sans recommandation — la conception est une mission
distincte avec sa propre grille :

- traduire une persistance de **rang** en une règle de position, à partir d'une
  série **hebdomadaire** et sans détail au trade ;
- établir que le côté perdant reste identifiable **en temps réel**, et pas
  seulement dans un panel reconstruit a posteriori ;
- chiffrer la capacité et le coût — 537 comptes appariés au dernier trimestre,
  mais leurs positions ne sont pas observables (Phase 0 § 4) ;
- départager persistance d'incompétence et persistance d'appétit pour le risque,
  ou assumer de ne pas pouvoir le faire.

### Verdict

> **PHASE DE CONCEPTION**, sur la cellule **perdants** — les quatre strates,
> t de +2,91 à +6,68, contre zéro cellule significative côté gagnants.
>
> **Copier les gagnants n'a aucune base dans ces données.** Fader les perdants
> en a une, statistiquement robuste et économiquement modeste, dont la nature
> exacte — incompétence ou appétit pour le risque — n'est pas départageable avec
> les données publiques disponibles.
