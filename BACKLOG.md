# Backlog — tests, analyses et refactors différés

Ce fichier est l'index canonique de ce qu'il reste à creuser. Chaque item :
- **quoi** : la question à répondre ou le travail à faire
- **pourquoi** : la motivation et l'origine (review, mémoire, expérience)
- **quand** : prêt maintenant / date de revisite / condition de déclenchement
- **comment** : script à lancer, query à écrire, ou pointeur vers le code

Mettre à jour au fil de l'eau. Quand un item est traité → le supprimer et logger le résultat dans `CHANGELOG.md` ou `docs/synthese.md`.

---

## 1. Analyses prêtes à lancer (données suffisantes)

Ces features ont été analysées le 2026-05-11 via `backtests/analyze_obs_features.py` (rétrospectif) puis les hypothèses fortes testées en walk-forward via `backtests/backtest_we_oi_gates.py`. **Aucun gate n'a passé 4/4 strict.** Résumé :

- ❌ **`entry_session` (WE)** — rétrospectif fort (WE n=22 avg −$3.12, WR 36%). Walk-forward : `WE skip S5 LONG only` à **3/4** (ΔDD +0.39, à 0.11pp du seuil 0.5pp), le 12m casse. **Re-tester ~2026-08-11** : si la fenêtre 12m glisse vers une période moins négative, ça pourrait basculer 4/4.
- ❌ **`entry_oi_delta` SHORT gate** (mirror v11.4.9) — rétrospectif fort sur S5 SHORT (n=5 avg −$8.25). Walk-forward : **0/4 toutes thresholds** (+5% à +25%). Pattern = bruit d'échantillonnage, sur 28 mois historique le gate détruit systématiquement le pnl. **Classer définitivement.**
- 🟡 **`entry_crowding`** — rétrospectif WEAK (spread $3.62, pas extrême). Pas testé en walk-forward (signal trop faible pour justifier le coût).
- 🟡 **`entry_confluence`** — rétrospectif WEAK (spread $5.69 mais buckets pas extrêmes). Pas testé en walk-forward.

**Re-run** `backtests/analyze_obs_features.py` et `backtests/backtest_we_oi_gates.py` à 200+ trades ou ~2026-08-11.

---

## 2. Analyses en attente de données suffisantes (forward validation)

- [ ] **`basket_metrics` × drawdown** — instrumenté 2026-05-11. Revisiter ~**2026-07-11**. Question : les drawdowns observés corrèlent-ils avec `effective_n` bas ou `mean_corr_to_btc` extrême au moment d'ouverture ? Si oui → motivation pour un gate validé walk-forward. Voir mémoire `project_basket_correlation_review.md`.
- [ ] **`entry_side_imbalance` × slippage/pnl** — instrumenté 2026-05-11. Revisiter ~**2026-08-11** (3 mois) ou ~**2026-11-11** (6 mois). Re-lancer `backtests/backtest_l2_imbalance.py` une fois qu'on a 200+ trades avec ESI loggé en OPEN events. Le signal rétrospectif sur 75 trades était fort (+17 bps slippage défavorable vs favorable) mais bruité, à confirmer.
- [ ] **Modulator 2D** (`mult = 1 + α×btc_z + β×disp_z`) — testé walk-forward 4/4 strict 2026-05-11 via `backtests/backtest_modulator_2d.py` sur 14 configs. **Aucune ne passe 4/4 strict** : 5 configs atteignent 3/4, toutes négatives sur la fenêtre 3m (régime récent ne récompense pas l'extension). Mais la magnitude positive sur 28m/12m/6m est énorme (β[S5,S9,S10]=+0.5 : +59597pp / +3330pp / +67pp, ΔDD −9pp). **Re-tester ~2026-08-11 (3 mois)** : si le 3m négatif d'aujourd'hui était du bruit, l'extension repassera 4/4 dans une fenêtre future. Si c'était un shift durable, elle ne repassera jamais et on classe définitivement.
- [ ] **`S9F_OBS` hit rate** — événements ±3%/2h loggés. Besoin de 6+ mois de données pour évaluer si la fenêtre rapide a un edge.
- [ ] **`ETH_OBS` S8 hit rate** — actuellement n=0 trades pris (observation-only sur ETH). Besoin de n≥10 triggers S8 pour valider.

---

## 3. Hypothèses à re-tester quand le contexte change

- [ ] **ML pour WR estimator (XGBoost / régression logistique)** — rejeté à 87 trades (insuffisant). Re-considérer à **300+ trades fermés** (~mi-2027 au rythme actuel).
- [ ] **Maker post-only execution workflow** — rejeté à $500-1000 capital (gain théorique ~$5/mois). Re-considérer à **capital >$15k** par bot (mémoire `slippage_ceiling`).
- [ ] **WR auto-close** — testé et rejeté au walk-forward (le seuil WR<25% qui ferme auto perd). A re-tester avec 200+ trades dans le DB d'historique pour voir si signal stabilise.
- [ ] **S5 directional flip en bull** — testé et rejeté (régime-dépendant, sliding OOS pas stable). Variante directionnelle traitée v12.2.0 par modulator α. Surveiller si une fenêtre future justifie de re-tester.

---

## 4. Refactors différés (code review 2026-05-11)

Non-bloquant. Tous sans changement de comportement, restart-compatible.

- [ ] **m4 — extract `_scan_and_trade` body to `signals.scan`** (130 lignes → fonction pure de `signals.py`). Rend `bot.py` réellement "thin orchestrator" comme son docstring le prétend.
- [ ] **m6 — unify close helpers via `bot.close_with_retry(sym, exit_price, reason)`** — actuellement `api_close_symbol`, `api_pause`, `check_exits` re-implémentent la même logique autour de `_failed_closes` avec des conventions légèrement différentes.
- [ ] **m7 — async funding fetch dans `close_position`** — actuellement bloque le close path sur un roundtrip HL. Pattern proposé : log close immédiat avec `funding_usdt=0`, task background qui fait UPDATE après. Plus invasif que m4/m6 (transaction trade ↔ funding).

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
