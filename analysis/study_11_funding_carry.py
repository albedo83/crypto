"""Study 11 — Funding Rate Carry Trade: earn the spread between altcoins.

Strategy: Long the symbol with the lowest funding, short the one with the highest.
Market-neutral: no directional risk. Earn the funding differential every 8h.

Uses live Binance REST API to fetch current and historical funding rates.

Run: python3 -m analysis.study_11_funding_carry
"""

from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta

from analysis.utils import apply_dark_theme, savefig, OUTPUT_DIR

SYMBOLS = [
    "ADAUSDT", "BNBUSDT", "BCHUSDT", "TRXUSDT", "HYPEUSDT",
    "ZROUSDT", "AAVEUSDT", "LINKUSDT", "SUIUSDT",
    "AVAXUSDT", "XRPUSDT", "XMRUSDT", "XLMUSDT", "TONUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT",
]

COST_BPS_ENTRY = 4.0  # one-time cost to open both legs (2 × maker roundtrip)


# ═════════════════════════════════════════════════════════════════════
# DATA: fetch funding rate history from Binance
# ═════════════════════════════════════════════════════════════════════

async def fetch_funding_history(session, symbol, limit=500):
    """Fetch historical funding rates from Binance."""
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}
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
    df["symbol"] = symbol
    df["time"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["rate"] = df["fundingRate"].astype(float)
    df["rate_bps"] = df["rate"] * 1e4
    return df[["symbol", "time", "rate", "rate_bps"]]


async def fetch_current_funding(session):
    """Fetch current premiumIndex for all symbols (includes live funding rate)."""
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    try:
        async with session.get(url) as resp:
            data = await resp.json()
    except Exception:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df = df[df["symbol"].isin(SYMBOLS)]
    df["rate"] = df["lastFundingRate"].astype(float)
    df["rate_bps"] = df["rate"] * 1e4
    df["next_time"] = pd.to_datetime(df["nextFundingTime"].astype(int), unit="ms", utc=True)
    df["mark_price"] = df["markPrice"].astype(float)
    return df[["symbol", "rate", "rate_bps", "next_time", "mark_price"]]


async def fetch_all_history():
    """Fetch funding history for all symbols."""
    print("  Fetching funding history from Binance...")
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_funding_history(session, sym) for sym in SYMBOLS]
        results = await asyncio.gather(*tasks)
        current = await fetch_current_funding(session)

    all_df = pd.concat([r for r in results if not r.empty], ignore_index=True)
    print(f"  {len(all_df)} funding entries across {all_df['symbol'].nunique()} symbols")
    if not all_df.empty:
        print(f"  Range: {all_df['time'].min()} → {all_df['time'].max()}")
    return all_df, current


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS 1: Current funding snapshot — best pairs right now
# ═════════════════════════════════════════════════════════════════════

def analyze_current(current: pd.DataFrame):
    """Find best carry pairs from current funding rates."""
    if current.empty:
        return pd.DataFrame()

    current = current.sort_values("rate_bps")
    print(f"\n  Current funding rates:")
    for _, r in current.iterrows():
        bar = "█" * int(abs(r["rate_bps"]) * 5)
        color = "+" if r["rate_bps"] >= 0 else "-"
        print(f"    {r['symbol']:12s} {r['rate_bps']:+6.2f} bps  {color}{bar}")

    # Best pairs: long lowest, short highest
    rows = []
    for _, low in current.nsmallest(5, "rate_bps").iterrows():
        for _, high in current.nlargest(5, "rate_bps").iterrows():
            if low["symbol"] == high["symbol"]:
                continue
            spread = high["rate_bps"] - low["rate_bps"]
            if spread <= 0:
                continue
            # Per 8h: we earn the spread
            daily = spread * 3  # 3 settlements/day
            annual = daily * 365
            rows.append({
                "long": low["symbol"],
                "short": high["symbol"],
                "long_funding_bps": low["rate_bps"],
                "short_funding_bps": high["rate_bps"],
                "spread_bps": spread,
                "daily_bps": daily,
                "annual_pct": annual / 100,
                "breakeven_hours": COST_BPS_ENTRY / (spread / 8) if spread > 0 else 999,
            })

    pairs = pd.DataFrame(rows).sort_values("spread_bps", ascending=False)
    return pairs


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS 2: Historical spread stability — is it persistent?
# ═════════════════════════════════════════════════════════════════════

def analyze_historical_spreads(history: pd.DataFrame):
    """How stable are funding rate differentials over time?"""
    if history.empty:
        return pd.DataFrame()

    # Pivot: rows=time, columns=symbol, values=rate_bps
    pivot = history.pivot_table(index="time", columns="symbol", values="rate_bps")
    pivot = pivot.dropna(axis=1, thresh=len(pivot) * 0.5)  # drop symbols with <50% data

    rows = []
    symbols = list(pivot.columns)
    for i, s1 in enumerate(symbols):
        for s2 in symbols[i+1:]:
            spread = pivot[s1] - pivot[s2]
            valid = spread.dropna()
            if len(valid) < 20:
                continue
            rows.append({
                "pair": f"{s1}/{s2}",
                "long_when_positive": s2,  # long s2 when spread > 0 (s1 pays more)
                "short_when_positive": s1,
                "mean_spread_bps": valid.mean(),
                "median_spread_bps": valid.median(),
                "std_spread_bps": valid.std(),
                "min_spread_bps": valid.min(),
                "max_spread_bps": valid.max(),
                "pct_positive": (valid > 0).mean(),
                "n": len(valid),
                # Sharpe-like: mean / std
                "consistency": abs(valid.mean()) / valid.std() if valid.std() > 0 else 0,
            })

    pairs = pd.DataFrame(rows)
    if pairs.empty:
        return pairs
    pairs["abs_mean"] = pairs["mean_spread_bps"].abs()
    pairs = pairs.sort_values("abs_mean", ascending=False)
    return pairs


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS 3: Backtest carry trade
# ═════════════════════════════════════════════════════════════════════

def backtest_carry(history: pd.DataFrame, top_n_pairs: int = 5):
    """Backtest: at each settlement, long the lowest funding, short the highest."""
    if history.empty:
        return pd.DataFrame()

    pivot = history.pivot_table(index="time", columns="symbol", values="rate_bps")
    pivot = pivot.dropna(axis=1, thresh=len(pivot) * 0.5)

    trades = []
    positions = {}  # active carry positions

    for ts in sorted(pivot.index):
        row = pivot.loc[ts].dropna()
        if len(row) < 4:
            continue

        # Close existing positions — earn the funding
        for pair_key, pos in list(positions.items()):
            long_sym, short_sym = pair_key.split("|")
            long_rate = row.get(long_sym, 0)
            short_rate = row.get(short_sym, 0)
            # Carry earned: we're long the low-funder, short the high-funder
            # Long position: if funding > 0, we PAY. If < 0, we EARN.
            # Short position: if funding > 0, we EARN. If < 0, we PAY.
            carry_earned = short_rate - long_rate  # what we net per 8h
            pos["total_carry"] += carry_earned
            pos["settlements"] += 1

            # Rebalance every 3 days (9 settlements)
            if pos["settlements"] >= 9:
                net = pos["total_carry"] - COST_BPS_ENTRY  # subtract entry cost
                trades.append({
                    "entry_time": pos["entry_time"],
                    "exit_time": str(ts),
                    "long": long_sym,
                    "short": short_sym,
                    "settlements": pos["settlements"],
                    "total_carry_bps": pos["total_carry"],
                    "cost_bps": COST_BPS_ENTRY,
                    "net_bps": net,
                    "daily_avg_bps": pos["total_carry"] / (pos["settlements"] / 3),
                })
                del positions[pair_key]

        # Open new positions if we have capacity
        if len(positions) >= top_n_pairs:
            continue

        sorted_rates = row.sort_values()
        longs = sorted_rates.head(3)   # 3 lowest funding → long these
        shorts = sorted_rates.tail(3)  # 3 highest funding → short these

        for long_sym in longs.index:
            for short_sym in shorts.index:
                pair_key = f"{long_sym}|{short_sym}"
                if pair_key in positions:
                    continue
                if len(positions) >= top_n_pairs:
                    break

                spread = row[short_sym] - row[long_sym]
                if spread < 1.0:  # minimum 1 bps spread to enter
                    continue

                positions[pair_key] = {
                    "entry_time": str(ts),
                    "total_carry": 0.0,
                    "settlements": 0,
                }
            if len(positions) >= top_n_pairs:
                break

    return pd.DataFrame(trades)


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS 4: Risk — what happens when funding flips?
# ═════════════════════════════════════════════════════════════════════

def analyze_funding_persistence(history: pd.DataFrame):
    """How often does funding rate flip sign? Stability = safer carry."""
    if history.empty:
        return pd.DataFrame()

    rows = []
    for sym in history["symbol"].unique():
        s = history[history["symbol"] == sym].sort_values("time")
        if len(s) < 10:
            continue
        rates = s["rate_bps"]
        flips = (rates.shift(1) * rates < 0).sum()  # sign changes
        streak_same = 0
        max_streak = 0
        for i in range(1, len(rates)):
            if np.sign(rates.iloc[i]) == np.sign(rates.iloc[i-1]):
                streak_same += 1
                max_streak = max(max_streak, streak_same)
            else:
                streak_same = 0

        rows.append({
            "symbol": sym,
            "mean_rate_bps": rates.mean(),
            "std_rate_bps": rates.std(),
            "pct_positive": (rates > 0).mean(),
            "flips": flips,
            "flip_rate_pct": flips / len(rates) * 100,
            "max_same_streak": max_streak,
            "n_settlements": len(rates),
        })

    return pd.DataFrame(rows).sort_values("flip_rate_pct")


# ═════════════════════════════════════════════════════════════════════
# PLOTS
# ═════════════════════════════════════════════════════════════════════

def plot_current_rates(current):
    if current.empty:
        return
    current = current.sort_values("rate_bps")
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = ["#3fb950" if r < 0 else "#f85149" for r in current["rate_bps"]]
    ax.barh(current["symbol"].str.replace("USDT", ""), current["rate_bps"],
            color=colors, edgecolor="white", linewidth=0.5)
    ax.axvline(0, color="white", linewidth=0.5)
    ax.set_xlabel("Funding Rate (bps per 8h)")
    ax.set_title("Current Funding Rates — Green = shorts pay (long is free)")
    plt.tight_layout()
    savefig("carry_current_rates.png")


def plot_spread_history(history, pair_long, pair_short):
    if history.empty:
        return
    pivot = history.pivot_table(index="time", columns="symbol", values="rate_bps")
    if pair_long not in pivot.columns or pair_short not in pivot.columns:
        return
    spread = pivot[pair_short] - pivot[pair_long]
    spread = spread.dropna()
    if len(spread) < 5:
        return

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [2, 1]})

    ax = axes[0]
    ax.plot(spread.index, spread.values, linewidth=1.5, color="#d29922")
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.axhline(spread.mean(), color="#3fb950", linewidth=1, linestyle="--",
               label=f"Mean: {spread.mean():.1f} bps")
    ax.fill_between(spread.index, 0, spread.values,
                    where=spread > 0, alpha=0.2, color="#3fb950")
    ax.fill_between(spread.index, 0, spread.values,
                    where=spread < 0, alpha=0.2, color="#f85149")
    ax.set_ylabel("Funding Spread (bps)")
    ax.set_title(f"Carry Spread: SHORT {pair_short.replace('USDT','')} / LONG {pair_long.replace('USDT','')}")
    ax.legend()

    ax = axes[1]
    cum = spread.cumsum()
    ax.plot(cum.index, cum.values, linewidth=2, color="#3fb950")
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Cumulative Carry (bps)")
    ax.set_xlabel("Time")

    plt.tight_layout()
    savefig(f"carry_spread_{pair_short}_{pair_long}.png")


def plot_backtest(trades):
    if trades.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Cumulative P&L
    ax = axes[0]
    cum = trades["net_bps"].cumsum()
    ax.plot(range(len(trades)), cum, linewidth=2, color="#d29922")
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Cumulative Net P&L (bps)")
    ax.set_title(f"Carry Trade Backtest: {len(trades)} trades, {cum.iloc[-1]:+.0f} bps total")

    # Per-pair performance
    ax = axes[1]
    pair_perf = trades.groupby(trades["long"] + "/" + trades["short"])["net_bps"].mean()
    pair_perf = pair_perf.sort_values()
    colors = ["#3fb950" if v > 0 else "#f85149" for v in pair_perf]
    ax.barh(pair_perf.index, pair_perf.values, color=colors, edgecolor="white", linewidth=0.5)
    ax.axvline(0, color="white", linewidth=0.5)
    ax.set_xlabel("Mean Net P&L per 3-day hold (bps)")
    ax.set_title("Performance by Pair")

    plt.tight_layout()
    savefig("carry_backtest.png")


def plot_persistence(persist):
    if persist.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    persist = persist.sort_values("flip_rate_pct")
    colors = ["#3fb950" if f < 30 else "#d29922" if f < 50 else "#f85149"
              for f in persist["flip_rate_pct"]]
    ax.barh(persist["symbol"].str.replace("USDT", ""), persist["flip_rate_pct"],
            color=colors, edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Flip Rate (% of settlements where sign changes)")
    ax.set_title("Funding Stability — Green = stable (better for carry)")
    ax.axvline(50, color="white", linewidth=0.5, linestyle="--", alpha=0.5)
    plt.tight_layout()
    savefig("carry_persistence.png")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 11 — Funding Rate Carry Trade")
    print("=" * 70)

    loop = asyncio.new_event_loop()
    history, current = loop.run_until_complete(fetch_all_history())

    # ── 1. Current snapshot ──────────────────────────────────────
    print("\n── 1. Current Funding Rates ──")
    if not current.empty:
        pairs = analyze_current(current)
        if not pairs.empty:
            print(f"\n  Top 10 carry pairs RIGHT NOW:")
            top = pairs.head(10)
            for _, r in top.iterrows():
                print(f"    LONG {r['long'].replace('USDT',''):>6s} ({r['long_funding_bps']:+.1f}bps) + "
                      f"SHORT {r['short'].replace('USDT',''):>6s} ({r['short_funding_bps']:+.1f}bps) "
                      f"= spread {r['spread_bps']:.1f}bps/8h "
                      f"= {r['daily_bps']:.1f}bps/day "
                      f"= {r['annual_pct']:.0f}%/year "
                      f"| breakeven {r['breakeven_hours']:.0f}h")
            pairs.to_csv(f"{OUTPUT_DIR}/carry_current_pairs.csv", index=False)

    # ── 2. Historical spread analysis ────────────────────────────
    print("\n── 2. Historical Spread Stability ──")
    hist_spreads = analyze_historical_spreads(history)
    if not hist_spreads.empty:
        # Show most consistent spreads
        consistent = hist_spreads[hist_spreads["consistency"] > 0.3].nlargest(15, "abs_mean")
        if not consistent.empty:
            print("  Most consistent funding spreads:")
            for _, r in consistent.iterrows():
                direction = "positive" if r["mean_spread_bps"] > 0 else "negative"
                print(f"    {r['pair']:25s} mean={r['mean_spread_bps']:+.1f}bps "
                      f"std={r['std_spread_bps']:.1f} "
                      f"consistency={r['consistency']:.2f} "
                      f"same-sign {r['pct_positive']:.0%} (n={r['n']:.0f})")
        hist_spreads.to_csv(f"{OUTPUT_DIR}/carry_spread_analysis.csv", index=False)

    # ── 3. Funding persistence ───────────────────────────────────
    print("\n── 3. Funding Rate Persistence ──")
    persist = analyze_funding_persistence(history)
    if not persist.empty:
        print("  Most stable (low flip rate = better for carry):")
        for _, r in persist.head(10).iterrows():
            print(f"    {r['symbol']:12s} mean={r['mean_rate_bps']:+.2f}bps "
                  f"flip={r['flip_rate_pct']:.0f}% "
                  f"max_streak={r['max_same_streak']:.0f} "
                  f"({r['n_settlements']:.0f} settlements)")
        print("  Most volatile (high flip rate = dangerous for carry):")
        for _, r in persist.tail(5).iterrows():
            print(f"    {r['symbol']:12s} mean={r['mean_rate_bps']:+.2f}bps "
                  f"flip={r['flip_rate_pct']:.0f}% ")
        persist.to_csv(f"{OUTPUT_DIR}/carry_persistence.csv", index=False)

    # ── 4. Backtest ──────────────────────────────────────────────
    print("\n── 4. Carry Trade Backtest ──")
    trades = backtest_carry(history)
    if not trades.empty:
        total = trades["net_bps"].sum()
        mean = trades["net_bps"].mean()
        win = (trades["net_bps"] > 0).mean()
        avg_carry = trades["total_carry_bps"].mean()
        print(f"  {len(trades)} trades (3-day holds)")
        print(f"  Avg carry earned: {avg_carry:+.1f} bps/trade")
        print(f"  Avg net (after {COST_BPS_ENTRY} bps entry cost): {mean:+.1f} bps/trade")
        print(f"  Win rate: {win:.0%}")
        print(f"  Total: {total:+.0f} bps")

        # By pair
        by_pair = trades.groupby(trades["long"] + "/" + trades["short"]).agg(
            trades=("net_bps", "count"),
            mean_net=("net_bps", "mean"),
            total_net=("net_bps", "sum"),
        ).sort_values("total_net", ascending=False)
        print(f"\n  Best pairs:")
        for pair, r in by_pair.head(10).iterrows():
            print(f"    {pair:25s} {r['trades']:.0f} trades | "
                  f"mean {r['mean_net']:+.1f} bps | total {r['total_net']:+.0f} bps")

        trades.to_csv(f"{OUTPUT_DIR}/carry_backtest.csv", index=False)

    # ── 5. Combined with OI divergence ───────────────────────────
    print("\n── 5. Carry + OI Divergence (combined potential) ──")
    if not trades.empty:
        carry_daily = trades["net_bps"].sum() / max(1, (len(history) / len(SYMBOLS) / 3))
        print(f"  Carry trade alone: ~{carry_daily:.1f} bps/day")
        print(f"  OI divergence (from study_06): ~110 bps/day (estimated)")
        print(f"  Combined: ~{carry_daily + 110:.0f} bps/day")
        print(f"  On $1000: ~${(carry_daily + 110)/100:.1f}/day = ~${(carry_daily + 110)/100*30:.0f}/month")

    # ── Plots ────────────────────────────────────────────────────
    print("\nPlots...")
    plot_current_rates(current)
    if not hist_spreads.empty and len(hist_spreads) > 0:
        best = hist_spreads.iloc[0]
        # Plot the best spread
        s1, s2 = best["pair"].split("/")
        plot_spread_history(history, s2, s1)  # long s2, short s1
    plot_backtest(trades)
    plot_persistence(persist)

    # ── Verdict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    if not pairs.empty:
        best_pair = pairs.iloc[0]
        print(f"  Meilleure paire maintenant: LONG {best_pair['long']} + SHORT {best_pair['short']}")
        print(f"  Spread: {best_pair['spread_bps']:.1f} bps / 8h = {best_pair['annual_pct']:.0f}%/an")
        print(f"  Breakeven: {best_pair['breakeven_hours']:.0f}h (coût d'entrée remboursé)")
    if not persist.empty:
        safest = persist.head(3)["symbol"].tolist()
        print(f"  Symbols les plus stables pour carry: {', '.join(safest)}")

    return {"current": current, "pairs": pairs, "history": history,
            "spreads": hist_spreads, "persist": persist, "trades": trades}


if __name__ == "__main__":
    run()
