"""Walk-forward : gate/dé-amp S5 LONG sur consec_up (2026-07-08).

EDA (`eda_s5_long_reversal.py`) : la cause du retournement = consec_up bas.
S5 LONG sur token PAS en up-streak (consec_up<2) = WR 55% / 31% cata / −$1110 ;
en up-streak (≥2) = WR 81% / 13% cata / +$2381. Le seul séparateur d'entrée.

Teste si gater/dé-amp S5 LONG quand consec_up < seuil survit strict 4/4.
margin_check ON (cap proportionnel v1.13.0 = défaut). Rien shippé sans 4/4.
Usage : python3 -m backtests.backtest_s5_long_consec
"""
import dataclasses as dc
import sys

sys.path.insert(0, "/home/crypto")

import bisect
from datetime import datetime
from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

WINDOWS = (("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3))


def main():
    print("Loading…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000)
              for w, m in WINDOWS}

    # lookup consec_up(coin, ts) ← la row feature ≤ ts
    cu = {}
    for coin, rows in features.items():
        cu[coin] = ([r["t"] for r in rows], [r.get("consec_up") for r in rows])

    def consec_at(coin, ts):
        if coin not in cu:
            return None
        tl, vl = cu[coin]
        i = bisect.bisect_right(tl, ts) - 1
        return vl[i] if i >= 0 else None

    def gate_fn(thr):
        def fn(coin, ts, strat, dr):
            if strat == "S5" and dr == 1:
                c = consec_at(coin, ts)
                return c is not None and c < thr
            return False
        return fn

    br._P = DEFAULT_PARAMS

    def run(w, skip_fn=None):
        r = run_window(features, data, sectors, dxy, starts[w], end_ms,
                       start_capital=500.0, oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True, skip_fn=skip_fn)
        return r["end_capital"], r.get("max_dd_pct", 0.0)

    base = {w: run(w) for w, _ in WINDOWS}
    print("base :", {w: f"{base[w][0]:.0f}/{base[w][1]:.0f}%" for w, _ in WINDOWS}, flush=True)

    print(f"\n{'variante':<18} {'fen':<5} {'end$':>8} {'Δ$':>9} {'DD':>6} {'ΔDD':>6} {'ok'}")
    for thr in (1, 2, 3):
        passes = 0
        for w, _ in WINDOWS:
            e, d = run(w, gate_fn(thr))
            eb, db = base[w]
            ok = (e >= eb - 0.01) and (d >= db - 0.01)
            passes += ok
            print(f"gate consec<{thr}{'':<7} {w:<5} {e:>8.0f} {e-eb:>+9.0f} {d:>5.0f}% "
                  f"{d-db:>+5.0f} {'✓' if ok else '·'}", flush=True)
        print(f"  → gate consec_up<{thr} : {passes}/4" + ("  *** PASS STRICT ***" if passes == 4 else ""))


if __name__ == "__main__":
    main()
