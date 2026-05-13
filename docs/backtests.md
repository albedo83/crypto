# Rolling backtests

**Générée le** : 2026-05-13 09:35 UTC
**Bot version** : v12.5.9
**Données jusqu'à** : 2026-05-13
**Capitaux testés** : $500 / $1 000

Chaque ligne répond à la question : *si j'avais lancé le bot avec $500 / $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier).

**Coûts backtest** : 13 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v12.5.9)

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
| 28 mois | 2024-01-13 | $500 | $866 414 | +$865 914 | +173182.8% | -74.3% | 1105 | 52% | S9 |
| 28 mois | 2024-01-13 | $1 000 | $1 732 874 | +$1 731 874 | +173187.4% | -74.3% | 1105 | 52% | S9 |
| 12 mois | 2025-05-13 | $500 | $45 558 | +$45 058 | +9011.6% | -41.4% | 461 | 55% | S9 |
| 12 mois | 2025-05-13 | $1 000 | $91 116 | +$90 116 | +9011.6% | -41.4% | 461 | 55% | S9 |
| 6 mois | 2025-11-13 | $500 | $7 029 | +$6 529 | +1305.8% | -32.9% | 230 | 54% | S9 |
| 6 mois | 2025-11-13 | $1 000 | $14 058 | +$13 058 | +1305.8% | -32.9% | 230 | 54% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $500 | $5 743 | +$5 243 | +1048.6% | -32.9% | 210 | 53% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $1 000 | $11 487 | +$10 487 | +1048.7% | -32.9% | 210 | 53% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $500 | $4 710 | +$4 210 | +842.1% | -32.9% | 184 | 52% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $1 000 | $9 421 | +$8 421 | +842.1% | -32.9% | 184 | 52% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $500 | $2 492 | +$1 992 | +398.3% | -44.2% | 154 | 51% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 000 | $4 983 | +$3 983 | +398.3% | -44.2% | 154 | 51% | S9 |
| 3 mois | 2026-02-13 | $500 | $1 663 | +$1 163 | +232.5% | -17.4% | 135 | 53% | S5 |
| 3 mois | 2026-02-13 | $1 000 | $3 325 | +$2 325 | +232.5% | -17.4% | 135 | 53% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $500 | $698 | +$198 | +39.5% | -15.5% | 107 | 49% | S5 |
| depuis 2026-03-01 | 2026-03-01 | $1 000 | $1 395 | +$395 | +39.5% | -15.5% | 107 | 49% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $500 | $722 | +$222 | +44.4% | -11.8% | 79 | 51% | S5 |
| depuis 2026-03-25 | 2026-03-25 | $1 000 | $1 444 | +$444 | +44.4% | -11.8% | 79 | 51% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $500 | $689 | +$189 | +37.8% | -11.8% | 77 | 49% | S5 |
| depuis 2026-03-26 | 2026-03-26 | $1 000 | $1 378 | +$378 | +37.8% | -11.8% | 77 | 49% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $500 | $720 | +$220 | +44.1% | -11.8% | 71 | 52% | S5 |
| depuis 2026-04-01 | 2026-04-01 | $1 000 | $1 441 | +$441 | +44.1% | -11.8% | 71 | 52% | S5 |
| 1 mois | 2026-04-13 | $500 | $577 | +$77 | +15.4% | -25.3% | 52 | 50% | S1 |
| 1 mois | 2026-04-13 | $1 000 | $1 154 | +$154 | +15.4% | -25.3% | 52 | 50% | S1 |
| depuis 2026-04-29 | 2026-04-29 | $500 | $690 | +$190 | +37.9% | -5.1% | 27 | 67% | S5 |
| depuis 2026-04-29 | 2026-04-29 | $1 000 | $1 379 | +$379 | +37.9% | -5.1% | 27 | 67% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $500 | $606 | +$106 | +21.2% | -5.3% | 24 | 62% | S5 |
| depuis 2026-05-01 | 2026-05-01 | $1 000 | $1 212 | +$212 | +21.2% | -5.3% | 24 | 62% | S5 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois, capital $1 000)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 73 | 49% | +$167 232 |
| S10 | 354 | 55% | +$104 243 |
| S5 | 450 | 49% | +$551 612 |
| S8 | 114 | 57% | +$343 860 |
| S9 | 114 | 50% | +$564 926 |

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
