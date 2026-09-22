# Rolling backtests

**Générée le** : 2026-09-22 16:14 UTC
**Bot version** : v1.29.0
**Données jusqu'à** : 2026-09-22
**Empreinte** : config `489b7d68da94` · git `1e6cddb+dirty` · fichiers de données du 2026-09-22T16:10Z · `signal_mult` = {'S1': 1.0, 'S10': 2.0, 'S5': 3.0, 'S8': 1.25, 'S9': 2.0}
**Capitaux testés** : $1 000
**Cap notionnel** : PROPORTIONNEL `0.3 × equity` (v1.13.0, 2026-07-07) — remplace le $500 fixe. Débloque le compounding (chiffres ~9× plus élevés qu'à l'ancien cap), concentration constante, 0 cascade de marge.
**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via `alfred/rules.py`, identique au bot live. Anciens chiffres : `docs/backtests_legacy_pre_phase6.md`.
**Booking des trails** : RÉALISTE (v1.15.5, 2026-07-25) — les sorties par trail (`prop_trail`, `s10_trailing`, `s8_inlife`, `opp_floor`) sont bookées au MARK de la clôture, pas à leur niveau théorique : elles ne sont évaluées qu'aux clôtures 4h et le live sort au marché à ce moment-là. L'ancien booking surévaluait le P&L d'environ 50 % sur chaque fenêtre OOS. Anciens chiffres : `docs/backtests_synthetic_trail_pre_v1_15_5.md` · analyse : `backtests/trail_booking_bias_results.md`.

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v1.29.0)

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
| 28 mois | 2024-05-22 | $13 293 | +$12 293 | +1229.3% | -39.3% | 1322 | 50% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $11 895 | +$10 895 | +1089.5% | -30.5% | 1125 | 50% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $14 693 | +$13 693 | +1369.3% | -30.5% | 1089 | 50% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $8 739 | +$7 739 | +773.9% | -30.5% | 1027 | 50% | S1 |
| depuis 2025-01-01 | 2025-01-01 | $6 981 | +$5 981 | +598.1% | -30.5% | 974 | 50% | S1 |
| depuis 2025-02-01 | 2025-02-01 | $6 169 | +$5 169 | +516.9% | -30.5% | 938 | 50% | S1 |
| depuis 2025-03-01 | 2025-03-01 | $6 107 | +$5 107 | +510.7% | -30.5% | 884 | 50% | S1 |
| depuis 2025-04-01 | 2025-04-01 | $6 739 | +$5 739 | +573.9% | -30.5% | 836 | 51% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $4 942 | +$3 942 | +394.2% | -30.5% | 794 | 50% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $4 680 | +$3 680 | +368.0% | -30.5% | 742 | 49% | S1 |
| depuis 2025-07-01 | 2025-07-01 | $4 069 | +$3 069 | +306.9% | -30.5% | 705 | 49% | S1 |
| depuis 2025-08-01 | 2025-08-01 | $4 399 | +$3 399 | +339.9% | -26.3% | 667 | 49% | S1 |
| depuis 2025-09-01 | 2025-09-01 | $4 832 | +$3 832 | +383.2% | -26.3% | 627 | 49% | S1 |
| 12 mois | 2025-09-22 | $4 050 | +$3 050 | +305.0% | -26.3% | 588 | 49% | S1 |
| depuis 2025-10-01 | 2025-10-01 | $4 619 | +$3 619 | +361.9% | -26.3% | 577 | 49% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $3 824 | +$2 824 | +282.4% | -26.3% | 534 | 49% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $2 390 | +$1 390 | +139.0% | -26.3% | 486 | 48% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $2 116 | +$1 116 | +111.6% | -26.3% | 451 | 46% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 944 | +$944 | +94.4% | -26.3% | 411 | 47% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 365 | +$365 | +36.5% | -25.3% | 352 | 46% | S1 |
| 6 mois | 2026-03-22 | $1 291 | +$291 | +29.1% | -26.3% | 326 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 548 | +$548 | +54.8% | -18.8% | 311 | 47% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 604 | +$604 | +60.4% | -18.8% | 264 | 48% | S1 |
| depuis 2026-06-01 | 2026-06-01 | $1 193 | +$193 | +19.3% | -26.2% | 212 | 45% | S1 |
| depuis 2026-06-11 | 2026-06-11 | $1 420 | +$420 | +42.0% | -15.5% | 184 | 48% | S1 |
| 3 mois | 2026-06-22 | $1 440 | +$440 | +44.0% | -15.5% | 162 | 48% | S1 |
| depuis 2026-07-01 | 2026-07-01 | $1 164 | +$164 | +16.4% | -22.4% | 147 | 43% | S1 |
| depuis 2026-07-09 | 2026-07-09 | $1 283 | +$283 | +28.3% | -15.5% | 128 | 43% | S1 |
| depuis 2026-08-01 | 2026-08-01 | $1 343 | +$343 | +34.3% | -15.5% | 95 | 41% | S1 |
| 1 mois | 2026-08-22 | $1 143 | +$143 | +14.3% | -10.9% | 57 | 40% | S1 |
| depuis 2026-09-01 | 2026-09-01 | $1 310 | +$310 | +31.0% | -16.7% | 41 | 44% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 124 | 58% | +$5 466 |
| S10 | 325 | 55% | +$2 489 |
| S5 | 600 | 47% | +$892 |
| S8 | 147 | 44% | +$2 563 |
| S9 | 126 | 48% | +$883 |

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
