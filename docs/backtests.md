# Rolling backtests

**Générée le** : 2026-05-30 15:51 UTC
**Bot version** : v12.9.7
**Données jusqu'à** : 2026-05-30
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.9.7)

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
| 28 mois | 2024-01-30 | $300 | $5 280 124 | +$5 279 824 | +1759941.4% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $500 | $8 800 640 | +$8 800 140 | +1760027.9% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $1 000 | $17 601 520 | +$17 600 520 | +1760052.0% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $2 000 | $35 202 784 | +$35 200 784 | +1760039.2% | -77.5% | 1164 | 52% | S1 |
| 12 mois | 2025-05-30 | $300 | $101 657 | +$101 357 | +33785.7% | -54.7% | 474 | 55% | S1 |
| 12 mois | 2025-05-30 | $500 | $169 431 | +$168 931 | +33786.2% | -54.7% | 474 | 55% | S1 |
| 12 mois | 2025-05-30 | $1 000 | $338 865 | +$337 865 | +33786.5% | -54.7% | 474 | 55% | S1 |
| 12 mois | 2025-05-30 | $2 000 | $677 729 | +$675 729 | +33786.5% | -54.7% | 474 | 55% | S1 |
| 6 mois | 2025-11-30 | $300 | $3 078 | +$2 778 | +926.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $500 | $5 130 | +$4 630 | +926.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $1 000 | $10 261 | +$9 261 | +926.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $2 000 | $20 522 | +$18 522 | +926.1% | -54.7% | 240 | 52% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $3 312 | +$3 012 | +1003.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $5 519 | +$5 019 | +1003.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $11 039 | +$10 039 | +1003.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $22 078 | +$20 078 | +1003.9% | -54.7% | 237 | 51% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $2 670 | +$2 370 | +790.0% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $4 450 | +$3 950 | +790.0% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $8 900 | +$7 900 | +790.0% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $17 801 | +$15 801 | +790.0% | -54.7% | 211 | 49% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $2 645 | +$2 345 | +781.8% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $4 409 | +$3 909 | +781.8% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $8 818 | +$7 818 | +781.8% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $17 636 | +$15 636 | +781.8% | -42.8% | 178 | 50% | S1 |
| 3 mois | 2026-02-28 | $300 | $799 | +$499 | +166.5% | -16.3% | 134 | 47% | S1 |
| 3 mois | 2026-02-28 | $500 | $1 332 | +$832 | +166.5% | -16.3% | 134 | 47% | S1 |
| 3 mois | 2026-02-28 | $1 000 | $2 665 | +$1 665 | +166.5% | -16.3% | 134 | 47% | S1 |
| 3 mois | 2026-02-28 | $2 000 | $5 330 | +$3 330 | +166.5% | -16.3% | 134 | 47% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $715 | +$415 | +138.2% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $1 191 | +$691 | +138.2% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $2 382 | +$1 382 | +138.2% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $4 765 | +$2 765 | +138.2% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $740 | +$440 | +146.5% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $1 233 | +$733 | +146.5% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $2 465 | +$1 465 | +146.5% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $4 930 | +$2 930 | +146.5% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $706 | +$406 | +135.2% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $1 176 | +$676 | +135.2% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $2 352 | +$1 352 | +135.2% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $4 705 | +$2 705 | +135.2% | -11.8% | 100 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $672 | +$372 | +124.1% | -13.1% | 94 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $1 121 | +$621 | +124.1% | -13.1% | 94 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $2 241 | +$1 241 | +124.1% | -13.1% | 94 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $4 483 | +$2 483 | +124.1% | -13.1% | 94 | 46% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $706 | +$406 | +135.5% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $1 177 | +$677 | +135.5% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $2 355 | +$1 355 | +135.5% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $4 710 | +$2 710 | +135.5% | -10.4% | 50 | 52% | S1 |
| 1 mois | 2026-04-30 | $300 | $662 | +$362 | +120.7% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $500 | $1 104 | +$604 | +120.7% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $1 000 | $2 207 | +$1 207 | +120.7% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $2 000 | $4 415 | +$2 415 | +120.7% | -10.4% | 46 | 46% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $690 | +$390 | +130.0% | -10.4% | 48 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $1 150 | +$650 | +130.0% | -10.4% | 48 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $2 300 | +$1 300 | +130.0% | -10.4% | 48 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $4 600 | +$2 600 | +130.0% | -10.4% | 48 | 52% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 113 | 57% | +$14 330 290 |
| S10 | 348 | 55% | +$1 357 976 |
| S5 | 454 | 49% | +$9 660 706 |
| S8 | 119 | 54% | +$3 001 691 |
| S9 | 130 | 49% | +$6 850 120 |

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
