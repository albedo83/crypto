"""Study 06 — Swing backtest: funding, basis, OI divergence, liquidation bounces.

Horizon 1-8h. Tests whether these signals predict price on timeframes where fees are negligible.

Run: python3 -m analysis.study_06_swing
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from analysis.db import fetch_df
from analysis.utils import (
    ID_TO_SYMBOL, apply_dark_theme, savefig, OUTPUT_DIR,
)

COST_BPS = 4.0  # maker roundtrip


# ═════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════

def load_data() -> dict:
    print("  Loading data...")

    # Price at 1-minute (from trades)
    price_1m = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket,
               instrument_id,
               last(price, exchange_ts) AS close,
               sum(notional) AS volume,
               count(*) AS trades
        FROM trades_raw
        GROUP BY bucket, instrument_id
        ORDER BY instrument_id, bucket
    """)
    price_1m["bucket"] = pd.to_datetime(price_1m["bucket"], utc=True)
    price_1m["symbol"] = price_1m["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    price_1m: {len(price_1m)} rows")

    # Funding events
    funding = fetch_df("""
        SELECT exchange_ts, instrument_id, funding_rate
        FROM funding ORDER BY instrument_id, exchange_ts
    """)
    funding["exchange_ts"] = pd.to_datetime(funding["exchange_ts"], utc=True)
    funding["symbol"] = funding["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    funding: {len(funding)} rows")

    # Mark/index (basis) — sample to 1 min
    basis_1m = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket,
               instrument_id,
               last(mark_price, exchange_ts) AS mark,
               last(index_price, exchange_ts) AS spot,
               last(basis_bps, exchange_ts) AS basis_bps,
               last(funding_rate, exchange_ts) AS live_funding
        FROM mark_index
        GROUP BY bucket, instrument_id
        ORDER BY instrument_id, bucket
    """)
    basis_1m["bucket"] = pd.to_datetime(basis_1m["bucket"], utc=True)
    basis_1m["symbol"] = basis_1m["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    basis_1m: {len(basis_1m)} rows")

    # Open interest
    oi = fetch_df("""
        SELECT exchange_ts, instrument_id, open_interest
        FROM open_interest ORDER BY instrument_id, exchange_ts
    """)
    oi["exchange_ts"] = pd.to_datetime(oi["exchange_ts"], utc=True)
    oi["symbol"] = oi["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    open_interest: {len(oi)} rows")

    # Liquidation clusters
    liq = fetch_df("""
        SELECT * FROM liquidation_clusters(p_gap_seconds := 120)
    """)
    if not liq.empty:
        liq["cluster_start"] = pd.to_datetime(liq["cluster_start"], utc=True)
        liq["cluster_end"] = pd.to_datetime(liq["cluster_end"], utc=True)
        liq["symbol"] = liq["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    liq_clusters: {len(liq)} rows")

    return {
        "price_1m": price_1m, "funding": funding,
        "basis_1m": basis_1m, "oi": oi, "liq": liq,
    }


# ═════════════════════════════════════════════════════════════════════
# SIGNAL 1: FUNDING RATE PRE-SETTLEMENT DRIFT
# ═════════════════════════════════════════════════════════════════════

def test_funding_drift(price_1m: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Before high funding settlement, does price drift in the expected direction?"""
    rows = []
    for _, f in funding.iterrows():
        settle_time = f["exchange_ts"]
        iid = f["instrument_id"]
        sym = f["symbol"]
        rate = float(f["funding_rate"])

        prices = price_1m[
            (price_1m["instrument_id"] == iid)
            & (price_1m["bucket"] >= settle_time - pd.Timedelta(hours=4))
            & (price_1m["bucket"] <= settle_time + pd.Timedelta(hours=4))
        ].sort_values("bucket")

        if len(prices) < 30:
            continue

        settle_price_rows = prices[prices["bucket"] <= settle_time]
        if settle_price_rows.empty:
            continue
        settle_price = settle_price_rows.iloc[-1]["close"]

        for hours_before in (1, 2, 4):
            t_before = settle_time - pd.Timedelta(hours=hours_before)
            before_rows = prices[prices["bucket"] <= t_before]
            if before_rows.empty:
                continue
            price_before = before_rows.iloc[-1]["close"]
            drift_bps = (settle_price / price_before - 1) * 1e4

            row = {
                "symbol": sym, "settle_time": settle_time,
                "funding_rate": rate, "funding_bps": rate * 1e4,
                "hours_before": hours_before,
                "drift_bps": drift_bps,
            }

            # If funding > 0 (longs pay), expect price to drop pre-settlement
            # Signal: short when funding high, long when funding low
            if rate > 0:
                row["expected_direction"] = "DOWN"
                row["signal_correct"] = drift_bps < 0
            else:
                row["expected_direction"] = "UP"
                row["signal_correct"] = drift_bps > 0
            rows.append(row)

        # Post-settlement: does price revert after funding paid?
        for hours_after in (1, 2, 4):
            t_after = settle_time + pd.Timedelta(hours=hours_after)
            after_rows = prices[prices["bucket"] >= t_after]
            if after_rows.empty:
                continue
            price_after = after_rows.iloc[0]["close"]
            revert_bps = (price_after / settle_price - 1) * 1e4
            rows.append({
                "symbol": sym, "settle_time": settle_time,
                "funding_rate": rate, "funding_bps": rate * 1e4,
                "hours_before": -hours_after,  # negative = after
                "drift_bps": revert_bps,
                "expected_direction": "UP" if rate > 0 else "DOWN",
                "signal_correct": (revert_bps > 0) if rate > 0 else (revert_bps < 0),
            })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# SIGNAL 2: BASIS MEAN-REVERSION
# ═════════════════════════════════════════════════════════════════════

def test_basis_reversion(basis_1m: pd.DataFrame, price_1m: pd.DataFrame) -> pd.DataFrame:
    """When basis is extreme, does it revert? And does price follow?"""
    rows = []
    for iid in basis_1m["instrument_id"].unique():
        sym = ID_TO_SYMBOL[iid]
        b = basis_1m[basis_1m["instrument_id"] == iid].sort_values("bucket").copy()
        p = price_1m[price_1m["instrument_id"] == iid].sort_values("bucket")

        if len(b) < 100:
            continue

        b["basis_z"] = (b["basis_bps"] - b["basis_bps"].rolling(120).mean()) / b["basis_bps"].rolling(120).std()

        # Forward price returns at various horizons
        merged = b.merge(p[["bucket", "close"]], on="bucket", how="inner")
        for mins in (30, 60, 120, 240):
            shift = mins  # 1-min buckets
            merged[f"ret_{mins}m"] = merged["close"].pct_change(shift).shift(-shift) * 1e4

        for _, r in merged.dropna(subset=["basis_z", "ret_60m"]).iterrows():
            for horizon in ("ret_30m", "ret_60m", "ret_120m", "ret_240m"):
                if pd.isna(r.get(horizon)):
                    continue
                rows.append({
                    "symbol": sym,
                    "bucket": r["bucket"],
                    "basis_bps": r["basis_bps"],
                    "basis_z": r["basis_z"],
                    "horizon": horizon,
                    "ret_bps": r[horizon],
                })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# SIGNAL 3: OI × PRICE DIVERGENCE
# ═════════════════════════════════════════════════════════════════════

def test_oi_divergence(oi: pd.DataFrame, price_1m: pd.DataFrame) -> pd.DataFrame:
    """Price up + OI down = weak move (fade it). Price up + OI up = strong (follow)."""
    rows = []
    for iid in oi["instrument_id"].unique():
        sym = ID_TO_SYMBOL[iid]
        o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts").copy()
        p = price_1m[price_1m["instrument_id"] == iid].sort_values("bucket")

        if len(o) < 10:
            continue

        # OI change over last reading (~5 min)
        o["oi_change_pct"] = o["open_interest"].pct_change() * 100
        o["bucket"] = o["exchange_ts"].dt.floor("5min")

        # Price change over same window
        p5 = p.copy()
        p5["bucket"] = p5["bucket"].dt.floor("5min")
        p5_agg = p5.groupby("bucket").agg(close=("close", "last")).reset_index()

        merged = o.merge(p5_agg, on="bucket", how="inner")
        merged["price_ret_5m"] = merged["close"].pct_change() * 1e4

        # Forward returns
        for periods, label in [(6, "30m"), (12, "60m"), (24, "120m"), (48, "240m")]:
            merged[f"fwd_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

        # Classify: price up + OI down = "weak_long", price down + OI down = "weak_short"
        for _, r in merged.dropna(subset=["oi_change_pct", "price_ret_5m"]).iterrows():
            price_up = r["price_ret_5m"] > 5  # > 5 bps move
            price_down = r["price_ret_5m"] < -5
            oi_up = r["oi_change_pct"] > 0.05
            oi_down = r["oi_change_pct"] < -0.05

            if price_up and oi_down:
                regime = "weak_long"
            elif price_down and oi_up:
                regime = "weak_short"
            elif price_up and oi_up:
                regime = "strong_long"
            elif price_down and oi_down:
                regime = "strong_short"
            else:
                regime = "neutral"

            for horizon in ("fwd_30m", "fwd_60m", "fwd_120m", "fwd_240m"):
                if pd.isna(r.get(horizon)):
                    continue
                rows.append({
                    "symbol": sym, "regime": regime,
                    "price_ret_5m": r["price_ret_5m"],
                    "oi_change_pct": r["oi_change_pct"],
                    "horizon": horizon,
                    "fwd_ret_bps": r[horizon],
                })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# SIGNAL 4: LIQUIDATION CASCADE BOUNCE (extended horizon)
# ═════════════════════════════════════════════════════════════════════

def test_liq_bounce_extended(liq: pd.DataFrame, price_1m: pd.DataFrame) -> pd.DataFrame:
    """After large liquidation clusters, does price revert over 1-4 hours?"""
    if liq.empty:
        return pd.DataFrame()

    # Top clusters by notional
    big = liq.nlargest(100, "total_notional")
    rows = []
    for _, cl in big.iterrows():
        t0 = cl["cluster_end"]
        iid = cl["instrument_id"]
        sym = cl["symbol"]

        prices = price_1m[
            (price_1m["instrument_id"] == iid)
            & (price_1m["bucket"] >= t0 - pd.Timedelta(minutes=5))
            & (price_1m["bucket"] <= t0 + pd.Timedelta(hours=4))
        ].sort_values("bucket")

        if len(prices) < 10:
            continue

        ref_rows = prices[prices["bucket"] <= t0]
        if ref_rows.empty:
            continue
        ref_price = ref_rows.iloc[-1]["close"]

        for mins in (5, 15, 30, 60, 120, 240):
            t_target = t0 + pd.Timedelta(minutes=mins)
            target_rows = prices[prices["bucket"] <= t_target]
            if target_rows.empty:
                continue
            target_price = target_rows.iloc[-1]["close"]
            ret = (target_price / ref_price - 1) * 1e4

            # Bounce = reversal from dominant side
            if cl["dominant_side"] == "SELL":
                bounce = ret > 0
            else:
                bounce = ret < 0

            rows.append({
                "symbol": sym,
                "cluster_time": t0,
                "dominant_side": cl["dominant_side"],
                "liq_count": cl["liq_count"],
                "total_notional": cl["total_notional"],
                "horizon_min": mins,
                "ret_bps": ret,
                "bounce": bounce,
            })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# COMBINED BACKTEST
# ═════════════════════════════════════════════════════════════════════

def backtest_combined(price_1m, funding, basis_1m, oi, liq) -> pd.DataFrame:
    """Simple combined strategy backtest on 1h horizon."""
    trades = []

    for iid in [1, 2, 3]:
        sym = ID_TO_SYMBOL[iid]
        p = price_1m[price_1m["instrument_id"] == iid].sort_values("bucket").copy()
        b = basis_1m[basis_1m["instrument_id"] == iid].sort_values("bucket")
        o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts")

        if len(p) < 200:
            continue

        # Merge basis
        p = p.merge(b[["bucket", "basis_bps"]], on="bucket", how="left")
        p["basis_bps"] = p["basis_bps"].ffill()

        # Merge OI (forward-fill 5-min data to 1-min)
        o_renamed = o.rename(columns={"exchange_ts": "bucket"})[["bucket", "open_interest"]]
        o_renamed["bucket"] = o_renamed["bucket"].dt.floor("1min")
        p = p.merge(o_renamed, on="bucket", how="left")
        p["open_interest"] = p["open_interest"].ffill()

        # Compute features
        p["basis_z"] = (p["basis_bps"] - p["basis_bps"].rolling(60).mean()) / p["basis_bps"].rolling(60).std()
        p["oi_change"] = p["open_interest"].pct_change(5) * 100  # 5-min OI change
        p["price_change_5m"] = p["close"].pct_change(5) * 1e4
        p["ret_60m"] = p["close"].pct_change(60).shift(-60) * 1e4

        # Add funding info
        f_sym = funding[funding["instrument_id"] == iid]
        p["next_funding_rate"] = np.nan
        p["minutes_to_settlement"] = np.nan
        for _, f in f_sym.iterrows():
            ft = f["exchange_ts"]
            mask = (p["bucket"] >= ft - pd.Timedelta(hours=8)) & (p["bucket"] < ft)
            p.loc[mask, "next_funding_rate"] = float(f["funding_rate"])
            p.loc[mask, "minutes_to_settlement"] = (ft - p.loc[mask, "bucket"]).dt.total_seconds() / 60

        # Signal scoring
        p["signal"] = 0.0

        # Basis extreme → mean revert (short when basis high)
        p["signal"] -= p["basis_z"].clip(-3, 3) * 0.3

        # OI divergence: price up + OI down → fade (short)
        weak_long = (p["price_change_5m"] > 5) & (p["oi_change"] < -0.05)
        weak_short = (p["price_change_5m"] < -5) & (p["oi_change"] > 0.05)
        p.loc[weak_long, "signal"] -= 0.3
        p.loc[weak_short, "signal"] += 0.3

        # Funding pre-settlement: short when funding high & < 2h to settle
        near_settle = p["minutes_to_settlement"].between(0, 120)
        high_funding = p["next_funding_rate"] > 0.0005  # > 5 bps
        low_funding = p["next_funding_rate"] < -0.0005
        p.loc[near_settle & high_funding, "signal"] -= 0.4
        p.loc[near_settle & low_funding, "signal"] += 0.4

        # Trade when signal is strong enough
        valid = p.dropna(subset=["signal", "ret_60m"])
        position = 0
        entry_bar = 0

        for i in range(len(valid)):
            idx = valid.index[i]
            sig = valid.loc[idx, "signal"]
            ret = valid.loc[idx, "ret_60m"]

            if position == 0:
                if sig > 0.5:
                    position = 1
                    entry_bar = i
                    entry_sig = sig
                elif sig < -0.5:
                    position = -1
                    entry_bar = i
                    entry_sig = sig
            else:
                if i - entry_bar >= 60:  # hold 60 min
                    gross = position * ret
                    net = gross - COST_BPS
                    trades.append({
                        "symbol": sym,
                        "direction": "LONG" if position == 1 else "SHORT",
                        "entry_time": str(valid.loc[valid.index[entry_bar], "bucket"]),
                        "signal_score": entry_sig,
                        "gross_bps": gross,
                        "net_bps": net,
                        "basis_z": valid.loc[valid.index[entry_bar], "basis_z"],
                    })
                    position = 0

    return pd.DataFrame(trades)


# ═════════════════════════════════════════════════════════════════════
# PLOTS
# ═════════════════════════════════════════════════════════════════════

def plot_funding_drift(fd: pd.DataFrame):
    if fd.empty:
        return
    # Pre-settlement drift by funding level
    pre = fd[fd["hours_before"] > 0]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Scatter: funding rate vs drift
    for ax, h in zip(axes, [2, 4]):
        sub = pre[pre["hours_before"] == h]
        if sub.empty:
            continue
        colors = ["#3fb950" if c else "#f85149" for c in sub["signal_correct"]]
        ax.scatter(sub["funding_bps"], sub["drift_bps"], c=colors, alpha=0.7, s=40)
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.axvline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_xlabel("Funding Rate (bps)")
        ax.set_ylabel(f"Price Drift {h}h Before Settlement (bps)")
        ax.set_title(f"{h}h Pre-Settlement | Hit={sub['signal_correct'].mean():.0%} (n={len(sub)})")
    plt.tight_layout()
    savefig("swing_funding_drift.png")

    # Post-settlement reversion
    post = fd[fd["hours_before"] < 0]
    if not post.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        for h in [-1, -2, -4]:
            sub = post[post["hours_before"] == h]
            if sub.empty:
                continue
            hit = sub["signal_correct"].mean()
            avg = sub["drift_bps"].abs().mean()
            ax.bar(f"{-h}h post", hit, color="#3fb950" if hit > 0.5 else "#f85149")
            ax.text(f"{-h}h post", hit + 0.02, f"{avg:.0f}bps avg", ha="center", fontsize=10)
        ax.axhline(0.5, color="white", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_ylabel("Hit Rate (correct direction)")
        ax.set_title("Post-Settlement Reversion")
        ax.set_ylim(0, 1)
        plt.tight_layout()
        savefig("swing_funding_post.png")


def plot_basis_signal(basis_df: pd.DataFrame):
    if basis_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, horizon in zip(axes, ["ret_60m", "ret_240m"]):
        sub = basis_df[basis_df["horizon"] == horizon]
        if sub.empty:
            continue
        for sym in sub["symbol"].unique():
            s = sub[sub["symbol"] == sym].dropna(subset=["basis_z", "ret_bps"])
            if len(s) < 50:
                continue
            # Quintile analysis
            s = s.copy()
            s["q"] = pd.qcut(s["basis_z"], 5, labels=False, duplicates="drop")
            means = s.groupby("q")["ret_bps"].mean()
            ax.plot(means.index, means.values, marker="o", linewidth=2, label=sym)
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_xlabel("Basis Z-score Quintile (0=low, 4=high)")
        ax.set_ylabel("Mean Forward Return (bps)")
        ax.set_title(f"Basis → {horizon.replace('ret_', '')} Return")
        ax.legend()
    plt.tight_layout()
    savefig("swing_basis_quintiles.png")


def plot_oi_regimes(oi_df: pd.DataFrame):
    if oi_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, horizon in zip(axes, ["fwd_60m", "fwd_240m"]):
        sub = oi_df[oi_df["horizon"] == horizon]
        if sub.empty:
            continue
        regimes = ["weak_long", "strong_long", "neutral", "strong_short", "weak_short"]
        colors = {"weak_long": "#f85149", "strong_long": "#3fb950",
                  "neutral": "#7d8590", "strong_short": "#3fb950", "weak_short": "#f85149"}
        vals = []
        labels = []
        for regime in regimes:
            s = sub[sub["regime"] == regime]
            if len(s) > 5:
                vals.append(s["fwd_ret_bps"].mean())
                labels.append(f"{regime}\n(n={len(s)})")
            else:
                vals.append(0)
                labels.append(f"{regime}\n(n<5)")
        bar_colors = [colors.get(r, "#7d8590") for r in regimes]
        ax.bar(labels, vals, color=bar_colors, edgecolor="white", linewidth=0.5)
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_ylabel("Mean Forward Return (bps)")
        ax.set_title(f"OI Regime → {horizon.replace('fwd_', '')} Return")
    plt.tight_layout()
    savefig("swing_oi_regimes.png")


def plot_liq_bounce(liq_df: pd.DataFrame):
    if liq_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, side in zip(axes, ["SELL", "BUY"]):
        sub = liq_df[liq_df["dominant_side"] == side]
        if sub.empty:
            continue
        pivoted = sub.groupby("horizon_min").agg(
            bounce_rate=("bounce", "mean"),
            avg_ret=("ret_bps", "mean"),
            n=("bounce", "count"),
        )
        ax.bar(pivoted.index, pivoted["bounce_rate"], width=8,
               color="#3fb950", edgecolor="white", linewidth=0.5)
        ax.axhline(0.5, color="white", linewidth=0.5, linestyle="--", alpha=0.5)
        for i, row in pivoted.iterrows():
            ax.text(i, row["bounce_rate"] + 0.02,
                    f"{row['avg_ret']:.0f}bps\nn={row['n']:.0f}",
                    ha="center", fontsize=9)
        ax.set_xlabel("Minutes After Cluster")
        ax.set_ylabel("Bounce Rate")
        ax.set_title(f"{side}-dominant Liquidations")
        ax.set_ylim(0, 1)
    plt.tight_layout()
    savefig("swing_liq_bounce.png")


def plot_combined_backtest(bt: pd.DataFrame):
    if bt.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # P&L curve
    ax = axes[0]
    for sym in bt["symbol"].unique():
        t = bt[bt["symbol"] == sym]
        ax.plot(range(len(t)), t["net_bps"].cumsum().values, linewidth=2, label=sym)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Cumulative Net P&L (bps)")
    ax.set_title("Combined Strategy — Net P&L")
    ax.legend()

    # Per-symbol stats
    ax = axes[1]
    syms = bt["symbol"].unique()
    gross = [bt[bt["symbol"] == s]["gross_bps"].mean() for s in syms]
    net = [bt[bt["symbol"] == s]["net_bps"].mean() for s in syms]
    x = np.arange(len(syms))
    ax.bar(x - 0.15, gross, 0.3, label="Gross", color="#3fb950")
    ax.bar(x + 0.15, net, 0.3, label="Net", color="#f85149")
    ax.set_xticks(x)
    ax.set_xticklabels(syms)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Mean P&L per trade (bps)")
    ax.set_title("Per-Symbol Performance")
    ax.legend()

    plt.tight_layout()
    savefig("swing_combined_backtest.png")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 06 — Swing Strategy Backtest (1-8h horizon)")
    print("=" * 70)

    data = load_data()

    # ── Signal 1: Funding ────────────────────────────────────────
    print("\n── 1. Funding Pre-Settlement Drift ──")
    fd = test_funding_drift(data["price_1m"], data["funding"])
    if not fd.empty:
        pre = fd[fd["hours_before"] > 0]
        for h in (1, 2, 4):
            sub = pre[pre["hours_before"] == h]
            if sub.empty:
                continue
            hit = sub["signal_correct"].mean()
            avg_drift = sub["drift_bps"].mean()
            avg_abs = sub["drift_bps"].abs().mean()
            print(f"  {h}h before: hit rate {hit:.0%} | avg drift {avg_drift:+.1f} bps | "
                  f"avg |drift| {avg_abs:.1f} bps (n={len(sub)})")
        post = fd[fd["hours_before"] < 0]
        for h in (-1, -2, -4):
            sub = post[post["hours_before"] == h]
            if sub.empty:
                continue
            hit = sub["signal_correct"].mean()
            avg = sub["drift_bps"].abs().mean()
            print(f"  {-h}h after:  hit rate {hit:.0%} | avg |move| {avg:.1f} bps (n={len(sub)})")
        fd.to_csv(f"{OUTPUT_DIR}/swing_funding.csv", index=False)

    # ── Signal 2: Basis ──────────────────────────────────────────
    print("\n── 2. Basis Mean-Reversion ──")
    bd = test_basis_reversion(data["basis_1m"], data["price_1m"])
    if not bd.empty:
        for horizon in ("ret_60m", "ret_120m", "ret_240m"):
            sub = bd[bd["horizon"] == horizon]
            for sym in sub["symbol"].unique():
                s = sub[sub["symbol"] == sym].dropna(subset=["basis_z", "ret_bps"])
                if len(s) < 50:
                    continue
                rho, pval = spearmanr(s["basis_z"], s["ret_bps"])
                print(f"  {sym} {horizon}: rho={rho:+.4f} p={pval:.4f} (n={len(s)})")
        bd.to_csv(f"{OUTPUT_DIR}/swing_basis.csv", index=False)

    # ── Signal 3: OI Divergence ──────────────────────────────────
    print("\n── 3. OI × Price Divergence ──")
    oi_df = test_oi_divergence(data["oi"], data["price_1m"])
    if not oi_df.empty:
        for horizon in ("fwd_60m", "fwd_120m", "fwd_240m"):
            sub = oi_df[oi_df["horizon"] == horizon]
            print(f"  {horizon}:")
            for regime in ("weak_long", "strong_long", "weak_short", "strong_short"):
                s = sub[sub["regime"] == regime]
                if len(s) > 5:
                    print(f"    {regime:15s}: avg {s['fwd_ret_bps'].mean():+.1f} bps (n={len(s)})")
        oi_df.to_csv(f"{OUTPUT_DIR}/swing_oi.csv", index=False)

    # ── Signal 4: Liquidation bounce ─────────────────────────────
    print("\n── 4. Liquidation Bounce (extended) ──")
    lb = test_liq_bounce_extended(data["liq"], data["price_1m"])
    if not lb.empty:
        for mins in (5, 15, 30, 60, 120, 240):
            sub = lb[lb["horizon_min"] == mins]
            if len(sub) < 5:
                continue
            hit = sub["bounce"].mean()
            avg = sub["ret_bps"].mean()
            print(f"  {mins:3d}min: bounce rate {hit:.0%} | avg ret {avg:+.1f} bps (n={len(sub)})")
        lb.to_csv(f"{OUTPUT_DIR}/swing_liq_bounce.csv", index=False)

    # ── Combined backtest ────────────────────────────────────────
    print("\n── 5. Combined Strategy Backtest ──")
    bt = backtest_combined(data["price_1m"], data["funding"],
                          data["basis_1m"], data["oi"], data["liq"])
    if not bt.empty:
        for sym in bt["symbol"].unique():
            t = bt[bt["symbol"] == sym]
            print(f"  {sym}: {len(t)} trades | "
                  f"gross {t['gross_bps'].mean():+.1f} bps/trade | "
                  f"net {t['net_bps'].mean():+.1f} bps/trade | "
                  f"win {(t['gross_bps']>0).mean():.0%} | "
                  f"total net {t['net_bps'].sum():+.0f} bps")
        bt.to_csv(f"{OUTPUT_DIR}/swing_backtest.csv", index=False)

        total_gross = bt["gross_bps"].sum()
        total_net = bt["net_bps"].sum()
        print(f"\n  TOTAL: {len(bt)} trades | gross {total_gross:+.0f} bps | "
              f"net {total_net:+.0f} bps | "
              f"avg {bt['net_bps'].mean():+.1f} bps/trade")

    # ── Plots ────────────────────────────────────────────────────
    print("\nGenerating plots...")
    plot_funding_drift(fd)
    plot_basis_signal(bd)
    plot_oi_regimes(oi_df)
    plot_liq_bounce(lb)
    plot_combined_backtest(bt)

    return {"funding": fd, "basis": bd, "oi": oi_df, "liq_bounce": lb, "backtest": bt}


if __name__ == "__main__":
    run()
