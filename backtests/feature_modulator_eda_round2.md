# Feature-modulator EDA — round 2

_Generated 2026-05-15 14:38 UTC_

Continuous-feature EDA + S1 LONG × Asia disambiguation. Round 1 (see `feature_modulator_eda.md`) tested only `conf_partial` + `session_*` and surfaced 1 borderline candidate (S1 LONG × Asia, ρ_pnl=+0.393 ✓ but ρ_net=+0.282 ✗).

## TL;DR

**0 strict candidates under modulator ON, 1 under modulator OFF**: S1 LONG × `entry_n_stress`. This is a reverse asymmetry from round 1: the per-trade ranking signal (ρ_net) is robust under BOTH configurations, but the v11.10.0 macro modulator's size variance muddies ρ_pnl under canonical config. When the modulator is held off (flat sizing), ρ_pnl also clears Bonferroni. **Interpretation**: real per-trade edges that the existing macro modulator partially masks at the PnL level. See *Modulator OFF cross-reference* and **Recommendation**.

**S1 LONG × Asia disambiguation**: **Underlying edge confirmed** — ρ_pnl barely changes when the macro modulator is disabled, so the round-1 S1 LONG × Asia signal is not a size-amplification artifact.

- Dataset: 28m=1105 trades (modulator ON, canonical config)
- Features: 10 continuous candle-based features captured at entry
- Effect floor: |ρ| ≥ 0.15
- Family-wise α = 0.05 (Bonferroni over actually-run tests)
- Strict candidate: passes BOTH `ρ_net` AND `ρ_pnl` Bonferroni
- Null shuffles per test: 200

## Disambiguation: S1 LONG × Asia (modulator ON vs OFF)

Round 1 found ρ_pnl=+0.393 (Bonferroni ✓) but ρ_net=+0.282 (Bonferroni ✗). Hypothesis: PnL was inflated by the v11.10.0 macro modulator amplifying size at high `btc_z`, which happens to coincide with Asia-session S1 fires. We rerun the 28m backtest with the modulator disabled. If ρ_pnl collapses to match ρ_net (~+0.28), it's a size artifact. If it stays at +0.39+, the edge is real.

| Modulator | n | n_Asia | ρ_net | p_null_net | ρ_pnl | p_null_pnl | Asia total_pnl ($) | non-Asia total_pnl ($) |
|---|---|---|---|---|---|---|---|---|
| ON | 73 | 16 | +0.282 | 0.0100 | +0.393 | 0.0000 | +178,907 | -11,675 |
| OFF | 73 | 16 | +0.282 | 0.0100 | +0.371 | 0.0050 | +5,503 | -926 |

**Δρ_pnl (ON − OFF) = +0.022**, **Δρ_net (ON − OFF) = +0.000**

**Verdict: underlying edge confirmed** — disabling the modulator changes ρ_pnl by less than 0.05, so the S1 LONG × Asia association is not driven by the modulator's size amplification. Note however that ρ_net (and therefore the *strict* Bonferroni joint filter) was already failing in round 1, and the n=73 (only 16 Asia trades) sample size is too small to confidently call this a deployable edge.

## Sample sizes (28m window, modulator ON)

| Strat | Dir | n |
|---|---|---|
| S1 | LONG | 73 |
| S1 | SHORT | 0 ⚠ low-n |
| S5 | LONG | 281 |
| S5 | SHORT | 169 |
| S8 | LONG | 114 |
| S8 | SHORT | 0 ⚠ low-n |
| S9 | LONG | 0 ⚠ low-n |
| S9 | SHORT | 86 |
| S10 | LONG | 0 ⚠ low-n |
| S10 | SHORT | 354 |

_Cells with n<30 skipped from testing._

## Full results — 28m window

Sorted by |ρ_net| descending. `cand` ✓ = passes BOTH Bonferroni filters (strict). `bh` ✓ = passes BH FDR on either outcome (less conservative).

| Strat | Dir | Feature | n | ρ_net | p_bonf_net | ρ_pnl | p_bonf_pnl | bh | cand |
|---|---|---|---|---|---|---|---|---|---|
| S1 | LONG | `entry_n_stress` | 73 | +0.380 | 0.000 | +0.385 | 0.300 | ✓ |  |
| S1 | LONG | `entry_lead` | 73 | -0.296 | 0.000 | -0.264 | 1.000 | ✓ |  |
| S1 | LONG | `entry_drawdown_abs` | 73 | +0.230 | 1.000 | +0.266 | 0.900 |  |  |
| S1 | LONG | `entry_range_pct` | 73 | +0.214 | 1.000 | +0.087 | 1.000 |  |  |
| S1 | LONG | `entry_clean` | 73 | +0.204 | 1.000 | +0.169 | 1.000 |  |  |
| S9 | SHORT | `entry_ret24h_abs` | 86 | -0.197 | 1.000 | +0.010 | 1.000 |  |  |
| S9 | SHORT | `entry_range_pct` | 86 | -0.161 | 1.000 | -0.087 | 1.000 |  |  |
| S1 | LONG | `entry_disp_7d` | 73 | -0.159 | 1.000 | -0.107 | 1.000 |  |  |
| S9 | SHORT | `entry_clean` | 86 | -0.140 | 1.000 | -0.096 | 1.000 |  |  |
| S9 | SHORT | `entry_shock` | 86 | -0.137 | 1.000 | +0.076 | 1.000 |  |  |
| S9 | SHORT | `entry_disp_7d` | 86 | -0.125 | 1.000 | -0.034 | 1.000 |  |  |
| S5 | SHORT | `entry_range_pct` | 169 | +0.116 | 1.000 | +0.096 | 1.000 |  |  |
| S5 | SHORT | `entry_lead` | 169 | +0.114 | 1.000 | -0.004 | 1.000 |  |  |
| S9 | SHORT | `entry_n_stress` | 86 | +0.111 | 1.000 | +0.161 | 1.000 |  |  |
| S5 | SHORT | `entry_drawdown_abs` | 169 | +0.107 | 1.000 | -0.006 | 1.000 |  |  |
| S8 | LONG | `entry_n_stress` | 114 | -0.105 | 1.000 | -0.022 | 1.000 |  |  |
| S8 | LONG | `entry_drawdown_abs` | 114 | +0.099 | 1.000 | +0.075 | 1.000 |  |  |
| S9 | SHORT | `entry_disp_24h` | 86 | -0.089 | 1.000 | -0.031 | 1.000 |  |  |
| S8 | LONG | `entry_clean` | 114 | -0.088 | 1.000 | -0.069 | 1.000 |  |  |
| S5 | SHORT | `entry_ret24h_abs` | 169 | +0.086 | 1.000 | +0.133 | 1.000 |  |  |
| S8 | LONG | `entry_disp_7d` | 114 | +0.085 | 1.000 | +0.091 | 1.000 |  |  |
| S1 | LONG | `entry_disp_24h` | 73 | -0.079 | 1.000 | -0.051 | 1.000 |  |  |
| S5 | LONG | `entry_disp_7d` | 281 | -0.072 | 1.000 | -0.031 | 1.000 |  |  |
| S5 | SHORT | `entry_n_stress` | 169 | +0.070 | 1.000 | -0.057 | 1.000 |  |  |
| S5 | LONG | `entry_n_stress` | 281 | +0.069 | 1.000 | +0.077 | 1.000 |  |  |
| S5 | LONG | `entry_range_pct` | 281 | -0.062 | 1.000 | -0.061 | 1.000 |  |  |
| S5 | SHORT | `entry_disp_7d` | 169 | -0.061 | 1.000 | +0.024 | 1.000 |  |  |
| S8 | LONG | `entry_ret24h_abs` | 114 | +0.059 | 1.000 | +0.059 | 1.000 |  |  |
| S10 | SHORT | `entry_vol_z` | 354 | +0.057 | 1.000 | +0.083 | 1.000 |  |  |
| S5 | LONG | `entry_disp_24h` | 281 | -0.054 | 1.000 | -0.028 | 1.000 |  |  |
| S9 | SHORT | `entry_vol_z` | 86 | -0.043 | 1.000 | -0.035 | 1.000 |  |  |
| S5 | LONG | `entry_clean` | 281 | -0.040 | 1.000 | -0.058 | 1.000 |  |  |
| S5 | SHORT | `entry_shock` | 169 | -0.039 | 1.000 | +0.028 | 1.000 |  |  |
| S1 | LONG | `entry_shock` | 73 | -0.038 | 1.000 | -0.122 | 1.000 |  |  |
| S10 | SHORT | `entry_disp_7d` | 354 | +0.037 | 1.000 | +0.021 | 1.000 |  |  |
| S8 | LONG | `entry_shock` | 114 | +0.037 | 1.000 | +0.046 | 1.000 |  |  |
| S10 | SHORT | `entry_drawdown_abs` | 354 | -0.036 | 1.000 | -0.045 | 1.000 |  |  |
| S10 | SHORT | `entry_disp_24h` | 354 | -0.035 | 1.000 | -0.036 | 1.000 |  |  |
| S8 | LONG | `entry_vol_z` | 114 | -0.031 | 1.000 | +0.011 | 1.000 |  |  |
| S5 | SHORT | `entry_disp_24h` | 169 | -0.030 | 1.000 | -0.034 | 1.000 |  |  |
| S5 | SHORT | `entry_clean` | 169 | -0.028 | 1.000 | -0.005 | 1.000 |  |  |
| S5 | LONG | `entry_shock` | 281 | -0.027 | 1.000 | -0.004 | 1.000 |  |  |
| S5 | LONG | `entry_ret24h_abs` | 281 | -0.027 | 1.000 | -0.018 | 1.000 |  |  |
| S1 | LONG | `entry_vol_z` | 73 | +0.027 | 1.000 | -0.072 | 1.000 |  |  |
| S10 | SHORT | `entry_lead` | 354 | -0.026 | 1.000 | -0.036 | 1.000 |  |  |
| S10 | SHORT | `entry_clean` | 354 | -0.025 | 1.000 | -0.053 | 1.000 |  |  |
| S5 | SHORT | `entry_vol_z` | 169 | +0.023 | 1.000 | +0.050 | 1.000 |  |  |
| S10 | SHORT | `entry_range_pct` | 354 | -0.022 | 1.000 | -0.005 | 1.000 |  |  |
| S8 | LONG | `entry_range_pct` | 114 | +0.020 | 1.000 | +0.071 | 1.000 |  |  |
| S9 | SHORT | `entry_drawdown_abs` | 86 | +0.015 | 1.000 | -0.008 | 1.000 |  |  |
| S10 | SHORT | `entry_n_stress` | 354 | +0.014 | 1.000 | -0.004 | 1.000 |  |  |
| S10 | SHORT | `entry_shock` | 354 | +0.014 | 1.000 | +0.012 | 1.000 |  |  |
| S10 | SHORT | `entry_ret24h_abs` | 354 | -0.010 | 1.000 | -0.015 | 1.000 |  |  |
| S5 | LONG | `entry_lead` | 281 | -0.010 | 1.000 | +0.027 | 1.000 |  |  |
| S1 | LONG | `entry_ret24h_abs` | 73 | -0.007 | 1.000 | -0.057 | 1.000 |  |  |
| S8 | LONG | `entry_disp_24h` | 114 | -0.007 | 1.000 | -0.099 | 1.000 |  |  |
| S5 | LONG | `entry_drawdown_abs` | 281 | -0.005 | 1.000 | +0.037 | 1.000 |  |  |
| S5 | LONG | `entry_vol_z` | 281 | -0.004 | 1.000 | +0.021 | 1.000 |  |  |
| S9 | SHORT | `entry_lead` | 86 | -0.002 | 1.000 | +0.170 | 1.000 |  |  |
| S8 | LONG | `entry_lead` | 114 | +0.001 | 1.000 | -0.004 | 1.000 |  |  |

## Candidates (strict: both Bonferroni filters)

**None.** No (strat, dir, feature) triple passed the joint `|ρ| ≥ 0.15` + `p_bonferroni < 0.05` filter on BOTH `net_bps` AND `pnl_usdt` on the 28m window. Round-1 lesson applied: requiring BOTH outcomes to clear Bonferroni filters out the PnL-only candidates that were inflated by the v11.10.0 size modulator.

## Modulator OFF cross-reference (rerun on flat sizing)

To cross-check the round-1 size-artifact hypothesis in a generalized way, the entire EDA was rerun on the 28m window with the v11.10.0 adaptive modulator disabled (`ADAPTIVE_ALPHA = {}` and `ADAPTIVE_ALPHA_DIR = {}`). Under flat sizing, ρ_pnl reflects only the per-trade bps edge (no size variance from the modulator).

Cells where |ρ_net| ≥ 0.15 in at least one run, sorted by max(|ρ_net|) across the two runs.

| Strat | Dir | Feature | n | ρ_net ON | ρ_net OFF | ρ_pnl ON | ρ_pnl OFF | p_bonf_pnl ON | p_bonf_pnl OFF | tag |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 | LONG | `entry_n_stress` | 73 | +0.380 | +0.380 | +0.385 | +0.365 | 0.300 | 0.000 | OFF-only (size noise hides edge) |
| S1 | LONG | `entry_lead` | 73 | -0.296 | -0.296 | -0.264 | -0.311 | 1.000 | 0.300 |  |
| S1 | LONG | `entry_drawdown_abs` | 73 | +0.230 | +0.230 | +0.266 | +0.284 | 0.900 | 0.900 |  |
| S1 | LONG | `entry_range_pct` | 73 | +0.214 | +0.214 | +0.087 | +0.113 | 1.000 | 1.000 |  |
| S1 | LONG | `entry_clean` | 73 | +0.204 | +0.204 | +0.169 | +0.177 | 1.000 | 1.000 |  |
| S9 | SHORT | `entry_ret24h_abs` | 86 | -0.197 | -0.197 | +0.010 | -0.049 | 1.000 | 1.000 |  |
| S9 | SHORT | `entry_range_pct` | 86 | -0.161 | -0.161 | -0.087 | -0.116 | 1.000 | 1.000 |  |
| S1 | LONG | `entry_disp_7d` | 73 | -0.159 | -0.159 | -0.107 | -0.112 | 1.000 | 1.000 |  |

### Tercile breakdown for OFF-only candidates

Buckets computed on the modulator-OFF dataset. Note: even with the macro modulator disabled, `strat_size()` still scales with `capital`, so `total_pnl` reflects compounding (drawdown periods shrink subsequent sizes). The pure per-trade edge is `mean_net (bps)`; `total_pnl` is shown for context but is dollar-weighted by historical sequence.

#### S1 LONG × `entry_n_stress` (OFF)

- n = 73
- ρ_net = +0.380, p_bonf = 0.0000 (✓)
- ρ_pnl = +0.365, p_bonf = 0.0000 (✓)

_Tercile bounds: low ≤ 0, high ≥ 0_

| Bucket | n | WR | mean_net (bps) | total_pnl ($) | mean_size ($) |
|---|---|---|---|---|---|
| low | 49 | 43% | -152.0 | +3,479 | 1,737 |
| high | 24 | 62% | +1006.3 | +1,098 | 440 |


## FDR secondary view (BH q=0.05)

Tests that pass Benjamini-Hochberg at q=0.05 (with the |ρ|≥0.15 effect floor) on at least one of `net_bps`/`pnl_usdt`. FDR is more permissive than Bonferroni — a hit here that fails Bonferroni is *observation-only*, not ship-ready.

| Strat | Dir | Feature | n | ρ_net | ρ_pnl | bh_net | bh_pnl | cand |
|---|---|---|---|---|---|---|---|---|
| S1 | LONG | `entry_lead` | 73 | -0.296 | -0.264 | ✓ |  |  |
| S1 | LONG | `entry_n_stress` | 73 | +0.380 | +0.385 | ✓ |  |  |

## Caveats

- **Sample sizes**: S1 SHORT, S8 SHORT, S9 LONG, S10 LONG all have n=0 on 28m. Those (strat, dir) combinations are untestable and silently skipped. S1 LONG is the smallest tested cell at n=73 — power is limited.
- **No 12m replication**: round 2 focused on extending feature coverage on 28m and the modulator-OFF rerun, since the round-1 12m view had no S1 LONG trades (n=0) and didn't help disambiguate the round-1 candidate. The 12m table is still derivable from the JSON dataset.
- **In-sample association**: any surviving candidate would still need a walk-forward 4/4 train/test sweep on `size *= 1 + α × normalize(feature)` before any deployment claim. EDA shows correlation, not causation, and does not account for the slot-effect interactions a sizing rule would induce.
- **Bonferroni is conservative**: with ~70 tests at α=0.05, the per-test threshold is ~0.0007. Effects with |ρ|≈0.10 would be missed. BH FDR is reported as a secondary view but the strict bar (BOTH net AND pnl Bonferroni) is what gates the Recommendation.
- **Disambiguation interpretation**: even if ρ_pnl barely changes ON vs OFF, the round-1 ρ_net was already non-significant (p_bonf≈0.30). The strict bar that round 2 enforces would have rejected it regardless of the modulator confound.

## Recommendation

**Pre-registered next step** for the surviving triple(s) — S1 LONG × `entry_n_stress` (OFF): open `backtests/backtest_feature_modulator.py` (does not exist yet — to be created) and walk-forward-sweep `size *= 1 + α × clip(zscore(feature))` per (strat, dir, feature). Match the v11.10.0 pattern: per-α grid {0.25, 0.5, 1.0}, strict 4/4 windows (28m/12m/6m/3m), ΔDD avg ≤ +1pp, IS/OOS sliding split, null-shuffle z>3. **Do not build it yet** — this is just the pre-registered protocol if the user decides to proceed.

**Important nuance**: the candidate(s) above pass the strict joint filter only under modulator OFF (flat sizing). The per-trade ranking signal (ρ_net) is robust in BOTH configurations, but the v11.10.0 macro modulator adds enough size variance to muddy ρ_pnl under canonical (ON) config. Operationally, this means a feature-based modulator can extract edge that the macro modulator currently dilutes, but **the two modulators interact** — any walk-forward must either (a) test the feature modulator on top of the macro one (canonical comparison), or (b) evaluate replacing/restricting the macro modulator on the affected strategy. Recommended: test (a) first since it doesn't disturb the currently-shipped v11.10.0 behavior on other strategies.

## Reproducibility

```bash
cd /home/crypto
.venv/bin/python3 -m backtests.feature_modulator_eda_round2
```

Dataset persisted at `feature_modulator_dataset_r2.json`.
