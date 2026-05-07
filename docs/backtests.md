# Rolling backtests

**Générée le** : 2026-05-07 10:10 UTC
**Bot version** : v11.8.4
**Données jusqu'à** : 2026-05-07
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.8.4)

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
| 28 mois | 2024-01-07 | $300 | $61 820 | +$61 520 | +20506.8% | -54.6% | 1098 | 53% | S9 |
| 28 mois | 2024-01-07 | $500 | $103 033 | +$102 533 | +20506.5% | -54.6% | 1098 | 53% | S9 |
| 28 mois | 2024-01-07 | $1 000 | $206 063 | +$205 063 | +20506.3% | -54.6% | 1098 | 53% | S9 |
| 28 mois | 2024-01-07 | $2 000 | $412 128 | +$410 128 | +20506.4% | -54.6% | 1098 | 53% | S9 |
| 12 mois | 2025-05-07 | $300 | $8 042 | +$7 742 | +2580.5% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-07 | $500 | $13 403 | +$12 903 | +2580.5% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-07 | $1 000 | $26 805 | +$25 805 | +2580.5% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-07 | $2 000 | $53 610 | +$51 610 | +2580.5% | -30.7% | 462 | 55% | S9 |
| 6 mois | 2025-11-07 | $300 | $1 202 | +$902 | +300.7% | -30.7% | 230 | 52% | S9 |
| 6 mois | 2025-11-07 | $500 | $2 003 | +$1 503 | +300.7% | -30.7% | 230 | 52% | S9 |
| 6 mois | 2025-11-07 | $1 000 | $4 007 | +$3 007 | +300.7% | -30.7% | 230 | 52% | S9 |
| 6 mois | 2025-11-07 | $2 000 | $8 014 | +$6 014 | +300.7% | -30.7% | 230 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $1 071 | +$771 | +256.8% | -30.7% | 199 | 51% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $1 784 | +$1 284 | +256.8% | -30.7% | 199 | 51% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $3 568 | +$2 568 | +256.8% | -30.7% | 199 | 51% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $7 137 | +$5 137 | +256.8% | -30.7% | 199 | 51% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $929 | +$629 | +209.6% | -30.7% | 173 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $1 548 | +$1 048 | +209.6% | -30.7% | 173 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $3 096 | +$2 096 | +209.6% | -30.7% | 173 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $6 192 | +$4 192 | +209.6% | -30.7% | 173 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $526 | +$226 | +75.3% | -30.7% | 144 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $877 | +$377 | +75.3% | -30.7% | 144 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $1 753 | +$753 | +75.3% | -30.7% | 144 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $3 506 | +$1 506 | +75.3% | -30.7% | 144 | 48% | S9 |
| 3 mois | 2026-02-07 | $300 | $626 | +$326 | +108.6% | -30.7% | 128 | 50% | S8 |
| 3 mois | 2026-02-07 | $500 | $1 043 | +$543 | +108.6% | -30.7% | 128 | 50% | S8 |
| 3 mois | 2026-02-07 | $1 000 | $2 086 | +$1 086 | +108.6% | -30.7% | 128 | 50% | S8 |
| 3 mois | 2026-02-07 | $2 000 | $4 173 | +$2 173 | +108.6% | -30.7% | 128 | 50% | S8 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $279 | $-21 | -6.9% | -30.7% | 96 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $466 | $-34 | -6.9% | -30.7% | 96 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $931 | $-69 | -6.9% | -30.7% | 96 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $1 863 | $-137 | -6.9% | -30.7% | 96 | 43% | S1 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $300 | $299 | $-1 | -0.3% | -27.2% | 68 | 43% | S1 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $500 | $498 | $-2 | -0.3% | -27.2% | 68 | 43% | S1 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $1 000 | $997 | $-3 | -0.3% | -27.2% | 68 | 43% | S1 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $2 000 | $1 993 | $-7 | -0.3% | -27.2% | 68 | 43% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $300 | $274 | $-26 | -8.6% | -27.2% | 66 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $500 | $457 | $-43 | -8.6% | -27.2% | 66 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $1 000 | $914 | $-86 | -8.6% | -27.2% | 66 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $2 000 | $1 827 | $-173 | -8.6% | -27.2% | 66 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $323 | +$23 | +7.7% | -19.5% | 58 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $539 | +$39 | +7.7% | -19.5% | 58 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 077 | +$77 | +7.7% | -19.5% | 58 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 155 | +$155 | +7.7% | -19.5% | 58 | 45% | S1 |
| 1 mois | 2026-04-07 | $300 | $335 | +$35 | +11.6% | -19.5% | 48 | 46% | S1 |
| 1 mois | 2026-04-07 | $500 | $558 | +$58 | +11.6% | -19.5% | 48 | 46% | S1 |
| 1 mois | 2026-04-07 | $1 000 | $1 116 | +$116 | +11.6% | -19.5% | 48 | 46% | S1 |
| 1 mois | 2026-04-07 | $2 000 | $2 233 | +$233 | +11.6% | -19.5% | 48 | 46% | S1 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $300 | $321 | +$21 | +7.1% | -5.4% | 15 | 60% | S1 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $500 | $536 | +$36 | +7.1% | -5.4% | 15 | 60% | S1 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $1 000 | $1 071 | +$71 | +7.1% | -5.4% | 15 | 60% | S1 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $2 000 | $2 142 | +$142 | +7.1% | -5.4% | 15 | 60% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $296 | $-4 | -1.3% | -3.5% | 13 | 46% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $494 | $-6 | -1.3% | -3.5% | 13 | 46% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $987 | $-13 | -1.3% | -3.5% | 13 | 46% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $1 974 | $-26 | -1.3% | -3.5% | 13 | 46% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 74 | 55% | +$25 572 |
| S10 | 350 | 56% | +$60 053 |
| S5 | 449 | 48% | +$58 486 |
| S8 | 108 | 61% | +$105 856 |
| S9 | 117 | 52% | +$160 161 |

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
