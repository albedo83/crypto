# S9 dead-in-water — walk-forward 4/4 strict

_Generated 2026-05-16. Mirror v12.6.0 S8 mechanic. 4 variantes._

Critère: ΔPnL > 0 sur chaque fenêtre + avg ΔDD ≤ +1pp.

## Baseline (no hook)

| Window | PnL % | DD % | Trades |
|---|---:|---:|---:|
| 28m | +556259.2% | -74.3% | 1130 |
| 12m | +8730.7% | -41.4% | 464 |
| 6m | +1253.4% | -32.9% | 230 |
| 3m | +299.2% | -16.8% | 132 |

## Variant A_SHORT_T8h: S9 dir=-1 T+8h

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | Fired | Eval |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +560211.4% | **+3952.2pp** | -74.3% | +0.0pp | 1133 | +3 | 2 | 57 |
| 12m | +8730.7% | **+0.0pp** | -41.4% | +0.0pp | 464 | +0 | 0 | 17 |
| 6m | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 | 0 | 9 |
| 3m | +299.2% | **+0.0pp** | -16.8% | +0.0pp | 132 | +0 | 0 | 5 |

Avg ΔPnL: +988.1pp · Avg ΔDD: +0.00pp · **✗ 1/4 (avg ΔDD = +0.00pp)**

## Variant B_LONG_T8h: S9 dir=1 T+8h

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | Fired | Eval |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +538934.6% | **-17324.7pp** | -74.3% | +0.0pp | 1131 | +1 | 1 | 21 |
| 12m | +8455.8% | **-275.0pp** | -41.4% | -0.0pp | 465 | +1 | 1 | 5 |
| 6m | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 | 0 | 1 |
| 3m | +299.2% | **+0.0pp** | -16.8% | +0.0pp | 132 | +0 | 0 | 0 |

Avg ΔPnL: -4399.9pp · Avg ΔDD: -0.00pp · **✗ 0/4 (avg ΔDD = -0.00pp)**

## Variant C_SHORT_T4h: S9 dir=-1 T+4h

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | Fired | Eval |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +413732.4% | **-142526.9pp** | -71.1% | +3.2pp | 1131 | +1 | 9 | 62 |
| 12m | +8104.5% | **-626.3pp** | -44.9% | -3.5pp | 464 | +0 | 2 | 20 |
| 6m | +1257.5% | **+4.1pp** | -32.9% | +0.0pp | 230 | +0 | 1 | 10 |
| 3m | +300.5% | **+1.2pp** | -16.8% | +0.0pp | 132 | +0 | 1 | 6 |

Avg ΔPnL: -35786.9pp · Avg ΔDD: -0.06pp · **✗ 2/4 (avg ΔDD = -0.06pp)**

## Variant D_LONG_T4h: S9 dir=1 T+4h

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | Fired | Eval |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +540808.5% | **-15450.7pp** | -74.3% | +0.0pp | 1131 | +1 | 2 | 23 |
| 12m | +8485.5% | **-245.2pp** | -41.4% | +0.0pp | 465 | +1 | 2 | 6 |
| 6m | +1253.4% | **+0.0pp** | -32.9% | +0.0pp | 230 | +0 | 0 | 1 |
| 3m | +299.2% | **+0.0pp** | -16.8% | +0.0pp | 132 | +0 | 0 | 0 |

Avg ΔPnL: -3924.0pp · Avg ΔDD: +0.00pp · **✗ 0/4 (avg ΔDD = +0.00pp)**

## Stragglers per variant (28m sample)

Trades cut where the engine's trade-list shows the cut LOCKED a loss smaller than the trade would have ended at. Reported as count and total bps saved/lost.

| Variant | n_fired_28m | n_fired_12m | n_fired_6m | n_fired_3m |
|---|---:|---:|---:|---:|
| A_SHORT_T8h | 2 | 0 | 0 | 0 |
| B_LONG_T8h | 1 | 1 | 0 | 0 |
| C_SHORT_T4h | 9 | 2 | 1 | 1 |
| D_LONG_T4h | 2 | 2 | 0 | 0 |

## Summary

| Variant | Pass | Avg ΔPnL | Avg ΔDD | Verdict |
|---|---:|---:|---:|---|
| A_SHORT_T8h | 1/4 | +988.1pp | +0.00pp | ✗ 1/4 |
| B_LONG_T8h | 0/4 | -4399.9pp | -0.00pp | ✗ 0/4 |
| C_SHORT_T4h | 2/4 | -35786.9pp | -0.06pp | ✗ 2/4 |
| D_LONG_T4h | 0/4 | -3924.0pp | +0.00pp | ✗ 0/4 |