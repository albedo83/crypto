# Rolling backtests

**Générée le** : 2026-05-09 06:52 UTC
**Bot version** : v11.10.2
**Données jusqu'à** : 2026-05-09
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.10.2)

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
| 28 mois | 2024-01-09 | $300 | $953 683 | +$953 383 | +317794.3% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-09 | $500 | $1 589 479 | +$1 588 979 | +317795.8% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-09 | $1 000 | $3 179 003 | +$3 178 003 | +317800.3% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-09 | $2 000 | $6 358 038 | +$6 356 038 | +317801.9% | -56.7% | 1102 | 53% | S9 |
| 12 mois | 2025-05-09 | $300 | $23 733 | +$23 433 | +7811.0% | -37.2% | 464 | 55% | S9 |
| 12 mois | 2025-05-09 | $500 | $39 555 | +$39 055 | +7811.1% | -37.2% | 464 | 55% | S9 |
| 12 mois | 2025-05-09 | $1 000 | $79 111 | +$78 111 | +7811.1% | -37.2% | 464 | 55% | S9 |
| 12 mois | 2025-05-09 | $2 000 | $158 223 | +$156 223 | +7811.1% | -37.2% | 464 | 55% | S9 |
| 6 mois | 2025-11-09 | $300 | $2 483 | +$2 183 | +727.6% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-09 | $500 | $4 138 | +$3 638 | +727.6% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-09 | $1 000 | $8 276 | +$7 276 | +727.6% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-09 | $2 000 | $16 552 | +$14 552 | +727.6% | -32.7% | 229 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $2 045 | +$1 745 | +581.5% | -32.7% | 203 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $3 408 | +$2 908 | +581.5% | -32.7% | 203 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $6 815 | +$5 815 | +581.5% | -32.7% | 203 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $13 631 | +$11 631 | +581.5% | -32.7% | 203 | 53% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $1 713 | +$1 413 | +471.0% | -32.7% | 177 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $2 855 | +$2 355 | +471.0% | -32.7% | 177 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $5 710 | +$4 710 | +471.0% | -32.7% | 177 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $11 419 | +$9 419 | +471.0% | -32.7% | 177 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $744 | +$444 | +148.0% | -51.4% | 148 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $1 240 | +$740 | +148.0% | -51.4% | 148 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $2 480 | +$1 480 | +148.0% | -51.4% | 148 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $4 960 | +$2 960 | +148.0% | -51.4% | 148 | 49% | S9 |
| 3 mois | 2026-02-09 | $300 | $1 012 | +$712 | +237.2% | -29.3% | 131 | 50% | S8 |
| 3 mois | 2026-02-09 | $500 | $1 686 | +$1 186 | +237.2% | -29.3% | 131 | 50% | S8 |
| 3 mois | 2026-02-09 | $1 000 | $3 372 | +$2 372 | +237.2% | -29.3% | 131 | 50% | S8 |
| 3 mois | 2026-02-09 | $2 000 | $6 744 | +$4 744 | +237.2% | -29.3% | 131 | 50% | S8 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $339 | +$39 | +13.0% | -27.7% | 100 | 45% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $565 | +$65 | +13.0% | -27.7% | 100 | 45% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 130 | +$130 | +13.0% | -27.7% | 100 | 45% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $2 260 | +$260 | +13.0% | -27.7% | 100 | 45% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $371 | +$71 | +23.6% | -24.1% | 72 | 46% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $618 | +$118 | +23.6% | -24.1% | 72 | 46% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 236 | +$236 | +23.6% | -24.1% | 72 | 46% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $2 472 | +$472 | +23.6% | -24.1% | 72 | 46% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $334 | +$34 | +11.2% | -24.0% | 70 | 44% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $556 | +$56 | +11.2% | -24.0% | 70 | 44% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 112 | +$112 | +11.2% | -24.0% | 70 | 44% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $2 224 | +$224 | +11.2% | -24.0% | 70 | 44% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $335 | +$35 | +11.6% | -21.7% | 64 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $558 | +$58 | +11.6% | -21.7% | 64 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 116 | +$116 | +11.6% | -21.7% | 64 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 232 | +$232 | +11.6% | -21.7% | 64 | 45% | S1 |
| 1 mois | 2026-04-09 | $300 | $408 | +$108 | +35.9% | -18.0% | 49 | 51% | S5 |
| 1 mois | 2026-04-09 | $500 | $679 | +$179 | +35.9% | -18.0% | 49 | 51% | S5 |
| 1 mois | 2026-04-09 | $1 000 | $1 359 | +$359 | +35.9% | -18.0% | 49 | 51% | S5 |
| 1 mois | 2026-04-09 | $2 000 | $2 718 | +$718 | +35.9% | -18.0% | 49 | 51% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $408 | +$108 | +36.0% | -3.6% | 19 | 68% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $680 | +$180 | +36.0% | -3.6% | 19 | 68% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 360 | +$360 | +36.0% | -3.6% | 19 | 68% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $2 719 | +$719 | +36.0% | -3.6% | 19 | 68% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $396 | +$96 | +31.9% | -3.6% | 17 | 71% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $660 | +$160 | +31.9% | -3.6% | 17 | 71% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 319 | +$319 | +31.9% | -3.6% | 17 | 71% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 638 | +$638 | +31.9% | -3.6% | 17 | 71% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 74 | 55% | +$637 860 |
| S10 | 350 | 57% | +$584 167 |
| S5 | 452 | 49% | +$1 165 572 |
| S8 | 108 | 61% | +$1 797 087 |
| S9 | 118 | 52% | +$2 171 351 |

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
