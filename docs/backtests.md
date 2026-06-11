# Rolling backtests

**Générée le** : 2026-06-11 15:03 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-06-11
**Capitaux testés** : $500
**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via `alfred/rules.py`, identique au bot live. Anciens chiffres : `docs/backtests_legacy_pre_phase6.md`.

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.17.3)

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
- Kill-switch (réactiver un token) : supprimer de `trade_blacklist` dans `alfred/settings.py`.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-02-11 | $8 197 | +$7 697 | +1539.4% | -53.8% | 1217 | 53% | S1 |
| depuis 2024-07-01 | 2024-07-01 | $7 605 | +$7 105 | +1421.1% | -56.2% | 998 | 54% | S9 |
| depuis 2024-08-01 | 2024-08-01 | $7 351 | +$6 851 | +1370.2% | -53.2% | 949 | 53% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $7 513 | +$7 013 | +1402.6% | -37.2% | 898 | 54% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $7 720 | +$7 220 | +1443.9% | -25.3% | 857 | 54% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $7 764 | +$7 264 | +1452.8% | -24.6% | 827 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $6 496 | +$5 996 | +1199.1% | -43.6% | 773 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $6 156 | +$5 656 | +1131.3% | -26.7% | 727 | 54% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $6 047 | +$5 547 | +1109.4% | -23.9% | 691 | 54% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $5 362 | +$4 862 | +972.4% | -31.9% | 638 | 55% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $5 285 | +$4 785 | +957.1% | -32.9% | 596 | 55% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 946 | +$4 446 | +889.2% | -38.6% | 557 | 55% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $4 594 | +$4 094 | +818.8% | -44.5% | 513 | 55% | S5 |
| 12 mois | 2025-06-11 | $4 447 | +$3 947 | +789.4% | -46.5% | 500 | 55% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 834 | +$3 334 | +666.8% | -51.9% | 477 | 54% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $3 935 | +$3 435 | +687.0% | -28.8% | 442 | 54% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $4 119 | +$3 619 | +723.8% | -15.3% | 407 | 55% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 729 | +$3 229 | +645.8% | -16.7% | 361 | 55% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $2 759 | +$2 259 | +451.8% | -27.9% | 316 | 54% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $1 835 | +$1 335 | +267.1% | -50.3% | 273 | 51% | S1 |
| 6 mois | 2025-12-11 | $1 671 | +$1 171 | +234.3% | -53.7% | 266 | 50% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 634 | +$1 134 | +226.7% | -54.0% | 246 | 50% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 570 | +$1 070 | +214.0% | -53.4% | 215 | 51% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $810 | +$310 | +62.1% | -33.5% | 160 | 49% | S1 |
| 3 mois | 2026-03-11 | $781 | +$281 | +56.1% | -34.3% | 153 | 49% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $950 | +$450 | +89.9% | -30.2% | 132 | 49% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $811 | +$311 | +62.1% | -33.5% | 124 | 48% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $983 | +$483 | +96.6% | -29.4% | 79 | 53% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $954 | +$454 | +90.7% | -30.1% | 77 | 52% | S1 |
| 1 mois | 2026-05-11 | $356 | $-144 | -28.8% | -44.5% | 55 | 47% | S10 |
| depuis 2026-05-31 | 2026-05-31 | $342 | $-158 | -31.5% | -43.2% | 27 | 48% | S10 |
| depuis 2026-06-01 | 2026-06-01 | $308 | $-192 | -38.5% | -47.3% | 27 | 41% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 119 | 51% | +$1 885 |
| S10 | 349 | 57% | +$856 |
| S5 | 460 | 47% | +$1 557 |
| S8 | 142 | 54% | +$1 522 |
| S9 | 147 | 66% | +$1 877 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 34 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector` (parité validée vs `alfred.features`, 800/800 tirages — `backtests/test_feature_parity.py`).
- **Params & règles** : noyau ALFRED partagé bot/backtest — `alfred/settings.py` (`DEFAULT_PARAMS`) + `alfred/rules.py` (exits/sizing) + `alfred/signals.py`. Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 13 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
