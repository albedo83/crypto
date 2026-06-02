# Rolling backtests

**Générée le** : 2026-06-02 09:50 UTC
**Bot version** : v12.12.1
**Données jusqu'à** : 2026-06-02
**Capitaux testés** : $500

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.12.1)

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

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-02-02 | $366 593 | +$366 093 | +73218.6% | -67.6% | 1184 | 52% | S5 |
| depuis 2024-07-01 | 2024-07-01 | $321 167 | +$320 667 | +64133.4% | -67.6% | 960 | 53% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $296 479 | +$295 979 | +59195.7% | -65.0% | 911 | 52% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $312 847 | +$312 347 | +62469.5% | -51.4% | 862 | 53% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $326 354 | +$325 854 | +65170.8% | -51.4% | 820 | 54% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $330 120 | +$329 620 | +65924.0% | -51.4% | 788 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $273 276 | +$272 776 | +54555.2% | -42.2% | 737 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $231 841 | +$231 341 | +46268.3% | -38.4% | 694 | 54% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $209 235 | +$208 735 | +41746.9% | -38.4% | 656 | 54% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $179 378 | +$178 878 | +35775.6% | -50.6% | 604 | 54% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $162 240 | +$161 740 | +32348.0% | -55.1% | 562 | 55% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $155 883 | +$155 383 | +31076.5% | -56.0% | 525 | 55% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $150 657 | +$150 157 | +30031.4% | -56.7% | 482 | 55% | S1 |
| 12 mois | 2025-06-02 | $150 927 | +$150 427 | +30085.4% | -56.7% | 481 | 55% | S1 |
| depuis 2025-07-01 | 2025-07-01 | $108 117 | +$107 617 | +21523.3% | -55.9% | 446 | 54% | S1 |
| depuis 2025-08-01 | 2025-08-01 | $105 769 | +$105 269 | +21053.9% | -55.9% | 414 | 55% | S1 |
| depuis 2025-09-01 | 2025-09-01 | $103 923 | +$103 423 | +20684.6% | -55.9% | 376 | 55% | S1 |
| depuis 2025-10-01 | 2025-10-01 | $68 735 | +$68 235 | +13647.0% | -55.9% | 332 | 54% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $21 453 | +$20 953 | +4190.6% | -55.9% | 288 | 53% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $6 073 | +$5 573 | +1114.7% | -55.9% | 245 | 50% | S5 |
| 6 mois | 2025-12-02 | $6 168 | +$5 668 | +1133.6% | -55.9% | 244 | 50% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $4 997 | +$4 497 | +899.3% | -55.9% | 219 | 48% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $4 589 | +$4 089 | +817.8% | -53.2% | 188 | 50% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 378 | +$878 | +175.6% | -15.5% | 136 | 47% | S1 |
| 3 mois | 2026-03-02 | $1 311 | +$811 | +162.1% | -15.5% | 136 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 426 | +$926 | +185.2% | -11.8% | 108 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 409 | +$909 | +181.9% | -11.8% | 100 | 49% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 362 | +$862 | +172.4% | -10.4% | 56 | 54% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 324 | +$824 | +164.8% | -10.4% | 52 | 54% | S1 |
| 1 mois | 2026-05-02 | $1 327 | +$827 | +165.3% | -10.4% | 51 | 53% | S1 |
| depuis 2026-05-31 | 2026-05-31 | $546 | +$46 | +9.1% | -2.2% | 7 | 71% | S5 |
| depuis 2026-06-01 | 2026-06-01 | $517 | +$17 | +3.4% | -2.2% | 5 | 60% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 113 | 57% | +$49 246 |
| S10 | 349 | 55% | +$27 306 |
| S5 | 463 | 49% | +$127 248 |
| S8 | 127 | 54% | +$81 630 |
| S9 | 132 | 52% | +$80 664 |

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
