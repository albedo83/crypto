# Rolling backtests

**Générée le** : 2026-07-31 06:37 UTC
**Bot version** : v1.17.3
**Données jusqu'à** : 2026-07-31
**Empreinte** : config `d7a415020619` · git `f2f7636+dirty` · fichiers de données du 2026-07-31T04:10Z · `signal_mult` = {'S1': 1.0, 'S10': 2.0, 'S5': 3.0, 'S8': 1.25, 'S9': 2.0}
**Capitaux testés** : $1 000
**Cap notionnel** : PROPORTIONNEL `0.3 × equity` (v1.13.0, 2026-07-07) — remplace le $500 fixe. Débloque le compounding (chiffres ~9× plus élevés qu'à l'ancien cap), concentration constante, 0 cascade de marge.
**Sémantique** : ALIGNED (phase 6, 2026-06-10) — exits/sizing via `alfred/rules.py`, identique au bot live. Anciens chiffres : `docs/backtests_legacy_pre_phase6.md`.
**Booking des trails** : RÉALISTE (v1.15.5, 2026-07-25) — les sorties par trail (`prop_trail`, `s10_trailing`, `s8_inlife`, `opp_floor`) sont bookées au MARK de la clôture, pas à leur niveau théorique : elles ne sont évaluées qu'aux clôtures 4h et le live sort au marché à ce moment-là. L'ancien booking surévaluait le P&L d'environ 50 % sur chaque fenêtre OOS. Anciens chiffres : `docs/backtests_synthetic_trail_pre_v1_15_5.md` · analyse : `backtests/trail_booking_bias_results.md`.

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

**Notional cap** : $20,000 par trade (override via `BACKTEST_MAX_NOTIONAL` env, 0 = désactivé). Modélise la profondeur d'orderbook HL : sans ce cap les ancres longues compoundent au-delà de la taille réellement exécutable.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v1.17.3)

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
| 28 mois | 2024-03-31 | $15 463 | +$14 463 | +1446.3% | -51.4% | 1309 | 50% | S1 |
| depuis 2024-08-01 | 2024-08-01 | $8 579 | +$7 579 | +757.9% | -47.2% | 1120 | 49% | S1 |
| depuis 2024-09-01 | 2024-09-01 | $9 728 | +$8 728 | +872.8% | -35.7% | 1062 | 50% | S1 |
| depuis 2024-10-01 | 2024-10-01 | $11 454 | +$10 454 | +1045.4% | -31.5% | 1020 | 50% | S1 |
| depuis 2024-11-01 | 2024-11-01 | $12 968 | +$11 968 | +1196.8% | -31.5% | 983 | 50% | S1 |
| depuis 2024-12-01 | 2024-12-01 | $6 341 | +$5 341 | +534.1% | -31.5% | 931 | 50% | S8 |
| depuis 2025-01-01 | 2025-01-01 | $5 832 | +$4 832 | +483.2% | -31.5% | 877 | 50% | S8 |
| depuis 2025-02-01 | 2025-02-01 | $5 095 | +$4 095 | +409.5% | -31.5% | 839 | 49% | S1 |
| depuis 2025-03-01 | 2025-03-01 | $5 067 | +$4 067 | +406.7% | -31.5% | 785 | 50% | S1 |
| depuis 2025-04-01 | 2025-04-01 | $5 343 | +$4 343 | +434.3% | -31.5% | 738 | 51% | S1 |
| depuis 2025-05-01 | 2025-05-01 | $3 719 | +$2 719 | +271.9% | -31.5% | 691 | 50% | S1 |
| depuis 2025-06-01 | 2025-06-01 | $3 450 | +$2 450 | +245.0% | -31.5% | 642 | 50% | S8 |
| depuis 2025-07-01 | 2025-07-01 | $2 883 | +$1 883 | +188.3% | -31.5% | 604 | 49% | S1 |
| 12 mois | 2025-07-31 | $3 425 | +$2 425 | +242.5% | -31.3% | 565 | 50% | S1 |
| depuis 2025-08-01 | 2025-08-01 | $3 264 | +$2 264 | +226.4% | -31.3% | 565 | 49% | S1 |
| depuis 2025-09-01 | 2025-09-01 | $3 627 | +$2 627 | +262.7% | -31.3% | 524 | 49% | S1 |
| depuis 2025-10-01 | 2025-10-01 | $3 467 | +$2 467 | +246.7% | -31.3% | 474 | 50% | S1 |
| depuis 2025-11-01 | 2025-11-01 | $2 750 | +$1 750 | +175.0% | -31.3% | 432 | 49% | S1 |
| depuis 2025-12-01 | 2025-12-01 | $1 821 | +$821 | +82.1% | -31.3% | 385 | 48% | S1 |
| depuis 2026-01-01 | 2026-01-01 | $1 615 | +$615 | +61.5% | -31.3% | 350 | 47% | S1 |
| 6 mois | 2026-01-31 | $1 339 | +$339 | +33.9% | -31.3% | 316 | 46% | S1 |
| depuis 2026-02-01 | 2026-02-01 | $1 416 | +$416 | +41.6% | -31.3% | 312 | 47% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $956 | $-44 | -4.4% | -31.3% | 253 | 45% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 024 | +$24 | +2.4% | -29.9% | 214 | 45% | S1 |
| 3 mois | 2026-04-30 | $1 215 | +$215 | +21.5% | -29.9% | 168 | 48% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 231 | +$231 | +23.1% | -29.9% | 167 | 49% | S1 |
| depuis 2026-06-01 | 2026-06-01 | $1 093 | +$93 | +9.3% | -14.7% | 110 | 51% | S10 |
| depuis 2026-06-11 | 2026-06-11 | $1 096 | +$96 | +9.6% | -8.6% | 86 | 53% | S10 |
| 1 mois | 2026-06-30 | $1 135 | +$135 | +13.5% | -6.1% | 52 | 52% | S5 |
| depuis 2026-07-01 | 2026-07-01 | $994 | $-6 | -0.6% | -7.4% | 50 | 52% | S10 |
| depuis 2026-07-09 | 2026-07-09 | $972 | $-28 | -2.8% | -7.0% | 31 | 48% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 63 | 62% | +$6 185 |
| S10 | 330 | 56% | +$3 421 |
| S5 | 637 | 46% | $-3 894 |
| S8 | 153 | 48% | +$5 252 |
| S9 | 126 | 56% | +$3 498 |

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
