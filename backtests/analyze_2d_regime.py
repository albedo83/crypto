"""Retrospective 2D regime analysis on live trades.

Read-only. Does NOT modify the bot or the database.

Answers: does the pnl of the 87 live closed trades cluster better in a 2D
grid (BTC trend × cross-sectional dispersion) than in a 1D grid (BTC trend
alone) ? If yes → motivation for a 2D modulator (Option 1, separate plan).
If no → close the ticket, the 1D btc_z modulator is the right granularity.

Run: python3 -m backtests.analyze_2d_regime

Caveats:
- The bot's actual modulator uses btc_z (rolling z-score of 30d return on
  180d window). We only have ~46 days of market_snapshots, not enough for
  the 180d window. Instead this script uses raw BTC 7d return and raw
  disp_24h — same regime axes in spirit, simpler thresholds.
- Cross-sectional dispersion mirrors signals.compute_cross_context: std
  across all tracked alts of their (price_now / price_24h_ago - 1).
"""

from __future__ import annotations

import datetime
import sqlite3
import statistics
from collections import defaultdict

DB_PATH = "/home/crypto/analysis/output_live/reversal_ticks.db"

# Lookback windows (in seconds)
BTC_TREND_LOOKBACK_S = 7 * 86400        # 7d BTC return
DISP_LOOKBACK_S = 24 * 3600              # 24h cross-sectional dispersion

# Bucket boundaries
# BTC 7d return in bps. <-300 ≈ falling, -300..300 ≈ neutral, >300 ≈ rising.
BTC_LOW, BTC_HIGH = -300, 300
# Cross-sectional std of 24h returns (bps). <200 ≈ tight, >500 ≈ fragmented.
DISP_LOW, DISP_HIGH = 200, 500

# Verdict thresholds
SIGNAL_MIN_CELL_PNL_USD = 2.0       # |avg pnl| > $2 in a cell
SIGNAL_MIN_CELL_N = 5                # cell needs ≥ 5 trades to count
LOW_CONFIDENCE_N = 5


def iso_to_epoch(iso_str: str) -> int:
    return int(datetime.datetime.fromisoformat(iso_str).timestamp())


def fetch_trades(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT symbol, direction, strategy, entry_time, exit_time,
               pnl_usdt, mae_bps, mfe_bps
        FROM trades
        WHERE exit_time IS NOT NULL
        ORDER BY entry_time
    """).fetchall()
    out = []
    for r in rows:
        try:
            ts = iso_to_epoch(r[3])
        except Exception:
            continue
        out.append({
            "symbol": r[0], "direction": r[1], "strategy": r[2] or "?",
            "entry_time": r[3], "entry_ts": ts,
            "pnl_usdt": float(r[5] or 0),
            "mae_bps": float(r[6] or 0), "mfe_bps": float(r[7] or 0),
        })
    return out


def find_price_at(conn: sqlite3.Connection, symbol: str, ts: int,
                  window_s: int = 7200) -> float | None:
    """Closest market_snapshots price (alts) to ts, within ±window_s seconds.
    Returns None if no snapshot is available in that window."""
    row = conn.execute("""
        SELECT price FROM market_snapshots
        WHERE symbol = ? AND ts BETWEEN ? AND ?
        ORDER BY ABS(ts - ?) LIMIT 1
    """, (symbol, ts - window_s, ts + window_s, ts)).fetchone()
    return float(row[0]) if row and row[0] else None


def find_btc_at(conn: sqlite3.Connection, ts: int,
                window_s: int = 7200) -> float | None:
    """Closest BTC mark_px from ticks table to ts (BTC isn't in
    market_snapshots — only alts are)."""
    row = conn.execute("""
        SELECT mark_px FROM ticks
        WHERE symbol = 'BTC' AND ts BETWEEN ? AND ?
        ORDER BY ABS(ts - ?) LIMIT 1
    """, (ts - window_s, ts + window_s, ts)).fetchone()
    return float(row[0]) if row and row[0] else None


def compute_btc_7d_bps(conn: sqlite3.Connection, entry_ts: int) -> float | None:
    """BTC 7d return in bps at entry_ts."""
    p_now = find_btc_at(conn, entry_ts)
    p_then = find_btc_at(conn, entry_ts - BTC_TREND_LOOKBACK_S)
    if p_now is None or p_then is None or p_then <= 0:
        return None
    return (p_now / p_then - 1) * 1e4


def compute_disp_24h(conn: sqlite3.Connection, entry_ts: int,
                     tokens: list[str]) -> float | None:
    """Cross-sectional std of 24h returns across tokens at entry_ts.
    Returns bps. None if too few tokens have data."""
    rets = []
    for sym in tokens:
        p_now = find_price_at(conn, sym, entry_ts)
        p_then = find_price_at(conn, sym, entry_ts - DISP_LOOKBACK_S)
        if p_now is None or p_then is None or p_then <= 0:
            continue
        rets.append((p_now / p_then - 1) * 1e4)
    if len(rets) < 10:  # need enough alts to compute meaningful std
        return None
    return float(statistics.stdev(rets))


def bucket_btc(v: float | None) -> str:
    if v is None:
        return "?"
    if v < BTC_LOW:
        return "btc<"
    if v > BTC_HIGH:
        return "btc>"
    return "btc~"


def bucket_disp(v: float | None) -> str:
    if v is None:
        return "?"
    if v < DISP_LOW:
        return "disp<"
    if v > DISP_HIGH:
        return "disp>"
    return "disp~"


def safe_mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    trades = fetch_trades(conn)
    print(f"=== 2D regime retrospective ({len(trades)} closed trades) ===\n")

    # Collect tokens to use for cross-sectional dispersion
    tokens = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM market_snapshots").fetchall()]
    tokens = [t for t in tokens if t != "BTC"]
    print(f"Tokens available for cross-sectional dispersion: {len(tokens)}\n")

    enriched: list[dict] = []
    skipped_no_btc = 0
    skipped_no_disp = 0
    for t in trades:
        btc_7d = compute_btc_7d_bps(conn, t["entry_ts"])
        if btc_7d is None:
            skipped_no_btc += 1
            continue
        disp = compute_disp_24h(conn, t["entry_ts"], tokens)
        if disp is None:
            skipped_no_disp += 1
            continue
        enriched.append({**t, "btc_7d_bps": btc_7d, "disp_24h": disp})

    print(f"Enriched: {len(enriched)}/{len(trades)} trades")
    print(f"  skipped (no BTC reference price in market_snapshots): {skipped_no_btc}")
    print(f"  skipped (insufficient cross-sectional data): {skipped_no_disp}\n")

    if len(enriched) < 20:
        print("Too few enriched trades for analysis. Exiting.")
        return 1

    # Distribution sanity
    btc_vals = [e["btc_7d_bps"] for e in enriched]
    disp_vals = [e["disp_24h"] for e in enriched]
    print(f"BTC 7d distribution (bps): min={min(btc_vals):.0f}, p25={sorted(btc_vals)[len(btc_vals)//4]:.0f}, "
          f"median={statistics.median(btc_vals):.0f}, p75={sorted(btc_vals)[3*len(btc_vals)//4]:.0f}, max={max(btc_vals):.0f}")
    print(f"Disp 24h distribution (bps): min={min(disp_vals):.0f}, p25={sorted(disp_vals)[len(disp_vals)//4]:.0f}, "
          f"median={statistics.median(disp_vals):.0f}, p75={sorted(disp_vals)[3*len(disp_vals)//4]:.0f}, max={max(disp_vals):.0f}\n")

    # ── 2D Grid ──
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for e in enriched:
        cells[(bucket_btc(e["btc_7d_bps"]), bucket_disp(e["disp_24h"]))].append(e)

    print(f"--- 2D grid (btc_7d × disp_24h), avg pnl_usdt | n trades | WR ---")
    btc_buckets = ["btc<", "btc~", "btc>"]
    disp_buckets = ["disp<", "disp~", "disp>"]
    # Header
    header = " " * 14 + "  ".join(f"{b:>14}" for b in disp_buckets)
    print(header)
    for bb in btc_buckets:
        line = f"{bb:<14}"
        for db in disp_buckets:
            cell = cells.get((bb, db), [])
            n = len(cell)
            if n == 0:
                line += f"  {'—':>14}"
            else:
                pnl = safe_mean([e["pnl_usdt"] for e in cell])
                wr = sum(1 for e in cell if e["pnl_usdt"] > 0) / n * 100
                flag = "*" if n < LOW_CONFIDENCE_N else " "
                line += f"  {pnl:+5.2f}$ n={n:>2} {wr:>3.0f}%{flag}"
        print(line)
    print("  (* = low confidence, n < 5)\n")

    # ── 1D Baseline ──
    print("--- 1D baseline (btc_7d only) ---")
    by_btc: dict[str, list[dict]] = defaultdict(list)
    for e in enriched:
        by_btc[bucket_btc(e["btc_7d_bps"])].append(e)
    for bb in btc_buckets:
        cell = by_btc.get(bb, [])
        n = len(cell)
        if n == 0:
            print(f"  {bb:<8} : —")
            continue
        pnl = safe_mean([e["pnl_usdt"] for e in cell])
        wr = sum(1 for e in cell if e["pnl_usdt"] > 0) / n * 100
        print(f"  {bb:<8} : avg={pnl:+5.2f}$ n={n:>2} WR={wr:.0f}%")
    print()

    # ── Per-strategy breakdown (only cells with n>=5) ──
    print("--- Per-strategy 2D cells (n>=5) ---")
    by_strat: dict[tuple, list[dict]] = defaultdict(list)
    for e in enriched:
        key = (e["strategy"], e["direction"],
               bucket_btc(e["btc_7d_bps"]), bucket_disp(e["disp_24h"]))
        by_strat[key].append(e)
    for key, group in sorted(by_strat.items()):
        if len(group) < 5:
            continue
        strat, dir_, bb, db = key
        pnl = safe_mean([e["pnl_usdt"] for e in group])
        wr = sum(1 for e in group if e["pnl_usdt"] > 0) / len(group) * 100
        print(f"  {strat:<4} {dir_:<5} {bb:<6} {db:<7} n={len(group):>2} avg={pnl:+5.2f}$ WR={wr:.0f}%")
    print()

    # ── Verdict ──
    # Look for any cell with |avg pnl| > threshold AND n >= threshold
    strong_cells = []
    for (bb, db), cell in cells.items():
        n = len(cell)
        if n < SIGNAL_MIN_CELL_N:
            continue
        avg = safe_mean([e["pnl_usdt"] for e in cell])
        if abs(avg) >= SIGNAL_MIN_CELL_PNL_USD:
            strong_cells.append(((bb, db), n, avg))

    # Also check: at btc_z constant, does disp_z change the answer ?
    disp_effect = False
    for bb in btc_buckets:
        cells_in_row = [(db, cells.get((bb, db), [])) for db in disp_buckets]
        cells_in_row = [(db, c) for db, c in cells_in_row if len(c) >= SIGNAL_MIN_CELL_N]
        if len(cells_in_row) < 2:
            continue
        pnls = [safe_mean([e["pnl_usdt"] for e in c]) for _, c in cells_in_row]
        if max(pnls) - min(pnls) >= 2 * SIGNAL_MIN_CELL_PNL_USD:
            disp_effect = True

    if strong_cells and disp_effect:
        print("VERDICT: SIGNAL PRESENT — 2D adds information beyond btc_7d alone.")
        for (bb, db), n, avg in sorted(strong_cells, key=lambda x: -abs(x[2])):
            print(f"  {bb} × {db}: n={n}, avg pnl ${avg:+.2f}")
        print("Next step: prototyper modulator 2D (mult = 1 + α×btc_z + β×disp_z),")
        print("           valider walk-forward 4/4 strict via backtest_rolling.py.")
    else:
        reasons = []
        if not strong_cells:
            reasons.append(f"aucune cellule avec |avg pnl| > ${SIGNAL_MIN_CELL_PNL_USD} et n >= {SIGNAL_MIN_CELL_N}")
        if not disp_effect:
            reasons.append("disp_24h ne contribue pas significativement à btc_7d constant")
        print("VERDICT: NO SIGNAL — la grille 2D ne fait pas mieux que la 1D.")
        for r in reasons:
            print(f"  • {r}")
        print("Close ticket. Log dans BACKLOG.md, revisiter à 200+ trades fermés.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
