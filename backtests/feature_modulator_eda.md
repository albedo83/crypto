# Feature-modulator EDA — results

_Generated 2026-05-15 14:19 UTC_

## TL;DR

**1 candidate** survived Bonferroni + effect-floor on the 28m window: 0 on both (`net_bps` AND `pnl_usdt`), 0 on `net_bps` only, 1 on `pnl_usdt` only.

**Important**: all candidates are `pnl_usdt`-only survivors. PnL aggregates per-trade bps with size, and under the v11.10.0 adaptive macro modulator size varies with `btc_z`. For S1 in particular, the candidate's ρ_pnl signal may be conflated with the size-amplification at high `btc_z` — see the Candidates section for the asymmetry warning. Treat as **provisional**, not a clear go-signal.

See the Candidates section for details and 12m replication status.

- Dataset: 28m=1105 trades, 12m=461 trades
- Features: `conf_partial` (0-4) + one-hot `session_*` (Asia, EU, US, Night, WE)
- Effect floor: |ρ| ≥ 0.15
- Family-wise α = 0.05 (Bonferroni over actually-run tests)
- Null shuffles per test: 200

## Method

1. Ran `backtest_rolling.run_window` with the live shipped config (D2 dead-timeout + runner extension + v11.10.0 adaptive macro modulator) on the 28m + 12m windows, using a patched engine that records two at-entry features on every trade:
   - `conf_partial = sum([|drawdown|>3000, vol_z>1.5, |ret_24h|>200, n_stress_global≥5])` — mirrors `analysis/bot/bot.py:258-262` minus the OI component (`oi_delta_1h < -1.0`) which is unavailable in backtest.
   - `session ∈ {Asia, EU, US, Night, WE}` derived from the entry candle's UTC hour and weekday — exact mirror of the live formula at `analysis/bot/bot.py:263-266`.
2. For each (strategy ∈ {S1,…,S10}, direction ∈ {+1,-1}, feature ∈ {conf_partial, session_*}) cell with n ≥ 30 trades: Spearman ρ vs `net_bps` and `pnl_usdt`.
3. Null-shuffle p-value: shuffle the feature array 200 times, recompute ρ, p = fraction of |ρ_shuffle| ≥ |ρ_real|.
4. Bonferroni correction across the actual test count (degenerate one-hot cells with std=0 are skipped before counting).
5. A pair is a **candidate** when `p_bonferroni < 0.05` AND `|ρ| ≥ 0.15` for either `net_bps` or `pnl_usdt`.

## Sample sizes

| Strat | Dir | n (28m) | n (12m) |
|---|---|---|---|
| S1 | LONG | 73 | 0 |
| S1 | SHORT | 0 ⚠ low-n | 0 |
| S5 | LONG | 281 | 126 |
| S5 | SHORT | 169 | 73 |
| S8 | LONG | 114 | 54 |
| S8 | SHORT | 0 ⚠ low-n | 0 |
| S9 | LONG | 0 ⚠ low-n | 0 |
| S9 | SHORT | 86 | 0 |
| S10 | LONG | 0 ⚠ low-n | 0 |
| S10 | SHORT | 354 | 163 |

_Cells with n<30 on 28m skipped from testing._

## Results — 28m window

Sorted by |ρ_net| descending. `p_bonf` columns are the raw null-shuffle p multiplied by the test count (capped at 1 in the table for readability).

| Strat | Dir | Feature | n | ρ_net | p_bonf_net | ρ_pnl | p_bonf_pnl | sig |
|---|---|---|---|---|---|---|---|---|
| S1 | LONG | `session_Asia` | 73 | +0.282 | 0.300 | +0.393 | 0.000 | ✓ |
| S9 | SHORT | `session_EU` | 86 | -0.187 | 1.000 | -0.202 | 1.000 |  |
| S9 | SHORT | `session_Asia` | 86 | +0.171 | 1.000 | +0.125 | 1.000 |  |
| S1 | LONG | `session_US` | 73 | -0.123 | 1.000 | -0.114 | 1.000 |  |
| S5 | SHORT | `conf_partial` | 169 | +0.116 | 1.000 | +0.082 | 1.000 |  |
| S8 | LONG | `conf_partial` | 114 | -0.094 | 1.000 | -0.070 | 1.000 |  |
| S10 | SHORT | `session_EU` | 354 | -0.091 | 1.000 | -0.046 | 1.000 |  |
| S10 | SHORT | `session_Asia` | 354 | +0.085 | 1.000 | +0.049 | 1.000 |  |
| S9 | SHORT | `session_WE` | 86 | +0.079 | 1.000 | +0.103 | 1.000 |  |
| S1 | LONG | `session_EU` | 73 | -0.079 | 1.000 | -0.201 | 1.000 |  |
| S1 | LONG | `conf_partial` | 73 | +0.078 | 1.000 | +0.018 | 1.000 |  |
| S5 | LONG | `session_EU` | 281 | +0.077 | 1.000 | +0.070 | 1.000 |  |
| S10 | SHORT | `session_US` | 354 | -0.071 | 1.000 | -0.070 | 1.000 |  |
| S5 | LONG | `session_Asia` | 281 | -0.070 | 1.000 | +0.002 | 1.000 |  |
| S9 | SHORT | `conf_partial` | 86 | -0.068 | 1.000 | -0.059 | 1.000 |  |
| S10 | SHORT | `session_WE` | 354 | +0.065 | 1.000 | +0.061 | 1.000 |  |
| S1 | LONG | `session_WE` | 73 | -0.064 | 1.000 | -0.058 | 1.000 |  |
| S9 | SHORT | `session_US` | 86 | -0.064 | 1.000 | -0.028 | 1.000 |  |
| S8 | LONG | `session_WE` | 114 | -0.060 | 1.000 | +0.044 | 1.000 |  |
| S5 | SHORT | `session_WE` | 169 | -0.059 | 1.000 | +0.033 | 1.000 |  |
| S8 | LONG | `session_Asia` | 114 | +0.052 | 1.000 | +0.119 | 1.000 |  |
| S10 | SHORT | `conf_partial` | 354 | +0.049 | 1.000 | +0.043 | 1.000 |  |
| S5 | SHORT | `session_US` | 169 | +0.045 | 1.000 | -0.042 | 1.000 |  |
| S5 | LONG | `session_WE` | 281 | -0.024 | 1.000 | -0.049 | 1.000 |  |
| S8 | LONG | `session_EU` | 114 | +0.018 | 1.000 | -0.043 | 1.000 |  |
| S5 | LONG | `session_US` | 281 | +0.017 | 1.000 | -0.019 | 1.000 |  |
| S8 | LONG | `session_US` | 114 | -0.013 | 1.000 | -0.114 | 1.000 |  |
| S5 | LONG | `conf_partial` | 281 | -0.001 | 1.000 | +0.016 | 1.000 |  |
| S5 | SHORT | `session_EU` | 169 | +0.001 | 1.000 | -0.006 | 1.000 |  |
| S5 | SHORT | `session_Asia` | 169 | +0.000 | 1.000 | +0.025 | 1.000 |  |

## Results — 12m window (replication check)

Same analysis on 12m. If a 28m candidate truly carries signal, the same direction (sign of ρ) should show up here even if n is smaller and significance is harder.

| Strat | Dir | Feature | n | ρ_net | p_bonf_net | ρ_pnl | p_bonf_pnl | sig |
|---|---|---|---|---|---|---|---|---|
| S8 | LONG | `session_EU` | 54 | -0.195 | 1.000 | -0.190 | 1.000 |  |
| S8 | LONG | `session_Asia` | 54 | +0.179 | 1.000 | +0.273 | 1.000 |  |
| S5 | SHORT | `conf_partial` | 73 | +0.164 | 1.000 | +0.138 | 1.000 |  |
| S10 | SHORT | `conf_partial` | 163 | +0.127 | 1.000 | +0.085 | 1.000 |  |
| S8 | LONG | `session_WE` | 54 | +0.126 | 1.000 | +0.074 | 1.000 |  |
| S5 | LONG | `session_Asia` | 126 | -0.098 | 1.000 | +0.036 | 1.000 |  |
| S10 | SHORT | `session_WE` | 163 | +0.096 | 1.000 | +0.107 | 1.000 |  |
| S8 | LONG | `session_US` | 54 | -0.095 | 1.000 | -0.133 | 1.000 |  |
| S5 | LONG | `session_EU` | 126 | +0.095 | 1.000 | +0.076 | 1.000 |  |
| S10 | SHORT | `session_US` | 163 | -0.078 | 1.000 | -0.043 | 1.000 |  |
| S5 | LONG | `conf_partial` | 126 | -0.076 | 1.000 | -0.031 | 1.000 |  |
| S8 | LONG | `conf_partial` | 54 | -0.060 | 1.000 | -0.078 | 1.000 |  |
| S10 | SHORT | `session_EU` | 163 | -0.058 | 1.000 | -0.063 | 1.000 |  |
| S5 | LONG | `session_WE` | 126 | +0.042 | 1.000 | +0.001 | 1.000 |  |
| S5 | SHORT | `session_EU` | 73 | -0.040 | 1.000 | +0.011 | 1.000 |  |
| S5 | SHORT | `session_Asia` | 73 | +0.035 | 1.000 | +0.043 | 1.000 |  |
| S5 | LONG | `session_US` | 126 | -0.031 | 1.000 | -0.097 | 1.000 |  |
| S10 | SHORT | `session_Asia` | 163 | +0.030 | 1.000 | -0.010 | 1.000 |  |
| S5 | SHORT | `session_WE` | 73 | +0.015 | 1.000 | +0.116 | 1.000 |  |
| S5 | SHORT | `session_US` | 73 | -0.013 | 1.000 | -0.134 | 1.000 |  |

## Candidates


### S1 LONG × `session_Asia`

- n (28m) = 73
- ρ_net = +0.282, p_bonf = 0.3000 (✗)
- ρ_pnl = +0.393, p_bonf = 0.0000 (✓)
- 12m replication: cell absent (n<MIN_N on 12m)

| Bucket | n | mean_net (bps) | mean_pnl ($) | total_pnl ($) | WR |
|---|---|---|---|---|---|
| Asia ★ | 16 | +889.8 | +11182 | +178,907 | 75% |
| EU | 18 | -10.9 | -172 | -3,088 | 33% |
| US | 19 | -19.6 | -420 | -7,985 | 37% |
| WE | 20 | +151.9 | -30 | -602 | 55% |
- ⚠ **Significance asymmetry**: `pnl_usdt` Bonferroni-significant, `net_bps` not. The per-trade bps direction matches (same sign, similar magnitude on ρ_net), but the null-shuffle variance is larger on `net_bps` so the p-value doesn't clear Bonferroni. Two possible explanations: (1) genuine feature→edge effect that's underpowered at n=73, or (2) PnL ρ is inflated by the v11.10.0 size modulator co-amplifying `btc_z` and feature value (likely for S1, which fires *only* when btc30 > +2000 bps and is itself S1-α=+0.5-amplified). Disambiguating requires rerunning the EDA with `apply_adaptive_modulator=False`.

**Suggested next step (conditional)**: open `backtests/backtest_feature_modulator.py` and walk-forward-sweep `size *= 1 + α × normalize(feature)` on the surviving (strat, dir, feature) triples. Match the v11.10.0 pattern: per-α grid, strict 4/4 + ΔDD avg ≤ +1pp. **Before doing so**, address the asymmetry warnings above — if all candidates are pnl-only, redo the EDA on `net_bps` with size held constant (disable adaptive_modulator) to isolate per-trade edge from regime-size confounding.

## Caveats

- **Partial confluence**: the live `entry_confluence` has 5 components (drawdown, vol_z, ret_24h, n_stress_global, oi_delta_1h). The backtest can only reconstruct the first 4; the OI component (1h delta) requires live OI snapshots not available in the historical 4h candle data. The partial version may under-detect signal vs the live version.
- **No out-of-sample split**: this is a pure association test on in-sample data. A candidate would still need a walk-forward train/test before any deployment claim.
- **EDA, not a ship signal**: zero candidates here means *no further work on these two features*. It does **not** mean the bot's other observation features (OI delta, crowding, full confluence) carry no signal — they were out of scope.
- **Bonferroni is conservative**: with ~50-60 tests and per-test α=0.05, the per-test threshold drops to ~0.00083. Real but small effects (|ρ|≈0.1) would be missed. The effect-floor at |ρ|≥0.15 is independently restrictive — anything weaker is too small to justify a modulator anyway.
- **Slot effect not modeled**: the analysis is per-trade, not per-portfolio. Even a real per-trade ρ wouldn't automatically translate to a profitable sizing rule because the modulated size frees/consumes slot capacity. The intended next step (`backtest_feature_modulator.py`) is where slot effects are tested, not here.

## Reproducibility

```bash
cd /home/crypto
.venv/bin/python3 -m backtests.feature_modulator_eda
```

Dataset persisted at `backtests/feature_modulator_dataset.json`.
