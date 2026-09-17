"""Bounded 3-vs-4 macro slot recheck; protocol docs/reprise_2026_09_17.md.

Run in the isolated checkout. Source files and funding DB are read-only.
Results are diagnostic historical comparisons, not new out-of-sample evidence.
"""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from alfred.settings import DEFAULT_PARAMS
import backtests.backtest_genetic as genetic
import backtests.backtest_rolling as engine
from backtests.backtest_sector import compute_sector_features
from backtests.fingerprint import config_hash, resolved_config


def ms(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    work = Path(__file__).resolve().parents[1]
    if work == args.source_root.resolve():
        parser.error("Use an isolated checkout")
    source = args.source_root / "backtests/output/pairs_data"
    snapshot = work / "backtests/output/macro_recheck_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for symbol in DEFAULT_PARAMS.all_symbols:
        for suffix in ("_4h_3y.json", "_4h.json", "_oi_4h.json"):
            path = source / (symbol + suffix)
            if path.exists():
                content = path.read_bytes()
                (snapshot / path.name).write_bytes(content)
                manifest[path.name] = hashlib.sha256(content).hexdigest()
    dxy_path = source / "macro_DXY.json"
    if dxy_path.exists():
        content = dxy_path.read_bytes()
        (snapshot / dxy_path.name).write_bytes(content)
        manifest[dxy_path.name] = hashlib.sha256(content).hexdigest()
    genetic.DATA_DIR = str(snapshot)
    engine.DATA_DIR = str(snapshot)
    data = genetic.load_3y_candles()
    missing = sorted(set(DEFAULT_PARAMS.all_symbols) - set(data))
    if missing:
        raise ValueError(f"Missing candle symbols: {missing}")
    oi = engine.load_oi()
    funding = {}
    db_path = args.source_root / "backtests/output/funding_history.db"
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        for symbol in DEFAULT_PARAMS.trade_symbols:
            rows = db.execute("SELECT ts,funding_rate FROM funding WHERE symbol=? ORDER BY ts", (symbol,)).fetchall()
            if not rows:
                raise ValueError(f"Missing funding: {symbol}")
            funding[symbol] = (np.array([r[0] for r in rows], dtype=np.int64),
                               np.array([r[1] for r in rows], dtype=np.float64))
    finally:
        db.close()
    features = genetic.build_features(data)
    sectors = compute_sector_features(features, data)
    dxy = engine.load_dxy()
    dates = ["2024-09-16T12:00", "2025-03-16T12:00", "2025-09-16T12:00",
             "2026-03-16T12:00", "2026-09-16T12:00"]
    windows = list(zip(dates, dates[1:]))
    if data["BTC"][-1]["t"] < ms(dates[-1]):
        raise ValueError("BTC data do not cover the predeclared end")
    output = {
        "protocol": "docs/reprise_2026_09_17.md", "source_base_commit": "c69131b",
        "source_file_hashes": manifest,
        "candle_ranges": {s: [v[0]["t"], v[-1]["t"], len(v)] for s, v in data.items()},
        "oi_ranges": {s: [v[0][0], v[-1][0], len(v)] for s, v in oi.items() if v},
        "funding_ranges": {s: [int(v[0][0]), int(v[0][-1]), len(v[0])] for s, v in funding.items()},
        "funding_sha256": {s: hashlib.sha256(v[0].tobytes() + v[1].tobytes()).hexdigest()
                           for s, v in funding.items()},
        "interpretation": "Historical sensitivity only; reused data, no future return claim.",
        "runs": [],
    }
    print("Snapshot loaded; fixed 4 disjoint semesters, slots 3/4, costs 4/6 bps", flush=True)
    for slip in (4.0, 6.0):
        engine.BACKTEST_SLIPPAGE_BPS = slip
        # COST is bound at import time; changing the label alone is inert.
        engine.COST = engine.TAKER_FEE_BPS + slip
        assert engine.COST == DEFAULT_PARAMS.taker_fee_bps + slip
        for start, end in windows:
            for slots in (3, 4):
                t0 = time.monotonic()
                p = replace(DEFAULT_PARAMS, max_macro_slots=slots)
                engine._P = p
                engine.MAX_MACRO_SLOTS = slots
                result = engine.run_window(
                    features, data, sectors, dxy, start_ts_ms=ms(start),
                    end_ts_ms=ms(end) - 1, start_capital=1000,
                    oi_data=oi, funding_data=funding, apply_adaptive_modulator=True,
                    aligned=True, margin_check=True, mfe_on_close=True,
                    realistic_trail_booking=True)
                uncovered, hours = 0, 0
                for trade in result["trades"]:
                    # Diagnostic sample coverage, measured on the hourly grid.
                    grid = np.arange(trade["entry_t"], trade["exit_t"], 3600000, dtype=np.int64)
                    times = funding[trade["coin"]][0]
                    idx = np.searchsorted(times, grid, side="right") - 1
                    valid = idx >= 0
                    valid &= grid - times[np.maximum(idx, 0)] < 3600000
                    uncovered += int((~valid).sum())
                    hours += len(grid)
                item = {
                    "start_inclusive": start, "end_exclusive": end,
                    "slippage_bps": slip, "max_macro_slots": slots,
                    "effective_round_trip_cost_bps_ex_funding": engine.COST,
                    "config_hash": config_hash(p), "resolved_config": resolved_config(p),
                    "end_capital": result["end_capital"], "max_dd_pct": result["max_dd_pct"],
                    "n_trades": result["n_trades"],
                    "n_s1": sum(t["strat"] == "S1" for t in result["trades"]),
                    "funding_uncovered_hours": uncovered, "position_hours": hours,
                }
                output["runs"].append(item)
                args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
                print(f"slip={slip:g} {start[:10]}..{end[:10]} slots={slots} "
                      f"capital={item['end_capital']:.2f} DD={item['max_dd_pct']:.2f} "
                      f"n={item['n_trades']} S1={item['n_s1']} "
                      f"funding_missing={uncovered}/{hours} ({time.monotonic()-t0:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
