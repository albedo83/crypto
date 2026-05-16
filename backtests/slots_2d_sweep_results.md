# Slot allocation 2D sweep — résultats walk-forward 4/4

_Generated 2026-05-16 08:28 UTC._

## Baseline (v12.6.3 — M=3, T=4, P=6, SD=4, PS=2)

| Window | PnL % | DD % | Trades |
|---|---:|---:|---:|
| 28 mois | +462321.0% | -74.3% | 1110 |
| 12 mois | +7976.6% | -41.4% | 459 |
| 6 mois | +1253.4% | -32.9% | 230 |
| 3 mois | +421.8% | -16.8% | 134 |

## Sweep results (vs baseline)

| Config | 28m ΔPnL | 12m ΔPnL | 6m ΔPnL | 3m ΔPnL | avg ΔDD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| dense_macro | +515388pp | -911.5pp | -553.1pp | -138.5pp | +5.91pp | ✗ 1/4 |
| dense_token | -124001pp | +632.3pp | -362.2pp | -77.2pp | -5.53pp | ✗ 1/4 |
| balanced_4_4_8 | +389425pp | +262.2pp | -44.8pp | -17.3pp | +0.00pp | ✗ 2/4 |
| max_total_4_5_9 | +20934pp | +943.6pp | -392.0pp | -90.6pp | -5.53pp | ✗ 2/4 |
| rollback_v12.6.2 | -344198pp | -763.8pp | -12.4pp | -4.8pp | -0.00pp | ✗ 0/4 |
| token_heavy_2_5_7 | -344991pp | -191.3pp | -371.3pp | -81.3pp | -5.53pp | ✗ 0/4 |
| loose_dir_5 | -80971pp | +121.4pp | +0.0pp | +0.0pp | +0.00pp | ✗ 1/4 |
| loose_sector_3 | -215528pp | -2380.1pp | +47.5pp | -16.7pp | +2.62pp | ✗ 1/4 |
| loose_dir+sector | -204283pp | -3294.7pp | +47.5pp | -16.7pp | +2.05pp | ✗ 1/4 |

## Notes
- `SD` = MAX_SAME_DIRECTION, `PS` = MAX_PER_SECTOR
- Strict criterion: ΔPnL > 0 sur chaque fenêtre + avg ΔDD ≤ +1pp
- Si un PASS apparaît, ship-eligible après revue mécanique (corrélation, slippage)