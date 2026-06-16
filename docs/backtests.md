# Rolling backtests

**Générée le** : 2026-06-16 08:24 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-06-15
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
| 28 mois | 2024-02-15 | $8 916 | +$8 416 | +1683.2% | -41.9% | 1205 | 53% | S1 |
| depuis 2024-07-01 | 2024-07-01 | $7 913 | +$7 413 | +1482.5% | -55.0% | 968 | 53% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $7 672 | +$7 172 | +1434.4% | -53.5% | 920 | 53% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $7 944 | +$7 444 | +1488.8% | -34.7% | 879 | 54% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $8 035 | +$7 535 | +1507.0% | -17.9% | 851 | 54% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $8 096 | +$7 596 | +1519.2% | -17.3% | 821 | 54% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $6 464 | +$5 964 | +1192.9% | -27.0% | 768 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $6 178 | +$5 678 | +1135.6% | -25.0% | 724 | 54% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $5 433 | +$4 933 | +986.6% | -32.3% | 682 | 54% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $4 730 | +$4 230 | +846.0% | -40.7% | 624 | 54% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $4 803 | +$4 303 | +860.6% | -39.9% | 588 | 55% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 202 | +$3 702 | +740.3% | -43.2% | 546 | 55% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $4 280 | +$3 780 | +756.0% | -43.0% | 510 | 54% | S5 |
| 12 mois | 2025-06-15 | $4 185 | +$3 685 | +737.1% | -43.3% | 491 | 55% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 739 | +$3 239 | +647.8% | -49.2% | 470 | 54% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $3 713 | +$3 213 | +642.7% | -37.3% | 439 | 54% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $4 105 | +$3 605 | +721.0% | -15.3% | 409 | 55% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 314 | +$2 814 | +562.7% | -21.3% | 360 | 54% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $3 152 | +$2 652 | +530.4% | -23.3% | 317 | 54% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 216 | +$1 716 | +343.1% | -26.0% | 262 | 54% | S1 |
| 6 mois | 2025-12-15 | $2 192 | +$1 692 | +338.3% | -29.4% | 251 | 53% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $2 051 | +$1 551 | +310.2% | -31.5% | 233 | 52% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $996 | +$496 | +99.2% | -29.1% | 192 | 49% | S8 |
| depuis 2026-03-01 | 2026-03-01 | $660 | +$160 | +32.0% | -26.6% | 148 | 47% | S5 |
| 3 mois | 2026-03-15 | $665 | +$165 | +32.9% | -26.6% | 133 | 47% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $677 | +$177 | +35.5% | -33.3% | 121 | 46% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $651 | +$151 | +30.2% | -26.7% | 112 | 46% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $630 | +$130 | +26.0% | -33.9% | 72 | 49% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 062 | +$562 | +112.3% | -26.3% | 72 | 51% | S1 |
| 1 mois | 2026-05-15 | $616 | +$116 | +23.2% | -27.0% | 45 | 47% | S5 |
| depuis 2026-05-31 | 2026-05-31 | $455 | $-45 | -9.0% | -33.4% | 27 | 48% | S8 |
| depuis 2026-06-01 | 2026-06-01 | $417 | $-83 | -16.6% | -37.1% | 25 | 40% | S8 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 116 | 52% | +$2 311 |
| S10 | 345 | 57% | +$845 |
| S5 | 461 | 47% | +$1 482 |
| S8 | 141 | 54% | +$1 840 |
| S9 | 142 | 68% | +$1 937 |

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
