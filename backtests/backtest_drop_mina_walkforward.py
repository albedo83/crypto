"""Walk-forward strict for dropping MINA from the trading universe.

4 sliding 6-month OOS windows, non-overlapping, covering 2024-06-01 → 2026-06-01.
Each window starts fresh at $500 capital (no compounding bias across splits).

PASS criterion (strict 4/4): no_mina must beat baseline on ALL 4 windows for ROI,
AND DD not worse by >2pp on ALL 4. Anything less = FAIL → keep MINA.

Usage: python3 -m backtests.backtest_drop_mina_walkforward
"""
from __future__ import annotations

from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import backtests.backtest_genetic as bg
import analysis.bot.config as bc


SPLITS = [
    ("split_1  2024-06 → 2024-12", datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    ("split_2  2024-12 → 2025-06", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    ("split_3  2025-06 → 2025-12", datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    ("split_4  2025-12 → 2026-06", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]
START_CAP = 500.0
DD_TOLERANCE_PP = 2.0  # no_mina DD may worsen by up to 2pp and still PASS


def reload_data():
    from backtests.backtest_rolling import load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    return data, features, sector_feats, load_dxy(), load_oi(), load_funding()


def run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding):
    from backtests.backtest_rolling import run_window
    return run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start_dt.timestamp() * 1000),
        end_ts_ms=int(end_dt.timestamp() * 1000),
        start_capital=START_CAP,
        oi_data=oi, funding_data=funding,
    )


def mina_attribution(trades):
    rows = [t for t in trades if t.get("coin") == "MINA"]
    return len(rows), sum(t.get("pnl", 0.0) for t in rows)


def main():
    # --- BASELINE (with MINA) ---
    saved_tokens = list(bg.TOKENS)
    saved_s10 = set(bc.S10_ALLOWED_TOKENS)
    saved_meme = list(bc.SECTORS.get("Meme", []))

    print("=" * 84)
    print("BASELINE: TOKENS includes MINA (35 tokens)")
    print("=" * 84)
    data, features, sector_feats, dxy, oi, funding = reload_data()
    baseline = {}
    for name, start_dt, end_dt in SPLITS:
        print(f"\n-- {name} --", flush=True)
        r = run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding)
        mn, mp = mina_attribution(r["trades"])
        baseline[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                          "mina_n": mn, "mina_pnl": mp, "best": r["best_strat"]}
        print(f"   ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  n={r['n_trades']:4d}  best={r['best_strat']}  | MINA: {mn} trades / ${mp:+,.2f}", flush=True)

    # --- NO_MINA ---
    bg.TOKENS = [t for t in saved_tokens if t != "MINA"]
    bc.S10_ALLOWED_TOKENS = {t for t in saved_s10 if t != "MINA"}
    bc.SECTORS = dict(bc.SECTORS)
    bc.SECTORS["Meme"] = [t for t in saved_meme if t != "MINA"]
    bc.TOKEN_SECTOR = {t: s for s, toks in bc.SECTORS.items() for t in toks}

    print()
    print("=" * 84)
    print("NO_MINA: TOKENS = 34 (MINA removed from TRADE_SYMBOLS + S10_ALLOWED + Meme sector)")
    print("=" * 84)
    data, features, sector_feats, dxy, oi, funding = reload_data()
    nomina = {}
    for name, start_dt, end_dt in SPLITS:
        print(f"\n-- {name} --", flush=True)
        r = run_split(start_dt, end_dt, features, data, sector_feats, dxy, oi, funding)
        nomina[name] = {"roi": r["pnl_pct"], "dd": r["max_dd_pct"], "n": r["n_trades"],
                        "best": r["best_strat"]}
        print(f"   ROI={r['pnl_pct']:+8.1f}%  DD={r['max_dd_pct']:6.1f}%  n={r['n_trades']:4d}  best={r['best_strat']}", flush=True)

    # restore
    bg.TOKENS = saved_tokens
    bc.S10_ALLOWED_TOKENS = saved_s10
    bc.SECTORS["Meme"] = saved_meme
    bc.TOKEN_SECTOR = {t: s for s, toks in bc.SECTORS.items() for t in toks}

    # --- VERDICT ---
    print()
    print("=" * 84)
    print(f"WALK-FORWARD STRICT VERDICT (4/4 required for PASS, DD tolerance ±{DD_TOLERANCE_PP}pp)")
    print("=" * 84)
    print(f"{'Split':<32} {'BL ROI':>10} {'NM ROI':>10} {'ΔROI':>10} {'ΔDD':>9} {'MINA $':>10} {'PASS?':>7}")

    roi_pass = 0
    dd_pass = 0
    for name, _, _ in SPLITS:
        b = baseline[name]
        n = nomina[name]
        d_roi = n["roi"] - b["roi"]
        d_dd = n["dd"] - b["dd"]
        roi_ok = d_roi > 0
        dd_ok = d_dd >= -DD_TOLERANCE_PP
        if roi_ok: roi_pass += 1
        if dd_ok: dd_pass += 1
        verdict = "✓" if (roi_ok and dd_ok) else "✗"
        print(f"{name:<32} {b['roi']:>+9.1f}% {n['roi']:>+9.1f}% {d_roi:>+9.2f}pp {d_dd:>+8.2f}pp {b['mina_pnl']:>+9.2f} {verdict:>7}")

    print()
    print(f"ROI strict: {roi_pass}/4 splits where dropping MINA helped")
    print(f"DD strict:  {dd_pass}/4 splits where DD didn't worsen by >{DD_TOLERANCE_PP}pp")
    overall = "PASS — drop MINA" if (roi_pass == 4 and dd_pass == 4) else "FAIL — keep MINA"
    print(f"\nOVERALL: {overall}")


if __name__ == "__main__":
    main()
