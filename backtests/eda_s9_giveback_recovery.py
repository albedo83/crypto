"""EDA: combien de S9 winners ont fait un MFE puis un drawdown puis un rebond final ?

Pour chaque S9 trade :
  - mfe_bps  = peak gain pendant le hold
  - mae_bps  = pire perte pendant le hold (négatif)
  - final_net_bps = résultat à l'exit

On catégorise :
  - Pure winner       : mae_bps > -300  AND final_net_bps > 0  → jamais en danger
  - Bimodal winner    : mae_bps <= -300 AND final_net_bps > 0  → "dip then recover"
  - Pure loser        : mfe_bps <  300  AND final_net_bps < 0  → jamais espoir
  - Giveback loser    : mfe_bps >= 300  AND final_net_bps < 0  → "rise then fall" (WLD)

Output: tableau de fréquences + exemples concrets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from backtests.backtest_sector import compute_sector_features
import backtests.backtest_genetic as bg


def main():
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()

    # 24 months
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    r = run_window(
        features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
        start_ts_ms=int(start.timestamp() * 1000),
        end_ts_ms=int(end.timestamp() * 1000),
        start_capital=500.0, oi_data=oi, funding_data=funding,
    )

    s9 = [t for t in r["trades"] if t.get("strat") == "S9"]
    print(f"Total S9 trades over 24 months: {len(s9)}")
    print()

    # Categorize
    pure_winner = []        # safe winners
    bimodal_winner = []     # dip then recover
    pure_loser = []
    giveback_loser = []     # rise then fall (WLD pattern)
    for t in s9:
        mfe = t.get("mfe_bps", 0.0)
        mae = t.get("mae_bps", 0.0)
        net = t.get("net", 0.0)
        if net > 0:
            if mae <= -300:
                bimodal_winner.append(t)
            else:
                pure_winner.append(t)
        else:
            if mfe >= 300:
                giveback_loser.append(t)
            else:
                pure_loser.append(t)

    cats = [
        ("Pure winner       (mae > -300, net > 0)", pure_winner, "safe path → trail cuts nothing real"),
        ("Bimodal winner    (mae <= -300, net > 0)", bimodal_winner, "DIP → RECOVER → win. **Trail would FALSELY CUT these.**"),
        ("Pure loser        (mfe < 300, net < 0)", pure_loser, "never had a chance, trail irrelevant"),
        ("Giveback loser    (mfe >= 300, net < 0)", giveback_loser, "**RISE → FALL** (WLD pattern). Trail would help."),
    ]
    print(f"{'Category':<48} {'Count':>6} {'Net $':>10} {'Avg net bps':>13}  Story")
    print("-" * 130)
    for name, lst, story in cats:
        n = len(lst)
        sum_pnl = sum(t.get("pnl", 0) for t in lst)
        avg_net = sum(t.get("net", 0) for t in lst) / n if n else 0
        print(f"{name:<48} {n:>6} {sum_pnl:>+9,.0f}$ {avg_net:>+11.0f}    {story}")

    print()
    print("=" * 84)
    print("CRITICAL RATIO:  bimodal winners vs giveback losers")
    print("=" * 84)
    n_save = len(giveback_loser)
    n_kill = len(bimodal_winner)
    print(f"  Trades the trail HELPS (giveback losers):     {n_save}")
    print(f"  Trades the trail HURTS (bimodal winners):     {n_kill}")
    print(f"  Ratio kill/save: {n_kill / max(1, n_save):.2f}x")
    print()
    # net dollar impact assuming a trail @ MFE 1000 / offset 500 would cut at MFE - offset
    # For giveback_loser: would exit at +500 instead of net (negative)
    saved = sum(max(0, 500 - t.get("net", 0)) for t in giveback_loser) / 100 * (500*0.18)  # rough $
    # For bimodal_winner: would exit at MFE - 500 instead of full final winning
    # Actual bps lost = final_net - (mfe - 500), if positive (we're cutting before peak)
    hurt = 0.0
    for t in bimodal_winner:
        mfe = t.get("mfe_bps", 0)
        net = t.get("net", 0)
        # If trail would have armed (mfe>=1000) AND exited at mfe-500 instead of net
        if mfe >= 1000:
            exit_at = mfe - 500
            if exit_at < net:
                hurt += (net - exit_at)  # net bps lost vs counterfactual
    print(f"  S9 bimodal winners with MFE >= 1000 that WOULD be cut by trail (1000/500):")
    candidates = [t for t in bimodal_winner if t.get("mfe_bps", 0) >= 1000]
    print(f"    {len(candidates)} trades")
    print()
    print("EXAMPLES — bimodal winners (kept by current rules, would die under trail):")
    bimodal_winner.sort(key=lambda t: -t.get("mfe_bps", 0))
    for t in bimodal_winner[:8]:
        coin = t.get("coin","?"); d=t.get("dir",0)
        mfe = t.get("mfe_bps",0); mae=t.get("mae_bps",0); net=t.get("net",0); pnl=t.get("pnl",0)
        print(f"  {coin:6} {'SHORT' if d<0 else 'LONG ':5}  mfe={mfe:+6.0f}  mae={mae:+6.0f}  net={net:+6.0f} bps  pnl=${pnl:+,.1f}")
    print()
    print("EXAMPLES — giveback losers (saved by trail):")
    giveback_loser.sort(key=lambda t: t.get("net", 0))
    for t in giveback_loser[:8]:
        coin = t.get("coin","?"); d=t.get("dir",0)
        mfe = t.get("mfe_bps",0); mae=t.get("mae_bps",0); net=t.get("net",0); pnl=t.get("pnl",0)
        print(f"  {coin:6} {'SHORT' if d<0 else 'LONG ':5}  mfe={mfe:+6.0f}  mae={mae:+6.0f}  net={net:+6.0f} bps  pnl=${pnl:+,.1f}")


if __name__ == "__main__":
    main()
