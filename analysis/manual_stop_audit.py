#!/usr/bin/env python3
"""Audit des manual_stop_set : combien on a gagné/perdu en clippant tôt ?

Pour chaque trade fermé via `manual_stop_set`, on calcule le PnL contrafactuel
si la position avait été laissée jusqu'à son exit naturel (timeout ou
catastrophe_stop). On compare au PnL réel pour mesurer le "manque à gagner"
ou la "perte évitée".

Simplifications :
- On modélise seulement 2 exits naturels : catastrophe_stop (sur low/high de
  bougie) ET timeout (à entry + HOLD_HOURS de la stratégie).
- Les exits intra-trade (traj_cut, dead_timeout, s8_inlife, prop_trail) sont
  ignorés — ils dépendent de btc_z au moment T et ne sont pas faciles à replay.
  Approximation acceptable car ces exits sont conçus pour MITIGER les pires
  pertes, donc le contrafactuel "timeout pur" est un upper bound du gain.

Usage :
    python3 -m analysis.manual_stop_audit --live
    python3 -m analysis.manual_stop_audit --paper
    python3 -m analysis.manual_stop_audit --junior
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = "/home/crypto"
PAIRS_DATA = os.path.join(ROOT, "backtests", "output", "pairs_data")

# HOLD_HOURS per strategy (from config.py)
HOLD_HOURS = {
    "S1": 72,
    "S5": 48,
    "S8": 60,
    "S9": 48,
    "S10": 24,
    "MANUAL": 48,
}
# Catastrophe stop per strategy (in bps, negative)
STOP_BPS = {
    "S1": -1250,
    "S5": -1250,
    "S8": -750,
    "S9": -1250,
    "S10": -1250,
    "MANUAL": -1250,
}
COST_BPS = 10  # round-trip cost (taker + funding flat)


def load_candles(symbol: str) -> list[dict]:
    path = os.path.join(PAIRS_DATA, f"{symbol}_4h_3y.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def counterfactual_pnl(symbol: str, direction_str: str, entry_price: float,
                       entry_ts_ms: int, target_exit_ts_ms: int,
                       stop_bps: float, size_usdt: float) -> tuple[float, str, float]:
    """Walk forward through 4h candles from entry_ts_ms up to target_exit_ts_ms.

    Returns (exit_price, reason, gross_bps).
    """
    candles = load_candles(symbol)
    if not candles:
        return entry_price, "no_data", 0.0

    direction = 1 if direction_str == "LONG" else -1
    # Catastrophe stop price level
    stop_price = entry_price * (1 + direction * stop_bps / 1e4)

    last_close_in_window = None
    for c in candles:
        if c["t"] < entry_ts_ms:
            continue
        if c["t"] > target_exit_ts_ms:
            break
        low = float(c["l"])
        high = float(c["h"])
        close = float(c["c"])
        # Catastrophe stop check on intra-candle range
        if direction == 1:  # LONG : catastrophe if price drops to stop_price
            if low <= stop_price:
                gross_bps = stop_bps  # already negative
                return stop_price, "catastrophe_stop_simulated", gross_bps
        else:  # SHORT : catastrophe if price rises to stop_price
            if high >= stop_price:
                gross_bps = stop_bps  # already negative
                return stop_price, "catastrophe_stop_simulated", gross_bps
        last_close_in_window = close

    # No catastrophe → close at last in-window candle close (≈ timeout price)
    if last_close_in_window is None:
        return entry_price, "no_candle_in_window", 0.0
    gross_bps = direction * (last_close_in_window / entry_price - 1) * 1e4
    return last_close_in_window, "timeout_simulated", gross_bps


def run(bot_name: str) -> int:
    db_paths = {
        "live": os.path.join(ROOT, "analysis", "output_live", "reversal_ticks.db"),
        "paper": os.path.join(ROOT, "analysis", "output", "reversal_ticks.db"),
        "junior": os.path.join(ROOT, "analysis", "output_live2", "reversal_ticks.db"),
    }
    if bot_name not in db_paths:
        print(f"unknown bot: {bot_name}")
        return 1
    db = sqlite3.connect(db_paths[bot_name])
    rows = db.execute(
        """SELECT entry_time, exit_time, symbol, strategy, direction,
                  entry_price, exit_price, pnl_usdt, size_usdt, mfe_bps,
                  mae_bps, hold_hours, reason
           FROM trades
           WHERE reason='manual_stop_set'
           ORDER BY exit_time"""
    ).fetchall()

    if not rows:
        print(f"No manual_stop_set trades found for {bot_name}.")
        return 0

    print("=" * 130)
    print(f"  Manual-stop audit — {bot_name.upper()}  (n={len(rows)} manual_stop_set trades)")
    print("=" * 130)
    print()
    print(f"  {'Date':16} {'Sym':5} {'St':3} {'Dir':5} "
          f"{'Size':>7} {'Real':>8} {'Counter':>10} {'Δ':>9} {'CF reason':22} {'MFE':>5} {'MAE':>5}")
    print("  " + "-" * 126)

    sum_real = 0.0
    sum_cf = 0.0
    n_loss_avoided = 0
    n_gain_missed = 0
    sum_loss_avoided = 0.0
    sum_gain_missed = 0.0

    for r in rows:
        (entry_iso, exit_iso, sym, strat, dir_str, entry_price, exit_price,
         pnl_real, size, mfe_bps, mae_bps, hold_h, reason) = r
        entry_dt = datetime.fromisoformat(entry_iso)
        entry_ts_ms = int(entry_dt.timestamp() * 1000)
        hold_hours = HOLD_HOURS.get(strat, 48)
        target_exit_ts_ms = entry_ts_ms + hold_hours * 3600 * 1000
        stop_bps = STOP_BPS.get(strat, -1250)

        cf_price, cf_reason, cf_gross_bps = counterfactual_pnl(
            sym, dir_str, entry_price, entry_ts_ms, target_exit_ts_ms,
            stop_bps, size)
        cf_net_bps = cf_gross_bps - COST_BPS
        cf_pnl = size * cf_net_bps / 1e4
        delta = cf_pnl - pnl_real

        sum_real += pnl_real
        sum_cf += cf_pnl
        if cf_pnl < pnl_real:
            n_loss_avoided += 1
            sum_loss_avoided += (pnl_real - cf_pnl)
        elif cf_pnl > pnl_real:
            n_gain_missed += 1
            sum_gain_missed += (cf_pnl - pnl_real)

        # Mark verdict
        if abs(delta) < 0.5:
            verdict = " "
        elif cf_pnl > pnl_real:
            verdict = "⚠"  # left money on table
        else:
            verdict = "✓"  # avoided loss

        print(f"  {entry_iso[:16]} {sym:5} {strat:3} {dir_str:5} "
              f"{size:>7.0f} {pnl_real:+8.2f} {cf_pnl:+10.2f} {delta:+9.2f} "
              f"{cf_reason:22} {mfe_bps:+5.0f} {mae_bps:+5.0f} {verdict}")

    print("  " + "-" * 126)
    print()
    print(f"  Total real PnL (manual_stop_set)  : ${sum_real:+10.2f}")
    print(f"  Total counterfactual PnL          : ${sum_cf:+10.2f}")
    print(f"  Net Δ (CF − Real)                 : ${sum_cf - sum_real:+10.2f}")
    print()
    print(f"  Trades où manual_stop a évité une perte (CF pire que real) :")
    print(f"    n={n_loss_avoided}, cumulé évité = ${sum_loss_avoided:+10.2f}")
    print(f"  Trades où manual_stop a coupé un gain (CF mieux que real) :")
    print(f"    n={n_gain_missed}, cumulé manqué = ${-sum_gain_missed:+10.2f}")
    print()
    if sum_real - sum_cf > 0:
        verdict_global = f"✓ Discipline GAGNANTE : +${sum_real - sum_cf:.2f} évité"
    else:
        verdict_global = f"⚠ Discipline coûte ${sum_cf - sum_real:.2f} d'opportunité ratée"
    print(f"  Verdict global : {verdict_global}")
    print()
    print("  Note : le contrafactuel modélise seulement catastrophe_stop + timeout.")
    print("         Les exits intra-trade (traj_cut, dead_timeout, s8_inlife, prop_trail)")
    print("         ne sont pas replayés → le CF surestime le gain naturel (upper bound).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_const", const="live", dest="bot")
    g.add_argument("--paper", action="store_const", const="paper", dest="bot")
    g.add_argument("--junior", action="store_const", const="junior", dest="bot")
    args = ap.parse_args()
    return run(args.bot)


if __name__ == "__main__":
    sys.exit(main())
