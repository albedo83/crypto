# Backlog — tests, analyses et refactors différés

Ce fichier est l'index canonique de ce qu'il reste à creuser. Chaque item :
- **quoi** : la question à répondre ou le travail à faire
- **pourquoi** : la motivation et l'origine (review, mémoire, expérience)
- **quand** : prêt maintenant / date de revisite / condition de déclenchement
- **comment** : script à lancer, query à écrire, ou pointeur vers le code

Mettre à jour au fil de l'eau. Quand un item est traité → le supprimer et logger le résultat dans `CHANGELOG.md` ou `docs/synthese.md`.

---

## 1. Section close — 4 features observation-only testées 2026-05-11

Analyse rétrospective via `backtests/analyze_obs_features.py` puis hypothèses fortes testées en walk-forward 4/4 strict :

- ❌ **`entry_session` (WE)** — rétrospectif fort (WE n=22 avg −$3.12, WR 36%). Walk-forward `WE skip S5 LONG only` à **3/4** (ΔDD +0.39, à 0.11pp du seuil), le 12m casse. **Re-tester ~2026-08-11** : si la fenêtre 12m glisse vers une période moins négative, ça pourrait basculer 4/4.
- ❌ **`entry_oi_delta` SHORT gate** (mirror v11.4.9) — rétrospectif fort (n=5 S5 SHORT avg −$8.25). Walk-forward **0/4** toutes thresholds (+5% à +25%). Pattern = bruit. **Classer définitivement.**
- ❌ **`entry_crowding`** — rétrospectif WEAK (spread $3.62). Walk-forward **0/6** configs (skip crowd<1, <20, <30 testés). Toutes les configs détruisent le pnl par −300k+ pp. **Classer définitivement.**
- ❌ **`entry_confluence`** — rétrospectif WEAK (spread $5.69). Walk-forward **0/6** configs (skip conf=0, conf=1, conf<2). Pareil, destruction massive du pnl. **Classer définitivement.**

**Conclusion section** : sur 4 features × ~15 gates testés, **un seul (WE skip S5 LONG only) atteint 3/4 strict, à 0.11pp du seuil ΔDD**. Les 3 autres features ne survivent pas au walk-forward. Re-tester WE skip à 2026-08-11 ; les autres sont enterrées.

---

## 2. Analyses en attente de données suffisantes (forward validation)

- [x] ~~**`basket_metrics` × drawdown** — instrumenté 2026-05-11.~~ **Testé 2026-05-15 (gate ET haircut continu) : NÉGATIF, classer.** Voir `backtests/basket_haircut_eda.md`, commits `86a0d3e` → `8ed2253`. Premise "low eff_n → upcoming DD" non soutenue par les données (à l'onset DD, eff_n est +0.10 PLUS ÉLEVÉ que baseline, MW p≥0.73 sur 3 fenêtres 7/14/30j). Spearman normalisé `|ρ|≤0.06`. Sweep 27 configs (3 windows × 3 EFFN_REF × 3 MIN_HAIRCUT) : **0/27 passent acceptance** (A: ΔPnL ≥-5% ET ΔDD ≥+5pp 4/4 ; B: ΔCalmar ≥+15% 4/4). Best config (30d/REF=6.0/MIN=0.5) délivre avg ΔCalmar = -47% : haircut détruit 50-80% du PnL pour seulement +0 à +20pp de DD reduction. Cause racine : (1) DD piloté par mouvements idiosyncratiques sur positions à conviction élevée (S8/S9), pas par corrélation panier ; (2) eff_n est trop stable (rarement sous 0.6 × REF) → haircut tape la quasi-totalité des entrées non sélectivement ; (3) modulator macro v11.10.0 déjà actif → haircut multiplicatif par-dessus réduit massivement le compounding. **Instrumentation live gardée pour monitoring/alerting (sa raison d'être actuelle)**, mais aucune piste de gate ou de haircut à creuser. Voir mémoire `project_basket_correlation_review.md` (updated).
- [ ] **`entry_side_imbalance` × slippage/pnl** — instrumenté 2026-05-11. Revisiter ~**2026-08-11** (3 mois) ou ~**2026-11-11** (6 mois). Re-lancer `backtests/backtest_l2_imbalance.py` une fois qu'on a 200+ trades avec ESI loggé en OPEN events. Le signal rétrospectif sur 75 trades était fort (+17 bps slippage défavorable vs favorable) mais bruité, à confirmer.
- [ ] **Modulator 2D** (`mult = 1 + α×btc_z + β×disp_z`) — testé walk-forward 4/4 strict 2026-05-11 via `backtests/backtest_modulator_2d.py` sur 14 configs. **Aucune ne passe 4/4 strict** : 5 configs atteignent 3/4, toutes négatives sur la fenêtre 3m (régime récent ne récompense pas l'extension). Mais la magnitude positive sur 28m/12m/6m est énorme (β[S5,S9,S10]=+0.5 : +59597pp / +3330pp / +67pp, ΔDD −9pp). **Re-tester ~2026-08-11 (3 mois)** : si le 3m négatif d'aujourd'hui était du bruit, l'extension repassera 4/4 dans une fenêtre future. Si c'était un shift durable, elle ne repassera jamais et on classe définitivement.
- [ ] **Funding amplifier** (`mult += γ × funding_z × (-dir_sign)` avec robust z-score MAD-based) — testé walk-forward 2026-05-11 via `backtests/backtest_funding_amplifier.py` sur 12 configs (γ sweep sur S9, S5, S8, S10, ALL). **0/12 pass strict** mais **γ[S8]=+0.5 atteint 3/4** avec pattern inversé : seul 28m négatif (-28k%), 12m/6m/3m tous positifs (+59/+21.5/+2.4 pp), ΔDD favorable (-0.78pp). Mécaniquement S8 = capitulation LONG amplifié quand funding très négatif (shorts en panique) — intuition propre. **Re-tester ~2026-08-11** : si la fenêtre 28m cassante glisse de 3 mois, ça pourrait basculer 4/4.
- [ ] **Target volatility sizing** (`size × clip(vol_ref / max(vol_cur, 30 bps), MIN, MAX)`) — testé walk-forward 2026-05-11 via `backtests/backtest_target_vol_sizing.py` sur 12 configs (clamp ranges × strat filters). **0/12 pass strict**. Un candidate DD-friendly : `clip [0.7-1.5] S5 only` (+19251pp avg PnL, -1.71pp avg DD, 2/4 — 28m/12m positifs énormes, 6m/3m négatifs légers). La thèse "DD baisse, PnL stagne" n'est pas validée : tradeoff DD/PnL pas favorable sur ce strat-set. **Re-tester ~2026-08-11** S5-only en particulier.
- [ ] **`S9F_OBS` hit rate** — événements ±3%/2h loggés. Besoin de 6+ mois de données pour évaluer si la fenêtre rapide a un edge.
- [ ] **`ETH_OBS` S8 hit rate** — actuellement n=0 trades pris (observation-only sur ETH). Besoin de n≥10 triggers S8 pour valider.

---

## 3. Hypothèses à re-tester quand le contexte change

- [ ] **ML pour WR estimator (XGBoost / régression logistique)** — rejeté à 87 trades (insuffisant). Re-considérer à **300+ trades fermés** (~mi-2027 au rythme actuel).
- [ ] **Maker post-only execution workflow** — rejeté à $500-1000 capital (gain théorique ~$5/mois). Re-considérer à **capital >$15k** par bot (mémoire `slippage_ceiling`).
- [ ] **WR auto-close** — testé et rejeté au walk-forward (le seuil WR<25% qui ferme auto perd). A re-tester avec 200+ trades dans le DB d'historique pour voir si signal stabilise.
- [ ] **S5 directional flip en bull** — testé et rejeté (régime-dépendant, sliding OOS pas stable). Variante directionnelle traitée v12.2.0 par modulator α. Surveiller si une fenêtre future justifie de re-tester.
- [ ] **Feature modulator continu per-trade — S1 LONG × `entry_n_stress`** — EDA 2026-05-15 (rounds 1+2, commits `e9ac7f3` → `9da1d3b`, rapports `backtests/feature_modulator_eda*.md`). Sur 12 features candle-based × 10 (strat, dir) testés Spearman + null-shuffle 200× + Bonferroni, **1 seul candidat strict** : S1 LONG × `entry_n_stress` (count alts en stress à l'entrée) — ρ_net=+0.380 / ρ_pnl=+0.365 (modulator OFF), tercile low n=49 mean_net=−152 bps vs high n=24 mean_net=+1006 bps. Mais : passe strict-strict uniquement sous modulator OFF, **n=73 sur 28m / n=0 sur 12m** (S1 dormant depuis 12 mois → out-of-sample impossible), interaction avec v11.10.0 α=+0.5 à designer. **Frigo** : re-tester quand S1 aura repris activité et qu'on aura 30+ trades supplémentaires (typiquement reprise BTC bull marquée). Le suspect du round 1 (S1 Asia) a été disambiguated : pas un artefact de taille mais ρ_net failed Bonferroni → rejeté par filtre strict.
- [x] ~~**S5 LONG in-life exit à T+8h** (variant `strong` mfe<50 & pain≥50 ; variant `triple_mid` mfe<300 & pain>60 & sector_div_delta<−500)~~ — **Testé 2026-05-15 : classer.** Voir `backtests/s5_dead_t8h_walkforward.md`, commits `4a899af` → `ef36b2c`. EDA in-sample (`mid_trade_profiling_eda.md`) avait identifié à T+8h une signature "dead trade walking" robuste sur 28m (n=72/281 cuts, WR=13.9%, savings=+108 bps, null-shuffle z=−6.41). Walk-forward strict 4/4 : **strong RED (1/4, 28m=+6 956pp, 12m=−2 185, 6m=−71, 3m=−25), triple_mid RED (1/4, 28m=+93 539pp, 12m=−260, 6m=−70, 3m=−3)**. ΔDD avg favorable sur les deux (+0.51 / +0.08pp). Triple_mid domine strong sur tous les axes (28m ×13.4 gain, smaller misses on small windows, gentler DD) mais aucun ne survit le strict. Le 28m énorme isole une edge réelle long-historique mais qui ne se réplique pas sur 12m/6m/3m → S5 LONG bimodal (winners à long-tail) coupé trop tôt sur fenêtres récentes. Cohérent avec inlife_exit_results.md Family A/B/C : "S5 has no clean trailable structure". Parity check passé (4/4 bit-identique baseline). **Pistes futures non testées (n'ouvrir que si demande explicite)** : (1) regime-conditioned triple_mid par btc_z (mirror v12.5.30 S8 trail), (2) T+12h checkpoint au lieu de T+8h, (3) null-shuffle sur courbe equity 28m pour confirmer signal vs noise.
- [x] ~~**S5 cluster split (S5_A / S5_B sur (vol_z, range_pct, lead))**~~ — **Testé 2026-05-15 : classer.** Voir `backtests/s5_cluster_eda.md`, commits `767df01` → `c283810`. K-Means + GMM sur 281 LONG + 169 SHORT, K∈{2,3,4}. **LONG fail bootstrap stability** (silhouette 0.442 OK mais ARI=0.627 < 0.65 → cluster minoritaire instable). **SHORT pass gate stabilité** (silhouette 0.489, ARI=0.716) mais **phase 2 cosmétique** : profils sortie quasi-identiques entre clusters (Mann-Whitney p=0.180 sur net_bps, Cliff's δ=+0.156 < 0.2, WR 56.7% vs 54.0%, hold_hours médian identique ~45h, distribution exit_reason identique). Conclusion mécanique : les 3 features statiques (vol_z, range_pct, lead) clusterent bien les **entrées** (cluster 0 "burst" = vol_z=4.4 / range=844 bps / lead=2.6 vs cluster 1 "régulier") mais **pas les trajectoires** — le bimodal S5 documenté v12.5.30 est piloté par des facteurs post-entry (régime macro, réintégration dispersion sectorielle, mouvements idiosyncratiques) non prédictibles à partir de l'entry-state seul. **Pistes futures (non lancées) qui pourraient mieux capturer le bimodal** : trajectoire intra-position (MFE/MAE à T+4h/T+12h comme features), dispersion sectorielle contemporaine pendant le hold, classifier supervisé sur `mfe_bps > 1500` (note : Family C de `inlife_exit_results.md` l'a déjà essayé sans succès).
- [x] ~~**S9 dead-in-water (mirror v12.6.0 S8 sur S9 LONG+SHORT à T+4h et T+8h)**~~ — **Testé 2026-05-16 : classer**, 0/4 variantes passent strict 4/4. Voir `backtests/s9_dead_in_water_walkforward.md`. Variantes : A (SHORT T+8h) 1/4, B (LONG T+8h) 0/4, C (SHORT T+4h) **2/4 mais ΔPnL avg=−35787pp**, D (LONG T+4h) 0/4. **Sample sizes minuscules** : à T+8h la rule fire 2-4 trades sur 28 mois entiers (S9 = soit MFE>50 vite par mean-reversion, soit current<−500 → S9_EARLY_EXIT déjà actif). À T+4h plus actif (13 cuts SHORT) mais détruit le PnL (confirme EDA mid-trade 2026-05-15 : T+4h SHORT savings=−$23). **Mécanique pourquoi** : S9 fade ±20% a un délai de reversal 8-24h, pas immédiat comme S8 capitulation. Couper à T+4h ou T+8h sur S9 = locker des pertes qui auraient retracé. Le dead-in-water est **mécaniquement spécifique à S8** (rebond immédiat attendu). S9 est déjà protégé par `S9_EARLY_EXIT` (T+8h current<−500) et `S9_ADAPTIVE_STOP` — pas besoin d'un 3e mécanisme.
- [x] ~~**S5 LONG mid-trade × disp_7d gate (T+8h, T+12h)**~~ — **Testé 2026-05-28 : classer**, 0/3 variantes passent strict 4/4. Voir `backtests/s5_disp_inlife_walkforward.md`, scripts `backtests/eda_s5_unexplored*.py` + `backtests/backtest_s5_disp_inlife.py`. EDA in-sample (consolidée T+8h∪T+12h, dédup par trade_id, null-shuffle 400× sur disp_7d bucket) avait identifié **1 candidat strict** : R1 = mfe<50 & pain≥50 & disp_7d≥700 @ T+8h, n=38, WR=13.2%, savings=+196 bps, **z=+3.43** (signal le plus fort jamais mesuré sur S5 mid-trade). R2 = triple_mid + disp gate (n=22, z=+2.76) near-miss. Walk-forward strict 4/4 sur 3 variantes : **R1 RED 2/4** (28m −72 815pp, 12m −1 923, 6m +91, 3m +2 ; ΔDD avg −0.11pp), **R2 RED 1/4** (28m −22 092pp, 12m −298, 6m 0, 3m +0.9 ; ΔDD avg −0.96pp), **R3 RED 2/4** (R1 + mae≤−500 super-strict ; ΔDD avg −1.27pp). Mécanique : la signature trouvée par l'EDA mesure mean savings arithmétique (+196 bps/cut × n=38 ≈ +7 448 bps cumulés) mais le S5 LONG est bimodal avec long-tail winners — couper 5/38 winners (13.2% WR in-sample) au MAE détruit le compounding géométrique sur les fenêtres longues. **Pattern symétrique** : les 3 variantes réduisent toutes le DD (−0.11 à −1.27 pp avg, jusqu'à −4.28 pp sur 12m R1) mais détruisent le PnL. Confirme et étend les conclusions de `s5_dead_t8h_walkforward.md` et `inlife_exit_results.md` (Family A/B/C). **Memo R&D** : EDA mean-savings ≠ compounded edge sur distribution bimodale, ajouter geometric sanity check avant lancer walk-forward. **Pistes futures non explorées** (n'ouvrir que si demande explicite) : in-trade BTC price delta, in-trade OI delta, in-trade funding flip — toutes manquent dans les snapshots actuels et nécessiteraient une nouvelle infrastructure de logging mid-trade.
- [x] ~~**MAX_POSITIONS sweep {6,7,8,9}**~~ — **Testé 2026-05-16 : classer**, MAX_POSITIONS=6 reste optimal. Voir `backtests/max_positions_sweep_results.md`. Sub-caps fixes (M=3, T=4) → effective cap = 7 (3+4) donc 7=8=9 identiques. Seul 28m gagne (+57k pp absolu) sur cap=7, 12m/6m/3m identiques à 6 (la cap ne binde jamais sur ces fenêtres récentes). Strict 1/4 → rejet. MAX_POSITIONS=6 confirmé empiriquement comme optimum local.
- [x] ~~**2D sweep slots (M × T × P + dir/sector dims)**~~ — **Testé 2026-05-16 : 0/9 PASS**, baseline v12.6.3 (M=3, T=4, P=6, SD=4, PS=2) confirmée comme optimum Pareto. Voir `backtests/slots_2d_sweep_results.md`. Observations intéressantes pour futurs sweeps : (1) **`dense_macro` (M=4, T=3, P=7)** réduit le DD 28m de 11.6pp (−74.3% → −62.7%) mais casse PnL sur fenêtres récentes — piste potentielle si on optimise DD au prix de PnL ; (2) **`balanced_4_4_8`** rate 4/4 de très peu (28m+12m énormes gains, 6m/3m légèrement négatifs à −45pp/−17pp) — à re-tester ~2026-08-16 si la fenêtre 6m glisse vers une période moins négative ; (3) `rollback_v12.6.2` (M=2) est strictement pire que baseline (−344k pp 28m) → confirme l'amélioration v12.6.3 ; (4) `loose_sector_3` (PS=3) améliore le DD 28m beaucoup mais détruit le PnL → trades dans même secteur souvent corrélés perdants.

---

## 4. Refactors du code review 2026-05-11 — section close

Tous traités le 2026-05-11 :

- ✅ **m4** — `_scan_and_trade` passé de 130 à ~30 lignes. Extraction de `_build_token_signals` et `_log_eth_observations` comme méthodes du bot. Pas de changement de comportement.
- ✅ **m6** — méthode `bot.close_and_check(sym, exit_price, now, reason) -> bool` ajoutée. Encapsule le check `_failed_closes`. Les 2 callers concernés (`api_close_symbol`, `api_pause`) utilisent maintenant cette méthode au lieu d'inspecter `_failed_closes` directement.
- ✅ **m7** — fetch_position_funding désormais time-boxé à 5s via `ThreadPoolExecutor.future.result(timeout=)`. Si HL ralentit, le close path ne hang plus — fallback fail-open existant préservé. Version douce du pattern async UPDATE (jugée plus invasive pour gain marginal vu la fréquence de close ~50/mois).

---

## 5. Idées de fond (roadmap)

Items de mémoire long-terme, sans deadline.

- [ ] **Token rotation auto-screening** — actuellement les 28 tokens sont hardcodés dans `TRADE_SYMBOLS`. Mémoire `project_token_rotation`. Idée : scoring périodique par volume/spread/historical edge, drop les pires, ajoute les nouveaux liquides.
- [ ] **DD reduction sans tuer le compounding** — mémoire `project_dd_reduction`. Pistes : sizing dynamique par régime, levier variable, regime filter explicite (au-delà du modulator existant).
- [ ] **Native HL stop orders pour `manual_stop_usdt`** — actuellement le déclenchement est polling-based (cadence 20s post-v12.16.3, était 60s). Sur un alt volatil le prix peut traverser le seuil entre 2 ticks → slippage de réaction. Cible : à chaque `POST /api/manual_stop`, le bot place aussi un ordre conditionnel `Stop` sur Hyperliquid. Si le prix touche, l'exchange close instantanément, le bot réconcilie au scan suivant. Nécessite : SDK call pour conditional orders, stockage `order_id` dans `Position`, cleanup à la fermeture/changement de stop. Effort 1-2j. Décision prise 2026-06-07 après que l'utilisateur ait flag la latence sur GMX manual_stop $0.50.
- [x] ~~**MKR mort depuis ~09/2025**~~ — **RETIRÉ à l'acte de la phase 6 (2026-06-10)** : sorti de `alfred/settings.py` (trade_symbols + sectors) et de `backtests/backtest_genetic.TOKENS`. Impact chiffré : -93pp sur 28m (path dependence pré-09/2025), no-op 6m/3m (déjà mort). Le legacy config.py garde MKR (inerte, legacy en fin de vie). Piste restante : évaluer SKY en remplacement via le screener universe_expansion.

---

## 6. Cadence des analyses récurrentes

- **Strategy drift monitor** : auto via cron, **chaque lundi 08:00 UTC** (Telegram). Pas d'action manuelle requise sauf si une alerte arrive.
- **Supervisor** : auto via cron, **chaque jour 08:00 UTC** (Telegram). Lecture passive.
- **Régénérer `docs/backtests.md`** : manuel, après chaque changement de paramètre dans `config.py`. Commande : `python3 -m backtests.backtest_rolling`.
- **Re-lancer `backtest_l2_imbalance.py`** : manuel, **trimestriel** tant que la thèse OBI n'est pas tranchée.
- **Analyser les features `entry_*`** (section 1) : **avant fin 2026**.

---

## Comment je m'en sers

- Sessions Claude : lire ce fichier en début de session si le sujet est de la R&D ou un refactor. Pas besoin de le lire pour une intervention ciblée (bug fix, ajout simple, restart).
- Sessions humaines : checklist quand on se demande "qu'est-ce qu'il restait à faire ?".
- Quand on traite un item → suppression + mention dans `CHANGELOG.md` ou `docs/synthese.md` selon que c'est trading-impacting ou doc-seulement.
- [ ] **Re-test cap notionnel liquidity-aware** (R&D 2026-06-11, `backtests/backtest_liquidity_cap.py`) — $500 confirmé optimal au genou de la frontière (grille flat 250-1000 + 12 variantes k_liq×vol24h, 0 PASS strict, régime bear punit la taille marginale). Re-run (~3 min) si : balance SENIOR ≥ $1400 (alerte auto dans le digest 08:30), btc_z 30j durablement > 0, ou échéance trimestrielle (~2026-09). Option $350 documentée pour l'agenda DD-reduction (−9pp DD moyen contre −27% PnL long).
- [x] **Re-tests issus de l'audit d'ablation 2026-06-11** : ✅ TRAITÉ 2026-07-02
  (`backtests/chantier4_exit_chain_audit.py`, résultats
  `backtests/chantier4_exit_chain_results.md`). Les 3 suspects SURVIVENT :
  whitelist S10 KEEP (retrait 3/7 glissant, mirage single-end-date) ;
  dead_timeout KEEP (morte au BT — shadowée — mais filet live réel, PYTH
  06-19) ; runner_ext KEEP dormante (0 tir live JAMAIS, retrait 5/7 glissant
  mais 2/4 strict — pari de régime). Prochain re-audit ablation : ~2026-09.

---

## Idempotence des ordres live sur retry 429 (hl#1, revue 2026-06-14)

- **quoi** : `_sdk_retry` (alfred/hl.py) retente les ordres `market_open`/`market_close`
  sur une HTTP 429. Si HL fille puis renvoie 429, l'ordre est re-soumis →
  position en double. Pas de clé d'idempotence (`cloid`).
- **pourquoi** : finding HIGH de la revue de code multi-agents. Réel mais
  **faible probabilité** (HL renvoie 429 surtout AVANT process, pas après fill)
  et le reconcile horaire détecte le mismatch de taille. Non corrigé à chaud :
  toucher la soumission d'ordres réels est risqué, et le fix propre demande de
  vérifier le support `cloid` du SDK + un test soigné.
- **quand** : prêt, mais à traiter dans une passe dédiée (pas en urgence).
- **comment** : (a) passer un `cloid` déterministe à `exchange.market_open/close`
  si le SDK le supporte (HL dédupe alors) ; OU (b) sur 429 d'un open, checker
  `user_fills_by_time` AVANT de re-soumettre, et abandonner si un fill a déjà
  atterri. Tester en paper-équivalent / dry-run avant tout déploiement live.

---

## Réservation de budget marge pour stratégies high-z (slot priority, 2026-06-14)

- **quoi** : à petit/moyen capital, les S8/S9 (haute espérance) sont souvent
  skippés par saturation de MARGE quand les slots sont pleins de S5 (premise
  EDA confirmée : take-rate S8/S9 ≈ 23% sur 3m / 41% sur 6m à $500). Idée :
  réserver une fraction du budget de marge aux high-z (S8/S9/S1) — un candidat
  low-z (S5/S10) ne peut consommer que (1-frac) du budget.
- **résultat** : walk-forward 7 tranches glissantes 6m, $500, margin ON.
  frac 25% = meilleur (+$764 agrégé, ΔDD +3.33pp = DD meilleur) MAIS **4/7
  seulement**, perd sur la tranche la plus récente (2025-12→06 −$66). 15% =
  +$301/4-7/DD pire. 40% = −$708/3-7. **Edge régime-dépendant, PAS strict 4/4
  → non shippé.** (NB : la priorité intra-scan S8/S9>S5 existe déjà via le
  tri par z — bot:580 + BT:1346. Ce test ne porte que sur la réservation
  cross-scan.)
- **quand** : re-tester si le capital grossit (à $1000 la contention chute :
  take-rate S8/S9 ≈ 77-100% sur les fenêtres longues) OU régime-conditionner
  la réservation (réserver seulement en bear, où S8/S9 sont amplifiés/valent
  le plus) — piste non testée.
- **comment** : hook `reserve_highz_frac` + `reserve_z_threshold` dans
  `run_window` (off par défaut), instrument `skip_log` pour compter les skips
  par stratégie/raison. Harness : voir l'historique de cette session.

---

## Cap notionnel PLUS BAS pour libérer les slots des petits comptes (2026-06-14)

> ✅ **TRANCHÉ 2026-06-17** — walk-forward lancé à $80/$100 (cap ∈ {500,50,40,30},
> `backtests/backtest_small_cap_notional.py`). Mécanique confirmée (le cap bas
> libère les slots : margin-skips 4328→121, S8+S9 155→232) et réduit le DD, MAIS
> rogne le PnL sur les fenêtres de tendance → **aucune config ne passe strict 4/4**
> (PnL bloquant). Décision : nouveau bot `baby` (<$100) déployé en sizing STANDARD
> sans override. Détails : `backtests/small_cap_notional_results.md`.

- **quoi** : le cap notionnel ($500) ne mord qu'au-dessus de ~$850 de capital
  (un S5 = 0.18×cap×3.25 atteint $500 à cap≈$855). En dessous il est DORMANT —
  inactif pour junior ($333, S5=$195) et senior ($680, S5=$398). Or c'est le cap
  qui libère les slots (en rapetissant les S5 → place pour S8/S9). Idée : un cap
  plus bas ($250→mord à ~$430 ; $300→~$510) clipperait les S5 de senior/junior
  DÈS MAINTENANT → diversification + capture S8/S9 à petit capital. Serait le fix
  propre que la réservation-marge cherchait (cf. [[slot-priority-2026-06]], 4/7).
- **pourquoi** : user a révélé que $500 a été choisi « au pif » (peur des gros
  trades). Le walk-forward du cap n'a testé que $500 vs valeurs PLUS HAUTES
  ($1000/$1500/no-cap, validé 4/4) — la zone < $500 est vierge.
- **arbitrage** : un cap plus bas plafonne aussi les GAGNANTS plus tôt (upside
  écrêté). À trancher au walk-forward (PnL ET DD).
- **quand** : différé (user a dit non pour l'instant, 2026-06-14). Prêt à lancer.
- **comment** : balayer `max_notional_per_trade` ∈ {250,300,400,500} en
  walk-forward dates glissantes (cf. harness slot-priority), spécifiquement à
  $333 et $680 (capitaux réels) en plus de $500. Vérifier DD en priorité.


---

## Stop S5 LONG serré à −500 bps — indice à creuser (2026-06-17)

- **quoi** : lors du test slow-bleed (réfuté, cf. `backtests/s5_slow_bleed_results.md`),
  l'ablation null `abl_hold0_l500` — couper S5 LONG dès que cur ≤ −500 bps, SANS
  condition de hold ni de régime — est la SEULE config à passer strict 4/4 en aligned
  (+177 pp sumΔ, DD intact). C'est en pratique un **stop catastrophe S5 LONG abaissé**.
- **pourquoi prudence** : pick post-hoc parmi 11 configs, magnitude faible (+32 pp sur
  base +820 % ≈ +4 % relatif). Preuve faible — ne PAS adopter sur cette base.
- **comment** : test DÉDIÉ pré-enregistré — sweep du stop catastrophe S5 LONG
  ∈ {−400,−500,−600} en aligned (flag Params, OFF défaut), 4 fenêtres glissantes,
  + null-shuffle (comme `backtest_trajectory_cut_r2_stability.py`) pour écarter le bruit.
  Vérifier qu'il ne rogne pas les runners (CRV-type, MAE −361 → +1990).
- **quand** : différé (priorité basse, signal faible). Prêt si on veut serrer le tail S5 LONG.

---

## Gate trend/chop (Efficiency Ratio) sur traj_cut (2026-06-18)

- **quoi** : idée — traj_cut nuit en bear-CHOPPY (whipsaw, sell-the-dip) mais aide
  en bear-TRENDING. L'ajouter un filtre Efficiency Ratio (ER de BTC) pour ne firer
  qu'en trending. Origine : senior −$82 sur 5 traj_cut en régime choppy actuel.
- **résultat premise EDA** (ON vs OFF, 28m, 19 fires appariés, ER n=6/24h) :
  corrélation ER↔impact = **+0.41** (bon sens) MAIS trop mince — le seul bucket
  clairement perdant est ER<0.2 (n=2, −56$/fire) ; ER 0.2-0.4 est POSITIF ;
  traj_cut net +$11 sur 28m. Régime récent OSCILLE (ER 0.05→0.68/jour, pas
  choppy soutenu) et un loser live (PYTH) a firé à ER=0.68 trending → l'ER ne
  prédit pas l'issue. **Premise trop faible → sweep NON lancé (overfit sur 2
  fires).** Cohérent avec le prior « filtres de régime échouent ».
- **quand** : ne pas re-tester tel quel. Pistes restantes si on y revient :
  discriminateur trend/chop plus fin (ADX, autocorrélation des returns), ou par
  TOKEN plutôt que BTC. Mais attente faible.
- **comment** : harness premise = run ON vs OFF (dc.replace traj_cut_strategies),
  apparier trades traj_cut par (coin,entry_t), bucket impact par ER. ER =
  |Δnet|/Σ|Δstep| sur n bougies BTC.

## Chantiers architecture (revue utilisateur 2026-07-02)

- **Chantier 3 — coûts réels par signal** : ✅ TRAITÉ 2026-07-02, écart NON
  matériel (slippage réel +0.1 bps moyen vs modèle 4 bps ; intégrale funding BT
  exacte à 0.0 bps ; S8 ne vit PAS à crédit). Résultats :
  `backtests/costs_by_signal_results.md`. Reliquat : **re-mesurer S9 à n≥20**
  (queue p90 +171 bps sur n=7, seul drapeau) — re-lancer
  `python3 -m backtests.measure_costs_by_signal` dans quelques mois.

- **Filet hard-stop, étapes restantes** :
  - **B — resserrement** : miroiter le plancher actif le plus serré
    (manual_stop / opp_floor / LOCK arbitre IA) sur le trigger résident —
    **place-then-cancel** (jamais cancel-then-replace : zéro fenêtre sans filet).
  - **C — junior/baby** : activer `hard_stop_enabled` après quelques jours
    propres sur SENIOR (triggers posés/annulés/re-posés sans incident).

- **Chantier 6 — sentinelle d'expiry des clés agent** : ✅ TRAITÉ 2026-07-02
  (v1.7.5, `agent_expiry_review()` dans daily_report.py — J−21 ligne 🔑,
  J−7 urgent 🚨 + status ⚠️, expiré critique ; lit bots.json, testé 5 paliers,
  cron = pas de restart). JUNIOR sonnera le 2026-10-05, BABY le 2026-11-17.

## Revue 2026-07-05 (points 4-5)
- ~~**Vol-targeting sizing** (pt 4)~~ : **REJETÉ 07-05**
  (`backtests/vol_targeting_results.md`) — premise P3 retournée (la vol est
  le carburant du fade : tercile volatil = 48 % du PnL), sweep 1/4 partout,
  VT_full dégrade même le DD 28m. Hooks gardés ; re-test seulement si bear
  durable ou capital ×3.
- ~~**Funding comme TILT d'entrée** (pt 5)~~ : **RÉFUTÉ 07-05** au
  premise-gate (`backtests/funding_filter_results.md`) — funding HL épinglé
  au taux de base, pas de variance exploitable, le seul bucket toxique
  flippe de signe sur 12m. Classé, ne pas re-tester.
- **Pistes données restantes (revue 07-05, ordre reviewer)** : OI quadrants
  ΔOI×Δprix comme feature S8/S9 (données déjà stockées) ; premium z-score
  (metaAndAssetCtxs, non regardé) ; calendrier macro FOMC/CPI en dur (gate
  S9 ±2h, backtestable gratuit) ; DVOL Deribit 6h comme régime forward ;
  momentum cross-sectionnel sur univers élargi (vrai chantier — store
  candles au-delà des 34).
- **Slippage conditionnel** (pt 6, mesuré 07-05) : moyenne ≈ 0 confirmée
  MAIS p90 croît avec la vol (+33 → +71 bps du bucket calme au bucket
  ≥20 %) ; pires fills idiosyncratiques (DYDX ×2, book mince, PAS la vol).
  Pas de changement de modèle BT (4 bps flat : conservateur en moyenne,
  optimiste sur la queue). Re-mesurer S9 à n≥20 (flag existant) + garder
  un œil sur DYDX.
- **Arrondi des constantes baroques** (MC joint 07-05, acquis quel que soit
  le verdict final) : si des chocs joints ±20 % laissent le 12m à 6.5× sans
  un perdant et des « gagnantes » à +1 %, la décimale de signal_mult S5=3.25
  (vs 3.0) n'a jamais rien porté. Passe d'arrondi sur les scalaires de
  settings (3.25→3, 1.125→1, seuils à la dizaine de bps) + vérif parité BT
  ± bruit MC. Réduction de surface gratuite, PAS un re-centrage. Candidat
  release batchée.
