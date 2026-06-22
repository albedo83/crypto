"""EDA prémisse — trail S10 : y a-t-il des gains rendus à protéger ?

Avant tout sweep walk-forward (coûteux), on vérifie la prémisse : parmi les trades
S10, combien culminent à un MFE dans [300, 600) bps (que le trail actuel, armé à
600, ne protège PAS) puis finissent nettement plus bas / perdants ? Si ce paquet
est marginal → pas la peine de sweeper. S'il est gros et net-négatif → un trail
plus serré a une chance.

Lecture seule (run_window canonique aligné). Usage: python3 -m backtests.eda_s10_trail
"""
import statistics as st

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

CUR_TRIGGER = 600.0   # s10_trailing_trigger actuel
CUR_OFFSET = 150.0


def main():
    print("Chargement données 3 ans…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); funding = load_funding()
    latest = max(c["t"] for c in data["BTC"])
    start = latest - int(28 * 30.4 * 86400 * 1000)  # ~28 mois
    print("Run backtest 28m (aligné, margin, mfe_on_close)…")
    r = run_window(features, data, sector_features, dxy, start, latest,
                   start_capital=1000.0, oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True,
                   margin_check=True, mfe_on_close=True)
    trades = [t for t in r["trades"] if t.get("strat") == "S10"]
    print(f"\n=== S10 : {len(trades)} trades sur 28m ===")
    if not trades:
        print("Aucun trade S10."); return
    wins = [t for t in trades if t["net"] > 0]
    print(f"WR {100*len(wins)/len(trades):.0f}%  | somme net_bps {sum(t['net'] for t in trades):+.0f}")

    def bucket(lo, hi):
        return [t for t in trades if lo <= t["mfe_bps"] < hi]
    print("\nPar pic de MFE atteint :")
    for lo, hi, lbl in [(0, 300, '<300'), (300, 600, '[300,600) ← trail RATE'),
                        (600, 1e9, '>=600 (trail actif)')]:
        b = bucket(lo, hi)
        if not b:
            print(f"  {lbl:<24} n=0"); continue
        losers = [t for t in b if t["net"] <= 0]
        gb = [t["mfe_bps"] - t["net"] for t in b]  # gain rendu approx
        print(f"  {lbl:<24} n={len(b):<3} perdants={len(losers):<3} "
              f"net_moy={st.mean(t['net'] for t in b):+7.0f}bps "
              f"giveback_moy={st.mean(gb):.0f}bps "
              f"net_sum={sum(t['net'] for t in b):+.0f}")

    # Focus : le paquet que le trail actuel rate
    miss = bucket(300, 600)
    if miss:
        ml = [t for t in miss if t["net"] <= 0]
        print(f"\n>>> PAQUET RATÉ [300,600) : {len(miss)} trades, "
              f"{len(ml)} perdants, net cumulé {sum(t['net'] for t in miss):+.0f}bps")
        print("    (si un trail à 300 les avait coupés vers MFE-150 = ~+150bps chacun,")
        print(f"     gain potentiel grossier vs réel ≈ "
              f"{sum(150 - t['net'] for t in miss):+.0f}bps — borne indicative, à valider par sweep)")
    print("\nPrémisse OK si le paquet [300,600) est nombreux ET net-négatif.")


if __name__ == "__main__":
    main()
