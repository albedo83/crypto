"""Study 07 — Asia Edge: est-ce que ADA en session asiatique concentre l'edge ?

Hypothèse: le settlement funding à 00:00 UTC (début session Asia) + faible volume ADA
           crée un edge exploitable que le marché met plus longtemps à corriger.

Run: python3 -m analysis.study_07_asia_edge
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


def load_data() -> dict:
    print("  Loading data...")

    price_1m = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket,
               instrument_id,
               last(price, exchange_ts) AS close,
               sum(notional) AS volume,
               count(*) AS trades
        FROM trades_raw
        GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    price_1m["bucket"] = pd.to_datetime(price_1m["bucket"], utc=True)
    price_1m["symbol"] = price_1m["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    price_1m: {len(price_1m)}")

    funding = fetch_df("SELECT * FROM funding ORDER BY instrument_id, exchange_ts")
    funding["exchange_ts"] = pd.to_datetime(funding["exchange_ts"], utc=True)
    funding["symbol"] = funding["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    funding: {len(funding)}")

    oi = fetch_df("SELECT * FROM open_interest ORDER BY instrument_id, exchange_ts")
    oi["exchange_ts"] = pd.to_datetime(oi["exchange_ts"], utc=True)
    oi["symbol"] = oi["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    OI: {len(oi)}")

    basis_1m = fetch_df("""
        SELECT time_bucket('1 minute', exchange_ts) AS bucket,
               instrument_id,
               last(basis_bps, exchange_ts) AS basis_bps,
               last(funding_rate, exchange_ts) AS live_funding
        FROM mark_index
        GROUP BY bucket, instrument_id ORDER BY instrument_id, bucket
    """)
    basis_1m["bucket"] = pd.to_datetime(basis_1m["bucket"], utc=True)
    basis_1m["symbol"] = basis_1m["instrument_id"].map(ID_TO_SYMBOL)
    print(f"    basis_1m: {len(basis_1m)}")

    return {"price_1m": price_1m, "funding": funding, "oi": oi, "basis_1m": basis_1m}


# ═════════════════════════════════════════════════════════════════════
# TEST 1: Funding drift par heure de settlement
# ═════════════════════════════════════════════════════════════════════

def test_funding_by_settlement_hour(price_1m, funding):
    """Le settlement de 00h UTC (Asia) a-t-il plus d'impact que 08h ou 16h ?"""
    rows = []
    for _, f in funding.iterrows():
        ft = f["exchange_ts"]
        iid = f["instrument_id"]
        sym = f["symbol"]
        rate = float(f["funding_rate"])
        settle_hour = ft.hour  # 0, 8, or 16

        p = price_1m[
            (price_1m["instrument_id"] == iid)
            & (price_1m["bucket"] >= ft - pd.Timedelta(hours=3))
            & (price_1m["bucket"] <= ft + pd.Timedelta(hours=3))
        ].sort_values("bucket")

        if len(p) < 20:
            continue

        settle_rows = p[p["bucket"] <= ft]
        if settle_rows.empty:
            continue
        settle_price = settle_rows.iloc[-1]["close"]

        for hours, direction in [(-2, "pre"), (-1, "pre"), (1, "post"), (2, "post")]:
            if direction == "pre":
                t = ft + pd.Timedelta(hours=hours)  # hours is negative
                target = p[p["bucket"] <= t]
            else:
                t = ft + pd.Timedelta(hours=hours)
                target = p[p["bucket"] >= t]
            if target.empty:
                continue

            ref = target.iloc[-1]["close"] if direction == "pre" else target.iloc[0]["close"]
            if direction == "pre":
                drift = (settle_price / ref - 1) * 1e4
            else:
                drift = (ref / settle_price - 1) * 1e4

            # Expected: high funding → price drops pre-settlement, reverts post
            if rate > 0:
                correct = drift < 0 if direction == "pre" else drift > 0
            else:
                correct = drift > 0 if direction == "pre" else drift < 0

            rows.append({
                "symbol": sym, "settle_hour": settle_hour,
                "funding_rate": rate, "abs_funding_bps": abs(rate) * 1e4,
                "period": f"{abs(hours)}h_{direction}",
                "drift_bps": drift, "abs_drift_bps": abs(drift),
                "correct": correct,
            })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# TEST 2: OI divergence par session
# ═════════════════════════════════════════════════════════════════════

def test_oi_divergence_by_session(oi, price_1m):
    """Le signal OI divergence est-il plus fort en session Asia ?"""
    rows = []
    for iid in oi["instrument_id"].unique():
        sym = ID_TO_SYMBOL[iid]
        o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts").copy()
        p = price_1m[price_1m["instrument_id"] == iid].sort_values("bucket")

        o["oi_change_pct"] = o["open_interest"].pct_change() * 100
        o["bucket"] = o["exchange_ts"].dt.floor("5min")

        p5 = p.copy()
        p5["bucket"] = p5["bucket"].dt.floor("5min")
        p5_agg = p5.groupby("bucket").agg(close=("close", "last")).reset_index()

        merged = o.merge(p5_agg, on="bucket", how="inner")
        merged["price_ret_5m"] = merged["close"].pct_change() * 1e4
        merged["hour"] = merged["bucket"].dt.hour
        merged["session"] = merged["hour"].map(
            lambda h: next((n for n, (s, e) in SESSIONS.items() if s <= h < e), "overnight")
        )

        for periods, label in [(12, "60m"), (24, "120m"), (48, "240m")]:
            merged[f"fwd_{label}"] = merged["close"].pct_change(periods).shift(-periods) * 1e4

        for _, r in merged.dropna(subset=["oi_change_pct", "price_ret_5m"]).iterrows():
            price_up = r["price_ret_5m"] > 5
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
                continue  # skip neutral

            for horizon in ("fwd_60m", "fwd_120m", "fwd_240m"):
                if pd.isna(r.get(horizon)):
                    continue
                rows.append({
                    "symbol": sym, "session": r["session"],
                    "regime": regime, "horizon": horizon,
                    "fwd_ret_bps": r[horizon],
                    "hour": r["hour"],
                })

    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# TEST 3: Volume et spread par heure — la liquidité explique-t-elle l'edge ?
# ═════════════════════════════════════════════════════════════════════

def test_liquidity_by_hour(price_1m):
    """Volume et nombre de trades par heure — confirmer que Asia = moins liquide."""
    price_1m = price_1m.copy()
    price_1m["hour"] = price_1m["bucket"].dt.hour
    hourly = price_1m.groupby(["symbol", "hour"]).agg(
        avg_volume=("volume", "mean"),
        avg_trades=("trades", "mean"),
        volatility=("close", lambda x: x.pct_change().std() * 1e4),
    ).reset_index()
    return hourly


# ═════════════════════════════════════════════════════════════════════
# TEST 4: Lead-lag BTC→ADA par session
# ═════════════════════════════════════════════════════════════════════

def test_leadlag_by_session(price_1m):
    """Le lead-lag BTC→ADA est-il plus fort en Asia ?"""
    pivot = price_1m.pivot_table(index="bucket", columns="symbol", values="close")
    pivot = pivot.dropna()
    rets = pivot.pct_change().dropna()
    rets["hour"] = rets.index.hour
    rets["session"] = rets["hour"].map(
        lambda h: next((n for n, (s, e) in SESSIONS.items() if s <= h < e), "overnight")
    )

    rows = []
    for session in list(SESSIONS.keys()) + ["all"]:
        sub = rets if session == "all" else rets[rets["session"] == session]
        if len(sub) < 50:
            continue
        for lag in range(1, 11):
            c = sub["BTCUSDT"].corr(sub["ADAUSDT"].shift(-lag))
            rows.append({"session": session, "lag_min": lag, "corr": c})
    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════
# TEST 5: Backtest ADA-only, Asia-focused
# ═════════════════════════════════════════════════════════════════════

def backtest_ada_asia(price_1m, oi, funding, basis_1m):
    """Backtest focalisé ADA en pondérant plus les signaux en session Asia."""
    iid = 3  # ADAUSDT
    p = price_1m[price_1m["instrument_id"] == iid].sort_values("bucket").copy()
    b = basis_1m[basis_1m["instrument_id"] == iid].sort_values("bucket")
    o = oi[oi["instrument_id"] == iid].sort_values("exchange_ts")
    f = funding[funding["instrument_id"] == iid]

    p = p.merge(b[["bucket", "basis_bps", "live_funding"]], on="bucket", how="left")
    p["basis_bps"] = p["basis_bps"].ffill()
    p["live_funding"] = p["live_funding"].ffill()

    o_r = o.rename(columns={"exchange_ts": "bucket"})[["bucket", "open_interest"]]
    o_r["bucket"] = o_r["bucket"].dt.floor("1min")
    p = p.merge(o_r, on="bucket", how="left")
    p["open_interest"] = p["open_interest"].ffill()

    # Features
    p["hour"] = p["bucket"].dt.hour
    p["session"] = p["hour"].map(
        lambda h: next((n for n, (s, e) in SESSIONS.items() if s <= h < e), "overnight")
    )
    p["oi_change"] = p["open_interest"].pct_change(5) * 100
    p["price_change_5m"] = p["close"].pct_change(5) * 1e4
    p["ret_60m"] = p["close"].pct_change(60).shift(-60) * 1e4
    p["ret_120m"] = p["close"].pct_change(120).shift(-120) * 1e4

    # Funding proximity
    p["next_funding_rate"] = np.nan
    p["min_to_settle"] = np.nan
    for _, fr in f.iterrows():
        ft = fr["exchange_ts"]
        mask = (p["bucket"] >= ft - pd.Timedelta(hours=8)) & (p["bucket"] < ft)
        p.loc[mask, "next_funding_rate"] = float(fr["funding_rate"])
        p.loc[mask, "min_to_settle"] = (ft - p.loc[mask, "bucket"]).dt.total_seconds() / 60

    # Signal: OI divergence
    weak_long = (p["price_change_5m"] > 3) & (p["oi_change"] < -0.03)
    weak_short = (p["price_change_5m"] < -3) & (p["oi_change"] > 0.03)

    p["signal"] = 0.0
    p.loc[weak_long, "signal"] -= 0.4  # fade weak longs
    p.loc[weak_short, "signal"] += 0.4  # fade weak shorts

    # Funding boost near 00:00 UTC settlement
    near_00 = p["min_to_settle"].between(0, 120) & (p["bucket"].dt.hour.isin([22, 23, 0, 1]))
    high_fund = p["next_funding_rate"] > 0.0003
    low_fund = p["next_funding_rate"] < -0.0003
    p.loc[near_00 & high_fund, "signal"] -= 0.5
    p.loc[near_00 & low_fund, "signal"] += 0.5

    # Asia boost: amplify signal during 0-4h UTC
    asia_peak = p["hour"].isin([0, 1, 2, 3])
    p.loc[asia_peak, "signal"] *= 1.5

    # Trade
    trades_60 = []
    trades_120 = []
    position = 0
    entry_bar = 0

    for i in range(len(p)):
        sig = p.iloc[i]["signal"]
        if position == 0:
            if sig > 0.35:
                position = 1
                entry_bar = i
                entry_sig = sig
                entry_session = p.iloc[i]["session"]
                entry_hour = p.iloc[i]["hour"]
            elif sig < -0.35:
                position = -1
                entry_bar = i
                entry_sig = sig
                entry_session = p.iloc[i]["session"]
                entry_hour = p.iloc[i]["hour"]
        else:
            held = i - entry_bar
            if held == 60:
                ret = p.iloc[i]["close"] / p.iloc[entry_bar]["close"]
                gross = (ret - 1) * 1e4 * position
                trades_60.append({
                    "entry_time": str(p.iloc[entry_bar]["bucket"]),
                    "session": entry_session, "hour": entry_hour,
                    "signal": entry_sig, "direction": "LONG" if position == 1 else "SHORT",
                    "gross_bps": gross, "net_bps": gross - 4.0,
                })
            if held == 120:
                ret = p.iloc[i]["close"] / p.iloc[entry_bar]["close"]
                gross = (ret - 1) * 1e4 * position
                trades_120.append({
                    "entry_time": str(p.iloc[entry_bar]["bucket"]),
                    "session": entry_session, "hour": entry_hour,
                    "signal": entry_sig, "direction": "LONG" if position == 1 else "SHORT",
                    "gross_bps": gross, "net_bps": gross - 4.0,
                })
                position = 0

    return pd.DataFrame(trades_60), pd.DataFrame(trades_120)


# ═════════════════════════════════════════════════════════════════════
# PLOTS
# ═════════════════════════════════════════════════════════════════════

def plot_funding_by_settle(fd):
    if fd.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, sym in zip(axes, ["BTCUSDT", "ETHUSDT", "ADAUSDT"]):
        sub = fd[(fd["symbol"] == sym) & (fd["period"] == "2h_pre")]
        if sub.empty:
            continue
        for hour in [0, 8, 16]:
            h = sub[sub["settle_hour"] == hour]
            if h.empty:
                continue
            hit = h["correct"].mean()
            avg = h["abs_drift_bps"].mean()
            ax.bar(f"{hour}h UTC", hit, color="#3fb950" if hit > 0.55 else "#f85149",
                   edgecolor="white", linewidth=0.5)
            ax.text(f"{hour}h UTC", hit + 0.02, f"|{avg:.0f}|bps\nn={len(h)}", ha="center", fontsize=10)
        ax.axhline(0.5, color="white", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_ylim(0, 1)
        ax.set_ylabel("Hit Rate")
        ax.set_title(f"{sym} — Funding Drift by Settlement Hour")
    plt.tight_layout()
    savefig("asia_funding_by_hour.png")


def plot_oi_by_session(oi_df):
    if oi_df.empty:
        return
    ada = oi_df[(oi_df["symbol"] == "ADAUSDT") & (oi_df["horizon"] == "fwd_120m")]
    if ada.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    sessions = ["asian", "european", "us", "overnight"]
    regimes = ["weak_long", "weak_short"]
    colors = {"weak_long": "#f85149", "weak_short": "#3fb950"}
    x = np.arange(len(sessions))
    w = 0.3
    for i, regime in enumerate(regimes):
        vals = []
        counts = []
        for session in sessions:
            s = ada[(ada["session"] == session) & (ada["regime"] == regime)]
            vals.append(s["fwd_ret_bps"].mean() if len(s) > 3 else 0)
            counts.append(len(s))
        ax.bar(x + (i - 0.5) * w, vals, w, label=regime, color=colors[regime],
               edgecolor="white", linewidth=0.5)
        for j, (v, n) in enumerate(zip(vals, counts)):
            if n > 0:
                ax.text(x[j] + (i - 0.5) * w, v + (2 if v >= 0 else -5),
                        f"n={n}", ha="center", fontsize=9, color="white")
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(sessions)
    ax.set_ylabel("Mean 2h Forward Return (bps)")
    ax.set_title("ADA OI Divergence by Session (2h horizon)")
    ax.legend()
    plt.tight_layout()
    savefig("asia_oi_by_session.png")


def plot_liquidity_hours(hourly):
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, (col, label) in zip(axes, [("avg_volume", "Avg Notional/min ($)"),
                                         ("avg_trades", "Avg Trades/min"),
                                         ("volatility", "Volatility (bps/min)")]):
        for sym in hourly["symbol"].unique():
            s = hourly[hourly["symbol"] == sym].sort_values("hour")
            ax.plot(s["hour"], s[col], marker="o", markersize=4, linewidth=1.5, label=sym)
        for h in (4, 8, 14, 21):
            ax.axvline(h, color="yellow", linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_xlabel("UTC Hour")
        ax.set_ylabel(label)
        ax.legend(fontsize=8)
    axes[0].set_title("Volume par heure")
    axes[1].set_title("Activité par heure")
    axes[2].set_title("Volatilité par heure")
    fig.suptitle("Liquidité 24h — zones jaunes = frontières de session", fontsize=13)
    plt.tight_layout()
    savefig("asia_liquidity_24h.png")


def plot_leadlag_sessions(ll):
    if ll.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    colors = {"asian": "#e74c3c", "european": "#3498db", "us": "#2ecc71",
              "overnight": "#f39c12", "all": "white"}
    for session in ["asian", "european", "us", "overnight", "all"]:
        sub = ll[ll["session"] == session]
        if sub.empty:
            continue
        ax.plot(sub["lag_min"], sub["corr"], marker="o", markersize=4,
                linewidth=2 if session == "asian" else 1,
                color=colors.get(session, "white"), label=session,
                alpha=1.0 if session in ("asian", "all") else 0.5)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("Lag (minutes)")
    ax.set_ylabel("Correlation BTC→ADA")
    ax.set_title("Lead-Lag BTC→ADA par Session")
    ax.legend()
    plt.tight_layout()
    savefig("asia_leadlag_sessions.png")


def plot_backtest_by_session(trades, horizon_label):
    if trades.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # By session
    ax = axes[0]
    sessions = ["asian", "european", "us", "overnight"]
    gross_vals = []
    net_vals = []
    counts = []
    for session in sessions:
        t = trades[trades["session"] == session]
        gross_vals.append(t["gross_bps"].mean() if len(t) > 0 else 0)
        net_vals.append(t["net_bps"].mean() if len(t) > 0 else 0)
        counts.append(len(t))
    x = np.arange(len(sessions))
    ax.bar(x - 0.15, gross_vals, 0.3, label="Gross", color="#3fb950")
    ax.bar(x + 0.15, net_vals, 0.3, label="Net", color="#f85149")
    for i, n in enumerate(counts):
        ax.text(i, max(gross_vals[i], net_vals[i]) + 1, f"n={n}", ha="center", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(sessions)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_ylabel("Mean P&L per trade (bps)")
    ax.set_title(f"ADA {horizon_label} — Performance by Session")
    ax.legend()

    # By hour
    ax = axes[1]
    hourly = trades.groupby("hour").agg(
        net_mean=("net_bps", "mean"), n=("net_bps", "count")
    ).reset_index()
    colors = ["#e74c3c" if h < 8 else "#3498db" if h < 14 else "#2ecc71" if h < 21 else "#f39c12"
              for h in hourly["hour"]]
    ax.bar(hourly["hour"], hourly["net_mean"], color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="white", linewidth=0.5, alpha=0.5)
    ax.set_xlabel("UTC Hour")
    ax.set_ylabel("Mean Net P&L (bps)")
    ax.set_title(f"ADA {horizon_label} — P&L by Hour")
    for h in (8, 14, 21):
        ax.axvline(h - 0.5, color="yellow", linewidth=0.5, linestyle="--", alpha=0.5)
    plt.tight_layout()
    savefig(f"asia_backtest_{horizon_label}.png")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def run():
    apply_dark_theme()
    print("=" * 70)
    print("STUDY 07 — Asia Edge Deep Dive")
    print("=" * 70)

    data = load_data()

    # ── Test 1: Funding par heure de settlement ──────────────────
    print("\n── 1. Funding Drift par Settlement Hour ──")
    fd = test_funding_by_settlement_hour(data["price_1m"], data["funding"])
    if not fd.empty:
        pre = fd[fd["period"] == "2h_pre"]
        for sym in ["BTCUSDT", "ETHUSDT", "ADAUSDT"]:
            print(f"  {sym}:")
            for hour in [0, 8, 16]:
                h = pre[(pre["symbol"] == sym) & (pre["settle_hour"] == hour)]
                if h.empty:
                    continue
                hit = h["correct"].mean()
                avg = h["abs_drift_bps"].mean()
                print(f"    {hour:02d}h UTC: hit={hit:.0%} avg|drift|={avg:.0f}bps (n={len(h)})")
        fd.to_csv(f"{OUTPUT_DIR}/asia_funding.csv", index=False)

    # ── Test 2: OI divergence par session ────────────────────────
    print("\n── 2. OI Divergence par Session ──")
    oi_df = test_oi_divergence_by_session(data["oi"], data["price_1m"])
    if not oi_df.empty:
        ada = oi_df[oi_df["symbol"] == "ADAUSDT"]
        for horizon in ("fwd_60m", "fwd_120m"):
            print(f"  ADA {horizon}:")
            for session in ["asian", "european", "us", "overnight"]:
                for regime in ["weak_long", "weak_short"]:
                    s = ada[(ada["session"] == session) & (ada["horizon"] == horizon)
                            & (ada["regime"] == regime)]
                    if len(s) > 3:
                        print(f"    {session:10s} {regime:12s}: {s['fwd_ret_bps'].mean():+.1f} bps (n={len(s)})")
        oi_df.to_csv(f"{OUTPUT_DIR}/asia_oi_sessions.csv", index=False)

    # ── Test 3: Liquidité par heure ──────────────────────────────
    print("\n── 3. Liquidité par Heure ──")
    hourly = test_liquidity_by_hour(data["price_1m"])
    ada_h = hourly[hourly["symbol"] == "ADAUSDT"].sort_values("hour")
    min_vol_hour = ada_h.loc[ada_h["avg_volume"].idxmin(), "hour"]
    max_vol_hour = ada_h.loc[ada_h["avg_volume"].idxmax(), "hour"]
    print(f"  ADA: min volume h={min_vol_hour:.0f} UTC, max volume h={max_vol_hour:.0f} UTC")
    print(f"  Ratio max/min: {ada_h['avg_volume'].max() / ada_h['avg_volume'].min():.1f}x")
    hourly.to_csv(f"{OUTPUT_DIR}/asia_liquidity.csv", index=False)

    # ── Test 4: Lead-lag par session ─────────────────────────────
    print("\n── 4. Lead-Lag BTC→ADA par Session ──")
    ll = test_leadlag_by_session(data["price_1m"])
    if not ll.empty:
        for session in ["asian", "european", "us", "overnight"]:
            peak = ll[(ll["session"] == session)].nlargest(1, "corr")
            if not peak.empty:
                print(f"  {session:10s}: peak lag={peak.iloc[0]['lag_min']:.0f}min corr={peak.iloc[0]['corr']:.4f}")

    # ── Test 5: Backtest ADA Asia-focused ────────────────────────
    print("\n── 5. Backtest ADA Asia-Focused ──")
    t60, t120 = backtest_ada_asia(data["price_1m"], data["oi"], data["funding"], data["basis_1m"])

    for trades, label in [(t60, "60min"), (t120, "120min")]:
        if trades.empty:
            continue
        print(f"\n  Horizon {label}:")
        print(f"    Total: {len(trades)} trades | "
              f"gross {trades['gross_bps'].mean():+.1f} | "
              f"net {trades['net_bps'].mean():+.1f} bps/trade | "
              f"win {(trades['gross_bps']>0).mean():.0%} | "
              f"total net {trades['net_bps'].sum():+.0f} bps")

        # By session
        for session in ["asian", "european", "us", "overnight"]:
            t = trades[trades["session"] == session]
            if len(t) < 3:
                continue
            print(f"    {session:10s}: {len(t)} trades | "
                  f"gross {t['gross_bps'].mean():+.1f} | "
                  f"net {t['net_bps'].mean():+.1f} | "
                  f"win {(t['gross_bps']>0).mean():.0%}")

        trades.to_csv(f"{OUTPUT_DIR}/asia_backtest_{label}.csv", index=False)

    # ── Plots ────────────────────────────────────────────────────
    print("\nGenerating plots...")
    plot_funding_by_settle(fd)
    plot_oi_by_session(oi_df)
    plot_liquidity_hours(hourly)
    plot_leadlag_sessions(ll)
    if not t60.empty:
        plot_backtest_by_session(t60, "60min")
    if not t120.empty:
        plot_backtest_by_session(t120, "120min")

    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)

    return {"funding": fd, "oi_sessions": oi_df, "liquidity": hourly,
            "leadlag": ll, "trades_60": t60, "trades_120": t120}


if __name__ == "__main__":
    run()
