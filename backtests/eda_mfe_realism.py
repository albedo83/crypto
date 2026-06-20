"""Étape A — quantifier le biais MFE-wick du backtest.

Le BT track MFE/MAE depuis les high/low de bougie (mèches incluses) ; le bot live
track MFE depuis le mark horaire. Les règles d'exit pilotées par MFE (prop_trail,
traj_cut, dead_timeout, s8_inlife, runner_ext, s10_trail, s8_dead, s9_early_dead)
arment donc sur des pics que le bot réel ne voit jamais → BT optimiste.

Ce script compare sur 28m :
  A1 = baseline (MFE sur high/low) — le canonique actuel.
  A2 = mfe_on_close (MFE sur close = proxy mark) — retire le biais de mèche.
Le catastrophe-stop reste sur high/low dans les deux (ordre au repos, réaliste).

A2 est CONSERVATEUR sur la cadence (1 échantillon/4h vs 4 marks/4h en live) → borne
BASSE du rendement atteignable. Si une règle reste net-positive même en A2, elle est
robuste ; si elle s'effondre, c'était un artefact de mèche.

Sortie : PnL total + PnL & n par raison de sortie, A1 vs A2, pour voir QUELLES règles
portent le biais (prop_trail récente vs anciennes traj_cut/dead_timeout/…).
"""
import datetime as dt
from collections import defaultdict

from backtests import backtest_rolling as br


def run(mfe_on_close: bool, data, features, sector_features, dxy_data, oi_data,
        funding_data, start_ms, latest_ts):
    return br.run_window(
        features, data, sector_features, dxy_data,
        start_ms, latest_ts, start_capital=1000.0,
        oi_data=oi_data, funding_data=funding_data,
        apply_adaptive_modulator=True, aligned=True, margin_check=True,
        mfe_on_close=mfe_on_close,
    )


def by_reason(res):
    agg = defaultdict(lambda: [0, 0.0])
    for t in res["trades"]:
        agg[t.get("reason", "?")][0] += 1
        agg[t.get("reason", "?")][1] += t.get("pnl", 0.0)
    return agg


def main():
    print("Loading data...", flush=True)
    data = br.load_3y_candles()
    features = br.build_features(data)
    sector_features = br.compute_sector_features(features, data)
    dxy_data = br.load_dxy()
    oi_data = br.load_oi()
    funding_data = br.load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = dt.datetime.fromtimestamp(latest_ts / 1000, tz=dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=28 * 30)
    start_ms = int(start_dt.timestamp() * 1000)
    print(f"Window {start_dt.date()} → {end_dt.date()} (~28m)\n", flush=True)

    print("Run A1 (baseline, MFE sur high/low)...", flush=True)
    a1 = run(False, data, features, sector_features, dxy_data, oi_data, funding_data, start_ms, latest_ts)
    print("Run A2 (mfe_on_close, MFE sur close)...", flush=True)
    a2 = run(True, data, features, sector_features, dxy_data, oi_data, funding_data, start_ms, latest_ts)

    print("\n================ RÉSULTAT 28m ================")
    print(f"  A1 baseline (wick MFE) : PnL ${a1['pnl']:+,.0f}  ({a1['pnl']/10:.0f}%)  "
          f"n={a1['n_trades']}  DD {a1['max_dd_pct']:.1f}%")
    print(f"  A2 mfe_on_close (mark) : PnL ${a2['pnl']:+,.0f}  ({a2['pnl']/10:.0f}%)  "
          f"n={a2['n_trades']}  DD {a2['max_dd_pct']:.1f}%")
    drop = a2['pnl'] - a1['pnl']
    pct = 100 * drop / a1['pnl'] if a1['pnl'] else 0
    print(f"  Δ (A2−A1) : ${drop:+,.0f}  ({pct:+.0f}% du PnL baseline)")
    print(f"  → le biais wick-MFE gonflait le BT de ${-drop:,.0f}" if drop < 0
          else f"  → A2 ≥ A1 (inattendu)")

    r1 = by_reason(a1); r2 = by_reason(a2)
    reasons = sorted(set(r1) | set(r2), key=lambda k: r1.get(k, [0, 0])[1])
    print(f"\n  Par raison de sortie (n, PnL$) — A1 → A2 :")
    print(f"  {'reason':18}{'A1_n':>5}{'A1_pnl':>10}   {'A2_n':>5}{'A2_pnl':>10}{'Δpnl':>10}")
    for k in reasons:
        n1, p1 = r1.get(k, [0, 0.0])
        n2, p2 = r2.get(k, [0, 0.0])
        print(f"  {k:18}{n1:>5}{p1:>10,.0f}   {n2:>5}{p2:>10,.0f}{p2-p1:>10,.0f}")


if __name__ == "__main__":
    main()
