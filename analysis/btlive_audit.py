"""btlive_audit — trade-by-trade explanation of the live vs backtest gap.

Matches live trades to backtest trades by (coin, strategy, direction) and
entry timestamp within +/- 4h. Reports:

  1. Matched trades: per-trade pnl delta breakdown (Δ entry px, Δ size,
     Δ exit px, Δ pnl, Δ reason).
  2. Live-only trades: trades the bot took that the backtest didn't.
     Their pnl sum is "extra" PnL on live's side (positive or negative).
  3. Backtest-only trades: trades the backtest took that live didn't.
     Their pnl sum is "missed" PnL by live.
  4. Aggregate reconciliation: sum of all three categories must add up
     to the headline gap (Δ total PnL).

Read-only on the bot. Reads DB + runs one backtest window.

Usage:
    .venv/bin/python3 -m analysis.btlive_audit --live
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict


BOTS = {
    "paper":  ("analysis/output",       "paper"),
    "live":   ("analysis/output_live",  "live"),
    "junior": ("analysis/output_live2", "junior"),
}


def read_live_trades(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT entry_time, exit_time, symbol, strategy, direction, "
        "       size_usdt, gross_bps, net_bps, pnl_usdt, funding_usdt, "
        "       reason, entry_price, exit_price "
        "FROM trades ORDER BY entry_time"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        entry_iso, exit_iso = r[0], r[1]
        entry_dt = dt.datetime.fromisoformat(entry_iso.replace('Z', '+00:00'))
        exit_dt  = dt.datetime.fromisoformat(exit_iso.replace('Z', '+00:00'))
        d = 1 if r[4] == "LONG" else -1
        out.append({
            "src": "live",
            "coin": r[2], "strat": r[3], "dir": d,
            "entry_ts": int(entry_dt.timestamp() * 1000),
            "exit_ts":  int(exit_dt.timestamp() * 1000),
            "size": r[5], "gross_bps": r[6], "net_bps": r[7],
            "pnl": r[8], "funding": r[9] or 0.0,
            "reason": r[10],
            "entry_px": r[11], "exit_px": r[12],
            "matched_to": None,
        })
    return out


def run_backtest_and_get_trades(start_dt: dt.datetime, start_cap: float, project_root: str) -> list[dict]:
    sys.path.insert(0, project_root)
    from backtests import backtest_rolling
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )

    print("Loading backtest data...", file=sys.stderr)
    data = backtest_rolling.load_3y_candles()
    features = backtest_rolling.build_features(data)
    sector_features = backtest_rolling.compute_sector_features(features, data)
    dxy_data = backtest_rolling.load_dxy()
    oi_data = backtest_rolling.load_oi()
    funding_data = backtest_rolling.load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])

    early_exit_params = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext_cfg = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)

    start_ms = int(start_dt.timestamp() * 1000)
    print(f"Running backtest {start_dt.date()} → "
          f"{dt.datetime.fromtimestamp(latest_ts/1000, dt.timezone.utc).date()} "
          f"cap=${start_cap:.0f}...", file=sys.stderr)
    res = backtest_rolling.run_window(
        features, data, sector_features, dxy_data,
        start_ms, latest_ts, start_capital=start_cap,
        oi_data=oi_data, funding_data=funding_data,
        early_exit_params=early_exit_params,
        runner_extension=runner_ext_cfg,
        apply_adaptive_modulator=True,
    )
    out = []
    for t in res["trades"]:
        out.append({
            "src": "bt",
            "coin": t["coin"], "strat": t["strat"], "dir": t["dir"],
            "entry_ts": int(t["entry_t"]), "exit_ts": int(t["exit_t"]),
            "size": t["size"], "pnl": t["pnl"],
            "net": t.get("net", 0),
            "reason": t["reason"],
            "matched_to": None,
        })
    return out, res


def match_trades(live: list[dict], bt: list[dict], slack_ms: int = 4 * 3600 * 1000):
    """Match each live trade to a backtest trade by (coin, strat, dir) and entry within slack."""
    # Index BT by (coin, strat, dir)
    bt_idx: dict = defaultdict(list)
    for i, b in enumerate(bt):
        bt_idx[(b["coin"], b["strat"], b["dir"])].append(i)

    matched_pairs = []  # (live_idx, bt_idx)
    used_bt = set()
    for i, lt in enumerate(live):
        key = (lt["coin"], lt["strat"], lt["dir"])
        candidates = [j for j in bt_idx.get(key, []) if j not in used_bt]
        if not candidates:
            continue
        # Closest by entry_ts
        best = min(candidates, key=lambda j: abs(bt[j]["entry_ts"] - lt["entry_ts"]))
        if abs(bt[best]["entry_ts"] - lt["entry_ts"]) <= slack_ms:
            matched_pairs.append((i, best))
            used_bt.add(best)
            lt["matched_to"] = best
            bt[best]["matched_to"] = i
    return matched_pairs


def fmt_ts(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%m-%d %H:%M")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper",  action="store_const", dest="bot", const="paper")
    g.add_argument("--live",   action="store_const", dest="bot", const="live")
    g.add_argument("--junior", action="store_const", dest="bot", const="junior")
    parser.add_argument("--start-cap", type=float, default=None)
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_rel, bot_key = BOTS[args.bot]
    output_dir = os.path.join(project_root, output_rel)
    db_path = os.path.join(output_dir, "reversal_ticks.db")

    sys.path.insert(0, project_root)
    from backtests.backtest_rolling import BOT_DEPLOYMENTS
    deploy_map = {b: d for b, d in BOT_DEPLOYMENTS}
    start_dt = dt.datetime.fromisoformat(deploy_map[bot_key]).replace(tzinfo=dt.timezone.utc)
    default_caps = {"paper": 1000.0, "live": 500.0, "junior": 300.0}
    start_cap = args.start_cap if args.start_cap else default_caps[bot_key]

    # 1. Load both sides
    live = read_live_trades(db_path)
    start_iso = start_dt.isoformat()
    live = [t for t in live if dt.datetime.fromtimestamp(t["entry_ts"]/1000, dt.timezone.utc).isoformat() >= start_iso]
    bt, bt_summary = run_backtest_and_get_trades(start_dt, start_cap, project_root)

    print(f"\nLoaded {len(live)} live trades, {len(bt)} backtest trades")

    # 2. Match
    pairs = match_trades(live, bt, slack_ms=4 * 3600 * 1000)
    print(f"Matched: {len(pairs)} pairs")

    live_only = [t for t in live if t["matched_to"] is None]
    bt_only   = [t for t in bt   if t["matched_to"] is None]
    print(f"Live-only: {len(live_only)},  BT-only: {len(bt_only)}")

    # 3. Aggregate
    live_total = sum(t["pnl"] for t in live)
    bt_total   = sum(t["pnl"] for t in bt)
    gap = live_total - bt_total

    print(f"\n{'='*72}")
    print(f"  HEADLINE GAP — {bot_key.upper()}")
    print(f"  Live total PnL:  ${live_total:+.2f}")
    print(f"  BT   total PnL:  ${bt_total:+.2f}")
    print(f"  Gap (L − BT):    ${gap:+.2f}")
    print(f"{'='*72}\n")

    # 4. Matched trade deltas
    matched_delta = 0.0
    matched_l_pnl = 0.0
    matched_b_pnl = 0.0
    for li, bi in pairs:
        matched_l_pnl += live[li]["pnl"]
        matched_b_pnl += bt[bi]["pnl"]
    matched_delta = matched_l_pnl - matched_b_pnl

    live_only_pnl = sum(t["pnl"] for t in live_only)
    bt_only_pnl   = sum(t["pnl"] for t in bt_only)

    print(f"GAP BREAKDOWN:")
    print(f"  Matched trades ({len(pairs)} pairs): live ${matched_l_pnl:+.2f} − BT ${matched_b_pnl:+.2f} = ${matched_delta:+.2f}")
    print(f"    → écarts de prix/taille/timing/exit sur les mêmes trades")
    print(f"  Live-only ({len(live_only)} trades): ${live_only_pnl:+.2f}")
    print(f"    → trades pris par live mais pas par BT (live's extra trades)")
    print(f"  BT-only   ({len(bt_only)} trades):   ${bt_only_pnl:+.2f}")
    print(f"    → trades pris par BT mais pas par live (live missed)")
    print(f"  Sum check: matched_Δ + live_only − bt_only = ${matched_delta + live_only_pnl - bt_only_pnl:+.2f}")
    print(f"  (should match gap ${gap:+.2f}; diff = ${(matched_delta + live_only_pnl - bt_only_pnl) - gap:+.4f})")

    # 5. Top contributors among matched
    print(f"\n--- TOP 15 MATCHED trades by |Δpnl|  (Δ = live − BT) ---")
    pair_details = []
    for li, bi in pairs:
        L = live[li]; B = bt[bi]
        d = L["pnl"] - B["pnl"]
        pair_details.append((d, L, B))
    pair_details.sort(key=lambda x: abs(x[0]), reverse=True)
    print(f"  {'Coin':<6} {'Strat':<5} {'Dir':<5} {'EntryL':<12} {'EntryBT':<12} {'L_size':>7} {'BT_size':>7} {'L_pnl':>8} {'BT_pnl':>8} {'Δpnl':>8}  {'L_reason':<18} {'BT_reason':<14}")
    for d, L, B in pair_details[:15]:
        dir_s = "LONG" if L["dir"] == 1 else "SHORT"
        print(f"  {L['coin']:<6} {L['strat']:<5} {dir_s:<5} {fmt_ts(L['entry_ts']):<12} {fmt_ts(B['entry_ts']):<12} {L['size']:>7.1f} {B['size']:>7.1f} {L['pnl']:>+8.2f} {B['pnl']:>+8.2f} {d:>+8.2f}  {L['reason'][:18]:<18} {B['reason'][:14]:<14}")

    # 6. Live-only trades (full list if small)
    print(f"\n--- LIVE-ONLY trades ({len(live_only)}) ---")
    if len(live_only) <= 30:
        live_only.sort(key=lambda x: x["pnl"])
        print(f"  {'Coin':<6} {'Strat':<5} {'Dir':<5} {'Entry':<12} {'Size':>7} {'Pnl':>8}  Reason")
        for t in live_only:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"  {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} {t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")
    else:
        live_only.sort(key=lambda x: abs(x["pnl"]), reverse=True)
        print(f"  Top 20 by |pnl|:")
        for t in live_only[:20]:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"  {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} {t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")

    # 7. BT-only trades
    print(f"\n--- BACKTEST-ONLY trades ({len(bt_only)}) ---")
    if len(bt_only) <= 30:
        bt_only.sort(key=lambda x: x["pnl"], reverse=True)
        print(f"  {'Coin':<6} {'Strat':<5} {'Dir':<5} {'Entry':<12} {'Size':>7} {'Pnl':>8}  Reason")
        for t in bt_only:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"  {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} {t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")
    else:
        bt_only.sort(key=lambda x: abs(x["pnl"]), reverse=True)
        print(f"  Top 20 by |pnl|:")
        for t in bt_only[:20]:
            dir_s = "LONG" if t["dir"] == 1 else "SHORT"
            print(f"  {t['coin']:<6} {t['strat']:<5} {dir_s:<5} {fmt_ts(t['entry_ts']):<12} {t['size']:>7.1f} {t['pnl']:>+8.2f}  {t['reason']}")

    # 8. Per-strat breakdown of the gap
    print(f"\n--- PER-STRATEGY GAP BREAKDOWN ---")
    per_strat = defaultdict(lambda: {"matched_l": 0.0, "matched_b": 0.0, "live_only": 0.0, "bt_only": 0.0, "n_match": 0, "n_lo": 0, "n_bo": 0})
    for li, bi in pairs:
        s = live[li]["strat"]
        per_strat[s]["matched_l"] += live[li]["pnl"]
        per_strat[s]["matched_b"] += bt[bi]["pnl"]
        per_strat[s]["n_match"]   += 1
    for t in live_only:
        per_strat[t["strat"]]["live_only"] += t["pnl"]
        per_strat[t["strat"]]["n_lo"] += 1
    for t in bt_only:
        per_strat[t["strat"]]["bt_only"] += t["pnl"]
        per_strat[t["strat"]]["n_bo"] += 1
    print(f"  {'Strat':<6} {'n_m':>4} {'matched_d':>10} {'n_LO':>5} {'live_only':>13} {'n_BO':>5} {'bt_only':>11} {'total':>10}")
    for s in sorted(per_strat):
        v = per_strat[s]
        mD = v["matched_l"] - v["matched_b"]
        total = mD + v["live_only"] - v["bt_only"]
        print(f"  {s:<6} {v['n_match']:>4} {mD:>+10.2f} {v['n_lo']:>5} {v['live_only']:>+13.2f} {v['n_bo']:>5} {v['bt_only']:>+11.2f} {total:>+10.2f}")


if __name__ == "__main__":
    main()
