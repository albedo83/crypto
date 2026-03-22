"""Study 14 — Signal Fusion: does smart money improve the LiveBot?

Test 3 scenarios:
A. LiveBot actuel (OI divergence + funding + lead-lag)
B. Smart money seul
C. Fusion: OI divergence + smart money combinés

If B and C are better than A → integrate.
If B fires at different times than A → 3rd bot.

Run: python3 -m analysis.study_14_signal_fusion
"""

from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from analysis.utils import apply_dark_theme, OUTPUT_DIR

SYMBOLS = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "XRPUSDT",
           "BNBUSDT", "SUIUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT",
           "XMRUSDT", "LTCUSDT", "BCHUSDT"]
COST_BPS = 4.0


async def fetch_data(symbols, period="5m", limit=500):
    """Fetch L/S ratios, top L/S ratios, klines, and OI for all symbols."""
    async with aiohttp.ClientSession() as session:
        all_data = {}
        for sym in symbols:
            d = {}
            # Global L/S
            try:
                r = await session.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                                       params={"symbol": sym, "period": period, "limit": limit})
                if r.status == 200:
                    raw = await r.json()
                    df = pd.DataFrame(raw)
                    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                    df["crowd_long"] = df["longAccount"].astype(float)
                    d["crowd"] = df[["time", "crowd_long"]]
            except: pass

            # Top L/S
            try:
                r = await session.get("https://fapi.binance.com/futures/data/topLongShortPositionRatio",
                                       params={"symbol": sym, "period": period, "limit": limit})
                if r.status == 200:
                    raw = await r.json()
                    df = pd.DataFrame(raw)
                    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                    df["top_long"] = df["longAccount"].astype(float)
                    d["top"] = df[["time", "top_long"]]
            except: pass

            # OI
            try:
                r = await session.get("https://fapi.binance.com/futures/data/openInterestHist",
                                       params={"symbol": sym, "period": period, "limit": limit})
                if r.status == 200:
                    raw = await r.json()
                    if raw:
                        df = pd.DataFrame(raw)
                        df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                        df["oi"] = df["sumOpenInterest"].astype(float)
                        d["oi"] = df[["time", "oi"]]
            except: pass

            # Klines
            try:
                r = await session.get("https://fapi.binance.com/fapi/v1/klines",
                                       params={"symbol": sym, "interval": period, "limit": limit})
                if r.status == 200:
                    raw = await r.json()
                    df = pd.DataFrame(raw, columns=["ot","o","h","l","c","v","ct","qv","t","tb","tbq","i"])
                    df["time"] = pd.to_datetime(df["ot"], unit="ms", utc=True)
                    df["close"] = df["c"].astype(float)
                    d["price"] = df[["time", "close"]]
            except: pass

            if "crowd" in d and "price" in d:
                all_data[sym] = d
            await asyncio.sleep(0.5)

    return all_data


def build_signals(all_data):
    """Build all signal variants for each symbol."""
    results = []
    for sym, d in all_data.items():
        price = d["price"].sort_values("time")
        crowd = d.get("crowd", pd.DataFrame())
        top = d.get("top", pd.DataFrame())
        oi = d.get("oi", pd.DataFrame())

        # Merge all on time
        merged = price.copy()
        if not crowd.empty:
            merged = merged.merge(crowd, on="time", how="inner")
        if not top.empty:
            merged = merged.merge(top, on="time", how="inner")
        if not oi.empty:
            merged = merged.merge(oi, on="time", how="inner")

        if len(merged) < 60:
            continue

        # Forward returns
        for n, label in [(6,"30m"), (12,"60m"), (24,"120m")]:
            merged[f"ret_{label}"] = merged["close"].pct_change(n).shift(-n) * 1e4

        # Signal A: OI divergence
        if "oi" in merged.columns:
            merged["oi_change"] = merged["oi"].pct_change(3) * 100
            merged["price_change"] = merged["close"].pct_change(3) * 1e4
            merged["oi_signal"] = 0.0
            weak_long = (merged["price_change"] > 3) & (merged["oi_change"] < -0.03)
            weak_short = (merged["price_change"] < -3) & (merged["oi_change"] > 0.03)
            merged.loc[weak_long, "oi_signal"] = -1.0
            merged.loc[weak_short, "oi_signal"] = 1.0

        # Signal B: Smart money divergence
        if "crowd_long" in merged.columns and "top_long" in merged.columns:
            merged["smart_div"] = merged["top_long"] - merged["crowd_long"]
            merged["smart_z"] = (merged["smart_div"] - merged["smart_div"].rolling(60).mean()) / merged["smart_div"].rolling(60).std()
            merged["smart_signal"] = merged["smart_z"].clip(-2, 2) / 2  # normalize to [-1, 1]

        # Signal C: Contrarian (crowd extreme)
        if "crowd_long" in merged.columns:
            merged["crowd_z"] = (merged["crowd_long"] - merged["crowd_long"].rolling(60).mean()) / merged["crowd_long"].rolling(60).std()
            merged["contra_signal"] = -merged["crowd_z"].clip(-2, 2) / 2  # inverted

        merged["symbol"] = sym
        results.append(merged)

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def test_signals(df):
    """Compare A, B, C and A+B combined."""
    if df.empty:
        return {}

    configs = {
        "A_oi_only": ["oi_signal"],
        "B_smart_only": ["smart_signal"],
        "C_contra_only": ["contra_signal"],
        "AB_oi+smart": ["oi_signal", "smart_signal"],
        "ABC_all": ["oi_signal", "smart_signal", "contra_signal"],
    }

    all_results = {}
    for config_name, signal_cols in configs.items():
        valid_cols = [c for c in signal_cols if c in df.columns]
        if not valid_cols:
            continue

        # Composite = average of available signals
        df[f"composite_{config_name}"] = df[valid_cols].mean(axis=1)
        comp_col = f"composite_{config_name}"

        rows = []
        for sym in df["symbol"].unique():
            sub = df[df["symbol"] == sym]
            for horizon in ("ret_30m", "ret_60m", "ret_120m"):
                valid = sub.dropna(subset=[comp_col, horizon])
                if len(valid) < 30:
                    continue
                rho, pval = spearmanr(valid[comp_col], valid[horizon])
                rows.append({
                    "config": config_name, "symbol": sym,
                    "horizon": horizon, "rho": rho, "pval": pval, "n": len(valid),
                })
        all_results[config_name] = pd.DataFrame(rows)

    return all_results


def backtest_configs(df):
    """Backtest each config: enter when composite > threshold, hold 2h."""
    configs = {
        "A_oi_only": "oi_signal",
        "B_smart_only": "smart_signal",
        "AB_oi+smart": None,  # will compute
    }

    # Compute AB composite
    if "oi_signal" in df.columns and "smart_signal" in df.columns:
        df["ab_composite"] = df[["oi_signal", "smart_signal"]].mean(axis=1)
        configs["AB_oi+smart"] = "ab_composite"
    else:
        del configs["AB_oi+smart"]

    all_trades = {}
    for config_name, sig_col in configs.items():
        if sig_col is None or sig_col not in df.columns:
            continue

        trades = []
        for sym in df["symbol"].unique():
            sub = df[df["symbol"] == sym].sort_values("time").reset_index(drop=True)
            if len(sub) < 50:
                continue

            position = 0
            entry_bar = 0
            hold = 24  # 24 × 5m = 2h

            for i in range(len(sub)):
                sig = sub.iloc[i].get(sig_col, 0)
                if not np.isfinite(sig):
                    continue

                if position == 0:
                    if sig > 0.3:
                        position = 1; entry_bar = i
                    elif sig < -0.3:
                        position = -1; entry_bar = i
                else:
                    if i - entry_bar >= hold:
                        entry_p = sub.iloc[entry_bar]["close"]
                        exit_p = sub.iloc[i]["close"]
                        gross = position * (exit_p / entry_p - 1) * 1e4
                        trades.append({
                            "symbol": sym,
                            "direction": "LONG" if position == 1 else "SHORT",
                            "gross_bps": gross,
                            "net_bps": gross - COST_BPS,
                        })
                        position = 0

        all_trades[config_name] = pd.DataFrame(trades)

    return all_trades


def test_overlap(df):
    """How often do OI divergence and smart money fire at the same time?"""
    if "oi_signal" not in df.columns or "smart_signal" not in df.columns:
        return {}

    results = {}
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].dropna(subset=["oi_signal", "smart_signal"])
        if len(sub) < 30:
            continue

        oi_active = sub["oi_signal"].abs() > 0.3
        smart_active = sub["smart_signal"].abs() > 0.3
        both = oi_active & smart_active
        either = oi_active | smart_active

        # When both fire, do they agree on direction?
        both_rows = sub[both]
        if len(both_rows) > 5:
            agree = (np.sign(both_rows["oi_signal"]) == np.sign(both_rows["smart_signal"])).mean()
        else:
            agree = 0

        results[sym] = {
            "oi_only": oi_active.sum(),
            "smart_only": smart_active.sum(),
            "both": both.sum(),
            "either": either.sum(),
            "overlap_pct": both.sum() / either.sum() * 100 if either.sum() > 0 else 0,
            "agree_when_both": agree * 100,
        }

    return results


def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 14 — Signal Fusion: OI divergence + Smart Money")
    print("=" * 70)

    print("\nFetching data...")
    loop = asyncio.new_event_loop()
    all_data = loop.run_until_complete(fetch_data(SYMBOLS))
    print(f"  {len(all_data)} symbols with complete data")

    print("\nBuilding signals...")
    df = build_signals(all_data)
    if df.empty:
        print("  No data")
        return
    print(f"  {len(df)} rows, {df['symbol'].nunique()} symbols")

    # ── 1. Signal correlation ────────────────────────────────────
    print("\n── 1. Signal Correlation ──")
    sig_cols = [c for c in ["oi_signal", "smart_signal", "contra_signal"] if c in df.columns]
    corr = df[sig_cols].corr()
    print(corr.to_string(float_format="%.3f"))
    if "oi_signal" in corr.columns and "smart_signal" in corr.columns:
        c = corr.loc["oi_signal", "smart_signal"]
        print(f"\n  OI ↔ Smart money correlation: {c:.3f}")
        print(f"  → {'INDEPENDENT (good for fusion!)' if abs(c) < 0.3 else 'CORRELATED (redundant)'}")

    # ── 2. Signal overlap ────────────────────────────────────────
    print("\n── 2. Signal Overlap (when do they fire?) ──")
    overlap = test_overlap(df)
    for sym, o in overlap.items():
        print(f"  {sym:12s}: OI={o['oi_only']:3d} Smart={o['smart_only']:3d} "
              f"Both={o['both']:3d} | overlap {o['overlap_pct']:.0f}% | "
              f"agree {o['agree_when_both']:.0f}%")

    # ── 3. Predictive power comparison ───────────────────────────
    print("\n── 3. Predictive Power: A vs B vs A+B ──")
    results = test_signals(df)
    for config_name, rdf in results.items():
        if rdf.empty:
            continue
        for horizon in ("ret_60m", "ret_120m"):
            sub = rdf[rdf["horizon"] == horizon]
            if sub.empty:
                continue
            avg_rho = sub["rho"].mean()
            sig_count = (sub["pval"] < 0.05).sum()
            print(f"  {config_name:20s} {horizon}: avg rho={avg_rho:+.4f} | "
                  f"{sig_count}/{len(sub)} significant")

    # ── 4. Backtest comparison ───────────────────────────────────
    print("\n── 4. Backtest Comparison ──")
    bt = backtest_configs(df)
    for config_name, trades in bt.items():
        if trades.empty:
            continue
        n = len(trades)
        gross = trades["gross_bps"].mean()
        net = trades["net_bps"].mean()
        win = (trades["net_bps"] > 0).mean()
        total = trades["net_bps"].sum()
        print(f"  {config_name:20s}: {n:3d} trades | gross {gross:+.1f} | "
              f"net {net:+.1f} bps | win {win:.0%} | total {total:+.0f} bps")

    # ── Verdict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)

    if "A_oi_only" in bt and "AB_oi+smart" in bt:
        a = bt["A_oi_only"]
        ab = bt["AB_oi+smart"]
        if not a.empty and not ab.empty:
            improve = ab["net_bps"].mean() - a["net_bps"].mean()
            more_trades = len(ab) - len(a)
            print(f"  OI seul:        {a['net_bps'].mean():+.1f} bps/trade, {len(a)} trades")
            print(f"  OI + Smart:     {ab['net_bps'].mean():+.1f} bps/trade, {len(ab)} trades")
            print(f"  Amélioration:   {improve:+.1f} bps/trade, {more_trades:+d} trades")

            if improve > 0 and more_trades > 0:
                print(f"\n  → FUSIONNER dans le LiveBot (meilleur edge + plus de trades)")
            elif improve > 0 and more_trades <= 0:
                print(f"\n  → FUSIONNER (meilleur edge par trade)")
            elif improve <= 0 and more_trades > 10:
                print(f"\n  → 3ème BOT SÉPARÉ (edge pas meilleur mais plus de trades)")
            else:
                print(f"\n  → PAS CONCLUANT — garder le LiveBot actuel")

    return {"signals": df, "results": results, "backtest": bt, "overlap": overlap}


if __name__ == "__main__":
    run()
