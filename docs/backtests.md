# Rolling backtests

**Générée le** : 2026-05-30 15:57 UTC
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
| 28 mois | 2024-01-30 | $300 | $5 507 217 | +$5 506 917 | +1835639.0% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $500 | $9 178 790 | +$9 178 290 | +1835658.1% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $1 000 | $18 357 761 | +$18 356 761 | +1835676.1% | -77.5% | 1164 | 52% | S1 |
| 28 mois | 2024-01-30 | $2 000 | $36 715 912 | +$36 713 912 | +1835695.6% | -77.5% | 1164 | 52% | S1 |
| 12 mois | 2025-05-30 | $300 | $116 799 | +$116 499 | +38833.1% | -54.7% | 472 | 55% | S1 |
| 12 mois | 2025-05-30 | $500 | $194 668 | +$194 168 | +38833.6% | -54.7% | 472 | 55% | S1 |
| 12 mois | 2025-05-30 | $1 000 | $389 336 | +$388 336 | +38833.6% | -54.7% | 472 | 55% | S1 |
| 12 mois | 2025-05-30 | $2 000 | $778 672 | +$776 672 | +38833.6% | -54.7% | 472 | 55% | S1 |
| 6 mois | 2025-11-30 | $300 | $3 210 | +$2 910 | +970.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $500 | $5 351 | +$4 851 | +970.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $1 000 | $10 701 | +$9 701 | +970.1% | -54.7% | 240 | 52% | S1 |
| 6 mois | 2025-11-30 | $2 000 | $21 402 | +$19 402 | +970.1% | -54.7% | 240 | 52% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $3 423 | +$3 123 | +1040.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $5 705 | +$5 205 | +1040.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $11 409 | +$10 409 | +1040.9% | -54.7% | 237 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $22 818 | +$20 818 | +1040.9% | -54.7% | 237 | 51% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $2 785 | +$2 485 | +828.2% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $4 641 | +$4 141 | +828.2% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $9 282 | +$8 282 | +828.2% | -54.7% | 211 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $18 564 | +$16 564 | +828.2% | -54.7% | 211 | 49% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $2 759 | +$2 459 | +819.7% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $4 598 | +$4 098 | +819.6% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $9 197 | +$8 197 | +819.7% | -42.8% | 178 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $18 393 | +$16 393 | +819.7% | -42.8% | 178 | 50% | S1 |
| 3 mois | 2026-02-28 | $300 | $789 | +$489 | +163.0% | -16.8% | 133 | 46% | S1 |
| 3 mois | 2026-02-28 | $500 | $1 315 | +$815 | +163.0% | -16.8% | 133 | 46% | S1 |
| 3 mois | 2026-02-28 | $1 000 | $2 630 | +$1 630 | +163.0% | -16.8% | 133 | 46% | S1 |
| 3 mois | 2026-02-28 | $2 000 | $5 261 | +$3 261 | +163.0% | -16.8% | 133 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $745 | +$445 | +148.5% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $1 242 | +$742 | +148.5% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $2 485 | +$1 485 | +148.5% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $4 969 | +$2 969 | +148.5% | -15.5% | 130 | 46% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $771 | +$471 | +157.1% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $1 285 | +$785 | +157.1% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $2 571 | +$1 571 | +157.1% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $5 142 | +$3 142 | +157.1% | -11.8% | 102 | 47% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $736 | +$436 | +145.3% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $1 227 | +$727 | +145.3% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $2 453 | +$1 453 | +145.3% | -11.8% | 100 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $4 907 | +$2 907 | +145.3% | -11.8% | 100 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $775 | +$475 | +158.2% | -11.8% | 94 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $1 291 | +$791 | +158.2% | -11.8% | 94 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $2 582 | +$1 582 | +158.2% | -11.8% | 94 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $5 164 | +$3 164 | +158.2% | -11.8% | 94 | 48% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $737 | +$437 | +145.6% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $1 228 | +$728 | +145.6% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $2 456 | +$1 456 | +145.6% | -10.4% | 50 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $4 912 | +$2 912 | +145.6% | -10.4% | 50 | 52% | S1 |
| 1 mois | 2026-04-30 | $300 | $693 | +$393 | +131.1% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $500 | $1 156 | +$656 | +131.1% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $1 000 | $2 311 | +$1 311 | +131.1% | -10.4% | 46 | 46% | S1 |
| 1 mois | 2026-04-30 | $2 000 | $4 623 | +$2 623 | +131.1% | -10.4% | 46 | 46% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $716 | +$416 | +138.7% | -10.4% | 46 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $1 193 | +$693 | +138.7% | -10.4% | 46 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $2 387 | +$1 387 | +138.7% | -10.4% | 46 | 52% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $4 773 | +$2 773 | +138.7% | -10.4% | 46 | 52% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 113 | 57% | +$14 331 261 |
| S10 | 348 | 55% | +$1 358 068 |
| S5 | 454 | 49% | +$11 172 103 |
| S8 | 119 | 54% | +$3 001 895 |
| S9 | 130 | 49% | +$6 850 585 |

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
