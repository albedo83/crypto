# S5 LONG mid-trade exit conditioned on disp_7d — walk-forward

_Generated 2026-05-28._ Source: `backtests/eda_s5_unexplored*.py`, `backtests/backtest_s5_disp_inlife.py`.

## TL;DR

**Verdict : RED on all variants. Classer.** The EDA premise gate (z=+3.43) was a *mean-savings* edge but the underlying S5 LONG distribution is bimodal with long-tail winners. Cutting at T+8h destroys geometric compounding even when arithmetic mean improves.

| Variant | Rule | EDA z | n EDA | WF 4/4 | ΔPnL 28m | ΔDD avg |
|---|---|---|---|---|---|---|
| R1_disp_strong  | mfe<50 & pain≥50 & disp_7d≥700 @ T+8h | +3.43 | 38 | **2/4** | −72 815 pp | −0.11 pp |
| R2_disp_triple  | mfe<300 & pain≥60 & sd_delta<−500 & disp_7d≥700 @ T+8h∪T+12h | +2.76 | 22 | **1/4** | −22 092 pp | −0.96 pp |
| R3_disp_strict  | R1 + mae≤−500 (super-strict) | n/a | n/a | **2/4** | −85 514 pp | −1.27 pp |

Strict gate: 4/4 ΔPnL > 0 AND ΔDD avg ≤ +1pp. All three RED.

## Why the EDA premise gate misfired

The EDA measured `savings_bps = mean(cur_ur) - mean(final_net)` per cut. For R1: n=38, savings=+196 bps → arithmetic edge ≈ +7 448 bps cumulative.

But the WF 28m baseline PnL is +510 039 % (1127 trades, geometric compounding × 28 months). Cutting 5/38 long-tail winners (the 13.2% in-sample WR) at MAE destroys their geometric contribution. One +5000 bps S5 LONG winner in 2024 that compounds through to 2026 is worth thousands of % at the end — kill that one and the savings on the 33 losers don't pay it back.

This is precisely the bimodal-S5 problem documented in `backtests/inlife_exit_results.md` (all 4 in-life exit families A.1/A.2/B/C failed on S5) and `backtests/s5_dead_t8h_walkforward.md` (`strong` and `triple_mid` both RED on T+8h).

The dispersion gate (disp_7d ≥ 700) was supposed to filter out exactly the regime where the bimodal long-tail doesn't manifest — but empirically it doesn't. Even at disp_7d ≥ 700, ~10% of S5 LONGs with mfe<50 & pain≥50 at T+8h are long-tail winners.

## Symmetric finding: every variant reduces DD

| Variant | ΔDD 28m | ΔDD 12m | ΔDD 6m | ΔDD 3m | avg |
|---|---|---|---|---|---|
| R1_disp_strong | +3.84 | −4.28 | −0.00 | +0.00 | **−0.11** |
| R2_disp_triple | −0.64 | −3.21 | +0.00 | +0.00 | **−0.96** |
| R3_disp_strict | −1.76 | −3.33 | −0.00 | +0.00 | **−1.27** |

This is the canonical "trade PnL for DD" pattern. Useful as a sizing/leverage knob in a regime-conditioned framework, but not as a hard CUT.

## Recommendations

1. **Classer R1/R2/R3 as cut rules.** Walk-forward strict 4/4 fails on all three for the same fundamental reason (S5 LONG bimodality).
2. **Memo for future R&D**: the EDA premise gate based on mean savings is necessary but not sufficient for walk-forward acceptance. Need also a geometric/compounding sanity check before launching walk-forward (e.g. require positive ΔPnL_compounded on a quick scaled-down baseline).
3. **DD-sensitive variant** could ship as a "sizing haircut" rather than a cut, but that's its own R&D round and falls under [project_dd_reduction] memory.
4. **Genuinely unexplored gaps still on the table**:
   - In-trade BTC price delta (BTC moved adversely ≥X bps during the hold) — distinct from `btc_z` at entry / cut.
   - In-trade OI delta on the position's token — current snapshot lacks this.
   - In-trade funding flip.

These would need new mid-trade snapshot features. Defer until 50+ trades have been logged with these fields live.
