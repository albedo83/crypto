# Rolling backtests

**Générée le** : 2026-05-20 05:51 UTC
**Bot version** : v12.7.0
**Données jusqu'à** : 2026-05-20
**Capitaux testés** : $500 / $1 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 / $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.7.0)

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
| 28 mois | 2024-01-20 | $500 | $1 633 809 | +$1 633 309 | +326661.8% | -74.3% | 1124 | 52% | S9 |
| 28 mois | 2024-01-20 | $1 000 | $3 267 506 | +$3 266 506 | +326650.6% | -74.3% | 1124 | 52% | S9 |
| 12 mois | 2025-05-20 | $500 | $42 558 | +$42 058 | +8411.6% | -41.4% | 464 | 55% | S9 |
| 12 mois | 2025-05-20 | $1 000 | $85 115 | +$84 115 | +8411.5% | -41.4% | 464 | 55% | S9 |
| 6 mois | 2025-11-20 | $500 | $6 623 | +$6 123 | +1224.6% | -32.9% | 232 | 53% | S9 |
| 6 mois | 2025-11-20 | $1 000 | $13 246 | +$12 246 | +1224.6% | -32.9% | 232 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $5 152 | +$4 652 | +930.3% | -32.9% | 216 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $10 303 | +$9 303 | +930.3% | -32.9% | 216 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $4 154 | +$3 654 | +730.7% | -32.9% | 190 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $8 307 | +$7 307 | +730.7% | -32.9% | 190 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $2 197 | +$1 697 | +339.4% | -44.2% | 160 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $4 394 | +$3 394 | +339.4% | -44.2% | 160 | 49% | S9 |
| 3 mois | 2026-02-20 | $500 | $1 107 | +$607 | +121.3% | -19.0% | 129 | 50% | S8 |
| 3 mois | 2026-02-20 | $1 000 | $2 213 | +$1 213 | +121.3% | -19.0% | 129 | 50% | S8 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $615 | +$115 | +23.0% | -15.5% | 113 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 230 | +$230 | +23.0% | -15.5% | 113 | 46% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $637 | +$137 | +27.3% | -11.8% | 85 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 273 | +$273 | +27.3% | -11.8% | 85 | 47% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $607 | +$107 | +21.5% | -11.8% | 83 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 215 | +$215 | +21.5% | -11.8% | 83 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $579 | +$79 | +15.8% | -13.1% | 77 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 158 | +$158 | +15.8% | -13.1% | 77 | 45% | S1 |
| 1 mois | 2026-04-20 | $500 | $575 | +$75 | +15.1% | -11.1% | 44 | 50% | S1 |
| 1 mois | 2026-04-20 | $1 000 | $1 151 | +$151 | +15.1% | -11.1% | 44 | 50% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $608 | +$108 | +21.6% | -5.1% | 33 | 55% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 216 | +$216 | +21.6% | -5.1% | 33 | 55% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $594 | +$94 | +18.7% | -5.1% | 31 | 55% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 187 | +$187 | +18.7% | -5.1% | 31 | 55% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 109 | 57% | +$392 022 |
| S10 | 351 | 55% | +$222 824 |
| S5 | 446 | 48% | +$710 403 |
| S8 | 115 | 57% | +$734 874 |
| S9 | 103 | 51% | +$1 206 383 |

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
