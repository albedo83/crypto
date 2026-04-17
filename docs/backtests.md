# Rolling backtests

**Générée le** : 2026-04-17 13:07 UTC
**Bot version** : v11.4.10
**Données jusqu'à** : 2026-04-17

Chaque ligne répond à la question : *si j'avais lancé le bot avec $1 000 au début de cette fenêtre jusqu'à la date des données, avec les paramètres actuels du bot, combien aurais-je fini ?*

P&L calculé avec la formule corrigée v11.3.0+ (`size_usdt` est le notionnel, pas de multiplication par le levier). Capital de départ : $1 000.

**Coûts backtest** : 14 bps round-trip = 10 bps (taker 9 + funding 1, calibrés depuis les fills live) + 4 bps de slippage moyen que le backtest doit modéliser puisqu'il utilise les closes 4h au lieu de l'avgPx réel. Le live bot lui n'applique que 10 bps car le slippage est déjà dans l'avgPx.

Ce fichier est **régénéré automatiquement** par `python3 -m backtests.backtest_rolling`. Relancer après tout changement de règles ou de paramètres du bot.

## Filtres actifs (v11.4.10)

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

| Fenêtre | Start | Balance finale | P&L | P&L % | DD max | Trades | WR | Best strat |
|---|---|---|---|---|---|---|---|---|
| 28 mois | 2023-12-17 | $115 079 | +$114 079 | +11407.9% | -58.8% | 1083 | 53% | S9 |
| 12 mois | 2025-04-17 | $15 846 | +$14 846 | +1484.6% | -29.9% | 455 | 55% | S9 |
| 6 mois | 2025-10-17 | $5 313 | +$4 313 | +431.3% | -22.2% | 232 | 57% | S9 |
| depuis 2025-11-01 | 2025-11-01 | $4 104 | +$3 104 | +310.4% | -22.2% | 212 | 55% | S9 |
| depuis 2025-12-01 | 2025-12-01 | $2 834 | +$1 834 | +183.4% | -22.2% | 172 | 53% | S9 |
| depuis 2026-01-01 | 2026-01-01 | $2 427 | +$1 427 | +142.7% | -22.2% | 144 | 50% | S9 |
| 3 mois | 2026-01-17 | $2 448 | +$1 448 | +144.8% | -22.2% | 126 | 52% | S9 |
| depuis 2026-02-01 | 2026-02-01 | $1 584 | +$584 | +58.4% | -27.5% | 113 | 49% | S9 |
| depuis 2026-03-01 | 2026-03-01 | $955 | $-45 | -4.5% | -21.0% | 65 | 45% | S10 |
| 1 mois | 2026-03-17 | $944 | $-56 | -5.6% | -21.0% | 50 | 44% | S10 |
| depuis 2026-04-01 | 2026-04-01 | $928 | $-72 | -7.2% | -15.7% | 29 | 41% | S10 |

## Breakdown par stratégie sur la fenêtre la plus longue (28 mois)

| Stratégie | Trades | Win Rate | P&L |
|---|---|---|---|
| S1 | 73 | 51% | +$471 |
| S10 | 331 | 56% | +$14 689 |
| S5 | 441 | 49% | +$22 404 |
| S8 | 116 | 61% | +$31 532 |
| S9 | 122 | 50% | +$44 983 |

## Méthodologie

- **Source** : candles 4h Hyperliquid, 28 tokens traded + BTC/ETH référence.
- **Features** : `backtests.backtest_genetic.build_features` + secteurs via `backtest_sector`.
- **Params** : importés directement depuis `analysis.bot.config` (`SIZE_PCT`, `SIGNAL_MULT`, `STOP_LOSS_BPS`, etc.). Tout changement du bot est automatiquement reflété au prochain run.
- **Entry timing** : open de la bougie suivante (no look-ahead).
- **Exit** : stop détecté sur low/high de la bougie, sinon timeout au hold configuré. S9 early exit si unrealized < -500 bps après 8h.
- **Positions restantes** en fin de fenêtre : mark-to-market au dernier close.
- **Costs** : 14 bps par trade round-trip (9 taker + 1 funding + 4 slippage backtest). Pas de multiplication par le levier.

## Limites

- Les S10 features (squeeze detection) utilisent les mêmes bougies 4h que les autres signaux. Le live bot utilise aussi des ticks 60s pour certains contextes (OI delta, crowding) qui ne sont pas disponibles dans l'historique → cette dimension est absente du backtest.
- Pas de modélisation du slippage variable selon la liquidité du carnet — on applique un coût fixe de 10 bps.
- Pas de modélisation des funding rates variables — on utilise le coût moyen.
- Les fenêtres courtes (1 mois, 3 mois) sont statistiquement bruitées : S8 fire ~1/mois, S1 rarement. Prendre les résultats avec précaution.
