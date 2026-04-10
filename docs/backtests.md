# Rolling backtests

**Générée le** : 2026-04-10 13:02 UTC
**Bot version** : v11.3.3
**Données jusqu'à** : 2026-04-08

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier). Coûts : 12 bps par trade. Capital de départ : $1 000.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2023-12-08 | $45 360 | +$44 360 | +4436.0% | -43.6% | 1517 | 52% | S5 |
| 12 mois | 2025-04-08 | $4 167 | +$3 167 | +316.7% | -41.1% | 666 | 52% | S5 |
| 6 mois | 2025-10-08 | $2 405 | +$1 405 | +140.5% | -33.5% | 339 | 53% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $2 042 | +$1 042 | +104.2% | -33.5% | 291 | 53% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $1 658 | +$658 | +65.8% | -33.5% | 237 | 52% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $1 588 | +$588 | +58.8% | -33.5% | 185 | 51% | S5 |
| 3 mois | 2026-01-08 | $1 533 | +$533 | +53.3% | -33.5% | 174 | 52% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $1 363 | +$363 | +36.3% | -33.2% | 133 | 50% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 036 | +$36 | +3.6% | -13.0% | 75 | 51% | S10 |
| 1 mois | 2026-03-08 | $975 | $-25 | -2.5% | -13.0% | 69 | 49% | S10 |
| depuis 2026-04-01 | 2026-04-01 | $898 | $-102 | -10.2% | -12.9% | 15 | 40% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 72 | 57% | +$3 228 |
| S10 | 737 | 53% | +$7 811 |
| S5 | 449 | 48% | +$14 466 |
| S8 | 120 | 58% | +$6 539 |
| S9 | 139 | 50% | +$12 317 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 12 bps par trade (taker + slippage + funding, pas de multiplication levier).

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 12 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
