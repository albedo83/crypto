# Rolling backtests

**Générée le** : 2026-05-08 15:45 UTC
**Bot version** : v11.10.0
**Données jusqu'à** : 2026-05-08
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.10.0)

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
| 28 mois | 2024-01-08 | $300 | $880 039 | +$879 739 | +293246.3% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $500 | $1 466 738 | +$1 466 238 | +293247.6% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $1 000 | $2 933 518 | +$2 932 518 | +293251.8% | -56.7% | 1102 | 53% | S9 |
| 28 mois | 2024-01-08 | $2 000 | $5 867 066 | +$5 865 066 | +293253.3% | -56.7% | 1102 | 53% | S9 |
| 12 mois | 2025-05-08 | $300 | $25 962 | +$25 662 | +8554.2% | -37.2% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $500 | $43 271 | +$42 771 | +8554.1% | -37.2% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $1 000 | $86 542 | +$85 542 | +8554.2% | -37.2% | 465 | 55% | S9 |
| 12 mois | 2025-05-08 | $2 000 | $173 085 | +$171 085 | +8554.2% | -37.2% | 465 | 55% | S9 |
| 6 mois | 2025-11-08 | $300 | $2 365 | +$2 065 | +688.2% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $500 | $3 941 | +$3 441 | +688.2% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $1 000 | $7 882 | +$6 882 | +688.2% | -32.7% | 229 | 53% | S9 |
| 6 mois | 2025-11-08 | $2 000 | $15 763 | +$13 763 | +688.2% | -32.7% | 229 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $1 870 | +$1 570 | +523.2% | -32.7% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $3 116 | +$2 616 | +523.2% | -32.7% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $6 232 | +$5 232 | +523.2% | -32.7% | 203 | 52% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $12 465 | +$10 465 | +523.2% | -32.7% | 203 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $1 581 | +$1 281 | +426.9% | -32.7% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $2 634 | +$2 134 | +426.9% | -32.7% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $5 269 | +$4 269 | +426.9% | -32.7% | 177 | 49% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $10 537 | +$8 537 | +426.9% | -32.7% | 177 | 49% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $687 | +$387 | +128.9% | -51.4% | 148 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $1 144 | +$644 | +128.9% | -51.4% | 148 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $2 288 | +$1 288 | +128.8% | -51.4% | 148 | 48% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $4 577 | +$2 577 | +128.8% | -51.4% | 148 | 48% | S9 |
| 3 mois | 2026-02-08 | $300 | $1 126 | +$826 | +275.5% | -28.6% | 131 | 50% | S8 |
| 3 mois | 2026-02-08 | $500 | $1 877 | +$1 377 | +275.5% | -28.6% | 131 | 50% | S8 |
| 3 mois | 2026-02-08 | $1 000 | $3 755 | +$2 755 | +275.5% | -28.6% | 131 | 50% | S8 |
| 3 mois | 2026-02-08 | $2 000 | $7 509 | +$5 509 | +275.5% | -28.6% | 131 | 50% | S8 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $313 | +$13 | +4.3% | -27.7% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $521 | +$21 | +4.3% | -27.7% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 043 | +$43 | +4.3% | -27.7% | 100 | 43% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $2 085 | +$85 | +4.3% | -27.7% | 100 | 43% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $342 | +$42 | +14.1% | -24.1% | 72 | 43% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $570 | +$70 | +14.0% | -24.1% | 72 | 43% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 140 | +$140 | +14.0% | -24.1% | 72 | 43% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $2 281 | +$281 | +14.0% | -24.1% | 72 | 43% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $308 | +$8 | +2.6% | -24.0% | 70 | 41% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $513 | +$13 | +2.6% | -24.0% | 70 | 41% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 026 | +$26 | +2.6% | -24.0% | 70 | 41% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $2 052 | +$52 | +2.6% | -24.0% | 70 | 41% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $350 | +$50 | +16.5% | -18.0% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $583 | +$83 | +16.5% | -18.0% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 165 | +$165 | +16.5% | -18.0% | 62 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 330 | +$330 | +16.5% | -18.0% | 62 | 45% | S1 |
| 1 mois | 2026-04-08 | $300 | $390 | +$90 | +29.9% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $500 | $649 | +$149 | +29.9% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $1 000 | $1 299 | +$299 | +29.9% | -18.0% | 50 | 50% | S5 |
| 1 mois | 2026-04-08 | $2 000 | $2 597 | +$597 | +29.9% | -18.0% | 50 | 50% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $376 | +$76 | +25.5% | -3.6% | 19 | 58% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $627 | +$127 | +25.5% | -3.6% | 19 | 58% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 255 | +$255 | +25.5% | -3.6% | 19 | 58% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $2 509 | +$509 | +25.5% | -3.6% | 19 | 58% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $328 | +$28 | +9.3% | -5.4% | 16 | 50% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $547 | +$47 | +9.3% | -5.4% | 16 | 50% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 093 | +$93 | +9.3% | -5.4% | 16 | 50% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 186 | +$186 | +9.3% | -5.4% | 16 | 50% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 74 | 55% | +$637 860 |
| S10 | 350 | 57% | +$584 167 |
| S5 | 452 | 48% | +$674 599 |
| S8 | 108 | 61% | +$1 797 087 |
| S9 | 118 | 52% | +$2 171 351 |

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
