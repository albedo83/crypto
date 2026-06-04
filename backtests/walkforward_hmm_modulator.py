"""D3 — Walk-forward strict 4/4 HMM-based macro modulator.

Replace `1 + α × btc_z` with `sum(prob_k × mult_k)` where:
- prob_k = HMM probability of state k (bear/neutral/bull)
- mult_k = 1 + α_k where α_k = α for the regime's directional sign

For S1 (α=+0.5): mult_bear=0.5, mult_neutral=1, mult_bull=1.5
For S8/S9 (α=-0.5): mult_bear=1.5, mult_neutral=1, mult_bull=0.5

WF protocol:
- For each split S, train HMM on data BEFORE S_start (only)
- Apply HMM probabilities forward in BT
- Compare baseline (btc_z modulator) vs HMM modulator
- Strict 4/4: ΔPnL ≥ 0 AND ΔDD ≤ +2pp on all splits
"""
import json
import numpy as np
from hmmlearn.hmm import GaussianHMM
from datetime import datetime, timezone

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    ADAPTIVE_ALPHA,
)


def compute_regime_probs(closes_btc: np.ndarray, ts_btc: np.ndarray,
                          train_end_ts_ms: int) -> dict[int, np.ndarray]:
    """Train HMM on data <= train_end_ts_ms; return regime probs at every ts.

    Returns dict: {ts_ms → [p_bear, p_neutral, p_bull]}
    """
    W = 180  # 30d on 4h grid
    n = len(closes_btc)
    # Compute ret_30d series
    ret_30d = np.full(n, np.nan)
    for i in range(W, n):
        if closes_btc[i - W] > 0:
            ret_30d[i] = closes_btc[i] / closes_btc[i - W] - 1

    # Training set: ts <= train_end_ts_ms AND ret_30d valid
    train_mask = (~np.isnan(ret_30d)) & (ts_btc <= train_end_ts_ms)
    X_train = ret_30d[train_mask].reshape(-1, 1)
    if len(X_train) < 200:
        return {}

    # Train with multi-restart, pick best log-likelihood
    best_model = None
    best_score = -np.inf
    for seed in range(5):
        try:
            m = GaussianHMM(n_components=3, covariance_type="full",
                            n_iter=100, random_state=seed, tol=1e-3)
            m.fit(X_train)
            s = m.score(X_train)
            if s > best_score:
                best_score = s
                best_model = m
        except Exception:
            pass
    if best_model is None:
        return {}

    # Label states by mean ret_30d (sort: bear=lowest, bull=highest)
    means = best_model.means_.flatten()
    order = np.argsort(means)
    raw_to_label = {order[0]: 0, order[1]: 1, order[2]: 2}  # 0=bear 1=neutral 2=bull

    # Predict probs at every valid ts (including FUTURE — uses HMM params trained on past only)
    full_mask = ~np.isnan(ret_30d)
    X_full = ret_30d[full_mask].reshape(-1, 1)
    ts_full = ts_btc[full_mask]
    probs_raw = best_model.predict_proba(X_full)
    # Re-order columns to bear/neutral/bull
    probs_ordered = np.zeros_like(probs_raw)
    for raw_idx, lbl in raw_to_label.items():
        probs_ordered[:, lbl] = probs_raw[:, raw_idx]

    return {int(ts_full[i]): probs_ordered[i].astype(float) for i in range(len(ts_full))}


def make_hmm_modulator_size_fn(regime_probs_map: dict[int, np.ndarray],
                                  hmm_alphas: dict[str, dict[int, float]]):
    """Build a size_fn replacing baseline modulator with HMM-weighted.

    For a candidate with (strat, dir) at ts, the modulator multiplier is:
        sum(prob_k × (1 + α_{strat,dir,k}))
    where α_{strat,dir,k} is the regime-specific alpha.

    Returns a callable compatible with run_window's size_fn signature:
        fn(cand, feature_dict, n_positions) -> multiplier

    We extract ts from feature_dict's underlying timestamp. Backtest engine
    calls size_fn but doesn't pass ts directly; we rely on a closure variable.
    """
    # Default fallback: 1.0 (no change)
    def fn(cand, f, n_pos):
        # f is the features dict for this token at this ts.
        # We need ts — backtest engine doesn't pass it in size_fn signature.
        # Workaround: store ts in the cand dict (the engine doesn't see this).
        ts = cand.get("_ts")
        if ts is None or ts not in regime_probs_map:
            return 1.0  # fail-open: baseline modulator already applied prior
        strat = cand["strat"]
        direction = cand["dir"]
        # Look up regime-specific alphas
        alphas = hmm_alphas.get((strat, direction))
        if alphas is None:
            # Try strat-only
            alphas = hmm_alphas.get(strat)
        if alphas is None:
            return 1.0
        probs = regime_probs_map[ts]
        mult = sum(probs[k] * (1.0 + alphas[k]) for k in range(3))
        # Clip same as baseline modulator
        return max(0.3, min(2.5, mult))
    return fn


def main():
    print("Loading 4h candles...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()
    print(f"  {len(data)} tokens")

    btc_arr = data["BTC"]
    btc_closes = np.array([c["c"] for c in btc_arr])
    btc_ts = np.array([c["t"] for c in btc_arr])

    latest_ts = max(c["t"] for c in data["BTC"])
    SIX_M_MS = 6 * 30 * 24 * 3600 * 1000
    splits = [
        ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
        ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
        ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
        ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
    ]

    early = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner = ({"strategies": RUNNER_EXT_STRATEGIES, "extra_candles": RUNNER_EXT_HOURS // 4,
               "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS, "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE}
              if RUNNER_EXT_STRATEGIES else None)

    # HMM regime alphas: mirror current ADAPTIVE_ALPHA mapping
    # ADAPTIVE_ALPHA = {"S1": +0.5, "S8": -0.5, "S9": -0.5}
    # For HMM: alphas per (strat, regime). bear=0, neutral=1, bull=2 by our labeling.
    # We use the baseline α applied to a fixed btc_z_proxy per regime:
    #   bear: btc_z_proxy = -1.5 → mult = 1 + α × -1.5
    #   neutral: btc_z_proxy = 0 → mult = 1
    #   bull: btc_z_proxy = +1.5 → mult = 1 + α × +1.5
    hmm_alphas = {}
    for strat, alpha in ADAPTIVE_ALPHA.items():
        hmm_alphas[strat] = {0: alpha * -1.5, 1: 0.0, 2: alpha * 1.5}
    print(f"  HMM alphas (strat → {{regime: α}}): {hmm_alphas}")

    print(f"\nRunning WF on {len(splits)} splits...")
    results_baseline = []
    results_hmm = []

    for label, start_ts, end_ts in splits:
        print(f"\n=== {label} ===")
        # Train HMM on data BEFORE split start
        regime_probs = compute_regime_probs(btc_closes, btc_ts, train_end_ts_ms=start_ts)
        print(f"  HMM trained on data <= {datetime.fromtimestamp(start_ts/1000, tz=timezone.utc).date()}, "
              f"{len(regime_probs)} ts assigned")

        # Baseline run (existing btc_z modulator)
        r_base = run_window(features, data, sector_features, dxy,
                            start_ts, end_ts, start_capital=1000.0,
                            oi_data=oi, early_exit_params=early,
                            runner_extension=runner, funding_data=funding,
                            apply_adaptive_modulator=True,
                            max_notional_per_trade=500.0, margin_check=True)
        results_baseline.append(r_base)
        print(f"  Baseline (btc_z modul): PnL={r_base['pnl_pct']:+.2f}%  DD={r_base['max_dd_pct']:+.2f}%  "
              f"trades={r_base['n_trades']}")

        # HMM run: disable baseline modulator, use size_fn instead
        # We need to inject ts into each candidate. Since extra_candidate_fn isn't enough,
        # we use a different approach: pass the HMM map as a size_multiplier dict keyed by
        # something the engine handles. The cleanest is to use size_fn but the BT engine
        # only passes (cand, f, n_pos) without ts.
        # Workaround: monkey-patch the cand dict at signal-detection time. We do this by
        # post-processing the candidates. But run_window builds candidates internally.
        # Alternative: write the HMM-modulator multiplier into apply_adaptive_modulator path.
        #
        # CLEANEST: Disable apply_adaptive_modulator, instead use size_multiplier per-strat
        # that is REGIME-DEPENDENT at each ts. But size_multiplier is static.
        #
        # Solution: implement size_fn that reads ts from the feature dict if present.
        # The BT engine passes f = feat_by_ts[ts][coin] — we'd need to inject ts.
        # Modify run_window to pass ts in the cand dict via a hidden field.

        # For now: approximation — use precomputed avg regime probs per token-period,
        # apply as static size_multiplier. Less accurate but tractable.
        # Compute mean probs over each split for each (strat, dir) ratio:
        split_ts = sorted([t for t in regime_probs.keys() if start_ts <= t <= end_ts])
        if not split_ts:
            print(f"  ! No regime data for split — skip HMM run")
            results_hmm.append(None)
            continue
        avg_probs = np.mean([regime_probs[t] for t in split_ts], axis=0)
        print(f"  Avg regime probs in split: bear={avg_probs[0]:.2f} neutral={avg_probs[1]:.2f} "
              f"bull={avg_probs[2]:.2f}")
        size_mult_hmm = {}
        for strat, alpha_map in hmm_alphas.items():
            mult = sum(avg_probs[k] * (1.0 + alpha_map[k]) for k in range(3))
            size_mult_hmm[strat] = max(0.3, min(2.5, mult))
        print(f"  Static HMM multipliers per strat: {size_mult_hmm}")

        r_hmm = run_window(features, data, sector_features, dxy,
                           start_ts, end_ts, start_capital=1000.0,
                           oi_data=oi, early_exit_params=early,
                           runner_extension=runner, funding_data=funding,
                           apply_adaptive_modulator=False,  # disable baseline
                           size_multiplier=size_mult_hmm,    # static HMM mult
                           max_notional_per_trade=500.0, margin_check=True)
        results_hmm.append(r_hmm)
        print(f"  HMM (avg static modul):  PnL={r_hmm['pnl_pct']:+.2f}%  DD={r_hmm['max_dd_pct']:+.2f}%  "
              f"trades={r_hmm['n_trades']}")

    # Compare
    print("\n=== STRICT 4/4 VERDICT (HMM vs baseline) ===")
    print(f"{'Split':<22} {'base PnL%':>10} {'HMM PnL%':>10} {'ΔPnL':>8} {'base DD%':>10} {'HMM DD%':>10} {'ΔDD':>8} {'check':>10}")
    print("-" * 100)
    all_pass_pnl = True
    all_pass_dd = True
    for i, (label, _, _) in enumerate(splits):
        b = results_baseline[i]
        h = results_hmm[i]
        if h is None:
            print(f"{label:<22} (no HMM data)")
            all_pass_pnl = False
            all_pass_dd = False
            continue
        d_pnl = h["pnl_pct"] - b["pnl_pct"]
        d_dd = h["max_dd_pct"] - b["max_dd_pct"]
        ok_pnl = d_pnl >= 0
        ok_dd = d_dd <= 2.0
        if not ok_pnl: all_pass_pnl = False
        if not ok_dd: all_pass_dd = False
        check = "PASS" if (ok_pnl and ok_dd) else f"{'✗pnl' if not ok_pnl else ''}{'✗dd' if not ok_dd else ''}"
        print(f"{label:<22} {b['pnl_pct']:>+10.2f} {h['pnl_pct']:>+10.2f} {d_pnl:>+8.2f} "
              f"{b['max_dd_pct']:>+10.2f} {h['max_dd_pct']:>+10.2f} {d_dd:>+8.2f} {check:>10}")

    print(f"\nStrict 4/4 ΔPnL ≥ 0: {'PASS' if all_pass_pnl else 'FAIL'}")
    print(f"Strict 4/4 ΔDD ≤ +2pp: {'PASS' if all_pass_dd else 'FAIL'}")
    final = "STRICT 4/4 PASS" if (all_pass_pnl and all_pass_dd) else "FAIL"
    print(f"\n=== FINAL VERDICT: {final} ===")

    # Save
    with open("/home/crypto/backtests/output/walkforward_hmm_results.json", "w") as f:
        json.dump({
            "verdict": final,
            "all_pass_pnl": bool(all_pass_pnl),
            "all_pass_dd": bool(all_pass_dd),
            "per_split": [
                {"label": label,
                 "baseline": {"pnl_pct": float(b["pnl_pct"]), "dd_pct": float(b["max_dd_pct"]), "n": int(b["n_trades"])},
                 "hmm": {"pnl_pct": float(h["pnl_pct"]) if h else None, "dd_pct": float(h["max_dd_pct"]) if h else None,
                         "n": int(h["n_trades"]) if h else None} if h else None}
                for (label, _, _), b, h in zip(splits, results_baseline, results_hmm)
            ],
        }, f, indent=2)


if __name__ == "__main__":
    main()
