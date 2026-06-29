"""Levier C — derisk global en bear profond (overlay de sizing).

Réduit la taille de TOUTES les entrées quand btc_z < seuil. But : réduire le
drawdown pendant les bears soutenus sans détruire le PnL (on accepte un PnL
légèrement moindre si le DD s'améliore nettement). Le trade est toujours pris,
juste plus petit (pas un filtre d'entrée → pas de slot-substitution).

Compare baseline (pas de derisk) vs plusieurs (seuil, facteur), sur 28/12/6/3m,
aligné/margin/mfe_on_close. Métriques : PnL$ + maxDD%.

Usage : python3 -m backtests.sweep_bear_derisk
"""
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

WINDOWS = [(28, "28m"), (12, "12m"), (6, "6m"), (3, "3m")]
CONFIGS = [
    ("baseline",        None),
    ("z<-0.5 ×0.5",     (-0.5, 0.5)),
    ("z<-1.0 ×0.5",     (-1.0, 0.5)),
    ("z<-1.0 ×0.3",     (-1.0, 0.3)),
    ("z<-1.5 ×0.5",     (-1.5, 0.5)),
]


def main():
    print("Chargement données 3 ans…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); fund = load_funding()
    latest = max(c["t"] for c in data["BTC"])

    res = {c[0]: {} for c in CONFIGS}
    for months, wl in WINDOWS:
        start = latest - int(months * 30.4 * 86400 * 1000)
        for cl, bd in CONFIGS:
            r = run_window(features, data, sf, dxy, start, latest,
                           start_capital=1000.0, oi_data=oi, funding_data=fund,
                           apply_adaptive_modulator=True, aligned=True,
                           margin_check=True, mfe_on_close=True, bear_derisk=bd)
            res[cl][wl] = (r["pnl"], r["max_dd_pct"])
            print(f"  [{wl}] {cl:<14} PnL={r['pnl']:+10.0f}  DD={r['max_dd_pct']:6.1f}%",
                  flush=True)

    base = res["baseline"]
    print("\n" + "=" * 74)
    print("RÉCAP  (ΔPnL vs baseline / DD absolu ; on cherche DD↓ sans PnL trop ↓)")
    print("config".ljust(15) + "".join(w[1].rjust(15) for w in WINDOWS))
    for cl, _ in CONFIGS:
        line = cl.ljust(15)
        for _, wl in WINDOWS:
            pnl, dd = res[cl][wl]
            dpnl = pnl - base[wl][0]
            ddd = dd - base[wl][1]   # >0 = DD amélioré (moins négatif)
            line += f"{dpnl:+8.0f}/{ddd:+5.1f}".rjust(15)
        print(line)
    print("\nFormat cellule : ΔPnL$ vs base / ΔDD pp (+ = DD AMÉLIORÉ).")
    print("Levier C intéressant si ΔDD>0 franc (DD réduit) ET ΔPnL pas trop négatif.")


if __name__ == "__main__":
    main()
