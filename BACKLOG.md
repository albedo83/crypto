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
