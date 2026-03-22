"""Study 12 — Token Unlock Calendar: short before unlock, cover after.

Approach:
1. Fetch upcoming unlocks from DeFiLlama / public APIs
2. Get historical price data around past unlocks via Binance klines
3. Measure: does price consistently drop before unlock?
4. Quantify edge and optimal timing

Run: python3 -m analysis.study_12_token_unlocks
"""

from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta

from analysis.utils import apply_dark_theme, savefig, OUTPUT_DIR

# Symbols we can trade on Binance Futures
TRADEABLE = {
    "SUI", "AVAX", "ADA", "LINK", "AAVE", "UNI", "TRX", "XRP", "SOL",
    "DOGE", "DOT", "LTC", "BNB", "ETH", "BTC", "ARB", "OP", "APT",
    "SEI", "TIA", "STRK", "PIXEL", "MANTA", "JUP", "W", "ENA",
    "HYPE", "SUI", "ZRO", "XMR", "XLM", "TON", "BCH",
}


async def fetch_defillama_unlocks(session):
    """Try DeFiLlama emissions/unlocks API."""
    # DeFiLlama doesn't have a direct unlock API, but has protocol data
    # Try their emissions endpoint
    protocols = []
    try:
        url = "https://api.llama.fi/protocols"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Filter for our tradeable tokens
                for p in data:
                    sym = (p.get("symbol") or "").upper()
                    if sym in TRADEABLE:
                        protocols.append({
                            "symbol": sym,
                            "name": p.get("name"),
                            "mcap": p.get("mcap", 0),
                            "tvl": p.get("tvl", 0),
                        })
    except Exception as e:
        print(f"    DeFiLlama error: {e}")
    return protocols


async def fetch_binance_klines(session, symbol, interval="1d", limit=90):
    """Fetch daily klines for a symbol."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return pd.DataFrame()
            data = await resp.json()
    except Exception:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    df["close"] = df["close"].astype(float)
    df["volume"] = df["quote_volume"].astype(float)
    df["symbol"] = symbol
    return df[["date", "symbol", "close", "volume"]]


# Known major token unlocks (from public sources)
# Format: (symbol, date, amount_description, pct_of_supply)
KNOWN_UNLOCKS = [
    # SUI — massive unlocks in 2025-2026
    ("SUI", "2025-11-01", "64M SUI", 0.5),
    ("SUI", "2025-12-01", "64M SUI", 0.5),
    ("SUI", "2026-01-01", "64M SUI", 0.5),
    ("SUI", "2026-02-01", "64M SUI", 0.5),
    ("SUI", "2026-03-01", "64M SUI", 0.5),
    # AVAX — quarterly unlocks
    ("AVAX", "2025-11-25", "9.5M AVAX", 2.2),
    ("AVAX", "2026-02-25", "9.5M AVAX", 2.2),
    # ARB — cliff unlocks
    ("ARB", "2025-11-16", "92M ARB", 0.7),
    ("ARB", "2025-12-16", "92M ARB", 0.7),
    ("ARB", "2026-01-16", "92M ARB", 0.7),
    ("ARB", "2026-02-16", "92M ARB", 0.7),
    ("ARB", "2026-03-16", "92M ARB", 0.7),
    # APT — monthly unlocks
    ("APT", "2025-11-12", "11M APT", 2.1),
    ("APT", "2025-12-12", "11M APT", 2.1),
    ("APT", "2026-01-12", "11M APT", 2.1),
    ("APT", "2026-02-12", "11M APT", 2.1),
    ("APT", "2026-03-12", "11M APT", 2.1),
    # OP — monthly
    ("OP", "2025-11-30", "31M OP", 0.7),
    ("OP", "2025-12-31", "31M OP", 0.7),
    ("OP", "2026-01-31", "31M OP", 0.7),
    ("OP", "2026-02-28", "31M OP", 0.7),
    # SEI — periodic
    ("SEI", "2025-12-15", "55M SEI", 0.5),
    ("SEI", "2026-01-15", "55M SEI", 0.5),
    ("SEI", "2026-02-15", "55M SEI", 0.5),
    ("SEI", "2026-03-15", "55M SEI", 0.5),
    # TIA — large unlock
    ("TIA", "2025-10-31", "175M TIA", 16.0),
    # SOL — ongoing emissions
    ("SOL", "2026-01-01", "inflation", 1.5),
    ("SOL", "2026-02-01", "inflation", 1.5),
    ("SOL", "2026-03-01", "inflation", 1.5),
    # LINK — team unlocks
    ("LINK", "2025-12-01", "team vesting", 1.0),
    ("LINK", "2026-03-01", "team vesting", 1.0),
    # DOGE — inflation only (no unlocks)
    # XRP — escrow releases
    ("XRP", "2026-01-01", "1B XRP escrow", 1.8),
    ("XRP", "2026-02-01", "1B XRP escrow", 1.8),
    ("XRP", "2026-03-01", "1B XRP escrow", 1.8),
]


async def fetch_all_data():
    """Fetch klines for all symbols with known unlocks."""
    symbols = list(set(u[0] for u in KNOWN_UNLOCKS))
    print(f"  Fetching klines for {len(symbols)} symbols...")
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_binance_klines(session, sym, "1d", 180) for sym in symbols]
        results = await asyncio.gather(*tasks)
        protos = await fetch_defillama_unlocks(session)

    all_klines = pd.concat([r for r in results if not r.empty], ignore_index=True)
    print(f"  {len(all_klines)} daily klines across {all_klines['symbol'].nunique()} symbols")
    return all_klines, protos


def analyze_unlock_impact(klines: pd.DataFrame, unlocks: list) -> pd.DataFrame:
    """For each unlock event, measure price action before and after."""
    rows = []
    for sym, date_str, desc, pct in unlocks:
        unlock_date = pd.Timestamp(date_str).date()
        k = klines[klines["symbol"] == sym].sort_values("date")
        if k.empty:
            continue

        # Find the unlock date in klines
        dates = list(k["date"])
        if unlock_date not in dates:
            # Find closest date
            diffs = [(abs((d - unlock_date).days), d) for d in dates]
            if not diffs:
                continue
            closest = min(diffs, key=lambda x: x[0])
            if closest[0] > 5:
                continue
            unlock_idx = dates.index(closest[1])
        else:
            unlock_idx = dates.index(unlock_date)

        prices = k["close"].values
        if unlock_idx < 7 or unlock_idx >= len(prices) - 7:
            continue

        unlock_price = prices[unlock_idx]

        # Returns relative to unlock date
        for days, label in [(-7, "7d_before"), (-3, "3d_before"), (-1, "1d_before"),
                            (1, "1d_after"), (3, "3d_after"), (7, "7d_after")]:
            idx = unlock_idx + days
            if 0 <= idx < len(prices):
                ret = (prices[idx] / unlock_price - 1) * 1e4
                rows.append({
                    "symbol": sym, "unlock_date": date_str,
                    "pct_supply": pct, "description": desc,
                    "period": label, "days": days,
                    "return_bps": ret,
                })

    return pd.DataFrame(rows)


def analyze_strategy(impact: pd.DataFrame) -> pd.DataFrame:
    """Backtest: short 3 days before, cover on unlock day."""
    if impact.empty:
        return pd.DataFrame()

    # For each unlock: short at -3d, cover at 0d
    # P&L = -(price_at_0 / price_at_-3 - 1) × 10000 = return_at_-3d (inverted)
    before = impact[impact["period"] == "3d_before"].copy()
    # The return is "price at -3d vs price at unlock day"
    # If price drops 50 bps from -3d to unlock → we SHORT → we GAIN 50 bps
    before["strategy_pnl_bps"] = -before["return_bps"]  # short = gain when price drops
    before["net_pnl_bps"] = before["strategy_pnl_bps"] - 4.0  # 4 bps cost

    # Also test: short 7d before, cover 3d after (longer hold)
    before_7 = impact[impact["period"] == "7d_before"].copy()
    after_3 = impact[impact["period"] == "3d_after"]

    trades = []
    for _, b in before.iterrows():
        trades.append({
            "symbol": b["symbol"], "unlock_date": b["unlock_date"],
            "pct_supply": b["pct_supply"],
            "strategy": "short_3d_before",
            "gross_bps": b["strategy_pnl_bps"],
            "net_bps": b["net_pnl_bps"],
        })

    # Long-hold variant: short 7d before, cover 3d after
    for _, b7 in before_7.iterrows():
        a3 = after_3[(after_3["symbol"] == b7["symbol"]) & (after_3["unlock_date"] == b7["unlock_date"])]
        if a3.empty:
            continue
        # Total return from -7d to +3d
        total_ret = a3.iloc[0]["return_bps"] - b7["return_bps"]  # price change over full period
        trades.append({
            "symbol": b7["symbol"], "unlock_date": b7["unlock_date"],
            "pct_supply": b7["pct_supply"],
            "strategy": "short_7d_cover_3d_after",
            "gross_bps": -total_ret,  # short
            "net_bps": -total_ret - 4.0,
        })

    return pd.DataFrame(trades)


def plot_average_unlock_profile(impact):
    if impact.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Average price profile around unlock
    ax = axes[0]
    avg = impact.groupby("days")["return_bps"].agg(["mean", "std", "count"])
    ax.bar(avg.index, avg["mean"],
           color=["#f85149" if v < 0 else "#3fb950" for v in avg["mean"]],
           edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.axvline(0, color="yellow", linewidth=1.5, linestyle="--", label="Unlock day")
    for i, row in avg.iterrows():
        ax.text(i, row["mean"] + (3 if row["mean"] >= 0 else -8),
                f"n={row['count']:.0f}", ha="center", fontsize=9, color="#7d8590")
    ax.set_xlabel("Days relative to unlock")
    ax.set_ylabel("Mean return vs unlock day (bps)")
    ax.set_title("Average Price Profile Around Token Unlocks")
    ax.legend()

    # By supply percentage
    ax = axes[1]
    impact_cp = impact.copy()
    impact_cp["size"] = pd.cut(impact_cp["pct_supply"], bins=[0, 1, 3, 20], labels=["<1%", "1-3%", ">3%"])
    before3 = impact_cp[impact_cp["period"] == "3d_before"]
    for size in ["<1%", "1-3%", ">3%"]:
        sub = before3[before3["size"] == size]
        if len(sub) > 2:
            ax.bar(size, -sub["return_bps"].mean(),
                   color="#3fb950" if -sub["return_bps"].mean() > 0 else "#f85149",
                   edgecolor="white", linewidth=0.5)
            ax.text(size, -sub["return_bps"].mean() + 2, f"n={len(sub)}", ha="center", fontsize=10)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Short P&L 3d before unlock (bps)")
    ax.set_title("Edge by Unlock Size (% of supply)")

    plt.tight_layout()
    savefig("unlock_profile.png")


def plot_by_symbol(trades):
    if trades.empty:
        return
    short3 = trades[trades["strategy"] == "short_3d_before"]
    if short3.empty:
        return
    by_sym = short3.groupby("symbol")["net_bps"].agg(["mean", "count", "sum"])
    by_sym = by_sym.sort_values("mean", ascending=False)

    fig, ax = plt.subplots(figsize=(12, 5))
    colors = ["#3fb950" if v > 0 else "#f85149" for v in by_sym["mean"]]
    ax.barh(by_sym.index, by_sym["mean"], color=colors, edgecolor="white", linewidth=0.5)
    for i, (sym, row) in enumerate(by_sym.iterrows()):
        ax.text(row["mean"] + (2 if row["mean"] >= 0 else -15),
                i, f"n={row['count']:.0f} tot={row['sum']:+.0f}bps",
                va="center", fontsize=10)
    ax.axvline(0, color="white", linewidth=0.5)
    ax.set_xlabel("Mean net P&L per unlock (bps)")
    ax.set_title("Token Unlock Short Strategy by Symbol")
    plt.tight_layout()
    savefig("unlock_by_symbol.png")


def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 12 — Token Unlock Calendar")
    print("=" * 70)

    print("\nFetching data...")
    loop = asyncio.new_event_loop()
    klines, protos = loop.run_until_complete(fetch_all_data())

    print(f"\n  Known unlock events: {len(KNOWN_UNLOCKS)}")
    print(f"  Symbols with futures data: {klines['symbol'].nunique()}")

    # Upcoming unlocks
    today = datetime.now(timezone.utc).date()
    upcoming = [(s, d, desc, pct) for s, d, desc, pct in KNOWN_UNLOCKS
                if pd.Timestamp(d).date() >= today]
    past = [(s, d, desc, pct) for s, d, desc, pct in KNOWN_UNLOCKS
            if pd.Timestamp(d).date() < today]

    print(f"\n── Upcoming Unlocks ({len(upcoming)}) ──")
    for sym, date, desc, pct in sorted(upcoming, key=lambda x: x[1])[:10]:
        days_until = (pd.Timestamp(date).date() - today).days
        print(f"  {date} | {sym:5s} | {desc:20s} | {pct:.1f}% supply | in {days_until}d")

    # ── Analyze past unlocks ─────────────────────────────────────
    print(f"\n── Past Unlock Analysis ({len(past)} events) ──")
    impact = analyze_unlock_impact(klines, past)
    if not impact.empty:
        print("  Average price change around unlock day:")
        avg = impact.groupby("period")[["return_bps"]].agg(["mean", "count"])
        avg.columns = ["mean_bps", "n"]
        for period in ["7d_before", "3d_before", "1d_before", "1d_after", "3d_after", "7d_after"]:
            if period in avg.index:
                r = avg.loc[period]
                print(f"    {period:15s}: {r['mean_bps']:+.0f} bps (n={r['n']:.0f})")
        impact.to_csv(f"{OUTPUT_DIR}/unlock_impact.csv", index=False)

    # ── Strategy backtest ────────────────────────────────────────
    print(f"\n── Strategy Backtest ──")
    trades = analyze_strategy(impact)
    if not trades.empty:
        for strat in trades["strategy"].unique():
            st = trades[trades["strategy"] == strat]
            print(f"\n  {strat}:")
            print(f"    {len(st)} trades | gross {st['gross_bps'].mean():+.1f} bps | "
                  f"net {st['net_bps'].mean():+.1f} bps | "
                  f"win {(st['net_bps']>0).mean():.0%} | total {st['net_bps'].sum():+.0f} bps")

        # By symbol
        print("\n  Par symbole (short 3d before):")
        short3 = trades[trades["strategy"] == "short_3d_before"]
        for sym in short3["symbol"].unique():
            s = short3[short3["symbol"] == sym]
            print(f"    {sym:5s}: {len(s)} unlocks | net {s['net_bps'].mean():+.1f} bps | "
                  f"total {s['net_bps'].sum():+.0f} bps")
        trades.to_csv(f"{OUTPUT_DIR}/unlock_trades.csv", index=False)

    # ── Plots ────────────────────────────────────────────────────
    print("\nPlots...")
    plot_average_unlock_profile(impact)
    plot_by_symbol(trades)

    # ── Verdict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    if not trades.empty:
        best_strat = trades.groupby("strategy")["net_bps"].mean().idxmax()
        best = trades[trades["strategy"] == best_strat]
        print(f"  Meilleure stratégie: {best_strat}")
        print(f"  Net moyen: {best['net_bps'].mean():+.1f} bps/trade")
        print(f"  Win rate: {(best['net_bps']>0).mean():.0%}")

        # Tradeable on Binance Futures?
        tradeable_syms = set(short3["symbol"].unique()) & TRADEABLE
        print(f"\n  Symboles tradeable sur Binance Futures: {', '.join(tradeable_syms)}")

        # Practical edge
        avg_unlocks_month = len(past) / max(1, (pd.Timestamp(past[-1][1]) - pd.Timestamp(past[0][1])).days / 30)
        print(f"  ~{avg_unlocks_month:.0f} unlocks/mois tradeable")
        monthly_bps = avg_unlocks_month * best["net_bps"].mean()
        print(f"  Estimation: ~{monthly_bps:.0f} bps/mois")

    return {"impact": impact, "trades": trades, "upcoming": upcoming}


if __name__ == "__main__":
    run()
