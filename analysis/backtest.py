"""Backtest — Book imbalance strategy with vol/spread filters.

Strategy:
  LONG  when tob_bid_ratio > threshold_high AND low_vol AND tight_spread
  SHORT when tob_bid_ratio < threshold_low  AND low_vol AND tight_spread
  Hold for N bars (N × 5s), then exit.

Cost scenarios: gross, maker (4bps), realistic (5bps), taker (spread+8bps)

Run: python3 -m analysis.backtest
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from analysis.db import fetch_df
from analysis.utils import (
    ID_TO_SYMBOL, apply_dark_theme, savefig, OUTPUT_DIR, add_session_column,
)

# ── Strategy parameters ──────────────────────────────────────────────
PARAMS = {
    "threshold_high": 0.55,    # long when imbalance above
    "threshold_low": 0.45,     # short when imbalance below
    "hold_bars": 6,            # holding period = 6 × 5s = 30s
    "vol_filter": True,        # only trade in low-vol regime
    "spread_filter": True,     # only trade when spread is tight
    "vol_lookback": 60,        # rolling window for vol (5 min)
    "spread_lookback": 60,     # rolling window for spread
}

COST_SCENARIOS = {
    "gross":     0.0,   # no cost
    "maker":     4.0,   # 0.02% × 2 sides
    "realistic": 5.0,   # maker + 1 bps adverse selection
    "taker_ADA": 11.5,  # 0.06% fees + 3.5 bps spread
    "taker_BTC": 8.0,   # 0.06% fees + 0.02 bps spread
}


def load_data() -> pd.DataFrame:
    """Load and merge book + trade data at 5s."""
    print("  Loading book_imbalance_1s...")
    book = fetch_df("""
        SELECT bucket, instrument_id, tob_bid_ratio, avg_spread_bps, close_mid, tick_count
        FROM book_imbalance_1s ORDER BY instrument_id, bucket
    """)
    book["bucket"] = pd.to_datetime(book["bucket"], utc=True)

    print("  Loading trade_stats_5s...")
    trades = fetch_df("""
        SELECT time_bucket('5 seconds', exchange_ts) AS bucket,
               instrument_id,
               count(*) AS trade_count,
               sum(notional) AS total_notional,
               sum(CASE WHEN aggressor_side='BUY'  THEN notional ELSE 0 END) AS buy_notional,
               sum(CASE WHEN aggressor_side='SELL' THEN notional ELSE 0 END) AS sell_notional
        FROM trades_raw WHERE aggressor_side IS NOT NULL
        GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    trades["bucket"] = pd.to_datetime(trades["bucket"], utc=True)

    df = book.merge(trades, on=["bucket", "instrument_id"], how="inner")
    df["symbol"] = df["instrument_id"].map(ID_TO_SYMBOL)
    return df


def compute_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add strategy features per symbol."""
    results = []
    for iid in df["instrument_id"].unique():
        sub = df[df["instrument_id"] == iid].sort_values("bucket").copy()
        mid = sub["close_mid"]

        # Forward returns at various holding periods
        for n, label in [(1,"5s"),(2,"10s"),(6,"30s"),(12,"60s"),(24,"120s")]:
            sub[f"fwd_{label}"] = mid.pct_change(n).shift(-n)

        # Rolling realized vol (5-min)
        sub["rvol"] = mid.pct_change().rolling(params["vol_lookback"]).std()
        sub["rvol_median"] = sub["rvol"].expanding().median()
        sub["low_vol"] = sub["rvol"] <= sub["rvol_median"]

        # Spread filter
        sp = sub["avg_spread_bps"]
        sub["spread_median"] = sp.rolling(params["spread_lookback"]).median()
        sub["tight_spread"] = sp <= sub["spread_median"]

        # OFI at 5s
        sub["ofi_5s"] = (
            (sub["buy_notional"] - sub["sell_notional"])
            / sub["total_notional"].replace(0, np.nan)
        )

        results.append(sub)
    return pd.concat(results, ignore_index=True)


def generate_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Generate entry signals."""
    df = df.copy()
    imb = df["tob_bid_ratio"]

    base_long = imb > params["threshold_high"]
    base_short = imb < params["threshold_low"]

    if params["vol_filter"]:
        base_long = base_long & df["low_vol"]
        base_short = base_short & df["low_vol"]

    if params["spread_filter"]:
        base_long = base_long & df["tight_spread"]
        base_short = base_short & df["tight_spread"]

    df["signal"] = 0
    df.loc[base_long, "signal"] = 1
    df.loc[base_short, "signal"] = -1
    return df


def run_backtest_vectorized(df: pd.DataFrame, hold_bars: int) -> pd.DataFrame:
    """Vectorized backtest: every signal → trade, allowing overlaps.
    Quick signal quality assessment."""
    hold_label = {1:"5s",2:"10s",6:"30s",12:"60s",24:"120s"}.get(hold_bars, f"{hold_bars*5}s")
    fwd_col = f"fwd_{hold_label}"

    entries = df[df["signal"] != 0].copy()
    if entries.empty:
        return pd.DataFrame()

    entries["direction"] = entries["signal"]
    entries["gross_pnl_bps"] = entries["direction"] * entries[fwd_col] * 1e4
    entries["spread_at_entry"] = entries["avg_spread_bps"]
    return entries


def run_backtest_realistic(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Loop-based backtest: no overlapping trades, proper holding period."""
    hold = params["hold_bars"]
    trades = []

    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].sort_values("bucket").reset_index(drop=True)
        position = 0
        entry_bar = None
        entry_price = None
        entry_spread = None
        entry_time = None

        for i in range(len(sub)):
            if position == 0:
                sig = sub.loc[i, "signal"]
                if sig != 0 and np.isfinite(sub.loc[i, "close_mid"]):
                    position = sig
                    entry_bar = i
                    entry_price = sub.loc[i, "close_mid"]
                    entry_spread = sub.loc[i, "avg_spread_bps"]
                    entry_time = sub.loc[i, "bucket"]
            else:
                bars_held = i - entry_bar
                # Exit conditions: hold period reached, or signal reversal
                sig = sub.loc[i, "signal"]
                reversal = (position == 1 and sig == -1) or (position == -1 and sig == 1)

                if bars_held >= hold or reversal:
                    exit_price = sub.loc[i, "close_mid"]
                    if not np.isfinite(exit_price):
                        continue
                    exit_spread = sub.loc[i, "avg_spread_bps"]
                    gross_bps = (exit_price / entry_price - 1) * 1e4 * position
                    avg_spread = (entry_spread + exit_spread) / 2

                    trades.append({
                        "symbol": sym,
                        "entry_time": entry_time,
                        "exit_time": sub.loc[i, "bucket"],
                        "direction": "LONG" if position == 1 else "SHORT",
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "bars_held": bars_held,
                        "gross_pnl_bps": gross_bps,
                        "avg_spread_bps": avg_spread,
                    })
                    position = 0

    return pd.DataFrame(trades)


def analyze_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Compute P&L stats for each symbol under different cost scenarios."""
    if trades.empty:
        return pd.DataFrame()

    rows = []
    for sym in trades["symbol"].unique():
        t = trades[trades["symbol"] == sym]
        n = len(t)
        gross = t["gross_pnl_bps"]
        avg_spread = t["avg_spread_bps"].mean()

        for scenario, cost_bps in COST_SCENARIOS.items():
            net = gross - cost_bps
            total = net.sum()
            mean = net.mean()
            std = net.std()
            sharpe = mean / std * np.sqrt(252 * 24 * 120) if std > 0 else 0  # annualized (120 trades/hour at 30s)
            win_rate = (net > 0).mean()
            max_dd = (net.cumsum() - net.cumsum().cummax()).min()

            rows.append({
                "symbol": sym, "scenario": scenario, "cost_bps": cost_bps,
                "n_trades": n, "avg_spread_bps": avg_spread,
                "total_pnl_bps": total,
                "mean_pnl_bps": mean,
                "std_pnl_bps": std,
                "sharpe_ann": sharpe,
                "win_rate": win_rate,
                "max_dd_bps": max_dd,
                "trades_per_hour": n / max(1, (t["exit_time"].max() - t["entry_time"].min()).total_seconds() / 3600),
            })
    return pd.DataFrame(rows)


def sensitivity_analysis(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Test different thresholds and holding periods."""
    rows = []
    for thresh_offset in (0.03, 0.05, 0.07, 0.10, 0.15, 0.20):
        for hold_bars in (2, 6, 12, 24):
            p = dict(params)
            p["threshold_high"] = 0.5 + thresh_offset
            p["threshold_low"] = 0.5 - thresh_offset
            p["hold_bars"] = hold_bars

            sig_df = generate_signals(df, p)
            trades = run_backtest_realistic(sig_df, p)
            if trades.empty:
                continue
            for sym in trades["symbol"].unique():
                t = trades[trades["symbol"] == sym]
                gross_mean = t["gross_pnl_bps"].mean()
                net_mean = gross_mean - 5.0  # realistic cost
                rows.append({
                    "symbol": sym,
                    "threshold": thresh_offset,
                    "hold_s": hold_bars * 5,
                    "n_trades": len(t),
                    "gross_mean_bps": gross_mean,
                    "net_mean_bps": net_mean,
                    "win_rate_gross": (t["gross_pnl_bps"] > 0).mean(),
                })
    return pd.DataFrame(rows)


def plot_pnl_curve(trades: pd.DataFrame, cost_bps: float = 5.0) -> None:
    """Cumulative P&L over time."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, sym in zip(axes, trades["symbol"].unique()):
        t = trades[trades["symbol"] == sym].sort_values("exit_time")
        if t.empty:
            continue
        gross_cum = t["gross_pnl_bps"].cumsum()
        net_cum = (t["gross_pnl_bps"] - cost_bps).cumsum()
        ax.plot(t["exit_time"], gross_cum, linewidth=1.5, label="Gross", color="#2ecc71")
        ax.plot(t["exit_time"], net_cum, linewidth=1.5, label=f"Net (-{cost_bps}bps)", color="#e74c3c")
        ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
        ax.set_title(f"{sym} — {len(t)} trades")
        ax.set_ylabel("Cumulative P&L (bps)")
        ax.legend(fontsize=9)
        ax.tick_params(axis="x", rotation=30)
    fig.suptitle("Backtest P&L — Book Imbalance Strategy", fontsize=14)
    plt.tight_layout()
    savefig("backtest_pnl.png")


def plot_sensitivity(sens_df: pd.DataFrame) -> None:
    """Heatmap of net P&L by threshold × holding period."""
    for sym in sens_df["symbol"].unique():
        sub = sens_df[sens_df["symbol"] == sym]
        pivot = sub.pivot_table(index="threshold", columns="hold_s", values="gross_mean_bps")
        if pivot.empty:
            continue
        fig, ax = plt.subplots(figsize=(8, 6))
        vmax = max(1, np.nanmax(np.abs(pivot.values)))
        im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f"{c}s" for c in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f"±{t:.0%}" for t in pivot.index])
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                v = pivot.values[i, j]
                if np.isfinite(v):
                    ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                            color="black", fontsize=10, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Gross mean P&L (bps)")
        ax.set_xlabel("Holding period")
        ax.set_ylabel("Signal threshold (from 0.5)")
        ax.set_title(f"Sensitivity — {sym}")
        plt.tight_layout()
        savefig(f"backtest_sensitivity_{sym}.png")


def plot_by_session(trades: pd.DataFrame) -> None:
    """P&L breakdown by session."""
    if trades.empty:
        return
    trades = trades.copy()
    add_session_column(trades, ts_col="entry_time")
    fig, ax = plt.subplots(figsize=(10, 5))
    symbols = trades["symbol"].unique()
    sessions = ["asian", "european", "us", "overnight"]
    x = np.arange(len(symbols))
    w = 0.2
    colors = {"asian": "#e74c3c", "european": "#3498db", "us": "#2ecc71", "overnight": "#f39c12"}
    for i, session in enumerate(sessions):
        vals = []
        for sym in symbols:
            s = trades[(trades["symbol"] == sym) & (trades["session"] == session)]
            vals.append(s["gross_pnl_bps"].mean() if len(s) > 0 else 0)
        ax.bar(x + i * w, vals, w, label=session, color=colors[session],
               edgecolor="white", linewidth=0.5)
    ax.set_xticks(x + 1.5 * w)
    ax.set_xticklabels(symbols)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Mean gross P&L per trade (bps)")
    ax.set_title("Strategy Performance by Session")
    ax.legend()
    plt.tight_layout()
    savefig("backtest_sessions.png")


def run() -> dict:
    apply_dark_theme()
    print("=" * 70)
    print("BACKTEST — Book Imbalance Strategy")
    print("=" * 70)
    print(f"Params: threshold=±{PARAMS['threshold_high']-0.5:.0%}, "
          f"hold={PARAMS['hold_bars']*5}s, "
          f"vol_filter={PARAMS['vol_filter']}, spread_filter={PARAMS['spread_filter']}")

    print("\nLoading data...")
    raw = load_data()
    print(f"  {len(raw)} merged rows")

    print("\nComputing features...")
    df = compute_features(raw, PARAMS)

    print("\nGenerating signals...")
    df = generate_signals(df, PARAMS)
    n_long = (df["signal"] == 1).sum()
    n_short = (df["signal"] == -1).sum()
    n_flat = (df["signal"] == 0).sum()
    print(f"  Long: {n_long} ({n_long/len(df):.1%}), "
          f"Short: {n_short} ({n_short/len(df):.1%}), "
          f"Flat: {n_flat} ({n_flat/len(df):.1%})")

    print("\n── Realistic Backtest (no overlapping trades) ──")
    trades = run_backtest_realistic(df, PARAMS)
    if trades.empty:
        print("  No trades generated!")
        return {}
    print(f"  {len(trades)} trades")

    stats = analyze_trades(trades)
    print("\n── P&L by Symbol × Cost Scenario ──")
    display_cols = ["symbol", "scenario", "n_trades", "total_pnl_bps",
                    "mean_pnl_bps", "sharpe_ann", "win_rate", "trades_per_hour"]
    print(stats[display_cols].to_string(index=False, float_format="%.2f"))
    stats.to_csv(f"{OUTPUT_DIR}/backtest_stats.csv", index=False)

    print("\n── Sensitivity Analysis ──")
    sens = sensitivity_analysis(df, PARAMS)
    if not sens.empty:
        # Show best parameter combos
        best = sens.nlargest(10, "gross_mean_bps")
        print(best.to_string(index=False, float_format="%.2f"))
        sens.to_csv(f"{OUTPUT_DIR}/backtest_sensitivity.csv", index=False)

    # ── Comparison: with vs without filters ──
    print("\n── Filter Impact ──")
    for label, vf, sf in [("no filters", False, False),
                           ("vol only", True, False),
                           ("spread only", False, True),
                           ("both filters", True, True)]:
        p = dict(PARAMS)
        p["vol_filter"] = vf
        p["spread_filter"] = sf
        sig_df = generate_signals(df, p)
        t = run_backtest_realistic(sig_df, p)
        if t.empty:
            continue
        for sym in t["symbol"].unique():
            ts = t[t["symbol"] == sym]
            print(f"  {label:15s} | {sym} | n={len(ts):5d} | "
                  f"gross={ts['gross_pnl_bps'].mean():+.2f} bps | "
                  f"win={( ts['gross_pnl_bps']>0).mean():.1%}")

    print("\nGenerating plots...")
    plot_pnl_curve(trades)
    plot_sensitivity(sens)
    plot_by_session(trades)

    # ── Verdict ──
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    for sym in trades["symbol"].unique():
        t = trades[trades["symbol"] == sym]
        gross = t["gross_pnl_bps"].mean()
        spread = t["avg_spread_bps"].mean()
        print(f"\n  {sym}:")
        print(f"    Gross edge: {gross:+.2f} bps/trade")
        print(f"    Avg spread: {spread:.2f} bps")
        print(f"    Win rate (gross): {(t['gross_pnl_bps']>0).mean():.1%}")
        print(f"    Trades/hour: {stats[stats['symbol']==sym].iloc[0]['trades_per_hour']:.1f}")
        maker_net = gross - 4.0
        print(f"    Net (maker 4bps): {maker_net:+.2f} bps/trade → ", end="")
        if maker_net > 0:
            yearly = maker_net * stats[stats["symbol"]==sym].iloc[0]["trades_per_hour"] * 24 * 365
            print(f"PROFITABLE (~{yearly:.0f} bps/year)")
        else:
            breakeven = gross
            print(f"need cost < {breakeven:.1f} bps to breakeven")

    return {"trades": trades, "stats": stats, "sensitivity": sens}


if __name__ == "__main__":
    run()
