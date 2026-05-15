# S5 LONG "dead trade walking" — T+8h checkpoint, walk-forward 4/4 strict

_Generated 2026-05-15. Inputs: `backtests/mid_trade_profiling_eda.md`._
_Code: `backtests/backtest_s5_dead_t8h.py`. Artifacts: `backtests/s5_dead_t8h_artifacts.json`._

---

## TL;DR

Both candidate rules **FAIL** strict 4/4 walk-forward.

| Variant | Verdict | 4/4 PnL? | ΔDD avg | Recommendation |
|---|---|---|---|---|
| **Strong** (mfe<50 AND pain≥50) | **RED** | 1/4 (28m only) | +0.51pp | **Classer** |
| **Triple_mid** (mfe<300 AND pain>60 AND sd<−500) | **RED** | 1/4 (28m only) | +0.08pp | **Classer** |

The 28m wins are large for both variants (+7 k pp strong, +93 k pp triple_mid) but every other window is **negative** (12m / 6m / 3m). The "dead trade walking" signature found in-sample on 28m (null-shuffle z=−6.41 in the EDA) **does not generalize** to shorter windows — the rule overfits to the long history.

**No ship.** S5 LONG remains untreated by an in-life exit. Update BACKLOG with the negative result.

---

## Methodology

### Hook implementation

Source: `backtests/backtest_s5_dead_t8h.py`. The hook plugs into `backtest_rolling.run_window`'s existing `inlife_exit_extra` callback (extended in this commit to surface `trade_id`, `time_in_pain_pct`, and `sector_div_delta` in the per-position snapshot).

Per-position state is closure-local, keyed by `trade_id`:
- The hook fires on the **first** snapshot where `hold_h ≥ 8.0` (first 4h candle past T+8h).
- Whether it fires or not, `trade_id` is marked `evaluated` — the position is never re-checked.
- S5 LONG filter: `strat == "S5" AND dir == 1`. Everything else short-circuits to `None`.
- Pain counter (`pos["pain_candles"]`) was previously gated to the mid-trade dump path; now also runs when `inlife_exit_extra` is active.

### Parity check

Hook installed but always returns `None`. Result must match baseline bit-for-bit on every window.

| Window | Baseline n / PnL% / DD% | Parity n / PnL% / DD% | Match |
|---|---|---|---|
| 28m | 1108 / +227 769.63% / −70.77% | 1108 / +227 769.63% / −70.77% | ✓ |
| 12m | 462 / +8 231.29% / −41.41% | 462 / +8 231.29% / −41.41% | ✓ |
| 6m | 231 / +1 057.40% / −39.10% | 231 / +1 057.40% / −39.10% | ✓ |
| 3m | 135 / +207.39% / −17.45% | 135 / +207.39% / −17.45% | ✓ |

**Parity OK on all 4 windows.** The hook plumbing is non-invasive.

### Strict acceptance criteria

- ΔPnL > 0 on EACH of 4 windows
- avg ΔDD ≤ +2pp
- 4/4 → GREEN ; 3/4 → YELLOW ; ≤2/4 → RED

All runs use `apply_adaptive_modulator=True` (canonical v11.10.0 prod config). `DEAD_TIMEOUT_*` read live from `analysis/bot/config.py` (currently v12.5.0 tightened, MAE floor −500).

---

## Baseline 4 windows

Matches the v12.5.30 baseline reference from `inlife_exit_results.md`:

| Window | PnL % | Max DD % | Trades | S5 LONG | Avg WR |
|---|---|---|---|---|---|
| 28m | +227 769.63 | −70.77 | 1108 | 284 | 52.3 |
| 12m | +8 231.29 | −41.41 | 462 | 126 | 55.2 |
| 6m | +1 057.40 | −39.10 | 231 | 64 | 54.1 |
| 3m | +207.39 | −17.45 | 135 | 42 | 53.3 |

Note: the previous run reported 1108 trades on 28m here vs the EDA's 1105/281 prior. Within engine-version drift; the v12.5.30 in-life trail is included in current `EARLY_EXIT` defaults.

---

## Variant Strong — `mfe<50 AND pain≥50`

**Fires**: 122 total / 458 S5 LONG positions evaluated at T+8h (26.6%).

| Window | Base PnL% | Var PnL% | ΔPnL | Base DD% | Var DD% | ΔDD | n_cut | n_S5L | Total trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +227 769.63 | +234 725.18 | **+6 955.54** | −70.77 | −64.20 | +6.57 | 77 | 301 | 1125 |
| 12m | +8 231.29 | +6 046.48 | **−2 184.81** | −41.41 | −41.41 | +0.00 | 30 | 135 | 470 |
| 6m | +1 057.40 | +986.42 | **−70.99** | −39.10 | −39.10 | −0.00 | 8 | 65 | 232 |
| 3m | +207.39 | +182.65 | **−24.74** | −17.45 | −21.98 | −4.54 | 7 | 44 | 136 |

**Exit reason distribution (28m)**: timeout=802, stop=129, `s5_dead_t8h=77`, dead_timeout=66, s10_trailing=41, s9_early=8, mtm_final=2. The 77 cuts replace ~26 baseline `dead_timeout` exits and reroute some baseline `stop` outcomes (catastrophe stop frequency drops from 141 → 129) — the rule is firing on the right cohort.

**Verdict**: **RED** (1/4 pass). avg ΔDD +0.51pp (under threshold), but only 28m has positive ΔPnL. 3m's −4.54pp ΔDD is the surprise — cutting 7/44 positions on a 3m window means each unlucky cut materially changes the compounding base.

---

## Variant Triple_mid — `mfe<300 AND pain>60 AND sector_div_delta<−500`

**Fires**: 44 total / 435 S5 LONG positions evaluated at T+8h (10.1%).

| Window | Base PnL% | Var PnL% | ΔPnL | Base DD% | Var DD% | ΔDD | n_cut | n_S5L | Total trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 28m | +227 769.63 | +321 308.81 | **+93 539.17** | −70.77 | −70.13 | +0.64 | 29 | 288 | 1114 |
| 12m | +8 231.29 | +7 971.20 | **−260.08** | −41.41 | −41.41 | +0.00 | 9 | 129 | 464 |
| 6m | +1 057.40 | +986.95 | **−70.46** | −39.10 | −39.10 | −0.00 | 4 | 67 | 233 |
| 3m | +207.39 | +204.80 | **−2.59** | −17.45 | −17.78 | −0.32 | 2 | 42 | 135 |

**Exit reason distribution (28m)**: timeout=812, stop=134, dead_timeout=85, s10_trailing=42, `s5_dead_t8h=29`, s9_early=9, mtm_final=3. Cleaner-targeted than `strong` — same effect on the 28m compounding curve, much fewer cuts.

**Verdict**: **RED** (1/4 pass). avg ΔDD +0.08pp (well under threshold), and the 12m/6m/3m misses are tiny in absolute pp terms — but the strict gate is strict. The 28m result is impressive (+93 k pp ≈ +41% on baseline) and isolates a real edge on long history, but the small-window data refuses to confirm it.

---

## Per-variant verdict

| Test | Strong | Triple_mid |
|---|---|---|
| 4/4 PnL strict? | ✗ (1/4) | ✗ (1/4) |
| avg ΔDD ≤ +2pp? | ✓ (+0.51pp) | ✓ (+0.08pp) |
| 28m ΔPnL | +6 956pp (+3% on baseline) | +93 539pp (+41% on baseline) |
| Smallest-window ΔPnL | −24.74pp on 3m | −2.59pp on 3m |
| Verdict | **RED** | **RED** |

Neither variant ships.

---

## Strong vs Triple_mid — side-by-side

| Metric | Strong | Triple_mid | Winner |
|---|---|---|---|
| 28m ΔPnL | +6 956pp | +93 539pp | Triple_mid (×13.4) |
| 28m ΔDD | +6.57pp | +0.64pp | Triple_mid (gentler) |
| 12m ΔPnL | −2 185pp | −260pp | Triple_mid (smaller loss) |
| 6m ΔPnL | −71pp | −70pp | ~tied |
| 3m ΔPnL | −24.74pp | −2.59pp | Triple_mid (smaller loss) |
| 3m ΔDD | −4.54pp | −0.32pp | Triple_mid (gentler) |
| Cuts on 28m / S5L | 77 / 301 | 29 / 288 | n/a (different policies) |
| %cut_helped (28m, exit_dist proxy) | s5_dead_t8h replaces some dead_timeout+stop+timeout cohort | s5_dead_t8h displaces dead_timeout cohort | Triple_mid more surgical |

**Triple_mid dominates Strong on every dimension.** Both still fail the strict gate, but Triple_mid is the cleaner candidate if a future revision relaxes the 4/4 strict requirement (it doesn't here).

The 28m S5 strat PnL breakdown is telling:
- Baseline S5: $746 709 (28m)
- Strong S5: $677 663 (**−$69 k**) — Strong cuts actively hurt S5 itself
- Triple_mid S5: $1 080 023 (**+$333 k**) — Triple_mid cuts boost S5 itself

The reason Triple_mid's TOTAL 28m PnL (+93k pp) far exceeds its S5 sub-PnL gain (+33k pp on $1k start) is compounding: cutting losing capital earlier inflates the base for every subsequent trade across all strats. That same compounding mechanism reverses on shorter windows when the cuts cut a future winner.

---

## Why the in-sample edge doesn't generalize

The mid-trade EDA found 13.9% WR on 72/281 cuts at T+8h on 28m with +108 bps avg savings (null-shuffle z=−6.41). That's a real, large in-sample edge.

What the walk-forward shows:

1. **The edge is concentrated in 28m vs 12m**: most of the dead-walking cohort sits in the 28m → 12m bracket (16 of 28 months). Once we restrict to the last 12 months, the cohort shrinks to 30 cuts (strong) or 9 cuts (triple_mid) and the asymmetric savings vanish.
2. **Catastrophe-stop interaction is benign but ineffective on small windows**: of the 7/44 S5L positions cut on 3m by Strong, several were on track to recover post-T+8h (Strong is too aggressive).
3. **Compounding dominates on long windows**: 28m's ×3 000 compounding lever means even a 5% efficiency gain on losers translates to huge ΔPnL. On 3m (×3 compounding), the same rule loses to single-trade luck.
4. **S5 LONG winners are bimodal** (confirmed by Family B from `inlife_exit_results.md`): cutting at T+8h truncates the long tail of late recovers — exactly what destroyed S5 in Family A/B/C of the prior in-life research.

This is consistent with the prior conclusion: **S5 has no clean trailable structure**. The EDA found a statistically real "dead at T+8h" cohort but it's not separable from late-mean-reverter S5 LONGs ex ante.

---

## Caveats

1. **Single-pass walk-forward.** No fold-cross, no IS/OOS split, no rolling-origin replication. Standard `inlife_exit` methodology — sufficient for negative result.
2. **No null-shuffle on the equity curve.** Only relevant if a variant passed 4/4. Both failed, so no follow-up needed.
3. **Interaction with existing `s8_inlife` (v12.5.30):** the two rules target different strategies (S5 vs S8) so no direct conflict. Verified by exit_dist — `s5_dead_t8h` and `s8_inlife` are independent codes.
4. **Catastrophe-stop ordering verified**: hook fires AFTER `exit_reason = "stop"` (line 644 in `backtest_rolling.py`). A position that hit −1250 bps before T+8h is closed by `stop` first — no double-cut.
5. **Pain counter and sector_div tracking added in this commit** are activated only when `inlife_exit_extra is not None`. Baseline runs (hook=None) still produce bit-identical output. Verified by the parity check.
6. **Adaptive modulator active in every run.** Measured edge is *incremental* over the v11.10.0 modulator. The modulator's S5 LONG α defaults to +0.5 — in bear regimes (where dead-walking is likely amplified), it shrinks position size, partially mitigating the dead-walking cost before our rule fires.

---

## Reproduction

```bash
cd /home/crypto
.venv/bin/python3 -m backtests.backtest_s5_dead_t8h
# ~25s end-to-end, 12 run_window calls (4 baseline + 4 parity + 4×2 variants)
# Artifacts: backtests/s5_dead_t8h_artifacts.json
```

Commits: `4a899af` (hook) → `ef36b2c` (results) → this report.

---

## Decision

**Both variants RED. Classer. Update `BACKLOG.md` with the negative result.**

The "dead at T+8h" signature from the EDA is real on 28m but does not survive 4/4 walk-forward. S5 LONG's bimodal winner distribution defeats any fixed-threshold in-life trail (consistent with the inlife_exit_results.md Family A/B/C verdicts).

**No version bump. No live config change. No bot restart.**

If the user wants to revisit:
- Try a **regime-conditioned** version of triple_mid (bucket by btc_z like the v12.5.30 S8 trail). The 28m success may be concentrated in one regime bucket.
- Try **T+12h** instead of T+8h (the EDA showed savings degrades at T+12h but the in-life cohort might be more stable).
- Run a **null-shuffle on the equity curve** to confirm the 28m ΔPnL is signal not noise — the in-sample WR null-shuffle was z=−6.41 but that's a different question from "does the equity-curve gain replicate".
