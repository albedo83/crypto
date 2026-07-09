# Rolling backtests

**Générée le** : 2026-07-09 06:48 UTC
**Bot version** : v1.13.3
**Données jusqu'à** : 2026-07-09
**Capitaux testés** : $1 000
**Cap notionnel** : PROPORTIONNEL `0.3 × equity` (v1.13.0, 2026-07-07) — remplace le $500 fixe. Débloque le compounding (chiffres ~9× plus élevés qu'à l'ancien cap), concentration constante, 0 cascade de marge.
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
| 28 mois | 2024-03-09 | $129 816 | +$128 816 | +12881.6% | -39.3% | 1362 | 60% | S5 |
| depuis 2024-08-01 | 2024-08-01 | $55 866 | +$54 866 | +5486.6% | -39.3% | 1131 | 59% | S5 |
| depuis 2024-09-01 | 2024-09-01 | $58 987 | +$57 987 | +5798.7% | -29.0% | 1079 | 60% | S5 |
| depuis 2024-10-01 | 2024-10-01 | $63 362 | +$62 362 | +6236.2% | -29.0% | 1032 | 60% | S5 |
| depuis 2024-11-01 | 2024-11-01 | $67 315 | +$66 315 | +6631.5% | -29.0% | 995 | 60% | S5 |
| depuis 2024-12-01 | 2024-12-01 | $35 778 | +$34 778 | +3477.8% | -29.0% | 945 | 61% | S5 |
| depuis 2025-01-01 | 2025-01-01 | $29 174 | +$28 174 | +2817.4% | -29.0% | 890 | 61% | S5 |
| depuis 2025-02-01 | 2025-02-01 | $19 464 | +$18 464 | +1846.4% | -29.0% | 846 | 60% | S5 |
| depuis 2025-03-01 | 2025-03-01 | $14 957 | +$13 957 | +1395.7% | -29.0% | 791 | 60% | S5 |
| depuis 2025-04-01 | 2025-04-01 | $17 203 | +$16 203 | +1620.3% | -29.0% | 742 | 61% | S5 |
| depuis 2025-05-01 | 2025-05-01 | $11 265 | +$10 265 | +1026.5% | -29.0% | 696 | 61% | S5 |
| depuis 2025-06-01 | 2025-06-01 | $9 772 | +$8 772 | +877.2% | -29.0% | 650 | 61% | S5 |
| depuis 2025-07-01 | 2025-07-01 | $7 638 | +$6 638 | +663.8% | -29.0% | 607 | 61% | S5 |
| 12 mois | 2025-07-09 | $8 141 | +$7 141 | +714.1% | -29.0% | 600 | 61% | S5 |
| depuis 2025-08-01 | 2025-08-01 | $6 593 | +$5 593 | +559.3% | -29.0% | 564 | 60% | S5 |
| depuis 2025-09-01 | 2025-09-01 | $6 540 | +$5 540 | +554.0% | -26.1% | 523 | 60% | S5 |
| depuis 2025-10-01 | 2025-10-01 | $7 554 | +$6 554 | +655.4% | -23.4% | 469 | 60% | S5 |
| depuis 2025-11-01 | 2025-11-01 | $4 995 | +$3 995 | +399.5% | -23.4% | 423 | 60% | S5 |
| depuis 2025-12-01 | 2025-12-01 | $2 968 | +$1 968 | +196.8% | -23.4% | 375 | 58% | S5 |
| depuis 2026-01-01 | 2026-01-01 | $2 609 | +$1 609 | +160.9% | -23.4% | 346 | 57% | S5 |
| 6 mois | 2026-01-09 | $2 539 | +$1 539 | +153.9% | -23.4% | 331 | 57% | S5 |
| depuis 2026-02-01 | 2026-02-01 | $2 450 | +$1 450 | +145.0% | -22.9% | 305 | 58% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 670 | +$670 | +67.0% | -22.9% | 243 | 57% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 625 | +$625 | +62.5% | -22.9% | 204 | 57% | S1 |
| 3 mois | 2026-04-09 | $1 714 | +$714 | +71.4% | -22.9% | 190 | 57% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 373 | +$373 | +37.3% | -22.9% | 151 | 53% | S1 |
| depuis 2026-06-01 | 2026-06-01 | $1 113 | +$113 | +11.3% | -22.0% | 92 | 53% | S5 |
| 1 mois | 2026-06-09 | $1 337 | +$337 | +33.7% | -6.2% | 64 | 61% | S5 |
| depuis 2026-06-10 | 2026-06-10 | $1 264 | +$264 | +26.4% | -6.2% | 61 | 59% | S8 |
| depuis 2026-06-11 | 2026-06-11 | $1 223 | +$223 | +22.3% | -6.2% | 59 | 61% | S5 |
| depuis 2026-07-01 | 2026-07-01 | $922 | $-78 | -7.8% | -7.7% | 23 | 52% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 87 | 56% | +$33 721 |
| S10 | 355 | 55% | +$20 672 |
| S5 | 602 | 67% | +$57 336 |
| S8 | 172 | 47% | +$11 721 |
| S9 | 146 | 54% | +$5 366 |

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
