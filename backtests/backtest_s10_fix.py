"""Validate S10 improvements in walk-forward.

Tests 4 hypotheses based on backtest_s10_diag findings:
    H1: skip S10 LONG (keep SHORT-only fade)
    H2: skip S10 on losing tokens (p-hacking candidate — listed from 28m IS)
    H3: H1 + H2 combined
    H4: disable S10 entirely (sanity baseline)

A hypothesis survives only if it improves P&L on ALL 4 windows (28m/12m/6m/3m).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window

OI_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")

# Tokens with net positive P&L on S10 (from backtest_s10_diag over 28m window)
S10_KEEP_TOKENS = {
    "IMX", "SNX", "ARB", "OP", "GALA", "SUI", "LINK", "AAVE", "AVAX", "MINA",
    "SAND", "GMX", "COMP", "CRV",
}
# Anything not in this list is losing: STX MKR SEI PENDLE LDO APT WLD DOGE NEAR
# BLUR PYTH SOL INJ


def gate_h1(sym, ts, strat, direction):
    """Skip S10 LONG (keep SHORT-only)."""
    return strat == "S10" and direction == 1


def gate_h2(sym, ts, strat, direction):
    """Skip S10 on losing tokens."""
    return strat == "S10" and sym not in S10_KEEP_TOKENS


def gate_h3(sym, ts, strat, direction):
    """H1 + H2: SHORT-only on kept tokens."""
    return strat == "S10" and (direction == 1 or sym not in S10_KEEP_TOKENS)


def gate_h4(sym, ts, strat, direction):
    """Disable S10 entirely (sanity baseline)."""
    return strat == "S10"


def fmt_dollar(v: float) -> str:
    return f"${v:>8,.0f}".replace(",", " ")


def main() -> int:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)

    latest_ts = max(c["t"] for c in data["BTC"])
    db = sqlite3.connect(OI_DB) if os.path.exists(OI_DB) else None
    if db:
        oi_last = db.execute("SELECT MAX(ts) FROM asset_ctx").fetchone()[0]
        end_dt = min(
            datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc),
            datetime.fromtimestamp(oi_last, tz=timezone.utc),
        )
    else:
        end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    end_ts = int(end_dt.timestamp() * 1000)

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]

    print(f"End capped to {end_dt.date()}\n")
    print("Baseline:")
    baselines = {}
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window(features, data, sector_features, {}, start_ts, end_ts)
        baselines[label] = r
        print(f"  {label:<5} {fmt_dollar(r['end_capital'])} "
              f"({r['pnl_pct']:+6.0f}%) DD {r['max_dd_pct']:+5.1f}% n={r['n_trades']}")

    hypotheses = [
        ("H1 S10 SHORT-only", gate_h1),
        ("H2 S10 good-tokens", gate_h2),
        ("H3 SHORT+good-tok", gate_h3),
        ("H4 S10 disabled", gate_h4),
    ]

    print(f"\n{'='*78}")
    print(f"{'Hypothesis':<22} {'window':<6} {'Δ$':>12} {'Δ%':>8} "
          f"{'Δ DD':>7} {'Δ n':>6} {'verdict'}")
    print(f"{'='*78}")

    for h_name, h_fn in hypotheses:
        all_positive = True
        rows = []
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, {}, start_ts, end_ts,
                           skip_fn=h_fn)
            base = baselines[label]
            d_dol = r["end_capital"] - base["end_capital"]
            d_pct = r["pnl_pct"] - base["pnl_pct"]
            d_dd = r["max_dd_pct"] - base["max_dd_pct"]
            d_n = r["n_trades"] - base["n_trades"]
            rows.append((label, d_dol, d_pct, d_dd, d_n))
            if d_dol < 0:
                all_positive = False
        verdict = "✓ SURVIVES" if all_positive else "✗ rejects"
        for i, (label, d_dol, d_pct, d_dd, d_n) in enumerate(rows):
            name_col = h_name if i == 0 else ""
            v_col = verdict if i == 0 else ""
            sign = "+" if d_dol >= 0 else ""
            print(f"{name_col:<22} {label:<6} {sign}{fmt_dollar(d_dol):>11} "
                  f"{d_pct:>+6.0f}% {d_dd:>+6.1f} {d_n:>+6} {v_col}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
