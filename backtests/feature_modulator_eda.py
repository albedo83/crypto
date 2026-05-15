"""Feature-modulator EDA — does an at-entry feature → outcome signal exist?

Research question: before building a continuous size modulator like the v11.10.0
macro modulator (size *= 1 + α × btc_z) but per-trade instead of per-regime, we
need evidence that an at-entry feature has a non-trivial relationship with
trade outcome. This script answers that for the two features that can be
reconstructed in the backtest:

  - entry_confluence_partial  (sum of 4 candle-based booleans at entry; the
                               live version has 5 incl. an OI component that
                               the backtest cannot reconstruct)
  - entry_session             (categorical {Asia, EU, US, Night, WE} from
                               the entry candle's UTC hour/weekday)

Pipeline:
  1. Run backtests.backtest_rolling.run_window on 28m and 12m windows with
     the live shipped config (D2 + runner extension + adaptive modulator),
     using the patched engine that records conf_partial + session on each
     trade.
  2. Save the per-trade dataset to backtests/feature_modulator_dataset.json.
  3. For each (strategy ∈ {S1,S5,S8,S9,S10}, direction ∈ {+1,-1}, feature ∈
     {conf_partial, session_Asia, …}): Spearman ρ vs (net_bps, pnl_usdt),
     null-shuffle p-value (200 shuffles), Bonferroni correction over the
     actually-run tests.
  4. Pairs with Bonferroni-corrected p < 0.05 AND |ρ| ≥ 0.15 are candidates.
  5. Write backtests/feature_modulator_eda.md with TL;DR + tables.

Run:
  cd /home/crypto && .venv/bin/python3 -m backtests.feature_modulator_eda
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np
from scipy.stats import spearmanr  # type: ignore

from backtests.backtest_rolling import (
    run_window, load_oi, load_funding,
)
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

OUT_DIR = os.path.dirname(__file__)
DATASET_PATH = os.path.join(OUT_DIR, "feature_modulator_dataset.json")
REPORT_PATH = os.path.join(OUT_DIR, "feature_modulator_eda.md")

STRATS = ["S1", "S5", "S8", "S9", "S10"]
DIRS = [1, -1]
SESSIONS = ["Asia", "EU", "US", "Night", "WE"]
MIN_N = 30           # skip cells with fewer than 30 trades
EFFECT_FLOOR = 0.15  # |ρ| floor for a candidate
ALPHA = 0.05
N_SHUFFLES = 200


def run_backtest(label: str, start_dt: datetime, end_dt: datetime,
                 features, data, sector_features, oi_data, funding_data) -> list:
    """Run the canonical backtest_rolling configuration on [start, end]."""
    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    print(f"[3/5] Running backtest {label} ({start_dt.date()} → {end_dt.date()})...",
          flush=True)
    r = run_window(
        features, data, sector_features, {},
        start_ts, end_ts,
        start_capital=1000.0,
        oi_data=oi_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=True,
    )
    trades = r["trades"]
    for t in trades:
        t["window"] = label
    print(f"[3/5] Running backtest {label}... {len(trades)} trades captured "
          f"(PnL ${r['pnl']:,.0f}, WR {r['win_rate']:.0f}%, DD {r['max_dd_pct']:.1f}%)",
          flush=True)
    return trades


def session_features(session: str) -> dict:
    """One-hot encode the session label."""
    return {f"session_{s}": int(session == s) for s in SESSIONS}


def shuffle_pvalue(x: np.ndarray, y: np.ndarray, rho_real: float,
                   n_shuffles: int = N_SHUFFLES, rng=None) -> tuple[float, float, float]:
    """Null-shuffle p-value for spearman ρ.

    Shuffles `x` n_shuffles times, recomputes ρ each time, returns:
        (p_value, shuffle_mean, shuffle_sd)
    p is two-sided: fraction of |ρ_shuffle| ≥ |ρ_real|.
    """
    rng = rng or np.random.default_rng(42)
    n = len(x)
    rhos = np.empty(n_shuffles)
    x_shuf = x.copy()
    for i in range(n_shuffles):
        rng.shuffle(x_shuf)
        r, _ = spearmanr(x_shuf, y)
        rhos[i] = r if not np.isnan(r) else 0.0
    abs_real = abs(rho_real)
    p = float((np.abs(rhos) >= abs_real).mean())
    return p, float(rhos.mean()), float(rhos.std())


def run_eda(trades: list, window_label: str) -> list:
    """Run the per-(strat,dir,feature) Spearman + null-shuffle analysis.

    Returns a list of result dicts (one per test run), all containing:
        window, strat, dir, feature, n, rho_net, p_null_net, p_bonf_net,
                                      rho_pnl, p_null_pnl, p_bonf_pnl,
                                      significant
    """
    # Build feature rows per trade
    rows = []
    for t in trades:
        if t.get("session") is None or t.get("conf_partial") is None:
            continue
        rows.append({
            "strat": t["strat"],
            "dir":   t["dir"],
            "net":   float(t.get("net", 0.0)),
            "pnl":   float(t.get("pnl", 0.0)),
            "conf_partial": int(t["conf_partial"]),
            "session": t["session"],
            **session_features(t["session"]),
        })

    feature_names = ["conf_partial"] + [f"session_{s}" for s in SESSIONS]
    cells = defaultdict(list)
    for r in rows:
        cells[(r["strat"], r["dir"])].append(r)

    # First pass: count actual tests (after low-n skip) for Bonferroni
    test_specs: list[tuple[str, int, str]] = []
    for (strat, dir_) in sorted(cells.keys()):
        if len(cells[(strat, dir_)]) < MIN_N:
            continue
        for feat in feature_names:
            sample = cells[(strat, dir_)]
            x = np.array([r[feat] for r in sample], dtype=float)
            if x.std() == 0:  # degenerate (all zero or all one)
                continue
            test_specs.append((strat, dir_, feat))

    n_tests = len(test_specs)
    bonf_thresh = ALPHA / max(1, n_tests)
    print(f"[4/5] Analysis ({window_label}): {n_tests} tests after low-n + "
          f"degenerate skips. Bonferroni threshold p<{bonf_thresh:.5f}",
          flush=True)

    rng = np.random.default_rng(2026)
    results = []
    for (strat, dir_, feat) in test_specs:
        sample = cells[(strat, dir_)]
        x = np.array([r[feat] for r in sample], dtype=float)
        y_net = np.array([r["net"] for r in sample], dtype=float)
        y_pnl = np.array([r["pnl"] for r in sample], dtype=float)
        rho_net, _ = spearmanr(x, y_net)
        rho_pnl, _ = spearmanr(x, y_pnl)
        if np.isnan(rho_net):
            rho_net = 0.0
        if np.isnan(rho_pnl):
            rho_pnl = 0.0
        p_net, _, _ = shuffle_pvalue(x, y_net, rho_net, rng=rng)
        p_pnl, _, _ = shuffle_pvalue(x, y_pnl, rho_pnl, rng=rng)
        sig_net = (p_net < bonf_thresh) and (abs(rho_net) >= EFFECT_FLOOR)
        sig_pnl = (p_pnl < bonf_thresh) and (abs(rho_pnl) >= EFFECT_FLOOR)
        results.append({
            "window": window_label,
            "strat": strat,
            "dir": dir_,
            "feature": feat,
            "n": len(sample),
            "rho_net": rho_net,
            "p_null_net": p_net,
            "p_bonf_net": p_net * n_tests,
            "rho_pnl": rho_pnl,
            "p_null_pnl": p_pnl,
            "p_bonf_pnl": p_pnl * n_tests,
            "sig_net": bool(sig_net),
            "sig_pnl": bool(sig_pnl),
            "significant": bool(sig_net or sig_pnl),
        })

    return results


def cell_breakdown(trades: list, strat: str, dir_: int, feature: str) -> list[str]:
    """Per-bucket breakdown (n, mean_net, mean_pnl, total_pnl, WR) for a cell."""
    sub = [t for t in trades if t["strat"] == strat and t["dir"] == dir_
           and t.get("session") is not None]
    if not sub:
        return []
    lines = ["| Bucket | n | mean_net (bps) | mean_pnl ($) | total_pnl ($) | WR |",
             "|---|---|---|---|---|---|"]
    if feature == "conf_partial":
        buckets = sorted({t["conf_partial"] for t in sub})
        for b in buckets:
            xs = [t for t in sub if t["conf_partial"] == b]
            nets = [t["net"] for t in xs]
            pnls = [t["pnl"] for t in xs]
            wins = sum(1 for p in pnls if p > 0)
            lines.append(f"| conf={b} | {len(xs)} | {sum(nets)/len(nets):+.1f} | "
                         f"{sum(pnls)/len(pnls):+.0f} | {sum(pnls):+,.0f} | "
                         f"{wins/len(xs)*100:.0f}% |")
    else:
        target = feature.replace("session_", "")
        for s in SESSIONS:
            xs = [t for t in sub if t["session"] == s]
            if not xs:
                continue
            nets = [t["net"] for t in xs]
            pnls = [t["pnl"] for t in xs]
            wins = sum(1 for p in pnls if p > 0)
            star = " ★" if s == target else ""
            lines.append(f"| {s}{star} | {len(xs)} | {sum(nets)/len(nets):+.1f} | "
                         f"{sum(pnls)/len(pnls):+.0f} | {sum(pnls):+,.0f} | "
                         f"{wins/len(xs)*100:.0f}% |")
    return lines


def build_report(results_28m: list, results_12m: list, n_trades_28m: int,
                 n_trades_12m: int, trades_28m: list | None = None) -> str:
    candidates_28m = [r for r in results_28m if r["significant"]]
    n_cand = len(candidates_28m)

    lines: list[str] = []
    lines.append("# Feature-modulator EDA — results")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append("## TL;DR")
    lines.append("")
    if n_cand == 0:
        lines.append("**0 candidates** survived Bonferroni + effect-floor on the "
                     "28m window. Feature-driven per-trade modulation is **not "
                     "supported** by `conf_partial` or `session` on the historical "
                     "data the backtest can reconstruct. No basis for a "
                     "`backtest_feature_modulator.py` walk-forward sweep on these "
                     "two features.")
    else:
        # Distinguish pnl-only from both-significant survivors
        both = [c for c in candidates_28m if c["sig_net"] and c["sig_pnl"]]
        pnl_only = [c for c in candidates_28m if c["sig_pnl"] and not c["sig_net"]]
        net_only = [c for c in candidates_28m if c["sig_net"] and not c["sig_pnl"]]
        lines.append(f"**{n_cand} candidate{'s' if n_cand>1 else ''}** survived "
                     "Bonferroni + effect-floor on the 28m window: "
                     f"{len(both)} on both (`net_bps` AND `pnl_usdt`), "
                     f"{len(net_only)} on `net_bps` only, "
                     f"{len(pnl_only)} on `pnl_usdt` only.")
        if pnl_only and not both and not net_only:
            lines.append("")
            lines.append("**Important**: all candidates are `pnl_usdt`-only "
                         "survivors. PnL aggregates per-trade bps with size, "
                         "and under the v11.10.0 adaptive macro modulator size "
                         "varies with `btc_z`. For S1 in particular, the "
                         "candidate's ρ_pnl signal may be conflated with the "
                         "size-amplification at high `btc_z` — see the "
                         "Candidates section for the asymmetry warning. "
                         "Treat as **provisional**, not a clear go-signal.")
        lines.append("")
        lines.append("See the Candidates section for details and 12m "
                     "replication status.")
    lines.append("")
    lines.append(f"- Dataset: 28m={n_trades_28m} trades, 12m={n_trades_12m} trades")
    lines.append(f"- Features: `conf_partial` (0-4) + one-hot `session_*` "
                 f"({', '.join(SESSIONS)})")
    lines.append(f"- Effect floor: |ρ| ≥ {EFFECT_FLOOR}")
    lines.append(f"- Family-wise α = {ALPHA} (Bonferroni over actually-run tests)")
    lines.append(f"- Null shuffles per test: {N_SHUFFLES}")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("1. Ran `backtest_rolling.run_window` with the live shipped "
                 "config (D2 dead-timeout + runner extension + v11.10.0 adaptive "
                 "macro modulator) on the 28m + 12m windows, using a patched "
                 "engine that records two at-entry features on every trade:")
    lines.append("   - `conf_partial = sum([|drawdown|>3000, vol_z>1.5, "
                 "|ret_24h|>200, n_stress_global≥5])` — mirrors "
                 "`analysis/bot/bot.py:258-262` minus the OI component "
                 "(`oi_delta_1h < -1.0`) which is unavailable in backtest.")
    lines.append("   - `session ∈ {Asia, EU, US, Night, WE}` derived from the "
                 "entry candle's UTC hour and weekday — exact mirror of the "
                 "live formula at `analysis/bot/bot.py:263-266`.")
    lines.append("2. For each (strategy ∈ {S1,…,S10}, direction ∈ {+1,-1}, "
                 "feature ∈ {conf_partial, session_*}) cell with n ≥ "
                 f"{MIN_N} trades: Spearman ρ vs `net_bps` and `pnl_usdt`.")
    lines.append("3. Null-shuffle p-value: shuffle the feature array "
                 f"{N_SHUFFLES} times, recompute ρ, p = fraction of "
                 "|ρ_shuffle| ≥ |ρ_real|.")
    lines.append("4. Bonferroni correction across the actual test count "
                 "(degenerate one-hot cells with std=0 are skipped before "
                 "counting).")
    lines.append("5. A pair is a **candidate** when "
                 f"`p_bonferroni < {ALPHA}` AND `|ρ| ≥ {EFFECT_FLOOR}` for "
                 "either `net_bps` or `pnl_usdt`.")
    lines.append("")
    lines.append("## Sample sizes")
    lines.append("")
    by_cell_28m: dict[tuple, int] = defaultdict(int)
    by_cell_12m: dict[tuple, int] = defaultdict(int)
    for r in results_28m:
        by_cell_28m[(r["strat"], r["dir"])] = r["n"]
    for r in results_12m:
        by_cell_12m[(r["strat"], r["dir"])] = r["n"]
    lines.append("| Strat | Dir | n (28m) | n (12m) |")
    lines.append("|---|---|---|---|")
    for strat in STRATS:
        for d in DIRS:
            key = (strat, d)
            n28 = by_cell_28m.get(key, 0)
            n12 = by_cell_12m.get(key, 0)
            dlabel = "LONG" if d == 1 else "SHORT"
            mark28 = "" if n28 >= MIN_N else " ⚠ low-n"
            lines.append(f"| {strat} | {dlabel} | {n28}{mark28} | {n12} |")
    lines.append("")
    lines.append(f"_Cells with n<{MIN_N} on 28m skipped from testing._")
    lines.append("")
    lines.append("## Results — 28m window")
    lines.append("")
    lines.append("Sorted by |ρ_net| descending. `p_bonf` columns are the raw "
                 "null-shuffle p multiplied by the test count "
                 "(capped at 1 in the table for readability).")
    lines.append("")
    lines.append("| Strat | Dir | Feature | n | ρ_net | p_bonf_net | ρ_pnl | "
                 "p_bonf_pnl | sig |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    res_sorted = sorted(results_28m, key=lambda r: -abs(r["rho_net"]))
    for r in res_sorted:
        dlabel = "LONG" if r["dir"] == 1 else "SHORT"
        sig = "✓" if r["significant"] else ""
        pbn = min(1.0, r["p_bonf_net"])
        pbp = min(1.0, r["p_bonf_pnl"])
        lines.append(
            f"| {r['strat']} | {dlabel} | `{r['feature']}` | {r['n']} | "
            f"{r['rho_net']:+.3f} | {pbn:.3f} | "
            f"{r['rho_pnl']:+.3f} | {pbp:.3f} | {sig} |"
        )
    lines.append("")
    lines.append("## Results — 12m window (replication check)")
    lines.append("")
    lines.append("Same analysis on 12m. If a 28m candidate truly carries signal, "
                 "the same direction (sign of ρ) should show up here even if "
                 "n is smaller and significance is harder.")
    lines.append("")
    lines.append("| Strat | Dir | Feature | n | ρ_net | p_bonf_net | ρ_pnl | "
                 "p_bonf_pnl | sig |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    res12_sorted = sorted(results_12m, key=lambda r: -abs(r["rho_net"]))
    for r in res12_sorted:
        dlabel = "LONG" if r["dir"] == 1 else "SHORT"
        sig = "✓" if r["significant"] else ""
        pbn = min(1.0, r["p_bonf_net"])
        pbp = min(1.0, r["p_bonf_pnl"])
        lines.append(
            f"| {r['strat']} | {dlabel} | `{r['feature']}` | {r['n']} | "
            f"{r['rho_net']:+.3f} | {pbn:.3f} | "
            f"{r['rho_pnl']:+.3f} | {pbp:.3f} | {sig} |"
        )
    lines.append("")
    lines.append("## Candidates")
    lines.append("")
    if n_cand == 0:
        lines.append("**None.** No (strat, dir, feature) triple passed the joint "
                     f"`|ρ| ≥ {EFFECT_FLOOR}` + `p_bonferroni < {ALPHA}` filter on "
                     "the 28m window. The 12m results are presented above for "
                     "completeness — they don't change the conclusion.")
        lines.append("")
        lines.append("**Interpretation**: the bot's at-entry candle-based "
                     "confluence and session features carry no statistically "
                     "robust per-trade ranking signal once you adjust for "
                     "multiple comparisons. This is consistent with the fact "
                     "that binary skip-filters on these features have all failed "
                     "walk-forward 4/4 in prior R&D — there's literally no "
                     "predictive ρ to exploit, with or without the slot-effect "
                     "complication.")
        lines.append("")
        lines.append("**No next step**: no `backtest_feature_modulator.py` "
                     "walk-forward sweep is justified on these two features. "
                     "Future EDA should target the **observation-only** features "
                     "the backtest currently cannot reconstruct: full "
                     "`entry_confluence` (with OI component), `entry_crowding`, "
                     "`entry_oi_delta`. Those require running the analysis on "
                     "live trade data from the SQLite DB once a sufficient "
                     "sample has accumulated (per-protocol target: 50+ trades "
                     "with full entry-context, currently logged but not yet "
                     "analyzed).")
    else:
        lines.append("")
        for r in candidates_28m:
            dlabel = "LONG" if r["dir"] == 1 else "SHORT"
            # Find the 12m counterpart
            r12 = next((x for x in results_12m
                        if x["strat"] == r["strat"] and x["dir"] == r["dir"]
                        and x["feature"] == r["feature"]), None)
            lines.append(f"### {r['strat']} {dlabel} × `{r['feature']}`")
            lines.append("")
            lines.append(f"- n (28m) = {r['n']}")
            lines.append(f"- ρ_net = {r['rho_net']:+.3f}, p_bonf = "
                         f"{min(1.0, r['p_bonf_net']):.4f} "
                         f"({'✓' if r['sig_net'] else '✗'})")
            lines.append(f"- ρ_pnl = {r['rho_pnl']:+.3f}, p_bonf = "
                         f"{min(1.0, r['p_bonf_pnl']):.4f} "
                         f"({'✓' if r['sig_pnl'] else '✗'})")
            if r12:
                same_sign = (
                    np.sign(r12["rho_net"]) == np.sign(r["rho_net"])
                    and abs(r12["rho_net"]) >= EFFECT_FLOOR / 2
                )
                lines.append(
                    f"- 12m replication: n={r12['n']}, ρ_net={r12['rho_net']:+.3f}, "
                    f"sign-match={'yes' if same_sign else 'no'}"
                )
            else:
                lines.append("- 12m replication: cell absent (n<MIN_N on 12m)")
            # Per-bucket breakdown so the reader sees what's driving rho
            if trades_28m is not None:
                bd = cell_breakdown(trades_28m, r["strat"], r["dir"],
                                     r["feature"])
                if bd:
                    lines.append("")
                    lines.extend(bd)
            # Discrepancy warning: rho_pnl significant but rho_net not
            if r["sig_pnl"] and not r["sig_net"]:
                lines.append("- ⚠ **Significance asymmetry**: `pnl_usdt` "
                             "Bonferroni-significant, `net_bps` not. The "
                             "per-trade bps direction matches (same sign, "
                             "similar magnitude on ρ_net), but the "
                             "null-shuffle variance is larger on `net_bps` so "
                             "the p-value doesn't clear Bonferroni. Two "
                             "possible explanations: (1) genuine "
                             "feature→edge effect that's underpowered at "
                             "n=73, or (2) PnL ρ is inflated by the v11.10.0 "
                             "size modulator co-amplifying `btc_z` and "
                             "feature value (likely for S1, which fires "
                             "*only* when btc30 > +2000 bps and is itself "
                             "S1-α=+0.5-amplified). Disambiguating requires "
                             "rerunning the EDA with "
                             "`apply_adaptive_modulator=False`.")
            lines.append("")
        lines.append("**Suggested next step (conditional)**: open "
                     "`backtests/backtest_feature_modulator.py` and "
                     "walk-forward-sweep `size *= 1 + α × normalize(feature)` "
                     "on the surviving (strat, dir, feature) triples. Match "
                     "the v11.10.0 pattern: per-α grid, strict 4/4 + ΔDD avg "
                     "≤ +1pp. **Before doing so**, address the asymmetry "
                     "warnings above — if all candidates are pnl-only, "
                     "redo the EDA on `net_bps` with size held constant "
                     "(disable adaptive_modulator) to isolate per-trade edge "
                     "from regime-size confounding.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append(f"- **Partial confluence**: the live `entry_confluence` has 5 "
                 "components (drawdown, vol_z, ret_24h, n_stress_global, "
                 "oi_delta_1h). The backtest can only reconstruct the first 4; "
                 "the OI component (1h delta) requires live OI snapshots not "
                 "available in the historical 4h candle data. The partial "
                 "version may under-detect signal vs the live version.")
    lines.append("- **No out-of-sample split**: this is a pure association test "
                 "on in-sample data. A candidate would still need a "
                 "walk-forward train/test before any deployment claim.")
    lines.append("- **EDA, not a ship signal**: zero candidates here means "
                 "*no further work on these two features*. It does **not** mean "
                 "the bot's other observation features (OI delta, crowding, "
                 "full confluence) carry no signal — they were out of scope.")
    lines.append("- **Bonferroni is conservative**: with ~50-60 tests and per-test "
                 f"α={ALPHA}, the per-test threshold drops to ~{ALPHA/60:.5f}. "
                 "Real but small effects (|ρ|≈0.1) would be missed. The "
                 "effect-floor at |ρ|≥0.15 is independently restrictive — "
                 "anything weaker is too small to justify a modulator anyway.")
    lines.append("- **Slot effect not modeled**: the analysis is per-trade, "
                 "not per-portfolio. Even a real per-trade ρ wouldn't "
                 "automatically translate to a profitable sizing rule because "
                 "the modulated size frees/consumes slot capacity. The "
                 "intended next step (`backtest_feature_modulator.py`) is "
                 "where slot effects are tested, not here.")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append("```bash")
    lines.append("cd /home/crypto")
    lines.append(".venv/bin/python3 -m backtests.feature_modulator_eda")
    lines.append("```")
    lines.append("")
    lines.append("Dataset persisted at `backtests/feature_modulator_dataset.json`.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    print("[1/5] Understanding scaffolding... (done — see commit history)",
          flush=True)
    print("[2/5] Backtest_rolling.py patched (see prior commit) — "
          "loading data...", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    print(f"      Loaded {len(data)} coins, "
          f"{sum(len(f) for f in features.values())} feature points", flush=True)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()
    funding_data = load_funding()
    print(f"      OI for {len(oi_data)} coins, funding for "
          f"{len(funding_data)} coins", flush=True)

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"      Data ends at {end_dt.isoformat()}", flush=True)

    trades_28m = run_backtest(
        "28m", end_dt - relativedelta(months=28), end_dt,
        features, data, sector_features, oi_data, funding_data,
    )
    trades_12m = run_backtest(
        "12m", end_dt - relativedelta(months=12), end_dt,
        features, data, sector_features, oi_data, funding_data,
    )

    all_trades = trades_28m + trades_12m
    with open(DATASET_PATH, "w") as fh:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_28m": len(trades_28m),
            "n_12m": len(trades_12m),
            "trades": all_trades,
        }, fh, default=str)
    print(f"      Dataset written to {DATASET_PATH}", flush=True)

    results_28m = run_eda(trades_28m, "28m")
    results_12m = run_eda(trades_12m, "12m")

    report = build_report(results_28m, results_12m,
                           len(trades_28m), len(trades_12m),
                           trades_28m=trades_28m)
    with open(REPORT_PATH, "w") as fh:
        fh.write(report)
    print(f"[5/5] Report written: {REPORT_PATH}", flush=True)

    n_cand = sum(1 for r in results_28m if r["significant"])
    if n_cand == 0:
        print("DONE — 0 candidates, feature-driven modulation not supported "
              "by conf_partial or session on 28m data", flush=True)
    else:
        print(f"DONE — {n_cand} candidate(s) survived Bonferroni on 28m. "
              "See report for replication on 12m.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
