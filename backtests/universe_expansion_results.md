# Universe expansion Phase 2 — Backtest comparatif

_Generated 2026-05-16. Configs A (29+10=39) et B (29+20=49) vs baseline (29)._

## Critère gate
- ΔPnL > 0 sur **6m ET 12m** (2/2 strict)
- avg ΔDD ≤ +2pp

## Baseline (29 tokens, v12.6.3 config)

| Window | PnL % | DD % | Trades |
|---|---:|---:|---:|
| 6m | +908.6% | -32.9% | 233 |
| 12m | +5546.9% | -41.4% | 471 |

## config_A — 39 tokens (+10 new)

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 6m | +955.8% | **+47.2pp** | -41.4% | -8.5pp | 245 | +12 |
| 12m | +8050.0% | **+2503.0pp** | -41.4% | +0.0pp | 505 | +34 |

**Verdict**: ✗ 2/2 (avg DD degradation = +4.25pp)

## config_B — 49 tokens (+20 new)

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 6m | +684.9% | **-223.7pp** | -53.3% | -20.4pp | 278 | +45 |
| 12m | +8856.1% | **+3309.2pp** | -53.3% | -11.9pp | 541 | +70 |

**Verdict**: ✗ 1/2 (avg DD degradation = +16.16pp)

## config_C_curated — 35 tokens (+6 new)

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 6m | +966.2% | **+57.6pp** | -36.3% | -3.5pp | 242 | +9 |
| 12m | +7812.4% | **+2265.4pp** | -41.4% | -0.0pp | 489 | +18 |

**Verdict**: ✓ PASS 2/2

## config_C_minus — 34 tokens (+5 new)

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |
|---|---:|---:|---:|---:|---:|---:|
| 6m | +909.5% | **+0.8pp** | -36.3% | -3.5pp | 236 | +3 |
| 12m | +6835.3% | **+1288.3pp** | -41.4% | -0.0pp | 483 | +12 |

**Verdict**: ✓ PASS 2/2