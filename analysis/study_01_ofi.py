"""Study 01 — Order Flow Imbalance: does buy/sell imbalance predict forward returns?

Metrics: quintile spread (Q5-Q1), Spearman rho, hit rate, by symbol and session.
Run: python3 -m analysis.study_01_ofi
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from analysis.db import fetch_df
from analysis.utils import (
    INSTRUMENT_IDS, ID_TO_SYMBOL, SESSIONS,
    add_session_column, apply_dark_theme, savefig, OUTPUT_DIR,
    quintile_stats,
)


def load_ofi() -> pd.DataFrame:
    """Load order_flow_1m and compute forward returns."""
    df = fetch_df("""
        SELECT bucket, instrument_id, ofi_ratio, net_flow,
               total_notional, close_price, trade_count
        FROM order_flow_1m
        ORDER BY instrument_id, bucket
    """)
    if df.empty:
        raise RuntimeError("order_flow_1m is empty — run REFRESH MATERIALIZED VIEW first")
    df["bucket"] = pd.to_datetime(df["bucket"], utc=True)
    results = []
    for iid in df["instrument_id"].unique():
        sub = df[df["instrument_id"] == iid].sort_values("bucket").copy()
        sub["ret_1m"] = sub["close_price"].pct_change(1).shift(-1)
        sub["ret_5m"] = sub["close_price"].pct_change(5).shift(-5)
        sub["ret_10m"] = sub["close_price"].pct_change(10).shift(-10)
        results.append(sub)
    df = pd.concat(results, ignore_index=True)
    add_session_column(df)
    df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)
    return df


def analyze_quintiles(df: pd.DataFrame) -> pd.DataFrame:
    """Quintile analysis: OFI quintile → mean forward return."""
    rows = []
    for sym in df["symbol"].unique():
        for horizon in ("ret_1m", "ret_5m", "ret_10m"):
            sub = df[df["symbol"] == sym].dropna(subset=["ofi_ratio", horizon])
            if len(sub) < 100:
                continue
            sub = sub.copy()
            sub["q"] = pd.qcut(sub["ofi_ratio"], 5, labels=False, duplicates="drop")
            means = sub.groupby("q")[horizon].mean()
            spread = means.iloc[-1] - means.iloc[0] if len(means) >= 2 else np.nan
            rows.append({
                "symbol": sym,
                "horizon": horizon,
                "Q1_mean_bps": means.iloc[0] * 10000 if len(means) >= 1 else np.nan,
                "Q5_mean_bps": means.iloc[-1] * 10000 if len(means) >= 2 else np.nan,
                "spread_Q5_Q1_bps": spread * 10000,
            })
    return pd.DataFrame(rows)


def analyze_spearman(df: pd.DataFrame) -> pd.DataFrame:
    """Spearman rho of OFI vs forward returns by symbol and session."""
    rows = []
    for sym in df["symbol"].unique():
        for session in list(SESSIONS.keys()) + ["all"]:
            sub = df[df["symbol"] == sym]
            if session != "all":
                sub = sub[sub["session"] == session]
            for horizon in ("ret_1m", "ret_5m", "ret_10m"):
                valid = sub.dropna(subset=["ofi_ratio", horizon])
                if len(valid) < 30:
                    continue
                rho, pval = spearmanr(valid["ofi_ratio"], valid[horizon])
                rows.append({
                    "symbol": sym,
                    "session": session,
                    "horizon": horizon,
                    "rho": rho,
                    "pval": pval,
                    "n": len(valid),
                })
    return pd.DataFrame(rows)


def analyze_hit_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Directional hit rate: when OFI > threshold, fraction of positive forward returns."""
    rows = []
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym]
        for horizon in ("ret_1m", "ret_5m", "ret_10m"):
            valid = sub.dropna(subset=["ofi_ratio", horizon])
            for threshold in (0.1, 0.2, 0.3):
                # Long when OFI > threshold
                longs = valid[valid["ofi_ratio"] > threshold]
                # Short when OFI < -threshold
                shorts = valid[valid["ofi_ratio"] < -threshold]
                long_hit = (longs[horizon] > 0).mean() if len(longs) > 10 else np.nan
                short_hit = (shorts[horizon] < 0).mean() if len(shorts) > 10 else np.nan
                rows.append({
                    "symbol": sym, "horizon": horizon, "threshold": threshold,
                    "long_n": len(longs), "long_hit": long_hit,
                    "short_n": len(shorts), "short_hit": short_hit,
                })
    return pd.DataFrame(rows)


def analyze_autocorrelation(df: pd.DataFrame) -> pd.DataFrame:
    """OFI autocorrelation at lags 1-10 minutes."""
    rows = []
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].sort_values("bucket")["ofi_ratio"].dropna()
        for lag in range(1, 11):
            if len(sub) > lag + 30:
                ac = sub.corr(sub.shift(lag))
                rows.append({"symbol": sym, "lag_min": lag, "autocorr": ac})
    return pd.DataFrame(rows)


def plot_quintiles(df: pd.DataFrame, quintiles_df: pd.DataFrame) -> None:
    """Bar chart of mean return by OFI quintile, per symbol × horizon."""
    for sym in df["symbol"].unique():
        fig, axes = plt.subplots(1, 3, figsize=(16, 5))
        for ax, horizon in zip(axes, ("ret_1m", "ret_5m", "ret_10m")):
            sub = df[df["symbol"] == sym].dropna(subset=["ofi_ratio", horizon]).copy()
            if len(sub) < 100:
                ax.set_title(f"{horizon} (insufficient data)")
                continue
            sub["q"] = pd.qcut(sub["ofi_ratio"], 5, labels=False, duplicates="drop")
            means = sub.groupby("q")[horizon].mean() * 10000
            colors = ["#e74c3c" if v < 0 else "#2ecc71" for v in means]
            ax.bar(means.index, means.values, color=colors, edgecolor="white", linewidth=0.5)
            ax.set_xlabel("OFI Quintile")
            ax.set_ylabel("Mean Return (bps)")
            ax.set_title(f"{sym} — {horizon}")
            ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        fig.suptitle(f"OFI Quintile → Forward Return: {sym}", fontsize=14)
        plt.tight_layout()
        savefig(f"ofi_quintiles_{sym}.png")


def plot_spearman_heatmap(spearman_df: pd.DataFrame) -> None:
    """Heatmap of Spearman rho by symbol × session × horizon."""
    for horizon in ("ret_1m", "ret_5m", "ret_10m"):
        sub = spearman_df[spearman_df["horizon"] == horizon].copy()
        if sub.empty:
            continue
        pivot = sub.pivot_table(index="symbol", columns="session", values="rho")
        # Reorder columns
        col_order = [c for c in ["asian", "european", "us", "overnight", "all"] if c in pivot.columns]
        pivot = pivot[col_order]

        fig, ax = plt.subplots(figsize=(10, 4))
        im = ax.imshow(pivot.values, cmap="RdYlGn", vmin=-0.15, vmax=0.15, aspect="auto")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                if np.isfinite(val):
                    ax.text(j, i, f"{val:.3f}", ha="center", va="center",
                            color="black", fontsize=10, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Spearman rho")
        ax.set_title(f"OFI Spearman rho — {horizon}")
        plt.tight_layout()
        savefig(f"ofi_spearman_{horizon}.png")


def plot_autocorrelation(ac_df: pd.DataFrame) -> None:
    """OFI autocorrelation decay plot."""
    fig, ax = plt.subplots(figsize=(10, 5))
    for sym in ac_df["symbol"].unique():
        sub = ac_df[ac_df["symbol"] == sym]
        ax.plot(sub["lag_min"], sub["autocorr"], marker="o", label=sym, linewidth=2)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Lag (minutes)")
    ax.set_ylabel("Autocorrelation")
    ax.set_title("OFI Autocorrelation Decay")
    ax.legend()
    plt.tight_layout()
    savefig("ofi_autocorrelation.png")


def run() -> dict:
    """Run full OFI study, print tables, save plots. Return results dict."""
    apply_dark_theme()
    print("=" * 60)
    print("STUDY 01 — Order Flow Imbalance")
    print("=" * 60)

    print("\nLoading order_flow_1m data...")
    df = load_ofi()
    print(f"  {len(df)} rows, {df['symbol'].nunique()} symbols, "
          f"{df['bucket'].min()} → {df['bucket'].max()}")

    print("\n── Quintile Analysis ──")
    q_df = analyze_quintiles(df)
    print(q_df.to_string(index=False, float_format="%.2f"))
    q_df.to_csv(f"{OUTPUT_DIR}/ofi_quintiles.csv", index=False)

    print("\n── Spearman Correlations ──")
    s_df = analyze_spearman(df)
    print(s_df.to_string(index=False, float_format="%.4f"))
    s_df.to_csv(f"{OUTPUT_DIR}/ofi_spearman.csv", index=False)

    print("\n── Hit Rate ──")
    h_df = analyze_hit_rate(df)
    print(h_df.to_string(index=False, float_format="%.3f"))
    h_df.to_csv(f"{OUTPUT_DIR}/ofi_hitrate.csv", index=False)

    print("\n── OFI Autocorrelation ──")
    ac_df = analyze_autocorrelation(df)
    if not ac_df.empty:
        print(ac_df.to_string(index=False, float_format="%.4f"))
        ac_df.to_csv(f"{OUTPUT_DIR}/ofi_autocorrelation.csv", index=False)

    print("\nGenerating plots...")
    plot_quintiles(df, q_df)
    plot_spearman_heatmap(s_df)
    if not ac_df.empty:
        plot_autocorrelation(ac_df)

    return {"quintiles": q_df, "spearman": s_df, "hit_rate": h_df, "autocorrelation": ac_df}


if __name__ == "__main__":
    run()
