"""Phase 2 — Dump per-candle trajectories on 4 walk-forward splits.

Same 4 splits as backtest_drop_mina_walkforward.py (non-overlapping 6m), $500 cap.
Writes per-split:
  - backtests/output/trajectories_split_N.json — {trade_id: [{held, ur_bps, mfe_bps, mae_bps, btc_z}]}
  - backtests/output/trades_split_N.json — per-trade metadata for offline join

Usage: python3 -m backtests.dump_trajectories
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import backtests.backtest_genetic as bg


SPLITS = [
    (1, datetime(2024,  6, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc)),
    (2, datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2025,  6, 1, tzinfo=timezone.utc)),
    (3, datetime(2025,  6, 1, tzinfo=timezone.utc), datetime(2025, 12, 1, tzinfo=timezone.utc)),
    (4, datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026,  6, 1, tzinfo=timezone.utc)),
]
START_CAP = 500.0
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def main():
    from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
    from backtests.backtest_sector import compute_sector_features

    print(f"Loading data...", flush=True)
    t0 = time.time()
    data = bg.load_3y_candles()
    features = bg.build_features(data)
    sector_feats = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()
    print(f"  loaded in {time.time()-t0:.1f}s", flush=True)

    for split_n, sd, ed in SPLITS:
        traj_path = os.path.join(OUT_DIR, f"trajectories_split_{split_n}.json")
        trades_path = os.path.join(OUT_DIR, f"trades_split_{split_n}.json")
        print(f"\n[Split {split_n}] {sd.date()} → {ed.date()}", flush=True)
        t1 = time.time()
        r = run_window(
            features=features, data=data, sector_features=sector_feats, dxy_data=dxy,
            start_ts_ms=int(sd.timestamp() * 1000),
            end_ts_ms=int(ed.timestamp() * 1000),
            start_capital=START_CAP,
            oi_data=oi, funding_data=funding,
            trajectory_dump_path=traj_path,
        )
        # Strip trajectory from trades for the trades dump (it's in the trajectory file)
        trades_meta = [{
            "trade_id": t.get("trade_id"),
            "strat": t.get("strat"),
            "dir": t.get("dir"),
            "coin": t.get("coin"),
            "net": float(t.get("net", 0.0)),
            "size": float(t.get("size", 0.0)),
            "pnl": float(t.get("pnl", 0.0)),
            "entry_t": t.get("entry_t"),
            "exit_t": t.get("exit_t"),
            "mfe_bps": float(t.get("mfe_bps", 0.0)),
            "mae_bps": float(t.get("mae_bps", 0.0)),
            "reason": t.get("reason"),
        } for t in r["trades"]]
        with open(trades_path, "w") as fh:
            json.dump(trades_meta, fh)

        # Count trades with trajectories vs without (partial closes have no trajectory)
        with_traj = sum(1 for t in r["trades"] if t.get("trajectory") is not None)
        print(f"  → ROI {r['pnl_pct']:+.1f}%  DD {r['max_dd_pct']:.1f}%  trades={r['n_trades']} (with_traj={with_traj})  in {time.time()-t1:.1f}s")
        print(f"  → {traj_path}")
        print(f"  → {trades_path}")


if __name__ == "__main__":
    main()
