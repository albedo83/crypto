"""Walk-forward dédié dead_timeout sous MFE RÉALISTE (dates de fin glissantes).

L'audit d'ablation (backtest_rule_audit_realistic) a montré que dead_timeout est
négative sur les 4 fenêtres à date de fin fixe sous MFE réaliste (cap MFE 150 calibré
sur MFE-wick → coupe trop de trades récupérables quand le MFE est mesuré au mark).
L'ablation 1-à-1 étant path-dépendante (cf. drop-mina), on confirme par walk-forward
à dates de fin GLISSANTES (7 tranches OOS 6m), sous mfe_on_close=True.

Variants testés (tous mfe_on_close=True) :
  baseline   : dead_timeout cap=150 (actuel)
  -off       : dead_timeout désactivé
  -cap50     : cap abaissé à 50 (ne coupe que les vrais morts sous MFE réaliste)
  -cap0      : cap à 0 (encore plus strict)

ΔPnL = variant − baseline ; + = le variant fait MIEUX que garder cap=150.

Usage : python3 -m backtests.walkforward_dead_timeout_realistic
"""
from __future__ import annotations

import dataclasses as dc
import os
import sys
import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

OOS_MONTHS = 6
END_OFFSETS = [0, 3, 6, 9, 12, 15, 18]
START_CAP = 500.0

VARIANTS = [
    ("baseline",  {}),
    ("-off",      {"dead_timeout_mfe_cap_bps": -99999.0}),
    ("-cap50",    {"dead_timeout_mfe_cap_bps": 50.0}),
    ("-cap0",     {"dead_timeout_mfe_cap_bps": 0.0}),
]


def main() -> int:
    print("Loading data…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    slices = []
    for off in END_OFFSETS:
        oos_end = end_dt - relativedelta(months=off)
        oos_start = oos_end - relativedelta(months=OOS_MONTHS)
        slices.append((off, int(oos_start.timestamp() * 1000),
                       int(oos_end.timestamp() * 1000),
                       oos_start.date().isoformat(), oos_end.date().isoformat()))

    def run(params, s_ms, e_ms):
        br._P = params
        r = run_window(features, data, sectors, dxy,
                       start_ts_ms=s_ms, end_ts_ms=e_ms, start_capital=START_CAP,
                       oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       mfe_on_close=True)
        return r["end_capital"], r["max_dd_pct"]

    t0 = time.time()
    base = {off: run(DEFAULT_PARAMS, s, e) for off, s, e, _, _ in slices}
    print(f"\nWalk-forward dead_timeout (MFE réaliste, OOS 6m glissant, ${START_CAP:.0f})")
    print(f"(ΔPnL = variant − baseline ; + = mieux que cap=150)  [{time.time()-t0:.0f}s]\n")
    hdr = f"  {'tranche OOS':25s}{'baseline$':>11s}"
    for name, _ in VARIANTS[1:]:
        hdr += f"{name:>11s}"
    print(hdr, flush=True)

    agg = {name: {"win": 0, "tot": 0.0, "dd": 0.0} for name, _ in VARIANTS[1:]}
    for off, s_ms, e_ms, sd, ed in slices:
        b_cap, b_dd = base[off]
        line = f"  {sd}→{ed} {b_cap:>10.0f}"
        for name, kw in VARIANTS[1:]:
            v_cap, v_dd = run(dc.replace(DEFAULT_PARAMS, **kw), s_ms, e_ms)
            d = v_cap - b_cap
            line += f"{d:>+10.0f}$"
            agg[name]["tot"] += d
            agg[name]["dd"] += (v_dd - b_dd)
            if d > 1:
                agg[name]["win"] += 1
        print(line, flush=True)

    n = len(slices)
    print(f"\n  {'variant':10s}{'tranches mieux':>16s}{'ΣΔPnL':>12s}{'ΔDDmoy':>9s}   verdict")
    for name, _ in VARIANTS[1:]:
        a = agg[name]
        ddm = a["dd"] / n
        verdict = ("ROBUSTE (≥6/7)" if a["win"] >= n - 1 else
                   "probable (≥4/7)" if a["win"] >= n // 2 + 1 else
                   "non concluant")
        print(f"  {name:10s}{a['win']:>10d}/{n}{a['tot']:>+12.0f}${ddm:>+8.1f}pp   {verdict}")
    br._P = DEFAULT_PARAMS
    return 0


if __name__ == "__main__":
    sys.exit(main())
