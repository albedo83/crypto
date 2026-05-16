# Rolling backtests

**Générée le** : 2026-05-16 06:59 UTC
**Bot version** : v12.6.2
**Données jusqu'à** : 2026-05-16
**Capitaux testés** : $300 / $500 / $1 000 / $2 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $300 / $500 / $1 000 / $2 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.6.2)

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
- Kill-switch (réactiver un token) : supprimer de `TRADE_BLACKLIST` dans `analysis/bot/config.py`.

## Résumé par fenêtre

| Fenêtre | Start | Capital | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|---|
| 28 mois | 2024-01-16 | $300 | $426 715 | +$426 415 | +142138.2% | -74.3% | 1106 | 52% | S9 |
| 28 mois | 2024-01-16 | $500 | $711 207 | +$710 707 | +142141.4% | -74.3% | 1106 | 52% | S9 |
| 28 mois | 2024-01-16 | $1 000 | $1 422 417 | +$1 421 417 | +142141.7% | -74.3% | 1106 | 52% | S9 |
| 28 mois | 2024-01-16 | $2 000 | $2 844 798 | +$2 842 798 | +142139.9% | -74.3% | 1106 | 52% | S9 |
| 12 mois | 2025-05-16 | $300 | $24 786 | +$24 486 | +8162.0% | -41.4% | 461 | 55% | S9 |
| 12 mois | 2025-05-16 | $500 | $41 310 | +$40 810 | +8162.0% | -41.4% | 461 | 55% | S9 |
| 12 mois | 2025-05-16 | $1 000 | $82 620 | +$81 620 | +8162.0% | -41.4% | 461 | 55% | S9 |
| 12 mois | 2025-05-16 | $2 000 | $165 241 | +$163 241 | +8162.0% | -41.4% | 461 | 55% | S9 |
| 6 mois | 2025-11-16 | $300 | $4 023 | +$3 723 | +1241.0% | -32.9% | 229 | 53% | S9 |
| 6 mois | 2025-11-16 | $500 | $6 705 | +$6 205 | +1241.0% | -32.9% | 229 | 53% | S9 |
| 6 mois | 2025-11-16 | $1 000 | $13 410 | +$12 410 | +1241.0% | -32.9% | 229 | 53% | S9 |
| 6 mois | 2025-11-16 | $2 000 | $26 819 | +$24 819 | +1241.0% | -32.9% | 229 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $300 | $3 129 | +$2 829 | +943.1% | -32.9% | 213 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $5 215 | +$4 715 | +943.1% | -32.9% | 213 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $10 431 | +$9 431 | +943.1% | -32.9% | 213 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 000 | $20 861 | +$18 861 | +943.1% | -32.9% | 213 | 53% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $300 | $2 523 | +$2 223 | +741.0% | -32.9% | 187 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $4 205 | +$3 705 | +741.0% | -32.9% | 187 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $8 410 | +$7 410 | +741.0% | -32.9% | 187 | 50% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 000 | $16 820 | +$14 820 | +741.0% | -32.9% | 187 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $300 | $1 335 | +$1 035 | +344.9% | -44.2% | 157 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $2 224 | +$1 724 | +344.8% | -44.2% | 157 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $4 448 | +$3 448 | +344.8% | -44.2% | 157 | 50% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $2 000 | $8 897 | +$6 897 | +344.8% | -44.2% | 157 | 50% | S9 |
| 3 mois | 2026-02-16 | $300 | $1 187 | +$887 | +295.6% | -16.8% | 131 | 50% | S9 |
| 3 mois | 2026-02-16 | $500 | $1 978 | +$1 478 | +295.6% | -16.8% | 131 | 50% | S9 |
| 3 mois | 2026-02-16 | $1 000 | $3 956 | +$2 956 | +295.6% | -16.8% | 131 | 50% | S9 |
| 3 mois | 2026-02-16 | $2 000 | $7 912 | +$5 912 | +295.6% | -16.8% | 131 | 50% | S9 |
| depuis 2026-03-01 | 2026-03-01 | $300 | $374 | +$74 | +24.6% | -15.5% | 110 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $623 | +$123 | +24.6% | -15.5% | 110 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 246 | +$246 | +24.6% | -15.5% | 110 | 46% | S1 |
| depuis 2026-03-01 | 2026-03-01 | $2 000 | $2 491 | +$491 | +24.6% | -15.5% | 110 | 46% | S1 |
| depuis 2026-03-25 | 2026-03-25 | $300 | $387 | +$87 | +28.9% | -11.8% | 82 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $644 | +$144 | +28.9% | -11.8% | 82 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 289 | +$289 | +28.9% | -11.8% | 82 | 48% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $2 000 | $2 578 | +$578 | +28.9% | -11.8% | 82 | 48% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $300 | $369 | +$69 | +23.0% | -11.8% | 80 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $615 | +$115 | +23.0% | -11.8% | 80 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 230 | +$230 | +23.0% | -11.8% | 80 | 46% | S1 |
| depuis 2026-03-26 | 2026-03-26 | $2 000 | $2 460 | +$460 | +23.0% | -11.8% | 80 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $300 | $352 | +$52 | +17.2% | -13.1% | 74 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $586 | +$86 | +17.2% | -13.1% | 74 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 172 | +$172 | +17.2% | -13.1% | 74 | 46% | S1 |
| depuis 2026-04-01 | 2026-04-01 | $2 000 | $2 344 | +$344 | +17.2% | -13.1% | 74 | 46% | S1 |
| 1 mois | 2026-04-16 | $300 | $289 | $-11 | -3.6% | -25.3% | 51 | 45% | S1 |
| 1 mois | 2026-04-16 | $500 | $482 | $-18 | -3.6% | -25.3% | 51 | 45% | S1 |
| 1 mois | 2026-04-16 | $1 000 | $964 | $-36 | -3.6% | -25.3% | 51 | 45% | S1 |
| 1 mois | 2026-04-16 | $2 000 | $1 928 | $-72 | -3.6% | -25.3% | 51 | 45% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $300 | $369 | +$69 | +23.1% | -5.1% | 30 | 57% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $616 | +$116 | +23.1% | -5.1% | 30 | 57% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 231 | +$231 | +23.1% | -5.1% | 30 | 57% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $2 000 | $2 462 | +$462 | +23.1% | -5.1% | 30 | 57% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $300 | $361 | +$61 | +20.2% | -5.1% | 28 | 57% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $601 | +$101 | +20.2% | -5.1% | 28 | 57% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 202 | +$202 | +20.2% | -5.1% | 28 | 57% | S1 |
| depuis 2026-05-01 | 2026-05-01 | $2 000 | $2 404 | +$404 | +20.2% | -5.1% | 28 | 57% | S1 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $2 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 73 | 49% | +$307 545 |
| S10 | 355 | 55% | +$191 746 |
| S5 | 451 | 48% | +$672 307 |
| S8 | 114 | 57% | +$632 366 |
| S9 | 113 | 50% | +$1 038 835 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 13 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
