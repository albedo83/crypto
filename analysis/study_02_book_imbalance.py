"""Study 02 — Book Depth Imbalance: does bid/ask depth ratio predict mid-price direction?

Uses book_imbalance_1s (TOB) and book_depth_imbalance() (multi-level).
Run: python3 -m analysis.study_02_book_imbalance
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from analysis.db import fetch_df
from analysis.utils import (
    INSTRUMENT_IDS, ID_TO_SYMBOL,
    apply_dark_theme, savefig, OUTPUT_DIR,
)


def load_tob_imbalance() -> pd.DataFrame:
    """Load book_imbalance_1s and compute forward mid-price returns."""
    df = fetch_df("""
        SELECT bucket, instrument_id, tob_bid_ratio, avg_spread_bps, close_mid
        FROM book_imbalance_1s
        ORDER BY instrument_id, bucket
    """)
    if df.empty:
        raise RuntimeError("book_imbalance_1s is empty — run REFRESH MATERIALIZED VIEW first")
    df["bucket"] = pd.to_datetime(df["bucket"], utc=True)
    results = []
    for iid in df["instrument_id"].unique():
        sub = df[df["instrument_id"] == iid].sort_values("bucket").copy()
        # Buckets are 5s, so shift(N) = N*5 seconds
        for shifts, label in [(1, "5s"), (2, "10s"), (6, "30s"), (12, "60s"), (24, "120s")]:
            sub[f"ret_{label}"] = sub["close_mid"].pct_change(shifts).shift(-shifts)
        results.append(sub)
    df = pd.concat(results, ignore_index=True)
    df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)
    return df


def load_depth_imbalance(instrument_id: int, levels: int = 10,
                         limit: int = 500000) -> pd.DataFrame:
    """Load multi-level depth imbalance via SQL function (sampled if large)."""
    df = fetch_df("""
        SELECT exchange_ts, bid_depth, ask_depth, imbalance_ratio
        FROM book_depth_imbalance($1,
            (SELECT min(exchange_ts) FROM book_levels WHERE instrument_id = $1),
            (SELECT max(exchange_ts) FROM book_levels WHERE instrument_id = $1),
            $2)
        LIMIT $3
    """, instrument_id, levels, limit)
    if df.empty:
        return df
    df["exchange_ts"] = pd.to_datetime(df["exchange_ts"], utc=True)
    return df


def tob_decile_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Deciles of tob_bid_ratio → mean forward return at multiple horizons."""
    rows = []
    for sym in df["symbol"].unique():
        for horizon in ("ret_5s", "ret_10s", "ret_30s", "ret_60s", "ret_120s"):
            sub = df[df["symbol"] == sym].dropna(subset=["tob_bid_ratio", horizon])
            if len(sub) < 200:
                continue
            sub = sub.copy()
            sub["decile"] = pd.qcut(sub["tob_bid_ratio"], 10, labels=False, duplicates="drop")
            means = sub.groupby("decile")[horizon].mean()
            spread = means.iloc[-1] - means.iloc[0] if len(means) >= 2 else np.nan
            rho, pval = spearmanr(sub["tob_bid_ratio"], sub[horizon])
            rows.append({
                "symbol": sym, "horizon": horizon,
                "D1_bps": means.iloc[0] * 10000 if len(means) >= 1 else np.nan,
                "D10_bps": means.iloc[-1] * 10000 if len(means) >= 2 else np.nan,
                "spread_bps": spread * 10000,
                "rho": rho, "pval": pval, "n": len(sub),
            })
    return pd.DataFrame(rows)


def depth_level_contribution(instrument_id: int, symbol: str) -> pd.DataFrame:
    """Compare Spearman rho at 1,3,5,10 levels to see if deep levels add signal."""
    rows = []
    for levels in (1, 3, 5, 10):
        depth = load_depth_imbalance(instrument_id, levels=levels, limit=200000)
        if depth.empty or len(depth) < 100:
            continue
        # Join with 5s mid-price from book_imbalance_1s
        depth["bucket"] = depth["exchange_ts"].dt.floor("5s")
        mid = fetch_df("""
            SELECT bucket, close_mid
            FROM book_imbalance_1s
            WHERE instrument_id = $1
            ORDER BY bucket
        """, instrument_id)
        if mid.empty:
            continue
        mid["bucket"] = pd.to_datetime(mid["bucket"], utc=True)
        mid = mid.sort_values("bucket")
        # Forward returns on mid (5s buckets: shift(N) = N*5s)
        for shifts, label in [(1, "5s"), (2, "10s"), (6, "30s"), (12, "60s"), (24, "120s")]:
            mid[f"ret_{label}"] = mid["close_mid"].pct_change(shifts).shift(-shifts)
        # Merge
        merged = depth.merge(mid, on="bucket", how="inner")
        for horizon in ("ret_5s", "ret_10s", "ret_30s", "ret_60s", "ret_120s"):
            valid = merged.dropna(subset=["imbalance_ratio", horizon])
            if len(valid) < 50:
                continue
            rho, pval = spearmanr(valid["imbalance_ratio"], valid[horizon])
            rows.append({
                "symbol": symbol, "levels": levels, "horizon": horizon,
                "rho": rho, "pval": pval, "n": len(valid),
            })
    return pd.DataFrame(rows)


def spread_conditioning(df: pd.DataFrame) -> pd.DataFrame:
    """Is book imbalance signal cleaner when spread is tight?"""
    rows = []
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].dropna(subset=["tob_bid_ratio", "avg_spread_bps", "ret_30s"])
        if len(sub) < 200:
            continue
        median_spread = sub["avg_spread_bps"].median()
        for label, mask in [("tight", sub["avg_spread_bps"] <= median_spread),
                            ("wide", sub["avg_spread_bps"] > median_spread)]:
            s = sub[mask]
            if len(s) < 50:
                continue
            rho, pval = spearmanr(s["tob_bid_ratio"], s["ret_30s"])
            rows.append({
                "symbol": sym, "spread_regime": label,
                "median_spread_bps": s["avg_spread_bps"].median(),
                "rho_30s": rho, "pval": pval, "n": len(s),
            })
    return pd.DataFrame(rows)


def composite_signal(df_ofi: pd.DataFrame | None, df_book: pd.DataFrame) -> pd.DataFrame:
    """OFI + book imbalance composite vs each alone."""
    if df_ofi is None:
        return pd.DataFrame()
    rows = []
    for sym in df_book["symbol"].unique():
        book = df_book[df_book["symbol"] == sym].copy()
        book["bucket_1m"] = book["bucket"].dt.floor("1min")
        ofi_sym = df_ofi[df_ofi["symbol"] == sym][["bucket", "ofi_ratio"]].copy()
        ofi_sym.rename(columns={"bucket": "bucket_1m"}, inplace=True)
        merged = book.merge(ofi_sym, on="bucket_1m", how="inner")
        if len(merged) < 100:
            continue
        # Normalize and combine
        for col in ("tob_bid_ratio", "ofi_ratio"):
            merged[f"{col}_z"] = (merged[col] - merged[col].mean()) / merged[col].std()
        merged["composite"] = merged["tob_bid_ratio_z"] + merged["ofi_ratio_z"]
        for horizon in ("ret_10s", "ret_30s", "ret_60s"):
            if horizon not in merged.columns:
                continue
            valid = merged.dropna(subset=["composite", "tob_bid_ratio", "ofi_ratio", horizon])
            if len(valid) < 50:
                continue
            rho_comp, _ = spearmanr(valid["composite"], valid[horizon])
            rho_book, _ = spearmanr(valid["tob_bid_ratio"], valid[horizon])
            rho_ofi, _ = spearmanr(valid["ofi_ratio"], valid[horizon])
            rows.append({
                "symbol": sym, "horizon": horizon,
                "rho_composite": rho_comp,
                "rho_book_only": rho_book,
                "rho_ofi_only": rho_ofi,
                "n": len(valid),
            })
    return pd.DataFrame(rows)


def plot_decile_returns(df: pd.DataFrame) -> None:
    """Decile mean return bar charts."""
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].dropna(subset=["tob_bid_ratio", "ret_30s"]).copy()
        if len(sub) < 200:
            continue
        sub["decile"] = pd.qcut(sub["tob_bid_ratio"], 10, labels=False, duplicates="drop")
        means = sub.groupby("decile")["ret_30s"].mean() * 10000

        fig, ax = plt.subplots(figsize=(10, 5))
        colors = ["#e74c3c" if v < 0 else "#2ecc71" for v in means]
        ax.bar(means.index, means.values, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_xlabel("TOB Bid Ratio Decile")
        ax.set_ylabel("Mean 30s Return (bps)")
        ax.set_title(f"Book Imbalance Decile → 30s Return: {sym}")
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        plt.tight_layout()
        savefig(f"book_imb_deciles_{sym}.png")


def plot_level_contribution(level_df: pd.DataFrame) -> None:
    """Spearman rho by number of book levels."""
    if level_df.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    for sym in level_df["symbol"].unique():
        for horizon in ("ret_10s", "ret_30s"):
            sub = level_df[(level_df["symbol"] == sym) & (level_df["horizon"] == horizon)]
            if sub.empty:
                continue
            ax.plot(sub["levels"], sub["rho"], marker="o", linewidth=2,
                    label=f"{sym} {horizon}")
    ax.set_xlabel("Book Levels Used")
    ax.set_ylabel("Spearman rho")
    ax.set_title("Depth Level Contribution to Predictive Power")
    ax.legend()
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    plt.tight_layout()
    savefig("book_level_contribution.png")


def run() -> dict:
    """Run full book imbalance study."""
    apply_dark_theme()
    print("=" * 60)
    print("STUDY 02 — Book Depth Imbalance")
    print("=" * 60)

    print("\nLoading book_imbalance_1s data...")
    df = load_tob_imbalance()
    print(f"  {len(df)} rows, {df['symbol'].nunique()} symbols")

    print("\n── TOB Decile Analysis ──")
    dec_df = tob_decile_analysis(df)
    print(dec_df.to_string(index=False, float_format="%.4f"))
    dec_df.to_csv(f"{OUTPUT_DIR}/book_imb_deciles.csv", index=False)

    print("\n── Depth Level Contribution ──")
    level_rows = []
    for sym, iid in INSTRUMENT_IDS.items():
        print(f"  Processing {sym} (levels 1,3,5,10)...")
        contrib = depth_level_contribution(iid, sym)
        level_rows.append(contrib)
    level_df = pd.concat(level_rows, ignore_index=True) if level_rows else pd.DataFrame()
    if not level_df.empty:
        print(level_df.to_string(index=False, float_format="%.4f"))
        level_df.to_csv(f"{OUTPUT_DIR}/book_level_contrib.csv", index=False)

    print("\n── Spread Conditioning ──")
    spread_df = spread_conditioning(df)
    if not spread_df.empty:
        print(spread_df.to_string(index=False, float_format="%.4f"))
        spread_df.to_csv(f"{OUTPUT_DIR}/book_spread_cond.csv", index=False)

    print("\n── Composite Signal (OFI + Book) ──")
    try:
        ofi_df = fetch_df("""
            SELECT time_bucket('1 second', bucket) AS bucket, instrument_id, ofi_ratio
            FROM order_flow_1m
            ORDER BY instrument_id, bucket
        """)
        if not ofi_df.empty:
            ofi_df["bucket"] = pd.to_datetime(ofi_df["bucket"], utc=True)
            ofi_df["symbol"] = ofi_df["instrument_id"].map(ID_TO_SYMBOL)
        comp_df = composite_signal(ofi_df if not ofi_df.empty else None, df)
    except Exception:
        comp_df = pd.DataFrame()
    if not comp_df.empty:
        print(comp_df.to_string(index=False, float_format="%.4f"))
        comp_df.to_csv(f"{OUTPUT_DIR}/book_composite.csv", index=False)

    print("\nGenerating plots...")
    plot_decile_returns(df)
    plot_level_contribution(level_df)

    return {
        "deciles": dec_df, "level_contribution": level_df,
        "spread_conditioning": spread_df, "composite": comp_df,
    }


if __name__ == "__main__":
    run()
