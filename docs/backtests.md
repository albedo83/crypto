# Rolling backtests

**Générée le** : 2026-05-26 07:20 UTC
**Bot version** : v12.7.4
**Données jusqu'à** : 2026-05-26
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.7.4)

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

| Fenêtre | Start | Capital | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-01-26 | $300 | $1 449 406 | +$1 449 106 | +483035.5% | -74.3% | 1124 | 53% | S5 |
| 28 mois | 2024-01-26 | $500 | $2 415 633 | +$2 415 133 | +483026.5% | -74.3% | 1124 | 53% | S5 |
| 28 mois | 2024-01-26 | $1 000 | $4 831 304 | +$4 830 304 | +483030.4% | -74.3% | 1124 | 53% | S5 |
| 28 mois | 2024-01-26 | $2 000 | $9 662 671 | +$9 660 671 | +483033.6% | -74.3% | 1124 | 53% | S5 |
| 12 mois | 2025-05-26 | $300 | $30 337 | +$30 037 | +10012.4% | -41.4% | 465 | 55% | S5 |
| 12 mois | 2025-05-26 | $500 | $50 562 | +$50 062 | +10012.3% | -41.4% | 465 | 55% | S5 |
| 12 mois | 2025-05-26 | $1 000 | $101 123 | +$100 123 | +10012.3% | -41.4% | 465 | 55% | S5 |
| 12 mois | 2025-05-26 | $2 000 | $202 246 | +$200 246 | +10012.3% | -41.4% | 465 | 55% | S5 |
| 6 mois | 2025-11-26 | $300 | $4 139 | +$3 839 | +1279.5% | -32.9% | 237 | 53% | S5 |
| 6 mois | 2025-11-26 | $500 | $6 898 | +$6 398 | +1279.6% | -32.9% | 237 | 53% | S5 |
| 6 mois | 2025-11-26 | $1 000 | $13 795 | +$12 795 | +1279.5% | -32.9% | 237 | 53% | S5 |
| 6 mois | 2025-11-26 | $2 000 | $27 591 | +$25 591 | +1279.5% | -32.9% | 237 | 53% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $3 742 | +$3 442 | +1147.4% | -32.9% | 227 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $6 237 | +$5 737 | +1147.4% | -32.9% | 227 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $12 474 | +$11 474 | +1147.4% | -32.9% | 227 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $24 948 | +$22 948 | +1147.4% | -32.9% | 227 | 52% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $3 017 | +$2 717 | +905.7% | -32.9% | 201 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $5 029 | +$4 529 | +905.7% | -32.9% | 201 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $10 057 | +$9 057 | +905.7% | -32.9% | 201 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $20 115 | +$18 115 | +905.7% | -32.9% | 201 | 50% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $1 596 | +$1 296 | +432.0% | -44.2% | 171 | 50% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $2 660 | +$2 160 | +432.0% | -44.2% | 171 | 50% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $5 320 | +$4 320 | +432.0% | -44.2% | 171 | 50% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $10 640 | +$8 640 | +432.0% | -44.2% | 171 | 50% | S5 |
| 3 mois | 2026-02-26 | $300 | $479 | +$179 | +59.7% | -16.8% | 129 | 47% | S5 |
| 3 mois | 2026-02-26 | $500 | $799 | +$299 | +59.7% | -16.8% | 129 | 47% | S5 |
| 3 mois | 2026-02-26 | $1 000 | $1 597 | +$597 | +59.7% | -16.8% | 129 | 47% | S5 |
| 3 mois | 2026-02-26 | $2 000 | $3 194 | +$1 194 | +59.7% | -16.8% | 129 | 47% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $447 | +$147 | +49.0% | -15.5% | 124 | 47% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $745 | +$245 | +49.0% | -15.5% | 124 | 47% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 490 | +$490 | +49.0% | -15.5% | 124 | 47% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $2 979 | +$979 | +49.0% | -15.5% | 124 | 47% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $462 | +$162 | +54.1% | -11.8% | 96 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $771 | +$271 | +54.1% | -11.8% | 96 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 541 | +$541 | +54.1% | -11.8% | 96 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $3 083 | +$1 083 | +54.1% | -11.8% | 96 | 48% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $441 | +$141 | +47.1% | -11.8% | 94 | 47% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $735 | +$235 | +47.1% | -11.8% | 94 | 47% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 471 | +$471 | +47.1% | -11.8% | 94 | 47% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $2 941 | +$941 | +47.1% | -11.8% | 94 | 47% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $420 | +$120 | +40.1% | -13.1% | 88 | 47% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $701 | +$201 | +40.1% | -13.1% | 88 | 47% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 401 | +$401 | +40.1% | -13.1% | 88 | 47% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 803 | +$803 | +40.1% | -13.1% | 88 | 47% | S5 |
| 1 mois | 2026-04-26 | $300 | $385 | +$85 | +28.2% | -13.4% | 49 | 51% | S5 |
| 1 mois | 2026-04-26 | $500 | $641 | +$141 | +28.2% | -13.4% | 49 | 51% | S5 |
| 1 mois | 2026-04-26 | $1 000 | $1 282 | +$282 | +28.2% | -13.4% | 49 | 51% | S5 |
| 1 mois | 2026-04-26 | $2 000 | $2 564 | +$564 | +28.2% | -13.4% | 49 | 51% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $442 | +$142 | +47.2% | -10.4% | 44 | 55% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $736 | +$236 | +47.2% | -10.4% | 44 | 55% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 472 | +$472 | +47.2% | -10.4% | 44 | 55% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $2 945 | +$945 | +47.2% | -10.4% | 44 | 55% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $431 | +$131 | +43.7% | -10.4% | 42 | 55% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $719 | +$219 | +43.7% | -10.4% | 42 | 55% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 437 | +$437 | +43.7% | -10.4% | 42 | 55% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 875 | +$875 | +43.7% | -10.4% | 42 | 55% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 109 | 57% | +$957 551 |
| S10 | 353 | 55% | +$417 380 |
| S5 | 447 | 49% | +$3 543 847 |
| S8 | 112 | 57% | +$1 795 191 |
| S9 | 103 | 51% | +$2 946 702 |

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
