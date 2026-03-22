"""Study 03 — Liquidation Cascades: after a cluster of liquidations, bounce or continuation?

Event study around liquidation clusters. Profiles by side, size, spread impact.
Run: python3 -m analysis.study_03_liquidations
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from analysis.db import fetch_df
from analysis.utils import (
    INSTRUMENT_IDS, ID_TO_SYMBOL,
    apply_dark_theme, savefig, OUTPUT_DIR,
)


def load_clusters() -> pd.DataFrame:
    """Load liquidation clusters via SQL function."""
    df = fetch_df("""
        SELECT * FROM liquidation_clusters(
            p_start := (SELECT min(exchange_ts) FROM liquidations),
            p_end   := (SELECT max(exchange_ts) FROM liquidations),
            p_gap_seconds := 60
        )
    """)
    if df.empty:
        print("  WARNING: No liquidation clusters found")
        return df
    df["cluster_start"] = pd.to_datetime(df["cluster_start"], utc=True)
    df["cluster_end"] = pd.to_datetime(df["cluster_end"], utc=True)
    df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)
    df["duration_s"] = (df["cluster_end"] - df["cluster_start"]).dt.total_seconds()
    return df


def event_study_price(clusters: pd.DataFrame, window_min: int = 5) -> pd.DataFrame:
    """Normalized price around each cluster: t-5min to t+5min."""
    all_paths = []
    for _, cl in clusters.iterrows():
        t0 = cl["cluster_start"]
        iid = cl["instrument_id"]
        # Get trades around the cluster
        trades = fetch_df("""
            SELECT exchange_ts, price
            FROM trades_raw
            WHERE instrument_id = $1
              AND exchange_ts BETWEEN $2 AND $3
            ORDER BY exchange_ts
        """, iid, t0 - pd.Timedelta(minutes=5), t0 + pd.Timedelta(minutes=5))
        if trades.empty or len(trades) < 20:
            continue
        trades["exchange_ts"] = pd.to_datetime(trades["exchange_ts"], utc=True)
        # Normalize: price at t0 = 0 (in bps)
        ref_price = trades.iloc[(trades["exchange_ts"] - t0).abs().argsort().iloc[0]]["price"]
        if ref_price == 0:
            continue
        trades["ret_bps"] = (trades["price"] / ref_price - 1) * 10000
        trades["offset_s"] = (trades["exchange_ts"] - t0).dt.total_seconds()
        trades["cluster_id"] = cl["cluster_id"]
        trades["dominant_side"] = cl["dominant_side"]
        trades["total_notional"] = cl["total_notional"]
        trades["symbol"] = cl["symbol"]
        all_paths.append(trades[["offset_s", "ret_bps", "cluster_id",
                                  "dominant_side", "total_notional", "symbol"]])
    if not all_paths:
        return pd.DataFrame()
    return pd.concat(all_paths, ignore_index=True)


def bounce_analysis(clusters: pd.DataFrame) -> pd.DataFrame:
    """Compute bounce rate: does price reverse after liquidation cluster?"""
    rows = []
    for _, cl in clusters.iterrows():
        t0 = cl["cluster_end"]  # measure from cluster end
        iid = cl["instrument_id"]
        prices = fetch_df("""
            SELECT exchange_ts, price
            FROM trades_raw
            WHERE instrument_id = $1
              AND exchange_ts BETWEEN $2 AND $3
            ORDER BY exchange_ts
        """, iid, t0 - pd.Timedelta(seconds=10), t0 + pd.Timedelta(minutes=5))
        if prices.empty or len(prices) < 10:
            continue
        prices["exchange_ts"] = pd.to_datetime(prices["exchange_ts"], utc=True)
        # Price at cluster end
        pre = prices[prices["exchange_ts"] <= t0]
        post = prices[prices["exchange_ts"] > t0]
        if pre.empty or post.empty:
            continue
        ref_price = pre.iloc[-1]["price"]
        if ref_price == 0:
            continue

        row = {
            "cluster_id": cl["cluster_id"],
            "symbol": cl["symbol"],
            "dominant_side": cl["dominant_side"],
            "liq_count": cl["liq_count"],
            "total_notional": cl["total_notional"],
        }
        for secs in (60, 300):
            future = post[post["exchange_ts"] <= t0 + pd.Timedelta(seconds=secs)]
            if future.empty:
                row[f"ret_{secs}s_bps"] = np.nan
                row[f"bounce_{secs}s"] = np.nan
                continue
            end_price = future.iloc[-1]["price"]
            ret = (end_price / ref_price - 1) * 10000
            row[f"ret_{secs}s_bps"] = ret
            # Bounce = reversal from dominant liquidation direction
            # SELL-dominant → expect price was pushed down → bounce = positive return
            # BUY-dominant → expect price was pushed up → bounce = negative return
            if cl["dominant_side"] == "SELL":
                row[f"bounce_{secs}s"] = ret > 0
            else:
                row[f"bounce_{secs}s"] = ret < 0
        rows.append(row)
    return pd.DataFrame(rows)


def spread_during_clusters(clusters: pd.DataFrame) -> pd.DataFrame:
    """Spread behavior during liquidation clusters."""
    rows = []
    for _, cl in clusters.iterrows():
        iid = cl["instrument_id"]
        spreads = fetch_df("""
            SELECT avg(spread_bps) AS avg_spread_during
            FROM book_tob
            WHERE instrument_id = $1
              AND exchange_ts BETWEEN $2 AND $3
        """, iid, cl["cluster_start"], cl["cluster_end"])
        baseline = fetch_df("""
            SELECT avg(spread_bps) AS avg_spread_baseline
            FROM book_tob
            WHERE instrument_id = $1
              AND exchange_ts BETWEEN $2 AND $3
        """, iid, cl["cluster_start"] - pd.Timedelta(minutes=5), cl["cluster_start"])
        if spreads.empty or baseline.empty:
            continue
        rows.append({
            "cluster_id": cl["cluster_id"],
            "symbol": cl["symbol"],
            "spread_during_bps": float(spreads.iloc[0]["avg_spread_during"] or 0),
            "spread_baseline_bps": float(baseline.iloc[0]["avg_spread_baseline"] or 0),
            "spread_ratio": (float(spreads.iloc[0]["avg_spread_during"] or 0)
                           / float(baseline.iloc[0]["avg_spread_baseline"] or 1)),
        })
    return pd.DataFrame(rows)


def plot_event_study(paths: pd.DataFrame) -> None:
    """Average normalized price path around liquidation clusters."""
    if paths.empty:
        return
    for side in ("SELL", "BUY"):
        sub = paths[paths["dominant_side"] == side]
        if sub.empty:
            continue
        # Bin into 5s intervals and average
        sub = sub.copy()
        sub["bin"] = (sub["offset_s"] / 5).round() * 5
        avg = sub.groupby("bin")["ret_bps"].agg(["mean", "std", "count"])
        avg = avg[avg["count"] >= 3]

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(avg.index, avg["mean"], linewidth=2,
                color="#e74c3c" if side == "SELL" else "#2ecc71",
                label=f"Mean ({side}-dominant)")
        ax.fill_between(avg.index,
                       avg["mean"] - avg["std"] / np.sqrt(avg["count"]),
                       avg["mean"] + avg["std"] / np.sqrt(avg["count"]),
                       alpha=0.2)
        ax.axvline(0, color="yellow", linewidth=1.5, linestyle="--", label="Cluster start")
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_xlabel("Time offset from cluster start (seconds)")
        ax.set_ylabel("Cumulative return (bps)")
        ax.set_title(f"Liquidation Event Study — {side}-dominant clusters")
        ax.legend()
        plt.tight_layout()
        savefig(f"liq_event_study_{side}.png")


def plot_bounce_by_size(bounce_df: pd.DataFrame) -> None:
    """Bounce rate vs cluster size."""
    if bounce_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, secs in zip(axes, (60, 300)):
        col = f"bounce_{secs}s"
        valid = bounce_df.dropna(subset=[col]).copy()
        if valid.empty:
            continue
        # Size terciles
        valid["size_grp"] = pd.qcut(valid["total_notional"], 3,
                                     labels=["Small", "Medium", "Large"],
                                     duplicates="drop")
        rates = valid.groupby("size_grp")[col].mean()
        ax.bar(rates.index, rates.values, color=["#3498db", "#f39c12", "#e74c3c"],
               edgecolor="white", linewidth=0.5)
        ax.axhline(0.5, color="white", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_ylabel("Bounce Rate")
        ax.set_title(f"Bounce Rate at {secs}s by Cluster Size")
        ax.set_ylim(0, 1)
    plt.tight_layout()
    savefig("liq_bounce_by_size.png")


def run() -> dict:
    """Run full liquidation cascade study."""
    apply_dark_theme()
    print("=" * 60)
    print("STUDY 03 — Liquidation Cascades")
    print("=" * 60)

    print("\nLoading liquidation clusters...")
    clusters = load_clusters()
    if clusters.empty:
        print("  No clusters found — skipping study")
        return {"clusters": clusters}
    print(f"  {len(clusters)} clusters across {clusters['symbol'].nunique()} symbols")
    print(f"  SELL-dominant: {(clusters['dominant_side']=='SELL').sum()}, "
          f"BUY-dominant: {(clusters['dominant_side']=='BUY').sum()}")
    print(f"  Notional range: {clusters['total_notional'].min():.0f} — "
          f"{clusters['total_notional'].max():.0f}")
    clusters.to_csv(f"{OUTPUT_DIR}/liq_clusters.csv", index=False)

    # Use top clusters by notional for per-cluster analyses (avoid 2500+ queries)
    top_n = 100
    top_clusters = clusters.nlargest(top_n, "total_notional")
    print(f"  Using top {len(top_clusters)} clusters by notional for event study")

    print("\n── Event Study ──")
    paths = event_study_price(top_clusters)
    print(f"  {paths['cluster_id'].nunique()} clusters with price paths" if not paths.empty
          else "  No price paths available")

    print("\n── Bounce Analysis ──")
    bounce = bounce_analysis(top_clusters)
    if not bounce.empty:
        for secs in (60, 300):
            col = f"bounce_{secs}s"
            valid = bounce[col].dropna()
            rate = valid.mean() if len(valid) > 0 else np.nan
            print(f"  {secs}s bounce rate: {rate:.1%} (n={len(valid)})")
        bounce.to_csv(f"{OUTPUT_DIR}/liq_bounce.csv", index=False)
        print(bounce[["cluster_id", "symbol", "dominant_side", "liq_count",
                       "total_notional", "ret_60s_bps", "bounce_60s",
                       "ret_300s_bps", "bounce_300s"]].to_string(index=False, float_format="%.1f"))

    print("\n── Spread During Clusters ──")
    spread = spread_during_clusters(top_clusters)
    if not spread.empty:
        print(f"  Mean spread ratio (during/baseline): {spread['spread_ratio'].mean():.2f}x")
        spread.to_csv(f"{OUTPUT_DIR}/liq_spread.csv", index=False)

    print("\nGenerating plots...")
    plot_event_study(paths)
    if not bounce.empty:
        plot_bounce_by_size(bounce)

    return {"clusters": clusters, "bounce": bounce, "paths": paths, "spread": spread}


if __name__ == "__main__":
    run()
