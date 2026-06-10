# Rolling backtests

**Générée le** : 2026-06-10 18:31 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-06-10
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
| 28 mois | 2024-02-10 | $7 504 | +$7 004 | +1400.7% | -61.9% | 1181 | 53% | S1 |
| depuis 2024-07-01 | 2024-07-01 | $7 421 | +$6 921 | +1384.1% | -53.6% | 966 | 53% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $7 084 | +$6 584 | +1316.7% | -51.3% | 920 | 53% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $7 304 | +$6 804 | +1360.8% | -35.5% | 872 | 54% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $7 444 | +$6 944 | +1388.8% | -22.3% | 832 | 54% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $7 486 | +$6 986 | +1397.1% | -21.9% | 802 | 54% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $6 399 | +$5 899 | +1179.9% | -28.3% | 752 | 54% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $5 464 | +$4 964 | +992.9% | -24.5% | 712 | 53% | S8 |
| depuis 2025-02-01 | 2025-02-01 | $5 238 | +$4 738 | +947.6% | -26.7% | 676 | 53% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $4 738 | +$4 238 | +847.6% | -33.2% | 626 | 53% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $4 456 | +$3 956 | +791.3% | -37.9% | 584 | 53% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $4 320 | +$3 820 | +763.9% | -40.5% | 545 | 53% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $3 938 | +$3 438 | +687.6% | -46.1% | 504 | 53% | S5 |
| 12 mois | 2025-06-10 | $3 966 | +$3 466 | +693.1% | -45.8% | 494 | 53% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $3 237 | +$2 737 | +547.5% | -51.9% | 469 | 52% | S9 |
| depuis 2025-08-01 | 2025-08-01 | $3 320 | +$2 820 | +564.1% | -27.3% | 435 | 52% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $3 485 | +$2 985 | +597.0% | -20.7% | 400 | 53% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $3 139 | +$2 639 | +527.9% | -21.9% | 356 | 53% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $2 416 | +$1 916 | +383.1% | -33.8% | 312 | 52% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 548 | +$1 048 | +209.7% | -55.1% | 269 | 50% | S1 |
| 6 mois | 2025-12-10 | $1 474 | +$974 | +194.7% | -55.7% | 264 | 50% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 364 | +$864 | +172.8% | -56.4% | 244 | 48% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 330 | +$830 | +166.0% | -49.7% | 210 | 50% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $728 | +$228 | +45.6% | -51.5% | 157 | 47% | S1 |
| 3 mois | 2026-03-10 | $670 | +$170 | +33.9% | -53.6% | 151 | 47% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $848 | +$348 | +69.7% | -47.3% | 129 | 47% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $856 | +$356 | +71.2% | -47.0% | 121 | 48% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $792 | +$292 | +58.4% | -49.4% | 76 | 47% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $655 | +$155 | +30.9% | -54.0% | 73 | 44% | S1 |
| 1 mois | 2026-05-10 | $301 | $-199 | -39.8% | -62.1% | 53 | 36% | S10 |
| depuis 2026-05-31 | 2026-05-31 | $587 | +$87 | +17.4% | -32.0% | 27 | 44% | S5 |
| depuis 2026-06-01 | 2026-06-01 | $555 | +$55 | +10.9% | -32.0% | 25 | 36% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $500)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 112 | 53% | +$1 986 |
| S10 | 344 | 57% | +$816 |
| S5 | 441 | 46% | +$937 |
| S8 | 143 | 52% | +$1 408 |
| S9 | 141 | 66% | +$1 856 |

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
