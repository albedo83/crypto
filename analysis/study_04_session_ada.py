"""Study 04 — ADA Asian Session: does the ADA/Asia edge hold at microstructure level?

Prior finding: lag USDT-crypto exploitable only for ADA in Asian session.
Run: python3 -m analysis.study_04_session_ada
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
)


def load_session_stats() -> pd.DataFrame:
    """Per-session stats for all symbols: OFI, spread, volume, trade rate."""
    df = fetch_df("""
        SELECT
            o.bucket,
            o.instrument_id,
            o.ofi_ratio,
            o.net_flow,
            o.total_notional,
            o.trade_count,
            o.close_price,
            b.avg_spread_bps
        FROM order_flow_1m o
        LEFT JOIN book_tob_1m b
            ON o.bucket = b.bucket
            AND o.instrument_id = b.instrument_id
            AND o.venue_id = b.venue_id
        ORDER BY o.instrument_id, o.bucket
    """)
    if df.empty:
        raise RuntimeError("order_flow_1m empty — refresh matviews first")
    df["bucket"] = pd.to_datetime(df["bucket"], utc=True)
    df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)
    add_session_column(df)
    # Forward returns
    results = []
    for iid in df["instrument_id"].unique():
        sub = df[df["instrument_id"] == iid].sort_values("bucket").copy()
        sub["ret_1m"] = sub["close_price"].pct_change(1).shift(-1)
        sub["ret_5m"] = sub["close_price"].pct_change(5).shift(-5)
        results.append(sub)
    return pd.concat(results, ignore_index=True)


def session_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Compare stats across sessions for each symbol."""
    rows = []
    for sym in df["symbol"].unique():
        for session in SESSIONS:
            sub = df[(df["symbol"] == sym) & (df["session"] == session)]
            if len(sub) < 30:
                continue
            rows.append({
                "symbol": sym,
                "session": session,
                "n_minutes": len(sub),
                "mean_ofi": sub["ofi_ratio"].mean(),
                "std_ofi": sub["ofi_ratio"].std(),
                "mean_spread_bps": sub["avg_spread_bps"].mean(),
                "mean_notional": sub["total_notional"].mean(),
                "trades_per_min": sub["trade_count"].mean(),
            })
    return pd.DataFrame(rows)


def ofi_quintiles_by_session(df: pd.DataFrame) -> pd.DataFrame:
    """OFI quintile spread (Q5-Q1) per symbol × session × horizon."""
    rows = []
    for sym in df["symbol"].unique():
        for session in list(SESSIONS.keys()) + ["all"]:
            sub = df[df["symbol"] == sym]
            if session != "all":
                sub = sub[sub["session"] == session]
            for horizon in ("ret_1m", "ret_5m"):
                valid = sub.dropna(subset=["ofi_ratio", horizon])
                if len(valid) < 50:
                    continue
                valid = valid.copy()
                valid["q"] = pd.qcut(valid["ofi_ratio"], 5, labels=False, duplicates="drop")
                means = valid.groupby("q")[horizon].mean()
                if len(means) < 2:
                    continue
                spread = means.iloc[-1] - means.iloc[0]
                rho, pval = spearmanr(valid["ofi_ratio"], valid[horizon])
                rows.append({
                    "symbol": sym, "session": session, "horizon": horizon,
                    "spread_Q5_Q1_bps": spread * 10000,
                    "rho": rho, "pval": pval, "n": len(valid),
                })
    return pd.DataFrame(rows)


def return_autocorrelation(df: pd.DataFrame) -> pd.DataFrame:
    """Autocorrelation of 1m returns by session — momentum vs mean-reversion."""
    rows = []
    for sym in df["symbol"].unique():
        for session in SESSIONS:
            sub = df[(df["symbol"] == sym) & (df["session"] == session)]
            rets = sub["ret_1m"].dropna()
            if len(rets) < 50:
                continue
            for lag in (1, 2, 3, 5):
                ac = rets.corr(rets.shift(lag))
                rows.append({
                    "symbol": sym, "session": session,
                    "lag": lag, "autocorr": ac,
                })
    return pd.DataFrame(rows)


def hourly_heatmap_data(df: pd.DataFrame) -> pd.DataFrame:
    """Compute OFI rho, spread, volume by hour for 24h heatmap."""
    df["hour"] = df["bucket"].dt.hour
    rows = []
    for sym in df["symbol"].unique():
        for hour in range(24):
            sub = df[(df["symbol"] == sym) & (df["hour"] == hour)]
            if len(sub) < 20:
                continue
            valid = sub.dropna(subset=["ofi_ratio", "ret_5m"])
            rho = np.nan
            if len(valid) >= 20:
                rho, _ = spearmanr(valid["ofi_ratio"], valid["ret_5m"])
            rows.append({
                "symbol": sym, "hour": hour,
                "ofi_rho_5m": rho,
                "mean_spread_bps": sub["avg_spread_bps"].mean(),
                "mean_volume": sub["total_notional"].mean(),
                "n": len(sub),
            })
    return pd.DataFrame(rows)


def plot_session_comparison(comp_df: pd.DataFrame) -> None:
    """Grouped bar charts comparing sessions across symbols."""
    metrics = [("mean_ofi", "Mean OFI Ratio"), ("mean_spread_bps", "Mean Spread (bps)"),
               ("trades_per_min", "Trades/min")]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    session_order = ["asian", "european", "us", "overnight"]
    colors = {"asian": "#e74c3c", "european": "#3498db", "us": "#2ecc71", "overnight": "#f39c12"}

    for ax, (metric, label) in zip(axes, metrics):
        symbols = comp_df["symbol"].unique()
        x = np.arange(len(symbols))
        width = 0.2
        for i, session in enumerate(session_order):
            vals = [comp_df[(comp_df["symbol"] == s) & (comp_df["session"] == session)][metric].values
                    for s in symbols]
            vals = [v[0] if len(v) > 0 else 0 for v in vals]
            ax.bar(x + i * width, vals, width, label=session, color=colors[session],
                   edgecolor="white", linewidth=0.5)
        ax.set_xlabel("Symbol")
        ax.set_ylabel(label)
        ax.set_xticks(x + 1.5 * width)
        ax.set_xticklabels(symbols)
        ax.legend(fontsize=8)
    fig.suptitle("Session Comparison Across Symbols", fontsize=14)
    plt.tight_layout()
    savefig("session_comparison.png")


def plot_ofi_edge_by_session(quintile_df: pd.DataFrame) -> None:
    """OFI quintile spread by session — highlight ADA Asian."""
    for horizon in ("ret_1m", "ret_5m"):
        sub = quintile_df[quintile_df["horizon"] == horizon]
        if sub.empty:
            continue
        pivot = sub.pivot_table(index="symbol", columns="session", values="spread_Q5_Q1_bps")
        col_order = [c for c in ["asian", "european", "us", "overnight", "all"] if c in pivot.columns]
        pivot = pivot[col_order]

        fig, ax = plt.subplots(figsize=(10, 4))
        im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto",
                       vmin=-np.nanmax(np.abs(pivot.values)),
                       vmax=np.nanmax(np.abs(pivot.values)))
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                if np.isfinite(val):
                    ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                            color="black", fontsize=11, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Q5-Q1 spread (bps)")
        ax.set_title(f"OFI Edge by Session — {horizon}")
        plt.tight_layout()
        savefig(f"session_ofi_edge_{horizon}.png")


def plot_hourly_heatmap(hourly_df: pd.DataFrame) -> None:
    """24h heatmap: OFI rho × symbol."""
    if hourly_df.empty:
        return
    pivot = hourly_df.pivot_table(index="symbol", columns="hour", values="ofi_rho_5m")

    fig, ax = plt.subplots(figsize=(16, 4))
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-0.15, vmax=0.15)
    ax.set_xticks(range(24))
    ax.set_xticklabels(range(24))
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)
    ax.set_xlabel("UTC Hour")
    ax.set_title("OFI → 5m Return Spearman rho by Hour")
    # Session boundaries
    for h in (8, 14, 21):
        ax.axvline(h - 0.5, color="yellow", linewidth=1, linestyle="--", alpha=0.7)
    plt.colorbar(im, ax=ax, label="Spearman rho")
    plt.tight_layout()
    savefig("session_hourly_heatmap.png")


def run() -> dict:
    """Run ADA session analysis."""
    apply_dark_theme()
    print("=" * 60)
    print("STUDY 04 — ADA Asian Session Deep Dive")
    print("=" * 60)

    print("\nLoading session stats...")
    df = load_session_stats()
    print(f"  {len(df)} rows")

    print("\n── Session Comparison ──")
    comp = session_comparison(df)
    print(comp.to_string(index=False, float_format="%.4f"))
    comp.to_csv(f"{OUTPUT_DIR}/session_comparison.csv", index=False)

    print("\n── OFI Quintiles by Session ──")
    quint = ofi_quintiles_by_session(df)
    print(quint.to_string(index=False, float_format="%.4f"))
    quint.to_csv(f"{OUTPUT_DIR}/session_ofi_quintiles.csv", index=False)

    # Asia / all-day ratio for ADA
    ada_rows = quint[(quint["symbol"] == "ADAUSDT") & (quint["horizon"] == "ret_5m")]
    asia_rho = ada_rows[ada_rows["session"] == "asian"]["rho"].values
    all_rho = ada_rows[ada_rows["session"] == "all"]["rho"].values
    if len(asia_rho) > 0 and len(all_rho) > 0 and all_rho[0] != 0:
        ratio = asia_rho[0] / all_rho[0]
        print(f"\n  ★ ADA Asia/All-day rho ratio (ret_5m): {ratio:.2f}x")
        print(f"    Asia rho={asia_rho[0]:.4f}, All-day rho={all_rho[0]:.4f}")
    else:
        print("\n  ★ ADA Asia/All-day ratio: insufficient data")

    print("\n── Return Autocorrelation by Session ──")
    ac = return_autocorrelation(df)
    if not ac.empty:
        # Show lag-1 only for readability
        ac1 = ac[ac["lag"] == 1]
        print(ac1.to_string(index=False, float_format="%.4f"))
        ac.to_csv(f"{OUTPUT_DIR}/session_autocorrelation.csv", index=False)

    print("\n── Hourly Heatmap Data ──")
    hourly = hourly_heatmap_data(df)
    if not hourly.empty:
        hourly.to_csv(f"{OUTPUT_DIR}/session_hourly.csv", index=False)

    print("\nGenerating plots...")
    plot_session_comparison(comp)
    plot_ofi_edge_by_session(quint)
    plot_hourly_heatmap(hourly)

    return {"comparison": comp, "quintiles": quint, "autocorrelation": ac, "hourly": hourly}


if __name__ == "__main__":
    run()
