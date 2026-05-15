"""basket_haircut_eda — risk-side EDA v2.

Round 2 of the risk-side analysis. The first pass showed decile 1 lift
(1.84x) was driven entirely by n_pos=1 periods (eff_n=1.0 by construction
— not a correlation signal, just a "single position" signal).

This round restricts the analysis to ts where n_pos ≥ 2 (when basket
correlation could plausibly matter for risk) AND uses NORMALIZED eff_n
= eff_n / n_pos so we measure dilution-quality independent of basket size.

Also tests:
  - eff_n_W vs forward N-candle equity DRAWDOWN (continuous, not binary)
  - both at-ts and lagged

Reads:  backtests/basket_haircut_eda_data/basket_ts_28m.jsonl
Writes: backtests/basket_haircut_eda_data/riskside_summary_v2.json
"""
from __future__ import annotations
import json
from statistics import mean, median, stdev

import numpy as np
from scipy.stats import spearmanr, mannwhitneyu  # type: ignore


DATA_DIR = "/home/crypto/backtests/basket_haircut_eda_data"
WINDOWS = (7, 14, 30)
FWD_HORIZONS = (6, 12, 21, 42)  # 1d, 2d, 3.5d, 7d


def load_ts(label: str) -> list[dict]:
    out = []
    with open(f"{DATA_DIR}/basket_ts_{label}.jsonl") as fh:
        for line in fh:
            out.append(json.loads(line))
    return out


def forward_drawdown(eq: np.ndarray, idx: int, horizon: int) -> float:
    """Worst drawdown from eq[idx] within next `horizon` candles (in pct)."""
    end = min(len(eq), idx + horizon + 1)
    fwd = eq[idx:end]
    if len(fwd) < 2 or eq[idx] <= 0:
        return 0.0
    return float(min(0.0, (fwd.min() - eq[idx]) / eq[idx] * 100))


def main():
    rows = load_ts("28m")
    eq = np.array([r["capital"] + r["basket_unreal"] for r in rows], dtype=float)
    print(f"Loaded {len(rows)} ts. Eq: {eq[0]:.0f} → {eq[-1]:.0f}")

    # ── 1. Filter to ts where n_pos ≥ 2 ──
    valid = [(i, r) for i, r in enumerate(rows)
             if r["n_pos"] >= 2 and r["eff_n_7d"] is not None]
    print(f"Valid ts (n_pos ≥ 2 AND eff_n_7d defined): {len(valid)}")

    summary = {"n_valid": len(valid), "windows": {}}

    # ── 2. Spearman: eff_n vs forward DD over multiple horizons ──
    print("\n=== Spearman correlation: eff_n at-ts vs forward DD ===")
    print(f"{'Window':>8} {'Horizon (candles)':>18} {'rho_raw':>10} "
          f"{'rho_norm':>10} {'p (raw)':>10} {'n':>6}")
    for w in WINDOWS:
        key = f"eff_n_{w}d"
        win_results = {"by_horizon": {}}
        for h in FWD_HORIZONS:
            x_raw = []
            x_norm = []
            y = []
            for i, r in valid:
                v = r[key]
                if v is None:
                    continue
                fdd = forward_drawdown(eq, i, h)
                x_raw.append(v)
                x_norm.append(v / r["n_pos"])
                y.append(fdd)
            if len(x_raw) < 100:
                continue
            rho_raw, p_raw = spearmanr(x_raw, y)
            rho_norm, p_norm = spearmanr(x_norm, y)
            print(f"  {w:>5}d {h:>18}  {rho_raw:>+8.4f}  {rho_norm:>+8.4f}  "
                  f"{p_raw:>10.3g} {len(x_raw):>6}")
            win_results["by_horizon"][h] = {
                "rho_raw": float(rho_raw), "p_raw": float(p_raw),
                "rho_norm": float(rho_norm), "p_norm": float(p_norm),
                "n": len(x_raw),
            }
        summary["windows"][f"{w}d"] = win_results

    # NOTE on direction: if the haircut hypothesis ("low eff_n → bad future")
    # is true, we expect rho_raw POSITIVE: higher eff_n → less negative fwd DD.
    # In practice fwd_dd is ≤ 0, so positive rho means higher eff_n correlates
    # with less-negative (better) outcomes.

    # ── 3. Decile analysis on NORMALIZED eff_n ──
    print("\n=== Decile analysis on eff_n_W / n_pos (forward 42-candle worst DD) ===")
    H = 42
    for w in WINDOWS:
        key = f"eff_n_{w}d"
        rows_v = []
        for i, r in valid:
            v = r[key]
            if v is None:
                continue
            rows_v.append((v / r["n_pos"], forward_drawdown(eq, i, H), i, r["n_pos"]))
        if len(rows_v) < 100:
            continue
        rows_v.sort(key=lambda x: x[0])
        n_v = len(rows_v)
        dsize = n_v // 10
        print(f"\n  {w}d  (n={n_v}, decile_size={dsize})")
        print(f"    {'Decile':>6} {'norm range':>14} {'mean fwd DD':>13} "
              f"{'median':>9} {'P5':>8} {'avg n_pos':>10}")
        dec_results = []
        for d in range(10):
            lo = d * dsize
            hi = (d + 1) * dsize if d < 9 else n_v
            chunk = rows_v[lo:hi]
            fdds = [c[1] for c in chunk]
            npos = [c[3] for c in chunk]
            xs = [c[0] for c in chunk]
            print(f"    {d+1:>6} {xs[0]:.3f}-{xs[-1]:.3f} "
                  f"{mean(fdds):>+10.2f}% {median(fdds):>+8.2f}% "
                  f"{np.percentile(fdds, 5):>+7.2f}% {mean(npos):>9.2f}")
            dec_results.append({
                "decile": d + 1, "norm_lo": xs[0], "norm_hi": xs[-1],
                "mean_fwd_dd": mean(fdds), "median_fwd_dd": median(fdds),
                "p5_fwd_dd": float(np.percentile(fdds, 5)),
                "avg_n_pos": mean(npos),
            })
        summary["windows"][f"{w}d"]["deciles_norm"] = dec_results

    # ── 4. Direct test of haircut premise: if we had haircut by eff_n
    # at ts, what would forward DD have looked like in the LOW vs HIGH halves? ──
    print("\n=== High vs low half test (eff_n_W / n_pos) ===")
    print(f"  {'Window':>8} {'horizon':>9} {'low mean DD':>13} {'high mean DD':>14} "
          f"{'delta':>9} {'MW p':>10}")
    for w in WINDOWS:
        key = f"eff_n_{w}d"
        for h in FWD_HORIZONS:
            recs = []
            for i, r in valid:
                v = r[key]
                if v is None:
                    continue
                recs.append((v / r["n_pos"], forward_drawdown(eq, i, h)))
            if len(recs) < 100:
                continue
            recs.sort(key=lambda x: x[0])
            half = len(recs) // 2
            low = [r[1] for r in recs[:half]]
            high = [r[1] for r in recs[half:]]
            try:
                stat, p = mannwhitneyu(low, high, alternative="less")
            except ValueError:
                p = float("nan")
            print(f"  {w:>7}d {h:>9} {mean(low):>+10.2f}% {mean(high):>+11.2f}% "
                  f"{mean(low) - mean(high):>+7.2f} {p:>10.4g}")

    out_path = f"{DATA_DIR}/riskside_summary_v2.json"
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=float)
    print(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
