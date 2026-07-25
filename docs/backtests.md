# Rolling backtests

**Générée le** : 2026-07-25 15:56 UTC
**Bot version** : v1.15.5
**Données jusqu'à** : 2026-07-25
**Capitaux testés** : $1 000
**Cap notionnel** : PROPORTIONNEL `0.3 × equity` (v1.13.0, 2026-07-07) — remplace le $500 fixe. Débloque le compounding (chiffres ~9× plus élevés qu'à l'ancien cap), concentration constante, 0 cascade de marge.
**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via `alfred/rules.py`, identique au bot live. Anciens chiffres : `docs/backtests_legacy_pre_phase6.md`.
**Booking des trails** : RÉALISTE (v1.15.5, 2026-07-25) — les sorties par trail (`prop_trail`, `s10_trailing`, `s8_inlife`, `opp_floor`) sont bookées au MARK de la clôture, pas à leur niveau théorique : elles ne sont évaluées qu'aux clôtures 4h et le live sort au marché à ce moment-là. L'ancien booking surévaluait le P&L d'environ 50 % sur chaque fenêtre OOS. Anciens chiffres : `docs/backtests_synthetic_trail_pre_v1_15_5.md` · analyse : `backtests/trail_booking_bias_results.md`.

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v1.15.5)

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
| 28 mois | 2024-03-25 | $27 045 | +$26 045 | +2604.5% | -35.8% | 1252 | 51% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $14 179 | +$13 179 | +1317.9% | -35.8% | 1065 | 50% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $16 581 | +$15 581 | +1558.1% | -30.6% | 1014 | 51% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $18 169 | +$17 169 | +1716.9% | -30.6% | 973 | 51% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $20 022 | +$19 022 | +1902.2% | -30.6% | 939 | 51% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $12 223 | +$11 223 | +1122.3% | -30.6% | 884 | 51% | S1 |
| depuis 2025-01-01 | 2025-01-01 | $9 775 | +$8 775 | +877.5% | -30.6% | 835 | 51% | S1 |
| depuis 2025-02-01 | 2025-02-01 | $7 690 | +$6 690 | +669.0% | -30.6% | 798 | 50% | S1 |
| depuis 2025-03-01 | 2025-03-01 | $7 015 | +$6 015 | +601.5% | -30.6% | 746 | 51% | S1 |
| depuis 2025-04-01 | 2025-04-01 | $7 026 | +$6 026 | +602.6% | -30.6% | 704 | 51% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $5 469 | +$4 469 | +446.9% | -30.6% | 660 | 50% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $4 967 | +$3 967 | +396.7% | -30.6% | 613 | 50% | S1 |
| depuis 2025-07-01 | 2025-07-01 | $4 226 | +$3 226 | +322.6% | -30.6% | 573 | 50% | S1 |
| 12 mois | 2025-07-25 | $4 446 | +$3 446 | +344.6% | -29.2% | 544 | 50% | S1 |
| depuis 2025-08-01 | 2025-08-01 | $4 507 | +$3 507 | +350.7% | -29.2% | 538 | 50% | S1 |
| depuis 2025-09-01 | 2025-09-01 | $4 789 | +$3 789 | +378.9% | -29.2% | 500 | 50% | S1 |
| depuis 2025-10-01 | 2025-10-01 | $4 127 | +$3 127 | +312.7% | -29.2% | 452 | 50% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $3 052 | +$2 052 | +205.2% | -29.2% | 411 | 49% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 954 | +$954 | +95.4% | -29.2% | 363 | 47% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 874 | +$874 | +87.4% | -29.2% | 332 | 46% | S1 |
| 6 mois | 2026-01-25 | $1 718 | +$718 | +71.8% | -29.2% | 306 | 46% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 631 | +$631 | +63.1% | -29.2% | 297 | 48% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 122 | +$122 | +12.2% | -29.2% | 240 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 230 | +$230 | +23.0% | -29.2% | 202 | 47% | S1 |
| 3 mois | 2026-04-25 | $1 192 | +$192 | +19.2% | -29.2% | 165 | 48% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 085 | +$85 | +8.5% | -29.2% | 156 | 47% | S1 |
| depuis 2026-06-01 | 2026-06-01 | $801 | $-199 | -19.9% | -27.9% | 104 | 44% | S8 |
| depuis 2026-06-11 | 2026-06-11 | $989 | $-11 | -1.1% | -14.4% | 76 | 51% | S8 |
| 1 mois | 2026-06-25 | $1 052 | +$52 | +5.2% | -11.9% | 47 | 51% | S8 |
| depuis 2026-07-01 | 2026-07-01 | $932 | $-68 | -6.8% | -10.3% | 39 | 49% | S10 |
| depuis 2026-07-09 | 2026-07-09 | $970 | $-30 | -3.0% | -6.6% | 21 | 48% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 69 | 61% | +$10 274 |
| S10 | 355 | 56% | +$5 950 |
| S5 | 535 | 47% | $-3 811 |
| S8 | 160 | 47% | +$6 599 |
| S9 | 133 | 54% | +$7 033 |

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
