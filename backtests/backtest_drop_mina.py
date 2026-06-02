"""One-shot: measure the opportunity cost of removing MINA from the universe.

Runs 4 anchored windows on $500 capital, twice each:
  baseline  = current TOKENS (with MINA)
  no_mina   = TOKENS minus MINA + MINA removed from S10_ALLOWED + Meme sector

Reports per-window ΔPnL, ΔDD, ΔTrades and the MINA-attributable subset.

Usage: python3 -m backtests.backtest_drop_mina
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

# Monkey-patch BEFORE backtest_rolling imports anything that snapshots TOKENS
import backtests.backtest_genetic as bg
import analysis.bot.config as bc


def windows(end_dt: datetime) -> list[tuple[str, datetime]]:
    return [
        ("28 mois", end_dt - relativedelta(months=28)),
        ("12 mois", end_dt - relativedelta(months=12)),
        ("6 mois",  end_dt - relativedelta(months=6)),
        ("3 mois",  end_dt - relativedelta(months=3)),
    ]


def run_one_window(label, start_dt, end_dt, features, data, sector_feats, dxy, oi, funding):
    from backtests.backtest_rolling import run_window
    res = run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=500.0,
        oi_data=oi, funding_data=funding,
    )
    return res


def reload_data():
    """Reload everything from disk so we don't carry stale features when TOKENS changes."""
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    from backtests.backtest_sector import compute_sector_features
    sector_feats = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()
    return data, features, sector_feats, dxy, oi, funding


def trades_breakdown(trades, sym):
    rows = [t for t in trades if t.get("coin") == sym]
    pnl = sum(t.get("pnl", 0.0) for t in rows)
    return len(rows), pnl


def main():
    end_dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
    wins = windows(end_dt)

    # --- baseline (with MINA) ---
    print("=" * 78)
    print("BASELINE  TOKENS = current (35 including MINA)")
    print("=" * 78)
    saved_tokens = list(bg.TOKENS)
    saved_s10 = set(bc.S10_ALLOWED_TOKENS)
    saved_meme = list(bc.SECTORS.get("Meme", []))

    data, features, sector_feats, dxy, oi, funding = reload_data()

    baseline_results = {}
    for label, start_dt in wins:
        print(f"\n-- {label} ({start_dt.date()} → {end_dt.date()}) baseline --", flush=True)
        r = run_one_window(label, start_dt, end_dt, features, data, sector_feats, dxy, oi, funding)
        mina_n, mina_pnl = trades_breakdown(r["trades"], "MINA")
        baseline_results[label] = {
            "end_cap": r["end_capital"], "pnl_pct": r["pnl_pct"], "dd": r["max_dd_pct"],
            "n": r["n_trades"], "mina_n": mina_n, "mina_pnl": mina_pnl,
        }
        print(f"   bal=${r['end_capital']:,.0f}  ROI={r['pnl_pct']:.1f}%  DD={r['max_dd_pct']:.1f}%  trades={r['n_trades']}  MINA={mina_n} trades / ${mina_pnl:,.2f}", flush=True)

    # --- no_mina ---
    print()
    print("=" * 78)
    print("NO_MINA   TOKENS = current minus MINA (34)")
    print("=" * 78)
    bg.TOKENS = [t for t in saved_tokens if t != "MINA"]
    bc.S10_ALLOWED_TOKENS = {t for t in saved_s10 if t != "MINA"}
    bc.SECTORS = dict(bc.SECTORS)
    bc.SECTORS["Meme"] = [t for t in saved_meme if t != "MINA"]
    bc.TOKEN_SECTOR = {t: s for s, toks in bc.SECTORS.items() for t in toks}

    data, features, sector_feats, dxy, oi, funding = reload_data()

    nomina_results = {}
    for label, start_dt in wins:
        print(f"\n-- {label} ({start_dt.date()} → {end_dt.date()}) no_mina --", flush=True)
        r = run_one_window(label, start_dt, end_dt, features, data, sector_feats, dxy, oi, funding)
        nomina_results[label] = {
            "end_cap": r["end_capital"], "pnl_pct": r["pnl_pct"], "dd": r["max_dd_pct"],
            "n": r["n_trades"],
        }
        print(f"   bal=${r['end_capital']:,.0f}  ROI={r['pnl_pct']:.1f}%  DD={r['max_dd_pct']:.1f}%  trades={r['n_trades']}", flush=True)

    # restore
    bg.TOKENS = saved_tokens
    bc.S10_ALLOWED_TOKENS = saved_s10
    bc.SECTORS["Meme"] = saved_meme
    bc.TOKEN_SECTOR = {t: s for s, toks in bc.SECTORS.items() for t in toks}

    # --- summary ---
    print()
    print("=" * 78)
    print("SUMMARY  Δ = no_mina − baseline (positive Δ = dropping MINA helped)")
    print("=" * 78)
    print(f"{'Window':<10} {'Baseline ROI':>14} {'NoMINA ROI':>14} {'ΔROI%':>10} {'ΔDD%':>10} {'ΔTrades':>10} {'MINA n':>8} {'MINA $':>10}")
    for label, _ in wins:
        b = baseline_results[label]
        n = nomina_results[label]
        d_roi = n["pnl_pct"] - b["pnl_pct"]
        d_dd = n["dd"] - b["dd"]
        d_n = n["n"] - b["n"]
        print(f"{label:<10} {b['pnl_pct']:>13.1f}% {n['pnl_pct']:>13.1f}% {d_roi:>+9.2f}pp {d_dd:>+9.2f}pp {d_n:>+10d} {b['mina_n']:>8d} {b['mina_pnl']:>+9.2f}")


if __name__ == "__main__":
    main()
