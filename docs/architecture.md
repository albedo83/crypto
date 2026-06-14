# Architecture Alfred — document de référence

> **Source de vérité unique** de l'architecture du bot de trading, à jour avec le
> code (`alfred/`, v1.2.7, 2026-06-14). Remplace le cadrage « architecture » de
> `docs/bot.md` (qui décrit le stack legacy `analysis/bot/` décommissionné le
> 2026-06-12). Pour le *rationnel R&D* derrière chaque règle, voir `docs/bot.md`
> (détaillé) et `docs/synthese.md` (pédagogique) — leur logique de trading reste
> valide (noyau partagé), seul leur cadre runtime est périmé.

---

## 1. En une phrase

Alfred est un **process Python unique** (`python3 -m alfred`, port :8101) qui fait
tourner **3 bots de trading** (paper, live, junior) sur Hyperliquid à partir d'un
**flux de marché partagé** (un seul WebSocket) et d'un **noyau de règles commun au
bot et au backtest**, avec une **web unifiée** de supervision.

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
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │ BotInstance  │ │ BotInstance  │ │ BotInstance  │   (max 8)
   │   paper      │ │   live       │ │   junior     │
   │  PaperBroker │ │  LiveBroker  │ │  LiveBroker  │
   └──────────────┘ └──────────────┘ └──────────────┘
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
| `live` | SENIOR | live | $680.58 | clé directe (`HL_PRIVATE_KEY`) | la clé EST le wallet |
| `junior` | JUNIOR | live | $332.76 | agent (`JUNIOR_HL_PRIVATE_KEY` → master `0xb65d…56Fe`) | cap notionnel $500, opéré par un testeur |

- **Secrets** : `bots.json` ne stocke que les **noms** des variables `.env`, jamais
  les valeurs (résolus à l'init du broker).
- **Garde-fou nonce** : deux bots live ne peuvent pas partager la même clé signataire
  (validé au boot, refus de démarrer).
- **Overrides** : chaque bot peut surcharger n'importe quel `Params` via son bloc
  `overrides` (clé inconnue = fatale).
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
  phase 6). **Blacklist** SUI/IMX/LINK : gardés pour la donnée, skippés à l'entrée.
- 7 secteurs : L1, L1-major, Privacy, DeFi, Gaming, Infra, Meme.

---

## 5. Cadences (`alfred/__main__.py`)

| Quoi | Cadence | Détail |
|------|---------|--------|
| **Tick exits** | 20s | chaîne de sorties + MAJ MAE/MFE + manual_stop (plafond de latence) |
| **Scan entrées** | **au close 4h** (+180s de grâce) | évaluation des signaux d'entrée gated sur la bougie 4h |
| **Scan complet** | horaire (3600s) | refresh features + reconcile + equity, en plus du tick |
| **REST marché** | 60s | metaAndAssetCtxs (prix, OI, funding, premium) |
| **Snapshot marché** | horaire | dispersion cross-sectionnelle, btc_z, secteurs (calculé 1×) |
| **DXY (Yahoo)** | 6h | caché 48h |

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
| blacklist | SUI/IMX/LINK | skip à l'entrée |
| disp gate | **retiré** (99999) | ré-activable à 700 sur S5/S9 |

---

## 8. Chaîne de sorties (`rules.evaluate_exit` — ordre exact)

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
11. **traj_cut** (S5) — coupe « courbe désespérée » en régime bear (déclin rapide depuis le pic, collé au MAE, perte > −200 bps, btc_z < −0.5).
12. **s9_early_dead** — S9 sans MFE > +150 bps après 12h.
13. **btc_drop_cut** — LONG en perte + dump BTC 4h < −300 bps.
14. **dead_timeout** — à T−12h du timeout, position sans pouls (MFE ≤ 150, collée au MAE) → crystallise la perte.

Tous ces seuils sont des constantes dans `settings.py` avec kill-switch documenté.

---

## 9. Noyau partagé bot / backtest

`alfred/rules.py` est consommé **à l'identique** par le live (`botinstance.py`) et le
backtest (`backtests/backtest_rolling.py`, mode `aligned`). Doctrine : **plus jamais
de double implémentation d'une règle**. Toute divergence est un bug sérieux —
les divergences connues et justifiées sont tracées dans `docs/alfred_divergences.md`.

- Le backtest expose `alfred/tools/export_candles.py` (même source de candles).
- Coûts (round-trip, appliqués une fois au close) : taker 9 bps + slippage 0 (déjà
  dans avgPx en live) + funding drag 1 bps flat (remplacé par le réel en live) = **10 bps**.
- Échappatoire `BACKTEST_LEGACY_SEMANTICS=1` pour reproduire l'ancien moteur.

---

## 10. Couche données

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

## 11. Web & sécurité (`alfred/web/`)

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

## 12. Supervision & sentinelles (cron)

| Sentinelle | Cadence | Rôle | Action |
|-----------|---------|------|--------|
| `supervisor.py` | quotidien 8h UTC | rapport LLM (Haiku) santé flotte | observation seule (Telegram) |
| `analysis/strategy_review.py` | lundi 8h UTC | dérive par (stratégie, token, direction) | alerte seule |
| `analysis/regime_alert.py` | horaire :15 | régime BTC + S5 LONG en mauvais régime | alerte seule |
| `analysis/hedge_monitor.py` | 5 min | exposition/hedge | alerte |
| `alfred/tools/daily_report.py` | 8h30 UTC | digest flotte (balance, P&L, positions) + liens | Telegram |
| `backtests/paper_vs_bt_tracker.py` | 9h UTC | gap equity paper Alfred vs BT canonique | alerte si gap ≥ 5pp |
| watchdog | 5 min | relance `start_bots.sh` si Alfred absent | (ne surveille plus que Alfred) |

Toutes les sentinelles sont **observation/alerte**, jamais d'auto-modification de la
config ou de l'état des bots.

---

## 13. Règle d'or opérationnelle

**Ne JAMAIS redémarrer Alfred sans OK explicite de l'utilisateur** — il porte le bot
LIVE (argent réel). Éditer les fichiers et bumper `ALFRED_VERSION` librement, mais
l'utilisateur contrôle quand le process prend le changement. Le HTML dashboard est
caché en mémoire au premier chargement → un restart est nécessaire pour les changements
de `reversal.html`/`master.html` (mais pas pour les nouveaux endpoints API ni les tools
cron, relus à chaque exécution).

---

## 14. Où trouver quoi

| Besoin | Fichier |
|--------|---------|
| Constantes/params de la stratégie | `alfred/settings.py` |
| Logique de décision (signaux, exits, sizing) | `alfred/rules.py`, `alfred/signals.py` |
| Orchestration d'un bot | `alfred/botinstance.py` |
| Flux de marché / WS / candles | `alfred/market.py` |
| Exécution exchange | `alfred/hl.py`, `alfred/brokers.py` |
| Web / API / auth | `alfred/web/app.py`, `alfred/web/views.py` |
| Rationnel R&D détaillé par règle | `docs/bot.md` (logique valide, archi périmée) |
| Vulgarisation des stratégies | `docs/synthese.md` |
| Résultats backtest courants | `docs/backtests.md` |
| Divergences bot/backtest assumées | `docs/alfred_divergences.md` |
| Historique des versions Alfred | `alfred/CHANGELOG.md` |
| Mémoire long-terme de la refacto | `memory/project_alfred_refacto.md` |
</content>
