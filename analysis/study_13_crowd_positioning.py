"""Study 13 — Crowd Positioning: the crowd is always wrong.

Binance publishes free real-time data:
  - Top Trader Long/Short Position Ratio
  - Top Trader Long/Short Account Ratio
  - Global Long/Short Account Ratio

Thesis: extreme crowd positioning = contrarian signal.
When 75%+ are long → short. When top traders diverge from crowd → follow top.

Run: python3 -m analysis.study_13_crowd_positioning
"""

from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr
from datetime import datetime, timezone

from analysis.utils import apply_dark_theme, savefig, OUTPUT_DIR

SYMBOLS = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "BNBUSDT", "SUIUSDT", "AVAXUSDT", "LINKUSDT",
           "TRXUSDT", "XMRUSDT", "LTCUSDT", "BCHUSDT", "AAVEUSDT"]

COST_BPS = 4.0


async def fetch_ls_ratio(session, symbol, period="5m", limit=500):
    """Global long/short account ratio."""
    url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return pd.DataFrame()
            data = await resp.json()
    except Exception:
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["long_pct"] = df["longAccount"].astype(float)
    df["short_pct"] = df["shortAccount"].astype(float)
    df["ls_ratio"] = df["longShortRatio"].astype(float)
    df["symbol"] = symbol
    return df[["time", "symbol", "long_pct", "short_pct", "ls_ratio"]]


async def fetch_top_ls_ratio(session, symbol, period="5m", limit=500):
    """Top trader long/short position ratio."""
    url = "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return pd.DataFrame()
            data = await resp.json()
    except Exception:
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["top_long_pct"] = df["longAccount"].astype(float)
    df["top_short_pct"] = df["shortAccount"].astype(float)
    df["top_ls_ratio"] = df["longShortRatio"].astype(float)
    df["symbol"] = symbol
    return df[["time", "symbol", "top_long_pct", "top_short_pct", "top_ls_ratio"]]


async def fetch_klines(session, symbol, interval="5m", limit=500):
    """Fetch klines for price data."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return pd.DataFrame()
            data = await resp.json()
    except Exception:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy",
        "taker_buy_quote", "ignore"
    ])
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    df["symbol"] = symbol
    return df[["time", "symbol", "close"]]


async def fetch_all():
    print("  Fetching Binance positioning data...")
    async with aiohttp.ClientSession() as session:
        # Batch with rate limiting
        all_global = []
        all_top = []
        all_klines = []
        for sym in SYMBOLS:
            g = await fetch_ls_ratio(session, sym)
            t = await fetch_top_ls_ratio(session, sym)
            k = await fetch_klines(session, sym)
            if not g.empty: all_global.append(g)
            if not t.empty: all_top.append(t)
            if not k.empty: all_klines.append(k)
            await asyncio.sleep(0.3)

    global_df = pd.concat(all_global, ignore_index=True) if all_global else pd.DataFrame()
    top_df = pd.concat(all_top, ignore_index=True) if all_top else pd.DataFrame()
    klines_df = pd.concat(all_klines, ignore_index=True) if all_klines else pd.DataFrame()

    print(f"  Global L/S: {len(global_df)} rows, {global_df['symbol'].nunique() if not global_df.empty else 0} symbols")
    print(f"  Top L/S: {len(top_df)} rows")
    print(f"  Klines: {len(klines_df)} rows")
    if not global_df.empty:
        print(f"  Range: {global_df['time'].min()} → {global_df['time'].max()}")

    return global_df, top_df, klines_df


def analyze_contrarian(global_df, klines_df):
    """When the crowd is extreme, does the price reverse?"""
    rows = []
    for sym in global_df["symbol"].unique():
        g = global_df[global_df["symbol"] == sym].sort_values("time")
        k = klines_df[klines_df["symbol"] == sym].sort_values("time")
        if g.empty or k.empty:
            continue

        merged = g.merge(k, on=["time", "symbol"], how="inner")
        if len(merged) < 50:
            continue

        # Z-score of long percentage
        merged["long_z"] = (merged["long_pct"] - merged["long_pct"].rolling(60).mean()) / merged["long_pct"].rolling(60).std()

        # Forward returns
        for periods, label in [(6, "30m"), (12, "60m"), (24, "120m"), (48, "240m")]:
            merged[f"ret_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

        # Contrarian test: high long_pct → negative forward return?
        for horizon in ("ret_30m", "ret_60m", "ret_120m", "ret_240m"):
            valid = merged.dropna(subset=["long_z", horizon])
            if len(valid) < 50:
                continue
            rho, pval = spearmanr(valid["long_z"], valid[horizon])
            rows.append({
                "signal": "crowd_long_z", "symbol": sym,
                "horizon": horizon, "rho": rho, "pval": pval, "n": len(valid),
            })

            # Extreme quintiles
            valid = valid.copy()
            valid["q"] = pd.qcut(valid["long_z"], 5, labels=False, duplicates="drop")
            means = valid.groupby("q")[horizon].mean()
            if len(means) >= 2:
                spread = means.iloc[-1] - means.iloc[0]
                rows.append({
                    "signal": "crowd_Q5-Q1", "symbol": sym,
                    "horizon": horizon, "rho": spread / 1e4,
                    "pval": 0, "n": len(valid),
                    "Q1_bps": means.iloc[0], "Q5_bps": means.iloc[-1],
                    "spread_bps": spread,
                })

    return pd.DataFrame(rows)


def analyze_smart_vs_dumb(global_df, top_df, klines_df):
    """When top traders diverge from the crowd, who's right?"""
    rows = []
    for sym in global_df["symbol"].unique():
        g = global_df[global_df["symbol"] == sym][["time", "long_pct"]].rename(columns={"long_pct": "crowd_long"})
        t = top_df[top_df["symbol"] == sym][["time", "top_long_pct"]].rename(columns={"top_long_pct": "top_long"})
        k = klines_df[klines_df["symbol"] == sym][["time", "close"]]

        merged = g.merge(t, on="time").merge(k, on="time")
        if len(merged) < 50:
            continue

        # Divergence: top traders vs crowd
        merged["divergence"] = merged["top_long"] - merged["crowd_long"]
        # Positive = top traders more long than crowd → bullish
        # Negative = top traders more short than crowd → bearish

        merged["div_z"] = (merged["divergence"] - merged["divergence"].rolling(60).mean()) / merged["divergence"].rolling(60).std()

        for periods, label in [(6, "30m"), (12, "60m"), (24, "120m"), (48, "240m")]:
            merged[f"ret_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

        for horizon in ("ret_30m", "ret_60m", "ret_120m", "ret_240m"):
            valid = merged.dropna(subset=["div_z", horizon])
            if len(valid) < 50:
                continue
            rho, pval = spearmanr(valid["div_z"], valid[horizon])
            rows.append({
                "signal": "smart_vs_dumb", "symbol": sym,
                "horizon": horizon, "rho": rho, "pval": pval, "n": len(valid),
            })

    return pd.DataFrame(rows)


def analyze_extreme_events(global_df, klines_df):
    """What happens after extreme positioning (>70% or <30% long)?"""
    rows = []
    for sym in global_df["symbol"].unique():
        g = global_df[global_df["symbol"] == sym].sort_values("time")
        k = klines_df[klines_df["symbol"] == sym].sort_values("time")
        merged = g.merge(k, on=["time", "symbol"], how="inner")
        if len(merged) < 50:
            continue

        for periods, label in [(12, "60m"), (24, "120m"), (48, "240m")]:
            merged[f"ret_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

        for threshold_high, threshold_low, label in [
            (0.65, 0.35, "extreme_65"),
            (0.70, 0.30, "extreme_70"),
            (0.75, 0.25, "extreme_75"),
        ]:
            for horizon in ("ret_60m", "ret_120m", "ret_240m"):
                # Extreme long → expect reversal DOWN
                extreme_long = merged[merged["long_pct"] > threshold_high].dropna(subset=[horizon])
                extreme_short = merged[merged["long_pct"] < threshold_low].dropna(subset=[horizon])

                if len(extreme_long) > 5:
                    rows.append({
                        "symbol": sym, "condition": f"crowd>{threshold_high:.0%}_long",
                        "horizon": horizon,
                        "mean_ret_bps": extreme_long[horizon].mean(),
                        "contrarian_correct": (extreme_long[horizon] < 0).mean(),
                        "n": len(extreme_long),
                    })
                if len(extreme_short) > 5:
                    rows.append({
                        "symbol": sym, "condition": f"crowd<{threshold_low:.0%}_long",
                        "horizon": horizon,
                        "mean_ret_bps": extreme_short[horizon].mean(),
                        "contrarian_correct": (extreme_short[horizon] > 0).mean(),
                        "n": len(extreme_short),
                    })

    return pd.DataFrame(rows)


def backtest_contrarian(global_df, klines_df):
    """Backtest: short when crowd >70% long, long when crowd <30% long."""
    trades = []
    for sym in global_df["symbol"].unique():
        g = global_df[global_df["symbol"] == sym].sort_values("time")
        k = klines_df[klines_df["symbol"] == sym].sort_values("time")
        merged = g.merge(k, on=["time", "symbol"], how="inner").reset_index(drop=True)
        if len(merged) < 100:
            continue

        position = 0
        entry_bar = 0
        hold_periods = 24  # 24 × 5m = 2h

        for i in range(len(merged)):
            long_pct = merged.iloc[i]["long_pct"]
            if position == 0:
                if long_pct > 0.70:
                    position = -1; entry_bar = i  # crowd too long → short
                elif long_pct < 0.30:
                    position = 1; entry_bar = i   # crowd too short → long
            else:
                if i - entry_bar >= hold_periods:
                    entry_p = merged.iloc[entry_bar]["close"]
                    exit_p = merged.iloc[i]["close"]
                    gross = position * (exit_p / entry_p - 1) * 1e4
                    trades.append({
                        "symbol": sym,
                        "direction": "LONG" if position == 1 else "SHORT",
                        "entry_long_pct": merged.iloc[entry_bar]["long_pct"],
                        "gross_bps": gross,
                        "net_bps": gross - COST_BPS,
                    })
                    position = 0

    return pd.DataFrame(trades)


def plot_contrarian(contrarian_df):
    if contrarian_df.empty:
        return
    rho_data = contrarian_df[contrarian_df["signal"] == "crowd_long_z"]
    if rho_data.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Rho by symbol × horizon
    ax = axes[0]
    for horizon in ("ret_60m", "ret_120m", "ret_240m"):
        sub = rho_data[rho_data["horizon"] == horizon]
        if sub.empty:
            continue
        ax.scatter(sub["symbol"].str.replace("USDT", ""), sub["rho"],
                   label=horizon.replace("ret_", ""), s=50, alpha=0.8)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Spearman rho (crowd long z → return)")
    ax.set_title("Contrarian Signal: negative rho = crowd is wrong")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=45)

    # Spread Q5-Q1
    ax = axes[1]
    spread_data = contrarian_df[contrarian_df["signal"] == "crowd_Q5-Q1"]
    if not spread_data.empty:
        for horizon in ("ret_60m", "ret_120m"):
            sub = spread_data[spread_data["horizon"] == horizon]
            if sub.empty:
                continue
            ax.bar([s.replace("USDT", "") for s in sub["symbol"]],
                   sub["spread_bps"], alpha=0.7, label=horizon.replace("ret_", ""))
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_ylabel("Q5-Q1 spread (bps)")
        ax.set_title("Most long crowd → worst return spread")
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", rotation=45)

    plt.tight_layout()
    savefig("crowd_contrarian.png")


def plot_backtest(trades_df):
    if trades_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    by_sym = trades_df.groupby("symbol")["net_bps"].agg(["mean", "count", "sum"])
    by_sym = by_sym.sort_values("mean", ascending=False)
    colors = ["#3fb950" if v > 0 else "#f85149" for v in by_sym["mean"]]
    ax.barh([s.replace("USDT", "") for s in by_sym.index], by_sym["mean"],
            color=colors, edgecolor="white", linewidth=0.5)
    for i, (sym, row) in enumerate(by_sym.iterrows()):
        ax.text(row["mean"] + (1 if row["mean"] >= 0 else -8), i,
                f"n={row['count']:.0f}", va="center", fontsize=9)
    ax.axvline(0, color="white", linewidth=0.5)
    ax.set_xlabel("Mean net P&L per trade (bps)")
    ax.set_title("Contrarian Strategy by Symbol")

    ax = axes[1]
    cum = trades_df.sort_values("entry_long_pct").reset_index(drop=True)["net_bps"].cumsum()
    ax.plot(range(len(cum)), cum, linewidth=2, color="#d29922")
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Cumulative net P&L (bps)")
    ax.set_title(f"Contrarian Backtest: {len(trades_df)} trades, {cum.iloc[-1]:+.0f} bps")

    plt.tight_layout()
    savefig("crowd_backtest.png")


def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 13 — Crowd Positioning (Contrarian)")
    print("=" * 70)

    loop = asyncio.new_event_loop()
    global_df, top_df, klines_df = loop.run_until_complete(fetch_all())

    if global_df.empty:
        print("  No data — API may be rate-limited. Try again in a minute.")
        return

    # ── 1. Contrarian signal ─────────────────────────────────────
    print("\n── 1. Contrarian: crowd long z → future return ──")
    contrarian = analyze_contrarian(global_df, klines_df)
    if not contrarian.empty:
        rho_data = contrarian[contrarian["signal"] == "crowd_long_z"]
        for horizon in ("ret_60m", "ret_120m", "ret_240m"):
            sub = rho_data[rho_data["horizon"] == horizon]
            if sub.empty:
                continue
            avg_rho = sub["rho"].mean()
            sig_count = (sub["pval"] < 0.05).sum()
            print(f"  {horizon}: avg rho={avg_rho:+.4f} | {sig_count}/{len(sub)} significant")
            # Best symbols
            best = sub.nsmallest(3, "rho")
            for _, r in best.iterrows():
                print(f"    {r['symbol']:12s} rho={r['rho']:+.4f} {'*' if r['pval']<0.05 else ''}")

    # ── 2. Smart vs dumb money ───────────────────────────────────
    print("\n── 2. Smart vs Dumb Money Divergence ──")
    smart = analyze_smart_vs_dumb(global_df, top_df, klines_df)
    if not smart.empty:
        for horizon in ("ret_60m", "ret_120m", "ret_240m"):
            sub = smart[smart["horizon"] == horizon]
            if sub.empty:
                continue
            avg_rho = sub["rho"].mean()
            print(f"  {horizon}: avg rho={avg_rho:+.4f} (positive = follow top traders)")
            best = sub.nlargest(3, "rho")
            for _, r in best.iterrows():
                print(f"    {r['symbol']:12s} rho={r['rho']:+.4f} {'*' if r['pval']<0.05 else ''}")

    # ── 3. Extreme events ────────────────────────────────────────
    print("\n── 3. Extreme Crowd Positioning Events ──")
    extreme = analyze_extreme_events(global_df, klines_df)
    if not extreme.empty:
        for cond in extreme["condition"].unique():
            sub = extreme[(extreme["condition"] == cond) & (extreme["horizon"] == "ret_120m")]
            if sub.empty:
                continue
            avg_ret = sub["mean_ret_bps"].mean()
            avg_correct = sub["contrarian_correct"].mean()
            total_n = sub["n"].sum()
            print(f"  {cond:25s}: avg ret {avg_ret:+.0f} bps | "
                  f"contrarian correct {avg_correct:.0%} | n={total_n:.0f}")
        extreme.to_csv(f"{OUTPUT_DIR}/crowd_extremes.csv", index=False)

    # ── 4. Backtest ──────────────────────────────────────────────
    print("\n── 4. Contrarian Backtest (short >70% long, long <30%) ──")
    trades = backtest_contrarian(global_df, klines_df)
    if not trades.empty:
        print(f"  {len(trades)} trades | gross {trades['gross_bps'].mean():+.1f} | "
              f"net {trades['net_bps'].mean():+.1f} bps/trade | "
              f"win {(trades['net_bps']>0).mean():.0%} | "
              f"total {trades['net_bps'].sum():+.0f} bps")
        trades.to_csv(f"{OUTPUT_DIR}/crowd_backtest.csv", index=False)

    # ── Plots ────────────────────────────────────────────────────
    print("\nPlots...")
    plot_contrarian(contrarian)
    plot_backtest(trades)

    # ── Verdict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)

    return {"contrarian": contrarian, "smart": smart, "extreme": extreme, "trades": trades}


if __name__ == "__main__":
    run()
