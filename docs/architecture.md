# Architecture Alfred — document de référence

> **Source de vérité unique** de l'architecture du bot de trading, à jour avec le
> code (`alfred/`, v1.8.0, 2026-07-04). Remplace le cadrage « architecture » de
> `docs/bot.md` (qui décrit le stack legacy `analysis/bot/` décommissionné le
> 2026-06-12). Pour le *rationnel R&D* derrière chaque règle, voir `docs/bot.md`
> (détaillé) et `docs/synthese.md` (pédagogique) — leur logique de trading reste
> valide (noyau partagé), seul leur cadre runtime est périmé.

---

## 1. En une phrase

Alfred est un **process Python unique** (`python3 -m alfred`, port :8101) qui fait
tourner **4 bots de trading** (paper, live, junior, baby) sur Hyperliquid à partir
d'un **flux de marché partagé** (un seul WebSocket) et d'un **noyau de règles commun
au bot et au backtest**, avec une **web unifiée** de supervision. Le bot SENIOR
(live) porte en plus une **couche de décision IA** (arbitrage des entrées et des
sorties, § 9) qui surplombe les règles sans jamais les modifier.

---

## 2. Architecture runtime

```
                 Hyperliquid
   WS (candle 4h + trades, 1 connexion)   REST (metaAndAssetCtxs 60s,
            │                              candleSnapshot, funding, OI)
            ▼                                        │
   ┌─────────────────────────────────────────────────────────┐
   │  MarketDataMaster (alfred/market.py)                      │
   │   • 1 WS candle+trades pour TOUS les symboles             │
   │   • REST résiduel (prix/OI/funding 60s, DXY 6h)           │
   │   • store candles canonique (market.db) + reprise au boot │
   │   • snapshot marché horaire (features, btc_z, cross_ctx)  │
   │   • auto-audits : CANDLE_AUDIT, GAP_REPAIR, WS_RECONNECT  │
   └─────────────────────────────────────────────────────────┘
            │ states[sym] (prix, candles_4h, OI…) + snapshot
            ▼
   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
   │BotInstance │ │BotInstance │ │BotInstance │ │BotInstance │   (max 8)
   │  paper     │ │  live      │ │  junior    │ │  baby      │
   │PaperBroker │ │LiveBroker  │ │LiveBroker  │ │LiveBroker  │
   └────────────┘ └─────┬──────┘ └────────────┘ └────────────┘
                        │ (SENIOR seul)
                  arbitres IA entrée/sortie (§ 9)
            │ chacun : noyau partagé alfred/rules.py
            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  Web unifiée (alfred/web/, FastAPI :8101)                 │
   │   /master (supervision) · /bot/<id>/ (dashboard par bot)  │
   └─────────────────────────────────────────────────────────┘
```

- **MarketDataMaster** : possède toute la donnée marché. Une connexion WS sert les
  N bots (vs N connexions en legacy). Le snapshot marché horaire (cross-sectional
  dispersion, btc_z, secteurs) est calculé **une fois** et partagé.
- **BotInstance** (`alfred/botinstance.py`) : un par bot. Capital, positions, état,
  P&L, scan/entrées, chaîne de sorties, reconcile exchange, persistance — propres à
  chaque bot. Lit la donnée du master, décide via `rules.py`.
- **Broker** (`alfred/brokers.py`) : `PaperBroker` (simulation mémoire) ou
  `LiveBroker` (ordres réels via `alfred/hl.py` → SDK Hyperliquid).
- **Web** (`alfred/web/`) : une appli FastAPI, un port, toutes les vues.

Point d'entrée : `alfred/__main__.py` (scheduler des cadences + boucle master + web).
Lock `alfred/data/alfred.lock` : fail-bind propre si déjà lancé.

---

## 3. La flotte (`alfred/bots.json`)

| Bot | Label | Mode | Capital init | Wallet | Particularités |
|-----|-------|------|--------------|--------|----------------|
| `paper` | PAPER-ALFRED | paper | $1000 | — | simulation, baseline backtest-parité |
| `live` | SENIOR | live | $680.58 | clé directe (`HL_PRIVATE_KEY`) | la clé EST le wallet ; **seul bot avec la couche IA (§ 9)** |
| `junior` | JUNIOR | live | $332.76 | agent (`JUNIOR_HL_PRIVATE_KEY` → master `0xb65d…56Fe`, expire 2026-10-26) | capital_cap $500, opéré par un testeur |
| `baby` | BABY | live | $57.84 | agent (`BABY_HL_PRIVATE_KEY` → master `0xda40…b014c`, expire 2026-12-08) | capital_cap $400, DCA prévu, opéré par une tierce personne |

- **Secrets** : `bots.json` ne stocke que les **noms** des variables `.env`, jamais
  les valeurs (résolus à l'init du broker).
- **Garde-fou nonce** : deux bots live ne peuvent pas partager la même clé signataire
  (validé au boot, refus de démarrer).
- **Overrides** : chaque bot peut surcharger n'importe quel `Params` via son bloc
  `overrides` (clé inconnue = fatale).
- **Rôles web** : `bot:junior` / `bot:baby` ne voient que leur `/bot/<id>/*` (§ 12).
- Max 8 bots.

---

## 4. Les 5 signaux

Détection dans `alfred/signals.py`, paramètres dans `alfred/settings.py`. z-score =
robustesse statistique mesurée ; walk-forward = passes sur 4 fenêtres glissantes.

| Signal | Idée | Direction | Fréquence | Hold | Stop | z-score |
|--------|------|-----------|-----------|------|------|---------|
| **S1** | BTC +20%/30j → acheter le retard des alts | LONG | rare (qq/an) | 72h | −1250 bps | 6.42 |
| **S5** | Suivre la divergence sectorielle (breakout secteur) | LONG/SHORT | fréquent | 48h | −1250 bps | 3.67 |
| **S8** | Acheter les flush de capitulation (DD < −40%) | LONG | modéré | 60h | −750 bps | 6.99 |
| **S9** | Fader les mouvements extrêmes (±20%/24h) | LONG/SHORT | modéré | 48h | adaptatif* | 8.71 |
| **S10** | Fader les faux breakouts post-compression | SHORT seul** | modéré | 24h | −1250 bps | 3.66 |

\* S9 stop adaptatif : `max(−1250, −500 − |ret_24h|/8)` — plus le move est grand, plus le stop est serré (plage [−750, −1250]).
\** S10 : SHORT-only + whitelist 13 tokens (`s10_allowed_tokens`) ; longs désactivés (`s10_allow_longs=False`).

- **S2** (alt crash) retiré, **S4** (vol+DXY) suspendu.
- Univers : **34 tokens tradés** + BTC/ETH référence (`trade_symbols`, MKR retiré
  phase 6). **Blacklist vidée le 2026-06-30** (v1.6.7 — l'overfit de sélection a
  décru : le retrait gagne 6/7 en OOS glissant) ; ré-activable via `trade_blacklist`.
- 7 secteurs : L1, L1-major, Privacy, DeFi, Gaming, Infra, Meme.

---

## 5. Cadences (`alfred/__main__.py`)

| Quoi | Cadence | Détail |
|------|---------|--------|
| **Tick exits** | 20s | coupe-pertes/stops/timeout/manual + MAJ MAE/MFE ; **règles trail : uniquement au 1er tick post-clôture 4h** (v1.8.0, § 8) |
| **Scan entrées** | **au close 4h** (+180s de grâce) | évaluation des signaux d'entrée gated sur la bougie 4h |
| **Scan complet** | horaire (3600s) | refresh features + reconcile + equity, en plus du tick |
| **REST marché** | 60s | metaAndAssetCtxs (prix, OI, funding, premium) |
| **Snapshot marché** | horaire | dispersion cross-sectionnelle, btc_z, secteurs, capitulation breadth (calculé 1×) |
| **DXY (Yahoo)** | 6h | caché 48h |
| **Arbitre IA entrée** (SENIOR) | au scan 4h | 1 appel synchrone avant les ordres (§ 9) |
| **Arbitre IA sortie** (SENIOR) | throttle 1h | overlay dans on_tick, zone candidate (§ 9) |

Le gate d'entrée au close 4h (`_last_entry_scan_4h_close`, persisté) aligne le live
sur le backtest (le BT scanne 1×/bougie 4h) — corrige la dérive intra-bougie
observée en live (v12.9.0). Les exits, eux, tournent toutes les 20s.

---

## 6. Sizing & modulateur macro

**Sizing** (`rules.position_size`) : `base% × z-weight × haircut × signal_mult`, puis
modulateur, puis arrondi, puis **cap notionnel $500**, plancher $10.
- `size_pct=0.18`, `size_bonus=0.03`, **levier 2× cross**.
- `signal_mult` : S1=1.125, S5=3.25, S8=1.25, S9=2.0, S10=2.0.
- `liquidity_haircut` : S8=0.8.
- Le `size_usdt` est le **notionnel** (déjà leveragé) : `pnl = size_usdt × Δprix`,
  **jamais** re-multiplier par le levier (invariant v11.3.0).

**Modulateur macro adaptatif** (`rules.modulator_mult`, v11.10.0/v12.2.0) : module la
taille par `1 + α × btc_z`, où `btc_z` = z-score 6 mois du rendement BTC 30j.
- `α` : **S1=+0.5** (amplifié en bull), **S8=−0.5**, **S9=−0.5** (amplifiés en bear).
- Override directionnel : **S5 SHORT = −0.5** (`adaptive_alpha_dir`).
- S5 LONG et S10 **exclus** (relation au régime instable).
- btc_z clippé à ±2.5, multiplicateur borné [0.3, 2.5].

**Live only** : avant chaque ordre, la taille est clampée à la **marge disponible**
réelle du compte (v1.2.1) — réduction ou skip propre au lieu d'un rejet HL.

---

## 7. Gates d'entrée (`rules.entry_skip_reason`)

| Gate | Valeur | Effet |
|------|--------|-------|
| max positions | 6 | total simultané par bot |
| max même direction | 4 | longs OU shorts |
| max par secteur | 2 | |
| slots macro (S1) | 3 | |
| slots token (non-S1) | 4 | |
| OI gate LONG | OI 24h < −10% | bloque les longs (knife-catching) |
| cooldown | 24h | par token après une sortie |
| size floor | < $10 | skip (modulateur trop bas) |
| blacklist | **vide** (2026-06-30) | ré-activable via `trade_blacklist` |
| disp gate | **retiré** (99999) | ré-activable à 700 sur S5/S9 |
| arbitre IA (SENIOR) | veto / haircut | dernier filtre avant l'ordre (§ 9) |

---

## 8. Chaîne de sorties (`rules.evaluate_exit` — ordre exact)

<!-- EXIT_CHAIN:BEGIN -->
<!-- Généré par alfred/tools/gen_exit_chain_block.py — NE PAS ÉDITER À LA MAIN.
     Pre-commit : --check refuse tout commit si ce bloc diverge du code. -->

| # | Règle | Évaluation | Statut (settings) |
|---|-------|------------|-------------------|
| 1 | `runner_ext` | tick 20s | ACTIVE |
| 2 | `catastrophe_stop` | tick 20s | ACTIVE |
| 3 | `opp_floor` | 4h-close | ACTIVE |
| 4 | `timeout` | tick 20s | ACTIVE |
| 5 | `manual_stop` | tick 20s | ACTIVE |
| 6 | `s9_early` | tick 20s | ACTIVE |
| 7 | `s10_trail` | 4h-close | ACTIVE |
| 8 | `s8_dead` | tick 20s | ACTIVE |
| 9 | `s8_inlife` | 4h-close | ACTIVE |
| 10 | `prop_trail` | 4h-close | ACTIVE |
| 11 | `traj_cut` | tick 20s | ACTIVE |
| 12 | `s9_early_dead` | tick 20s | ACTIVE |
| 13 | `btc_drop_cut` | tick 20s | ACTIVE |
| 14 | `dead_timeout` | tick 20s | RETIRÉE |
<!-- EXIT_CHAIN:END -->

Évaluée à chaque tick (20s). **L'ordre est garant de la priorité** — première règle
qui matche gagne. Identique bot et backtest (mode `aligned`).

1. **runner_ext** (S9) — au timeout, prolonge de 12h un gagnant encore proche de son MFE (≥1200 bps). *Extension, pas sortie.*
2. **catastrophe_stop** — stop dur (−1250 / S8 −750 / S9 adaptatif). Évalué sur le pire du intra-bougie au BT.
3. **opp_floor** — signal opposé sur un gagnant détenu → plancher cliquet à 0.80× du gain (armé au scan 4h).
4. **timeout** — hold de la stratégie atteint.
5. **manual_stop** — plancher $ posé manuellement par l'utilisateur (`POST /api/manual_stop`).
6. **s9_early** — S9 en perte > −500 bps après 8h (fade raté).
7. **s10_trail** — trailing S10 : sortie à MFE−150 bps quand MFE > 600 bps.
8. **s8_dead** — S8 LONG sans MFE > +50 bps après 8h (capitulation morte).
9. **s8_inlife** — trail S8 régime-conditionné (bear/bull serré, neutral large).
10. **prop_trail** — trail proportionnel S9 (bull seul : arme à 100 bps, lock 0.65).
11. **traj_cut** (S5, **LONG only** v1.6.4) — coupe « courbe désespérée » en régime bear (déclin rapide depuis le pic, collé au MAE, perte > −200 bps, btc_z < −0.5). Les S5 SHORT pinnés mean-revertent → jamais coupés.
12. **s9_early_dead** — S9 sans MFE > +150 bps après 12h.
13. **btc_drop_cut** — LONG en perte + dump BTC 4h < −300 bps.
14. **dead_timeout** — à T−12h du timeout, position sans pouls (MFE ≤ 150, collée au MAE) → crystallise la perte.

Tous ces seuils sont des constantes dans `settings.py` avec kill-switch documenté.

**Trails sur close 4h (v1.8.0)** : les règles « sur le pic » (opp_floor,
s10_trail, s8_inlife, prop_trail) sont évaluées **au premier tick suivant
chaque clôture 4h**, sur un MFE échantillonné à ces clôtures — la granularité
exacte de leur validation (le tick 20 s bruité gonflait le pic et coupait les
gagnants ~50-100 bps trop tôt, cf. `backtests/trails_on_close_results.md`).
Coupe-pertes, stops, timeout et filet restent au tick. Kill-switch :
`trail_eval_4h_close=False`.

Sur SENIOR uniquement, l'**arbitre IA de sortie** (§ 9) surplombe cette chaîne — il
peut poser un stop protecteur sur un gagnant, jamais la court-circuiter.

**Filet hard-stop exchange-side** (v1.7.1-3, `alfred/hardstop.py`, SENIOR armé via
`hard_stop_enabled`) : chaque position porte un trigger order **reduce-only**
résident sur Hyperliquid au **plancher soft le plus serré − 200 bps**
(catastrophe, `manual_stop`, LOCK de l'arbitre IA, `opp_floor` — v1.7.3), posé à
l'ouverture, resserré en **place-then-cancel** (jamais sans filet pendant la
bascule), annulé à la fermeture, re-posé/nettoyé au reconcile. Process vivant → la chaîne 20s
ferme toujours avant lui ; il n'exécute que si le process est mort (crash,
watchdog, boot) ou si le marché va plus vite que 20s. Une fermeture exchange-side
est **bookée depuis les fills réels** au retour (reasons `exchange_stop` /
`liquidation` / `exchange_close`). Pas une règle : `rules.py`/BT inchangés
(divergence #15 dans `docs/alfred_divergences.md`).

---

## 9. Couche décision IA (SENIOR)

Depuis v1.6.0 (entrées) et v1.6.9 (sorties), le bot SENIOR porte une couche de
jugement LLM (Claude, `AI_ARBITER_MODEL`/`AI_EXIT_MODEL`) **au-dessus** du moteur de
règles. **Le rôle en une phrase : l'IA est un arbitre défensif — elle peut
RETENIR ou RÉDUIRE ce que les règles veulent faire, et PROTÉGER ou COUPER ce
qu'elles détiennent ; elle ne peut jamais initier, amplifier, ni reconfigurer.**

| L'IA PEUT | L'IA NE PEUT PAS |
|---|---|
| Véter une entrée que les règles allaient prendre | Ouvrir une position de sa propre initiative |
| Réduire la taille d'une entrée (haircut, plancher `FACTOR_MIN`) | Augmenter une taille au-delà du sizing des règles |
| Poser un stop protecteur sous un gagnant (LOCK) | Fermer un gagnant |
| Fermer un perdant condamné (CUT, **act depuis 2026-07-03**, ur ≤ −300 bps) | Toucher junior/baby/paper (gate `bot.id=="live"`) |
| — | Modifier config, paramètres, ou `rules.py` |
| — | Empêcher le bot de trader (fail-open intégral) |

Principes structurants :

- **Overlay live-only** : gaté `bot.id == "live"`, hors `rules.py` — le noyau partagé
  et le backtest restent 100 % déterministes. Aucun autre bot n'est affecté.
- **Inbacktestable → forward-validation seule** : un jugement LLM ne se rejoue pas
  sur le passé. La preuve se construit en marchant (scorecards contrefactuels),
  jamais bloquant sans ≥ 50 décisions résolues.
- **Fail-open** : timeout, erreur API ou budget dépassé → les règles s'appliquent
  telles quelles (event `ARBITER_FAILOPEN`). L'IA ne peut jamais *empêcher* le bot
  de fonctionner.

### Arbitre d'entrée (`ai_entry_arbiter.py`, v1.6.0)

Appelé en synchrone dans `_rank_and_enter` (1 appel par scan 4h, timeout court),
**dernier filtre avant l'ordre**. Décide GO / VETO / haircut de taille (`factor`,
plancher `AI_ARBITER_FACTOR_MIN`). Contexte : setup du trade, momentum token+BTC,
btc_z, dispersion, **indice de capitulation marché-large** (v1.7.0 — breadth 24h sur
~230 perps HL calculé par le master sans fetch supplémentaire : `down20_pct` /
`down10_pct` / `median_24h_bps`, signal de jugement, pas un gate). Mémoire
d'**hystérésis** (v1.6.6) : la décision précédente sur le même setup est réinjectée
dans le prompt pour éviter les revirements scan à scan. Règle ferme : veto par défaut
d'un SHORT qui combat une hausse alignée token+BTC (et symétrique LONG, v1.6.2).
Mode `AI_ARBITER_MODE` : `shadow` (décide et mesure sans agir) ou `act` (actuel).

### Arbitre de sortie (`ai_exit_arbiter.py`, v1.6.9)

Overlay `_ai_exit_overlay` dans `on_tick`, throttlé (`AI_EXIT_THROTTLE_S`, 1h) et
limité à une zone candidate. Deux verdicts, asymétriques par prudence :
- **LOCK** (agit) : pose un stop protecteur sous un gagnant (plancher
  `AI_EXIT_LOCK_UR_MIN_BPS`, écrit `manual_stop_usdt`) — protège, ne ferme
  jamais un gagnant. Le LOCK est **miroité sur le trigger résident
  exchange-side** (filet hard-stop, v1.7.3) : la protection tient même
  process mort. Nuance à surveiller (07/2026) : un LOCK trop serré ampute
  les runners (cf. chantier trails-sur-close) — le scorecard tranche.
- **CUT** (**act depuis 2026-07-03**, sur ordre utilisateur, preuve shadow
  n=1 +25.64 $) : ferme au market un perdant condamné, uniquement si
  ur ≤ `AI_EXIT_CUT_UR_MAX_BPS` = −300 bps, re-vérifié au prix frais au
  moment d'agir (v1.6.11) — jamais un gagnant, 1 décision/h max.

### La preuve : scorecards contrefactuels + disjoncteurs

- `ai_arbiter_scorecard.py` (cron horaire :20, récap TG 8h05) : compare le P&L réel
  au P&L « règles seules ». En shadow le trade réel EST le contrefactuel ; en act,
  un veto est **rejoué** via le noyau partagé (`rules.evaluate_exit` sur candles 4h).
- `ai_exit_scorecard.py` (cron :25, récap TG 8h10) : idem pour LOCK/CUT (dédup,
  contrefactuel hold-to-rules).
- **Disjoncteur** : si ≥ `*_CB_MIN` décisions résolues ET Δ cumulé < `*_CB_LOSS` →
  drapeau `arbiter_tripped` écrit, l'arbitre **dégrade en shadow** + alerte TG.
  Réarmement manuel (suppression du drapeau).
- Kill-switches : `AI_ARBITER_ENABLED=0` / `AI_EXIT_ENABLED=0` (`.env`). Budget
  mensuel plafonné (`AI_BUDGET_MONTHLY_USD`).

### Observation (n'agissent sur rien)

- `position_review.py` (cron 2h) : revue LLM des positions ouvertes — historique
  dans l'admin `/master` (plus de Telegram depuis v1.7.0).
- `supervisor.py` (quotidien 8h) : rapport santé flotte condensé — admin seulement.
- `entry_judge.py` : précurseur observation-only de l'arbitre d'entrée (juge ex-post
  les events `ENTRY_CONTEXT`) — **dormant** depuis que l'arbitre synchrone existe.
- Tout est audité en events (`ARBITER_DECISION`, `ARBITER_EXIT_DECISION`,
  `AI_SCORECARD`, `AI_EXIT_SCORECARD`, `POSITION_REVIEW`…) dans `live/bot.db`,
  visibles sur `/master` (section « Arbitrage IA »).

---

## 10. Noyau partagé bot / backtest

`alfred/rules.py` est consommé **à l'identique** par le live (`botinstance.py`) et le
backtest (`backtests/backtest_rolling.py`, mode `aligned`). Doctrine : **plus jamais
de double implémentation d'une règle**. Toute divergence est un bug sérieux —
les divergences connues et justifiées sont tracées dans `docs/alfred_divergences.md`.

- Le backtest expose `alfred/tools/export_candles.py` (même source de candles).
- Coûts — deux modèles distincts (ne pas confondre, cf. divergence #12) :
  **bot (ledger)** = taker 9 bps + slippage 0 (déjà dans avgPx) + funding drag
  1 bps flat swappé par le réel en live = 10 bps ; **backtest** = taker 9 bps +
  slippage 4 bps (`BACKTEST_SLIPPAGE_BPS`, il entre au close de bougie) +
  **intégrale de funding historique horaire par trade** (`compute_funding_cost`).
  Les deux validés vs fills réels le 2026-07-02 (`backtests/costs_by_signal_results.md` :
  slippage réel +0.1 bps moyen, intégrale funding exacte à Δ 0.0 bps).
- Échappatoire `BACKTEST_LEGACY_SEMANTICS=1` pour reproduire l'ancien moteur.

---

## 11. Couche données

| Store | Fichier | Contenu |
|-------|---------|---------|
| **Marché (canonique)** | `alfred/data/market.db` | `candles` (4h, store canonique), `ticks`, `events` (audits, alertes), snapshots |
| **Par bot** | `alfred/data/bots/<id>/bot.db` | `trades` (historique P&L), `admin_audit` |
| **État par bot** | `alfred/data/bots/<id>/state.json` | positions, capital, P&L, MAE/MFE, trajectoires (écriture atomique .tmp + os.replace) |

- **Reprise au boot** : restauration depuis la DB + event DOWNTIME + catch-up des
  excursions des positions ouvertes.
- **Concurrence** : `db._db_lock` sérialise les écritures SQLite ; `_pos_lock` garde
  les mutations de positions ; `_closing` = mutex par symbole autour de la fermeture.
- WS auto-reconnexion (le SDK n'en a pas) : reconnexions comptées + GAP_REPAIR au
  retour, y compris sur fermeture propre côté serveur (v1.2.1).

---

## 12. Web & sécurité (`alfred/web/`)

- **Pages** : `/master` (supervision : santé données, flotte, exposition agrégée,
  lifecycle par bot, éditeur `bots.json`, journal d'audit) ; `/bot/<id>/` (dashboard
  par bot) ; derrière nginx à `https://echonym.fr/alfred/`.
- **Auth** : cookies de session signés HMAC (stateless, 30j), backoff anti-brute-force,
  rate-limit sur les mutations.
- **Rôles** : `admin` (DASHBOARD_USER → tout) et `bot:<id>` (ex. JUNIOR_USER →
  uniquement `/bot/junior/*`). Mutations auditées dans `admin_audit`.
- **2FA TOTP** (v1.2.2) : optionnelle par compte (`DASHBOARD_TOTP_SECRET`), exigée
  pour les requêtes externes (via nginx), exemptée pour les sentinelles locales
  (connexion 127.0.0.1 directe sans X-Forwarded-For).
- API : read-only (`/api/state`, `/api/signals`, `/api/trades`, `/api/chart`,
  `/api/pnl`, `/api/events`, `/api/intervention_impact`) + mutations (`/api/close`,
  `/api/pause`, `/api/reset`, `/api/manual_stop`, `/api/capital`).

---

## 13. Supervision & sentinelles (cron)

| Sentinelle | Cadence | Rôle | Action |
|-----------|---------|------|--------|
| `supervisor.py` | quotidien 8h UTC | rapport LLM santé flotte, condensé | observation (admin `/master`, plus de TG) |
| `analysis/strategy_review.py` | lundi 8h UTC | dérive par (stratégie, token, direction) + `TRAJ_CUT_EFF` | alerte seule |
| `analysis/hedge_monitor.py` | 5 min | exposition/hedge | alerte |
| `alfred/tools/daily_report.py` | 8h30 UTC | digest flotte (balance, P&L, positions) + liens + **sentinelle expiry agents** (J−21 🔑 / J−7 🚨, v1.7.5 — JUNIOR 2026-10-26, BABY 2026-12-08) | Telegram |
| `backtests/paper_vs_bt_tracker.py` | 9h UTC | gap equity vs BT canonique, **les 4 bots** | TG consolidé si un gap ≥ 5pp |
| `position_review.py` | toutes les 2h | revue LLM des positions ouvertes SENIOR | historique admin (§ 9) |
| `ai_arbiter_scorecard.py` | horaire :20 (+ TG 8h05) | contrefactuel arbitre d'entrée + disjoncteur | § 9 |
| `ai_exit_scorecard.py` | horaire :25 (+ TG 8h10) | contrefactuel arbitre de sortie + disjoncteur | § 9 |
| `overfit_monitor.py` | 9h30 UTC | thermomètre Promesse-IS → OOS-BT → Live | log seul |
| watchdog | 5 min | relance `start_bots.sh` si Alfred absent | (ne surveille plus que Alfred) |

Toutes les sentinelles sont **observation/alerte**, jamais d'auto-modification de la
config ou de l'état des bots (seuls les arbitres IA du § 9 agissent, dans leurs
bornes). `analysis/regime_alert.py` (nudge régime BTC) **retiré du cron le
2026-07-02** — l'info vit sur le dashboard ; script conservé, ré-activable.

---

## 14. Règle d'or opérationnelle

**Ne JAMAIS redémarrer Alfred sans OK explicite de l'utilisateur** — il porte le bot
LIVE (argent réel). Éditer les fichiers et bumper `ALFRED_VERSION` librement, mais
l'utilisateur contrôle quand le process prend le changement.

**Procédure de restart (corrigée après l'incident du 2026-07-02)** : attendre la
**mort du PID** (pas la libération du port — la web tombe en premier et le shutdown
peut pendre), SIGKILL de grâce après 5 min, relancer seulement quand plus aucun
python `-m alfred` ne survit ; post-boot, vérifier la version dans le log ET le
comportement runtime (pas le fichier `.env` — l'env est figé au boot du process qui
tourne réellement). Sinon : relance fail-bindée sur le lock + ancien process qui
trade sans web avec l'ancien env. Le HTML dashboard est
caché en mémoire au premier chargement → un restart est nécessaire pour les changements
de `reversal.html`/`master.html` (mais pas pour les nouveaux endpoints API ni les tools
cron, relus à chaque exécution).

---

## 15. Où trouver quoi

| Besoin | Fichier |
|--------|---------|
| Constantes/params de la stratégie | `alfred/settings.py` |
| Logique de décision (signaux, exits, sizing) | `alfred/rules.py`, `alfred/signals.py` |
| Orchestration d'un bot | `alfred/botinstance.py` |
| Flux de marché / WS / candles | `alfred/market.py` |
| Exécution exchange | `alfred/hl.py`, `alfred/brokers.py` |
| Filet hard-stop (math + divergence #15) | `alfred/hardstop.py`, `docs/alfred_divergences.md` |
| Chantier trails-sur-close (validation) | `backtests/trails_on_close_results.md` |
| Web / API / auth | `alfred/web/app.py`, `alfred/web/views.py` |
| Couche IA : arbitres | `ai_entry_arbiter.py`, `ai_exit_arbiter.py` (racine du dépôt) |
| Couche IA : preuve/scorecards | `ai_arbiter_scorecard.py`, `ai_exit_scorecard.py` |
| Couche IA : observation | `position_review.py`, `supervisor.py`, `entry_judge.py` (dormant) |
| Rationnel R&D détaillé par règle | `docs/bot.md` (logique valide, archi périmée) |
| Vulgarisation des stratégies | `docs/synthese.md` |
| Résultats backtest courants | `docs/backtests.md` |
| Divergences bot/backtest assumées | `docs/alfred_divergences.md` |
| Historique des versions Alfred | `alfred/CHANGELOG.md` |
| Mémoire long-terme de la refacto | `memory/project_alfred_refacto.md` |
</content>
