"""Walk-forward validation of OI/mark_oracle candidate gates.

Tests 7 single-feature gates and 3 combinations across 4 rolling windows
(28m/12m/6m/3m). A gate is only kept if it improves P&L on ALL windows —
anything that wins on 28m but degrades shorter windows is p-hacking.

Pre-registered gates (decided before running):
    G1: skip S10 if mark_oracle_signed <= 0
    G2: skip S10 if mark_oracle_signed <= -5
    G3: skip S9  if mark_oracle_signed > 0
    G4: skip S9  if 0 < oi_delta_24h_signed < 52
    G5: skip S5  if oi_delta_24h_signed >= -880
    G6: skip S5  if oi_delta_6h_signed > 825
    G7: skip S8  if -12 < mark_oracle_signed < -5

    C1 = G1 + G3
    C2 = G1 + G3 + G6
    C3 = G1 + G3 + G4 + G6
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window

OI_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")
HOUR_S = 3600


def load_oi_lookup() -> dict:
    db = sqlite3.connect(OI_DB)
    out: dict[str, dict[int, tuple]] = defaultdict(dict)
    for row in db.execute(
        "SELECT symbol, ts, oi, mark_px, oracle_px, impact_bid, impact_ask "
        "FROM asset_ctx"
    ):
        sym, ts = row[0], (row[1] // HOUR_S) * HOUR_S
        out[sym][ts] = row[2:]
    return dict(out)


def features_at(oi_lookup, sym: str, ts_ms: int) -> dict | None:
    ts = (ts_ms // 1000 // HOUR_S) * HOUR_S
    sym_data = oi_lookup.get(sym)
    if not sym_data:
        return None
    now = sym_data.get(ts)
    past_6h = sym_data.get(ts - 6 * HOUR_S)
    past_24h = sym_data.get(ts - 24 * HOUR_S)
    if not now or not past_6h or not past_24h:
        return None
    oi_now, mark, oracle, _, _ = now
    oi_6h = past_6h[0]
    oi_24h = past_24h[0]
    if oi_6h <= 0 or oi_24h <= 0 or oracle <= 0:
        return None
    return {
        "oi_delta_6h": (oi_now / oi_6h - 1) * 1e4,
        "oi_delta_24h": (oi_now / oi_24h - 1) * 1e4,
        "mark_oracle": (mark / oracle - 1) * 1e4,
    }


# Gate definitions. Each returns True if the trade should be skipped.
def make_gates(oi_lookup):
    def _get(sym, ts_ms):
        return features_at(oi_lookup, sym, ts_ms)

    def g1(sym, ts, strat, direction):  # S10 mark_oracle <= 0
        if strat != "S10":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        return f["mark_oracle"] * direction <= 0

    def g2(sym, ts, strat, direction):  # S10 mark_oracle <= -5
        if strat != "S10":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        return f["mark_oracle"] * direction <= -5

    def g3(sym, ts, strat, direction):  # S9 mark_oracle > 0
        if strat != "S9":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        return f["mark_oracle"] * direction > 0

    def g4(sym, ts, strat, direction):  # S9 oi_delta_24h in (0, 52)
        if strat != "S9":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        v = f["oi_delta_24h"] * direction
        return 0 < v < 52

    def g5(sym, ts, strat, direction):  # S5 oi_delta_24h >= -880
        if strat != "S5":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        return f["oi_delta_24h"] * direction >= -880

    def g6(sym, ts, strat, direction):  # S5 oi_delta_6h > 825
        if strat != "S5":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        return f["oi_delta_6h"] * direction > 825

    def g7(sym, ts, strat, direction):  # S8 mark_oracle in (-12, -5)
        if strat != "S8":
            return False
        f = _get(sym, ts)
        if not f:
            return False
        v = f["mark_oracle"] * direction
        return -12 < v < -5

    def combine(*gates):
        def _c(sym, ts, strat, direction):
            return any(g(sym, ts, strat, direction) for g in gates)
        return _c

    return {
        "G1 S10 mark_or≤0": g1,
        "G2 S10 mark_or≤-5": g2,
        "G3 S9  mark_or>0": g3,
        "G4 S9  oi24h∈(0,52)": g4,
        "G5 S5  oi24h≥-880": g5,
        "G6 S5  oi6h>825": g6,
        "G7 S8  mar_or∈(-12,-5)": g7,
        "C1 G1+G3": combine(g1, g3),
        "C2 G1+G3+G6": combine(g1, g3, g6),
        "C3 G1+G3+G4+G6": combine(g1, g3, g4, g6),
    }


def fmt_dollar(v: float) -> str:
    return f"${v:>7,.0f}".replace(",", " ")


def main() -> int:
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)

    print("Loading OI history...")
    oi_lookup = load_oi_lookup()

    gates = make_gates(oi_lookup)

    # Cap to OI coverage end
    latest_ts = max(c["t"] for c in data["BTC"])
    oi_last = max(max(v.keys()) for v in oi_lookup.values())
    end_dt = min(
        datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc),
        datetime.fromtimestamp(oi_last, tz=timezone.utc),
    )
    end_ts = int(end_dt.timestamp() * 1000)

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]

    # Baseline
    print(f"\nEnd date capped to {end_dt.date()} (OI coverage)")
    print(f"Windows: {[w[0] for w in windows]}")
    print("\nBaseline:")
    baselines = {}
    for label, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window(features, data, sector_features, {}, start_ts, end_ts)
        baselines[label] = r
        print(f"  {label:<5} {fmt_dollar(r['end_capital'])} "
              f"({r['pnl_pct']:+6.0f}%)  DD {r['max_dd_pct']:+5.1f}%  "
              f"n={r['n_trades']}  WR {r['win_rate']:.0f}%")

    # Test each gate individually
    print(f"\n{'='*78}")
    print(f"{'Gate':<25} {'window':<6} {'Δ$':>10} {'Δ%':>7} {'Δ_DD':>7} "
          f"{'n_skip':>8} {'verdict'}")
    print(f"{'='*78}")
    survivors = []
    for gate_name, gate_fn in gates.items():
        all_positive = True
        gate_results = []
        for label, start_dt in windows:
            start_ts = int(start_dt.timestamp() * 1000)
            r = run_window(features, data, sector_features, {}, start_ts, end_ts,
                           skip_fn=gate_fn)
            base = baselines[label]
            d_dollar = r["end_capital"] - base["end_capital"]
            d_pct = r["pnl_pct"] - base["pnl_pct"]
            d_dd = r["max_dd_pct"] - base["max_dd_pct"]
            n_skip = base["n_trades"] - r["n_trades"]
            gate_results.append((label, d_dollar, d_pct, d_dd, n_skip))
            if d_dollar < 0:
                all_positive = False
        verdict = "✓ KEEP" if all_positive else "✗ reject"
        if all_positive:
            survivors.append(gate_name)
        for i, (label, d_dollar, d_pct, d_dd, n_skip) in enumerate(gate_results):
            name_col = gate_name if i == 0 else ""
            v_col = verdict if i == 0 else ""
            sign_dollar = "+" if d_dollar >= 0 else ""
            sign_pct = "+" if d_pct >= 0 else ""
            sign_dd = "+" if d_dd >= 0 else ""
            print(f"{name_col:<25} {label:<6} "
                  f"{sign_dollar}{fmt_dollar(d_dollar):>9} "
                  f"{sign_pct}{d_pct:>5.0f}% "
                  f"{sign_dd}{d_dd:>5.1f} "
                  f"{n_skip:>8} {v_col}")
        print()

    print(f"\n{'='*78}")
    print(f"SURVIVORS (improve on all 4 windows): {len(survivors)}")
    for s in survivors:
        print(f"  ✓ {s}")
    if not survivors:
        print("  (none — all gates degrade at least one window)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
