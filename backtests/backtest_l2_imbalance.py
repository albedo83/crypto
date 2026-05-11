"""Retrospective L2 order book imbalance analysis on live trades.

Read-only. Does NOT modify the bot or the database.

Answers: would a toxicity filter based on order book imbalance (using already-
logged impactPxs at entry time) have improved the 87 closed trades on live?

Run: python3 -m backtests.backtest_l2_imbalance
"""

from __future__ import annotations

import datetime
import sqlite3
import statistics
from collections import defaultdict

DB_PATH = "/home/crypto/analysis/output_live/reversal_ticks.db"

# Window around entry_time to find the closest tick row with valid impactPxs.
# Asymmetric: we want the last tick BEFORE the entry decision (the price the
# bot saw when ranking). Allow a tiny forward slack (30s) for clock drift.
TICK_WINDOW_BACK_S = 180  # 3 min back
TICK_WINDOW_FORWARD_S = 30

# Bucket thresholds for entry_side_imbalance ∈ [0, 1].
# High value = the side we want to enter is thin → unfavorable book.
BUCKET_FAVORABLE_MAX = 0.3
BUCKET_NEUTRE_MAX = 0.6
# défavorable: 0.6 - 1.0

# Signal thresholds for the verdict line.
SIGNAL_MIN_SLIP_DELTA_BPS = 5.0
SIGNAL_MIN_PNL_DELTA_USD = 1.0  # absolute, |Δ| < this counts as no signal
SIGNAL_MIN_DEFAVORABLE_N = 15


def iso_to_epoch(iso_str: str) -> int:
    """Convert '2026-05-10T16:23:35.687104+00:00' to int epoch seconds."""
    return int(datetime.datetime.fromisoformat(iso_str).timestamp())


def fetch_trades(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT symbol, direction, strategy, entry_time, exit_time,
               entry_price, exit_price, pnl_usdt, mae_bps, mfe_bps
        FROM trades
        WHERE exit_time IS NOT NULL AND entry_price > 0
        ORDER BY entry_time
    """).fetchall()
    trades = []
    for r in rows:
        try:
            ts = iso_to_epoch(r[3])
        except Exception:
            continue
        trades.append({
            "symbol": r[0], "direction": r[1], "strategy": r[2] or "?",
            "entry_time": r[3], "exit_time": r[4],
            "entry_price": float(r[5]), "exit_price": float(r[6] or 0),
            "pnl_usdt": float(r[7] or 0),
            "mae_bps": float(r[8] or 0), "mfe_bps": float(r[9] or 0),
            "entry_ts": ts,
        })
    return trades


def find_tick(conn: sqlite3.Connection, symbol: str, ts: int) -> tuple | None:
    """Return (tick_ts, mark, bid, ask) — closest tick to ts with valid bid/ask."""
    row = conn.execute("""
        SELECT ts, mark_px, impact_bid, impact_ask
        FROM ticks
        WHERE symbol = ?
          AND ts BETWEEN ? AND ?
          AND impact_bid IS NOT NULL AND impact_ask IS NOT NULL
          AND impact_bid > 0 AND impact_ask > impact_bid
        ORDER BY ABS(ts - ?)
        LIMIT 1
    """, (symbol, ts - TICK_WINDOW_BACK_S, ts + TICK_WINDOW_FORWARD_S, ts)).fetchone()
    return row


def enrich(trade: dict, tick: tuple) -> dict | None:
    """Compute spread, skew, entry_side_imbalance, observed_slippage. Return
    enriched trade or None if degenerate."""
    tick_ts, mark, bid, ask = tick
    if mark <= 0 or ask <= bid:
        return None
    spread = ask - bid
    spread_bps = spread / mark * 1e4
    # skew ∈ [0, 1]: where the mark sits in [bid, ask]. 0 = at bid, 1 = at ask.
    # Clamp to handle off-segment marks (extremely rare).
    raw_skew = (mark - bid) / spread
    skew = max(0.0, min(1.0, raw_skew))
    # entry_side_imbalance: thinness of the side WE will hit as taker.
    # LONG = buy = takes ask → unfavorable if (impact_ask - mark) is large fraction
    #   of spread → (1 - skew)
    # SHORT = sell = takes bid → unfavorable if (mark - impact_bid) is large
    #   fraction of spread → skew
    if trade["direction"] == "LONG":
        esi = 1.0 - skew
        slip = (trade["entry_price"] - mark) / mark * 1e4
    else:
        esi = skew
        slip = (mark - trade["entry_price"]) / mark * 1e4
    return {
        **trade,
        "tick_ts": tick_ts, "mark": mark, "bid": bid, "ask": ask,
        "spread_bps": spread_bps, "skew": skew,
        "entry_side_imbalance": esi,
        "observed_slippage_bps": slip,
    }


def bucket_of(esi: float) -> str:
    if esi < BUCKET_FAVORABLE_MAX:
        return "favorable"
    if esi < BUCKET_NEUTRE_MAX:
        return "neutre"
    return "défavorable"


def safe_mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def safe_median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def p90(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = max(0, min(len(s) - 1, int(0.9 * (len(s) - 1))))
    return s[idx]


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    trades = fetch_trades(conn)
    print(f"=== L2 imbalance retrospective ({len(trades)} closed trades from live) ===\n")

    enriched: list[dict] = []
    skipped_no_tick = 0
    skipped_degenerate = 0
    for t in trades:
        tick = find_tick(conn, t["symbol"], t["entry_ts"])
        if tick is None:
            skipped_no_tick += 1
            continue
        e = enrich(t, tick)
        if e is None:
            skipped_degenerate += 1
            continue
        enriched.append(e)

    print(f"Enriched: {len(enriched)}/{len(trades)} trades")
    print(f"  skipped (no tick in window ±{TICK_WINDOW_BACK_S}s/{TICK_WINDOW_FORWARD_S}s): {skipped_no_tick}")
    print(f"  skipped (degenerate book): {skipped_degenerate}\n")

    if len(enriched) < 10:
        print("Too few enriched trades for statistical analysis. Exiting.")
        return 1

    # Bucket
    buckets: dict[str, list[dict]] = defaultdict(list)
    for e in enriched:
        buckets[bucket_of(e["entry_side_imbalance"])].append(e)

    # Print bucket table
    headers = ["Bucket", "n", "slip_avg", "slip_med", "pnl_avg", "pnl_med", "WR",
               "MAE_avg", "MFE_avg", "spread_med", "spread_p90"]
    print(f"{headers[0]:<14} {headers[1]:>4} {headers[2]:>9} {headers[3]:>9} "
          f"{headers[4]:>8} {headers[5]:>8} {headers[6]:>5} "
          f"{headers[7]:>8} {headers[8]:>8} {headers[9]:>10} {headers[10]:>10}")
    bucket_order = ["favorable", "neutre", "défavorable"]
    bucket_stats: dict[str, dict] = {}
    for name in bucket_order:
        bs = buckets.get(name, [])
        slips = [e["observed_slippage_bps"] for e in bs]
        pnls = [e["pnl_usdt"] for e in bs]
        maes = [e["mae_bps"] for e in bs]
        mfes = [e["mfe_bps"] for e in bs]
        spreads = [e["spread_bps"] for e in bs]
        wr = (sum(1 for p in pnls if p > 0) / len(pnls) * 100) if pnls else 0.0
        stats = {
            "n": len(bs),
            "slip_avg": safe_mean(slips), "slip_med": safe_median(slips),
            "pnl_avg": safe_mean(pnls), "pnl_med": safe_median(pnls),
            "wr_pct": wr,
            "mae_avg": safe_mean(maes), "mfe_avg": safe_mean(mfes),
            "spread_med": safe_median(spreads), "spread_p90": p90(spreads),
        }
        bucket_stats[name] = stats
        flag = " ⚠ low confidence" if stats["n"] < 5 else ""
        print(f"{name:<14} {stats['n']:>4} {stats['slip_avg']:>+8.1f}b "
              f"{stats['slip_med']:>+8.1f}b "
              f"{stats['pnl_avg']:>+7.2f}$ {stats['pnl_med']:>+7.2f}$ "
              f"{stats['wr_pct']:>4.0f}% "
              f"{stats['mae_avg']:>+8.0f} {stats['mfe_avg']:>+8.0f} "
              f"{stats['spread_med']:>9.1f}b {stats['spread_p90']:>9.1f}b{flag}")
    print()

    # Per-strategy breakdown (only buckets with >=10 in same strategy)
    print("--- Per-strategy breakdown (strat, bucket, n, slip_avg, pnl_avg, WR) ---")
    by_strat: dict[tuple, list[dict]] = defaultdict(list)
    for e in enriched:
        by_strat[(e["strategy"], bucket_of(e["entry_side_imbalance"]))].append(e)
    for (strat, bk), trs in sorted(by_strat.items()):
        if len(trs) < 5:
            continue
        slips = [e["observed_slippage_bps"] for e in trs]
        pnls = [e["pnl_usdt"] for e in trs]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        print(f"  {strat:<4} {bk:<14} n={len(trs):>3} slip={safe_mean(slips):+5.1f}b "
              f"pnl={safe_mean(pnls):+6.2f}$ WR={wr:.0f}%")
    print()

    # Verdict
    fav = bucket_stats.get("favorable", {})
    def_ = bucket_stats.get("défavorable", {})
    if fav.get("n", 0) == 0 or def_.get("n", 0) == 0:
        print("VERDICT: INCONCLUSIVE — one of the extreme buckets is empty.")
        return 0
    slip_delta = def_["slip_avg"] - fav["slip_avg"]
    pnl_delta = def_["pnl_avg"] - fav["pnl_avg"]
    wr_delta = def_["wr_pct"] - fav["wr_pct"]
    print(f"Δ défavorable - favorable: slippage {slip_delta:+.1f} bps, "
          f"pnl {pnl_delta:+.2f}$, WR {wr_delta:+.0f}pp")

    signal = (
        abs(slip_delta) > SIGNAL_MIN_SLIP_DELTA_BPS
        and pnl_delta < -SIGNAL_MIN_PNL_DELTA_USD
        and def_["n"] >= SIGNAL_MIN_DEFAVORABLE_N
    )
    if signal:
        print("\nVERDICT: SIGNAL PRESENT — entries in unfavorable books underperform.")
        print(f"  Δ slippage {slip_delta:+.1f} bps > {SIGNAL_MIN_SLIP_DELTA_BPS} bps threshold")
        print(f"  Δ pnl {pnl_delta:+.2f}$ < -{SIGNAL_MIN_PNL_DELTA_USD}$ threshold")
        print(f"  n défavorable = {def_['n']} (>= {SIGNAL_MIN_DEFAVORABLE_N})")
        print("Next step: test gate `skip entry if entry_side_imbalance > 0.7` ")
        print("           in backtests/backtest_rolling.py with walk-forward 4/4 strict.")
    else:
        reasons = []
        if abs(slip_delta) <= SIGNAL_MIN_SLIP_DELTA_BPS:
            reasons.append(f"Δslip {slip_delta:+.1f}b ≤ {SIGNAL_MIN_SLIP_DELTA_BPS}b")
        if pnl_delta >= -SIGNAL_MIN_PNL_DELTA_USD:
            reasons.append(f"Δpnl {pnl_delta:+.2f}$ ≥ -{SIGNAL_MIN_PNL_DELTA_USD}$")
        if def_["n"] < SIGNAL_MIN_DEFAVORABLE_N:
            reasons.append(f"n_défavorable {def_['n']} < {SIGNAL_MIN_DEFAVORABLE_N}")
        print("\nVERDICT: NO SIGNAL — book imbalance at entry not predictive at current scale.")
        print(f"  Reason(s): {', '.join(reasons)}")
        print("  Close ticket. Revisit at ~$15k capital (slippage_ceiling memory).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
