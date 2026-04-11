# Rolling backtests

**Générée le** : 2026-04-11 10:25 UTC
**Bot version** : v11.3.4
**Données jusqu'à** : 2026-04-08

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier). Capital de départ : $1 000.

**Coûts backtest** : 14 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres S10 actifs (v11.3.4)

- `S10_ALLOW_LONGS = False` → SHORT fades seulement (LONG fades perdaient $4.8k sur 28m, 45% WR — *fade panic = fail*)
- `S10_ALLOWED_TOKENS` (whitelist de 13 tokens) : AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD

Filtres dérivés de `backtest_s10_walkforward.py` (train 2023-10→2025-02, test 2025-02→2026-02 out-of-sample). **Impact validé sur le test OOS** : P&L +123% vs baseline, DD améliorée de 8.7pp. Le 28m in-sample change peu (les pertes LONG de 2024 sont compensées par les gagnants). Kill-switch : `S10_ALLOW_LONGS = True` et `S10_ALLOWED_TOKENS = set(ALL_SYMBOLS)` dans `analysis/bot/config.py`.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2023-12-08 | $40 330 | +$39 330 | +3933.0% | -52.9% | 1138 | 52% | S9 |
| 12 mois | 2025-04-08 | $8 007 | +$7 007 | +700.7% | -32.6% | 478 | 53% | S9 |
| 6 mois | 2025-10-08 | $3 939 | +$2 939 | +293.9% | -22.2% | 241 | 55% | S9 |
| depuis 2025-11-01 | 2025-11-01 | $3 112 | +$2 112 | +211.2% | -21.1% | 208 | 54% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 103 | +$1 103 | +110.3% | -21.1% | 163 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 007 | +$1 007 | +100.7% | -21.1% | 132 | 48% | S9 |
| 3 mois | 2026-01-08 | $1 935 | +$935 | +93.5% | -21.1% | 121 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 357 | +$357 | +35.7% | -31.0% | 100 | 48% | S9 |
| depuis 2026-03-01 | 2026-03-01 | $837 | $-163 | -16.3% | -21.1% | 50 | 42% | S10 |
| 1 mois | 2026-03-08 | $789 | $-211 | -21.1% | -21.1% | 46 | 39% | S10 |
| depuis 2026-04-01 | 2026-04-01 | $855 | $-145 | -14.5% | -14.5% | 12 | 33% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 71 | 52% | +$993 |
| S10 | 316 | 56% | +$7 313 |
| S5 | 489 | 50% | +$3 874 |
| S8 | 128 | 59% | +$11 073 |
| S9 | 134 | 48% | +$16 076 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 14 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
