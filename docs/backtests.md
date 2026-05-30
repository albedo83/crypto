# Rolling backtests

**Générée le** : 2026-05-30 09:02 UTC
**Bot version** : v12.9.1
**Données jusqu'à** : 2026-05-30
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.9.1)

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
| 28 mois | 2024-01-30 | $300 | $2 190 456 | +$2 190 156 | +730051.9% | -74.3% | 1129 | 52% | S5 |
| 28 mois | 2024-01-30 | $500 | $3 651 002 | +$3 650 502 | +730100.4% | -74.3% | 1129 | 52% | S5 |
| 28 mois | 2024-01-30 | $1 000 | $7 301 909 | +$7 300 909 | +730090.9% | -74.3% | 1129 | 52% | S5 |
| 28 mois | 2024-01-30 | $2 000 | $14 603 851 | +$14 601 851 | +730092.6% | -74.3% | 1129 | 52% | S5 |
| 12 mois | 2025-05-30 | $300 | $62 018 | +$61 718 | +20572.7% | -40.3% | 466 | 55% | S5 |
| 12 mois | 2025-05-30 | $500 | $103 364 | +$102 864 | +20572.8% | -40.3% | 466 | 55% | S5 |
| 12 mois | 2025-05-30 | $1 000 | $206 729 | +$205 729 | +20572.9% | -40.3% | 466 | 55% | S5 |
| 12 mois | 2025-05-30 | $2 000 | $413 459 | +$411 459 | +20572.9% | -40.3% | 466 | 55% | S5 |
| 6 mois | 2025-11-30 | $300 | $3 680 | +$3 380 | +1126.6% | -32.9% | 235 | 52% | S5 |
| 6 mois | 2025-11-30 | $500 | $6 133 | +$5 633 | +1126.6% | -32.9% | 235 | 52% | S5 |
| 6 mois | 2025-11-30 | $1 000 | $12 266 | +$11 266 | +1126.6% | -32.9% | 235 | 52% | S5 |
| 6 mois | 2025-11-30 | $2 000 | $24 531 | +$22 531 | +1126.6% | -32.9% | 235 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $3 959 | +$3 659 | +1219.6% | -32.9% | 232 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $6 598 | +$6 098 | +1219.6% | -32.9% | 232 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $13 196 | +$12 196 | +1219.6% | -32.9% | 232 | 52% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $26 391 | +$24 391 | +1219.6% | -32.9% | 232 | 52% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $3 192 | +$2 892 | +963.9% | -32.9% | 206 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $5 320 | +$4 820 | +963.9% | -32.9% | 206 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $10 639 | +$9 639 | +963.9% | -32.9% | 206 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $21 278 | +$19 278 | +963.9% | -32.9% | 206 | 50% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $1 688 | +$1 388 | +462.8% | -44.2% | 176 | 49% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $2 814 | +$2 314 | +462.8% | -44.2% | 176 | 49% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $5 628 | +$4 628 | +462.8% | -44.2% | 176 | 49% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $11 255 | +$9 255 | +462.8% | -44.2% | 176 | 49% | S5 |
| 3 mois | 2026-02-28 | $300 | $529 | +$229 | +76.3% | -16.3% | 133 | 47% | S5 |
| 3 mois | 2026-02-28 | $500 | $881 | +$381 | +76.3% | -16.3% | 133 | 47% | S5 |
| 3 mois | 2026-02-28 | $1 000 | $1 763 | +$763 | +76.3% | -16.3% | 133 | 47% | S5 |
| 3 mois | 2026-02-28 | $2 000 | $3 525 | +$1 525 | +76.3% | -16.3% | 133 | 47% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $473 | +$173 | +57.6% | -15.5% | 129 | 46% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $788 | +$288 | +57.6% | -15.5% | 129 | 46% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 576 | +$576 | +57.6% | -15.5% | 129 | 46% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $3 152 | +$1 152 | +57.6% | -15.5% | 129 | 46% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $489 | +$189 | +63.0% | -11.8% | 101 | 47% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $815 | +$315 | +63.0% | -11.8% | 101 | 47% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 630 | +$630 | +63.0% | -11.8% | 101 | 47% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $3 261 | +$1 261 | +63.0% | -11.8% | 101 | 47% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $467 | +$167 | +55.6% | -11.8% | 99 | 45% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $778 | +$278 | +55.6% | -11.8% | 99 | 45% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 556 | +$556 | +55.6% | -11.8% | 99 | 45% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $3 112 | +$1 112 | +55.6% | -11.8% | 99 | 45% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $445 | +$145 | +48.3% | -13.1% | 93 | 45% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $741 | +$241 | +48.3% | -13.1% | 93 | 45% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 483 | +$483 | +48.3% | -13.1% | 93 | 45% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 965 | +$965 | +48.3% | -13.1% | 93 | 45% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $467 | +$167 | +55.8% | -10.4% | 49 | 51% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $779 | +$279 | +55.8% | -10.4% | 49 | 51% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 558 | +$558 | +55.8% | -10.4% | 49 | 51% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $3 115 | +$1 115 | +55.8% | -10.4% | 49 | 51% | S5 |
| 1 mois | 2026-04-30 | $300 | $436 | +$136 | +45.3% | -10.4% | 45 | 44% | S5 |
| 1 mois | 2026-04-30 | $500 | $727 | +$227 | +45.3% | -10.4% | 45 | 44% | S5 |
| 1 mois | 2026-04-30 | $1 000 | $1 453 | +$453 | +45.3% | -10.4% | 45 | 44% | S5 |
| 1 mois | 2026-04-30 | $2 000 | $2 907 | +$907 | +45.3% | -10.4% | 45 | 44% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $456 | +$156 | +52.1% | -10.4% | 47 | 51% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $760 | +$260 | +52.1% | -10.4% | 47 | 51% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 521 | +$521 | +52.1% | -10.4% | 47 | 51% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $3 041 | +$1 041 | +52.1% | -10.4% | 47 | 51% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 109 | 55% | +$1 384 347 |
| S10 | 348 | 55% | +$716 769 |
| S5 | 452 | 49% | +$5 683 891 |
| S8 | 111 | 58% | +$2 384 694 |
| S9 | 109 | 50% | +$4 432 150 |

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
