# Backlog — tests, analyses et refactors différés

Ce fichier est l'index canonique de ce qu'il reste à creuser. Chaque item :
- **quoi** : la question à répondre ou le travail à faire
- **pourquoi** : la motivation et l'origine (review, mémoire, expérience)
- **quand** : prêt maintenant / date de revisite / condition de déclenchement
- **comment** : script à lancer, query à écrire, ou pointeur vers le code

Mettre à jour au fil de l'eau. Quand un item est traité → le supprimer et logger le résultat dans `CHANGELOG.md` ou `docs/synthese.md`.

---

## 1. Analyses prêtes à lancer (données suffisantes)

Ces features sont loggées depuis longtemps et le sample size dépasse le seuil "50+ trades" du protocole. Aucun bot tournant ne les utilise pour décider.

- [ ] **`entry_oi_delta` × pnl** — 87 trades live. Est-ce que les entrées avec OI 24h en chute libre (-X%) sous-performent ? Si oui : on a déjà l'OI gate sur les LONG (v11.4.9), mais peut-être un gate plus fin sur les SHORT.
- [ ] **`entry_crowding` × pnl** — 87 trades. Le score crowding 0-100 a-t-il un rapport avec le pnl (i.e. les flushes "propres" sont-elles vraiment meilleures) ?
- [ ] **`entry_confluence` × pnl** — 87 trades. Plus de features extrêmes à l'entrée = meilleur trade ?
- [ ] **`entry_session` × pnl** — 87 trades. Asia/EU/US/Night/WE : un breakdown WR par session pourrait justifier un sizing par horaire.

**Comment** : un script `backtests/analyze_obs_features.py` (~80 lignes) qui lit `trades` DB, bucketise par chaque feature, sort un tableau WR/avg pnl/n par bucket. Lancer une fois sur live + une fois sur paper pour cross-check.

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
