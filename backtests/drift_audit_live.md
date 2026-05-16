# Equity drift audit — trade par trade (live)

_Generated 2026-05-16. Wallet `0x6E2aE12f1F093CAA9710F15f933516B9b6fA2d5d`. Window 90d (HL fills cutoff)._

## TL;DR

- Trades bot dans la fenêtre : **95**
- Trades matched avec close-fills HL : **95**
- Trades unmatched (close fills introuvables) : **0**
- **Bot tracked gross PnL** : `$+8.91`
- **HL closed_pnl sum (matched)** : `$+6.07`
- **Δ total (bot − HL)** : **`$+2.84`** ← le drift recherché

Décomposition vs alerte EQUITY_DRIFT actuelle :
- Drift sur fills (gross discrepancy) : `$+2.84`
- Funding diff (bot $-3.53 vs HL $-4.01) : `+$0.48`
- Fees diff (bot $11.48 estimé vs HL $10.56 réel) : `+$0.92`
- Total expliqué : `$+4.24` (vs alerte `+$6.88`)

## Top 20 trades par |Δ| absolu

| Symbol | Strat | Dir | Size $ | bot_gross | HL closedPnl | Δ | n_fills | Reason |
|---|---|---|---:|---:|---:|---:|---:|---|
| INJ | S5 | SHORT | 270.4 | +47.31 | +40.26 | **+7.04** | 2 | manual_stop_set |
| SEI | S5 | SHORT | 226.3 | -28.52 | -25.33 | **-3.19** | 1 | catastrophe_stop |
| LDO | S5 | SHORT | 42.0 | -5.78 | -2.89 | **-2.89** | 1 | timeout |
| BLUR | S5 | SHORT | 42.0 | -5.14 | -2.57 | **-2.57** | 1 | timeout |
| LDO | S5 | SHORT | 218.2 | -20.18 | -22.24 | **+2.06** | 1 | manual_close |
| WLD | S10 | SHORT | 29.8 | -4.04 | -2.02 | **-2.02** | 1 | timeout |
| WLD | S5 | SHORT | 41.6 | -3.48 | -1.74 | **-1.74** | 1 | manual_close |
| WLD | S8 | SHORT | 21.0 | -3.32 | -1.66 | **-1.66** | 1 | catastrophe_stop |
| OP | S5 | SHORT | 219.0 | +19.14 | +17.60 | **+1.54** | 1 | timeout |
| DYDX | S5 | SHORT | 24.1 | +2.40 | +1.20 | **+1.20** | 1 | timeout |
| SNX | S10 | SHORT | 29.3 | +2.16 | +1.08 | **+1.08** | 2 | timeout |
| DOGE | S5 | SHORT | 197.1 | -13.03 | -13.95 | **+0.92** | 1 | timeout |
| GALA | S5 | SHORT | 189.1 | -11.37 | -12.09 | **+0.73** | 1 | manual_close |
| GALA | S10 | SHORT | 29.8 | +1.27 | +0.64 | **+0.64** | 1 | timeout |
| IMX | S10 | SHORT | 30.9 | +1.15 | +0.57 | **+0.57** | 1 | timeout |
| APT | S5 | SHORT | 86.7 | +6.70 | +7.26 | **-0.56** | 1 | timeout |
| SUI | S10 | SHORT | 10.0 | +0.97 | +0.48 | **+0.48** | 1 | timeout |
| GALA | S10 | SHORT | 10.0 | +0.89 | +0.45 | **+0.45** | 1 | timeout |
| NEAR | S5 | SHORT | 40.7 | +0.86 | +0.43 | **+0.43** | 1 | timeout |
| APT | S10 | SHORT | 10.0 | +0.86 | +0.43 | **+0.43** | 1 | timeout |

## Per-token aggregation

| Symbol | n trades | Σ delta | avg / trade |
|---|---:|---:|---:|
| INJ | 4 | **+7.66** | +1.914 |
| WLD | 7 | **-5.16** | -0.737 |
| SEI | 5 | **-3.19** | -0.638 |
| BLUR | 7 | **-2.66** | -0.381 |
| GALA | 4 | **+1.81** | +0.452 |
| OP | 2 | **+1.54** | +0.770 |
| SNX | 4 | **+1.48** | +0.371 |
| DYDX | 6 | **+1.10** | +0.183 |
| DOGE | 7 | **+0.92** | +0.132 |
| LDO | 5 | **-0.78** | -0.155 |
| AAVE | 7 | **-0.69** | -0.098 |
| SUI | 2 | **+0.56** | +0.279 |
| NEAR | 4 | **+0.44** | +0.111 |
| COMP | 6 | **-0.33** | -0.055 |
| IMX | 2 | **+0.19** | +0.094 |
| ARB | 4 | **-0.18** | -0.046 |
| PENDLE | 3 | **+0.16** | +0.055 |
| SOL | 1 | **-0.04** | -0.039 |
| CRV | 2 | **+0.01** | +0.005 |
| APT | 3 | **-0.00** | -0.001 |
| PYTH | 3 | **-0.00** | -0.001 |
| SAND | 2 | **-0.00** | -0.000 |
| TON | 2 | **+0.00** | +0.000 |
| AVAX | 1 | **-0.00** | -0.000 |
| MINA | 1 | **+0.00** | +0.000 |
| GMX | 1 | **+0.00** | +0.000 |

## Unmatched trades (no HL close fills found within ±5min)

_Tous les trades ont matched des fills HL. Aucun trade fantôme._

## Trades avec multi-fills à la clôture (partial fills)

- **16** trades sur 95 ont eu &gt;1 fill HL à la clôture.
- Σ Δ sur multi-fill : `$+8.11` (16 trades, avg `$+0.507`/trade)
- Σ Δ sur single-fill : `$-5.28` (79 trades, avg `$-0.067`/trade)