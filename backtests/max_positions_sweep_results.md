# MAX_POSITIONS sweep — résultats walk-forward 4/4

_Generated 2026-05-16 08:24 UTC._

Sub-caps fixed: MAX_MACRO_SLOTS=3, MAX_TOKEN_SLOTS=4. 
Effective cap = min(MAX_POSITIONS, 3+4=7), so results for ≥7 should be identical.

## Critère 4/4 strict
- ΔPnL_pct > 0 sur chaque fenêtre
- avg ΔDD ≤ +1pp

## Baseline (MAX_POSITIONS=6)

| Window | PnL % | DD % | Trades | S1 n |
|---|---:|---:|---:|---:|
| 28 mois | +462321.0% | -74.3% | 1110 | 109 |
| 12 mois | +7976.6% | -41.4% | 459 | 8 |
| 6 mois | +1253.4% | -32.9% | 230 | 3 |
| 3 mois | +421.8% | -16.8% | 134 | 3 |

## MAX_POSITIONS = 7

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 28 mois | +519294.6% | **+56973.6pp** | -74.3% | +0.0pp | 1110 | +0 |
| 12 mois | +7976.6% | **+0.0pp** | -41.4% | +0.0pp | 459 | +0 |
| 6 mois | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 |
| 3 mois | +421.8% | **+0.0pp** | -16.8% | +0.0pp | 134 | +0 |

Avg ΔPnL: +14243.4pp · Avg ΔDD: +0.00pp · **✗ FAIL (1/4 ΔPnL > 0, avg ΔDD = +0.00pp)**

## MAX_POSITIONS = 8

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 28 mois | +519294.6% | **+56973.6pp** | -74.3% | +0.0pp | 1110 | +0 |
| 12 mois | +7976.6% | **+0.0pp** | -41.4% | +0.0pp | 459 | +0 |
| 6 mois | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 |
| 3 mois | +421.8% | **+0.0pp** | -16.8% | +0.0pp | 134 | +0 |

Avg ΔPnL: +14243.4pp · Avg ΔDD: +0.00pp · **✗ FAIL (1/4 ΔPnL > 0, avg ΔDD = +0.00pp)**

## MAX_POSITIONS = 9

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 28 mois | +519294.6% | **+56973.6pp** | -74.3% | +0.0pp | 1110 | +0 |
| 12 mois | +7976.6% | **+0.0pp** | -41.4% | +0.0pp | 459 | +0 |
| 6 mois | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 |
| 3 mois | +421.8% | **+0.0pp** | -16.8% | +0.0pp | 134 | +0 |

Avg ΔPnL: +14243.4pp · Avg ΔDD: +0.00pp · **✗ FAIL (1/4 ΔPnL > 0, avg ΔDD = +0.00pp)**