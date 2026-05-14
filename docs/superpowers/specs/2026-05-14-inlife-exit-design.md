# In-life exit research — S5 / S8 trailing extension

**Date** : 2026-05-14
**Auteur** : Sébastien + Claude
**Status** : Design figé — implémentation à valider via `writing-plans`
**Scope** : R&D backtest uniquement. Aucun changement au code prod sans walk-forward 4/4 strict + approbation.

---

## 1. Contexte et déclencheur

Trade live INJ S5 LONG du 2026-05-12 :
- Entrée $4.875, MFE +2329 bps (~+$63 latents), retracement ensuite
- Stop manuel posé par l'utilisateur → exit 2026-05-13 12:48 à +$47.56 (`manual_stop_set`)
- Prix actuel (2026-05-14) : $5.099 → la sortie naturelle S5 à 48h aurait rendu ~+$12
- **Δ vs no-action ≈ +$35**, soit ~75% du MFE capturé

Le geste manuel a fait ce que le **trailing stop S10** (`v11.4.0` : MFE−150 bps quand MFE > 600 bps) fait déjà mécaniquement, mais pour une stratégie qui n'a pas de trailing en prod.

### Prior art

| Script | Approche | Résultat |
|---|---|---|
| `backtest_s5_trailing.py` | Grille 2D (trigger × offset) sur S5 seul | Pas shippé → pas de 4/4 robuste apparent |
| `backtest_trailing_sweep.py` | Grille 5×3 sur S5/S8/S9, walk-forward 4/4 strict | Pas shippé → idem |
| `backtest_mfe_rollback_audit.py` | Audit features au pic MFE (OI flip, sector, btc) | Observation only |
| `backtest_giveback.py` | Règle "was green, now red" (MFE ≥ X puis cur ≤ Y) | Pas shippé |
| `backtest_early_mfe_exit.py` | Exit si MFE absent à H heures | Pas shippé |

**Le simple trailing MFE n'a pas passé 4/4 sur S5/S8/S9.** Hypothèse : la règle est trop pauvre — un même `(trigger, offset)` est imposé à tous les régimes, hold_hours et tokens, alors que le pattern de retracement varie.

### Hypothèse de recherche

Une règle d'exit **conditionnée sur plus que MFE seul** (régime btc_z, hold_hours, MAE, vélocité MFE, features OI/funding) peut capturer le pattern de retracement sans tuer les runners, là où le trailing simple échoue.

---

## 2. Objectif

Trouver une règle d'exit in-life pour **S5 et S8** qui :

- **Augmente le PnL** sur les 4 fenêtres walk-forward (28m / 12m / 6m / 3m), strict 4/4
- **N'aggrave pas le DD de plus de +1pp** (moyenne sur les 4 fenêtres) — cohérent avec les ship récents (v12.5.0, v12.2.0)
- **Kill-rate < 30%** : moins de 30% des positions S5/S8 fermées prématurément (anti-cull des runners)

Strats exclues :
- **S1** : momentum continuation, peu retracement-prone, hors scope explicite
- **S9** : a déjà `runner_extension` (v11.7.32) qui agit dans l'autre sens — interaction trop risquée
- **S10** : trailing déjà en prod (v11.4.0)

---

## 3. Trois familles testées en parallèle

Toutes les familles partagent le même harness et la même validation. Le but n'est pas de toutes les shipper — c'est de comparer trois représentations différentes du même problème pour identifier la plus simple qui passe.

### Famille A — Multi-feature trail (généralisation de S10)

Forme :
```
exit si :
    MFE_now >= activation(strategy, btc_z_bucket)
    AND cur_now <= MFE_now - offset(strategy, btc_z_bucket, hold_bucket)
```

Approche en 3 étapes incrémentales (la complexité ne s'ajoute que si la version plus simple ne passe pas) :

**A.1 — Grille globale (baseline strictement supérieure à `trailing_sweep`)**
- `activation ∈ {300, 500, 700, 1000, 1500} bps` — 5 valeurs (vs {300..800} du prior)
- `offset ∈ {100, 150, 200, 300} bps` — 4 valeurs (vs {100,150,200} du prior)
- **20 combos par stratégie**, mêmes params pour tous les régimes/hold_hours
- Si un combo passe 4/4 walk-forward strict → candidat A.1 retenu, on arrête là (parsimonie)

**A.2 — Conditionnement régime (seulement si A.1 échoue)**
- `btc_z_bucket ∈ {bear (z<-0.5), neutral (|z|≤0.5), bull (z>0.5)}` — 3 buckets
- Pour chaque bucket indépendamment : 20 combos `(activation, offset)`
- Le candidat final = concaténation des 3 meilleurs par bucket → 6 params par stratégie
- Filtre supplémentaire : **null shuffle obligatoire** (couche 2 §5) — sinon c'est du bruit

**A.3 — Conditionnement régime + hold (seulement si A.2 échoue)**
- `hold_bucket ∈ {early (<12h), mid (12-30h), late (>30h)}` — 3 buckets supplémentaires
- 9 buckets × 20 combos = **180 évaluations par stratégie**
- 18 params au total → risque overfit important
- **Parameter stability obligatoire** (couche 3 §5) : si les 9 (activation, offset) varient plus que 2× entre fenêtres walk-forward, rejet

Le but de l'incrémentalité : on n'ajoute des params QUE si la version plus simple n'a rien trouvé, et chaque palier a une validation anti-overfit plus stricte. C'est l'opposé d'une grille libre 4D.

### Famille B — Percentile empirique (zéro magic number)

Phase 1 (estimation) : sur le train set, pour chaque trade gagnant qui a touché MFE ≥ 300 bps, calcule `retracement_finale = MFE_peak - net_bps_final`. Bucketise par `(strategy, direction, hold_bucket, btc_z_quartile)`.

Phase 2 (règle) : à chaque pas 4h de la vie d'une position :
```
seuil_retracement = percentile_p(retracement_finale | bucket_courant)
exit si MFE_now - cur_now >= seuil_retracement
       AND MFE_now >= 300 bps  (filtre activation minimal)
```

Hyperparamètres :
- `p ∈ {70, 80, 90}` — 3 valeurs
- `min_MFE ∈ {300, 500} bps` — 2 valeurs
- Buckets : mêmes que famille A (cohérence)

Total : **6 combinaisons par stratégie**. Les seuils numériques eux-mêmes sortent des données — pas de choix arbitraire.

**Risque** : data-leakage si on bucketise sur des features qui dérivent de l'observation entière. Anti-fuite : les distributions empiriques sont calculées uniquement sur le **segment IS** de chaque walk-forward window (train ≠ test), pas sur les 3 ans entiers.

### Famille C — ML léger (logit + GBM léger)

Modèle : logistic regression (baseline) + sklearn `GradientBoostingClassifier(max_depth=3, n_estimators=50)`.

Features per-snapshot (calculées à chaque candle 4h pendant la vie d'une position) :
- Position : `MFE_now, MAE_now, cur_now, hold_h, time_since_MFE_peak_h, dMFE_per_h` (sur 12h glissants)
- Régime : `btc_z_30d, dispersion_24h, mean_corr_to_btc` (déjà loggés en v12.4.x)
- Per-token : `oi_delta_since_entry_bps, funding_now_bps, premium_now_bps`
- Catégorielles : `strategy_onehot, direction_onehot`

Cible (label) : `1 si cur_final < cur_now - 200 bps` (le trade va perdre plus de 200 bps depuis ce point), `0 sinon`. Cible asymétrique pour ne pas pénaliser les sorties au timeout proches du MFE.

Règle : à chaque snapshot, si `model.predict_proba(features)[1] > τ` → exit. Hyperparamètre `τ ∈ {0.55, 0.65, 0.75}`.

**Risque** : overfit massif possible (10+ features). Mitigations :
- Train sur IS du window, test OOS sur test segment du window (jamais sur 3 ans complets)
- Walk-forward 4/4 strict (4 modèles ré-entraînés indépendamment, params doivent généraliser)
- Null-shuffle sur `btc_z` : si modèle marche aussi bien avec régime mélangé, c'est du bruit
- Feature importance log (sklearn) : si une feature obscure dominate, suspect

---

## 4. Harness commun

### 4.1. Réutilisation existante

- `backtests.backtest_rolling.run_window(...)` : déjà accepte `trailing_extra` pour les hooks (S10 trailing). On ajoute un nouveau hook `inlife_exit_extra` analogue.
- `backtests.backtest_genetic.load_3y_candles()` : 3 ans de candles 4h, déjà cachés sur disque
- `backtests.backtest_genetic.build_features()` : features techniques + sector
- `load_oi()`, `load_funding()`, `load_dxy()` de `backtest_rolling`

### 4.2. Pseudocode du hook

```python
def inlife_exit_extra(pos, candle_idx, candles, features, ctx):
    """Appelé à chaque pas 4h pendant la vie d'une position.
    Retourne (should_exit: bool, reason: str) ou (False, "")."""
    # Calcule snapshot features pour cette position au pas courant
    snap = compute_snapshot(pos, candle_idx, candles, features, ctx)

    if RULE_FAMILY == "A":
        return rule_A_multifeature_trail(snap, params_A)
    elif RULE_FAMILY == "B":
        return rule_B_empirical_percentile(snap, distrib_B)
    elif RULE_FAMILY == "C":
        return rule_C_ml(snap, model_C, tau)

    return False, ""
```

### 4.3. Interaction avec exits existants

La nouvelle règle est **ADDITIVE**, jamais substitutive. Ordre d'évaluation préservé dans `trading.check_exits` :
1. Catastrophe stop (−1250 bps S5, −750 bps S8) — priorité absolue
2. Manual stop (si défini)
3. Trailing existant (S10 seulement)
4. **NOUVELLE règle in-life exit (S5, S8)** ← ajouté ici
5. Runner extension (S9)
6. Dead timeout (T−12h checks)
7. Natural timeout

Si la nouvelle règle déclenche avant le timeout naturel, elle remplace ce timeout. Sinon, comportement inchangé.

---

## 5. Validation anti-overfit (trois couches)

### Couche 1 — Walk-forward 4/4 strict

Standard du projet :
- Fenêtres : 28m / 12m / 6m / 3m (semi-overlapping)
- Critère : Δ PnL > 0 sur les 4 fenêtres → "robust"
- Critère DD : ΔDD moyen ≤ +1pp (soft, peut être négocié sur cas borderline)

### Couche 2 — Null shuffle (Famille A & C)

Pour les familles qui dépendent du régime (A : `btc_z_bucket` ; C : feature `btc_z_30d`) :
- 13 runs avec `btc_z` shuffled aléatoirement (préserve la distribution, casse la corrélation temporelle)
- La règle calibrée sur les vraies données doit battre la moyenne des 13 runs shuffled par ≥1σ
- Sinon : le signal est du bruit, on rejette la règle même si 4/4 sur les vraies données

Référence : approche validée en v11.10.0 (modulateur adaptatif).

### Couche 3 — Parameter stability (Familles A & B)

Pour les top-5 candidats de chaque famille :
- Re-optimiser les params indépendamment sur chaque fenêtre
- Les params optimaux par fenêtre doivent rester dans un facteur 2× du candidat global
- Sinon : le candidat est un point chanceux d'overfit, pas un signal stable

---

## 6. Scoring et sélection finale

### Score primaire (gate)

`is_robust = (Δ_PnL_28m > 0) AND (Δ_PnL_12m > 0) AND (Δ_PnL_6m > 0) AND (Δ_PnL_3m > 0)`

### Score secondaire (parmi les robust)

`composite = avg(Δ_PnL_pct) - λ_dd * max(0, avg(Δ_DD_pp) - 1) - λ_kill * max(0, kill_rate - 0.30)`

avec `λ_dd = 5`, `λ_kill = 10` (penalties hard sur DD>1pp et kill>30%).

### Sélection finale

1. Si famille A produit un candidat robust + null-shuffle + stable → ship A (parsimonie)
2. Sinon si famille B → ship B
3. Sinon si famille C → **NE PAS SHIPPER**, audit pour comprendre pourquoi seul ML passe (probablement overfit)
4. Sinon : rien à shipper, on documente le négatif (ça reste utile pour le futur)

---

## 7. Livrables

| Fichier | Rôle |
|---|---|
| `backtests/backtest_inlife_exit.py` | Harness principal (3 familles + validation) |
| `backtests/inlife_exit_results.md` | Résultats bruts (tables par famille, top candidats, null-shuffle, stability) |
| `docs/inlife_exit_findings.md` | Synthèse pour l'utilisateur — recommendation ship / no-ship + rationale |
| `backtests/__init__.py` | Pas de changement |
| Code prod | **AUCUN changement** dans cette tâche. Si un candidat est shipped, ce sera dans une PR séparée avec `/release` |

---

## 8. Hors scope (explicitement)

- Pas d'ajout de S1, S9 (interaction `runner_extension`), S10 (déjà trail)
- Pas de modification du `catastrophe_stop`, `dead_timeout`, `manual_stop` existants
- Pas de re-optimisation simultanée du sizing / leverage (orthogonal)
- Pas de live A/B test : seulement backtest, ship binaire si validation passe
- Pas de tuning de `runner_extension` S9 (orthogonal)

---

## 9. Risques connus et mitigations

| Risque | Mitigation |
|---|---|
| Overfit sur 3 ans (sample size limité) | Walk-forward strict + null shuffle + parameter stability |
| Famille C (ML) capture du bruit | Logit baseline, GBM peu profond, OOS sur chaque window, null shuffle obligatoire |
| Buckets `btc_z` x `hold` x ... → grille A trop fine | 3×3 = 9 buckets, params seulement (activation, offset) → 20/bucket reste raisonnable |
| Empirique B sur petits buckets | Bucket minimum 30 trades sinon merge avec bucket parent |
| Kill-rate trop haut tue le compounding | Hard penalty dans le scoring (λ_kill = 10) |
| Interaction avec `dead_timeout` v12.5.0 | Ordre d'évaluation préservé, dead_timeout passe avant nouvelle règle dans `check_exits` |
| Backtest qui passe mais live qui rate (slippage) | Cf. memory `project_slippage_ceiling.md` — la règle s'évalue sur la même grille candle 4h que les exits existants, slippage déjà inclus dans le baseline |

---

## 10. Définition du succès

- ✅ Au moins une famille passe les 3 couches de validation → recommendation ship dans `docs/inlife_exit_findings.md`, le ship effectif est une PR séparée
- ✅ Aucune famille ne passe → spec utile pour documenter ce qui ne marche pas, retour terrain documentés
- ❌ Bug ou erreur méthodologique détecté en cours → stop, audit, redesign

L'absence de candidat ship est un succès méthodologique tant que la conclusion est solide.
