"""Test 1c — frein de contagion (grille : addendum 2 du 2026-10-19).

Backtest continu du moteur (S1, S8, S9, S10), 2024-04-19 → fin. Reconstruction :
latent de chaque position ouverte à la clôture 4h d'une entrée = dir × (close /
prix d'entrée − 1), prix d'entrée = ouverture de la bougie d'entrée.
Règle : pas d'entrée dans le sens d si ≥ 2 positions ouvertes dans ce sens sont à
−500 bps ou pire. Notionnel fixe 150 $ par trade.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from backtests.backtest_sector import compute_sector_features

UTC = timezone.utc
START = datetime(2024, 4, 19, tzinfo=UTC)
SPLIT = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
NOT, LOSS, NMIN, DAY = 150.0, -500.0, 2, 86400000


def main():
    data = load_3y_candles()
    f = build_features(data)
    end = max(c["t"] for c in data["BTC"])
    r = run_window(f, data, compute_sector_features(f, data), load_dxy(), int(START.timestamp() * 1000), end,
                   start_capital=1000.0, oi_data=load_oi(), funding_data=load_funding(),
                   apply_adaptive_modulator=True, aligned=True, margin_check=True,
                   skip_fn=lambda c, t, s, d: s == "S5")
    T = sorted(r["trades"], key=lambda t: t["entry_t"])
    candle = {c: {x["t"]: x for x in cs} for c, cs in data.items()}
    for t in T:
        k = candle[t["coin"]].get(t["entry_t"])
        t["px0"] = k["o"] if k else None
        t["bps"] = t["pnl"] / t["size"] * 1e4 if t["size"] else 0.0
        t["cat"] = t["reason"] == "catastrophe_stop"

    # Grappes : stops à < 24 h d'un autre stop dans le même sens
    cats = [t for t in T if t["cat"]]
    clustered = sum(1 for a in cats if any(b is not a and b["dir"] == a["dir"] and abs(b["exit_t"] - a["exit_t"]) < DAY
                                          for b in cats))
    print(f"{len(cats)} stops catastrophe, dont {clustered} ({clustered / len(cats):.0%}) à moins de 24 h "
          f"d'un autre stop dans le même sens")

    blocked = set()
    for i, t in enumerate(T):
        n_bad = 0
        for o in T:
            if o is t or o["dir"] != t["dir"] or not (o["entry_t"] < t["entry_t"] < o["exit_t"]) or not o["px0"]:
                continue
            k = candle[o["coin"]].get(t["entry_t"] - 4 * 3600000)       # dernière clôture 4h connue
            if k and o["dir"] * (k["c"] / o["px0"] - 1) * 1e4 <= LOSS:
                n_bad += 1
        if n_bad >= NMIN:
            blocked.add(i)
    print(f"entrées bloquées par la règle : {len(blocked)} / {len(T)}")

    res = {}
    for lab, sel in (("découverte", lambda t: t["entry_t"] < SPLIT), ("validation", lambda t: t["entry_t"] >= SPLIT)):
        ref = [t for t in T if sel(t)]
        kept = [t for i, t in enumerate(T) if sel(t) and i not in blocked]
        out = [t for i, t in enumerate(T) if sel(t) and i in blocked]
        pr = sum(t["bps"] for t in ref) / 1e4 * NOT
        pk = sum(t["bps"] for t in kept) / 1e4 * NOT
        cr, ck = sum(t["cat"] for t in ref), sum(t["cat"] for t in kept)
        res[lab] = (pk >= pr and ck < cr)
        mo = np.mean([t["bps"] for t in out]) if out else float("nan")
        print(f"  {lab}: réf {pr:+.0f} $ ({cr} stops) | règle {pk:+.0f} $ ({ck} stops) | "
              f"{len(out)} entrées bloquées, moyenne {mo:+.0f} bp, dont {sum(t['cat'] for t in out)} stops")
    print(f"→ {'VALIDÉ' if all(res.values()) else 'REJETÉ'}")


if __name__ == "__main__":
    main()
