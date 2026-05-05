# Rolling backtests

**Générée le** : 2026-05-05 06:34 UTC
**Bot version** : v11.8.3
**Données jusqu'à** : 2026-05-05
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.8.3)

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
| 28 mois | 2024-01-05 | $300 | $61 078 | +$60 778 | +20259.3% | -54.6% | 1094 | 53% | S9 |
| 28 mois | 2024-01-05 | $500 | $101 795 | +$101 295 | +20259.0% | -54.6% | 1094 | 53% | S9 |
| 28 mois | 2024-01-05 | $1 000 | $203 588 | +$202 588 | +20258.8% | -54.6% | 1094 | 53% | S9 |
| 28 mois | 2024-01-05 | $2 000 | $407 178 | +$405 178 | +20258.9% | -54.6% | 1094 | 53% | S9 |
| 12 mois | 2025-05-05 | $300 | $8 006 | +$7 706 | +2568.6% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-05 | $500 | $13 343 | +$12 843 | +2568.7% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-05 | $1 000 | $26 686 | +$25 686 | +2568.6% | -30.7% | 462 | 55% | S9 |
| 12 mois | 2025-05-05 | $2 000 | $53 373 | +$51 373 | +2568.6% | -30.7% | 462 | 55% | S9 |
| 6 mois | 2025-11-05 | $300 | $1 508 | +$1 208 | +402.6% | -30.7% | 227 | 53% | S9 |
| 6 mois | 2025-11-05 | $500 | $2 513 | +$2 013 | +402.6% | -30.7% | 227 | 53% | S9 |
| 6 mois | 2025-11-05 | $1 000 | $5 026 | +$4 026 | +402.6% | -30.7% | 227 | 53% | S9 |
| 6 mois | 2025-11-05 | $2 000 | $10 052 | +$8 052 | +402.6% | -30.7% | 227 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $1 072 | +$772 | +257.2% | -30.7% | 195 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $1 786 | +$1 286 | +257.2% | -30.7% | 195 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $3 572 | +$2 572 | +257.2% | -30.7% | 195 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $7 145 | +$5 145 | +257.2% | -30.7% | 195 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $918 | +$618 | +205.9% | -30.7% | 169 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $1 529 | +$1 029 | +205.9% | -30.7% | 169 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $3 059 | +$2 059 | +205.9% | -30.7% | 169 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $6 118 | +$4 118 | +205.9% | -30.7% | 169 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $520 | +$220 | +73.2% | -30.7% | 140 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $866 | +$366 | +73.2% | -30.7% | 140 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $1 732 | +$732 | +73.2% | -30.7% | 140 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $3 464 | +$1 464 | +73.2% | -30.7% | 140 | 48% | S9 |
| 3 mois | 2026-02-05 | $300 | $593 | +$293 | +97.5% | -30.7% | 134 | 49% | S9 |
| 3 mois | 2026-02-05 | $500 | $988 | +$488 | +97.5% | -30.7% | 134 | 49% | S9 |
| 3 mois | 2026-02-05 | $1 000 | $1 975 | +$975 | +97.5% | -30.7% | 134 | 49% | S9 |
| 3 mois | 2026-02-05 | $2 000 | $3 950 | +$1 950 | +97.5% | -30.7% | 134 | 49% | S9 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $276 | $-24 | -8.0% | -30.7% | 92 | 42% | S10 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $460 | $-40 | -8.0% | -30.7% | 92 | 42% | S10 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $920 | $-80 | -8.0% | -30.7% | 92 | 42% | S10 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $1 840 | $-160 | -8.0% | -30.7% | 92 | 42% | S10 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $272 | $-28 | -9.4% | -25.2% | 56 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $453 | $-47 | -9.4% | -25.2% | 56 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $906 | $-94 | -9.4% | -25.2% | 56 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $1 812 | $-188 | -9.4% | -25.2% | 56 | 41% | S1 |
| 1 mois | 2026-04-05 | $300 | $313 | +$13 | +4.4% | -19.5% | 47 | 43% | S1 |
| 1 mois | 2026-04-05 | $500 | $522 | +$22 | +4.4% | -19.5% | 47 | 43% | S1 |
| 1 mois | 2026-04-05 | $1 000 | $1 044 | +$44 | +4.4% | -19.5% | 47 | 43% | S1 |
| 1 mois | 2026-04-05 | $2 000 | $2 088 | +$88 | +4.4% | -19.5% | 47 | 43% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $309 | +$9 | +3.0% | -4.4% | 9 | 67% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $515 | +$15 | +3.0% | -4.4% | 9 | 67% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 030 | +$30 | +3.0% | -4.4% | 9 | 67% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 061 | +$61 | +3.0% | -4.4% | 9 | 67% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 74 | 55% | +$21 432 |
| S10 | 347 | 56% | +$62 446 |
| S5 | 448 | 48% | +$55 283 |
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
