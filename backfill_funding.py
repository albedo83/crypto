"""One-shot backfill: fetch real funding per historical trade from HL, update
the trades table and bot._total_pnl so the dashboard reconciles with the
exchange equity.

Run ONLY when the live bot is stopped (otherwise state.json will race).

Usage:
    python3 backfill_funding.py --dry-run      # preview deltas
    python3 backfill_funding.py --apply         # actually write
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

from analysis.bot.config import FUNDING_DRAG_BPS
# Rate-based funding (matches live reality at 100% vs user_funding_history which is
# API-capped at 500 events). Same method as backtest_rolling.
from backtests.backtest_rolling import load_funding, compute_funding_cost

LIVE_DIR = "/home/crypto/analysis/output_live"
DB_PATH = f"{LIVE_DIR}/reversal_ticks.db"
STATE_PATH = f"{LIVE_DIR}/reversal_state.json"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = p.parse_args()
    dry = not args.apply

    # Load funding rate history (hourly per coin)
    funding_data = load_funding()
    print(f"Loaded funding rate history for {len(funding_data)} coins")

    # Load existing trades
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    trades = list(con.execute("""
        SELECT id, symbol, direction, entry_time, exit_time,
               size_usdt, pnl_usdt, funding_usdt, net_bps
        FROM trades ORDER BY exit_time
    """).fetchall())
    print(f"Loaded {len(trades)} trades from DB")

    # Load current state
    with open(STATE_PATH) as f:
        state = json.load(f)
    old_total_pnl = state.get("total_pnl", 0.0)
    print(f"Current bot._total_pnl: ${old_total_pnl:+.2f}")

    # Compute corrections
    # Current pnl = size × net_bps/1e4 where net_bps includes −1 bps flat funding.
    # New pnl   = size × (net_bps + FUNDING_DRAG_BPS)/1e4 + funding_usdt
    #           = pnl_old + size × FUNDING_DRAG_BPS/1e4 + funding_usdt
    corrections = []
    total_new_funding = 0.0
    total_delta = 0.0
    skipped = 0
    for t in trades:
        if t["funding_usdt"] and abs(t["funding_usdt"]) > 1e-9:
            skipped += 1
            continue  # already backfilled
        try:
            entry_ms = int(datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00")).timestamp() * 1000)
            exit_ms = int(datetime.fromisoformat(t["exit_time"].replace("Z", "+00:00")).timestamp() * 1000)
        except Exception as e:
            print(f"  [{t['id']}] {t['symbol']}: timestamp parse failed — {e}")
            continue
        # Compute funding from historical rate table × notional × hold hours.
        # Convention: compute_funding_cost returns USDC COST (positive = we paid).
        # HL API user_funding_history returns DELTA (negative = we paid).
        # Invert to match the convention used elsewhere in trading.py where
        # funding_usdt = delta (so we subtract funding_usdt from pnl... wait let's
        # think). In trading.py v11.7.5:
        #     pnl = size*net_bps/1e4 + funding_usdt - flat_funding_usdt
        # where funding_usdt is the HL delta (negative = paid). So cost is subtracted
        # by ADDING a negative number. Make the backfill consistent: store as delta
        # (negative when we paid).
        direction = 1 if t["direction"] == "LONG" else -1
        funding_cost = compute_funding_cost(
            funding_data, t["symbol"], direction,
            entry_ms, exit_ms, t["size_usdt"])
        funding = -funding_cost  # convert from "cost" (+=paid) to "delta" (-=paid)
        flat_reversal = t["size_usdt"] * FUNDING_DRAG_BPS / 1e4
        delta = flat_reversal + funding
        new_pnl = t["pnl_usdt"] + delta
        total_new_funding += funding
        total_delta += delta
        corrections.append({
            "id": t["id"], "symbol": t["symbol"], "dir": t["direction"],
            "exit": t["exit_time"][:16],
            "size": t["size_usdt"],
            "old_pnl": t["pnl_usdt"], "new_pnl": round(new_pnl, 4),
            "funding": round(funding, 4), "delta": round(delta, 4),
        })
        time.sleep(0.12)  # polite pacing

    # Report
    print()
    print(f"{'Exit':<17}{'Sym':<7}{'Dir':<6}{'Size':>8}{'Fund':>10}{'OldPnL':>10}{'NewPnL':>10}{'Δ':>10}")
    for c in corrections:
        print(f"  {c['exit']}  {c['symbol']:<6}{c['dir']:<5}{c['size']:>7.2f}{c['funding']:>+10.4f}"
              f"{c['old_pnl']:>+10.2f}{c['new_pnl']:>+10.2f}{c['delta']:>+10.4f}")
    print()
    print(f"Trades processed: {len(corrections)} (skipped already-backfilled: {skipped})")
    print(f"Sum of real funding_usdt (negative = paid): {total_new_funding:+.4f}")
    print(f"Sum of pnl deltas:                         {total_delta:+.4f}")
    print(f"Old total_pnl:  {old_total_pnl:+.4f}")
    print(f"New total_pnl:  {old_total_pnl + total_delta:+.4f}")

    if dry:
        print("\nDRY-RUN — no changes written. Re-run with --apply to commit.")
        return 0

    # Backups
    ts = time.strftime("%Y%m%d-%H%M%S")
    db_bak = f"{DB_PATH}.bak-{ts}"
    state_bak = f"{STATE_PATH}.bak-{ts}"
    shutil.copy2(DB_PATH, db_bak)
    shutil.copy2(STATE_PATH, state_bak)
    print(f"\nBackups written: {db_bak}, {state_bak}")

    # Apply DB updates
    for c in corrections:
        con.execute(
            "UPDATE trades SET funding_usdt = ?, pnl_usdt = ? WHERE id = ?",
            (c["funding"], c["new_pnl"], c["id"]),
        )
    con.commit()
    con.close()
    print(f"Updated {len(corrections)} trade rows in DB.")

    # Apply state update
    state["total_pnl"] = round(old_total_pnl + total_delta, 4)
    # peak_balance should be recomputed: it's the running max after each trade,
    # but as an approximation we bump it if the new total_pnl changes the ATH.
    # Since most trades were net winners, peak might shift slightly.
    new_balance = state.get("capital", 300.0) + state["total_pnl"]
    if new_balance > state.get("peak_balance", 0):
        state["peak_balance"] = round(new_balance, 4)
    # Atomic write
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)
    print(f"Updated state.json: total_pnl={state['total_pnl']:+.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
