> **ARCHIVE — chiffres PRÉ-PHASE 6 (sémantique legacy, archivés le 2026-06-10).**
> Ces chiffres étaient inflatés ~34× sur 28m par des divergences de sémantique
> vs l'exécution live (cap notionnel $20k pré-modulateur vs $500 post, prop_trail
> non simulé, prix d'exit au close…). Voir docs/alfred_phase6_preview.md pour
> l'attribution et docs/backtests.md pour la référence courante (aligned).

# Rolling backtests

**Générée le** : 2026-06-04 14:49 UTC
**Bot version** : v12.15.0
**Données jusqu'à** : 2026-06-04
**Capitaux testés** : $500

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.15.0)

**S10 filters** (v11.3.4)
- `S10_ALLOW_LONGS = False` → SHORT fades seulement (LONG fades perdaient $4.8k sur 28m, 45% WR — *fade panic = fail*)
- `S10_ALLOWED_TOKENS` (whitelist de 13 tokens) : AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD

Dérivés de `backtest_s10_walkforward.py` (train 2023-10→2025-02, test 2025-02→2026-02 OOS). Impact OOS : P&L +123% vs baseline, DD −8.7pp.

**OI gate LONG** (v11.4.9) — `OI_LONG_GATE_BPS = 1000`
- Skip LONG entries quand `Δ(OI, 24h) < -10%`. Longs qui se débouclent = flow baissier encore actif = LONG catche un couteau qui tombe.
- Validé walk-forward 4/4 : +$2 498 / +$816 / +$380 / +$252 sur 28m/12m/6m/3m, zéro impact DD. Helper : `features.oi_delta_24h_bps()`.
- Source : `backtests/backtest_external_gates.py`, `backtests/backtest_oi_gate_validate.py`.

**Trade blacklist** (v11.4.10) — `TRADE_BLACKLIST = {IMX, LINK, SUI}`
- Tokens net-négatifs sur les 4 fenêtres walk-forward : SUI (−$5 311 28m, −$1 045 12m, −$336 6m, −$98 3m), IMX (−$2 952 / −$566 / −$156 / −$53), LINK (−$2 415 / −$387 / −$185 / −$75).
- Validé sur `backtest_rolling` : +91% sur 28m (+$49 687), +63% 12m, +34% 6m, +18% 3m.
- DD 28m dégradée de ~10pp (swings absolus plus grands sur un capital plus haut), DD améliorée ou inchangée sur toutes les fenêtres récentes.
- Source : `backtests/backtest_worst_losers.py`, `backtests/backtest_loser_filters.py`.
- Kill-switch (réactiver un token) : supprimer de `TRADE_BLACKLIST` dans `analysis/bot/config.py`.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-02-04 | $318 342 | +$317 842 | +63568.3% | -61.9% | 1195 | 52% | S5 |
| depuis 2024-07-01 | 2024-07-01 | $291 889 | +$291 389 | +58277.8% | -61.9% | 970 | 53% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $271 352 | +$270 852 | +54170.3% | -61.9% | 924 | 52% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $281 832 | +$281 332 | +56266.4% | -55.5% | 875 | 53% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $300 220 | +$299 720 | +59944.0% | -55.5% | 833 | 54% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $298 957 | +$298 457 | +59691.5% | -55.5% | 801 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $255 830 | +$255 330 | +51066.1% | -40.1% | 748 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $194 862 | +$194 362 | +38872.5% | -42.3% | 707 | 54% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $176 194 | +$175 694 | +35138.8% | -49.7% | 669 | 54% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $152 152 | +$151 652 | +30330.4% | -56.3% | 615 | 54% | S1 |
| depuis 2025-04-01 | 2025-04-01 | $149 725 | +$149 225 | +29845.0% | -56.6% | 573 | 55% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $130 562 | +$130 062 | +26012.5% | -58.0% | 534 | 55% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $125 570 | +$125 070 | +25013.9% | -58.0% | 491 | 55% | S1 |
| 12 mois | 2025-06-04 | $123 812 | +$123 312 | +24662.5% | -58.0% | 488 | 55% | S1 |
| depuis 2025-07-01 | 2025-07-01 | $71 272 | +$70 772 | +14154.4% | -58.0% | 454 | 54% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $65 327 | +$64 827 | +12965.3% | -58.0% | 423 | 54% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $69 322 | +$68 822 | +13764.5% | -58.0% | 385 | 54% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $45 726 | +$45 226 | +9045.2% | -58.0% | 340 | 54% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $13 650 | +$13 150 | +2630.0% | -58.0% | 296 | 53% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $4 729 | +$4 229 | +845.8% | -58.0% | 253 | 50% | S5 |
| 6 mois | 2025-12-04 | $4 703 | +$4 203 | +840.6% | -58.0% | 252 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $3 902 | +$3 402 | +680.5% | -58.0% | 227 | 48% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $3 485 | +$2 985 | +597.1% | -52.1% | 194 | 49% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 334 | +$834 | +166.8% | -24.6% | 142 | 46% | S5 |
| 3 mois | 2026-03-04 | $1 179 | +$679 | +135.8% | -27.3% | 140 | 45% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 504 | +$1 004 | +200.8% | -18.8% | 114 | 47% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $1 526 | +$1 026 | +205.2% | -18.2% | 106 | 48% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 495 | +$995 | +198.9% | -18.2% | 62 | 52% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 460 | +$960 | +192.0% | -18.2% | 58 | 52% | S5 |
| 1 mois | 2026-05-04 | $802 | +$302 | +60.3% | -18.2% | 55 | 49% | S5 |
| depuis 2026-05-31 | 2026-05-31 | $523 | +$23 | +4.6% | -12.7% | 14 | 50% | S5 |
| depuis 2026-06-01 | 2026-06-01 | $505 | +$5 | +1.0% | -12.5% | 12 | 33% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 113 | 55% | +$45 794 |
| S10 | 348 | 55% | +$27 610 |
| S5 | 468 | 48% | +$101 027 |
| S8 | 133 | 53% | +$78 633 |
| S9 | 133 | 51% | +$64 777 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 13 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
