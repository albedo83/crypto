"""Study 08 — Radical: unconventional strategies nobody uses.

1. Pairs trading (market-neutral ADA/BTC ratio mean-reversion)
2. Liquidation chain surfing (ride the cascade, reverse at exhaustion)
3. OI compression → breakout (loaded spring)
4. Basis velocity (speed of basis change, not level)
5. Correlation breakdown (BTC-ETH decorrelation = opportunity)

Run: python3 -m analysis.study_08_radical
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from analysis.db import fetch_df
from analysis.utils import (
    ID_TO_SYMBOL, apply_dark_theme, savefig, OUTPUT_DIR, add_session_column,
)

COST_BPS = 4.0


def load_data():
    print("  Loading...")
    price = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket, instrument_id,
               last(price, exchange_ts) AS close, sum(notional) AS volume, count(*) AS trades
        FROM trades_raw GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    price["bucket"] = pd.to_datetime(price["bucket"], utc=True)
    price["symbol"] = price["instrument_id"].map(ID_TO_SYMBOL)

    oi = fetch_df("SELECT exchange_ts, instrument_id, open_interest FROM open_interest ORDER BY instrument_id, exchange_ts")
    oi["exchange_ts"] = pd.to_datetime(oi["exchange_ts"], utc=True)

    basis = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket, instrument_id,
               last(basis_bps, exchange_ts) AS basis_bps,
               last(mark_price, exchange_ts) AS mark, last(index_price, exchange_ts) AS spot
        FROM mark_index GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    basis["bucket"] = pd.to_datetime(basis["bucket"], utc=True)

    liq = fetch_df("""
        SELECT exchange_ts, instrument_id, side, notional
        FROM liquidations ORDER BY exchange_ts
    """)
    liq["exchange_ts"] = pd.to_datetime(liq["exchange_ts"], utc=True)

    print(f"    price: {len(price)}, oi: {len(oi)}, basis: {len(basis)}, liq: {len(liq)}")
    return {"price": price, "oi": oi, "basis": basis, "liq": liq}


# ═════════════════════════════════════════════════════════════════════
# 1. PAIRS TRADING: ADA/BTC ratio mean-reversion
# ═════════════════════════════════════════════════════════════════════

def test_pairs_trading(price):
    """Trade the ADA/BTC price ratio — market neutral."""
    btc = price[price["instrument_id"] == 1][["bucket", "close"]].rename(columns={"close": "btc"})
    ada = price[price["instrument_id"] == 3][["bucket", "close"]].rename(columns={"close": "ada"})
    eth = price[price["instrument_id"] == 2][["bucket", "close"]].rename(columns={"close": "eth"})

    df = btc.merge(ada, on="bucket").merge(eth, on="bucket")
    df = df.sort_values("bucket")

    results = {}
    for pair_name, num, den in [("ADA/BTC", "ada", "btc"), ("ETH/BTC", "eth", "btc"), ("ADA/ETH", "ada", "eth")]:
        df["ratio"] = df[num] / df[den]
        # Z-score of ratio (rolling 120min = 2h window)
        df["ratio_mean"] = df["ratio"].rolling(120).mean()
        df["ratio_std"] = df["ratio"].rolling(120).std()
        df["ratio_z"] = (df["ratio"] - df["ratio_mean"]) / df["ratio_std"]

        # Forward ratio return at various horizons
        for mins in (30, 60, 120, 240):
            df[f"ratio_ret_{mins}m"] = (df["ratio"].shift(-mins) / df["ratio"] - 1) * 1e4

        # Strategy: short ratio when z > 1.5, long when z < -1.5
        trades = []
        position = 0
        entry_bar = 0
        for i in range(len(df)):
            z = df.iloc[i]["ratio_z"]
            if not np.isfinite(z):
                continue
            if position == 0:
                if z > 1.5:
                    position = -1; entry_bar = i  # short ratio = short ADA, long BTC
                elif z < -1.5:
                    position = 1; entry_bar = i   # long ratio = long ADA, short BTC
            else:
                held = i - entry_bar
                # Exit: z crosses 0, or timeout 120 min
                if (position == 1 and z > 0) or (position == -1 and z < 0) or held >= 120:
                    entry_ratio = df.iloc[entry_bar]["ratio"]
                    exit_ratio = df.iloc[i]["ratio"]
                    gross = position * (exit_ratio / entry_ratio - 1) * 1e4
                    # Cost: 2 trades (both legs) = 2 × 4 bps = 8 bps
                    net = gross - 8.0
                    trades.append({
                        "pair": pair_name,
                        "direction": "LONG_RATIO" if position == 1 else "SHORT_RATIO",
                        "entry_time": str(df.iloc[entry_bar]["bucket"]),
                        "hold_min": held,
                        "entry_z": df.iloc[entry_bar]["ratio_z"],
                        "gross_bps": gross,
                        "net_bps": net,
                    })
                    position = 0

        # Correlation: ratio_z → forward ratio return
        corrs = {}
        for mins in (30, 60, 120, 240):
            col = f"ratio_ret_{mins}m"
            valid = df.dropna(subset=["ratio_z", col])
            if len(valid) > 100:
                rho, pval = spearmanr(valid["ratio_z"], valid[col])
                corrs[f"{mins}m"] = {"rho": rho, "pval": pval, "n": len(valid)}

        results[pair_name] = {
            "trades": pd.DataFrame(trades),
            "correlations": corrs,
        }

    return results


# ═════════════════════════════════════════════════════════════════════
# 2. LIQUIDATION CHAIN SURFING
# ═════════════════════════════════════════════════════════════════════

def test_liq_chain(liq, price):
    """Detect liquidation chains (3+ in 10s) and trade with momentum then reverse."""
    rows = []
    for iid in [1, 2, 3]:
        sym = ID_TO_SYMBOL[iid]
        l = liq[liq["instrument_id"] == iid].sort_values("exchange_ts")
        p = price[price["instrument_id"] == iid].sort_values("bucket")

        if len(l) < 10:
            continue

        # Detect chains: 3+ liquidations within 10 seconds
        chain_starts = []
        i = 0
        while i < len(l) - 2:
            window = l.iloc[i:i+20]  # look ahead
            t0 = window.iloc[0]["exchange_ts"]
            in_window = window[window["exchange_ts"] <= t0 + pd.Timedelta(seconds=10)]
            if len(in_window) >= 3:
                total_notional = float(in_window["notional"].sum())
                sell_count = (in_window["side"] == "SELL").sum()
                buy_count = (in_window["side"] == "BUY").sum()
                dominant = "SELL" if sell_count > buy_count else "BUY"
                chain_starts.append({
                    "time": t0, "count": len(in_window),
                    "notional": total_notional, "dominant": dominant,
                })
                i += len(in_window)  # skip past chain
            else:
                i += 1

        # For each chain, test: ride then reverse
        for chain in chain_starts:
            t0 = chain["time"]
            pr = p[(p["bucket"] >= t0 - pd.Timedelta(minutes=1))
                   & (p["bucket"] <= t0 + pd.Timedelta(hours=2))].sort_values("bucket")
            if len(pr) < 10:
                continue

            ref_rows = pr[pr["bucket"] <= t0]
            if ref_rows.empty:
                continue
            ref_price = ref_rows.iloc[-1]["close"]

            for mins in (1, 5, 15, 30, 60):
                target = pr[pr["bucket"] <= t0 + pd.Timedelta(minutes=mins)]
                if target.empty:
                    continue
                ret = (target.iloc[-1]["close"] / ref_price - 1) * 1e4

                # "Surf" strategy: trade WITH dominant side for first 5 min
                if mins <= 5:
                    surf_dir = -1 if chain["dominant"] == "SELL" else 1  # sell-dom → price drops → short
                    surf_pnl = surf_dir * ret
                else:
                    # "Reverse" strategy: fade after 5 min
                    rev_dir = 1 if chain["dominant"] == "SELL" else -1
                    after_5 = pr[pr["bucket"] >= t0 + pd.Timedelta(minutes=5)]
                    if after_5.empty:
                        continue
                    ref5 = after_5.iloc[0]["close"]
                    target_r = pr[pr["bucket"] <= t0 + pd.Timedelta(minutes=mins)]
                    if target_r.empty:
                        continue
                    rev_ret = (target_r.iloc[-1]["close"] / ref5 - 1) * 1e4
                    surf_pnl = rev_dir * rev_ret

                rows.append({
                    "symbol": sym, "chain_time": t0,
                    "dominant": chain["dominant"], "liq_count": chain["count"],
                    "notional": chain["notional"],
                    "horizon_min": mins,
                    "raw_ret_bps": ret,
                    "strategy": "surf" if mins <= 5 else "reverse",
                    "pnl_bps": surf_pnl,
                })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# 3. OI COMPRESSION → BREAKOUT (loaded spring)
# ═════════════════════════════════════════════════════════════════════

def test_oi_breakout(oi, price):
    """High OI + low volatility = loaded spring. Trade the breakout."""
    rows = []
    for iid in [1, 2, 3]:
        sym = ID_TO_SYMBOL[iid]
        p = price[price["instrument_id"] == iid].sort_values("bucket").copy()
        o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts").copy()

        if len(p) < 200 or len(o) < 20:
            continue

        # Rolling volatility (30-min)
        p["vol_30m"] = p["close"].pct_change().rolling(30).std() * 1e4
        p["vol_median"] = p["vol_30m"].expanding().median()
        p["low_vol"] = p["vol_30m"] < p["vol_median"] * 0.7  # unusually low vol

        # OI at 1-min (forward fill from 5-min OI)
        o_r = o.rename(columns={"exchange_ts": "bucket"})[["bucket", "open_interest"]]
        o_r["bucket"] = o_r["bucket"].dt.floor("1min")
        p = p.merge(o_r, on="bucket", how="left")
        p["open_interest"] = p["open_interest"].ffill()
        p["oi_z"] = (p["open_interest"] - p["open_interest"].rolling(120).mean()) / p["open_interest"].rolling(120).std()

        # "Loaded spring" = high OI + low vol
        p["spring"] = (p["oi_z"] > 1.0) & p["low_vol"]

        # Forward absolute return (breakout magnitude)
        for mins in (30, 60, 120):
            p[f"abs_ret_{mins}m"] = p["close"].pct_change(mins).shift(-mins).abs() * 1e4

        # When spring is loaded, is the subsequent move bigger?
        for mins in (30, 60, 120):
            col = f"abs_ret_{mins}m"
            spring = p[p["spring"]].dropna(subset=[col])
            normal = p[~p["spring"]].dropna(subset=[col])
            if len(spring) > 10 and len(normal) > 100:
                rows.append({
                    "symbol": sym, "horizon": f"{mins}m",
                    "spring_abs_ret": spring[col].mean(),
                    "normal_abs_ret": normal[col].mean(),
                    "ratio": spring[col].mean() / normal[col].mean() if normal[col].mean() > 0 else 0,
                    "n_spring": len(spring), "n_normal": len(normal),
                })

        # Can we predict DIRECTION? Use OFI from first 5 min after spring detection
        p["ret_5m"] = p["close"].pct_change(5).shift(-5) * 1e4
        p["ret_60m"] = p["close"].pct_change(60).shift(-60) * 1e4
        springs = p[p["spring"]].dropna(subset=["ret_5m", "ret_60m"])
        if len(springs) > 10:
            # If first 5 min is positive → momentum → 60 min also positive?
            springs = springs.copy()
            springs["dir_5m"] = np.sign(springs["ret_5m"])
            mom_correct = (springs["dir_5m"] * springs["ret_60m"] > 0).mean()
            rows.append({
                "symbol": sym, "horizon": "momentum_60m",
                "spring_abs_ret": springs["ret_60m"].abs().mean(),
                "normal_abs_ret": 0,
                "ratio": mom_correct,
                "n_spring": len(springs), "n_normal": 0,
            })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# 4. BASIS VELOCITY
# ═════════════════════════════════════════════════════════════════════

def test_basis_velocity(basis, price):
    """Not the level of basis, but how fast it changes."""
    rows = []
    for iid in [1, 2, 3]:
        sym = ID_TO_SYMBOL[iid]
        b = basis[basis["instrument_id"] == iid].sort_values("bucket").copy()
        p = price[price["instrument_id"] == iid].sort_values("bucket")

        if len(b) < 100:
            continue

        # Basis velocity = change over 5 min
        b["basis_vel"] = b["basis_bps"].diff(5)
        # Basis acceleration = change of velocity
        b["basis_accel"] = b["basis_vel"].diff(5)
        # Z-scores
        b["vel_z"] = (b["basis_vel"] - b["basis_vel"].rolling(60).mean()) / b["basis_vel"].rolling(60).std()

        merged = b.merge(p[["bucket", "close"]], on="bucket", how="inner")
        for mins in (30, 60, 120):
            merged[f"ret_{mins}m"] = merged["close"].pct_change(mins).shift(-mins) * 1e4

        # Rapid basis expansion → short (speculation bubble → will pop)
        # Rapid basis contraction → long (panic → will recover)
        for horizon in ("ret_30m", "ret_60m", "ret_120m"):
            valid = merged.dropna(subset=["vel_z", horizon])
            if len(valid) < 100:
                continue
            rho, pval = spearmanr(valid["vel_z"], valid[horizon])
            rows.append({
                "signal": "basis_velocity", "symbol": sym,
                "horizon": horizon, "rho": rho, "pval": pval, "n": len(valid),
            })
            # Also test acceleration
            valid2 = merged.dropna(subset=["basis_accel", horizon])
            if len(valid2) > 100:
                rho2, pval2 = spearmanr(valid2["basis_accel"], valid2[horizon])
                rows.append({
                    "signal": "basis_accel", "symbol": sym,
                    "horizon": horizon, "rho": rho2, "pval": pval2, "n": len(valid2),
                })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# 5. CORRELATION BREAKDOWN
# ═════════════════════════════════════════════════════════════════════

def test_correlation_breakdown(price):
    """When BTC-ETH or BTC-ADA decorrelate, trade the convergence."""
    pivot = price.pivot_table(index="bucket", columns="symbol", values="close").dropna()
    rets = pivot.pct_change().dropna()

    rows = []
    for pair, s1, s2 in [("BTC-ETH", "BTCUSDT", "ETHUSDT"),
                          ("BTC-ADA", "BTCUSDT", "ADAUSDT")]:
        # Rolling 30-min correlation
        roll_corr = rets[s1].rolling(30).corr(rets[s2])
        # When correlation drops below 0.5 = decorrelation event
        rets_copy = rets.copy()
        rets_copy["corr"] = roll_corr
        rets_copy["decorr"] = roll_corr < 0.3

        # During decorrelation: which one moved more? → fade the bigger mover
        rets_copy["ret_diff_30m"] = (
            pivot[s1].pct_change(30) - pivot[s2].pct_change(30)
        ) * 1e4  # positive = s1 outperformed

        # Forward convergence: does the spread revert?
        rets_copy["spread_ret_60m"] = rets_copy["ret_diff_30m"].shift(-60)
        rets_copy["spread_ret_120m"] = rets_copy["ret_diff_30m"].shift(-120)

        decorr = rets_copy[rets_copy["decorr"]].dropna(subset=["ret_diff_30m", "spread_ret_60m"])
        if len(decorr) > 20:
            # When s1 outperformed during decorr, does s2 catch up?
            rho60, pval60 = spearmanr(decorr["ret_diff_30m"], decorr["spread_ret_60m"])
            rho120, _ = spearmanr(decorr["ret_diff_30m"], decorr["spread_ret_120m"].dropna()) if len(decorr.dropna(subset=["spread_ret_120m"])) > 20 else (np.nan, np.nan)

            rows.append({
                "pair": pair,
                "decorr_events": decorr["decorr"].sum(),
                "rho_convergence_60m": rho60,
                "rho_convergence_120m": rho120,
                "pval_60m": pval60,
                "mean_spread_bps": decorr["ret_diff_30m"].mean(),
                "n": len(decorr),
            })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# PLOTS
# ═════════════════════════════════════════════════════════════════════

def plot_pairs(pairs_results):
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, (pair, data) in zip(axes, pairs_results.items()):
        trades = data["trades"]
        if trades.empty:
            ax.set_title(f"{pair}: no trades")
            continue
        cum_net = trades["net_bps"].cumsum()
        cum_gross = trades["gross_bps"].cumsum()
        ax.plot(range(len(trades)), cum_gross, color="#3fb950", linewidth=1.5, label="Gross")
        ax.plot(range(len(trades)), cum_net, color="#f85149", linewidth=1.5, label=f"Net (-8bps)")
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        n = len(trades)
        wr = (trades["gross_bps"] > 0).mean()
        ax.set_title(f"{pair}: {n} trades, win {wr:.0%}, net {cum_net.iloc[-1]:+.0f}bps")
        ax.set_xlabel("Trade #")
        ax.legend(fontsize=8)
    axes[0].set_ylabel("Cumulative P&L (bps)")
    plt.tight_layout()
    savefig("radical_pairs.png")


def plot_liq_chains(liq_df):
    if liq_df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, strat in zip(axes, ["surf", "reverse"]):
        sub = liq_df[liq_df["strategy"] == strat]
        if sub.empty:
            continue
        pivoted = sub.groupby("horizon_min")["pnl_bps"].agg(["mean", "count"])
        colors = ["#3fb950" if v > 0 else "#f85149" for v in pivoted["mean"]]
        ax.bar(pivoted.index, pivoted["mean"], color=colors, edgecolor="white", linewidth=0.5)
        for i, (idx, row) in enumerate(pivoted.iterrows()):
            ax.text(idx, row["mean"] + (1 if row["mean"] >= 0 else -2),
                    f"n={row['count']:.0f}", ha="center", fontsize=9)
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_xlabel("Minutes")
        ax.set_ylabel("Mean P&L (bps)")
        ax.set_title(f"Liq Chain: {strat.upper()}")
    plt.tight_layout()
    savefig("radical_liq_chains.png")


def plot_spring(spring_df):
    if spring_df.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    regular = spring_df[spring_df["horizon"] != "momentum_60m"]
    for sym in regular["symbol"].unique():
        s = regular[regular["symbol"] == sym]
        ax.plot(s["horizon"], s["ratio"], marker="o", linewidth=2, label=sym)
    ax.axhline(1, color="white", linewidth=0.5, linestyle="--", alpha=0.5, label="baseline")
    ax.set_ylabel("Breakout magnitude ratio (spring / normal)")
    ax.set_title("OI Spring: Are breakouts bigger after compression?")
    ax.legend()
    plt.tight_layout()
    savefig("radical_spring.png")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 08 — RADICAL: Unconventional Strategies")
    print("=" * 70)

    data = load_data()

    # ── 1. Pairs Trading ─────────────────────────────────────────
    print("\n── 1. Pairs Trading (Market-Neutral) ──")
    pairs = test_pairs_trading(data["price"])
    for pair, res in pairs.items():
        trades = res["trades"]
        corrs = res["correlations"]
        print(f"\n  {pair}:")
        for h, c in corrs.items():
            print(f"    Ratio z → {h} return: rho={c['rho']:+.4f} p={c['pval']:.4f} (n={c['n']})")
        if not trades.empty:
            n = len(trades)
            gross = trades["gross_bps"].mean()
            net = trades["net_bps"].mean()
            wr = (trades["gross_bps"] > 0).mean()
            total = trades["net_bps"].sum()
            print(f"    Backtest: {n} trades | gross {gross:+.1f} | net {net:+.1f} bps/trade | "
                  f"win {wr:.0%} | total {total:+.0f} bps")
        trades.to_csv(f"{OUTPUT_DIR}/radical_pairs_{pair.replace('/', '_')}.csv", index=False)

    # ── 2. Liquidation Chains ────────────────────────────────────
    print("\n── 2. Liquidation Chain Surfing ──")
    liq_df = test_liq_chain(data["liq"], data["price"])
    if not liq_df.empty:
        for strat in ["surf", "reverse"]:
            sub = liq_df[liq_df["strategy"] == strat]
            if sub.empty:
                continue
            print(f"  {strat.upper()}:")
            for mins in sub["horizon_min"].unique():
                s = sub[sub["horizon_min"] == mins]
                avg = s["pnl_bps"].mean()
                wr = (s["pnl_bps"] > 0).mean()
                print(f"    {mins:3d}min: {avg:+.1f} bps | win {wr:.0%} (n={len(s)})")
        liq_df.to_csv(f"{OUTPUT_DIR}/radical_liq_chains.csv", index=False)

    # ── 3. OI Spring ─────────────────────────────────────────────
    print("\n── 3. OI Compression → Breakout ──")
    spring = test_oi_breakout(data["oi"], data["price"])
    if not spring.empty:
        for sym in spring["symbol"].unique():
            s = spring[spring["symbol"] == sym]
            print(f"  {sym}:")
            for _, r in s.iterrows():
                if r["horizon"] == "momentum_60m":
                    print(f"    Momentum at 60m: {r['ratio']:.0%} correct | "
                          f"avg |move| {r['spring_abs_ret']:.0f} bps (n={r['n_spring']:.0f})")
                else:
                    print(f"    {r['horizon']}: spring {r['spring_abs_ret']:.0f} vs normal {r['normal_abs_ret']:.0f} bps "
                          f"→ {r['ratio']:.1f}x bigger (n={r['n_spring']:.0f})")
        spring.to_csv(f"{OUTPUT_DIR}/radical_spring.csv", index=False)

    # ── 4. Basis Velocity ────────────────────────────────────────
    print("\n── 4. Basis Velocity ──")
    bv = test_basis_velocity(data["basis"], data["price"])
    if not bv.empty:
        for sym in bv["symbol"].unique():
            s = bv[bv["symbol"] == sym]
            print(f"  {sym}:")
            for _, r in s.iterrows():
                sig = "*" if r["pval"] < 0.05 else ""
                print(f"    {r['signal']:15s} → {r['horizon']}: rho={r['rho']:+.4f} {sig} (n={r['n']:.0f})")
        bv.to_csv(f"{OUTPUT_DIR}/radical_basis_vel.csv", index=False)

    # ── 5. Correlation Breakdown ─────────────────────────────────
    print("\n── 5. Correlation Breakdown ──")
    corr = test_correlation_breakdown(data["price"])
    if not corr.empty:
        print(corr.to_string(index=False, float_format="%.4f"))
        corr.to_csv(f"{OUTPUT_DIR}/radical_correlation.csv", index=False)

    # ── Plots ────────────────────────────────────────────────────
    print("\nPlots...")
    plot_pairs(pairs)
    plot_liq_chains(liq_df)
    plot_spring(spring)

    # ── VERDICT ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT — What's exploitable?")
    print("=" * 70)

    return {"pairs": pairs, "liq_chains": liq_df, "spring": spring,
            "basis_vel": bv, "correlation": corr}


if __name__ == "__main__":
    run()
