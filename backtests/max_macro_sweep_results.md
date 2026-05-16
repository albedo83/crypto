# MAX_MACRO_SLOTS sweep — résultats walk-forward 4/4

_Generated 2026-05-16 08:03 UTC._

Data ends: 2026-05-16T04:00:00+00:00

Baseline: MAX_MACRO_SLOTS = 2 (production). Sweep tests [2, 3, 4, 5] on 28m/12m/6m/3m. Capital $1000. apply_adaptive_modulator=True.

## Critère d'acceptation strict 4/4
- ΔPnL_pct > 0 sur **chacune** des 4 fenêtres
- avg ΔDD_pct ≤ +1pp

## Baseline (MAX_MACRO_SLOTS=2)

| Window | PnL % | DD % | Trades | S1 n | S1 pnl $ |
|---|---:|---:|---:|---:|---:|
| 28 mois | +118122.9% | -74.3% | 1086 | 73 | +127808 |
| 12 mois | +7212.8% | -41.4% | 459 | 6 | +7756 |
| 6 mois | +1241.0% | -32.9% | 229 | 2 | +1501 |
| 3 mois | +417.0% | -16.8% | 133 | 2 | +579 |

## MAX_MACRO_SLOTS = 3

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | S1 n | ΔS1 n | S1 pnl $ | ΔS1 pnl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 28 mois | +462321.0% | **+344198.1pp** | -74.3% | +0.0pp | 1110 | +24 | 109 | +36 | +542994 | +415186 |
| 12 mois | +7976.6% | **+763.8pp** | -41.4% | -0.0pp | 459 | +0 | 8 | +2 | +9284 | +1529 |
| 6 mois | +1253.4% | **+12.4pp** | -32.9% | +0.0pp | 230 | +1 | 3 | +1 | +1629 | +128 |
| 3 mois | +421.8% | **+4.8pp** | -16.8% | +0.0pp | 134 | +1 | 3 | +1 | +628 | +49 |

Avg ΔPnL: +86244.8pp  ·  Avg ΔDD: +0.00pp  ·  **✓ PASS 4/4 strict**

## MAX_MACRO_SLOTS = 4

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | S1 n | ΔS1 n | S1 pnl $ | ΔS1 pnl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 28 mois | +276498.2% | **+158375.2pp** | -74.3% | +0.0pp | 1139 | +53 | 144 | +71 | +673526 | +545718 |
| 12 mois | +8238.8% | **+1026.0pp** | -41.4% | -0.0pp | 461 | +2 | 10 | +4 | +20082 | +12327 |
| 6 mois | +1208.6% | **-32.4pp** | -32.9% | +0.0pp | 231 | +2 | 4 | +2 | +3223 | +1722 |
| 3 mois | +404.5% | **-12.5pp** | -16.8% | +0.0pp | 135 | +2 | 4 | +2 | +1243 | +664 |

Avg ΔPnL: +39839.1pp  ·  Avg ΔDD: +0.00pp  ·  **✗ FAIL (2/4 ΔPnL > 0, avg ΔDD = +0.00pp)**

## MAX_MACRO_SLOTS = 5

| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr | S1 n | ΔS1 n | S1 pnl $ | ΔS1 pnl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 28 mois | +276498.2% | **+158375.2pp** | -74.3% | +0.0pp | 1139 | +53 | 144 | +71 | +673526 | +545718 |
| 12 mois | +8238.8% | **+1026.0pp** | -41.4% | -0.0pp | 461 | +2 | 10 | +4 | +20082 | +12327 |
| 6 mois | +1208.6% | **-32.4pp** | -32.9% | +0.0pp | 231 | +2 | 4 | +2 | +3223 | +1722 |
| 3 mois | +404.5% | **-12.5pp** | -16.8% | +0.0pp | 135 | +2 | 4 | +2 | +1243 | +664 |

Avg ΔPnL: +39839.1pp  ·  Avg ΔDD: +0.00pp  ·  **✗ FAIL (2/4 ΔPnL > 0, avg ΔDD = +0.00pp)**

## Lecture

Augmenter MAX_MACRO_SLOTS au-delà de 2 fait passer plus de signaux S1 — mais ces trades supplémentaires consomment des slots qui auraient pu aller à des trades token (S5/S8/S9/S10). Le critère 4/4 strict détermine si le compromis est favorable sur les 4 fenêtres simultanément.

**Si PASS** : pré-register un ship (modifier `MAX_MACRO_SLOTS` dans `config.py`, bump VERSION, restart). Backtest a déjà validé le delta net.

**Si FAIL** : la frustration sur le S1 raté (PENDLE 04-05) est une observation court-terme. Sur 28m+12m+6m+3m de backtest, les slots macro à 2 sont déjà l'optimum (ou très proche).
