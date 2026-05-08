# Rolling backtests

**Générée le** : 2026-05-08 15:08 UTC
**Bot version** : v11.9.2
**Données jusqu'à** : 2026-05-08
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.9.2)

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
| 28 mois | 2024-01-08 | $300 | $76 237 | +$75 937 | +25312.2% | -60.5% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $500 | $127 063 | +$126 563 | +25312.6% | -60.5% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $1 000 | $254 125 | +$253 125 | +25312.5% | -60.5% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $2 000 | $508 252 | +$506 252 | +25312.6% | -60.5% | 1102 | 53% | S9 |
| 12 mois | 2025-05-08 | $300 | $9 031 | +$8 731 | +2910.3% | -34.4% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $500 | $15 051 | +$14 551 | +2910.3% | -34.4% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $1 000 | $30 103 | +$29 103 | +2910.3% | -34.4% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $2 000 | $60 206 | +$58 206 | +2910.3% | -34.4% | 465 | 55% | S9 |
| 6 mois | 2025-11-08 | $300 | $1 375 | +$1 075 | +358.4% | -34.4% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $500 | $2 292 | +$1 792 | +358.4% | -34.4% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $1 000 | $4 584 | +$3 584 | +358.4% | -34.4% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $2 000 | $9 168 | +$7 168 | +358.4% | -34.4% | 229 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $1 161 | +$861 | +287.1% | -34.4% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $1 935 | +$1 435 | +287.1% | -34.4% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $3 871 | +$2 871 | +287.1% | -34.4% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $7 742 | +$5 742 | +287.1% | -34.4% | 203 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $1 003 | +$703 | +234.3% | -34.4% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $1 671 | +$1 171 | +234.3% | -34.4% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $3 343 | +$2 343 | +234.3% | -34.4% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $6 685 | +$4 685 | +234.3% | -34.4% | 177 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $559 | +$259 | +86.5% | -34.4% | 148 | 48% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $932 | +$432 | +86.5% | -34.4% | 148 | 48% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $1 865 | +$865 | +86.5% | -34.4% | 148 | 48% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $3 730 | +$1 730 | +86.5% | -34.4% | 148 | 48% | S5 |
| 3 mois | 2026-02-08 | $300 | $682 | +$382 | +127.5% | -34.4% | 131 | 50% | S5 |
| 3 mois | 2026-02-08 | $500 | $1 137 | +$637 | +127.5% | -34.4% | 131 | 50% | S5 |
| 3 mois | 2026-02-08 | $1 000 | $2 275 | +$1 275 | +127.5% | -34.4% | 131 | 50% | S5 |
| 3 mois | 2026-02-08 | $2 000 | $4 550 | +$2 550 | +127.5% | -34.4% | 131 | 50% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $274 | $-26 | -8.6% | -33.6% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $457 | $-43 | -8.6% | -33.6% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $914 | $-86 | -8.6% | -33.6% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $1 827 | $-173 | -8.6% | -33.6% | 100 | 43% | S1 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $300 | $300 | $-0 | -0.1% | -30.3% | 72 | 43% | S5 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $500 | $500 | $-0 | -0.1% | -30.3% | 72 | 43% | S5 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $1 000 | $999 | $-1 | -0.1% | -30.3% | 72 | 43% | S5 |
| depuis 2026-03-25 (paper) | 2026-03-25 | $2 000 | $1 999 | $-1 | -0.1% | -30.3% | 72 | 43% | S5 |
| depuis 2026-03-26 (live) | 2026-03-26 | $300 | $270 | $-30 | -10.1% | -30.3% | 70 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $500 | $450 | $-50 | -10.1% | -30.3% | 70 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $1 000 | $899 | $-101 | -10.1% | -30.3% | 70 | 41% | S1 |
| depuis 2026-03-26 (live) | 2026-03-26 | $2 000 | $1 798 | $-202 | -10.1% | -30.3% | 70 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $328 | +$28 | +9.3% | -21.7% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $547 | +$47 | +9.3% | -21.7% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 093 | +$93 | +9.3% | -21.7% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 186 | +$186 | +9.3% | -21.7% | 62 | 45% | S1 |
| 1 mois | 2026-04-08 | $300 | $364 | +$64 | +21.3% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $500 | $606 | +$106 | +21.3% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $1 000 | $1 213 | +$213 | +21.3% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $2 000 | $2 426 | +$426 | +21.3% | -18.0% | 50 | 50% | S5 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $300 | $328 | +$28 | +9.3% | -8.6% | 19 | 58% | S5 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $500 | $546 | +$46 | +9.3% | -8.6% | 19 | 58% | S5 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $1 000 | $1 093 | +$93 | +9.3% | -8.6% | 19 | 58% | S5 |
| depuis 2026-04-29 (junior) | 2026-04-29 | $2 000 | $2 185 | +$185 | +9.3% | -8.6% | 19 | 58% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $340 | +$40 | +13.3% | -6.7% | 16 | 50% | S9 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $567 | +$67 | +13.3% | -6.7% | 16 | 50% | S9 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 133 | +$133 | +13.3% | -6.7% | 16 | 50% | S9 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 266 | +$266 | +13.3% | -6.7% | 16 | 50% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 74 | 55% | +$30 730 |
| S10 | 350 | 57% | +$74 759 |
| S5 | 452 | 48% | +$116 533 |
| S8 | 108 | 61% | +$129 139 |
| S9 | 118 | 52% | +$155 091 |

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
