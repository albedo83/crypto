# Rolling backtests

**Générée le** : 2026-06-20 08:10 UTC
**Bot version** : v12.17.3
**Données jusqu'à** : 2026-06-19
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

**Trade blacklist** (v11.4.10) — `TRADE_BLACKLIST = {IMX, LINK, SUI}`
- Tokens net-négatifs sur les 4 fenêtres walk-forward : SUI (−$5 311 28m, −$1 045 12m, −$336 6m, −$98 3m), IMX (−$2 952 / −$566 / −$156 / −$53), LINK (−$2 415 / −$387 / −$185 / −$75).
- Validé sur `backtest_rolling` : +91% sur 28m (+$49 687), +63% 12m, +34% 6m, +18% 3m.
- DD 28m dégradée de ~10pp (swings absolus plus grands sur un capital plus haut), DD améliorée ou inchangée sur toutes les fenêtres récentes.
- Source : `backtests/backtest_worst_losers.py`, `backtests/backtest_loser_filters.py`.
- Kill-switch (réactiver un token) : supprimer de `trade_blacklist` dans `alfred/settings.py`.

## Résumé par fenêtre

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-02-19 | $10 317 | +$9 317 | +931.7% | -29.6% | 1273 | 59% | S5 |
| depuis 2024-07-01 | 2024-07-01 | $7 854 | +$6 854 | +685.4% | -52.5% | 1058 | 59% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $8 228 | +$7 228 | +722.8% | -59.4% | 1000 | 59% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $8 560 | +$7 560 | +756.0% | -31.9% | 962 | 60% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $9 114 | +$8 114 | +811.4% | -20.6% | 922 | 60% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $9 096 | +$8 096 | +809.6% | -20.4% | 890 | 60% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $8 274 | +$7 274 | +727.4% | -26.6% | 831 | 61% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $7 471 | +$6 471 | +647.1% | -16.0% | 791 | 61% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $6 962 | +$5 962 | +596.2% | -18.3% | 751 | 61% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $6 514 | +$5 514 | +551.4% | -21.0% | 695 | 61% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $6 487 | +$5 487 | +548.7% | -21.2% | 652 | 62% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $5 956 | +$4 956 | +495.6% | -25.7% | 612 | 62% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $5 583 | +$4 583 | +458.3% | -30.2% | 567 | 62% | S5 |
| 12 mois | 2025-06-19 | $5 132 | +$4 132 | +413.2% | -37.0% | 548 | 62% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $4 909 | +$3 909 | +390.9% | -40.4% | 529 | 61% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $4 582 | +$3 582 | +358.2% | -39.7% | 486 | 60% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $4 625 | +$3 625 | +362.5% | -31.5% | 452 | 60% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $4 613 | +$3 613 | +361.3% | -14.6% | 405 | 61% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $3 888 | +$2 888 | +288.8% | -19.2% | 364 | 60% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 887 | +$1 887 | +188.7% | -33.5% | 313 | 58% | S5 |
| 6 mois | 2025-12-19 | $2 752 | +$1 752 | +175.2% | -34.8% | 296 | 58% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $2 566 | +$1 566 | +156.6% | -37.5% | 286 | 57% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $2 558 | +$1 558 | +155.8% | -30.5% | 247 | 58% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 936 | +$936 | +93.6% | -17.6% | 192 | 56% | S1 |
| 3 mois | 2026-03-19 | $1 935 | +$935 | +93.5% | -17.6% | 173 | 57% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 825 | +$825 | +82.5% | -18.7% | 153 | 54% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 802 | +$802 | +80.2% | -18.9% | 102 | 53% | S1 |
| 1 mois | 2026-05-19 | $1 379 | +$379 | +37.9% | -23.4% | 70 | 56% | S5 |
| depuis 2026-06-01 | 2026-06-01 | $987 | $-13 | -1.3% | -36.3% | 36 | 50% | S5 |
| depuis 2026-06-10 | 2026-06-10 | $1 203 | +$203 | +20.3% | -4.9% | 16 | 62% | S8 |
| depuis 2026-06-11 | 2026-06-11 | $1 147 | +$147 | +14.7% | -5.1% | 17 | 65% | S9 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 110 | 57% | +$2 486 |
| S10 | 347 | 56% | +$661 |
| S5 | 513 | 66% | +$3 111 |
| S8 | 155 | 46% | +$1 368 |
| S9 | 148 | 58% | +$1 691 |

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
