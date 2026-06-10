"""Parité btc_z — divergence #10 de docs/alfred_divergences.md.

Deux implémentations du même z-score coexistent :
  BOT : alfred.features.compute_btc_z — fenêtre glissante sur la liste de
        candles closes, appelée à chaque scan avec l'historique courant.
  BT  : backtests.backtest_rolling — map vectorisée {ts → z} précalculée
        dans run_window (variant "baseline").

CAUSE RACINE IDENTIFIÉE (2026-06-10) — off-by-one de fenêtre :
  la map BT prend `rets[j-n_zw : j+1]` = **n_zw+1 observations**, le bot
  prend min(len-n_lb, n_z) = **n_zw observations**. Une observation de plus
  dans la moyenne/std → Δz ≈ 1e-3, immatériel pour les décisions (aucun
  point à |Δz| > 0.05 sur 500 candles) mais pas iso. Avec la fenêtre
  corrigée (`j-n_zw+1 : j+1`), parité EXACTE (Δ = 0.00e+00).

Doctrine phase 1 : la sémantique legacy du BT est PRÉSERVÉE jusqu'à la
remise à zéro (phase 6) — ce test n'aligne PAS backtest_rolling, il
verrouille la compréhension :
  gate A : la voie bot == la map BT à fenêtre corrigée, à 1e-9 près
           (prouve que l'unique divergence est l'off-by-one connu)
  gate B : l'écart legacy reste immatériel (max |Δz| < 0.05)

Usage :
    python3 -m backtests.test_btc_z_parity [N_candles=500]
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alfred.features import compute_btc_z
from alfred.settings import DEFAULT_PARAMS

BTC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "output", "pairs_data", "BTC_4h_3y.json")


def bt_btc_z_map(btc_candles: list, lookback_days: int = 30,
                 z_window_days: int = 180, cpd: int = 6,
                 fixed_window: bool = False) -> dict[int, float]:
    """Réplique EXACTE de la voie vectorisée de backtest_rolling.run_window
    (variant "baseline", post-v12.18.x). Gardée en copie locale pour que le
    test reste valide même si run_window évolue — toute édition de la map
    dans run_window doit être répercutée ici (et vice-versa).

    fixed_window=True applique la correction off-by-one (n_zw observations,
    comme le bot) — c'est la sémantique cible de la phase 6."""
    n_lb = lookback_days * cpd
    n_zw = z_window_days * cpd
    closes = np.array([float(c["c"]) for c in btc_candles])
    out: dict[int, float] = {}
    if len(closes) < n_lb + 30:
        return out
    rets: list[float] = []
    for i in range(n_lb, len(closes)):
        if closes[i - n_lb] > 0:
            rets.append(float(closes[i] / closes[i - n_lb] - 1))
        else:
            rets.append(0.0)
    lo_off = (n_zw - 1) if fixed_window else n_zw
    for j in range(len(rets)):
        past = rets[max(0, j - lo_off):j + 1]
        if len(past) < 30:
            continue
        arr = np.array(past)
        mean = float(arr.mean())
        std = float(arr.std()) or 1.0
        out[int(btc_candles[n_lb + j]["t"])] = (rets[j] - mean) / std
    return out


def main() -> int:
    n_test = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    with open(BTC_PATH) as f:
        candles = json.load(f)
    # Normalise les closes en float (le JSON HL les stocke en str)
    candles = [{**c, "c": float(c["c"])} for c in candles]
    print(f"BTC candles : {len(candles)} | test sur les {n_test} derniers")

    p = DEFAULT_PARAMS
    lb = getattr(p, "macro_lookback_days", 30)
    zw = getattr(p, "macro_z_window_days", 180)
    print(f"Params : lookback {lb}d, z-window {zw}d, 6 candles/jour")

    bt_legacy = bt_btc_z_map(candles, lb, zw)
    bt_fixed = bt_btc_z_map(candles, lb, zw, fixed_window=True)
    print(f"BT maps : {len(bt_legacy)} ts (legacy + fixed)")

    d_fixed: list[float] = []
    d_legacy: list[tuple[int, float, float, float]] = []
    n_none = 0
    for k in range(len(candles) - n_test, len(candles)):
        ts = int(candles[k]["t"])
        if ts not in bt_legacy or ts not in bt_fixed:
            continue
        # Voie BOT : historique jusqu'à ce candle INCLUS (le bot calcule au
        # scan suivant le close, avec ce candle dans son deque).
        z_bot = compute_btc_z(candles[:k + 1], lb, zw)
        if z_bot is None:
            n_none += 1
            continue
        d_fixed.append(abs(z_bot - bt_fixed[ts]))
        d_legacy.append((ts, z_bot, bt_legacy[ts], abs(z_bot - bt_legacy[ts])))

    if not d_legacy:
        print("Aucun point comparable — vérifier la fenêtre")
        return 1

    max_fixed = max(d_fixed)
    legacy_abs = [d[3] for d in d_legacy]
    max_legacy = max(legacy_abs)
    mean_legacy = sum(legacy_abs) / len(legacy_abs)
    n_sig = sum(1 for d in legacy_abs if d > 0.05)

    print(f"\nPoints comparés : {len(d_legacy)} (bot None: {n_none})")
    gate_a = "✓" if max_fixed < 1e-9 else "✗ (divergence NON expliquée par l'off-by-one !)"
    print(f"GATE A — bot vs BT fenêtre corrigée : max |Δz| = {max_fixed:.2e} {gate_a}")
    print(f"GATE B — écart legacy (off-by-one connu) : max |Δz| = {max_legacy:.2e}, "
          f"mean = {mean_legacy:.2e}, |Δz|>0.05 : {n_sig}/{len(d_legacy)} "
          f"{'✓ immatériel' if n_sig == 0 else '✗ MATÉRIEL'}")

    if max_fixed >= 1e-9:
        print("\nTop 5 écarts inexpliqués (bot vs fixed) :")
        pairs = sorted(zip(d_fixed, d_legacy), key=lambda x: -x[0])[:5]
        for dval, (ts, zb, zt, _) in pairs:
            import datetime as dt
            iso = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).isoformat()[:16]
            print(f"  {iso}  bot={zb:+.6f}  Δfixed={dval:.2e}")

    ok = max_fixed < 1e-9 and n_sig == 0
    print(f"\nVerdict : {'✓ PARITÉ COMPRISE (off-by-one documenté, immatériel)' if ok else '✗ À INVESTIGUER'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
