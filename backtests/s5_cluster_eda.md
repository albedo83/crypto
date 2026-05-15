# S5 cluster EDA — résultats

_Generated 2026-05-15 — research only, no live impact. Script: `backtests/backtest_s5_cluster_eda.py`, artifacts: `backtests/s5_cluster_artifacts.json`._

## TL;DR

| | |
|---|---|
| Hypothèse testée | 2 ou 3 archétypes statistiquement séparables au entry pour S5, avec dynamiques de sortie distinctes |
| Données | `feature_modulator_dataset_r2.json` — 281 S5 LONG + 169 S5 SHORT (28m, modulator ON) |
| Features clustering | `entry_vol_z`, `entry_range_pct`, `entry_lead` (= `|ret_42h| / mean_peer_|ret_42h|`, proxy direct de la sector divergence) |
| Phase 1 — S5 LONG | **FAIL** (silhouette 0.442 OK, mais bootstrap ARI 0.627 < 0.65) |
| Phase 1 — S5 SHORT | **PASS** (silhouette 0.489, ARI 0.716) |
| Phase 2 — S5 SHORT | profils de sortie **non statistiquement distincts** (Mann-Whitney p_net = 0.180, Cliff's δ = +0.156) |
| **Verdict** | **YELLOW pour SHORT, RED pour LONG** — clusters partiellement stables mais cosmétiques (séparent les entrées sans changer la dynamique de sortie) |
| Recommandation | **Classer**. Pas de phase 3. Le bimodal documenté en v12.5.30 (`inlife_exit_results.md`) n'est pas capturé par un découpage cluster sur `vol_z / range_pct / lead` |

---

## Méthodologie

### Features au entry

| Feature | Source | Interprétation |
|---|---|---|
| `entry_vol_z` | `features.compute_features` | z-score du volume sur la fenêtre récente (intensité du flux) |
| `entry_range_pct` | `range_pct` (= `(high - low) / open × 10 000` bps) | volatilité instantanée de la bougie d'entry |
| `entry_lead` | `|ret_42h(coin)| / mean_peer_|ret_42h|` | force relative vs pairs du secteur — **proxy direct de la sector divergence** qui est la condition d'entrée S5 |

`sector_divergence` brut n'est pas persistée dans le dataset r2, mais `entry_lead` mesure la même chose (en ratio normalisé plutôt qu'en différence absolue) et capture la variance résiduelle au-dessus du seuil d'entrée S5.

Standardisation `StandardScaler` (centrage + variance unitaire) avant clustering — les trois features sont sur des échelles très différentes.

### Algorithmes et validation

- **KMeans** + **GMM (full covariance)**, K ∈ {2, 3, 4}, `random_state=42`, `n_init=10` (KM) / `n_init=5` (GMM)
- **Validation** :
  1. Silhouette score (gate ≥ 0.30)
  2. BIC (GMM) pour signaler un plateau au "bon" K
  3. Bootstrap ARI : 50 ré-échantillonnages 80%-avec-remise, ré-ajustement, ARI vs labels du fit complet sur les indices ré-échantillonnés (gate ≥ 0.65)
- **Gate phase 1** : silhouette ≥ 0.30 ET ARI ≥ 0.65 — les deux

### Phase 2 — dynamique de sortie

Pour chaque cluster du meilleur K : moyennes/médianes de `net_bps`, `mfe_bps`, `mae_bps`, `hold_hours`, distribution de `exit_reason`, et WR (`net > 0`). Comparaison entre clusters par Mann-Whitney U (bilatéral) et Cliff's δ.

`mfe_held_h` (temps inside-trade pour atteindre le MFE) n'est pas dans le dataset r2 — non comparé.

---

## Phase 1 — résultats

### S5 LONG (n=281)

| K | KMeans silhouette | GMM silhouette | GMM BIC | Tailles (KMeans) |
|---|---|---|---|---|
| 2 | **0.442** | 0.390 | 2 075 | 230 / 51 |
| 3 | 0.440 | 0.392 | 2 065 | 203 / 43 / 35 |
| 4 | 0.397 | 0.252 | 2 066 | 178 / 61 / 33 / 9 |

- Meilleur : **KMeans K=2** (silhouette 0.442).
- BIC `Δ(K=2→3) = 10` (gain marginal) puis `Δ(K=3→4) = −2` (plateau) — cohérent avec K ≈ 2-3.
- **Bootstrap ARI = 0.627** — sous le seuil 0.65. Cluster 0 (n=51, ~18%) est petit et instable au re-fit.

→ **Phase 1 LONG : FAIL** (gate ARI).

### S5 SHORT (n=169)

| K | KMeans silhouette | GMM silhouette | GMM BIC | Tailles (KMeans) |
|---|---|---|---|---|
| 2 | **0.489** | 0.405 | 1 270 | 139 / 30 |
| 3 | 0.365 | 0.288 | 1 198 | 104 / 47 / 18 |
| 4 | 0.372 | 0.298 | 1 205 | 102 / 40 / 16 / 11 |

- Meilleur : **KMeans K=2** (silhouette 0.489).
- BIC continue de baisser à K=3 (`Δ = 72`) mais la silhouette s'effondre — K=3 surfit géométriquement sans gagner en lisibilité.
- **Bootstrap ARI = 0.716** — au-dessus du seuil 0.65.

→ **Phase 1 SHORT : PASS**. On passe en phase 2 sur S5 SHORT uniquement.

---

## Phase 2 — dynamique de sortie (S5 SHORT uniquement)

### Profils par cluster

| Cluster | n | WR | net mean (bps) | net median (bps) | MFE mean | MAE mean | hold mean (h) | reasons |
|---|---|---|---|---|---|---|---|---|
| 0 ("burst") | 30 | 56.7% | **+247** | +218 | +853 | −364 | 45.5 | timeout 24 / stop 3 / dead_timeout 3 |
| 1 ("régulier") | 139 | 54.0% | +66 | +56 | +604 | −522 | 45.4 | timeout 118 / stop 10 / dead_timeout 11 |

### Profils d'entry par cluster

| Cluster | entry_vol_z | entry_range_pct | entry_lead | entry_ret24h_abs | entry_drawdown_abs |
|---|---|---|---|---|---|
| 0 ("burst") | **4.39** | **844** | **2.62** | 749 | 2 860 |
| 1 ("régulier") | 1.76 | 421 | 1.01 | 414 | 2 312 |

→ Cluster 0 = entry haute énergie (vol_z >3, range >800 bps, leadership 2.6× peers). Cluster 1 = la moyenne.

### Test statistique pair (0, 1)

| Variable | Mann-Whitney p (bilatéral) | Cliff's δ | Magnitude |
|---|---|---|---|
| `net_bps` | 0.180 | +0.156 | négligeable (\|δ\|<0.2) |
| `mfe_bps` | 0.081 | — | marginal |
| `hold_hours` | ≈ 0.99 | ≈ 0 | identique |

- **Aucune comparaison ne passe p < 0.05.**
- **Cliff's δ sous le seuil 0.2** sur net_bps.
- WR quasi identique (56.7% vs 54.0%, écart de 2.7pp sans signification stat).
- Distribution `reason` : même mix (timeout dominant, stop minoritaire, dead_timeout minoritaire).
- **hold_hours médian identique** (~45h) — les deux clusters atteignent leur sortie au même rythme.

### Verdict phase 2

**YELLOW** : les clusters sont stables (phase 1 PASS) mais leurs profils de sortie se recouvrent au-delà de la noise threshold (Cliff's δ < 0.2, MW p > 0.05). On distingue le **point d'entrée** (cluster 0 = entrée plus violente) mais pas la **trajectoire de sortie**.

---

## Interprétation mécanique

**Ce que les clusters représentent vraiment** : ils séparent les entries S5 par **intensité de l'événement déclencheur** (volume + range + leadership relatif). Cluster 0 = "burst" — leg parabolique, volume spike, écart massif vs peers. Cluster 1 = "régulier" — divergence sectorielle modérée, vol_z proche de 1.

**Pourquoi les sorties ne diffèrent pas** : une fois la position S5 ouverte, l'évolution sur la fenêtre de hold (48h) est dominée par des facteurs **post-entry** non capturés par ces trois features statiques :

1. Régime macro (BTC z-score) — déjà modulé en v11.10.0 puis v12.2.0 sur la sizing
2. Réintégration ou pas de la dispersion sectorielle pendant le hold
3. Mouvements idiosyncratiques du token

Les "gigantesques winners" et les "modestes" évoqués en v12.5.30 sont **mélangés dans chaque cluster**. C'est cohérent avec le constat de `inlife_exit_results.md` : "S5 unsolved by every family — left untouched. Mechanically: S5 winners bimodal".

Autrement dit : le bimodal des outcomes S5 existe bien, mais il **n'est pas prédictible depuis l'entry-state seul**. Le découpage par K-Means/GMM sur `vol_z / range_pct / lead` produit des clusters cosmétiques.

### Note sur S5 SHORT cluster 0

Cluster 0 SHORT a un net_mean de **+247 bps** vs +66 bps pour cluster 1. Avec n=30 seulement et p=0.18, ce gap n'est pas significatif. Mais le hint directionnel est cohérent avec une intuition de marché : un short S5 sur token en burst extrême (vol_z=4.4, lead=2.6) est plus susceptible de mean-reverter violemment — sans pour autant que la moyenne sorte du bruit échantillon. À surveiller en obs-only si N croît (>100 cluster-0 trades en live), mais **non actionnable aujourd'hui**.

---

## Recommandation

**Classer.** Pas de phase 3 (`backtest_s5_cluster_split.py`).

Raisons :

1. **LONG fail gate stabilité** — le cluster minoritaire n'est pas robuste au resampling, l'edge potentielle ne survivrait pas walk-forward
2. **SHORT pass gate mais cosmétique** — les profils de sortie se chevauchent, un split d'exit-rule n'aurait rien à séparer
3. **Pas d'effet size suffisant** (Cliff's δ < 0.2 partout) — même un sweep agressif n'a rien à viser

Approches futures qui pourraient mieux saisir le bimodal S5 :

- **Trajectoire intra-position** (au lieu d'entry-only) : MFE/MAE à T+4h, T+12h, T+24h comme features → "early signature" du destin de la trade. Demande de capturer les snapshots intermédiaires dans `backtest_rolling.py` (proche du dispositif `inlife_exit` Family B).
- **Cross-sectional contemporain** : dispersion sectorielle au cours du hold (pas seulement à entry). Si la div continue de s'élargir = leg parabolique en cours, sinon = réintégration imminente.
- **Modèle supervisé direct** : au lieu de clusters non-supervisés, fit un classifier (logit / GBM) sur la binarisation `mfe_bps > 1500` (winner significatif). Plus efficace pour identifier l'edge si elle existe, même sans cluster structure propre. Note : Family C de `inlife_exit_results.md` l'a essayé sur S5 et n'a rien sorti.

---

## Caveats

- **Une seule fenêtre** : `feature_modulator_dataset_r2.json` couvre 28m. Pas de validation OOS sur une autre fenêtre (3m / 6m / 12m). C'est cohérent avec une EDA exploratoire ; un protocole walk-forward serait nécessaire pour phase 3.
- **N=30 pour SHORT cluster 0** : marge d'erreur élevée. Le hint directionnel pourrait disparaître ou s'amplifier avec plus de données.
- **`sector_divergence` brut absent** : remplacé par `entry_lead` (ratio normalisé). Si la version brute (différence absolue token vs sector_mean) clustère mieux, ce ne sera pas visible ici — peu probable car les deux mesurent la même chose, mais à noter.
- **3 features seulement** : un espace 3D restreint. Tester sur un superset (10 features de `entry_feats`) avec PCA n'a pas été fait — restait dans le scope phase 1 minimal.
- **Modulator ON only** : le dataset `trades_on` reflète la sizing post-v11.10.0/v12.2.0. La `pnl` est affectée par cette sizing, mais `net_bps` (le bps de la trade pure) ne l'est pas — clustering valide.

---

## Pointeurs

- `backtests/backtest_s5_cluster_eda.py` — script
- `backtests/s5_cluster_artifacts.json` — labels, silhouettes, BIC, ARI, profils par cluster, comparaisons MW
- `backtests/inlife_exit_results.md` — contexte v12.5.30 : "S5 unsolved by every family"
- `backtests/feature_modulator_dataset_r2.json` — dataset source (généré par `backtests/backtest_rolling.py`)
