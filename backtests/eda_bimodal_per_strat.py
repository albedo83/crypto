"""EDA: classifier les trades par strat selon les 4 catégories MFE/MAE/final.

Pour chaque strat : combien de bimodal winners (le trail tue) vs giveback losers (le trail sauve) ?
Le ratio kill/save dicte si la prop_trail vaut le coup.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict
import backtests.backtest_genetic as bg


def main():
    from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()

    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    r = run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start.timestamp() * 1000),
        end_ts_ms=int(end.timestamp() * 1000),
        start_capital=500.0, oi_data=oi, funding_data=funding,
    )

    by_strat = defaultdict(list)
    for t in r["trades"]:
        by_strat[t.get("strat", "?")].append(t)

    print(f"{'Strat':>6} {'Total':>6} {'PureWin':>8} {'BimoWin':>8} {'PureLos':>8} {'Giveback':>9}  {'Kill/Save':>10}  {'$ at stake':>13}")
    print("-" * 110)
    for strat in sorted(by_strat):
        trades = by_strat[strat]
        pure_win = bimo_win = pure_los = giveback = 0
        pw_d = bw_d = pl_d = gb_d = 0.0
        bw_pnl_total = 0.0
        gb_pnl_total = 0.0
        for t in trades:
            mfe = t.get("mfe_bps", 0.0)
            mae = t.get("mae_bps", 0.0)
            net = t.get("net", 0.0)
            pnl = t.get("pnl", 0.0)
            if net > 0:
                if mae <= -300:
                    bimo_win += 1; bw_d += pnl; bw_pnl_total += pnl
                else:
                    pure_win += 1; pw_d += pnl
            else:
                if mfe >= 300:
                    giveback += 1; gb_d += pnl; gb_pnl_total += pnl
                else:
                    pure_los += 1; pl_d += pnl
        kill_save = bimo_win / max(1, giveback)
        # $ at stake = bimodal winners we might lose vs giveback losers we might save
        dollar_at_stake = abs(gb_pnl_total) - bw_pnl_total
        print(f"{strat:>6} {len(trades):>6} {pure_win:>4} ${pw_d:>+5,.0f} {bimo_win:>4} ${bw_d:>+5,.0f} {pure_los:>4} ${pl_d:>+5,.0f} {giveback:>4} ${gb_d:>+5,.0f}  {kill_save:>10.2f}  ${dollar_at_stake:>+11,.0f}")

    print()
    print("Reading:")
    print("  - Kill/Save < 0.5: trail likely helps (many givebacks vs few bimodal winners)")
    print("  - Kill/Save > 1.0: trail likely hurts (more bimodal winners at risk)")
    print("  - $ at stake = potential save (giveback_loser $ absolute) - potential harm (bimodal_winner $)")
    print("    Positive = trail mathematically promising. Negative = forget it.")


if __name__ == "__main__":
    main()
