"""Test 1d — abaisser max_same_direction (grille : addendum 3 du 2026-10-19)."""
import dataclasses
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
import backtests.backtest_rolling as br
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

data = load_3y_candles(); f = build_features(data); sec = compute_sector_features(f, data)
dxy, oi, fu = br.load_dxy(), br.load_oi(), br.load_funding()
end = datetime.fromtimestamp(max(c["t"] for c in data["BTC"]) / 1000, tz=timezone.utc)
BASE = br._P
ms = lambda d: int(d.timestamp() * 1000)

def run(s, e, msd):
    br._P = dataclasses.replace(BASE, max_same_direction=msd)
    r = br.run_window(f, data, sec, dxy, ms(s), ms(e), start_capital=1000.0, oi_data=oi, funding_data=fu,
                      apply_adaptive_modulator=True, aligned=True, margin_check=True,
                      skip_fn=lambda c, t, st, d: st == "S5")
    br._P = BASE
    return r["pnl_pct"], r["max_dd_pct"], sum(1 for t in r["trades"] if t["reason"] == "catastrophe_stop")

qs, d = [], end
for _ in range(10):
    s = d - relativedelta(months=3); qs.append((s, d)); d = s
qs.reverse()
res = {m: [] for m in (4, 3, 2)}
for s, e in qs:
    row = {m: run(s, e, m) for m in res}
    for m in res: res[m].append(row[m])
    print(f"{s:%Y-%m-%d} " + " | ".join(f"max{m}: {row[m][0]:+7.1f} % DD {row[m][1]:6.1f} % cat {row[m][2]:2}" for m in res), flush=True)
y = {m: run(datetime(2026, 1, 1, tzinfo=timezone.utc), end, m) for m in res}
ratio = {m: sum(x[0] for x in res[m]) / abs(min(x[1] for x in res[m])) for m in res}
for m in (3, 2):
    ok = ratio[m] > ratio[4] and y[m][0] >= 0.9 * y[4][0]
    print(f"max_same_direction={m}: ratio {ratio[m]:.2f} vs {ratio[4]:.2f} | 2026 {y[m][0]:+.1f} % (DD {y[m][1]:.1f} %, cat {y[m][2]}) "
          f"vs {y[4][0]:+.1f} % (DD {y[4][1]:.1f} %, cat {y[4][2]}) → {'VALIDÉ' if ok else 'REJETÉ'}")
