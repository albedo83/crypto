# Rolling backtests

**Générée le** : 2026-06-10 17:53 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-06-08
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
| 28 mois | 2024-02-08 | $7 680 | +$7 180 | +1436.0% | -55.3% | 1183 | 53% | S1 |
| depuis 2024-07-01 | 2024-07-01 | $7 407 | +$6 907 | +1381.5% | -53.6% | 964 | 53% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $7 070 | +$6 570 | +1314.1% | -51.3% | 918 | 53% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $7 291 | +$6 791 | +1358.1% | -35.5% | 870 | 54% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $7 431 | +$6 931 | +1386.2% | -22.3% | 830 | 54% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $7 473 | +$6 973 | +1394.5% | -21.9% | 800 | 54% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $6 386 | +$5 886 | +1177.2% | -28.3% | 750 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $5 451 | +$4 951 | +990.2% | -24.5% | 710 | 53% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $5 225 | +$4 725 | +944.9% | -26.7% | 674 | 53% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $4 725 | +$4 225 | +844.9% | -33.2% | 624 | 53% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $4 443 | +$3 943 | +788.6% | -37.9% | 582 | 53% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 307 | +$3 807 | +761.3% | -40.5% | 543 | 53% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $3 925 | +$3 425 | +684.9% | -46.1% | 502 | 53% | S5 |
| 12 mois | 2025-06-08 | $3 945 | +$3 445 | +689.0% | -45.9% | 495 | 53% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 224 | +$2 724 | +544.8% | -51.9% | 467 | 52% | S10 |
| depuis 2025-08-01 | 2025-08-01 | $3 307 | +$2 807 | +561.4% | -27.3% | 433 | 53% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $3 472 | +$2 972 | +594.4% | -20.7% | 398 | 53% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 126 | +$2 626 | +525.2% | -21.9% | 354 | 53% | S10 |
| depuis 2025-11-01 | 2025-11-01 | $2 402 | +$1 902 | +380.5% | -33.8% | 310 | 52% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 539 | +$1 039 | +207.8% | -55.1% | 267 | 50% | S1 |
| 6 mois | 2025-12-08 | $1 467 | +$967 | +193.3% | -55.7% | 265 | 49% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 363 | +$863 | +172.6% | -56.4% | 242 | 48% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 331 | +$831 | +166.1% | -49.7% | 208 | 50% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $733 | +$233 | +46.6% | -50.1% | 155 | 48% | S1 |
| 3 mois | 2026-03-08 | $657 | +$157 | +31.3% | -52.6% | 152 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $856 | +$356 | +71.3% | -46.0% | 127 | 48% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $869 | +$369 | +73.8% | -45.5% | 119 | 49% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $798 | +$298 | +59.7% | -48.0% | 74 | 49% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $713 | +$213 | +42.6% | -50.8% | 71 | 45% | S1 |
| 1 mois | 2026-05-08 | $307 | $-193 | -38.5% | -60.4% | 56 | 39% | S10 |
| depuis 2026-05-31 | 2026-05-31 | $569 | +$69 | +13.8% | -31.9% | 22 | 50% | S5 |
| depuis 2026-06-01 | 2026-06-01 | $552 | +$52 | +10.3% | -32.0% | 20 | 40% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 112 | 57% | +$2 126 |
| S10 | 344 | 57% | +$860 |
| S5 | 445 | 45% | +$948 |
| S8 | 141 | 52% | +$1 445 |
| S9 | 141 | 67% | +$1 802 |

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
