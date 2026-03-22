"""Study 10 — Symbol Scanner: find the best altcoins for OI divergence.

Criteria (what makes ADA work):
1. Medium volume (not BTC-level efficient, not dead)
2. High OI relative to volume (lots of positions = more divergence)
3. Reasonable spread (tradeable)
4. Perpetual USDT-M futures on Binance

Fetches ALL Binance Futures symbols, ranks them, returns top candidates.

Run: python3 -m analysis.study_10_symbol_scan
"""

from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from analysis.utils import apply_dark_theme, savefig, OUTPUT_DIR


async def fetch_all_futures_data():
    """Fetch exchange info, 24h tickers, and OI for all USDT-M perpetuals."""
    async with aiohttp.ClientSession() as session:

        # 1. Exchange info — get all symbols
        print("  Fetching exchange info...")
        async with session.get("https://fapi.binance.com/fapi/v1/exchangeInfo") as resp:
            info = await resp.json()

        perpetuals = [s for s in info["symbols"]
                     if s["contractType"] == "PERPETUAL"
                     and s["quoteAsset"] == "USDT"
                     and s["status"] == "TRADING"]
        symbols = [s["symbol"] for s in perpetuals]
        print(f"  Found {len(symbols)} USDT-M perpetuals")

        # 2. 24h tickers — volume, price change
        print("  Fetching 24h tickers...")
        async with session.get("https://fapi.binance.com/fapi/v1/ticker/24hr") as resp:
            tickers_raw = await resp.json()
        tickers = {t["symbol"]: t for t in tickers_raw}

        # 3. OI for each symbol (batch)
        print("  Fetching open interest...")
        oi_data = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            tasks = []
            for sym in batch:
                url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}"
                tasks.append(session.get(url))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for sym, resp in zip(batch, results):
                if isinstance(resp, Exception):
                    continue
                try:
                    data = await resp.json()
                    oi_data[sym] = float(data.get("openInterest", 0))
                except Exception:
                    pass
            await asyncio.sleep(0.2)  # rate limit

        # 4. Book ticker for spread
        print("  Fetching book tickers (spread)...")
        async with session.get("https://fapi.binance.com/fapi/v1/ticker/bookTicker") as resp:
            books_raw = await resp.json()
        books = {b["symbol"]: b for b in books_raw}

    # Build dataframe
    rows = []
    for sym_info in perpetuals:
        sym = sym_info["symbol"]
        tick = tickers.get(sym, {})
        book = books.get(sym, {})
        oi = oi_data.get(sym, 0)

        price = float(tick.get("lastPrice", 0))
        volume_24h = float(tick.get("quoteVolume", 0))
        trades_24h = int(tick.get("count", 0))
        price_change_pct = float(tick.get("priceChangePercent", 0))

        bid = float(book.get("bidPrice", 0))
        ask = float(book.get("askPrice", 0))
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else price
        spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 999

        oi_value = oi * price  # OI in USDT

        rows.append({
            "symbol": sym,
            "price": price,
            "volume_24h_usdt": volume_24h,
            "trades_24h": trades_24h,
            "price_change_pct": price_change_pct,
            "oi_contracts": oi,
            "oi_value_usdt": oi_value,
            "spread_bps": round(spread_bps, 2),
            "oi_volume_ratio": oi_value / volume_24h if volume_24h > 0 else 0,
        })

    return pd.DataFrame(rows)


def score_symbols(df: pd.DataFrame) -> pd.DataFrame:
    """Score each symbol for OI divergence suitability."""
    # Filter: minimum requirements
    df = df[
        (df["volume_24h_usdt"] > 10_000_000) &    # > $10M daily volume
        (df["trades_24h"] > 50_000) &               # > 50K trades/day
        (df["spread_bps"] < 20) &                    # < 20 bps spread
        (df["oi_value_usdt"] > 5_000_000)            # > $5M OI
    ].copy()

    if df.empty:
        return df

    # Exclude stablecoins and BTC/ETH (too efficient)
    exclude = ["BTCUSDT", "ETHUSDT", "BTCDOMUSDT", "DEFIUSDT"]
    df = df[~df["symbol"].isin(exclude)]

    # Score components (0-1 normalized, higher = better for our strategy)

    # 1. OI/Volume ratio: higher = more positions relative to activity = more divergence
    df["score_oi_ratio"] = df["oi_volume_ratio"].rank(pct=True)

    # 2. Volume sweet spot: not too high (efficient), not too low (illiquid)
    # Log-transform, penalize extremes. ADA is ~$200M/day as reference
    log_vol = np.log10(df["volume_24h_usdt"])
    ideal_log = np.log10(200_000_000)  # $200M
    df["score_volume"] = 1 - np.abs(log_vol - ideal_log) / 3
    df["score_volume"] = df["score_volume"].clip(0, 1)

    # 3. Spread: lower = cheaper to trade
    df["score_spread"] = 1 - df["spread_bps"].rank(pct=True)

    # 4. OI absolute: higher = more signals
    df["score_oi_abs"] = df["oi_value_usdt"].rank(pct=True)

    # 5. Volatility (from 24h price change): moderate vol = more opportunities
    df["abs_change"] = df["price_change_pct"].abs()
    df["score_vol"] = 1 - np.abs(df["abs_change"] - 3) / 10  # ideal ~3% daily
    df["score_vol"] = df["score_vol"].clip(0, 1)

    # Combined score
    df["total_score"] = (
        df["score_oi_ratio"] * 0.30 +  # Most important: OI/volume ratio
        df["score_volume"] * 0.20 +     # Volume sweet spot
        df["score_spread"] * 0.20 +     # Low spread
        df["score_oi_abs"] * 0.15 +     # OI size
        df["score_vol"] * 0.15          # Volatility
    )

    return df.sort_values("total_score", ascending=False)


def classify_tiers(df: pd.DataFrame) -> pd.DataFrame:
    """Classify into tiers for the bot."""
    df = df.copy()
    df["tier"] = "C"
    df.loc[df["total_score"] > df["total_score"].quantile(0.7), "tier"] = "B"
    df.loc[df["total_score"] > df["total_score"].quantile(0.9), "tier"] = "A"
    return df


def plot_scores(df):
    if df.empty:
        return
    top = df.head(25)
    fig, axes = plt.subplots(1, 2, figsize=(16, 8))

    # Score bar chart
    ax = axes[0]
    colors = {"A": "#3fb950", "B": "#d29922", "C": "#7d8590"}
    ax.barh(top["symbol"], top["total_score"],
            color=[colors[t] for t in top["tier"]],
            edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Composite Score")
    ax.set_title("Top 25 Altcoins for OI Divergence Strategy")
    ax.invert_yaxis()

    # Score components
    ax = axes[1]
    x = np.arange(min(15, len(top)))
    w = 0.15
    top15 = top.head(15)
    for i, (col, label) in enumerate([
        ("score_oi_ratio", "OI/Vol"), ("score_volume", "Volume"),
        ("score_spread", "Spread"), ("score_oi_abs", "OI Size"),
        ("score_vol", "Volatility")
    ]):
        ax.bar(x + i*w, top15[col], w, label=label)
    ax.set_xticks(x + 2*w)
    ax.set_xticklabels(top15["symbol"], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Component Score")
    ax.set_title("Score Breakdown (Top 15)")
    ax.legend(fontsize=8)

    plt.tight_layout()
    savefig("symbol_scan_scores.png")


def plot_volume_vs_oi(df):
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 8))
    top = df.head(30)
    sizes = top["total_score"] * 500
    colors_map = {"A": "#3fb950", "B": "#d29922", "C": "#7d8590"}
    colors = [colors_map[t] for t in top["tier"]]
    ax.scatter(top["volume_24h_usdt"] / 1e6, top["oi_value_usdt"] / 1e6,
               s=sizes, c=colors, alpha=0.7, edgecolors="white", linewidth=0.5)
    for _, r in top.iterrows():
        ax.annotate(r["symbol"].replace("USDT", ""),
                    (r["volume_24h_usdt"]/1e6, r["oi_value_usdt"]/1e6),
                    fontsize=8, color="white", ha="center")
    ax.set_xlabel("24h Volume ($M)")
    ax.set_ylabel("Open Interest ($M)")
    ax.set_title("Volume vs OI — Size = Score, Green = Tier A")
    ax.set_xscale("log")
    ax.set_yscale("log")
    plt.tight_layout()
    savefig("symbol_scan_scatter.png")


def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 10 — Symbol Scanner: Best Altcoins for OI Divergence")
    print("=" * 70)

    print("\nFetching Binance Futures data...")
    loop = asyncio.get_event_loop()
    raw = loop.run_until_complete(fetch_all_futures_data())
    print(f"  {len(raw)} symbols fetched")

    print("\nScoring symbols...")
    scored = score_symbols(raw)
    scored = classify_tiers(scored)
    print(f"  {len(scored)} symbols after filtering")

    # Display top 20
    display_cols = ["symbol", "tier", "total_score", "volume_24h_usdt",
                    "oi_value_usdt", "oi_volume_ratio", "spread_bps", "trades_24h"]
    top20 = scored.head(20).copy()
    top20["volume_24h_usdt"] = (top20["volume_24h_usdt"] / 1e6).round(0).astype(int).astype(str) + "M"
    top20["oi_value_usdt"] = (top20["oi_value_usdt"] / 1e6).round(0).astype(int).astype(str) + "M"
    top20["oi_volume_ratio"] = top20["oi_volume_ratio"].round(3)
    top20["total_score"] = top20["total_score"].round(3)

    print("\n── Top 20 Candidates ──")
    print(top20[display_cols].to_string(index=False))

    # Tier breakdown
    tier_a = scored[scored["tier"] == "A"]
    tier_b = scored[scored["tier"] == "B"]
    print(f"\n── Tier A ({len(tier_a)} symbols) — Add first ──")
    for _, r in tier_a.iterrows():
        print(f"  {r['symbol']:12s} | vol ${r['volume_24h_usdt']/1e6:.0f}M | "
              f"OI ${r['oi_value_usdt']/1e6:.0f}M | spread {r['spread_bps']:.1f}bps | "
              f"OI/vol {r['oi_volume_ratio']:.3f}")

    print(f"\n── Tier B ({len(tier_b)} symbols) — Add second ──")
    for _, r in tier_b.head(10).iterrows():
        print(f"  {r['symbol']:12s} | vol ${r['volume_24h_usdt']/1e6:.0f}M | "
              f"OI ${r['oi_value_usdt']/1e6:.0f}M | spread {r['spread_bps']:.1f}bps")

    # Recommendation
    recommended = list(tier_a["symbol"].values) + list(tier_b.head(5)["symbol"].values)
    print(f"\n══ RECOMMANDATION: {len(recommended)} symboles à ajouter au bot ══")
    print(f"  {', '.join(recommended)}")
    print(f"\n  + ADAUSDT (déjà actif)")
    print(f"  = {len(recommended)+1} symboles total")

    est_trades_day = (len(recommended) + 1) * 5  # ~5 trades/day/symbol
    est_daily_bps = est_trades_day * 15  # ~15 bps net conservateur
    print(f"\n  Estimation: ~{est_trades_day} trades/jour × ~15 bps net")
    print(f"  = ~{est_daily_bps} bps/jour")
    print(f"  Sur 1000€: ~{est_daily_bps/100:.0f}€/jour = ~{est_daily_bps/100*30:.0f}€/mois")
    print(f"  Sur 5000€: ~{est_daily_bps/100*5:.0f}€/jour = ~{est_daily_bps/100*5*30:.0f}€/mois")

    # Save
    scored.to_csv(f"{OUTPUT_DIR}/symbol_scan.csv", index=False)

    print("\nPlots...")
    plot_scores(scored)
    plot_volume_vs_oi(scored)

    return {"scored": scored, "recommended": recommended}


if __name__ == "__main__":
    run()
