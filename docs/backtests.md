# Rolling backtests

**Générée le** : 2026-06-11 13:10 UTC
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
| 28 mois | 2024-02-11 | $7 825 | +$7 325 | +1465.0% | -48.2% | 1214 | 53% | S9 |
| depuis 2024-07-01 | 2024-07-01 | $7 269 | +$6 769 | +1353.7% | -53.3% | 994 | 54% | S9 |
| depuis 2024-08-01 | 2024-08-01 | $6 991 | +$6 491 | +1298.3% | -49.6% | 945 | 54% | S9 |
| depuis 2024-09-01 | 2024-09-01 | $7 123 | +$6 623 | +1324.6% | -32.8% | 893 | 55% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $7 294 | +$6 794 | +1358.7% | -29.0% | 853 | 55% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $7 340 | +$6 840 | +1368.0% | -28.2% | 823 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $6 275 | +$5 775 | +1155.0% | -43.6% | 769 | 55% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $5 975 | +$5 475 | +1095.1% | -26.9% | 723 | 55% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $5 837 | +$5 337 | +1067.4% | -26.5% | 687 | 55% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $5 373 | +$4 873 | +974.5% | -32.3% | 634 | 55% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $5 239 | +$4 739 | +947.8% | -34.3% | 592 | 55% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 919 | +$4 419 | +883.8% | -40.0% | 553 | 55% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $4 500 | +$4 000 | +800.0% | -46.5% | 510 | 55% | S5 |
| 12 mois | 2025-06-11 | $4 306 | +$3 806 | +761.2% | -48.9% | 497 | 55% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 734 | +$3 234 | +646.8% | -53.6% | 474 | 54% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $3 875 | +$3 375 | +675.0% | -29.3% | 439 | 55% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $4 064 | +$3 564 | +712.7% | -20.7% | 404 | 56% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 719 | +$3 219 | +643.8% | -19.4% | 360 | 56% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $2 995 | +$2 495 | +499.1% | -29.3% | 315 | 55% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 044 | +$1 544 | +308.7% | -52.8% | 272 | 53% | S1 |
| 6 mois | 2025-12-11 | $1 908 | +$1 408 | +281.5% | -54.9% | 265 | 52% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 874 | +$1 374 | +274.9% | -55.2% | 245 | 51% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 839 | +$1 339 | +267.8% | -53.4% | 214 | 53% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 066 | +$566 | +113.2% | -28.2% | 159 | 51% | S1 |
| 3 mois | 2026-03-11 | $1 004 | +$504 | +100.8% | -29.8% | 152 | 51% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $1 195 | +$695 | +139.0% | -25.3% | 131 | 52% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 083 | +$583 | +116.5% | -27.8% | 123 | 52% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 140 | +$640 | +127.9% | -26.5% | 78 | 55% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 103 | +$603 | +120.6% | -27.3% | 76 | 54% | S1 |
| 1 mois | 2026-05-11 | $534 | +$34 | +6.8% | -38.2% | 54 | 50% | S9 |
| depuis 2026-05-31 | 2026-05-31 | $517 | +$17 | +3.5% | -37.2% | 27 | 56% | S9 |
| depuis 2026-06-01 | 2026-06-01 | $399 | $-101 | -20.2% | -44.0% | 26 | 42% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 120 | 51% | +$1 681 |
| S10 | 343 | 57% | +$848 |
| S5 | 458 | 47% | +$1 429 |
| S8 | 144 | 53% | +$1 501 |
| S9 | 149 | 66% | +$1 865 |

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
