# Rolling backtests

**Générée le** : 2026-06-11 06:55 UTC
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
| 28 mois | 2024-02-11 | $7 687 | +$7 187 | +1437.4% | -61.9% | 1180 | 53% | S1 |
| depuis 2024-07-01 | 2024-07-01 | $7 634 | +$7 134 | +1426.8% | -54.1% | 970 | 54% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $7 417 | +$6 917 | +1383.3% | -51.2% | 921 | 53% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $7 496 | +$6 996 | +1399.2% | -35.5% | 872 | 54% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $7 637 | +$7 137 | +1427.3% | -22.3% | 832 | 54% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $7 689 | +$7 189 | +1437.8% | -21.8% | 802 | 54% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $6 107 | +$5 607 | +1121.4% | -41.2% | 754 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $5 657 | +$5 157 | +1031.4% | -24.5% | 712 | 53% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $5 430 | +$4 930 | +986.1% | -26.7% | 676 | 53% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $4 930 | +$4 430 | +886.0% | -33.2% | 626 | 53% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $4 792 | +$4 292 | +858.4% | -35.5% | 584 | 53% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 491 | +$3 991 | +798.3% | -40.8% | 546 | 53% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $4 121 | +$3 621 | +724.1% | -46.2% | 505 | 53% | S5 |
| 12 mois | 2025-06-11 | $3 914 | +$3 414 | +682.7% | -48.4% | 492 | 53% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 385 | +$2 885 | +576.9% | -51.7% | 470 | 52% | S9 |
| depuis 2025-08-01 | 2025-08-01 | $3 513 | +$3 013 | +602.6% | -27.3% | 435 | 53% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $3 677 | +$3 177 | +635.5% | -20.7% | 400 | 53% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 318 | +$2 818 | +563.7% | -22.0% | 356 | 53% | S8 |
| depuis 2025-11-01 | 2025-11-01 | $2 608 | +$2 108 | +421.6% | -33.8% | 312 | 53% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 764 | +$1 264 | +252.8% | -54.5% | 271 | 51% | S1 |
| 6 mois | 2025-12-11 | $1 595 | +$1 095 | +219.0% | -56.1% | 264 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 556 | +$1 056 | +211.3% | -56.4% | 244 | 49% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 639 | +$1 139 | +227.7% | -54.2% | 211 | 51% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $907 | +$407 | +81.3% | -43.8% | 157 | 48% | S1 |
| 3 mois | 2026-03-11 | $854 | +$354 | +70.7% | -45.3% | 150 | 49% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 032 | +$532 | +106.5% | -40.1% | 129 | 49% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $922 | +$422 | +84.4% | -43.4% | 121 | 49% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $972 | +$472 | +94.5% | -41.9% | 76 | 50% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $939 | +$439 | +87.9% | -42.9% | 74 | 49% | S1 |
| 1 mois | 2026-05-11 | $437 | $-63 | -12.6% | -54.1% | 52 | 42% | S10 |
| depuis 2026-05-31 | 2026-05-31 | $623 | +$123 | +24.5% | -28.9% | 26 | 54% | S9 |
| depuis 2026-06-01 | 2026-06-01 | $393 | $-107 | -21.4% | -34.1% | 24 | 42% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 112 | 53% | +$1 987 |
| S10 | 343 | 57% | +$803 |
| S5 | 443 | 46% | +$1 050 |
| S8 | 141 | 53% | +$1 489 |
| S9 | 141 | 66% | +$1 858 |

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
