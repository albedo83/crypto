"""One-shot equity realign tool.

Aligns the bot's persisted `_total_pnl` to the exchange truth
(`closed_pnl + funding_paid - taker_fees` on 90d window) and records
an `EQUITY_REALIGN` event for audit.

WHY THIS EXISTS
---------------
The v12.5.25 fix (2026-05-13) corrected a P&L over-recording bug on
winners and under-recording on losers (close-time notional was used
instead of open notional in the bps × size formula). All trades after
v12.5.25 record correctly, but the historical aggregate ~$7 of bias
remains in `bot._total_pnl`. EQUITY_DRIFT alert continues to fire on
the residue. This tool resets the aggregate to match exchange truth.

USAGE
-----
The bot for the target output directory MUST be stopped before this
runs (the tool checks the port is unbound; if not, it aborts).

    # Stop live first:  fuser -k 8098/tcp
    .venv/bin/python3 -m analysis.equity_realign --live    # or --paper, --junior

Effect:
- state.json: `total_pnl` overwritten with exchange truth
- state.json: `_pnl_realign_offset` accumulates the signed delta
- ticks.db events table: new EQUITY_REALIGN row with full breakdown
- `trades` table is NOT modified (audit-trail integrity)

After the tool runs, restart the bot — the new boot will load the
realigned `_total_pnl` and the offset, and the startup-drift-check
will subtract the offset before warning (no spurious P&L DRIFT log).

LIMITATIONS
-----------
- The realign captures the SNAPSHOT at run-time. New trades between
  the script and bot restart will be added to `bot._total_pnl` in
  memory and persisted on the next save — these are post-realign and
  do not affect the offset semantics. Safe.
- Funding/fees beyond the 90d HL window are NOT in the realign. If
  the wallet has been active >90 days, residue can persist.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sqlite3
import sys
import time
from urllib.request import Request, urlopen


BOTS = {
    "paper":  ("analysis/output",       8097, None,                 None),
    "live":   ("analysis/output_live",  8098, "HL_PRIVATE_KEY",     None),
    "junior": ("analysis/output_live2", 8099, "JUNIOR_HL_PRIVATE_KEY",
               "0xb65d5e52f229B1dAA6534034d7805A82dB7956Fe"),
}


def load_env(path: str = ".env") -> dict:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def post_hl(body: dict) -> object:
    req = Request("https://api.hyperliquid.xyz/info",
                  data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    return json.load(urlopen(req, timeout=15))


def realign(label: str, output_dir: str, addr: str) -> None:
    state_path = os.path.join(output_dir, "reversal_state.json")
    db_path = os.path.join(output_dir, "reversal_ticks.db")

    if not os.path.exists(state_path):
        sys.exit(f"state file not found: {state_path}")

    with open(state_path) as f:
        state = json.load(f)

    old_total_pnl = float(state.get("total_pnl", 0))
    old_offset = float(state.get("_pnl_realign_offset", 0))
    capital = float(state.get("capital", 0))
    old_peak = float(state.get("peak_balance", capital))

    # Fetch exchange truth on 90d window.
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 90 * 86400 * 1000

    fills = post_hl({"type": "userFillsByTime", "user": addr,
                     "startTime": start_ms, "endTime": now_ms})
    funding = post_hl({"type": "userFunding", "user": addr,
                       "startTime": start_ms, "endTime": now_ms})

    closed_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
    taker_fees = sum(float(f.get("fee", 0)) for f in fills)
    funding_paid = sum(float(f["delta"]["usdc"]) for f in funding)
    exch_realized = closed_pnl + funding_paid - taker_fees

    # The signed delta we apply to `_total_pnl`. Add to running offset.
    delta = exch_realized - old_total_pnl
    new_offset = old_offset + delta
    new_balance = capital + exch_realized

    # Conservative peak: only rebase if new balance is higher than recorded
    # peak (which it usually won't be if delta is negative). Otherwise keep
    # the peak so drawdown reflects the corrected, generally-deeper trough.
    new_peak = max(old_peak, new_balance)

    print(f"\n=== Realign {label.upper()} ({output_dir}) ===")
    print(f"  Wallet: {addr}")
    print(f"  Fills 90d:  {len(fills):>5d}  closed_pnl=${closed_pnl:+8.2f}  taker_fees=${taker_fees:>7.2f}")
    print(f"  Funding 90d:{len(funding):>5d}  funding_paid=${funding_paid:+8.2f}")
    print(f"  exch_realized = closed_pnl + funding_paid - taker_fees = ${exch_realized:+.2f}")
    print(f"")
    print(f"  Old state.json:")
    print(f"    capital              = ${capital:.2f}")
    print(f"    total_pnl            = ${old_total_pnl:+.2f}")
    print(f"    balance              = ${capital + old_total_pnl:.2f}")
    print(f"    peak_balance         = ${old_peak:.2f}")
    print(f"    _pnl_realign_offset  = ${old_offset:+.2f}")
    print(f"")
    print(f"  Δ to apply: ${delta:+.2f}")
    print(f"")
    print(f"  New state.json:")
    print(f"    total_pnl            = ${exch_realized:+.2f}")
    print(f"    balance              = ${new_balance:.2f}")
    print(f"    peak_balance         = ${new_peak:.2f}")
    print(f"    _pnl_realign_offset  = ${new_offset:+.2f}")

    # Apply
    state["total_pnl"] = round(exch_realized, 2)
    state["peak_balance"] = round(new_peak, 2)
    state["_pnl_realign_offset"] = round(new_offset, 4)

    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, state_path)
    print(f"\n  state.json: written.")

    # Audit event
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?, ?, ?, ?)",
            (int(time.time()), "EQUITY_REALIGN", None,
             json.dumps({
                 "old_total_pnl": round(old_total_pnl, 2),
                 "new_total_pnl": round(exch_realized, 2),
                 "delta_applied": round(delta, 2),
                 "running_offset": round(new_offset, 4),
                 "exch_closed_pnl": round(closed_pnl, 2),
                 "exch_taker_fees": round(taker_fees, 2),
                 "exch_funding_paid": round(funding_paid, 2),
                 "n_fills_90d": len(fills),
                 "n_funding_entries_90d": len(funding),
                 "old_peak_balance": round(old_peak, 2),
                 "new_peak_balance": round(new_peak, 2),
                 "tool_version": "12.6.1",
                 "wallet": addr,
             })))
        conn.commit()
        conn.close()
        print(f"  events table: EQUITY_REALIGN row appended.")
    else:
        print(f"  WARN: {db_path} not found, audit event SKIPPED.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper",  action="store_const", dest="bot", const="paper")
    g.add_argument("--live",   action="store_const", dest="bot", const="live")
    g.add_argument("--junior", action="store_const", dest="bot", const="junior")
    parser.add_argument("--force-running", action="store_true",
                        help="Skip the port-in-use check (DANGEROUS — bot will overwrite the realign on next save).")
    args = parser.parse_args()

    output_rel, port, key_env, master_addr = BOTS[args.bot]
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", output_rel.split("/", 1)[1] if "/" in output_rel else output_rel)
    output_dir = os.path.abspath(output_dir)

    if port_in_use(port) and not args.force_running:
        sys.exit(f"ERROR: port {port} is in use — stop the {args.bot} bot first "
                 f"(`fuser -k {port}/tcp`). Use --force-running to override.")

    if args.bot == "paper":
        sys.exit("Paper mode has no exchange truth to realign against. Aborting.")

    env = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    private_key = env.get(key_env or "")
    if not private_key:
        sys.exit(f"ERROR: {key_env} not in .env")

    if master_addr:
        addr = master_addr  # junior — funds on master, signer is agent
    else:
        from eth_account import Account
        addr = Account.from_key(private_key).address

    realign(args.bot, output_dir, addr)
    print("\nDONE. Restart the bot to load the realigned state.\n")


if __name__ == "__main__":
    main()
