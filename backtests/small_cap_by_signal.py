"""MESURE — que valent les 6 derniers mois, par signal, à PETIT CAPITAL ?

Étude de mesure (autorisée par la clause de clôture) : elle ne cherche aucun
edge et ne propose aucun réglage. Elle répond à une question de fait — le
problème des six derniers mois est-il S5 en particulier, ou tout le moteur ?

Le régime de capital est celui du live ($518), pas les $1 000 canoniques : la
décomposition par palier a montré que 74,5 % du gain du backtest 28 mois se
produit entre $6 000 et $12 000, un niveau hors d'atteinte ici. Mesurer à
$1 000 répondrait à une autre question que celle posée.

Usage : python3 -m backtests.small_cap_by_signal
"""
from __future__ import annotations
import collections, json, os, statistics, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtests.backtest_rolling as br  # noqa: E402
from backtests.backtest_rolling import (  # noqa: E402
    run_window, load_oi, load_funding, load_dxy)
from backtests.backtest_genetic import load_3y_candles, build_features  # noqa: E402
from backtests.backtest_sector import compute_sector_features  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = 518.34          # capital réel du live au reset
MONTHS = 6
MIN_CELL = 10         # sous ce seuil : cellule NON ÉMISE


def stats(rows):
    if len(rows) < MIN_CELL:
        return {"n": len(rows), "emitted": False}
    nets = [t["net"] for t in rows]
    pnl = sum(t["pnl"] for t in rows)
    return {"n": len(rows), "emitted": True, "pnl": round(pnl, 2),
            "wr": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1),
            "net_mean": round(statistics.fmean(nets), 1),
            "net_median": round(statistics.median(nets), 1),
            "pnl_per_trade": round(pnl / len(rows), 3)}


def main() -> int:
    from dateutil.relativedelta import relativedelta
    data = load_3y_candles(); feat = build_features(data)
    sec = compute_sector_features(feat, data)
    oi, fu, dx = load_oi(), load_funding(), load_dxy()
    br._P = P
    end = datetime.fromtimestamp(max(c["t"] for c in data["BTC"]) / 1000, timezone.utc)
    start = end - relativedelta(months=MONTHS)
    print(banner(P, data, extra={"étude": "6 mois par signal, petit capital",
                                 "capital": CAP,
                                 "fenêtre": f"{start:%Y-%m-%d}→{end:%Y-%m-%d}"}),
          flush=True)

    r = run_window(feat, data, sec, dx, start_ts_ms=int(start.timestamp() * 1000),
                   end_ts_ms=int(end.timestamp() * 1000), start_capital=CAP,
                   oi_data=oi, funding_data=fu, apply_adaptive_modulator=True,
                   aligned=True, margin_check=True, mfe_on_close=True,
                   realistic_trail_booking=True)
    tr = r["trades"]
    require_series("net des trades", [t["net"] for t in tr], min_n=50)
    print(f"\n  BACKTEST ${CAP:.0f} → ${r['end_capital']:.2f}  "
          f"({r['end_capital']-CAP:+.2f}$, {(r['end_capital']/CAP-1)*100:+.1f}%)  "
          f"n={r['n_trades']}  DD {r['max_dd_pct']:.1f}%\n")

    by = collections.defaultdict(list)
    bydir = collections.defaultdict(list)
    for t in tr:
        by[t["strat"]].append(t)
        bydir[(t["strat"], "LONG" if t["dir"] > 0 else "SHORT")].append(t)

    print(f"  {'signal':7s} {'n':>5s} {'P&L':>10s} {'$/trade':>9s} {'WR':>6s} "
          f"{'net moy':>9s} {'net méd':>9s}")
    out = {}
    for s in sorted(by, key=lambda k: sum(t["pnl"] for t in by[k])):
        v = stats(by[s]); out[s] = v
        if not v["emitted"]:
            print(f"  {s:7s} {v['n']:>5d}   NON ÉMISE (< {MIN_CELL})"); continue
        print(f"  {s:7s} {v['n']:>5d} {v['pnl']:>+10.2f} {v['pnl_per_trade']:>+9.3f} "
              f"{v['wr']:>5.1f}% {v['net_mean']:>+9.0f} {v['net_median']:>+9.0f}")

    print(f"\n  par direction :")
    outd = {}
    for k in sorted(bydir, key=lambda k: sum(t["pnl"] for t in bydir[k])):
        v = stats(bydir[k]); outd[f"{k[0]} {k[1]}"] = v
        lbl = f"{k[0]} {k[1]}"
        if not v["emitted"]:
            print(f"  {lbl:12s} {v['n']:>5d}   NON ÉMISE"); continue
        print(f"  {lbl:12s} {v['n']:>5d} {v['pnl']:>+10.2f} {v['pnl_per_trade']:>+9.3f} "
              f"{v['wr']:>5.1f}% {v['net_mean']:>+9.0f}")

    res = {"capital": CAP, "months": MONTHS,
           "window": {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}"},
           "end_capital": round(r["end_capital"], 2), "dd": r["max_dd_pct"],
           "n_trades": r["n_trades"], "by_strat": out, "by_strat_dir": outd,
           "fingerprint": fingerprint(P, data, extra={"étude": "small_cap_by_signal"})}
    p = os.path.join(ROOT, "analysis", "output", "small_cap_by_signal.json")
    json.dump(res, open(p, "w"), indent=1, default=str)
    print(f"\n  dump : {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
