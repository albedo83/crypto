"""Stress du coût d'entrée S9 (+30/+50 bps adverses) — luciole ou éruption ?

Contexte : mesure 2026-07-02 → slippage d'entrée S9 mean +13.7 / p90 +171 bps
sur n=7 (queue). Avant d'attendre n≥20, on stresse le BT : si l'edge S9
survit à +50 bps d'entrée adverse (qui rapproche mécaniquement son stop
adaptatif → stress du stop-hit rate aussi), le drapeau est de la comptabilité.

Config canonique (aligned + margin + mfe_on_close + modulateur + funding),
4 fenêtres 28m/12m/6m/3m × {base, S9+30, S9+50}.

Usage : python3 -m backtests.stress_s9_entry_cost
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/crypto")

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

WINDOWS_M = (28, 12, 6, 3)
CONFIGS = {"base": None, "S9+30": {"S9": 30.0}, "S9+50": {"S9": 50.0}}


def main():
    print("Chargement dataset…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sector = compute_sector_features(features, data)
    dxy, oi, funding = load_dxy(), load_oi(), load_funding()
    end_ts = max(c["t"] for c in data["BTC"])

    results = {}
    for months in WINDOWS_M:
        start_ts = end_ts - months * 30 * 86400 * 1000
        for name, slip in CONFIGS.items():
            r = run_window(features, data, sector, dxy, start_ts, end_ts,
                           start_capital=1000.0, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True,
                           entry_slip_bps_by_strat=slip)
            s9 = r["by_strat"].get("S9", {"n": 0, "pnl": 0.0, "wr": 0})
            s9_stops = sum(1 for t in r["trades"]
                           if t.get("strat") == "S9"
                           and t.get("reason") == "catastrophe_stop")
            results[(months, name)] = {
                "pnl_pct": r["pnl_pct"], "dd": r["max_dd_pct"],
                "s9_n": s9["n"], "s9_pnl": s9["pnl"], "s9_wr": s9["wr"],
                "s9_stops": s9_stops,
            }
            print(f"  {months:>2}m {name:<6} total {r['pnl_pct']:+9.1f}%  DD {r['max_dd_pct']:5.1f}%  "
                  f"S9: n={s9['n']:<3} pnl={s9['pnl']:+10.2f}$ wr={s9['wr']:.0f}% "
                  f"stops={s9_stops}", flush=True)

    print("\n=== Δ vs base (par fenêtre) ===")
    print(f"{'fen':<5}{'config':<8}{'Δtotal pp':>12}{'ΔDD pp':>9}{'ΔS9 pnl $':>12}"
          f"{'S9 stops b→s':>15}{'ΔS9 wr pp':>11}")
    for months in WINDOWS_M:
        b = results[(months, "base")]
        for name in ("S9+30", "S9+50"):
            s = results[(months, name)]
            print(f"{months:<5}{name:<8}{s['pnl_pct']-b['pnl_pct']:>+12.1f}"
                  f"{s['dd']-b['dd']:>+9.2f}{s['s9_pnl']-b['s9_pnl']:>+12.2f}"
                  f"{b['s9_stops']:>7}→{s['s9_stops']:<7}"
                  f"{s['s9_wr']-b['s9_wr']:>+11.0f}")


if __name__ == "__main__":
    main()
