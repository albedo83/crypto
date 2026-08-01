# Projet A — TREND-v0 : spécification gelée et grille d'acceptation

> **⚠ CE DOCUMENT EST ÉCRIT ET COMMITTÉ AVANT TOUTE EXÉCUTION.**
> Aucun backtest TREND n'a été lancé au moment où ces lignes sont écrites.
> L'historique git en fait foi : la grille du § 3 précède le premier run.
>
> **Rédigé le** : 2026-08-01 · **Phase 2** de la mission « Portefeuille & Projet A »
> · en attente du **GO explicite** de Seb pour la Phase 3.

---

## 0. Ce que ce projet cherche — et ce qu'il ne cherche pas

Le § 15 de `rapport.md` a acté un fait : la configuration en service porte un
drawdown de **−51,4 % sur 28 mois** (queue Monte Carlo : P95 = −51,8 %,
P99 = −58,7 %, cf. `docs/dd_anatomy.md`), sur un capital **non rechargeable**.
Le verdict de risque a été « aucun changement », faute de levier disponible du
côté du capital.

Le Projet A explore le **seul levier restant** : ajouter au portefeuille une
source de P&L **structurellement décorrélée** d'Alfred, pour que les creux de
l'un ne soient pas les creux de l'autre.

Alfred est un moteur de **fade** — il vend l'extension et achète la
capitulation. Sa décorrélation naturelle est un moteur de **suivi de tendance**,
qui fait exactement l'inverse : il achète l'extension. C'est l'hypothèse
TREND-v0, et elle vient de la littérature, pas d'une exploration de données.

**Ce n'est pas** une tentative d'améliorer Alfred. Le dossier « edge Alfred »
est clos (§ 13 de `rapport.md`, sept leviers d'entrée réfutés). Aucun paramètre
d'Alfred n'est touché par ce projet, quel qu'en soit le résultat.

---

## 1. Spécification TREND-v0 — GELÉE

Chaque ligne ci-dessous est un **choix de manuel**, pas un choix mesuré. Aucune
valeur n'a été balayée, comparée ou retenue pour son résultat : la campagne du
30 juillet a établi qu'une constante ajustée sur 28 mois est la classe d'objet
qui échoue systématiquement en walk-forward. Ici, il n'y a rien à ajuster parce
que rien n'a été choisi en regardant les données.

### 1.1 Univers

| | |
|---|---|
| source | **`Params.trade_symbols`** — source unique, celle du bot en service |
| gate de parité | l'univers réellement simulé est comparé à `Params.trade_symbols` via `measure_guards.require_match` ; **mismatch = le run refuse d'émettre** |
| éligibilité | un symbole entre dans l'univers dès qu'il dispose de **50 barres journalières** (30 pour le momentum + 14 pour l'ATR + marge) |
| contrainte sectorielle | **aucune** — Alfred en a une (`max_per_sector`), l'ajouter ici serait un paramètre de plus |

### 1.2 Barres

Les données sont en bougies 4 h. Une **barre journalière** = 6 bougies 4 h
consécutives, frontière **00:00 UTC**. Open = open de la première, close = close
de la sixième, high/low = extrêmes des six.

### 1.3 Entrée

| | |
|---|---|
| cadence | **scan quotidien**, à la clôture de la barre journalière |
| canal | Donchian **20 jours**, calculé sur les **20 barres précédant** la barre courante (barre courante exclue) |
| LONG | `close > max(high des 20 barres précédentes)` **ET** `rendement 30 j > 0` |
| SHORT | `close < min(low des 20 barres précédentes)` **ET** `rendement 30 j < 0` |
| déclenchement | **sur clôture**, pas sur mèche — cohérent avec la discipline « les règles se lisent aux clôtures » (v1.8.0, v1.15.5) |
| exécution | au **close de la barre déclenchante** |
| pyramidage | **interdit** — une position par symbole au plus |
| ré-entrée | autorisée dès qu'une nouvelle cassure se produit après une sortie. **Aucun cooldown** (ce serait un paramètre) |

Le rendement 30 j est `close / close[−30] − 1` sur barres journalières.

### 1.4 Sortie

| | |
|---|---|
| unique règle | **chandelier 3 × ATR(14)** |
| LONG | stop = `plus haut atteint depuis l'entrée − 3 × ATR(14)` |
| SHORT | stop = `plus bas atteint depuis l'entrée + 3 × ATR(14)` |
| cliquet | le stop ne recule jamais |
| ATR | True Range de Wilder sur barres journalières, lissage de Wilder sur 14 périodes, **valeur courante** à chaque évaluation |
| évaluation | **aux clôtures journalières uniquement** |
| booking | **au MARK de la clôture**, jamais au niveau théorique du stop |
| take-profit | **aucun** |
| hold maximum | **aucun** |

> Le booking au mark n'est pas un détail. Booker un trail à son niveau théorique
> valait ~50 % du P&L du backtest d'Alfred (v1.15.5, `project_trail_booking_bias`).
> TREND-v0 sort **au marché à la clôture qui constate le franchissement** — la
> seule sémantique que le live sait exécuter.

### 1.5 Sizing

| | |
|---|---|
| budget de risque | **1,0 % de l'equity par position** — valeur de manuel |
| notionnel | `risque / (3 × ATR / prix)` : la distance au stop initial est de 3 × ATR, donc un stop touché coûte le budget de risque |
| cap par position | **0,3 × equity** — parité avec le cap proportionnel d'Alfred (v1.13.0) |
| cap agrégé | notionnel total ≤ **2 × equity** — parité avec `Params.leverage` |
| nombre de positions | **N = 6** — parité avec `Params.max_positions` |
| arbitrage des slots | si plus de 6 signaux le même jour : **ordre alphabétique du symbole** |

**Pourquoi 1,0 % n'est pas un levier de résultat.** Le vol-targeting rend les
quatre clauses de la grille quasi invariantes d'échelle : doubler le budget de
risque double approximativement le rendement **et** le drawdown, donc laisse le
Calmar (C4) inchangé ; la corrélation (C1) est invariante par construction ; le
signe du P&L (C2, C3) l'est aussi tant qu'on n'approche pas la ruine. Fixer ce
paramètre à la valeur de manuel ne dissimule donc aucun choix de résultat.

**Pourquoi l'ordre alphabétique.** Tout autre critère de départage (force du
momentum, largeur du canal, liquidité) serait un **signal supplémentaire** qui
n'est pas dans la spec. L'ordre alphabétique est retenu précisément parce qu'il
ne porte **aucune information** : il ne peut ni aider ni nuire systématiquement.

### 1.6 Coûts

Modèle **identique à Alfred**, sans dérogation : `9 bps taker + 4 bps slippage`
= **13 bps round-trip**, appliqués une fois à la clôture sur le notionnel.

> **Réserve documentée, qui n'est pas une clause.** Ce modèle est *favorable* à
> TREND : Alfred tient ses positions 48 h, TREND les tiendra des semaines, et le
> coût de portage (funding) croît avec la durée. Le rapport de Phase 3
> **chiffrera** ce portage à partir de `load_funding()` sur les durées de
> détention réelles et le publiera **à côté** de C3 — sans modifier le verdict
> de C3, qui reste rendu sur le modèle instruit. Un lecteur pourra ainsi juger
> si une réussite de C3 est confortable ou marginale.

---

## 2. Fenêtres de référence

### 2.1 Fenêtres de creux — clause C2

Issues de la Phase 1 (`docs/dd_anatomy.md`), **fixées, non re-négociables** :

| # | fenêtre | ce qu'Alfred y a fait |
|---|---|---|
| A | **2024-08-03 → 2024-11-06** | le drawdown maximal : **−51,4 %**, 140 trades, −$897 |
| B | 2024-09-22 → 2024-10-21 | pire fenêtre 30 j : **−29,5 %**, 41 trades |
| C | 2024-08-03 → 2024-09-01 | 2ᵉ pire fenêtre 30 j : **−26,2 %**, 57 trades |
| D | 2025-07-21 → 2025-08-19 | 3ᵉ pire fenêtre 30 j : **−25,2 %**, 41 trades |

B et C sont incluses dans A ; D en est indépendante, un an plus tard. Les quatre
sont évaluées séparément.

### 2.2 Fenêtres walk-forward — clause C3

Fenêtres **doctrine** du projet : 4 fenêtres OOS de 6 mois **glissantes et non
chevauchantes**, aux décalages 0 / 6 / 12 / 18 mois depuis la fin des données.
Elles se déplacent avec le rafraîchissement des données ; leurs bornes exactes
sont imprimées par l'empreinte du run de verdict.

### 2.3 Fenêtre longue — clauses C1 et C4

28 mois, mêmes bornes que `docs/dd_anatomy.md`, capital de départ $1 000.

---

## 3. GRILLE D'ACCEPTATION — PRÉ-ENREGISTRÉE

### C1 — décorrélation

> **Corrélation de Pearson entre le P&L quotidien de TREND et celui d'Alfred
> < 0,30 sur les 28 mois.**

- Calculée sur les **rendements quotidiens en %** de chaque stratégie
  (`ret_pct`), sur **tous les jours civils** de la fenêtre, **jours sans trade
  inclus à 0**. Série Alfred = `data/alfred_daily_pnl.csv`.
- Spearman et la corrélation restreinte aux jours de co-activité sont
  **reportées à titre informatif** — le verdict porte sur le Pearson tous jours.

| cas | verdict |
|---|---|
| ρ < 0,30 | **PASSE** |
| ρ = 0,30 exactement | **ÉCHOUE** (le seuil est strict) |
| ρ < 0 | **PASSE** — une anticorrélation est meilleure, mais ne compense aucune autre clause |

### C2 — comportement dans les creux d'Alfred

> **P&L de TREND ≥ 0 sur chacune des quatre fenêtres A, B, C, D du § 2.1.**

| cas | verdict |
|---|---|
| les 4 fenêtres ≥ 0 | **PASSE** |
| P&L exactement 0 sur une fenêtre | **PASSE** (la clause dit « ≥ ») |
| une seule fenêtre < 0 | **ÉCHOUE** |
| **zéro trade** sur une fenêtre (donc P&L 0) | **PASSE au sens littéral.** Le rapport publie le **nombre de trades par fenêtre de creux**. Si les quatre fenêtres sont à zéro trade, le verdict global reste celui de la grille, mais le rapport porte la mention **« C2 non informative »** — une couverture qui ne se déclenche jamais n'est pas une couverture, et le lecteur doit le savoir sans qu'on déplace le poteau après coup |

### C3 — viabilité propre, après coûts

> **P&L net de TREND ≥ 0 sur chacune des 4 fenêtres walk-forward du § 2.2.**
> Strict **4/4**.

| cas | verdict |
|---|---|
| 4/4 ≥ 0 | **PASSE** |
| exactement 0 sur une fenêtre | **PASSE** |
| 3/4 | **ÉCHOUE** — c'est la règle du projet, elle a déjà servi à refuser sept leviers |

### C4 — apport au portefeuille

> **Calmar du portefeuille combiné > Calmar d'Alfred seul, aux DEUX allocations
> fixes 70/30 et 50/50.**

- **Calmar = CAGR / |drawdown max|**, sur la fenêtre de 28 mois.
- Allocation **fixe à l'origine, sans aucun rééquilibrage** (une fréquence de
  rééquilibrage serait un paramètre de plus). Chaque poche compose sur son
  propre capital ; l'equity du portefeuille est la somme des deux poches, et le
  drawdown combiné se lit sur cette somme.
- Référence à battre : Alfred seul, **recalculé dans le run de verdict** (ordre
  de grandeur au 2026-08-01 : CAGR ≈ +199 %/an, DD −51,4 %, Calmar ≈ 3,9).

| cas | verdict |
|---|---|
| Calmar combiné > Alfred aux deux allocations | **PASSE** |
| amélioration à 70/30 seulement | **ÉCHOUE** — les deux sont exigées |
| TREND a un meilleur Calmar isolé mais dilue le combiné | **ÉCHOUE.** C4 juge le **combiné**, pas TREND seul. Une dilution de rendement qui abaisse le Calmar combiné est un échec, même adossée à une belle statistique isolée |
| DD combiné nul (Calmar indéfini) | **PASSE**, Calmar traité comme +∞ |
| rendement total de TREND négatif | Calmar négatif → **ÉCHOUE** par construction |

### Verdict global

| | |
|---|---|
| **4 clauses sur 4** | TREND-v0 devient **candidat paper**. Cela signifie : éligible à une *décision* de déploiement paper, prise par Seb. Rien n'est déployé automatiquement |
| **une seule clause échoue** | **REJET.** Documentation du résultat, **aucun ajustement de paramètre, aucun re-run modifié**, et le dossier TREND-v0 est **clos**. Une reprise ultérieure devra porter une hypothèse **nouvelle**, pas un re-réglage de celle-ci |

---

## 4. Interdictions explicites

Elles ne sont pas des recommandations. Elles closent les échappatoires connues.

1. **Aucun autre lookback.** Ni 10, ni 50 jours pour le Donchian. Ni 20, ni 60
   pour le momentum.
2. **Aucun autre multiple d'ATR.** Ni 2, ni 4. Ni une autre période que 14.
3. **Aucune allocation optimisée.** 70/30 et 50/50, point. Pas de 60/40 « pour
   voir », pas de poids inverse-volatilité, pas de rééquilibrage.
4. **Aucun filtre ajouté** après lecture des résultats : ni régime, ni
   liquidité, ni secteur, ni liste noire de tokens.
5. **Aucune variante de direction.** Pas de « TREND-v0 mais LONG seulement »
   après avoir vu que les SHORT perdent. Ce serait la v1.17.0 à nouveau.
6. **Aucune lecture partielle.** Les quatre clauses sont lues **en une fois**,
   après le run complet. Pas de « C1 passe, continuons à peaufiner ».

### Mise au point du code

Écrire le harnais demandera des exécutions de débogage. Elles sont autorisées
**uniquement sur la fenêtre 2024-04-01 → 2024-06-30** — hors des fenêtres de
creux (§ 2.1) et hors des fenêtres walk-forward (§ 2.2), donc incapables de
révéler un verdict.

**Le run de verdict est le PREMIER run complet conforme à la spec.** Son
empreinte (`backtests/fingerprint.py`) est publiée dans le rapport final. S'il
faut le relancer, la raison est documentée et l'empreinte des deux runs figure
au rapport.

### Cas d'invalidation du run (≠ rejet de TREND)

Si le gate de parité d'univers échoue, si un garde-fou de `measure_guards.py`
lève, ou si la série de rendements est constante, **le run est nul** : pas de
verdict, correction, re-run. Ce n'est pas un échec de TREND-v0 — c'est
exactement le dispositif qui a manqué en juillet, quand cinq mesures fausses
ont émis des chiffres plausibles au lieu d'une erreur.

---

## 5. Livrable de la Phase 3

`docs/projet_a_trend_v0.md` — **ce fichier**, complété d'une section « Résultat »
donnant, clause par clause, le verdict et les chiffres qui le fondent.

Sans commentaire d'opportunité. « On pourrait essayer… », « avec un réglage
un peu différent… », « la tendance est encourageante » : **interdits**. Le
backtest rejette, il ne promet pas.

---

## 6. Résultat

> *À compléter en Phase 3, après GO explicite. Vide à ce jour — c'est le sujet
> de tout ce document.*
