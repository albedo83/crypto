# basket_haircut_eda — résultats

**Date** : 2026-05-15
**Auteur** : Claude (R&D)
**Statut** : **Négatif — classer**

## TL;DR

Hypothèse testée : utiliser l'`effective_n` du panier ouvert comme **haircut
multiplicatif continu** sur les nouvelles entrées (taille réduite quand les
positions ouvertes sont corrélées). Comparé sur trois fenêtres (7d / 14d /
30d) × trois `EFFN_REF` (3.0 / 4.0 / 6.0) × trois `MIN_HAIRCUT` (0.25 / 0.40
/ 0.50) = 27 configs sur 4 fenêtres walk-forward.

- **0 / 27 configs** passent le critère A (ΔPnL ≥ -5% **ET** ΔDD ≥ +5pp sur
  les 4 fenêtres).
- **0 / 27 configs** passent le critère B (ΔCalmar ≥ +15% sur les 4
  fenêtres).
- Le meilleur config (window=30d, REF=6.0, MIN=0.5) délivre ΔCalmar **moyen
  = -47%** : le haircut détruit beaucoup plus de PnL qu'il ne réduit de
  drawdown.
- Les 3 fenêtres (7d/14d/30d) produisent des résultats quasi-identiques :
  `effective_n` est stable à travers les lookbacks → la question
  bias/variance soulevée par la proposition n'a pas d'enjeu pratique ici
  (le signal est trop faible pour qu'il y ait un compromis utile).

**Recommandation** : ne pas implémenter. Classer dans `BACKLOG.md` comme
testé-et-rejeté.

## Méthodologie

### Choix : corrélation P&L vs corrélation prix

La proposition souligne qu'il faut **corrélation P&L** (signée par
direction) plutôt que corrélation prix nue — deux SHORT sur tokens
positivement corrélés en prix sont en réalité des trades qui se couvrent.

J'ai vérifié `analysis/bot/features.py:236-326` : l'instrumentation live
utilise déjà la corrélation P&L (lignes 296 et 305 : signed_btc multiplié
par `dirs_arr`, signed_mat = corr_mat × outer(dirs)). Le backfill backtest
reproduit cette même convention : `ρ_pnl[i,j] = dir_i × dir_j × ρ_price[i,j]`.

Formule effective_n (mirror live) :
```
signed_mat = corr_matrix * outer(dirs, dirs)
diag(signed_mat) = 1
eff_n = n² / sum(signed_mat),  clamped to [1, n]
fallback when sum ≤ 0:  eff_n = n  (fully over-hedged)
```

### Backfill du backtest

3 séries d'`effective_n` calculées à chaque candle 4h pendant 28 mois :
- `eff_n_7d`  : 42 candles de lookback (≈ 7 jours)
- `eff_n_14d` : 84 candles
- `eff_n_30d` : 180 candles (mirror live)

À chaque ouverture de position, on enregistre la valeur d'`effective_n`
du **panier existant avant l'entrée** (les 3 fenêtres). Si `n_pos < 2`,
on stocke `1.0`.

Performance : pré-calcul des séries de rendements par coin une fois en
début de `run_window`. À chaque ts on ne fait que 3 `np.corrcoef` sur des
slices ≤ 6×180. Coût marginal ≈ 3s sur le 28m (27s vs 24s baseline).

Validation : les baselines 28m / 12m / 6m / 3m sont **identiques** au
v12.5.30 (1105 / 461 / 230 / 135 trades, PnL et DD inchangés).

### EDA risque-side (étape 3)

But : vérifier la prémisse "low eff_n → upcoming drawdown" avant de
sweeper le haircut. Si la prémisse échoue, le haircut ne peut pas
fonctionner pour les bonnes raisons.

**Détection d'évènements DD sur la courbe equity** : peak-to-valley ≥ 15%
en ≤ 21 candles → 36 évènements sur le 28m (drop médian 18%, durée
médiane 6 candles ≈ 1 jour).

**Test 1 — Mann-Whitney `eff_n` à l'onset DD vs hors-DD** :

| Window | Lag (candles avant peak) | Mean(DD start) | Mean(non-DD) | Delta | MW p-value |
|---|---|---|---|---|---|
| 7d  | 0 | 2.11 | 2.01 | **+0.10** | 0.74 |
| 7d  | 7 | 1.82 | 2.01 | -0.19 | 0.24 |
| 14d | 0 | 2.14 | 2.02 | **+0.12** | 0.78 |
| 14d | 7 | 1.83 | 2.02 | -0.19 | 0.24 |
| 30d | 0 | 2.13 | 2.03 | **+0.10** | 0.73 |
| 30d | 7 | 1.86 | 2.03 | -0.17 | 0.28 |

À l'onset du DD, `eff_n` est **plus élevé** que la baseline, pas plus
bas. Aucun p-value n'est significatif. **La prémisse "low eff_n
prédit DD" n'est pas soutenue par les données.**

**Test 2 — Décile predictif sur `eff_n` brut (DD onset dans les 7 jours)** :

La déciles montre un "lift" de 1.84x sur la décile 1 (`eff_n=1.0`). Mais
inspection : la décile 1 est dominée par les ts à `n_pos=1` (992 ts sur
28m → `eff_n=1.0` par construction, pas par corrélation). Ce n'est pas du
**signal de concentration**, juste du **signal "petit panier"**.

**Test 3 — Spearman sur `eff_n / n_pos` (concentration normalisée, n_pos ≥ 2)** :

Restreint aux ts où `n_pos ≥ 2` pour éliminer l'artefact "panier trivial".
`eff_n / n_pos ∈ [0.17, 1.0]` mesure la "qualité de dilution" indépendante
de la taille du panier.

| Window | Horizon (candles) | ρ (raw) | ρ (norm) | p-value (norm) |
|---|---|---|---|---|
| 7d  | 6  (1d)   | -0.002 | **+0.058** | 0.001 |
| 7d  | 12 (2d)   | -0.019 | +0.033 | 0.05 |
| 7d  | 21 (3.5d) | -0.040 | -0.006 | 0.72 |
| 7d  | 42 (7d)   | -0.021 | -0.011 | 0.52 |
| 14d | 6         | -0.005 | +0.049 | 0.003 |
| 30d | 6         | -0.006 | +0.055 | 0.001 |

Magnitude de la corrélation **très faible** (`|ρ| ≤ 0.06`). Signal réel
uniquement à horizon court (1-2 jours), et dans la bonne direction (haut
`eff_n` → DD futur moins négatif). Mais l'effet est minuscule.

**Conclusion de l'EDA risque-side** : le signal "eff_n prédit DD" existe
mais est très faible (-0.8pp de moyenne de DD futur entre demi-décile
basse et haute, à horizon 1 jour seulement). Aucune des 3 fenêtres ne se
distingue clairement. La fenêtre 7d a le meilleur signal *strict-strict*
à 6-candle (MW p=2e-5) — mais le **delta de PnL anticipé** d'un haircut
sur ce signal est minime.

Décision : sweeper quand même les 3 fenêtres pour valider empiriquement.

### Sweep haircut (étape 4)

Formule : `mult = max(MIN_HAIRCUT, min(1.0, eff_n_W / EFFN_REF))`, appliquée
**APRÈS** le modulator adaptatif v11.10.0+v12.2.0 (multiplicatif par-dessus,
ne le remplace pas).

| Grille | Valeurs |
|---|---|
| Window W | {7, 14, 30} jours |
| EFFN_REF | {3.0, 4.0, 6.0} (6 = MAX_POSITIONS) |
| MIN_HAIRCUT | {0.25, 0.40, 0.50} |

27 configs × 4 fenêtres = 108 runs, ~5 min total.

### Critères d'acceptation

Le haircut "gagne" si **A** OU **B** :
- **A** : ΔPnL ≥ -5% **ET** ΔDD ≥ +5pp sur les 4 fenêtres
- **B** : ΔCalmar (PnL/|DD|) ≥ +15% sur les 4 fenêtres

## Résultats

### Baselines (no haircut)

| Window | PnL | DD | Calmar | Trades |
|---|---|---|---|---|
| 28m | +1 731 874 (+173 187%) | -74.3% | 23 296 | 1105 |
| 12m | +90 116 (+9 012%) | -41.4% | 2 176 | 461 |
| 6m  | +13 058 (+1 306%) | -32.9% | 397 | 230 |
| 3m  | +2 325 (+233%) | -17.4% | 133 | 135 |

### Sweep results

**0 / 27** configs passent A ou B.

Comparaison du **meilleur config** (avg ΔCalmar = -47%) :

`window=30d, EFFN_REF=6.0, MIN_HAIRCUT=0.5`

| Window | ΔPnL | ΔDD | ΔCalmar | Baseline Calmar | Haircut Calmar |
|---|---|---|---|---|---|
| 28m | **-79.5%** | +20.3pp | **-71.8%** | 23 296 | 6 562 |
| 12m | **-62.9%** | +6.9pp  | **-55.6%** | 2 176 | 967 |
| 6m  | **-51.5%** | +15.1pp | **-10.2%** | 397 | 357 |
| 3m  | **-50.6%** | +0.0pp  | **-50.6%** | 133 | 66 |

Même résultat pour window=7d et 14d (REF=6.0, MIN=0.5) — les 3 fenêtres
sont quasi-identiques.

### Pourquoi ça ne marche pas

1. **Le drawdown n'est pas principalement piloté par la corrélation du
   panier.** Les pires DD viennent de mouvements adverses sur une position
   à conviction élevée (S8 flush LONG qui repart à la baisse, S9 fade qui
   continue dans le sens du mouvement extrême). L'`effective_n` ne capte
   pas ce risque idiosyncratique.

2. **Le haircut taxe l'edge.** Le modulator adaptatif v11.10.0 amplifie
   déjà S1/S8/S9 en bear (S8 α=-0.5, etc.). Ajouter un haircut multiplicatif
   réduit `size * macro_mult * haircut` → le compounding est massivement
   ralenti (la perte sur 28m est de 79% du PnL vs la baseline alors que la
   DD ne baisse que de 20pp).

3. **`effective_n` est très stable.** Le panier moyen contient 2-3
   positions avec `eff_n ≈ 2.0`. Quand on a un panier de 5+ positions
   (rare : 43 ts sur 5107 = 0.8% du temps), `eff_n` peut atteindre 5-6.
   Le ratio `eff_n / EFFN_REF` passe rarement sous 0.6 même avec REF=6 —
   donc le haircut tape sur la quasi-totalité des entrées, pas
   sélectivement.

4. **Les 3 fenêtres convergent.** Hypothèse initiale : 7d réagit plus vite
   au régime, 30d plus lisse. En pratique les 3 séries sont fortement
   corrélées entre elles (la composition du panier change peu candle à
   candle) → choisir l'une ou l'autre n'a aucun impact mesurable.

## Limites de cette EDA

- 36 évènements DD sur 28m est statistiquement modeste. Une fenêtre plus
  longue (4-5 ans de candles 4h) pourrait révéler des sous-signaux. Mais
  ça ne changerait probablement pas la conclusion vu la magnitude.
- Le critère "DD ≥ 15% peak-to-valley en ≤ 21 candles" est arbitraire ;
  d'autres définitions (ex. DD relatif à equity peak global) pourraient
  donner d'autres évènements. J'ai essayé un balayage léger de
  `(drop_pct, max_candles)` — la cible 30 évènements est atteinte à 15% /
  21c, mais en allant à 10% / 30c on a 80+ évènements et la conclusion
  reste identique sur les Spearman et MW.
- Le sweep n'a pas testé `MIN_HAIRCUT > 0.5` (par ex. 0.7, 0.85) qui
  taxerait moins le PnL. Mais alors le haircut est si proche de 1.0
  qu'il ne réduit pas le DD non plus. Le compromis est intrinsèquement
  serré.
- Pas de test sur les coûts (slippage variable, funding) qui sont
  modélisés en fixe — un haircut effectif réduirait aussi le coût total
  proportionnellement, ce qui pourrait légèrement améliorer les chiffres
  mais ne renverserait pas la conclusion.

## Données et code

- Engine modifications : `backtests/backtest_rolling.py` (commit
  `86a0d3e` pour le backfill + `703d8f5` pour le hook `basket_haircut_fn`)
- Runner baseline : `backtests/basket_haircut_eda_run.py`
- EDA risque-side : `backtests/basket_haircut_eda_riskside.py`,
  `backtests/basket_haircut_eda_riskside2.py`
- Sweep : `backtests/basket_haircut_eda_sweep.py`
- Datasets : `backtests/basket_haircut_eda_data/` (trades JSONL,
  basket_ts JSONL, baseline_summary.json, riskside_summary*.json,
  sweep_results.json)

## Recommandation finale

**Classer** dans `BACKLOG.md` comme testé et rejeté. Le mécanisme proposé
n'a pas d'edge mesurable sur les données walk-forward 4/4. La voie alpha
risk-based reste à explorer ailleurs (par ex. sizing inversement
proportionnel à `mae_realized` historique du token, ou throttle "kill
new entries" en plein DD au lieu de sizing continu). L'instrumentation
live `effective_n` reste utile pour le **monitoring** (alerte si le
panier devient extrêmement concentré) — c'est son usage actuel et il est
suffisant.
