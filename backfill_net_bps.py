"""One-shot: recalculate net_bps for all trades so pnl = size × net_bps / 1e4
holds as an invariant. Useful after funding backfill (which adjusted pnl_usdt
but left net_bps at its pre-backfill value).

Semantic: net_bps after this script represents the TRUE net-of-all-costs
basis-points return, including funding — not the flat-model net bps.

Idempotent: running twice produces the same result.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time

DB_PATH = "/home/crypto/analysis/output_live/reversal_ticks.db"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    dry = not args.apply

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("""
        SELECT id, symbol, direction, size_usdt, pnl_usdt, net_bps
        FROM trades ORDER BY exit_time
    """).fetchall())
    print(f"Loaded {len(rows)} trades")

    corrections = []
    for r in rows:
        if r["size_usdt"] <= 0:
            continue
        new_net = r["pnl_usdt"] * 1e4 / r["size_usdt"]
        old_net = r["net_bps"]
        delta = new_net - old_net
        if abs(delta) < 0.05:  # <0.05 bps, not worth updating
            continue
        corrections.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "old_net": round(old_net, 1),
            "new_net": round(new_net, 1),
            "delta": round(delta, 1),
            "size": r["size_usdt"],
            "pnl": r["pnl_usdt"],
        })

    print(f"{'ID':>4}  {'Sym':<6}  {'Size':>8}  {'PnL':>8}  {'OldNet':>8}  {'NewNet':>8}  {'Δ':>7}")
    for c in corrections:
        print(f"  {c['id']:>3}  {c['symbol']:<6}  {c['size']:>7.2f}  {c['pnl']:>+7.2f}  "
              f"{c['old_net']:>+8.1f}  {c['new_net']:>+8.1f}  {c['delta']:>+7.1f}")
    print(f"\n{len(corrections)} trades will be updated (threshold |Δ| >= 0.05 bps)")

    if dry:
        print("\nDRY-RUN — no changes. Re-run with --apply.")
        return 0

    # Backup
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = f"{DB_PATH}.bak-netbps-{ts}"
    shutil.copy2(DB_PATH, bak)
    print(f"\nBackup: {bak}")

    for c in corrections:
        con.execute("UPDATE trades SET net_bps = ? WHERE id = ?",
                    (c["new_net"], c["id"]))
    con.commit()
    con.close()
    print(f"Updated {len(corrections)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
