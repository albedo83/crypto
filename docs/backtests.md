# Rolling backtests

**Générée le** : 2026-07-04 13:58 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-07-04
**Capitaux testés** : $1 000
**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via `alfred/rules.py`, identique au bot live. Anciens chiffres : `docs/backtests_legacy_pre_phase6.md`.

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

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

**Trade blacklist** (v11.4.10) — `TRADE_BLACKLIST = {}`
- Tokens net-négatifs sur les 4 fenêtres walk-forward : SUI (−$5 311 28m, −$1 045 12m, −$336 6m, −$98 3m), IMX (−$2 952 / −$566 / −$156 / −$53), LINK (−$2 415 / −$387 / −$185 / −$75).
- Validé sur `backtest_rolling` : +91% sur 28m (+$49 687), +63% 12m, +34% 6m, +18% 3m.
- DD 28m dégradée de ~10pp (swings absolus plus grands sur un capital plus haut), DD améliorée ou inchangée sur toutes les fenêtres récentes.
- Source : `backtests/backtest_worst_losers.py`, `backtests/backtest_loser_filters.py`.
- Kill-switch (réactiver un token) : supprimer de `trade_blacklist` dans `alfred/settings.py`.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-03-04 | $10 059 | +$9 059 | +905.9% | -36.9% | 1356 | 59% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $8 009 | +$7 009 | +700.9% | -40.5% | 1115 | 59% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $7 666 | +$6 666 | +666.6% | -41.6% | 1061 | 60% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $8 162 | +$7 162 | +716.2% | -25.8% | 1024 | 60% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $8 225 | +$7 225 | +722.5% | -26.3% | 987 | 60% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $8 054 | +$7 054 | +705.4% | -23.1% | 929 | 60% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $7 174 | +$6 174 | +617.4% | -14.7% | 878 | 61% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $6 504 | +$5 504 | +550.4% | -17.9% | 831 | 60% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $6 096 | +$5 096 | +509.6% | -20.6% | 773 | 60% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $6 145 | +$5 145 | +514.5% | -20.3% | 730 | 61% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $5 407 | +$4 407 | +440.7% | -27.8% | 683 | 61% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $5 167 | +$4 167 | +416.7% | -31.3% | 637 | 61% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $4 896 | +$3 896 | +389.6% | -32.6% | 591 | 60% | S5 |
| 12 mois | 2025-07-04 | $4 927 | +$3 927 | +392.7% | -35.5% | 591 | 61% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $4 523 | +$3 523 | +352.3% | -37.7% | 543 | 59% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $4 586 | +$3 586 | +358.6% | -31.2% | 504 | 59% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $4 712 | +$3 712 | +371.2% | -13.6% | 454 | 60% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $3 816 | +$2 816 | +281.6% | -19.1% | 412 | 59% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 856 | +$1 856 | +185.6% | -30.7% | 362 | 57% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $2 658 | +$1 658 | +165.8% | -32.3% | 329 | 57% | S5 |
| 6 mois | 2026-01-04 | $2 495 | +$1 495 | +149.5% | -32.0% | 319 | 56% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $2 360 | +$1 360 | +136.0% | -34.7% | 286 | 57% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 743 | +$743 | +74.3% | -24.0% | 223 | 55% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 903 | +$903 | +90.3% | -22.0% | 189 | 56% | S1 |
| 3 mois | 2026-04-04 | $1 881 | +$881 | +88.1% | -22.3% | 183 | 56% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 776 | +$776 | +77.6% | -23.6% | 134 | 54% | S1 |
| depuis 2026-06-01 | 2026-06-01 | $1 153 | +$153 | +15.3% | -27.5% | 73 | 49% | S5 |
| 1 mois | 2026-06-04 | $1 435 | +$435 | +43.5% | -7.2% | 63 | 59% | S8 |
| depuis 2026-06-10 | 2026-06-10 | $1 355 | +$355 | +35.5% | -7.5% | 46 | 59% | S8 |
| depuis 2026-06-11 | 2026-06-11 | $1 297 | +$297 | +29.7% | -7.8% | 47 | 60% | S5 |
| depuis 2026-07-01 | 2026-07-01 | $880 | $-120 | -12.0% | -12.6% | 11 | 36% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 96 | 55% | +$2 249 |
| S10 | 352 | 55% | +$609 |
| S5 | 596 | 67% | +$3 505 |
| S8 | 171 | 47% | +$1 616 |
| S9 | 141 | 55% | +$1 081 |

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
