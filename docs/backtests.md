# Rolling backtests

**Générée le** : 2026-06-02 06:26 UTC
**Bot version** : v12.11.0
**Données jusqu'à** : 2026-06-01
**Capitaux testés** : $500

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $15,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.11.0)

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
| 28 mois | 2024-02-01 | $241 598 | +$241 098 | +48219.6% | -77.5% | 1164 | 52% | S5 |
| depuis 2024-07-01 | 2024-07-01 | $223 198 | +$222 698 | +44539.6% | -77.5% | 943 | 53% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $208 163 | +$207 663 | +41532.6% | -68.3% | 894 | 52% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $223 098 | +$222 598 | +44519.6% | -51.4% | 845 | 53% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $234 647 | +$234 147 | +46829.4% | -51.4% | 804 | 54% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $237 856 | +$237 356 | +47471.2% | -51.4% | 772 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $192 932 | +$192 432 | +38486.4% | -42.2% | 721 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $164 104 | +$163 604 | +32720.9% | -38.4% | 678 | 54% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $147 885 | +$147 385 | +29476.9% | -40.3% | 642 | 54% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $132 052 | +$131 552 | +26310.4% | -49.3% | 593 | 55% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $117 011 | +$116 511 | +23302.2% | -54.3% | 553 | 55% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $111 845 | +$111 345 | +22269.1% | -55.2% | 516 | 55% | S1 |
| 12 mois | 2025-06-01 | $108 862 | +$108 362 | +21672.4% | -55.5% | 473 | 55% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $108 862 | +$108 362 | +21672.4% | -55.5% | 473 | 55% | S1 |
| depuis 2025-07-01 | 2025-07-01 | $80 058 | +$79 558 | +15911.5% | -54.7% | 439 | 54% | S1 |
| depuis 2025-08-01 | 2025-08-01 | $78 242 | +$77 742 | +15548.4% | -54.7% | 407 | 55% | S1 |
| depuis 2025-09-01 | 2025-09-01 | $76 440 | +$75 940 | +15188.0% | -54.7% | 369 | 55% | S1 |
| depuis 2025-10-01 | 2025-10-01 | $51 371 | +$50 871 | +10174.1% | -54.7% | 325 | 55% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $18 590 | +$18 090 | +3617.9% | -54.7% | 282 | 54% | S1 |
| 6 mois | 2025-12-01 | $5 860 | +$5 360 | +1072.0% | -54.7% | 240 | 51% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $5 860 | +$5 360 | +1072.0% | -54.7% | 240 | 51% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $4 806 | +$4 306 | +861.3% | -54.7% | 214 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $4 762 | +$4 262 | +852.4% | -42.8% | 181 | 50% | S1 |
| 3 mois | 2026-03-01 | $1 287 | +$787 | +157.3% | -15.5% | 133 | 47% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 287 | +$787 | +157.3% | -15.5% | 133 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 331 | +$831 | +166.2% | -11.8% | 105 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 316 | +$816 | +163.1% | -11.8% | 97 | 48% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 272 | +$772 | +154.3% | -10.4% | 53 | 53% | S1 |
| 1 mois | 2026-05-01 | $1 236 | +$736 | +147.2% | -10.4% | 49 | 53% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 236 | +$736 | +147.2% | -10.4% | 49 | 53% | S1 |
| depuis 2026-05-31 | 2026-05-31 | $507 | +$7 | +1.3% | 0.0% | 4 | 75% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 113 | 57% | +$34 481 |
| S10 | 347 | 54% | +$18 736 |
| S5 | 455 | 49% | +$87 783 |
| S8 | 120 | 53% | +$44 592 |
| S9 | 129 | 50% | +$55 506 |

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
