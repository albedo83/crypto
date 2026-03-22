"""Study 09 — Multi-altcoin: can we diversify OI divergence across symbols?

1. Check if ADA/BTC/ETH OI signals are correlated (from our DB)
2. Pull historical data from Binance REST for other altcoins
3. Test OI divergence on each
4. Estimate combined portfolio return if signals are independent

Run: python3 -m analysis.study_09_multi_alt
"""

from __future__ import annotations

import asyncio
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr
from datetime import datetime, timezone, timedelta

import aiohttp

from analysis.db import fetch_df
from analysis.utils import (
    ID_TO_SYMBOL, apply_dark_theme, savefig, OUTPUT_DIR, add_session_column,
)

COST_BPS = 4.0
EXTRA_SYMBOLS = ["DOGEUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"]


# ═════════════════════════════════════════════════════════════════════
# PART 1: Check correlation of OI signals across our 3 symbols
# ═════════════════════════════════════════════════════════════════════

def check_signal_correlation():
    """Are OI divergence signals on ADA/BTC/ETH independent?"""
    print("  Loading OI + price from DB...")
    oi = fetch_df("SELECT exchange_ts, instrument_id, open_interest FROM open_interest ORDER BY instrument_id, exchange_ts")
    oi["exchange_ts"] = pd.to_datetime(oi["exchange_ts"], utc=True)

    price = fetch_df("""
        SELECT time_bucket('5 minutes', exchange_ts) AS bucket, instrument_id,
               last(price, exchange_ts) AS close
        FROM trades_raw GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    price["bucket"] = pd.to_datetime(price["bucket"], utc=True)

    # Compute OI divergence signal per symbol
    signals = {}
    for iid in [1, 2, 3]:
        sym = ID_TO_SYMBOL[iid]
        o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts").copy()
        p = price[price["instrument_id"] == iid].sort_values("bucket").copy()

        o["oi_change"] = o["open_interest"].pct_change()
        o["bucket"] = o["exchange_ts"].dt.floor("5min")
        p5 = p.groupby("bucket").agg(close=("close", "last")).reset_index()

        merged = o.merge(p5, on="bucket", how="inner")
        merged["price_change"] = merged["close"].pct_change()

        # Signal: OI change × price change (negative = divergence)
        merged["oi_div_signal"] = -merged["oi_change"] * np.sign(merged["price_change"])
        signals[sym] = merged[["bucket", "oi_div_signal"]].dropna()

    # Merge all signals on bucket
    combined = signals["BTCUSDT"].rename(columns={"oi_div_signal": "BTC"})
    for sym in ["ETHUSDT", "ADAUSDT"]:
        combined = combined.merge(
            signals[sym].rename(columns={"oi_div_signal": sym[:3]}),
            on="bucket", how="inner"
        )

    # Correlation matrix
    corr = combined[["BTC", "ETH", "ADA"]].corr()
    return corr, combined


# ═════════════════════════════════════════════════════════════════════
# PART 2: Fetch Binance historical data for other altcoins
# ═════════════════════════════════════════════════════════════════════

async def fetch_binance_klines(session, symbol, interval="5m", limit=2000):
    """Fetch klines from Binance Futures REST."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return pd.DataFrame()
        data = await resp.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    df["bucket"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["quote_volume"].astype(float)
    df["symbol"] = symbol
    return df[["bucket", "symbol", "close", "volume"]]


async def fetch_binance_oi_history(session, symbol, period="5m", limit=500):
    """Fetch OI history from Binance Futures REST."""
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": period, "limit": limit}
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return pd.DataFrame()
        data = await resp.json()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["bucket"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["open_interest"] = df["sumOpenInterest"].astype(float)
    df["oi_value"] = df["sumOpenInterestValue"].astype(float)
    df["symbol"] = symbol
    return df[["bucket", "symbol", "open_interest", "oi_value"]]


async def fetch_all_altcoins():
    """Fetch klines + OI for all extra symbols."""
    print(f"  Fetching Binance data for {len(EXTRA_SYMBOLS)} altcoins...")
    async with aiohttp.ClientSession() as session:
        # Fetch in parallel
        kline_tasks = [fetch_binance_klines(session, sym) for sym in EXTRA_SYMBOLS]
        oi_tasks = [fetch_binance_oi_history(session, sym) for sym in EXTRA_SYMBOLS]

        klines = await asyncio.gather(*kline_tasks)
        ois = await asyncio.gather(*oi_tasks)

    result = {}
    for sym, kl, oi in zip(EXTRA_SYMBOLS, klines, ois):
        if kl.empty or oi.empty:
            print(f"    {sym}: no data")
            continue
        print(f"    {sym}: {len(kl)} klines, {len(oi)} OI points")
        result[sym] = {"klines": kl, "oi": oi}
    return result


# ═════════════════════════════════════════════════════════════════════
# PART 3: Test OI divergence on each altcoin
# ═════════════════════════════════════════════════════════════════════

def test_oi_divergence_single(klines, oi, symbol):
    """Test OI divergence strategy on a single symbol."""
    k = klines.sort_values("bucket").copy()
    o = oi.sort_values("bucket").copy()

    # Merge OI with price
    merged = k.merge(o[["bucket", "open_interest"]], on="bucket", how="inner")
    if len(merged) < 50:
        return None

    merged["price_change"] = merged["close"].pct_change() * 1e4  # bps
    merged["oi_change"] = merged["open_interest"].pct_change() * 100  # %

    # Forward returns
    for periods, label in [(6, "30m"), (12, "60m"), (24, "120m"), (48, "240m")]:
        merged[f"fwd_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

    # Classify OI divergence
    merged["regime"] = "neutral"
    weak_long = (merged["price_change"] > 3) & (merged["oi_change"] < -0.03)
    weak_short = (merged["price_change"] < -3) & (merged["oi_change"] > 0.03)
    strong_long = (merged["price_change"] > 3) & (merged["oi_change"] > 0.03)
    strong_short = (merged["price_change"] < -3) & (merged["oi_change"] < -0.03)
    merged.loc[weak_long, "regime"] = "weak_long"
    merged.loc[weak_short, "regime"] = "weak_short"
    merged.loc[strong_long, "regime"] = "strong_long"
    merged.loc[strong_short, "regime"] = "strong_short"

    # Session
    merged["hour"] = merged["bucket"].dt.hour
    merged["session"] = merged["hour"].map(
        lambda h: "asian" if 0 <= h < 8 else "european" if 8 <= h < 14 else "us" if 14 <= h < 21 else "overnight"
    )

    results = {"symbol": symbol}

    # OI divergence signal strength
    for horizon in ("fwd_60m", "fwd_120m", "fwd_240m"):
        for regime in ("weak_long", "weak_short"):
            sub = merged[merged["regime"] == regime].dropna(subset=[horizon])
            if len(sub) > 5:
                mean_ret = sub[horizon].mean()
                results[f"{regime}_{horizon}"] = round(mean_ret, 1)
                results[f"{regime}_{horizon}_n"] = len(sub)

    # Asia-specific
    for regime in ("weak_long", "weak_short"):
        asia = merged[(merged["regime"] == regime) & (merged["session"] == "asian")]
        if len(asia) > 3:
            ret = asia["fwd_120m"].dropna()
            if len(ret) > 3:
                results[f"{regime}_asia_120m"] = round(ret.mean(), 1)
                results[f"{regime}_asia_120m_n"] = len(ret)

    # Backtest
    trades = []
    position = 0
    entry_bar = 0
    for i in range(len(merged)):
        r = merged.iloc[i]
        if position == 0:
            session = r["session"]
            if session in ("european",):  # skip Europe
                continue
            if r["regime"] == "weak_short":
                position = 1; entry_bar = i
            elif r["regime"] == "weak_long":
                position = -1; entry_bar = i
        else:
            if i - entry_bar >= 24:  # hold 120 min (24 × 5min)
                entry_p = merged.iloc[entry_bar]["close"]
                exit_p = merged.iloc[i]["close"]
                gross = position * (exit_p / entry_p - 1) * 1e4
                trades.append({"gross": gross, "net": gross - COST_BPS,
                              "session": merged.iloc[entry_bar]["session"]})
                position = 0

    if trades:
        tdf = pd.DataFrame(trades)
        results["bt_trades"] = len(tdf)
        results["bt_gross_avg"] = round(tdf["gross"].mean(), 1)
        results["bt_net_avg"] = round(tdf["net"].mean(), 1)
        results["bt_win"] = round((tdf["gross"] > 0).mean(), 2)
        results["bt_net_total"] = round(tdf["net"].sum(), 0)

        # Asia only
        asia_trades = tdf[tdf["session"] == "asian"]
        if len(asia_trades) > 3:
            results["bt_asia_net_avg"] = round(asia_trades["net"].mean(), 1)
            results["bt_asia_win"] = round((asia_trades["gross"] > 0).mean(), 2)
            results["bt_asia_n"] = len(asia_trades)

    return results


# ═════════════════════════════════════════════════════════════════════
# PART 4: Signal timing correlation across symbols
# ═════════════════════════════════════════════════════════════════════

def check_signal_overlap(all_data):
    """Do OI divergence signals fire at the same time across symbols?"""
    signal_times = {}
    for sym, data in all_data.items():
        k = data["klines"].sort_values("bucket").copy()
        o = data["oi"].sort_values("bucket").copy()
        m = k.merge(o[["bucket", "open_interest"]], on="bucket", how="inner")
        m["price_change"] = m["close"].pct_change() * 1e4
        m["oi_change"] = m["open_interest"].pct_change() * 100

        # When there's a divergence signal
        weak_long = (m["price_change"] > 3) & (m["oi_change"] < -0.03)
        weak_short = (m["price_change"] < -3) & (m["oi_change"] > 0.03)
        signal = weak_long | weak_short
        signal_times[sym] = set(m[signal]["bucket"].dt.floor("1h"))

    # Pairwise overlap
    symbols = list(signal_times.keys())
    rows = []
    for i, s1 in enumerate(symbols):
        for s2 in symbols[i+1:]:
            overlap = len(signal_times[s1] & signal_times[s2])
            total = len(signal_times[s1] | signal_times[s2])
            pct = overlap / total if total > 0 else 0
            rows.append({"pair": f"{s1[:4]}-{s2[:4]}", "overlap": overlap,
                         "total": total, "overlap_pct": round(pct, 2)})

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# PLOTS
# ═════════════════════════════════════════════════════════════════════

def plot_altcoin_comparison(results_df):
    if results_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Net per trade
    ax = axes[0]
    syms = results_df["symbol"]
    net = results_df.get("bt_net_avg", pd.Series([0]*len(results_df)))
    colors = ["#3fb950" if v > 0 else "#f85149" for v in net]
    ax.barh(syms, net, color=colors, edgecolor="white", linewidth=0.5)
    ax.axvline(0, color="white", linewidth=0.5)
    ax.set_xlabel("Mean net P&L per trade (bps)")
    ax.set_title("OI Divergence: Net P&L by Symbol")

    # Win rate
    ax = axes[1]
    win = results_df.get("bt_win", pd.Series([0]*len(results_df)))
    colors2 = ["#3fb950" if v > 0.5 else "#f85149" for v in win]
    ax.barh(syms, win, color=colors2, edgecolor="white", linewidth=0.5)
    ax.axvline(0.5, color="white", linewidth=0.5, linestyle="--")
    ax.set_xlabel("Win Rate")
    ax.set_title("OI Divergence: Win Rate by Symbol")
    ax.set_xlim(0, 1)

    plt.tight_layout()
    savefig("multi_alt_comparison.png")


def plot_portfolio(results_df):
    """Simulated combined portfolio."""
    profitable = results_df[results_df.get("bt_net_avg", pd.Series(dtype=float)) > 0]
    if profitable.empty:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    total_daily = 0
    for _, r in profitable.iterrows():
        n = r.get("bt_trades", 0)
        days = 7  # approx data period
        trades_day = n / days if days > 0 else 0
        daily_bps = trades_day * r.get("bt_net_avg", 0)
        total_daily += daily_bps
        ax.bar(r["symbol"], daily_bps, color="#3fb950", edgecolor="white", linewidth=0.5)
        ax.text(r["symbol"], daily_bps + 1, f"{trades_day:.1f}t/d", ha="center", fontsize=9)

    ax.axhline(0, color="white", linewidth=0.5)
    ax.set_ylabel("Est. daily net P&L (bps)")
    ax.set_title(f"Combined Portfolio: ~{total_daily:.0f} bps/day across {len(profitable)} symbols")
    plt.tight_layout()
    savefig("multi_alt_portfolio.png")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 09 — Multi-Altcoin Diversification")
    print("=" * 70)

    # ── Part 1: Existing symbols correlation ─────────────────────
    print("\n── 1. Signal Correlation (ADA/BTC/ETH from DB) ──")
    corr, _ = check_signal_correlation()
    print("  OI divergence signal correlation matrix:")
    print(corr.to_string(float_format="%.3f"))
    print(f"\n  ADA-BTC correlation: {corr.loc['ADA','BTC']:.3f}")
    print(f"  ADA-ETH correlation: {corr.loc['ADA','ETH']:.3f}")
    print(f"  → {'INDEPENDENT' if abs(corr.loc['ADA','BTC']) < 0.3 else 'CORRELATED'} signals")

    # ── Part 2: Fetch other altcoins from Binance ────────────────
    print("\n── 2. Fetching Altcoin Data from Binance REST ──")
    loop = asyncio.get_event_loop()
    alt_data = loop.run_until_complete(fetch_all_altcoins())

    # ── Part 3: Test OI divergence on each ───────────────────────
    print("\n── 3. OI Divergence Test per Symbol ──")
    all_results = []
    for sym, data in alt_data.items():
        res = test_oi_divergence_single(data["klines"], data["oi"], sym)
        if res:
            all_results.append(res)
            n = res.get("bt_trades", 0)
            net = res.get("bt_net_avg", 0)
            win = res.get("bt_win", 0)
            total = res.get("bt_net_total", 0)
            asia_net = res.get("bt_asia_net_avg", "N/A")
            print(f"  {sym:12s}: {n:3d} trades | net {net:+.1f} bps/trade | "
                  f"win {win:.0%} | total {total:+.0f} bps | "
                  f"asia: {asia_net}")

    # Add our existing ADA result for comparison
    all_results.append({
        "symbol": "ADAUSDT*", "bt_trades": 37, "bt_net_avg": 20.9,
        "bt_win": 0.54, "bt_net_total": 773,
        "bt_asia_net_avg": 36.4, "bt_asia_win": 0.58, "bt_asia_n": 12,
    })

    results_df = pd.DataFrame(all_results)
    results_df.to_csv(f"{OUTPUT_DIR}/multi_alt_results.csv", index=False)

    # ── Part 4: Signal overlap ───────────────────────────────────
    print("\n── 4. Signal Timing Overlap ──")
    if len(alt_data) >= 2:
        overlap = check_signal_overlap(alt_data)
        if not overlap.empty:
            print(overlap.to_string(index=False))
            mean_overlap = overlap["overlap_pct"].mean()
            print(f"\n  Mean overlap: {mean_overlap:.0%} → "
                  f"{'MOSTLY INDEPENDENT' if mean_overlap < 0.3 else 'PARTIALLY CORRELATED'}")

    # ── Part 5: Combined portfolio estimate ──────────────────────
    print("\n── 5. Combined Portfolio Estimate ──")
    profitable = results_df[results_df.get("bt_net_avg", pd.Series(dtype=float)) > 0]
    if not profitable.empty:
        print(f"  Profitable symbols: {len(profitable)}")
        total_daily_bps = 0
        for _, r in profitable.iterrows():
            n = r.get("bt_trades", 0)
            net = r.get("bt_net_avg", 0)
            daily = (n / 7) * net  # 7 days of data
            total_daily_bps += daily
            print(f"    {r['symbol']:12s}: ~{n/7:.1f} trades/day × {net:+.1f} bps = {daily:+.1f} bps/day")
        print(f"\n  COMBINED: ~{total_daily_bps:.0f} bps/day")
        print(f"  Sur 1000€: ~{total_daily_bps/100:.1f}€/jour = ~{total_daily_bps/100*30:.0f}€/mois")
        print(f"  Sur 5000€: ~{total_daily_bps/100*5:.1f}€/jour = ~{total_daily_bps/100*5*30:.0f}€/mois")

    # ── Plots ────────────────────────────────────────────────────
    print("\nPlots...")
    plot_altcoin_comparison(results_df)
    plot_portfolio(results_df)

    return {"results": results_df, "correlation": corr}


if __name__ == "__main__":
    run()
