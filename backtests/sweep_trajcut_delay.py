"""Walk-forward : opportunité d'un DÉLAI avant le traj_cut.

Compare, sur 28/12/6/3 mois (aligné, margin, mfe_on_close = canonique) :
  - OFF        : traj_cut désactivé (kill-switch)
  - base(4h)   : traj_cut actuel (time_since_mfe_min_h=4)
  - delay 8/12/16/24h : on retarde le cut (laisse la mean-reversion opérer)

Décision : un délai PASSE s'il bat baseline ET OFF sur PnL aux 4 fenêtres sans
dégrader le DD (gate ~+2pp). Si OFF bat tout → traj_cut net-négatif même en histo.

Sweep par réassignation de backtest_rolling._P (le run aligné lit _p_run = replace(_P)).
Usage : python3 -m backtests.sweep_trajcut_delay
"""
import dataclasses as dc
import backtests.backtest_rolling as btr
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = [(28, "28m"), (12, "12m"), (6, "6m"), (3, "3m")]
# (label, override dict appliqué à DEFAULT_PARAMS)
CONFIGS = [
    ("OFF",      {"traj_cut_strategies": frozenset()}),
    ("base(4h)", {"traj_cut_time_since_mfe_min_h": 4.0}),
    ("delay8",   {"traj_cut_time_since_mfe_min_h": 8.0}),
    ("delay12",  {"traj_cut_time_since_mfe_min_h": 12.0}),
    ("delay16",  {"traj_cut_time_since_mfe_min_h": 16.0}),
    ("delay24",  {"traj_cut_time_since_mfe_min_h": 24.0}),
]


def main():
    print("Chargement données 3 ans…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); funding = load_funding()
    latest = max(c["t"] for c in data["BTC"])

    # results[label][win] = (pnl, dd, s5_pnl, s5_n)
    results = {c[0]: {} for c in CONFIGS}
    for months, wlbl in WINDOWS:
        start = latest - int(months * 30.4 * 86400 * 1000)
        for clbl, ov in CONFIGS:
            btr._P = dc.replace(DEFAULT_PARAMS, **ov)
            r = run_window(features, data, sector_features, dxy, start, latest,
                           start_capital=1000.0, oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True)
            s5 = r["by_strat"].get("S5", {"pnl": 0, "n": 0})
            results[clbl][wlbl] = (r["pnl"], r["max_dd_pct"], s5["pnl"], s5["n"])
            print(f"  [{wlbl}] {clbl:<9} PnL={r['pnl']:+10.0f}  DD={r['max_dd_pct']:5.1f}%  "
                  f"S5={s5['pnl']:+9.0f} (n={s5['n']})", flush=True)
    btr._P = DEFAULT_PARAMS  # restore

    print("\n" + "=" * 70)
    print("RÉCAP PnL total (Δ vs base, + = mieux) | DD")
    base = results["base(4h)"]
    hdr = "config".ljust(10) + "".join(w[1].rjust(16) for w in WINDOWS)
    print(hdr)
    for clbl, _ in CONFIGS:
        line = clbl.ljust(10)
        for _, wlbl in WINDOWS:
            pnl, dd, _, _ = results[clbl][wlbl]
            dpnl = pnl - base[wlbl][0]
            line += f"{dpnl:+9.0f}/{dd:4.1f}%".rjust(16)
        print(line)

    print("\nLecture : pour chaque fenêtre, Δ PnL vs base(4h) puis DD.")
    print("OFF Δ>0 partout = traj_cut historiquement net-négatif. "
          "Un delayX Δ>0 et > OFF = raffinement gagnant.")


if __name__ == "__main__":
    main()
