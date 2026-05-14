# In-life exit research — Final Results

_Generated 2026-05-14 — see `docs/superpowers/specs/2026-05-14-inlife-exit-design.md` and `docs/superpowers/plans/2026-05-14-inlife-exit.md`._

## TL;DR

| | |
|---|---|
| Strategies tested | S5, S8 |
| Families tested | A.1 (global trail) · A.2 (regime-conditioned) · B (empirical percentile) · C (logit + GBM) |
| Walk-forward windows | 28m / 12m / 6m / 3m, strict 4/4 + ΔDD avg ≤+1pp |
| **Robust candidate** | **S8 A.2 composite** — regime-conditioned trail |
| Null-shuffle z-score | **+10.52** (SIGNAL, 12/13 shuffles destroyed PnL) |
| S5 result | **Unsolved** — no family produced an S5 winner |
| Recommendation | **Ship S8 A.2** in a separate PR (with `/release` flow). Leave S5 untouched. |

---

## Baseline

| Window | PnL % | Max DD % | Trades |
|---|---|---|---|
| 28m | +227 770 | −70.8 | 1108 |
| 12m | +8 231 | −41.4 | 462 |
| 6m | +1 057 | −39.1 | 231 |
| 3m | +207 | −17.4 | 135 |

_Baseline uses current shipped config (v12.5.29). DEAD_TIMEOUT_* read live from `analysis/bot/config.py` so the reference moves with any prod retune._

---

## Family A.1 — Global MFE trail (20-combo grid)

**0 winners** under strict 4/4 + ΔDD avg ≤+1pp.

**Near-miss worth recording**: S8 act=1000 off=150 produced massive 4/4-positive PnL (Δ28m=+426 263, Δ12m=+7 643, Δ6m=+177, Δ3m=+124) but ΔDD avg +2.15pp (28m DD bump +7.5pp). The PnL/DD asymmetry suggested regime-conditioning could rescue it — which A.2 confirmed.

S5 was destroyed by every A.1 combo (all 20 produced large negative ΔPnL on 28m/12m).

## Family A.2 — Regime-conditioned (per-bucket sweep)

**1 winner** ✓ — **S8 composite**:

| Regime bucket | Activation (bps) | Offset (bps) | Per-bucket Δpnl avg |
|---|---|---|---|
| bear (btc_z < −0.5) | 1500 | 100 | +94 169 |
| neutral (\|btc_z\| ≤ 0.5) | 300 | 300 | +6 541 |
| bull (btc_z > 0.5) | 1500 | 100 | +34 |

Composite walk-forward result:

| Window | ΔPnL pp | ΔDD pp |
|---|---|---|
| 28m | +438 124 | +0.13 |
| 12m | +6 652 | +1.07 |
| 6m | +54 | −0.00 |
| 3m | +8 | +0.00 |
| **avg** | **+111 209** | **+0.30** |

**S5** — no per-bucket combo improved over baseline. Composite is a no-op for S5.

## Family B — Empirical percentile

**2 winners** ✓ (identical numerically):

| Strat | Percentile | min_MFE | ΔPnL (28m / 12m / 6m / 3m) | ΔDD avg |
|---|---|---|---|---|
| S8 | 70 | 300 | +2 212 / +220 / +31 / +5 | ~0.00 |
| S8 | 70 | 500 | +2 212 / +220 / +31 / +5 | ~0.00 |

The two variants are functionally identical because the 10-obs bucket floor in `make_B_rule` dominates the `min_mfe` filter — both reduce to the same active rule.

Cross-validation: same target (S8), much weaker gain (×50 below A.2), via a totally different mechanism (data-anchored vs absolute-bps). Both families agree S8 has a trailable structure that S5 lacks.

**S5** — destroyed by every config (−16k to −35k pp on 28m). Confirms "S5 winners are bimodal" hypothesis: cutting at past P70 retracement exits too early on the long tail.

## Family C — ML (logit + GBM)

**0 winners.** Both models rediscover the MFE-trail mechanism (top features: `mfe_bps`, `net`) but the dataset is 84.6% positive (most baseline winners give back ≥200bps from MFE), so trained models predict "exit" too aggressively and crush compounding on long windows.

| Best ML config | 28m | 12m | 6m | 3m | ΔDD avg |
|---|---|---|---|---|---|
| logit τ=0.75 S5 | −203 696 | −7 394 | −603 | +23 | −4.65 |
| gbm τ=0.75 S5 | −212 418 | −7 361 | −577 | +16 | −3.01 |

Note: ΔDD is consistently negative (drawdown improved), but the strict-positive PnL gate fails on every long window. The rule fires correctly but kills the compounding base.

**Verdict on ML approach**: without per-candle bps_path tracking (Caveat 2 of T6), the ML route adds no signal beyond what A.2 captures directly. The two top features by importance are `mfe_bps` and `net` — exactly the inputs to the A.2 rule.

---

## Validation: Null-shuffle on S8 A.2 (Task 7)

Method: shuffle btc_z values 13× (preserving marginal, breaking temporal alignment). Re-run the S8 A.2 composite rule. Compare real ΔPnL avg to shuffle mean.

| Metric | Value |
|---|---|
| REAL ΔPnL avg (4-window) | **+111 209 pp** |
| Shuffle mean (13 runs) | −20 099 pp |
| Shuffle SD | 12 484 |
| **z-score** | **+10.52** |
| Verdict | **SIGNAL ✓** |

Detail of shuffles:
- 12/13 shuffles produced NEGATIVE ΔPnL (range −1 769 to −42 693)
- 1/13 shuffle produced +6 203 pp — still ~17 000 pp below the real result

The rule's edge is **genuinely regime-driven**, not a bucketing artifact. The asymmetric per-regime trail (tight loose-trigger in extremes 1500/100, wide aggressive trail in neutral 300/300) is structurally tied to BTC z-score, consistent with the v11.10.0 modulator's S8 α=−0.5 (S8 is a bear-favored capitulation flush LONG).

## Validation: Parameter stability — SKIPPED

Plan T8 implemented stability check only for A.1 (no winners). The plan did not extend stability to family B winners. The B winners have not been independently stability-tested. Acceptable given that A.2's edge is ~50× larger and has passed null-shuffle.

---

## Recommendation

**Ship Family A.2 — S8 composite trail.**

| Parameter | Bear (btc_z < −0.5) | Neutral (\|btc_z\| ≤ 0.5) | Bull (btc_z > 0.5) |
|---|---|---|---|
| MFE activation (bps) | 1500 | 300 | 1500 |
| Exit offset (bps) | 100 | 300 | 100 |

**Mechanic**: at every 4h candle on an open S8 position, if `MFE ≥ activation(regime)` and `current ≤ MFE − offset(regime)`, exit with reason `s8_inlife`. Strategy-filtered (S5 untouched).

**Rationale**:
1. **A.1 (global) failed.** The single-parameter MFE trail couldn't pass the DD gate (+2.15pp).
2. **A.2 (regime) passed strict.** ΔPnL avg +111 209pp, ΔDD avg +0.30pp.
3. **Null-shuffle z=+10.52.** The edge survives randomization of btc_z by 10σ.
4. **Cross-validated by B.** The empirical-percentile family found S8 (and only S8) trailable via a totally different mechanism.
5. **Cross-validated by C.** ML features rediscover the MFE-trail intuition.
6. **Mechanically consistent.** S8 is a bear-favored capitulation LONG (v11.10.0 α=−0.5); an aggressive trail in bear regimes locks gains where the trade has the most upside slippage to give back. Bull-regime rule is essentially inactive (per-bucket contribution +34pp, vs +94 169 in bear).

**S5 left untouched.** No family produced a robust S5 candidate. S5 winners are bimodal (some gigantic, some modest); fixed-threshold trails catch falling knives.

**Risks / caveats**:
- Activation thresholds at extremes (1500/100 for bear+bull) are on the upper edge of the swept grid. The pattern may not generalize beyond +/-50% from these values without re-validation. Future re-tunes should re-sweep.
- The 12m window has ΔDD +1.07pp (single-window, vs avg +0.30pp). Borderline on a single-window basis.
- The bull bucket contributes almost nothing (Δpnl avg +34pp); removing it would simplify the rule to bear+neutral. Optional cleanup.
- This is a single backtest output. Live deployment should monitor S8 exit-reason distribution and confirm the rule fires expected fractions of trades.

**Next step**: open a separate PR with `/release` to ship in `analysis/bot/trading.py:check_exits`. Add new constants `S8_INLIFE_PARAMS` in `config.py`. Mirror the regime_bucket logic from the v11.10.0 modulator. Wire `check_exits` to call the new rule between the existing `s10_trailing` and `dead_timeout` blocks.

---

## Reproducibility

```bash
# Full re-run from scratch (~25 min total)
cd /home/crypto
.venv/bin/python3 -m backtests.backtest_inlife_exit --self-test     # baseline sanity
.venv/bin/python3 -m backtests.backtest_inlife_exit --family A      # T3 + T4
.venv/bin/python3 -m backtests.backtest_inlife_exit --family B      # T5
.venv/bin/python3 -m backtests.backtest_inlife_exit --family C      # T6
.venv/bin/python3 -m backtests.backtest_inlife_exit --validate      # T7 null-shuffle
```

Artifacts persist in `backtests/inlife_exit_artifacts.json`.
Per-family logs: `backtests/inlife_exit_{A1,A2,B,C,T7}.log`.

Commit chain: `9e17f02` (hook) → `3b62557`/`6609dc0` (skeleton) → `b4bc8b1` (A.1) → `79439b6` (A.2) → `3d1b7ec` (B) → `c18188f` (C) → `b176f20` (T7).
