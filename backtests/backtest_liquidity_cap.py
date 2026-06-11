"""R&D — cap notionnel liquidity-aware : cap(token, t) = min(k_liq × vol24h,
k_risk × capital) vs le flat $500 (validé walk-forward 2026-06 mais calibré
pour le reset à petit capital — il étouffe le compounding sur les tokens
épais et reste agressif sur les minces : $500 ≈ 33 bps du volume quotidien
de MINA vs 0.02 bps de SOL).

Volume 24h par token : backtests/output/oi_history.db (S3, hourly, 2023-05 →
~J-10) + alfred/data/market.db ticks (60s downsamplé hourly, depuis 2026-06-10).
Lookup = dernière valeur connue ≤ ts d'entrée (pas de look-ahead) ; les trous
(lag S3) héritent de la dernière valeur — le volume bouge lentement à cette
échelle.

Gate de ship (doctrine) : PnL ≥ baseline sur les 4 fenêtres (strict 4/4)
ET ΔDD moyen ≤ +2pp. Sinon : classer.

Usage : python3 -m backtests.backtest_liquidity_cap
"""

from __future__ import annotations

import bisect
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

_DIR = os.path.dirname(os.path.abspath(__file__))
S3_DB = os.path.join(_DIR, "output", "oi_history.db")
ALFRED_DB = os.path.join(_DIR, "..", "alfred", "data", "market.db")

WINDOWS = [
    ("28m", "2024-02-04"),
    ("12m", "2025-06-04"),
    ("6m",  "2025-12-04"),
    ("3m",  "2026-03-04"),
]
START_CAP = 500.0

# Grille : k_liq en bps du volume 24h ; k_risk en × capital courant (None = ∞) ;
# floor500 : cap = max(500, …) — ne descend jamais sous le $500 validé (le
# haircut des tokens minces a tué la v1 sur les fenêtres récentes).
GRID = [
    ("liq10",                 10.0, None, False),
    ("liq30",                 30.0, None, False),
    ("liq50",                 50.0, None, False),
    ("liq100",               100.0, None, False),
    ("liq30_risk075",         30.0, 0.75, False),
    ("liq30_risk100",         30.0, 1.00, False),
    ("liq50_risk100",         50.0, 1.00, False),
    ("floor500_liq30",        30.0, None, True),
    ("floor500_liq50",        50.0, None, True),
    ("floor500_liq100",      100.0, None, True),
    ("floor500_liq30_r100",   30.0, 1.00, True),
    ("floor500_liq50_r100",   50.0, 1.00, True),
]


def load_vol_series() -> dict[str, tuple[list[int], list[float]]]:
    """coin → (ts_ms triés, vol24h USD). Sources S3 puis Alfred (prime)."""
    series: dict[str, dict[int, float]] = {}
    if os.path.exists(S3_DB):
        db = sqlite3.connect(f"file:{S3_DB}?mode=ro", uri=True)
        for sym, ts, v in db.execute(
                "SELECT symbol, ts, day_ntl_vlm FROM asset_ctx WHERE day_ntl_vlm > 0"):
            series.setdefault(sym, {})[int(ts) * 1000] = float(v)
        db.close()
    if os.path.exists(ALFRED_DB):
        db = sqlite3.connect(f"file:{ALFRED_DB}?mode=ro", uri=True)
        for sym, ts, v in db.execute(
                """SELECT symbol, (ts/3600)*3600, AVG(day_ntl_vlm) FROM ticks
                   WHERE day_ntl_vlm > 0 GROUP BY symbol, ts/3600"""):
            series.setdefault(sym, {})[int(ts) * 1000] = float(v)
        db.close()
    return {sym: (sorted(d), [d[t] for t in sorted(d)]) for sym, d in series.items()}


def make_cap_fn(vol_series, k_liq_bps: float, k_risk: float | None,
                floor500: bool = False):
    def fn(coin: str, ts: int, capital: float) -> float:
        s = vol_series.get(coin)
        liq = 500.0                                   # fallback : flat baseline
        if s:
            i = bisect.bisect_right(s[0], ts) - 1
            if i >= 0:
                liq = k_liq_bps / 1e4 * s[1][i]
        cap = liq if k_risk is None else min(liq, k_risk * capital)
        if floor500:
            cap = max(cap, 500.0)   # jamais sous le $500 validé walk-forward
        return max(cap, 10.0)                          # plancher exchange
    return fn


def main() -> int:
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    vol = load_vol_series()
    end_ms = max(c["t"] for c in data["BTC"])
    print(f"  vol series: {len(vol)} tokens")

    def ms(d):
        return int(datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)

    results: dict[str, dict[str, dict]] = {}
    configs = [("baseline_500", None, None, False)] + GRID
    for name, k_liq, k_risk, floor500 in configs:
        fn = make_cap_fn(vol, k_liq, k_risk, floor500) if k_liq is not None else None
        results[name] = {}
        for win, start in WINDOWS:
            t0 = time.time()
            r = run_window(features, data, sectors, dxy,
                           start_ts_ms=ms(start), end_ts_ms=end_ms,
                           start_capital=START_CAP,
                           oi_data=oi, funding_data=funding,
                           apply_adaptive_modulator=True,
                           max_notional_fn=fn, aligned=True)
            results[name][win] = r
            print(f"  {name:16s} {win:4s} → {r['end_capital']:9.0f} "
                  f"({len(r['trades'])} tr, DD {r['max_dd_pct']:.1f}%) "
                  f"[{time.time() - t0:.0f}s]")

    base = results["baseline_500"]
    print("\n" + "=" * 86)
    print(f"  {'config':16s} " + "".join(f"{w:>16s}" for w, _ in WINDOWS) +
          "   4/4   ΔDD moy")
    print(f"  {'baseline $500':16s} " + "".join(
        f"{base[w]['end_capital']:>13.0f}   " for w, _ in WINDOWS))
    for name, k_liq, k_risk, _f5 in GRID:
        r = results[name]
        deltas, dds, ok = [], [], True
        cells = ""
        for w, _ in WINDOWS:
            d_pnl = (r[w]["end_capital"] - base[w]["end_capital"]) / START_CAP * 100
            d_dd = r[w]["max_dd_pct"] - base[w]["max_dd_pct"]   # + = DD améliorée
            deltas.append(d_pnl); dds.append(d_dd)
            if r[w]["end_capital"] < base[w]["end_capital"] - 0.01:
                ok = False
            cells += f"{d_pnl:>+12.1f}pp  "
        avg_dd = sum(dds) / len(dds)
        gate = "PASS" if (ok and avg_dd >= -2.0) else "fail"
        print(f"  {name:16s} {cells} {gate:>5s}  {avg_dd:+5.2f}pp")
    print("\n  (ΔPnL en pp de capital initial $500 vs baseline ; ΔDD>0 = DD améliorée)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
