"""S5 cluster EDA — phase 1 + phase 2.

Phase 1: Discover statistically separable archetypes among S5 trades at entry
(separately for LONG and SHORT), using K-Means and GMM with K in {2, 3, 4}.
Validate via silhouette score, BIC, and bootstrap stability (ARI).

Phase 2 (conditional on phase 1 passing the gate): Per-cluster exit dynamics —
WR, mean/median net_bps, mfe_bps, mae_bps, hold_hours, exit reason distribution.
Mann-Whitney U test on net_bps and mfe_bps between clusters.

Research only — no impact on running bot.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import mannwhitneyu
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

DATASET = Path("/home/crypto/backtests/feature_modulator_dataset_r2.json")
ARTIFACTS = Path("/home/crypto/backtests/s5_cluster_artifacts.json")

# Clustering inputs (3D feature space at entry)
FEATURES = ["entry_vol_z", "entry_range_pct", "entry_lead"]

# Validation thresholds (per spec)
SILHOUETTE_GATE = 0.30
ARI_GATE = 0.65

RNG_SEED = 42


def load_s5_trades() -> list[dict[str, Any]]:
    raw = json.loads(DATASET.read_text())
    # Trades_on is the modulator-ON (current production) set
    s5 = [t for t in raw["trades_on"] if t.get("strat") == "S5"]
    return s5


def hold_hours(t: dict[str, Any]) -> float:
    return (t["exit_t"] - t["entry_t"]) / 1000.0 / 3600.0


def build_feature_matrix(trades: list[dict[str, Any]]) -> np.ndarray:
    rows = []
    for t in trades:
        ef = t.get("entry_feats") or {}
        row = [float(ef.get(k, 0.0)) for k in FEATURES]
        rows.append(row)
    return np.asarray(rows, dtype=float)


def fit_kmeans(X: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    km = KMeans(n_clusters=k, random_state=RNG_SEED, n_init=10)
    labels = km.fit_predict(X)
    sil = silhouette_score(X, labels) if k > 1 and len(set(labels)) > 1 else float("nan")
    return labels, sil


def fit_gmm(X: np.ndarray, k: int) -> tuple[np.ndarray, float, float]:
    gmm = GaussianMixture(n_components=k, random_state=RNG_SEED, n_init=5, covariance_type="full")
    gmm.fit(X)
    labels = gmm.predict(X)
    sil = silhouette_score(X, labels) if k > 1 and len(set(labels)) > 1 else float("nan")
    bic = gmm.bic(X)
    return labels, sil, bic


def bootstrap_ari(
    X: np.ndarray, base_labels: np.ndarray, k: int, *, algo: str, n_iter: int = 50, frac: float = 0.8
) -> float:
    """Resample 80% with replacement, refit, compute ARI between bootstrap and base labels
    over the sampled indices."""
    rng = np.random.default_rng(RNG_SEED)
    n = X.shape[0]
    n_sample = int(frac * n)
    aris = []
    for _ in range(n_iter):
        idx = rng.choice(n, size=n_sample, replace=True)
        Xb = X[idx]
        try:
            if algo == "kmeans":
                km = KMeans(n_clusters=k, random_state=RNG_SEED, n_init=5)
                lab_b = km.fit_predict(Xb)
            else:
                gmm = GaussianMixture(n_components=k, random_state=RNG_SEED, n_init=3, covariance_type="full")
                gmm.fit(Xb)
                lab_b = gmm.predict(Xb)
            ari = adjusted_rand_score(base_labels[idx], lab_b)
            aris.append(ari)
        except Exception:
            continue
    return float(np.mean(aris)) if aris else float("nan")


def cluster_one_direction(direction_name: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Run K=2,3,4 with K-Means + GMM, validate, pick best K."""
    print(f"[1/3] Phase 1: clustering {direction_name} (n={len(trades)})...")
    raw_X = build_feature_matrix(trades)
    scaler = StandardScaler()
    X = scaler.fit_transform(raw_X)

    results: dict[int, dict[str, Any]] = {}
    for k in (2, 3, 4):
        km_labels, km_sil = fit_kmeans(X, k)
        gm_labels, gm_sil, gm_bic = fit_gmm(X, k)
        results[k] = {
            "kmeans": {
                "silhouette": float(km_sil),
                "labels": km_labels.tolist(),
                "size_per_cluster": dict(Counter(km_labels.tolist())),
            },
            "gmm": {
                "silhouette": float(gm_sil),
                "bic": float(gm_bic),
                "labels": gm_labels.tolist(),
                "size_per_cluster": dict(Counter(gm_labels.tolist())),
            },
        }
        print(
            f"  K={k}: KM sil={km_sil:.3f} sizes={dict(Counter(km_labels.tolist()))} | "
            f"GMM sil={gm_sil:.3f} BIC={gm_bic:.0f} sizes={dict(Counter(gm_labels.tolist()))}"
        )

    # Pick best K: highest silhouette across both algos
    best_k = 2
    best_algo = "kmeans"
    best_sil = -1.0
    for k in (2, 3, 4):
        for algo in ("kmeans", "gmm"):
            s = results[k][algo]["silhouette"]
            if not math.isnan(s) and s > best_sil:
                best_sil = s
                best_k = k
                best_algo = algo

    # Also check BIC plateau for GMM
    bic_2 = results[2]["gmm"]["bic"]
    bic_3 = results[3]["gmm"]["bic"]
    bic_4 = results[4]["gmm"]["bic"]
    bic_drop_2_to_3 = bic_2 - bic_3  # positive = K=3 better than K=2 by BIC
    bic_drop_3_to_4 = bic_3 - bic_4

    # Bootstrap stability on the chosen (K, algo)
    base_labels = np.asarray(results[best_k][best_algo]["labels"])
    ari = bootstrap_ari(X, base_labels, best_k, algo=best_algo, n_iter=50)
    print(f"  Best: {best_algo} K={best_k} sil={best_sil:.3f} bootstrap_ARI={ari:.3f}")

    # Gate: silhouette >= 0.30 AND ARI >= 0.65
    gate_pass = (best_sil >= SILHOUETTE_GATE) and (ari >= ARI_GATE)

    return {
        "direction": direction_name,
        "n_trades": len(trades),
        "feature_means_raw": dict(zip(FEATURES, raw_X.mean(axis=0).tolist())),
        "feature_stds_raw": dict(zip(FEATURES, raw_X.std(axis=0).tolist())),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "X_scaled": X.tolist(),
        "results_by_k": results,
        "best_k": best_k,
        "best_algo": best_algo,
        "best_silhouette": best_sil,
        "bootstrap_ari": ari,
        "bic_drop_2_to_3": bic_drop_2_to_3,
        "bic_drop_3_to_4": bic_drop_3_to_4,
        "gate_pass": gate_pass,
        "best_labels": results[best_k][best_algo]["labels"],
    }


def cluster_profile(trades: list[dict[str, Any]], labels: list[int]) -> dict[int, dict[str, Any]]:
    """Per-cluster exit profile."""
    by_cluster: dict[int, list[dict[str, Any]]] = {}
    for t, c in zip(trades, labels):
        by_cluster.setdefault(c, []).append(t)

    profile = {}
    for c, items in sorted(by_cluster.items()):
        net = np.array([t["net"] for t in items])
        mfe = np.array([t["mfe_bps"] for t in items])
        mae = np.array([t["mae_bps"] for t in items])
        hold = np.array([hold_hours(t) for t in items])
        reasons = Counter(t["reason"] for t in items)
        # WR by net_bps > 0
        wr = float((net > 0).mean()) * 100
        # Entry features means
        ef_keys = list(items[0]["entry_feats"].keys())
        ef_means = {k: float(np.mean([t["entry_feats"].get(k, 0.0) for t in items])) for k in ef_keys}

        profile[c] = {
            "n": len(items),
            "wr_pct": wr,
            "net_mean_bps": float(net.mean()),
            "net_median_bps": float(np.median(net)),
            "net_std_bps": float(net.std()),
            "mfe_mean_bps": float(mfe.mean()),
            "mfe_median_bps": float(np.median(mfe)),
            "mae_mean_bps": float(mae.mean()),
            "mae_median_bps": float(np.median(mae)),
            "hold_mean_h": float(hold.mean()),
            "hold_median_h": float(np.median(hold)),
            "reasons": dict(reasons),
            "entry_feature_means": ef_means,
        }
    return profile


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Cliff's delta effect size: probability(a>b) - probability(a<b). Range [-1, 1]."""
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        return float("nan")
    gt = 0
    lt = 0
    for x in a:
        gt += int((b < x).sum())
        lt += int((b > x).sum())
    return (gt - lt) / (n_a * n_b)


def compare_clusters(profile: dict[int, dict[str, Any]], trades: list[dict[str, Any]], labels: list[int]) -> dict[str, Any]:
    """Pairwise MW U tests on net_bps and mfe_bps between clusters."""
    by_cluster: dict[int, list[dict[str, Any]]] = {}
    for t, c in zip(trades, labels):
        by_cluster.setdefault(c, []).append(t)
    clusters = sorted(by_cluster.keys())
    comparisons = []
    for i, a in enumerate(clusters):
        for b in clusters[i + 1:]:
            net_a = np.array([t["net"] for t in by_cluster[a]])
            net_b = np.array([t["net"] for t in by_cluster[b]])
            mfe_a = np.array([t["mfe_bps"] for t in by_cluster[a]])
            mfe_b = np.array([t["mfe_bps"] for t in by_cluster[b]])
            hold_a = np.array([hold_hours(t) for t in by_cluster[a]])
            hold_b = np.array([hold_hours(t) for t in by_cluster[b]])

            try:
                _, p_net = mannwhitneyu(net_a, net_b, alternative="two-sided")
            except ValueError:
                p_net = 1.0
            try:
                _, p_mfe = mannwhitneyu(mfe_a, mfe_b, alternative="two-sided")
            except ValueError:
                p_mfe = 1.0
            try:
                _, p_hold = mannwhitneyu(hold_a, hold_b, alternative="two-sided")
            except ValueError:
                p_hold = 1.0

            comparisons.append({
                "pair": (a, b),
                "p_mw_net": float(p_net),
                "p_mw_mfe": float(p_mfe),
                "p_mw_hold": float(p_hold),
                "cliffs_delta_net": cliffs_delta(net_a, net_b),
                "cliffs_delta_mfe": cliffs_delta(mfe_a, mfe_b),
                "cliffs_delta_hold": cliffs_delta(hold_a, hold_b),
            })
    return {"pairwise": comparisons}


def phase2_for_direction(direction_name: str, trades: list[dict[str, Any]], best_labels: list[int]) -> dict[str, Any]:
    print(f"[2/3] Phase 2: exit dynamics for {direction_name}")
    profile = cluster_profile(trades, best_labels)
    for c, p in sorted(profile.items()):
        print(
            f"  Cluster {c}: n={p['n']} WR={p['wr_pct']:.1f}% net_mean={p['net_mean_bps']:+.0f}bps "
            f"mfe={p['mfe_mean_bps']:+.0f} mae={p['mae_mean_bps']:+.0f} reasons={p['reasons']}"
        )
    compare = compare_clusters(profile, trades, best_labels)
    for cmp in compare["pairwise"]:
        print(
            f"  Pair {cmp['pair']}: p_net={cmp['p_mw_net']:.4f} p_mfe={cmp['p_mw_mfe']:.4f} "
            f"delta_net={cmp['cliffs_delta_net']:+.3f}"
        )
    return {"profile_by_cluster": profile, "comparisons": compare}


def main() -> None:
    print("[1/3] Phase 1: loading S5 trades from feature_modulator_dataset_r2.json")
    all_s5 = load_s5_trades()
    long_trades = [t for t in all_s5 if t["dir"] == 1]
    short_trades = [t for t in all_s5 if t["dir"] == -1]
    print(f"  loaded {len(all_s5)} S5 trades (LONG={len(long_trades)}, SHORT={len(short_trades)})")

    out: dict[str, Any] = {"phase1": {}, "phase2": {}}

    long_p1 = cluster_one_direction("S5 LONG", long_trades)
    short_p1 = cluster_one_direction("S5 SHORT", short_trades)
    # Strip large arrays for artifact compactness
    for p in (long_p1, short_p1):
        p["X_scaled"] = None  # don't persist
    out["phase1"]["LONG"] = long_p1
    out["phase1"]["SHORT"] = short_p1

    gate_long = long_p1["gate_pass"]
    gate_short = short_p1["gate_pass"]
    print(f"[1/3] Phase 1 gate: LONG={'PASS' if gate_long else 'FAIL'} SHORT={'PASS' if gate_short else 'FAIL'}")

    # Phase 2 conditional — run for any direction that passes
    if gate_long:
        out["phase2"]["LONG"] = phase2_for_direction("S5 LONG", long_trades, long_p1["best_labels"])
    if gate_short:
        out["phase2"]["SHORT"] = phase2_for_direction("S5 SHORT", short_trades, short_p1["best_labels"])

    ARTIFACTS.write_text(json.dumps(out, indent=2, default=str))
    print(f"  artifacts -> {ARTIFACTS}")


if __name__ == "__main__":
    main()
