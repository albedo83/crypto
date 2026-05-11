"""Retrospective analysis of observation-only entry features.

Read-only. Does NOT modify the bot or the database.

For each of 4 features logged at entry since the early bot versions
(entry_oi_delta, entry_crowding, entry_confluence, entry_session),
bucket the 87 live closed trades and compute:
- avg pnl per bucket
- win rate per bucket
- breakdown by strategy (cells with n >= 5)
- comparison live vs junior as a cross-check

Outputs a per-feature verdict:
- SIGNAL — a bucket sub-performs/over-performs with |avg pnl| ≥ $2 and n ≥ 10
- WEAK — directional pattern but n < 10 or |Δ| < $2
- NONE — no pattern, the feature is noise

Run: python3 -m backtests.analyze_obs_features
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict

DBS = [
    ("live", "/home/crypto/analysis/output_live/reversal_ticks.db"),
    ("junior", "/home/crypto/analysis/output_live2/reversal_ticks.db"),
]

# Signal thresholds
SIGNAL_MIN_BUCKET_PNL_USD = 2.0
SIGNAL_MIN_BUCKET_N = 10
WEAK_MIN_PNL_USD = 1.0


def _coerce_int(v) -> int:
    """Some columns were stored as np.int64 BLOB before v11.5.x cast fix.
    Decode 8-byte little-endian if needed, else fall back to int()."""
    if v is None or v == "":
        return 0
    if isinstance(v, (bytes, bytearray)) and len(v) == 8:
        return int.from_bytes(v, "little", signed=True)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def fetch_trades(db_path: str) -> list[dict]:
    """All bot-decision closed trades with structured entry context."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT symbol, direction, strategy, entry_time, exit_time,
               entry_oi_delta, entry_crowding, entry_confluence, entry_session,
               pnl_usdt, mae_bps, mfe_bps, reason
        FROM trades
        WHERE exit_time IS NOT NULL
          AND reason NOT IN ('manual_stop', 'reset', 'manual_close')
        ORDER BY entry_time
    """).fetchall()
    out = []
    for r in rows:
        out.append({
            "symbol": r[0], "direction": r[1], "strategy": r[2] or "?",
            "entry_time": r[3], "exit_time": r[4],
            "oi_delta": float(r[5] or 0),
            "crowding": _coerce_int(r[6]),
            "confluence": _coerce_int(r[7]),
            "session": r[8] or "?",
            "pnl_usdt": float(r[9] or 0),
            "mae_bps": float(r[10] or 0), "mfe_bps": float(r[11] or 0),
            "reason": r[12] or "?",
        })
    return out


def bucket_oi(v: float) -> str:
    if v < -2:
        return "oi<-2%"
    if v < 0:
        return "oi-2..0%"
    if v < 2:
        return "oi 0..2%"
    return "oi>+2%"


def bucket_crowding(v: int) -> str:
    if v == 0:
        return "crowd=0"
    if v < 20:
        return "crowd 1-20"
    if v < 50:
        return "crowd 20-50"
    return "crowd >=50"


def bucket_confluence(v: int) -> str:
    # Already a discrete int 0..5
    return f"conf={v}"


def bucket_session(v: str) -> str:
    return v or "?"


def safe_mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def report_feature(name: str, trades: list[dict], bucket_fn, field: str) -> dict:
    """Bucket trades by feature, print table, return summary stats."""
    buckets = defaultdict(list)
    for t in trades:
        buckets[bucket_fn(t[field])].append(t)

    print(f"\n--- {name} ---")
    print(f"  {'bucket':<14} {'n':>4}  {'avg pnl':>9}  {'med pnl':>9}  {'WR':>4}  {'avg MAE':>8}  {'avg MFE':>8}")
    summary = {}
    for bk in sorted(buckets.keys()):
        bs = buckets[bk]
        n = len(bs)
        if n == 0:
            continue
        pnls = [t["pnl_usdt"] for t in bs]
        wr = sum(1 for p in pnls if p > 0) / n * 100
        avg_mae = safe_mean([t["mae_bps"] for t in bs])
        avg_mfe = safe_mean([t["mfe_bps"] for t in bs])
        avg_pnl = safe_mean(pnls)
        med_pnl = statistics.median(pnls)
        flag = "*" if n < 5 else " "
        print(f"  {bk:<14} {n:>4}  {avg_pnl:>+8.2f}$ {med_pnl:>+8.2f}$  {wr:>3.0f}%  "
              f"{avg_mae:>+7.0f}  {avg_mfe:>+7.0f}{flag}")
        summary[bk] = {"n": n, "avg_pnl": avg_pnl, "med_pnl": med_pnl, "wr": wr,
                       "avg_mae": avg_mae, "avg_mfe": avg_mfe}
    return summary


def report_per_strat(name: str, trades: list[dict], bucket_fn, field: str,
                      min_n: int = 5) -> None:
    """For each (strategy, bucket) cell with n >= min_n, print stats."""
    cells = defaultdict(list)
    for t in trades:
        cells[(t["strategy"], t["direction"], bucket_fn(t[field]))].append(t)
    big_cells = [(k, v) for k, v in cells.items() if len(v) >= min_n]
    if not big_cells:
        print(f"  (no cell with n >= {min_n} for per-strat breakdown)")
        return
    big_cells.sort(key=lambda kv: safe_mean([t["pnl_usdt"] for t in kv[1]]))
    print(f"  Per-strategy cells (n >= {min_n}):")
    for (strat, dir_, bk), bs in big_cells:
        pnls = [t["pnl_usdt"] for t in bs]
        wr = sum(1 for p in pnls if p > 0) / len(bs) * 100
        print(f"    {strat:<4} {dir_:<5} {bk:<14} n={len(bs):>3}  "
              f"avg={safe_mean(pnls):>+6.2f}$  WR={wr:>3.0f}%")


def verdict(name: str, summary: dict) -> str:
    """Return 'SIGNAL' / 'WEAK' / 'NONE' based on bucket spread."""
    if not summary:
        return "NONE (no data)"
    avg_pnls = [(b, s["avg_pnl"], s["n"]) for b, s in summary.items()]
    avg_pnls.sort(key=lambda x: x[1])
    worst = avg_pnls[0]
    best = avg_pnls[-1]
    spread = best[1] - worst[1]
    # Check if any bucket is extreme enough
    extreme = [x for x in avg_pnls if abs(x[1]) >= SIGNAL_MIN_BUCKET_PNL_USD
               and x[2] >= SIGNAL_MIN_BUCKET_N]
    if extreme and spread >= 2 * SIGNAL_MIN_BUCKET_PNL_USD:
        return f"SIGNAL — spread {spread:+.2f}$ ({worst[0]} → {best[0]})"
    if extreme:
        return f"WEAK — extreme bucket {extreme[0][0]} avg={extreme[0][1]:+.2f}$, spread only {spread:+.2f}$"
    if spread >= 2 * WEAK_MIN_PNL_USD:
        return f"WEAK — spread {spread:+.2f}$ but no bucket reaches |${SIGNAL_MIN_BUCKET_PNL_USD}|"
    return "NONE — buckets ~flat, feature looks like noise"


def analyze_bot(label: str, db_path: str) -> None:
    trades = fetch_trades(db_path)
    print(f"\n{'='*100}")
    print(f"=== {label.upper()}: {len(trades)} bot-decision closed trades ===")
    print(f"{'='*100}")
    if len(trades) < 10:
        print(f"  Too few trades ({len(trades)}) — skipping {label}.")
        return

    # Overall stats baseline
    pnls = [t["pnl_usdt"] for t in trades]
    wr = sum(1 for p in pnls if p > 0) / len(trades) * 100
    print(f"Baseline overall: avg={safe_mean(pnls):+.2f}$  median={statistics.median(pnls):+.2f}$  WR={wr:.0f}%\n")

    # 1. OI delta
    s = report_feature("entry_oi_delta (OI %change 1h before entry)",
                       trades, bucket_oi, "oi_delta")
    report_per_strat("entry_oi_delta", trades, bucket_oi, "oi_delta")
    print(f"  ► verdict: {verdict('oi_delta', s)}")

    # 2. Crowding score
    s = report_feature("entry_crowding (0-100 leverage stress at entry)",
                       trades, bucket_crowding, "crowding")
    report_per_strat("entry_crowding", trades, bucket_crowding, "crowding")
    print(f"  ► verdict: {verdict('crowding', s)}")

    # 3. Confluence
    s = report_feature("entry_confluence (0-5 count of extreme features)",
                       trades, bucket_confluence, "confluence")
    report_per_strat("entry_confluence", trades, bucket_confluence, "confluence")
    print(f"  ► verdict: {verdict('confluence', s)}")

    # 4. Session
    s = report_feature("entry_session (Asia/EU/US/Night/WE)",
                       trades, bucket_session, "session")
    report_per_strat("entry_session", trades, bucket_session, "session")
    print(f"  ► verdict: {verdict('session', s)}")


def main() -> int:
    print("=" * 100)
    print(" Retrospective analysis of observation-only entry features ")
    print("=" * 100)
    print("\nSignal thresholds:")
    print(f"  SIGNAL: bucket with |avg pnl| >= ${SIGNAL_MIN_BUCKET_PNL_USD} AND n >= {SIGNAL_MIN_BUCKET_N}, spread >= ${2 * SIGNAL_MIN_BUCKET_PNL_USD}")
    print(f"  WEAK  : pattern present but not strong enough")
    print(f"  NONE  : noise")

    for label, db_path in DBS:
        analyze_bot(label, db_path)

    print(f"\n{'='*100}")
    print("Summary: re-run after each significant trade count milestone (200, 300, ...)")
    print(f"{'='*100}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
