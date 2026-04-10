# Rolling backtests

**Générée le** : 2026-04-10 13:17 UTC
**Bot version** : v11.3.3
**Données jusqu'à** : 2026-04-08

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier). Capital de départ : $1 000.

**Coûts backtest** : 14 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2023-12-08 | $40 226 | +$39 226 | +3922.6% | -44.2% | 1517 | 51% | S5 |
| 12 mois | 2025-04-08 | $3 959 | +$2 959 | +295.9% | -41.3% | 666 | 52% | S5 |
| 6 mois | 2025-10-08 | $2 344 | +$1 344 | +134.4% | -33.6% | 339 | 52% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $1 998 | +$998 | +99.8% | -33.6% | 291 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $1 628 | +$628 | +62.8% | -33.6% | 237 | 51% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $1 566 | +$566 | +56.6% | -33.6% | 185 | 50% | S5 |
| 3 mois | 2026-01-08 | $1 513 | +$513 | +51.3% | -33.6% | 174 | 51% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $1 349 | +$349 | +34.9% | -33.2% | 133 | 49% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 031 | +$31 | +3.1% | -13.1% | 75 | 49% | S10 |
| 1 mois | 2026-03-08 | $970 | $-30 | -3.0% | -13.1% | 69 | 48% | S10 |
| depuis 2026-04-01 | 2026-04-01 | $897 | $-103 | -10.3% | -13.0% | 15 | 40% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 72 | 57% | +$3 061 |
| S10 | 737 | 52% | +$6 434 |
| S5 | 449 | 48% | +$12 686 |
| S8 | 120 | 58% | +$5 909 |
| S9 | 139 | 50% | +$11 135 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 14 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Le backtest et le live bot prennent leurs décisions sur les **mêmes** features 4h. Le live collecte aussi OI delta 1h et crowding via les ticks 60s, mais ces valeurs ne sont **que loguées** (entry_ctx, market_snapshots) et n'entrent dans aucune condition d'entrée/sortie. Le backtest ne perd donc rien côté décisions.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
