"""Compare two BACKTEST_TRADE_DUMP files trade-by-trade (Alfred phase 1
iso-result validation).

Usage:
    python3 -m backtests.compare_trade_dumps REF.json NEW.json [--eps 0.01]

Reason-label mapping: the legacy engine labels the catastrophe stop "stop";
the shared rules use "catastrophe_stop". Both normalize to "catastrophe_stop".
"""

from __future__ import annotations

import json
import sys

REASON_MAP = {"stop": "catastrophe_stop"}


def norm_reason(r: str) -> str:
    return REASON_MAP.get(r, r)


def key(w: dict) -> tuple:
    return (w["label"], w["capital"])


def trade_sig(t: dict) -> tuple:
    return (t["coin"], t["strat"], t["dir"], t["entry_t"], t["exit_t"],
            norm_reason(t["reason"]))


def main() -> int:
    ref_path, new_path = sys.argv[1], sys.argv[2]
    eps = 0.01
    if "--eps" in sys.argv:
        eps = float(sys.argv[sys.argv.index("--eps") + 1])

    ref = {key(w): w for w in json.load(open(ref_path))}
    new = {key(w): w for w in json.load(open(new_path))}

    if set(ref) != set(new):
        print(f"WINDOW MISMATCH: only-ref={set(ref) - set(new)} only-new={set(new) - set(ref)}")
        return 1

    total_diff = 0
    for k in sorted(ref, key=str):
        rw, nw = ref[k], new[k]
        diffs = []
        cap_delta = abs(rw["end_capital"] - nw["end_capital"])
        if cap_delta > eps:
            diffs.append(f"end_capital {rw['end_capital']:.2f} vs {nw['end_capital']:.2f} (Δ{cap_delta:.4f})")
        if abs(rw["max_dd_pct"] - nw["max_dd_pct"]) > 1e-6:
            diffs.append(f"max_dd {rw['max_dd_pct']:.4f} vs {nw['max_dd_pct']:.4f}")
        rt, nt = rw["trades"], nw["trades"]
        if len(rt) != len(nt):
            diffs.append(f"n_trades {len(rt)} vs {len(nt)}")
        n = min(len(rt), len(nt))
        first_mismatch = None
        for i in range(n):
            if trade_sig(rt[i]) != trade_sig(nt[i]):
                first_mismatch = i
                break
            if (abs(rt[i]["pnl"] - nt[i]["pnl"]) > eps
                    or abs(rt[i]["size"] - nt[i]["size"]) > eps):
                first_mismatch = i
                break
        if first_mismatch is not None:
            i = first_mismatch
            diffs.append(f"first trade mismatch at #{i}:\n"
                         f"    ref: {rt[i]}\n    new: {nt[i]}")
        if diffs:
            total_diff += 1
            print(f"✗ {k[0]} (${k[1]:.0f})")
            for d in diffs:
                print(f"  - {d}")
        else:
            print(f"✓ {k[0]} (${k[1]:.0f}) — {len(rt)} trades, end ${rw['end_capital']:.2f}")

    print(f"\n{len(ref) - total_diff}/{len(ref)} fenêtres identiques (eps=${eps})")
    return 0 if total_diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
