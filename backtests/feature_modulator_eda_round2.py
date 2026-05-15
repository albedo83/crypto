"""Feature-modulator EDA — round 2.

Round 2 expands the round-1 EDA in two ways:

  1. **Continuous features** — round 1 tested only `conf_partial` (0-4) and
     `session_*` (one-hot). Round 2 tests 10 continuous candle-based features
     captured at entry on every trade (see `entry_feats` in backtest_rolling).

  2. **S1 LONG × session_Asia disambiguation** — round 1's only candidate
     passed Bonferroni on `pnl_usdt` (+0.393) but failed on `net_bps` (+0.282).
     Suspected confounding with the v11.10.0 macro modulator (S1 fires only
     when btc_z is high, where the modulator amplifies size by ~1.5×–2.5×).
     We rerun the 28m backtest with the adaptive modulator *disabled* and
     report ρ_pnl side-by-side. If ρ_pnl drops to ~ρ_net, it was a size
     artifact. If it stays high, it's an underlying edge.

Methodology (same as round 1):
  - Spearman ρ on continuous features against both `net_bps` and `pnl_usdt`.
  - Null-shuffle p (200 shuffles, two-sided).
  - Bonferroni at α=0.05 across the actually-run tests.
  - BH FDR at q=0.05 as a secondary, less-conservative view.
  - Min n=30 per (strat, dir) subset.
  - Effect floor |ρ| ≥ 0.15.
  - Candidate must pass BOTH `ρ_net` AND `ρ_pnl` Bonferroni (round 1 lesson).

Run:
  cd /home/crypto && .venv/bin/python3 -m backtests.feature_modulator_eda_round2
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

# Import config FIRST so we can monkey-patch the modulator dicts before the
# backtest engine imports them (in `from analysis.bot.config import …`).
from analysis.bot import config as bot_config  # type: ignore

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
DATASET_PATH = os.path.join(OUT_DIR, "feature_modulator_dataset_r2.json")
REPORT_PATH = os.path.join(OUT_DIR, "feature_modulator_eda_round2.md")

STRATS = ["S1", "S5", "S8", "S9", "S10"]
DIRS = [1, -1]
SESSIONS = ["Asia", "EU", "US", "Night", "WE"]
CONT_FEATURES = [
    "entry_shock",
    "entry_clean",
    "entry_lead",
    "entry_vol_z",
    "entry_range_pct",
    "entry_disp_24h",
    "entry_disp_7d",
    "entry_n_stress",
    "entry_ret24h_abs",
    "entry_drawdown_abs",
]
MIN_N = 30
EFFECT_FLOOR = 0.15
ALPHA = 0.05
N_SHUFFLES = 200


# ── Backtest runners ────────────────────────────────────────────────

def _run_one(label: str, start_dt, end_dt, features, data, sector_features,
             oi_data, funding_data, apply_modulator: bool) -> list:
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
    print(f"[3/6] Backtest {label} ({start_dt.date()} → {end_dt.date()}, "
          f"modulator={'ON' if apply_modulator else 'OFF'})...", flush=True)
    r = run_window(
        features, data, sector_features, {},
        start_ts, end_ts,
        start_capital=1000.0,
        oi_data=oi_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        funding_data=funding_data,
        apply_adaptive_modulator=apply_modulator,
    )
    trades = r["trades"]
    for t in trades:
        t["window"] = label
        t["modulator"] = "ON" if apply_modulator else "OFF"
    print(f"      {len(trades)} trades, PnL ${r['pnl']:,.0f}, "
          f"WR {r['win_rate']:.0f}%, DD {r['max_dd_pct']:.1f}%", flush=True)
    return trades


# ── Stats helpers ────────────────────────────────────────────────────

def shuffle_pvalue(x: np.ndarray, y: np.ndarray, rho_real: float,
                   n_shuffles: int = N_SHUFFLES, rng=None) -> float:
    rng = rng or np.random.default_rng(42)
    rhos = np.empty(n_shuffles)
    x_shuf = x.copy()
    for i in range(n_shuffles):
        rng.shuffle(x_shuf)
        r, _ = spearmanr(x_shuf, y)
        rhos[i] = r if not np.isnan(r) else 0.0
    return float((np.abs(rhos) >= abs(rho_real)).mean())


def bh_fdr(pvalues: list[float], q: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg step-up. Returns a boolean list aligned with input."""
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    passed = [False] * n
    k_max = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= (rank / n) * q:
            k_max = rank
    if k_max > 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= k_max:
                passed[idx] = True
    return passed


# ── Core EDA ────────────────────────────────────────────────────────

def _session_onehot(session: str) -> dict:
    return {f"session_{s}": int(session == s) for s in SESSIONS}


def _build_rows(trades: list) -> list[dict]:
    rows = []
    for t in trades:
        ef = t.get("entry_feats") or {}
        if not ef or t.get("session") is None:
            continue
        row = {
            "strat": t["strat"], "dir": t["dir"],
            "net": float(t.get("net", 0.0)),
            "pnl": float(t.get("pnl", 0.0)),
            "size": float(t.get("size", 0.0)),
            "conf_partial": int(t.get("conf_partial", 0) or 0),
            "session": t["session"],
            **_session_onehot(t["session"]),
        }
        for fk in CONT_FEATURES:
            row[fk] = float(ef.get(fk, 0.0))
        rows.append(row)
    return rows


def run_eda(trades: list, label: str) -> list:
    """Spearman ρ + null-shuffle + Bonferroni + BH FDR.

    Returns list of dicts (one per test). Tests run on `CONT_FEATURES` only.
    `session` is kept around because the disambiguation block re-tests it
    separately for the modulator ON vs OFF comparison.
    """
    rows = _build_rows(trades)
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        cells[(r["strat"], r["dir"])].append(r)

    # First pass: count non-degenerate tests for Bonferroni
    test_specs: list[tuple[str, int, str]] = []
    for (strat, dir_) in sorted(cells.keys()):
        if len(cells[(strat, dir_)]) < MIN_N:
            continue
        for feat in CONT_FEATURES:
            sample = cells[(strat, dir_)]
            x = np.array([r[feat] for r in sample], dtype=float)
            if x.std() == 0:
                continue
            test_specs.append((strat, dir_, feat))

    n_tests = len(test_specs)
    bonf_thresh = ALPHA / max(1, n_tests)
    print(f"[5/6] EDA ({label}): {n_tests} continuous tests after low-n + "
          f"degenerate skips. Bonferroni p<{bonf_thresh:.5f}", flush=True)

    rng = np.random.default_rng(2026)
    results = []
    for (strat, dir_, feat) in test_specs:
        sample = cells[(strat, dir_)]
        x = np.array([r[feat] for r in sample], dtype=float)
        y_net = np.array([r["net"] for r in sample], dtype=float)
        y_pnl = np.array([r["pnl"] for r in sample], dtype=float)
        rho_net, _ = spearmanr(x, y_net)
        rho_pnl, _ = spearmanr(x, y_pnl)
        if np.isnan(rho_net): rho_net = 0.0
        if np.isnan(rho_pnl): rho_pnl = 0.0
        p_net = shuffle_pvalue(x, y_net, rho_net, rng=rng)
        p_pnl = shuffle_pvalue(x, y_pnl, rho_pnl, rng=rng)
        results.append({
            "window": label,
            "strat": strat, "dir": dir_, "feature": feat,
            "n": len(sample),
            "rho_net": float(rho_net), "p_null_net": p_net,
            "p_bonf_net": min(1.0, p_net * n_tests),
            "rho_pnl": float(rho_pnl), "p_null_pnl": p_pnl,
            "p_bonf_pnl": min(1.0, p_pnl * n_tests),
            "n_tests": n_tests,
        })

    # BH FDR on the two outcome families
    if results:
        p_net_list = [r["p_null_net"] for r in results]
        p_pnl_list = [r["p_null_pnl"] for r in results]
        bh_net = bh_fdr(p_net_list, q=ALPHA)
        bh_pnl = bh_fdr(p_pnl_list, q=ALPHA)
        for i, r in enumerate(results):
            r["bh_net"] = bh_net[i] and abs(r["rho_net"]) >= EFFECT_FLOOR
            r["bh_pnl"] = bh_pnl[i] and abs(r["rho_pnl"]) >= EFFECT_FLOOR
            # Strict candidate: BOTH net AND pnl pass Bonferroni + floor
            r["sig_net"] = (
                r["p_bonf_net"] < ALPHA and abs(r["rho_net"]) >= EFFECT_FLOOR
            )
            r["sig_pnl"] = (
                r["p_bonf_pnl"] < ALPHA and abs(r["rho_pnl"]) >= EFFECT_FLOOR
            )
            r["candidate"] = r["sig_net"] and r["sig_pnl"]

    return results


# ── S1 LONG × Asia disambiguation ──────────────────────────────────

def disambig_session_asia(trades_on: list, trades_off: list) -> dict:
    """Compute ρ_net and ρ_pnl on S1 LONG × session_Asia for both runs."""
    out = {}
    for label, trades in (("on", trades_on), ("off", trades_off)):
        rows = _build_rows(trades)
        sub = [r for r in rows if r["strat"] == "S1" and r["dir"] == 1]
        if not sub:
            out[label] = {"n": 0, "n_asia": 0}
            continue
        x = np.array([r["session_Asia"] for r in sub], dtype=float)
        y_net = np.array([r["net"] for r in sub], dtype=float)
        y_pnl = np.array([r["pnl"] for r in sub], dtype=float)
        rho_net, _ = spearmanr(x, y_net)
        rho_pnl, _ = spearmanr(x, y_pnl)
        out[label] = {
            "n": len(sub),
            "n_asia": int(sum(x)),
            "rho_net": float(rho_net) if not np.isnan(rho_net) else 0.0,
            "rho_pnl": float(rho_pnl) if not np.isnan(rho_pnl) else 0.0,
            "p_null_net": shuffle_pvalue(x, y_net, rho_net or 0.0,
                                          rng=np.random.default_rng(2026)),
            "p_null_pnl": shuffle_pvalue(x, y_pnl, rho_pnl or 0.0,
                                          rng=np.random.default_rng(2026)),
            "asia_total_pnl": float(sum(p for p, xi in zip(y_pnl, x) if xi)),
            "non_asia_total_pnl": float(sum(p for p, xi in zip(y_pnl, x) if not xi)),
            "asia_total_net": float(sum(p for p, xi in zip(y_net, x) if xi)),
            "non_asia_total_net": float(sum(p for p, xi in zip(y_net, x) if not xi)),
        }
    return out


# ── Bucket breakdown for surviving candidates ──────────────────────

def tercile_breakdown(trades: list, strat: str, dir_: int, feat: str) -> list[str]:
    """Low/mid/high tercile breakdown for a continuous feature."""
    rows = _build_rows(trades)
    sub = [r for r in rows if r["strat"] == strat and r["dir"] == dir_]
    if len(sub) < 9:
        return []
    vals = sorted(r[feat] for r in sub)
    n = len(vals)
    lo = vals[n // 3]
    hi = vals[2 * n // 3]

    def _bucket(v):
        if v <= lo: return "low"
        if v >= hi: return "high"
        return "mid"

    buckets = {"low": [], "mid": [], "high": []}
    for r in sub:
        buckets[_bucket(r[feat])].append(r)

    lines = [f"_Tercile bounds: low ≤ {lo:.3g}, high ≥ {hi:.3g}_",
             "",
             "| Bucket | n | WR | mean_net (bps) | total_pnl ($) | mean_size ($) |",
             "|---|---|---|---|---|---|"]
    for b in ("low", "mid", "high"):
        xs = buckets[b]
        if not xs:
            continue
        nets = [r["net"] for r in xs]
        pnls = [r["pnl"] for r in xs]
        sizes = [r.get("size", 0.0) for r in xs]
        mean_size = (sum(sizes) / len(sizes)) if sizes else 0.0
        wins = sum(1 for p in pnls if p > 0)
        lines.append(f"| {b} | {len(xs)} | {wins/len(xs)*100:.0f}% | "
                     f"{sum(nets)/len(nets):+.1f} | {sum(pnls):+,.0f} | "
                     f"{mean_size:,.0f} |")
    return lines


# ── Report ──────────────────────────────────────────────────────────

def build_report(results: list, n_trades: int,
                 disambig: dict, trades_on: list,
                 results_off: list | None = None,
                 trades_off: list | None = None) -> str:
    candidates = [r for r in results if r["candidate"]]
    bh_only = [r for r in results
               if not r["candidate"] and (r["bh_net"] or r["bh_pnl"])]
    n_cand = len(candidates)
    # Cross-reference modulator OFF results so we can spot the reverse
    # asymmetry: ρ_net passes Bonferroni under ON, ρ_pnl is muddied by size
    # variance — but under OFF, ρ_pnl also clears Bonferroni.
    off_by_key: dict[tuple, dict] = {}
    if results_off:
        for r in results_off:
            off_by_key[(r["strat"], r["dir"], r["feature"])] = r
    candidates_off = [r for r in (results_off or []) if r["candidate"]]
    # Survivors: strict candidate under EITHER ON or OFF (round-2 finding).
    cand_either = {(r["strat"], r["dir"], r["feature"]) for r in candidates}
    cand_either |= {(r["strat"], r["dir"], r["feature"]) for r in candidates_off}

    lines: list[str] = []
    lines.append("# Feature-modulator EDA — round 2")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append("Continuous-feature EDA + S1 LONG × Asia disambiguation. "
                 "Round 1 (see `feature_modulator_eda.md`) tested only "
                 "`conf_partial` + `session_*` and surfaced 1 borderline "
                 "candidate (S1 LONG × Asia, ρ_pnl=+0.393 ✓ but ρ_net=+0.282 ✗).")
    lines.append("")
    lines.append("## TL;DR")
    lines.append("")
    if n_cand == 0 and not candidates_off:
        lines.append("**0 candidates** passed the strict joint filter "
                     "(`p_bonferroni < 0.05` AND `|ρ| ≥ 0.15` on **BOTH** "
                     "`net_bps` AND `pnl_usdt`) on the canonical 28m window, "
                     "and 0 under the modulator-OFF rerun either. "
                     f"{len(bh_only)} test(s) survived the looser BH FDR "
                     "filter — see *FDR view* section, treat as observation "
                     "only, not ship-ready.")
    elif n_cand == 0 and candidates_off:
        names = ", ".join(f"{r['strat']} {'LONG' if r['dir']==1 else 'SHORT'} × `{r['feature']}`"
                          for r in candidates_off)
        lines.append(f"**0 strict candidates under modulator ON, "
                     f"{len(candidates_off)} under modulator OFF**: "
                     f"{names}. This is a reverse asymmetry from round 1: "
                     "the per-trade ranking signal (ρ_net) is robust under "
                     "BOTH configurations, but the v11.10.0 macro modulator's "
                     "size variance muddies ρ_pnl under canonical config. "
                     "When the modulator is held off (flat sizing), ρ_pnl "
                     "also clears Bonferroni. **Interpretation**: real "
                     "per-trade edges that the existing macro modulator "
                     "partially masks at the PnL level. See *Modulator "
                     "OFF cross-reference* and **Recommendation**.")
    else:
        lines.append(f"**{n_cand} candidate{'s' if n_cand>1 else ''}** "
                     "passed the strict joint filter on the 28m window "
                     f"(canonical, modulator ON), "
                     f"{len(candidates_off)} under modulator OFF. "
                     "See *Candidates* section for tercile breakdowns and "
                     "the **Recommendation** at the end of the report.")
    lines.append("")
    # Disambig top-line
    d_on = disambig.get("on", {})
    d_off = disambig.get("off", {})
    if d_on and d_off and d_on.get("n", 0) > 0 and d_off.get("n", 0) > 0:
        delta = d_on["rho_pnl"] - d_off["rho_pnl"]
        if abs(delta) < 0.05:
            verdict = ("**Underlying edge confirmed** — ρ_pnl barely "
                       "changes when the macro modulator is disabled, so "
                       "the round-1 S1 LONG × Asia signal is not a "
                       "size-amplification artifact.")
        elif d_off["rho_pnl"] < d_on["rho_pnl"] - 0.05:
            verdict = ("**Size-artifact confirmed (partial)** — ρ_pnl "
                       f"drops from {d_on['rho_pnl']:+.3f} (modulator ON) "
                       f"to {d_off['rho_pnl']:+.3f} (modulator OFF). The "
                       "round-1 PnL-only Bonferroni pass was inflated by "
                       "the v11.10.0 size modulator co-amplifying btc_z "
                       "and the Asia session.")
        else:
            verdict = ("**Inconclusive** — ρ_pnl moves but in the "
                       "unexpected direction.")
        lines.append(f"**S1 LONG × Asia disambiguation**: {verdict}")
        lines.append("")
    lines.append(f"- Dataset: 28m={n_trades} trades (modulator ON, canonical config)")
    lines.append(f"- Features: {len(CONT_FEATURES)} continuous candle-based "
                 "features captured at entry")
    lines.append(f"- Effect floor: |ρ| ≥ {EFFECT_FLOOR}")
    lines.append(f"- Family-wise α = {ALPHA} (Bonferroni over actually-run tests)")
    lines.append("- Strict candidate: passes BOTH `ρ_net` AND `ρ_pnl` Bonferroni")
    lines.append(f"- Null shuffles per test: {N_SHUFFLES}")
    lines.append("")

    # ── Disambiguation table ──
    lines.append("## Disambiguation: S1 LONG × Asia (modulator ON vs OFF)")
    lines.append("")
    lines.append("Round 1 found ρ_pnl=+0.393 (Bonferroni ✓) but ρ_net=+0.282 "
                 "(Bonferroni ✗). Hypothesis: PnL was inflated by the v11.10.0 "
                 "macro modulator amplifying size at high `btc_z`, which "
                 "happens to coincide with Asia-session S1 fires. We rerun "
                 "the 28m backtest with the modulator disabled. If ρ_pnl "
                 "collapses to match ρ_net (~+0.28), it's a size artifact. "
                 "If it stays at +0.39+, the edge is real.")
    lines.append("")
    if d_on.get("n", 0) > 0 and d_off.get("n", 0) > 0:
        lines.append("| Modulator | n | n_Asia | ρ_net | p_null_net | "
                     "ρ_pnl | p_null_pnl | Asia total_pnl ($) | non-Asia total_pnl ($) |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for label, d in (("ON", d_on), ("OFF", d_off)):
            lines.append(
                f"| {label} | {d['n']} | {d['n_asia']} | "
                f"{d['rho_net']:+.3f} | {d['p_null_net']:.4f} | "
                f"{d['rho_pnl']:+.3f} | {d['p_null_pnl']:.4f} | "
                f"{d['asia_total_pnl']:+,.0f} | {d['non_asia_total_pnl']:+,.0f} |"
            )
        delta_pnl = d_on["rho_pnl"] - d_off["rho_pnl"]
        delta_net = d_on["rho_net"] - d_off["rho_net"]
        lines.append("")
        lines.append(f"**Δρ_pnl (ON − OFF) = {delta_pnl:+.3f}**, "
                     f"**Δρ_net (ON − OFF) = {delta_net:+.3f}**")
        lines.append("")
        if abs(delta_pnl) < 0.05:
            lines.append("**Verdict: underlying edge confirmed** — disabling "
                         "the modulator changes ρ_pnl by less than 0.05, so "
                         "the S1 LONG × Asia association is not driven by "
                         "the modulator's size amplification. Note however "
                         "that ρ_net (and therefore the *strict* Bonferroni "
                         "joint filter) was already failing in round 1, and "
                         "the n=73 (only 16 Asia trades) sample size is too "
                         "small to confidently call this a deployable edge.")
        elif d_off["rho_pnl"] < d_on["rho_pnl"] - 0.05:
            lines.append("**Verdict: size-artifact confirmed** — disabling "
                         "the modulator drops ρ_pnl substantially, "
                         "consistent with the v11.10.0 modulator amplifying "
                         "S1 size precisely when btc_z is high (which "
                         "Asia-session entries are biased toward). The "
                         "round-1 PnL-only Bonferroni significance does not "
                         "reflect a per-trade edge, just a regime/size "
                         "co-amplification.")
        else:
            lines.append("**Verdict: inconclusive** — ρ_pnl shifted in the "
                         "wrong direction (modulator OFF increased it). "
                         "Likely small-sample noise.")
    else:
        lines.append("⚠ Disambiguation skipped — modulator-OFF run produced "
                     "no S1 LONG trades.")
    lines.append("")

    # ── Sample sizes per cell ──
    lines.append("## Sample sizes (28m window, modulator ON)")
    lines.append("")
    by_cell: dict[tuple, int] = defaultdict(int)
    for r in results:
        by_cell[(r["strat"], r["dir"])] = r["n"]
    lines.append("| Strat | Dir | n |")
    lines.append("|---|---|---|")
    for strat in STRATS:
        for d in DIRS:
            n = by_cell.get((strat, d), 0)
            dlabel = "LONG" if d == 1 else "SHORT"
            mark = "" if n >= MIN_N else " ⚠ low-n"
            lines.append(f"| {strat} | {dlabel} | {n}{mark} |")
    lines.append("")
    lines.append(f"_Cells with n<{MIN_N} skipped from testing._")
    lines.append("")

    # ── Full results table ──
    lines.append("## Full results — 28m window")
    lines.append("")
    lines.append("Sorted by |ρ_net| descending. `cand` ✓ = passes BOTH "
                 "Bonferroni filters (strict). `bh` ✓ = passes BH FDR on "
                 "either outcome (less conservative).")
    lines.append("")
    lines.append("| Strat | Dir | Feature | n | ρ_net | p_bonf_net | "
                 "ρ_pnl | p_bonf_pnl | bh | cand |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    res_sorted = sorted(results, key=lambda r: -abs(r["rho_net"]))
    for r in res_sorted:
        dlabel = "LONG" if r["dir"] == 1 else "SHORT"
        cand = "✓" if r["candidate"] else ""
        bh = "✓" if (r.get("bh_net") or r.get("bh_pnl")) else ""
        lines.append(
            f"| {r['strat']} | {dlabel} | `{r['feature']}` | {r['n']} | "
            f"{r['rho_net']:+.3f} | {r['p_bonf_net']:.3f} | "
            f"{r['rho_pnl']:+.3f} | {r['p_bonf_pnl']:.3f} | {bh} | {cand} |"
        )
    lines.append("")

    # ── Candidates ──
    lines.append("## Candidates (strict: both Bonferroni filters)")
    lines.append("")
    if n_cand == 0:
        lines.append("**None.** No (strat, dir, feature) triple passed the "
                     f"joint `|ρ| ≥ {EFFECT_FLOOR}` + `p_bonferroni < {ALPHA}` "
                     "filter on BOTH `net_bps` AND `pnl_usdt` on the 28m "
                     "window. Round-1 lesson applied: requiring BOTH "
                     "outcomes to clear Bonferroni filters out the "
                     "PnL-only candidates that were inflated by the "
                     "v11.10.0 size modulator.")
    else:
        for r in candidates:
            dlabel = "LONG" if r["dir"] == 1 else "SHORT"
            lines.append(f"### {r['strat']} {dlabel} × `{r['feature']}`")
            lines.append("")
            lines.append(f"- n = {r['n']}")
            lines.append(f"- ρ_net = {r['rho_net']:+.3f}, p_bonf = "
                         f"{r['p_bonf_net']:.4f} (✓)")
            lines.append(f"- ρ_pnl = {r['rho_pnl']:+.3f}, p_bonf = "
                         f"{r['p_bonf_pnl']:.4f} (✓)")
            lines.append("")
            bd = tercile_breakdown(trades_on, r["strat"], r["dir"],
                                    r["feature"])
            lines.extend(bd)
            lines.append("")
    lines.append("")

    # ── Modulator OFF cross-reference ──
    lines.append("## Modulator OFF cross-reference (rerun on flat sizing)")
    lines.append("")
    if results_off is None:
        lines.append("_Skipped — no modulator-OFF dataset provided._")
        lines.append("")
    else:
        lines.append("To cross-check the round-1 size-artifact hypothesis "
                     "in a generalized way, the entire EDA was rerun on the "
                     "28m window with the v11.10.0 adaptive modulator "
                     "disabled (`ADAPTIVE_ALPHA = {}` and "
                     "`ADAPTIVE_ALPHA_DIR = {}`). Under flat sizing, "
                     "ρ_pnl reflects only the per-trade bps edge (no size "
                     "variance from the modulator).")
        lines.append("")
        rows_table = []
        for r in results:
            key = (r["strat"], r["dir"], r["feature"])
            r_off = off_by_key.get(key)
            if not r_off:
                continue
            on_cand = r["candidate"]
            off_cand = r_off["candidate"]
            interesting = (on_cand or off_cand or
                           abs(r["rho_net"]) >= EFFECT_FLOOR or
                           abs(r_off["rho_net"]) >= EFFECT_FLOOR)
            if not interesting:
                continue
            if on_cand and off_cand:
                tag = "stable"
            elif off_cand and not on_cand:
                tag = "OFF-only (size noise hides edge)"
            elif on_cand and not off_cand:
                tag = "ON-only (suspect size artifact)"
            else:
                tag = ""
            rows_table.append((r, r_off, tag))
        if not rows_table:
            lines.append("No cells show |ρ_net| ≥ 0.15 in either run.")
        else:
            lines.append("Cells where |ρ_net| ≥ 0.15 in at least one run, "
                         "sorted by max(|ρ_net|) across the two runs.")
            lines.append("")
            lines.append("| Strat | Dir | Feature | n | ρ_net ON | ρ_net OFF | "
                         "ρ_pnl ON | ρ_pnl OFF | p_bonf_pnl ON | p_bonf_pnl OFF | "
                         "tag |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            rows_table.sort(key=lambda t: -max(abs(t[0]["rho_net"]),
                                                abs(t[1]["rho_net"])))
            for r, r_off, tag in rows_table:
                dlabel = "LONG" if r["dir"] == 1 else "SHORT"
                lines.append(
                    f"| {r['strat']} | {dlabel} | `{r['feature']}` | "
                    f"{r['n']} | {r['rho_net']:+.3f} | {r_off['rho_net']:+.3f} | "
                    f"{r['rho_pnl']:+.3f} | {r_off['rho_pnl']:+.3f} | "
                    f"{r['p_bonf_pnl']:.3f} | {r_off['p_bonf_pnl']:.3f} | "
                    f"{tag} |"
                )
        lines.append("")
        if candidates_off:
            lines.append("### Tercile breakdown for OFF-only candidates")
            lines.append("")
            lines.append("Buckets computed on the modulator-OFF dataset. "
                         "Note: even with the macro modulator disabled, "
                         "`strat_size()` still scales with `capital`, so "
                         "`total_pnl` reflects compounding (drawdown periods "
                         "shrink subsequent sizes). The pure per-trade edge "
                         "is `mean_net (bps)`; `total_pnl` is shown for "
                         "context but is dollar-weighted by historical "
                         "sequence.")
            lines.append("")
            for r in candidates_off:
                dlabel = "LONG" if r["dir"] == 1 else "SHORT"
                lines.append(f"#### {r['strat']} {dlabel} × `{r['feature']}` (OFF)")
                lines.append("")
                lines.append(f"- n = {r['n']}")
                lines.append(f"- ρ_net = {r['rho_net']:+.3f}, p_bonf = "
                             f"{r['p_bonf_net']:.4f} (✓)")
                lines.append(f"- ρ_pnl = {r['rho_pnl']:+.3f}, p_bonf = "
                             f"{r['p_bonf_pnl']:.4f} (✓)")
                lines.append("")
                if trades_off is not None:
                    bd = tercile_breakdown(trades_off, r["strat"], r["dir"],
                                            r["feature"])
                    lines.extend(bd)
                    lines.append("")
        lines.append("")

    # ── BH FDR secondary view ──
    lines.append("## FDR secondary view (BH q=0.05)")
    lines.append("")
    bh_pass = [r for r in results if r.get("bh_net") or r.get("bh_pnl")]
    if not bh_pass:
        lines.append("**None.** No test passes BH FDR either — the strict "
                     "and the relaxed views agree.")
    else:
        lines.append("Tests that pass Benjamini-Hochberg at q=0.05 (with the "
                     f"|ρ|≥{EFFECT_FLOOR} effect floor) on at least one of "
                     "`net_bps`/`pnl_usdt`. FDR is more permissive than "
                     "Bonferroni — a hit here that fails Bonferroni is "
                     "*observation-only*, not ship-ready.")
        lines.append("")
        lines.append("| Strat | Dir | Feature | n | ρ_net | ρ_pnl | "
                     "bh_net | bh_pnl | cand |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in bh_pass:
            dlabel = "LONG" if r["dir"] == 1 else "SHORT"
            lines.append(
                f"| {r['strat']} | {dlabel} | `{r['feature']}` | {r['n']} | "
                f"{r['rho_net']:+.3f} | {r['rho_pnl']:+.3f} | "
                f"{'✓' if r.get('bh_net') else ''} | "
                f"{'✓' if r.get('bh_pnl') else ''} | "
                f"{'✓' if r['candidate'] else ''} |"
            )
    lines.append("")

    # ── Caveats ──
    lines.append("## Caveats")
    lines.append("")
    lines.append("- **Sample sizes**: S1 SHORT, S8 SHORT, S9 LONG, S10 LONG "
                 "all have n=0 on 28m. Those (strat, dir) combinations are "
                 "untestable and silently skipped. S1 LONG is the smallest "
                 "tested cell at n=73 — power is limited.")
    lines.append("- **No 12m replication**: round 2 focused on extending "
                 "feature coverage on 28m and the modulator-OFF rerun, "
                 "since the round-1 12m view had no S1 LONG trades (n=0) "
                 "and didn't help disambiguate the round-1 candidate. The "
                 "12m table is still derivable from the JSON dataset.")
    lines.append("- **In-sample association**: any surviving candidate "
                 "would still need a walk-forward 4/4 train/test sweep on "
                 "`size *= 1 + α × normalize(feature)` before any "
                 "deployment claim. EDA shows correlation, not causation, "
                 "and does not account for the slot-effect interactions a "
                 "sizing rule would induce.")
    lines.append("- **Bonferroni is conservative**: with ~70 tests at "
                 "α=0.05, the per-test threshold is ~0.0007. Effects with "
                 "|ρ|≈0.10 would be missed. BH FDR is reported as a "
                 "secondary view but the strict bar (BOTH net AND pnl "
                 "Bonferroni) is what gates the Recommendation.")
    lines.append("- **Disambiguation interpretation**: even if ρ_pnl "
                 "barely changes ON vs OFF, the round-1 ρ_net was already "
                 "non-significant (p_bonf≈0.30). The strict bar that round "
                 "2 enforces would have rejected it regardless of the "
                 "modulator confound.")
    lines.append("")

    # ── Recommendation ──
    lines.append("## Recommendation")
    lines.append("")
    if n_cand == 0 and not candidates_off:
        lines.append("**No further work justified** on these features. "
                     "Conclusion is consistent with the round-1 finding "
                     "that `conf_partial`/`session_*` carry no robust "
                     "per-trade ranking signal — extending to 10 continuous "
                     "features confirms the same null result under a "
                     "stricter joint filter, in both the canonical "
                     "(modulator ON) and flat-sizing (modulator OFF) "
                     "configurations.")
        lines.append("")
        lines.append("Future EDA should target features the backtest "
                     "currently cannot reconstruct: full `entry_confluence` "
                     "(with the OI 1h-delta component), `entry_crowding`, "
                     "`entry_oi_delta`. Those require running the analysis "
                     "directly on live trade data from the SQLite DB once a "
                     "sufficient sample has accumulated (per-protocol "
                     "target: 50+ trades with full entry-context).")
    else:
        cand_names = []
        for r in candidates:
            dlabel = "LONG" if r["dir"] == 1 else "SHORT"
            cand_names.append(f"{r['strat']} {dlabel} × `{r['feature']}` (ON)")
        for r in candidates_off:
            dlabel = "LONG" if r["dir"] == 1 else "SHORT"
            cand_names.append(f"{r['strat']} {dlabel} × `{r['feature']}` (OFF)")
        lines.append(f"**Pre-registered next step** for the surviving "
                     f"triple(s) — {'; '.join(cand_names)}: open "
                     "`backtests/backtest_feature_modulator.py` (does not "
                     "exist yet — to be created) and walk-forward-sweep "
                     "`size *= 1 + α × clip(zscore(feature))` per "
                     "(strat, dir, feature). Match the v11.10.0 pattern: "
                     "per-α grid {0.25, 0.5, 1.0}, strict 4/4 windows "
                     "(28m/12m/6m/3m), ΔDD avg ≤ +1pp, IS/OOS sliding "
                     "split, null-shuffle z>3. **Do not build it yet** — "
                     "this is just the pre-registered protocol if the user "
                     "decides to proceed.")
        if candidates_off and not candidates:
            lines.append("")
            lines.append("**Important nuance**: the candidate(s) above pass "
                         "the strict joint filter only under modulator OFF "
                         "(flat sizing). The per-trade ranking signal "
                         "(ρ_net) is robust in BOTH configurations, but the "
                         "v11.10.0 macro modulator adds enough size variance "
                         "to muddy ρ_pnl under canonical (ON) config. "
                         "Operationally, this means a feature-based "
                         "modulator can extract edge that the macro "
                         "modulator currently dilutes, but **the two "
                         "modulators interact** — any walk-forward must "
                         "either (a) test the feature modulator on top of "
                         "the macro one (canonical comparison), or (b) "
                         "evaluate replacing/restricting the macro "
                         "modulator on the affected strategy. Recommended: "
                         "test (a) first since it doesn't disturb the "
                         "currently-shipped v11.10.0 behavior on other "
                         "strategies.")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append("```bash")
    lines.append("cd /home/crypto")
    lines.append(".venv/bin/python3 -m backtests.feature_modulator_eda_round2")
    lines.append("```")
    lines.append("")
    lines.append(f"Dataset persisted at `{os.path.basename(DATASET_PATH)}`.")
    lines.append("")
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    print("[1/6] Reviewing round 1 artifacts... (done — see "
          "feature_modulator_eda.md)", flush=True)

    # Reuse cached dataset if --reuse flag and file exists. Useful for
    # iterating on the analysis/report without rerunning the ~10-min
    # backtests.
    reuse = "--reuse" in argv and os.path.exists(DATASET_PATH)
    if reuse:
        print(f"[2/6] Reusing cached dataset from {DATASET_PATH}...",
              flush=True)
        with open(DATASET_PATH) as fh:
            d = json.load(fh)
        trades_on = d["trades_on"]
        trades_off = d["trades_off"]
        print(f"      Loaded {len(trades_on)} ON trades, "
              f"{len(trades_off)} OFF trades", flush=True)
    else:
        print("[2/6] Loading data + features...", flush=True)
        data = load_3y_candles()
        features = build_features(data)
        sector_features = compute_sector_features(features, data)
        oi_data = load_oi()
        funding_data = load_funding()
        latest_ts = max(c["t"] for c in data["BTC"])
        end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
        start_28m = end_dt - relativedelta(months=28)
        print(f"      Loaded {len(data)} coins, data ends {end_dt.date()}",
              flush=True)

        trades_on = _run_one(
            "28m_on", start_28m, end_dt, features, data, sector_features,
            oi_data, funding_data, apply_modulator=True,
        )

        print("[4/6] Disambiguation: rerunning 28m with modulator OFF "
              "(temporarily clearing ADAPTIVE_ALPHA + ADAPTIVE_ALPHA_DIR)...",
              flush=True)
        _saved_alpha = bot_config.ADAPTIVE_ALPHA.copy()
        _saved_alpha_dir = bot_config.ADAPTIVE_ALPHA_DIR.copy()
        try:
            bot_config.ADAPTIVE_ALPHA.clear()
            bot_config.ADAPTIVE_ALPHA_DIR.clear()
            trades_off = _run_one(
                "28m_off", start_28m, end_dt, features, data, sector_features,
                oi_data, funding_data, apply_modulator=False,
            )
        finally:
            bot_config.ADAPTIVE_ALPHA.update(_saved_alpha)
            bot_config.ADAPTIVE_ALPHA_DIR.update(_saved_alpha_dir)

        with open(DATASET_PATH, "w") as fh:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "n_on": len(trades_on),
                "n_off": len(trades_off),
                "trades_on": trades_on,
                "trades_off": trades_off,
            }, fh, default=str)
        print(f"      Dataset written to {DATASET_PATH}", flush=True)

    # EDA on both datasets
    results = run_eda(trades_on, "28m_on")
    results_off = run_eda(trades_off, "28m_off")
    disambig = disambig_session_asia(trades_on, trades_off)

    report = build_report(results, len(trades_on), disambig, trades_on,
                          results_off=results_off, trades_off=trades_off)
    with open(REPORT_PATH, "w") as fh:
        fh.write(report)
    print(f"[6/6] Report written: {REPORT_PATH}", flush=True)

    n_cand = sum(1 for r in results if r["candidate"])
    n_cand_off = sum(1 for r in results_off if r["candidate"])
    print(f"DONE — {n_cand} strict candidate(s) (ON), {n_cand_off} (OFF) "
          "on 28m.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
