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

## Pistes ouvertes
- Combiner Funding Sniper avec Extreme Reversion pour plus de trades
- Détecter le DÉBUT d'un gros move (pas le milieu) — insight du Multi-TF
- Filtrer les symboles perdants (TRX, BCH, TON) du Funding Sniper
