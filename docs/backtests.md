# Rolling backtests

**Générée le** : 2026-05-01 17:13 UTC
**Bot version** : v11.7.32
**Données jusqu'à** : 2026-05-01
**Capitaux testés** : $500 / $1 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 / $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.7.32)

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
| 28 mois | 2024-01-01 | $500 | $100 136 | +$99 636 | +19927.2% | -54.6% | 1088 | 53% | S9 |
| 28 mois | 2024-01-01 | $1 000 | $200 270 | +$199 270 | +19927.0% | -54.6% | 1088 | 53% | S9 |
| 12 mois | 2025-05-01 | $500 | $13 281 | +$12 781 | +2556.2% | -30.7% | 459 | 55% | S9 |
| 12 mois | 2025-05-01 | $1 000 | $26 562 | +$25 562 | +2556.2% | -30.7% | 459 | 55% | S9 |
| 6 mois | 2025-11-01 | $500 | $2 608 | +$2 108 | +421.7% | -30.7% | 231 | 54% | S9 |
| 6 mois | 2025-11-01 | $1 000 | $5 217 | +$4 217 | +421.7% | -30.7% | 231 | 54% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $1 734 | +$1 234 | +246.8% | -30.7% | 189 | 51% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $3 468 | +$2 468 | +246.8% | -30.7% | 189 | 51% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $1 504 | +$1 004 | +200.9% | -30.7% | 163 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $3 009 | +$2 009 | +200.9% | -30.7% | 163 | 49% | S9 |
| 3 mois | 2026-02-01 | $500 | $852 | +$352 | +70.4% | -30.7% | 134 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $852 | +$352 | +70.4% | -30.7% | 134 | 48% | S9 |
| 3 mois | 2026-02-01 | $1 000 | $1 704 | +$704 | +70.4% | -30.7% | 134 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $1 704 | +$704 | +70.4% | -30.7% | 134 | 48% | S9 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $453 | $-47 | -9.5% | -30.7% | 86 | 42% | S10 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $905 | $-95 | -9.5% | -30.7% | 86 | 42% | S10 |
| 1 mois | 2026-04-01 | $500 | $523 | +$23 | +4.7% | -19.5% | 48 | 44% | S9 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $523 | +$23 | +4.7% | -19.5% | 48 | 44% | S9 |
| 1 mois | 2026-04-01 | $1 000 | $1 047 | +$47 | +4.7% | -19.5% | 48 | 44% | S9 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 047 | +$47 | +4.7% | -19.5% | 48 | 44% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 72 | 54% | +$349 |
| S10 | 344 | 57% | +$29 545 |
| S5 | 447 | 48% | +$25 721 |
| S8 | 108 | 61% | +$52 928 |
| S9 | 117 | 53% | +$90 727 |

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
