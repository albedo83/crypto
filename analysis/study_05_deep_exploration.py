"""Study 05 — Deep Signal Exploration: 10 angles on the microstructure data.

A. Cross-asset lead-lag (BTC mène-t-il ETH/ADA ?)
B. Pics d'intensité de trading (volume spikes)
C. Whale vs retail (gros trades vs petits)
D. Vélocité du carnet (dImbalance/dt)
E. Dynamique du spread (compression → breakout ?)
F. Kyle's lambda (impact de prix par unité de volume)
G. VPIN proxy (trading informé)
H. Régimes de volatilité
I. Saisonnalité intraday
J. Matrice Session × Signal

Run: python3 -m analysis.study_05_deep_exploration
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

# ─────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────

def load_all_data() -> dict:
    """Load and merge base datasets."""

    print("  [1/4] book_imbalance_1s (5s buckets)...")
    book_5s = fetch_df("""
        SELECT bucket, instrument_id, tob_bid_ratio, avg_spread_bps, close_mid, tick_count
        FROM book_imbalance_1s ORDER BY instrument_id, bucket
    """)
    book_5s["bucket"] = pd.to_datetime(book_5s["bucket"], utc=True)
    print(f"         {len(book_5s)} rows")

    print("  [2/4] trade_stats_5s (aggregating trades_raw)...")
    trade_5s = fetch_df("""
        SELECT time_bucket('5 seconds', exchange_ts) AS bucket,
               instrument_id,
               count(*)                                                     AS trade_count,
               sum(notional)                                                AS total_notional,
               sum(CASE WHEN aggressor_side='BUY'  THEN notional ELSE 0 END) AS buy_notional,
               sum(CASE WHEN aggressor_side='SELL' THEN notional ELSE 0 END) AS sell_notional,
               last(price, exchange_ts)                                     AS close_price,
               max(notional)                                                AS max_trade_size
        FROM trades_raw
        WHERE aggressor_side IS NOT NULL
        GROUP BY bucket, instrument_id
        ORDER BY instrument_id, bucket
    """)
    trade_5s["bucket"] = pd.to_datetime(trade_5s["bucket"], utc=True)
    print(f"         {len(trade_5s)} rows")

    print("  [3/4] whale_flow_1m (top 5% trades)...")
    whale_1m = fetch_df("""
        WITH pcts AS (
            SELECT instrument_id,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY notional) AS p95
            FROM trades_raw WHERE aggressor_side IS NOT NULL
            GROUP BY instrument_id
        )
        SELECT time_bucket('1 minute', t.exchange_ts) AS bucket,
               t.instrument_id,
               sum(CASE WHEN t.notional >= p.p95 AND t.aggressor_side='BUY'  THEN t.notional ELSE 0 END) AS whale_buy,
               sum(CASE WHEN t.notional >= p.p95 AND t.aggressor_side='SELL' THEN t.notional ELSE 0 END) AS whale_sell,
               sum(CASE WHEN t.notional <  p.p95 AND t.aggressor_side='BUY'  THEN t.notional ELSE 0 END) AS retail_buy,
               sum(CASE WHEN t.notional <  p.p95 AND t.aggressor_side='SELL' THEN t.notional ELSE 0 END) AS retail_sell,
               last(t.price, t.exchange_ts) AS close_price
        FROM trades_raw t JOIN pcts p USING (instrument_id)
        WHERE t.aggressor_side IS NOT NULL
        GROUP BY bucket, t.instrument_id
        ORDER BY t.instrument_id, bucket
    """)
    whale_1m["bucket"] = pd.to_datetime(whale_1m["bucket"], utc=True)
    print(f"         {len(whale_1m)} rows")

    print("  [4/4] order_flow_1m...")
    ofi_1m = fetch_df("SELECT * FROM order_flow_1m ORDER BY instrument_id, bucket")
    ofi_1m["bucket"] = pd.to_datetime(ofi_1m["bucket"], utc=True)
    print(f"         {len(ofi_1m)} rows")

    # Enrich all
    for df in (book_5s, trade_5s, whale_1m, ofi_1m):
        df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)

    # Merge book + trades at 5s
    merged = book_5s.merge(trade_5s, on=["bucket", "instrument_id", "symbol"], how="inner")
    print(f"  Merged 5s: {len(merged)} rows")

    # Compute derived features on merged
    for iid in merged["instrument_id"].unique():
        mask = merged["instrument_id"] == iid
        idx = merged.loc[mask].index
        mid = merged.loc[idx, "close_mid"]
        # Forward returns (shift N → N*5 seconds)
        for n, label in [(1,"5s"),(2,"10s"),(6,"30s"),(12,"60s"),(24,"120s")]:
            merged.loc[idx, f"ret_{label}"] = mid.pct_change(n).shift(-n)
        # OFI at 5s
        merged.loc[idx, "ofi_5s"] = (
            (merged.loc[idx, "buy_notional"] - merged.loc[idx, "sell_notional"])
            / merged.loc[idx, "total_notional"].replace(0, np.nan)
        )
        # Book velocity (first diff of imbalance)
        merged.loc[idx, "book_velocity"] = merged.loc[idx, "tob_bid_ratio"].diff()
        # Book acceleration (second diff)
        merged.loc[idx, "book_accel"] = merged.loc[idx, "book_velocity"].diff()
        # Trade intensity z-score (rolling 5 min = 60 buckets)
        tc = merged.loc[idx, "trade_count"]
        merged.loc[idx, "intensity_z"] = (
            (tc - tc.rolling(60).mean()) / tc.rolling(60).std()
        )
        # Notional z-score
        tn = merged.loc[idx, "total_notional"]
        merged.loc[idx, "notional_z"] = (
            (tn - tn.rolling(60).mean()) / tn.rolling(60).std()
        )
        # Spread z-score (rolling 5 min)
        sp = merged.loc[idx, "avg_spread_bps"]
        merged.loc[idx, "spread_z"] = (
            (sp - sp.rolling(60).mean()) / sp.rolling(60).std()
        )
        # Signed volume
        merged.loc[idx, "signed_vol"] = (
            merged.loc[idx, "buy_notional"] - merged.loc[idx, "sell_notional"]
        )
        # Kyle's lambda (rolling 60 = 5 min)
        delta = mid.diff()
        sv = merged.loc[idx, "signed_vol"]
        cov = delta.rolling(60).cov(sv)
        var = sv.rolling(60).var()
        merged.loc[idx, "kyle_lambda"] = cov / var.replace(0, np.nan)
        # VPIN proxy
        buy_frac = merged.loc[idx, "buy_notional"] / merged.loc[idx, "total_notional"].replace(0, np.nan)
        merged.loc[idx, "vpin_proxy"] = (buy_frac - 0.5).abs().rolling(60).mean()
        # Max trade size relative
        merged.loc[idx, "max_trade_rel"] = (
            merged.loc[idx, "max_trade_size"] / merged.loc[idx, "total_notional"].replace(0, np.nan)
        )

    add_session_column(merged)
    merged["hour"] = merged["bucket"].dt.hour

    return {"merged": merged, "whale_1m": whale_1m, "ofi_1m": ofi_1m, "book_5s": book_5s}


# ─────────────────────────────────────────────────────────────────────
# A. Cross-asset lead-lag
# ─────────────────────────────────────────────────────────────────────

def section_a_lead_lag(book_5s: pd.DataFrame) -> pd.DataFrame:
    """Does BTC lead ETH/ADA? Cross-correlation at 5s lags."""
    pivot = book_5s.pivot_table(index="bucket", columns="symbol", values="close_mid")
    pivot = pivot.dropna()
    rets = pivot.pct_change().dropna()

    pairs = [("BTCUSDT","ETHUSDT"), ("BTCUSDT","ADAUSDT"), ("ETHUSDT","ADAUSDT")]
    max_lag = 24  # 24 × 5s = 120s

    rows = []
    for leader, follower in pairs:
        for lag in range(-max_lag, max_lag + 1):
            # positive lag → leader at t correlates with follower at t+lag → leader LEADS
            c = rets[leader].corr(rets[follower].shift(-lag))
            rows.append({
                "pair": f"{leader[:3]}→{follower[:3]}",
                "lag_units": lag, "lag_s": lag * 5,
                "corr": c,
            })
    df = pd.DataFrame(rows)

    # Repeat by session
    rets["hour"] = rets.index.hour
    rets["session"] = rets["hour"].map(
        lambda h: next((n for n,(s,e) in SESSIONS.items() if s<=h<e), "overnight")
    )
    session_rows = []
    for session in SESSIONS:
        sr = rets[rets["session"] == session]
        if len(sr) < 200:
            continue
        for leader, follower in pairs:
            for lag in [0, 1, 2, 3, 4, 5, 6]:
                c = sr[leader].corr(sr[follower].shift(-lag))
                session_rows.append({
                    "session": session,
                    "pair": f"{leader[:3]}→{follower[:3]}",
                    "lag_s": lag * 5, "corr": c,
                })
    session_df = pd.DataFrame(session_rows)

    return df, session_df


# ─────────────────────────────────────────────────────────────────────
# B. Trade intensity spikes
# ─────────────────────────────────────────────────────────────────────

def section_b_trade_intensity(m: pd.DataFrame) -> pd.DataFrame:
    """Do volume spikes predict direction?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym].dropna(subset=["intensity_z"])
        for horizon in ("ret_5s", "ret_10s", "ret_30s", "ret_60s"):
            valid = sub.dropna(subset=[horizon])
            if len(valid) < 200:
                continue
            # Raw intensity → return
            rho, pval = spearmanr(valid["intensity_z"], valid[horizon])
            rows.append({"signal": "intensity_z", "symbol": sym, "horizon": horizon,
                         "rho": rho, "pval": pval, "n": len(valid)})
            # Directional spikes: high intensity + buy-heavy or sell-heavy
            buy_ratio = valid["buy_notional"] / valid["total_notional"].replace(0, np.nan)
            spikes = valid[valid["intensity_z"] > 2]
            buy_spikes = spikes[buy_ratio.loc[spikes.index] > 0.6]
            sell_spikes = spikes[buy_ratio.loc[spikes.index] < 0.4]
            if len(buy_spikes) > 10:
                rows.append({"signal": "buy_spike(z>2)", "symbol": sym, "horizon": horizon,
                             "rho": np.nan, "pval": np.nan,
                             "mean_ret_bps": buy_spikes[horizon].mean() * 1e4,
                             "n": len(buy_spikes)})
            if len(sell_spikes) > 10:
                rows.append({"signal": "sell_spike(z>2)", "symbol": sym, "horizon": horizon,
                             "rho": np.nan, "pval": np.nan,
                             "mean_ret_bps": sell_spikes[horizon].mean() * 1e4,
                             "n": len(sell_spikes)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# C. Whale vs retail
# ─────────────────────────────────────────────────────────────────────

def section_c_whale_flow(whale_1m: pd.DataFrame) -> pd.DataFrame:
    """Top 5% trades (whales) vs bottom 95% (retail): qui est plus informé?"""
    rows = []
    for iid in whale_1m["instrument_id"].unique():
        sub = whale_1m[whale_1m["instrument_id"] == iid].sort_values("bucket").copy()
        sym = ID_TO_SYMBOL[iid]
        # OFI
        wb, ws = sub["whale_buy"], sub["whale_sell"]
        rb, rs = sub["retail_buy"], sub["retail_sell"]
        sub["whale_ofi"] = (wb - ws) / (wb + ws).replace(0, np.nan)
        sub["retail_ofi"] = (rb - rs) / (rb + rs).replace(0, np.nan)
        # Forward returns
        for n in (1, 5, 10):
            sub[f"ret_{n}m"] = sub["close_price"].pct_change(n).shift(-n)
        for ofi_col in ("whale_ofi", "retail_ofi"):
            for horizon in ("ret_1m", "ret_5m", "ret_10m"):
                valid = sub.dropna(subset=[ofi_col, horizon])
                if len(valid) < 50:
                    continue
                rho, pval = spearmanr(valid[ofi_col], valid[horizon])
                rows.append({"signal": ofi_col, "symbol": sym, "horizon": horizon,
                             "rho": rho, "pval": pval, "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# D. Book velocity (dImbalance/dt)
# ─────────────────────────────────────────────────────────────────────

def section_d_book_velocity(m: pd.DataFrame) -> pd.DataFrame:
    """La vitesse de changement du book prédit-elle mieux que le niveau?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym]
        for sig, label in [("tob_bid_ratio","level"), ("book_velocity","velocity"),
                           ("book_accel","accel")]:
            for horizon in ("ret_5s","ret_10s","ret_30s","ret_60s"):
                valid = sub.dropna(subset=[sig, horizon])
                if len(valid) < 200:
                    continue
                rho, pval = spearmanr(valid[sig], valid[horizon])
                rows.append({"signal": label, "symbol": sym, "horizon": horizon,
                             "rho": rho, "pval": pval, "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# E. Spread dynamics
# ─────────────────────────────────────────────────────────────────────

def section_e_spread_dynamics(m: pd.DataFrame) -> pd.DataFrame:
    """Compression du spread → breakout?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym].dropna(subset=["spread_z"]).copy()
        if len(sub) < 500:
            continue
        # Tight = spread below rolling median for > 30s (6 buckets)
        sub["tight"] = (sub["spread_z"] < -0.5).rolling(6).sum() >= 6
        # Wide
        sub["wide"] = (sub["spread_z"] > 0.5).rolling(6).sum() >= 6

        for regime, label in [(sub["tight"], "after_tight"), (sub["wide"], "after_wide")]:
            s = sub[regime]
            for horizon in ("ret_10s", "ret_30s", "ret_60s"):
                valid = s.dropna(subset=[horizon])
                if len(valid) < 30:
                    continue
                # Absolute return (volatility proxy)
                abs_ret = valid[horizon].abs().mean() * 1e4
                # Spread z → return direction?
                rho, pval = spearmanr(valid["tob_bid_ratio"], valid[horizon])
                rows.append({"signal": label, "symbol": sym, "horizon": horizon,
                             "abs_ret_bps": abs_ret, "book_imb_rho": rho,
                             "pval": pval, "n": len(valid)})
        # Overall: does spread predict absolute return magnitude?
        for horizon in ("ret_10s", "ret_30s", "ret_60s"):
            valid = sub.dropna(subset=["spread_z", horizon])
            if len(valid) < 200:
                continue
            rho, pval = spearmanr(valid["spread_z"], valid[horizon].abs())
            rows.append({"signal": "spread→|ret|", "symbol": sym, "horizon": horizon,
                         "abs_ret_bps": np.nan, "book_imb_rho": rho,
                         "pval": pval, "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# F. Kyle's lambda (price impact)
# ─────────────────────────────────────────────────────────────────────

def section_f_kyle_lambda(m: pd.DataFrame) -> pd.DataFrame:
    """Lambda élevé = trading informé. Le lambda prédit-il la volatilité future?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym].dropna(subset=["kyle_lambda"])
        if len(sub) < 200:
            continue
        # Lambda z-score
        lam = sub["kyle_lambda"]
        lam_z = (lam - lam.rolling(360).mean()) / lam.rolling(360).std()  # 30-min window
        sub = sub.copy()
        sub["lambda_z"] = lam_z
        # High lambda → predict future absolute return?
        for horizon in ("ret_10s", "ret_30s", "ret_60s"):
            valid = sub.dropna(subset=["lambda_z", horizon])
            if len(valid) < 200:
                continue
            rho, pval = spearmanr(valid["lambda_z"], valid[horizon].abs())
            rows.append({"signal": "lambda→|ret|", "symbol": sym, "horizon": horizon,
                         "rho": rho, "pval": pval, "n": len(valid)})
            # Also: high lambda + book imbalance → better directional signal?
            top_lambda = valid[valid["lambda_z"] > 1]
            if len(top_lambda) > 50:
                rho2, pval2 = spearmanr(top_lambda["tob_bid_ratio"], top_lambda[horizon])
                rows.append({"signal": "book|high_lambda", "symbol": sym, "horizon": horizon,
                             "rho": rho2, "pval": pval2, "n": len(top_lambda)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# G. VPIN proxy
# ─────────────────────────────────────────────────────────────────────

def section_g_vpin(m: pd.DataFrame) -> pd.DataFrame:
    """VPIN élevé = trading informé → volatilité imminente?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym].dropna(subset=["vpin_proxy"])
        if len(sub) < 200:
            continue
        for horizon in ("ret_10s", "ret_30s", "ret_60s", "ret_120s"):
            valid = sub.dropna(subset=[horizon])
            if len(valid) < 200:
                continue
            # VPIN → absolute return (volatility)
            rho, pval = spearmanr(valid["vpin_proxy"], valid[horizon].abs())
            rows.append({"signal": "vpin→|ret|", "symbol": sym, "horizon": horizon,
                         "rho": rho, "pval": pval, "n": len(valid)})
            # VPIN → directional (shouldn't work — VPIN is symmetric)
            rho2, pval2 = spearmanr(valid["vpin_proxy"], valid[horizon])
            rows.append({"signal": "vpin→ret", "symbol": sym, "horizon": horizon,
                         "rho": rho2, "pval": pval2, "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# H. Volatility regimes
# ─────────────────────────────────────────────────────────────────────

def section_h_vol_regimes(m: pd.DataFrame) -> pd.DataFrame:
    """Les signaux marchent-ils mieux en régime calme ou volatile?"""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym].copy()
        # Realized vol = rolling std of ret_5s over 60 periods (5 min)
        sub["rvol_5m"] = sub["ret_5s"].rolling(60).std()
        med_vol = sub["rvol_5m"].median()
        for regime, label in [(sub["rvol_5m"] <= med_vol, "low_vol"),
                              (sub["rvol_5m"] > med_vol, "high_vol")]:
            s = sub[regime]
            for sig in ("tob_bid_ratio", "ofi_5s", "book_velocity"):
                for horizon in ("ret_10s", "ret_30s", "ret_60s"):
                    valid = s.dropna(subset=[sig, horizon])
                    if len(valid) < 200:
                        continue
                    rho, pval = spearmanr(valid[sig], valid[horizon])
                    rows.append({"regime": label, "signal": sig, "symbol": sym,
                                 "horizon": horizon, "rho": rho, "pval": pval,
                                 "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# I. Intraday seasonality
# ─────────────────────────────────────────────────────────────────────

def section_i_intraday(m: pd.DataFrame) -> pd.DataFrame:
    """Patterns heure par heure: volume, spread, volatilité."""
    rows = []
    for sym in m["symbol"].unique():
        sub = m[m["symbol"] == sym]
        for hour in range(24):
            h = sub[sub["hour"] == hour]
            if len(h) < 100:
                continue
            rows.append({
                "symbol": sym, "hour": hour,
                "mean_trades": h["trade_count"].mean(),
                "mean_notional": h["total_notional"].mean(),
                "mean_spread_bps": h["avg_spread_bps"].mean(),
                "mean_abs_ret_5s": h["ret_5s"].abs().mean() * 1e4 if "ret_5s" in h else np.nan,
                "mean_imbalance": h["tob_bid_ratio"].mean(),
                "n": len(h),
            })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# J. Session × Signal matrix
# ─────────────────────────────────────────────────────────────────────

def section_j_session_matrix(m: pd.DataFrame) -> pd.DataFrame:
    """Heatmap complète : chaque signal × session × symbole → rho."""
    signals = ["tob_bid_ratio", "ofi_5s", "book_velocity", "intensity_z",
               "spread_z", "vpin_proxy", "max_trade_rel"]
    horizon = "ret_30s"
    rows = []
    for sym in m["symbol"].unique():
        for session in list(SESSIONS.keys()) + ["all"]:
            sub = m[m["symbol"] == sym]
            if session != "all":
                sub = sub[sub["session"] == session]
            for sig in signals:
                valid = sub.dropna(subset=[sig, horizon])
                if len(valid) < 50:
                    continue
                rho, pval = spearmanr(valid[sig], valid[horizon])
                rows.append({"symbol": sym, "session": session, "signal": sig,
                             "rho": rho, "pval": pval, "n": len(valid)})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
# Plots
# ─────────────────────────────────────────────────────────────────────

def plot_lead_lag(ll_df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(14, 6))
    for pair in ll_df["pair"].unique():
        sub = ll_df[ll_df["pair"] == pair]
        ax.plot(sub["lag_s"], sub["corr"], linewidth=2, label=pair)
    ax.axvline(0, color="yellow", linewidth=1, linestyle="--", alpha=0.7)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Lag (seconds) — positive = first asset LEADS")
    ax.set_ylabel("Cross-correlation of 5s returns")
    ax.set_title("Cross-Asset Lead-Lag (5s resolution)")
    ax.legend()
    plt.tight_layout()
    savefig("deep_lead_lag.png")


def plot_lead_lag_sessions(sess_df: pd.DataFrame) -> None:
    if sess_df.empty:
        return
    pairs = sess_df["pair"].unique()
    sessions = sess_df["session"].unique()
    fig, axes = plt.subplots(1, len(pairs), figsize=(6 * len(pairs), 5), squeeze=False)
    colors = {"asian": "#e74c3c", "european": "#3498db", "us": "#2ecc71", "overnight": "#f39c12"}
    for i, pair in enumerate(pairs):
        ax = axes[0, i]
        for session in sessions:
            sub = sess_df[(sess_df["pair"] == pair) & (sess_df["session"] == session)]
            if sub.empty:
                continue
            ax.plot(sub["lag_s"], sub["corr"], marker="o", linewidth=1.5,
                    label=session, color=colors.get(session, "white"), markersize=4)
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_title(pair)
        ax.set_xlabel("Lag (s)")
        ax.legend(fontsize=8)
    axes[0, 0].set_ylabel("Correlation")
    fig.suptitle("Lead-Lag by Session", fontsize=14)
    plt.tight_layout()
    savefig("deep_lead_lag_sessions.png")


def plot_session_signal_heatmap(matrix_df: pd.DataFrame) -> None:
    for sym in matrix_df["symbol"].unique():
        sub = matrix_df[matrix_df["symbol"] == sym]
        pivot = sub.pivot_table(index="signal", columns="session", values="rho")
        col_order = [c for c in ["asian","european","us","overnight","all"] if c in pivot.columns]
        pivot = pivot[col_order]
        if pivot.empty:
            continue
        fig, ax = plt.subplots(figsize=(10, 6))
        vmax = max(0.05, np.nanmax(np.abs(pivot.values)))
        im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                v = pivot.values[i, j]
                if np.isfinite(v):
                    ax.text(j, i, f"{v:.3f}", ha="center", va="center",
                            color="black", fontsize=9, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Spearman rho vs ret_30s")
        ax.set_title(f"Signal × Session — {sym}")
        plt.tight_layout()
        savefig(f"deep_session_matrix_{sym}.png")


def plot_vol_regime_comparison(vol_df: pd.DataFrame) -> None:
    if vol_df.empty:
        return
    horizon = "ret_30s"
    sub = vol_df[vol_df["horizon"] == horizon]
    for sym in sub["symbol"].unique():
        s = sub[sub["symbol"] == sym]
        pivot = s.pivot_table(index="signal", columns="regime", values="rho")
        if pivot.empty or len(pivot.columns) < 2:
            continue
        fig, ax = plt.subplots(figsize=(8, 4))
        x = np.arange(len(pivot.index))
        w = 0.35
        ax.bar(x - w/2, pivot.get("low_vol", 0), w, label="Low Vol", color="#3498db")
        ax.bar(x + w/2, pivot.get("high_vol", 0), w, label="High Vol", color="#e74c3c")
        ax.set_xticks(x)
        ax.set_xticklabels(pivot.index, rotation=15, ha="right")
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_ylabel(f"Spearman rho ({horizon})")
        ax.set_title(f"Signal Strength by Vol Regime — {sym}")
        ax.legend()
        plt.tight_layout()
        savefig(f"deep_vol_regime_{sym}.png")


def plot_intraday(intra_df: pd.DataFrame) -> None:
    if intra_df.empty:
        return
    metrics = [("mean_notional", "Notional ($)"), ("mean_spread_bps", "Spread (bps)"),
               ("mean_abs_ret_5s", "|5s ret| (bps)")]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, (col, label) in zip(axes, metrics):
        for sym in intra_df["symbol"].unique():
            sub = intra_df[intra_df["symbol"] == sym].sort_values("hour")
            ax.plot(sub["hour"], sub[col], marker="o", linewidth=1.5, label=sym, markersize=4)
        for h in (8, 14, 21):
            ax.axvline(h, color="yellow", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_xlabel("UTC Hour")
        ax.set_ylabel(label)
        ax.legend(fontsize=8)
    fig.suptitle("Intraday Seasonality", fontsize=14)
    plt.tight_layout()
    savefig("deep_intraday.png")


def plot_whale_vs_retail(whale_df: pd.DataFrame) -> None:
    if whale_df.empty:
        return
    horizon = "ret_5m"
    sub = whale_df[whale_df["horizon"] == horizon]
    if sub.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    symbols = sub["symbol"].unique()
    x = np.arange(len(symbols))
    w = 0.35
    for i, sig in enumerate(["whale_ofi", "retail_ofi"]):
        vals = [sub[(sub["symbol"]==s) & (sub["signal"]==sig)]["rho"].values for s in symbols]
        vals = [v[0] if len(v)>0 else 0 for v in vals]
        ax.bar(x + (i-0.5)*w, vals, w, label=sig.replace("_"," ").title(),
               color=["#e74c3c","#3498db"][i], edgecolor="white", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(symbols)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel(f"Spearman rho ({horizon})")
    ax.set_title("Whale vs Retail OFI — Predictive Power")
    ax.legend()
    plt.tight_layout()
    savefig("deep_whale_vs_retail.png")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def run() -> dict:
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 05 — Deep Signal Exploration")
    print("=" * 70)

    print("\nLoading data...")
    data = load_all_data()
    m = data["merged"]

    # ── A ────────────────────────────────────────────────────────────
    print("\n── A. Cross-Asset Lead-Lag ──")
    ll_df, ll_sess = section_a_lead_lag(data["book_5s"])
    # Find peak lag for each pair
    for pair in ll_df["pair"].unique():
        sub = ll_df[(ll_df["pair"] == pair) & (ll_df["lag_units"] > 0)]
        if sub.empty:
            continue
        peak = sub.loc[sub["corr"].idxmax()]
        print(f"  {pair}: peak at lag={peak['lag_s']:.0f}s, corr={peak['corr']:.4f}")
    ll_df.to_csv(f"{OUTPUT_DIR}/deep_lead_lag.csv", index=False)

    # ── B ────────────────────────────────────────────────────────────
    print("\n── B. Trade Intensity Spikes ──")
    ti_df = section_b_trade_intensity(m)
    if not ti_df.empty:
        # Show directional spikes
        spikes = ti_df[ti_df["signal"].str.contains("spike")]
        if not spikes.empty:
            print(spikes.to_string(index=False, float_format="%.3f"))
        # Show intensity_z rho
        iz = ti_df[ti_df["signal"] == "intensity_z"]
        if not iz.empty:
            print(iz[["symbol","horizon","rho","pval"]].to_string(index=False, float_format="%.4f"))
        ti_df.to_csv(f"{OUTPUT_DIR}/deep_trade_intensity.csv", index=False)

    # ── C ────────────────────────────────────────────────────────────
    print("\n── C. Whale vs Retail Flow ──")
    wh_df = section_c_whale_flow(data["whale_1m"])
    if not wh_df.empty:
        print(wh_df.to_string(index=False, float_format="%.4f"))
        wh_df.to_csv(f"{OUTPUT_DIR}/deep_whale_flow.csv", index=False)

    # ── D ────────────────────────────────────────────────────────────
    print("\n── D. Book Velocity ──")
    bv_df = section_d_book_velocity(m)
    if not bv_df.empty:
        # Compare level vs velocity vs accel
        pivot = bv_df[bv_df["horizon"]=="ret_30s"].pivot_table(
            index="symbol", columns="signal", values="rho")
        print(pivot.to_string(float_format="%.4f"))
        bv_df.to_csv(f"{OUTPUT_DIR}/deep_book_velocity.csv", index=False)

    # ── E ────────────────────────────────────────────────────────────
    print("\n── E. Spread Dynamics ──")
    sp_df = section_e_spread_dynamics(m)
    if not sp_df.empty:
        print(sp_df.to_string(index=False, float_format="%.4f"))
        sp_df.to_csv(f"{OUTPUT_DIR}/deep_spread_dynamics.csv", index=False)

    # ── F ────────────────────────────────────────────────────────────
    print("\n── F. Kyle's Lambda ──")
    kl_df = section_f_kyle_lambda(m)
    if not kl_df.empty:
        print(kl_df.to_string(index=False, float_format="%.4f"))
        kl_df.to_csv(f"{OUTPUT_DIR}/deep_kyle_lambda.csv", index=False)

    # ── G ────────────────────────────────────────────────────────────
    print("\n── G. VPIN Proxy ──")
    vp_df = section_g_vpin(m)
    if not vp_df.empty:
        # Show only vpin→|ret| (the interesting one)
        print(vp_df[vp_df["signal"]=="vpin→|ret|"].to_string(index=False, float_format="%.4f"))
        vp_df.to_csv(f"{OUTPUT_DIR}/deep_vpin.csv", index=False)

    # ── H ────────────────────────────────────────────────────────────
    print("\n── H. Volatility Regimes ──")
    vr_df = section_h_vol_regimes(m)
    if not vr_df.empty:
        # Show ret_30s comparison
        comp = vr_df[vr_df["horizon"]=="ret_30s"]
        pivot = comp.pivot_table(index=["symbol","signal"], columns="regime", values="rho")
        if not pivot.empty:
            pivot["ratio_hi_lo"] = pivot.get("high_vol", 0) / pivot.get("low_vol", 1).replace(0, np.nan)
            print(pivot.to_string(float_format="%.4f"))
        vr_df.to_csv(f"{OUTPUT_DIR}/deep_vol_regimes.csv", index=False)

    # ── I ────────────────────────────────────────────────────────────
    print("\n── I. Intraday Seasonality ──")
    intra_df = section_i_intraday(m)
    if not intra_df.empty:
        intra_df.to_csv(f"{OUTPUT_DIR}/deep_intraday.csv", index=False)
        # Show peak hours
        for sym in intra_df["symbol"].unique():
            s = intra_df[intra_df["symbol"] == sym]
            peak_vol = s.loc[s["mean_notional"].idxmax()]
            peak_spread = s.loc[s["mean_spread_bps"].idxmax()]
            print(f"  {sym}: peak volume h={peak_vol['hour']:.0f}, "
                  f"peak spread h={peak_spread['hour']:.0f}")

    # ── J ────────────────────────────────────────────────────────────
    print("\n── J. Session × Signal Matrix ──")
    matrix_df = section_j_session_matrix(m)
    if not matrix_df.empty:
        # Highlight where Asia is best
        asia = matrix_df[matrix_df["session"] == "asian"]
        all_ = matrix_df[matrix_df["session"] == "all"]
        merged_ratio = asia.merge(all_, on=["symbol","signal"], suffixes=("_asia","_all"))
        merged_ratio["ratio"] = merged_ratio["rho_asia"] / merged_ratio["rho_all"].replace(0, np.nan)
        best_asia = merged_ratio.nlargest(10, "ratio")
        if not best_asia.empty:
            print("  Top 10 signals where Asian session outperforms:")
            print(best_asia[["symbol","signal","rho_asia","rho_all","ratio"]]
                  .to_string(index=False, float_format="%.4f"))
        matrix_df.to_csv(f"{OUTPUT_DIR}/deep_session_matrix.csv", index=False)

    # ── Plots ────────────────────────────────────────────────────────
    print("\nGenerating plots...")
    plot_lead_lag(ll_df)
    plot_lead_lag_sessions(ll_sess)
    plot_session_signal_heatmap(matrix_df)
    plot_vol_regime_comparison(vr_df)
    plot_intraday(intra_df)
    plot_whale_vs_retail(wh_df)

    # ── Final summary ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("DISCOVERY SUMMARY")
    print("=" * 70)
    all_results = []
    for label, df, rho_col in [
        ("Lead-lag", ll_df, "corr"),
        ("Trade intensity", ti_df, "rho"),
        ("Whale flow", wh_df, "rho"),
        ("Book velocity", bv_df, "rho"),
        ("Kyle lambda", kl_df, "rho"),
        ("VPIN", vp_df, "rho"),
    ]:
        if df.empty or rho_col not in df.columns:
            continue
        best = df.loc[df[rho_col].abs().idxmax()]
        all_results.append({"study": label, "best_rho": best[rho_col],
                            "details": str(best.to_dict())})
        print(f"  {label}: best |rho| = {abs(best[rho_col]):.4f}")

    return {
        "lead_lag": ll_df, "lead_lag_sessions": ll_sess,
        "trade_intensity": ti_df, "whale_flow": wh_df,
        "book_velocity": bv_df, "spread_dynamics": sp_df,
        "kyle_lambda": kl_df, "vpin": vp_df,
        "vol_regimes": vr_df, "intraday": intra_df,
        "session_matrix": matrix_df,
    }


if __name__ == "__main__":
    run()
