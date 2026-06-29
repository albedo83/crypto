"""Le régime actuel (≈2 mois plats/négatifs) est-il déjà arrivé dans les 28m
qui ont calibré les règles ? On calcule l'equity curve du BT canonique 28m,
puis la distribution des rendements glissants sur 60 jours, et on situe la
période live actuelle dedans.

Usage : python3 -m backtests.regime_drawdown_context
"""
import datetime as dt
import numpy as np

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

WIN_D = 60  # fenêtre glissante ~2 mois


def main():
    print("Chargement + run 28m canonique…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sf = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); fund = load_funding()
    latest = max(c["t"] for c in data["BTC"])
    start = latest - int(28 * 30.4 * 86400 * 1000)
    r = run_window(features, data, sf, dxy, start, latest, start_capital=1000.0,
                   oi_data=oi, funding_data=fund, apply_adaptive_modulator=True,
                   aligned=True, margin_check=True, mfe_on_close=True)
    trades = sorted(r["trades"], key=lambda t: t["exit_t"])

    # equity curve journalière (compounding réel du BT)
    cap0 = 1000.0; cap = cap0
    pts = []  # (date, equity)
    for t in trades:
        cap += t["pnl"]
        day = dt.datetime.utcfromtimestamp(t["exit_t"] / 1000).date()
        pts.append((day, cap))
    # série journalière (dernière equity du jour)
    daily = {}
    for day, eq in pts:
        daily[day] = eq
    days = sorted(daily)
    eqs = np.array([daily[d] for d in days])
    print(f"{len(days)} jours d'activité, equity finale x{eqs[-1]/cap0:.1f}")

    # rendements glissants 60j (par index de jour-d'activité ~ proxy ; on aligne
    # plutôt sur les dates calendaires)
    date_arr = np.array([dt.datetime(d.year, d.month, d.day) for d in days])
    rolls = []
    for i in range(len(days)):
        d0 = date_arr[i]
        # trouve l'index ~60j plus tôt
        mask = date_arr <= d0 - dt.timedelta(days=WIN_D)
        if not mask.any():
            continue
        j = np.where(mask)[0][-1]
        ret = (eqs[i] / eqs[j] - 1) * 100
        rolls.append((days[i], ret))
    rr = np.array([x[1] for x in rolls])

    print(f"\n═══ Rendements glissants {WIN_D}j sur 28m (n={len(rr)} fenêtres) ═══")
    for p in (5, 10, 25, 50, 75, 90, 95):
        print(f"  p{p:<2} = {np.percentile(rr, p):+7.1f}%")
    neg = (rr <= 0).mean() * 100
    flat = ((rr > -5) & (rr < 5)).mean() * 100
    print(f"  → fenêtres 2 mois ≤ 0%   : {neg:.0f}%")
    print(f"  → fenêtres 2 mois plates (−5..+5%) : {flat:.0f}%")
    print(f"  → pire fenêtre 2 mois    : {rr.min():+.1f}%")

    # pires stretches (fin de fenêtre)
    worst = sorted(rolls, key=lambda x: x[1])[:8]
    print("\n  Pires fenêtres 2 mois (date de fin → rendement) :")
    for day, ret in worst:
        print(f"    …→ {day}  {ret:+.1f}%")

    # situer le live actuel : ~ -2.6% sur ~18j. On annualise grossièrement en
    # équivalent 60j NON (trop court) ; on compare plutôt la perf live 60j si dispo.
    print("\n═══ Contexte live ═══")
    print("  Live SENIOR ≈ -2.6% equity sur ~18j de bear (régime btc_z≈-1).")
    print("  → comparer au fait que ", f"{neg:.0f}% des fenêtres 2 mois du BT sont ≤0,")
    print("    pire =", f"{rr.min():+.1f}%. Si le live actuel est DANS cette enveloppe,")
    print("    le régime n'est pas inédit ; s'il est SOUS le min, il est hors-échantillon.")


if __name__ == "__main__":
    main()
