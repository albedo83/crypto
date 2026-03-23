# Research Findings — Strategy Backtests (23 mars 2026)

## Context
Backtest sur données historiques Binance. Klines 90j, OI/LS 27j, Funding 90j.
13 symboles altcoins + BTC/ETH référence. Coût simulé: 4 bps roundtrip.

## OI Divergence (stratégie originale) — INVALIDÉE
- Backtest 27j: **-$202 sur $1000** (1491 trades, 46% win)
- Signal quasi-aléatoire sur données récentes (gross +1.4 bps, net -2.6)
- Study_06 trouvait +21 bps sur 7j d'ADA → artefact statistique
- 42% des trades touchent le stop loss → le signal entre dans le mauvais sens

## Stratégies testées (classement)

### GAGNANTES

| Stratégie | Net/trade | Trades/90j | Win% | Robustesse |
|---|---|---|---|---|
| **Funding Sniper (entry-1h, hold 2h)** | +24.8 bps | 85 | 52% | ✓ 3 mois, mécanique |
| **Funding Sniper (entry-2h, hold 30m)** | +19.9 bps | 85 | 52% | ✓ 3 mois |
| **Funding Momentum (3× consécutif)** | +17.0 bps | 90 (27j) | 53% | ✓ edge structurel |
| **Extreme Reversion (>150bps/1h)** | +9.8 bps | 16567 (27j) | 55% | ⚠ beaucoup de trades |
| OI velocity >1% follow (120m) | +4.9 bps | 279 (27j) | 54% | ⚠ marginal |
| Volume Spike z>4 (30m) | +0.9 bps | 3025 (27j) | 54% | ⚠ marginal |

### PERDANTES
- Momentum Cascade (BTC → alts): -0.6 à -5.7 bps
- Smart Money seul: -4.0 bps (49% win = aléatoire)
- Cross-symbol lag: -4.0 bps
- Fixed-time momentum: -3.3 à -17.4 bps
- Bollinger Squeeze: -4.9 bps
- Crowd Capitulation: -0.6 bps
- Post-settlement reversion: -10.1 bps

### TROP BEAU (biais look-ahead)
- Multi-TF Follow (1h>100, 4h>150): +206 bps, 95% win → entre au milieu du move, pas exploitable tel quel

## Funding Sniper — Analyse détaillée (90 jours)

**Config optimale**: fund > 3 bps, entry 1h avant settlement, hold 2h

**Par mois**:
- Déc 2025: -22 bps (15 trades)
- Jan 2026: +24 bps (22 trades)
- Fév 2026: +100 bps (22 trades)
- Mar 2026: -11 bps (26 trades)

**Par session**:
- Asia: +41 bps ✓
- Overnight: +86 bps ✓
- US: -21 bps ✗ → ne pas trader

**Par symbole (top)**:
- ZROUSDT: +65 bps (29 trades) ✓
- XMRUSDT: +20 bps (18 trades) ✓
- XRPUSDT: +65 bps (2 trades) ✓
- AVAXUSDT: +19 bps (3 trades) ✓

**Symboles perdants**: TRX (-18 bps), BCH (-29 bps), TON (-50 bps)

**P&L simulation**: +$53 sur 90j à $250/trade = +$17.6/mois

## Backtest 1 AN — Extreme Reversion (signal principal validé)

**Période** : 23 mars 2025 → 23 mars 2026 (365 jours, 10 symboles)

### Résultats bruts signal

| Signal | Net/trade | Trades/jour | Win% | Robustesse |
|---|---|---|---|---|
| **Extreme Reversion >150bps** | **+7.5 bps** | 85 | 53% | ✓ 1 an, 31182 trades |
| Funding Sniper >3bps | +1.2 bps | 0.8 | 54% | ⚠ fragile sur 1 an |
| Combiné | +7.4 bps | 86 | 53% | ✓ |

### Funding Sniper : edge instable dans le temps

| Période | Net/trade |
|---|---|
| 27 jours | +24.8 bps |
| 90 jours | +19.9 bps |
| **1 an** | **+1.2 bps** ← quasi-nul |

Le funding sniper surperformait sur la période récente mais ne tient pas sur 1 an. L'extreme reversion est le vrai moteur.

### Par mois (combiné, hold 120m)

| Mois | Trades | Net bps | Résultat |
|---|---|---|---|
| 2025-03 | 839 | -9.7 | ✗ |
| 2025-04 | 3142 | +6.4 | ✓ |
| 2025-05 | 3426 | -6.9 | ✗ |
| 2025-06 | 2002 | -8.4 | ✗ |
| 2025-07 | 3068 | -14.8 | ✗ |
| 2025-08 | 2292 | +14.3 | ✓ |
| 2025-09 | 1275 | -14.3 | ✗ |
| 2025-10 | 2889 | +52.8 | ✓✓ |
| 2025-11 | 3565 | +10.7 | ✓ |
| 2025-12 | 1612 | -22.1 | ✗ |
| 2026-01 | 2469 | -9.2 | ✗ |
| 2026-02 | 3618 | +38.2 | ✓✓ |
| 2026-03 | 1263 | +17.1 | ✓ |

**6 mois perdants / 13** — mais les gagnants gagnent plus que les perdants perdent.

### Par symbole (1 an)

Tous positifs sauf AVAXUSDT (-1.5 bps). BNB est le meilleur (+23 bps).

### Estimation P&L réaliste

- Max ~22 trades/jour (4 positions × 11h / 2h hold)
- 22 × $250 × 7.4 bps = **~$4/jour = $123/mois = 12%/mois**
- Max drawdown : important (>$1000 en simulation brute)
- **6 mois perdants sur 13** — nécessite un circuit breaker ou filtre de régime

## Delta-Neutral Funding Carry — Analyse 1 an

### Sans basis risk (funding seul)
- 1 pair × 3x : **+3.6%/mois, 0 mois perdants** (trop beau)
- Le funding est mécanique : XMR (mean +1.62 bps) + ZRO (mean -0.89 bps)

### Avec basis risk (réaliste)
- Carry seul : **+$232/an = +1.8%/mois** mais **7 mois perdants**
- La divergence de prix entre les deux legs détruit les gains de funding
- Le basis risk est le vrai problème du carry delta-neutral

### Combo Carry + Extreme Reversion (simulation complète)
- **Résultat : -$782 sur 1 an** → le combo ne marche pas
- Le carry fait +$232, l'extreme reversion fait -$1014
- L'extreme reversion perd en simulation avec hold fixe 2h (pas de trailing stop)

### Leçon clé
Le carry sans gestion du basis risk promet 3.6%/mois mais livre 1.8%.
L'extreme reversion a un signal valide (+7.5 bps) mais la gestion de position doit
inclure un trailing stop pour capturer l'edge.

## Extreme Reversion avec gestion de position réelle — 1 an, 9 configs

**Résultat : TOUTES les configs perdent.**

| Config | Trades | Win% | P&L | %/mois | Losing months |
|---|---|---|---|---|---|
| Baseline (trail 25/15, sl -50, 150bps) | 4018 | 45% | -$360 | -2.8% | 9/13 |
| Tight (trail 15/10, sl -40) | 4413 | 43% | -$317 | -2.4% | 9/13 |
| Wide (trail 35/20, sl -60) | 3638 | 47% | -$362 | -2.8% | 8/13 |
| **Thresh 200 (meilleur)** | **2334** | **46%** | **-$173** | **-1.3%** | **8/13** |
| No trail, sl only | 2456 | 29% | -$169 | -1.3% | 8/13 |

### Pattern identique dans toutes les configs :
- Trail stop : **84% win, +$4348** → le trailing stop capture bien
- Stop loss : **0% win, -$5116** → les stop loss mangent TOUT le profit
- Le stop loss perd plus que le trailing stop gagne → net négatif

### Par symbole (config thresh 200) :
- Gagnants : XLM (+$84), SUI (+$28), ADA (+$23)
- Perdants : ZRO (-$241), XMR (-$231), AAVE (-$186)

### Conclusion
L'extreme reversion mean-reversion **ne fonctionne pas sur 1 an** même avec trailing stop.
Le signal raw (+7.5 bps) ne survit pas à la gestion de position (stop loss trop fréquents).
Le marché trend plus qu'il ne mean-revert sur ces timeframes.

## Pistes ouvertes
- Extreme reversion avec trailing stop (en cours de backtest)
- Circuit breaker (stop après N pertes/jour)
- Carry pur (sans extreme) comme base à ~2%/mois
- Réduire le basis risk du carry (rebalance plus fréquent, stop si divergence > seuil)
