"""Validate oi_align_long gate — sweep thresholds and combine with other gates."""
from __future__ import annotations

from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import VERSION
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_external_gates import (
    run_window, load_funding, load_oi, fmt_result,
    gate_oi_align_long, gate_funding_dir,
)


def combined_oi_funding(ctx):
    """Stack oi_align_long + funding_dir."""
    a = ctx["args"]
    d = ctx["oi_delta_24h"]
    f = ctx["funding"]
    if d is not None and ctx["dir"] == 1 and d < -a["oi_th"]:
        return True
    if f is not None:
        if ctx["dir"] == 1 and f > a["fund_th"]:
            return True
        if ctx["dir"] == -1 and f < -a["fund_th"]:
            return True
    return False


def main():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    funding_data = load_funding()
    oi_data = load_oi()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data ends {end_dt.isoformat()}")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m",  end_dt - relativedelta(months=6)),
        ("3m",  end_dt - relativedelta(months=3)),
    ]

    baselines = {}
    for label, start_dt in windows:
        r = run_window(features, data, sector_features, funding_data, oi_data,
                       int(start_dt.timestamp() * 1000), latest_ts)
        baselines[label] = r

    print(f"\n{'='*120}")
    print(f"OI gate validation — v{VERSION}")
    print(f"{'='*120}\n")

    print(f"{'Config':40} {'28m':>12} {'12m':>10} {'6m':>10} {'3m':>10}  (Δ vs baseline)")
    for lb in ("28m", "12m", "6m", "3m"):
        b = baselines[lb]
        print(f"{'baseline':40} ${b['pnl']:>10.0f} "
              + f"${b['pnl']:>8.0f} " * 0, end="")
    print()

    # Sweep oi_align_long thresholds
    print("\n── oi_align_long sweep (skip LONG if Δ(OI,24h) < -th) ──")
    for th in [500, 700, 800, 900, 1000, 1100, 1200, 1500, 2000]:
        deltas = []
        wins = 0
        for lb, start_dt in windows:
            r = run_window(features, data, sector_features, funding_data, oi_data,
                           int(start_dt.timestamp() * 1000), latest_ts,
                           gate_fn=gate_oi_align_long, gate_args={"th": th})
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins >= 3 else " ")
        dstr = " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp" for lb, dp, dd, _ in deltas)
        skips = " ".join(f"{lb}:{r['skipped']}" for lb, _, _, r in deltas)
        print(f"  {status} th={th:>4}: {dstr}  | skips {skips}")

    # Sweep funding_dir thresholds
    print("\n── funding_dir sweep (skip LONG if funding > +th, SHORT if < -th) ──")
    for th in [0.5, 1, 1.5, 2, 3, 5]:
        deltas = []
        wins = 0
        for lb, start_dt in windows:
            r = run_window(features, data, sector_features, funding_data, oi_data,
                           int(start_dt.timestamp() * 1000), latest_ts,
                           gate_fn=gate_funding_dir, gate_args={"th": th})
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins >= 3 else " ")
        dstr = " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp" for lb, dp, dd, _ in deltas)
        skips = " ".join(f"{lb}:{r['skipped']}" for lb, _, _, r in deltas)
        print(f"  {status} th={th:>4}: {dstr}  | skips {skips}")

    # Combined: oi_align_long + funding_dir
    print("\n── Combined oi_align_long + funding_dir ──")
    combos = [
        {"oi_th": 800,  "fund_th": 1.0},
        {"oi_th": 1000, "fund_th": 1.0},
        {"oi_th": 1000, "fund_th": 1.5},
        {"oi_th": 1000, "fund_th": 2.0},
        {"oi_th": 1000, "fund_th": 3.0},
        {"oi_th": 1200, "fund_th": 2.0},
    ]
    for args in combos:
        deltas = []
        wins = 0
        for lb, start_dt in windows:
            r = run_window(features, data, sector_features, funding_data, oi_data,
                           int(start_dt.timestamp() * 1000), latest_ts,
                           gate_fn=combined_oi_funding, gate_args=args)
            dp = r["pnl"] - baselines[lb]["pnl"]
            dd = r["max_dd_pct"] - baselines[lb]["max_dd_pct"]
            deltas.append((lb, dp, dd, r))
            if dp > 0:
                wins += 1
        status = "✓" if wins == 4 else ("≈" if wins >= 3 else " ")
        dstr = " ".join(f"{lb}:{dp:+7.0f}/{dd:+.1f}pp" for lb, dp, dd, _ in deltas)
        skips = " ".join(f"{lb}:{r['skipped']}" for lb, _, _, r in deltas)
        print(f"  {status} oi={args['oi_th']} fund={args['fund_th']}: "
              f"{dstr}  | skips {skips}")

    # Per-strategy breakdown for oi_align_long th=1000
    print("\n── Per-strategy impact of oi_align_long th=1000 ──")
    for lb, start_dt in windows:
        base = baselines[lb]
        r = run_window(features, data, sector_features, funding_data, oi_data,
                       int(start_dt.timestamp() * 1000), latest_ts,
                       gate_fn=gate_oi_align_long, gate_args={"th": 1000})
        print(f"\n  {lb}:")
        print(f"    {'strat':6} {'baseline':>20} {'with gate':>20} {'Δ':>15}")
        strats = sorted(set(list(base["by_strat"].keys()) + list(r["by_strat"].keys())))
        for s in strats:
            b = base["by_strat"].get(s, {"n": 0, "pnl": 0, "wr": 0})
            g = r["by_strat"].get(s, {"n": 0, "pnl": 0, "wr": 0})
            print(f"    {s:6} n={b['n']:>3} wr={b['wr']:>2}% ${b['pnl']:>+7.0f} "
                  f"→ n={g['n']:>3} wr={g['wr']:>2}% ${g['pnl']:>+7.0f} "
                  f"Δ$ {g['pnl']-b['pnl']:>+7.0f}")


if __name__ == "__main__":
    main()
