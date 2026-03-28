"""Session Effects Backtest — Do altcoins have systematic intra-day biases?

Concept: if alts systematically sell during US session and recover during Asia,
we can enter at a specific time and exit at another.

Sessions (UTC):
  Asia:  00:00-08:00 (candles 00:00, 04:00)
  EU:    08:00-16:00 (candles 08:00, 12:00)
  US:    16:00-24:00 (candles 16:00, 20:00)

Data: 4h candles (3 years, 28 tokens)

Usage:
    python3 -m analysis.backtest_sessions
"""

from __future__ import annotations

import json, os, random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from analysis.backtest_genetic import (
    load_3y_candles, TOKENS, COST_BPS, POSITION_SIZE,
    TRAIN_END, TEST_START,
)

SESSIONS = {
    0: "Asia", 4: "Asia",
    8: "EU", 12: "EU",
    16: "US", 20: "US",
}

SESSION_NAMES = ["Asia", "EU", "US"]


def classify_candles(data):
    """Add session label and return per candle to each candle."""
    enriched = {}
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        entries = []
        for i in range(1, len(candles)):
            c = candles[i]
            prev = candles[i - 1]
            t = c["t"]
            dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
            hour = dt.hour
            session = SESSIONS.get(hour, "?")
            ret_bps = (c["c"] / prev["c"] - 1) * 1e4 if prev["c"] > 0 else 0

            entries.append({
                "t": t, "hour": hour, "session": session,
                "weekday": dt.weekday(),  # 0=Mon, 6=Sun
                "open": c["o"], "close": c["c"],
                "ret_bps": ret_bps,
                "idx": i,
            })
        enriched[coin] = entries
    return enriched


def analyze_session_bias(enriched, period="all"):
    """Compute average return per session per token."""
    results = defaultdict(lambda: defaultdict(list))

    for coin, entries in enriched.items():
        for e in entries:
            if period == "train" and e["t"] >= TRAIN_END:
                continue
            if period == "test" and e["t"] < TEST_START:
                continue
            results[e["session"]][coin].append(e["ret_bps"])

    return results


def backtest_session(enriched, data, config):
    """Enter at session X open, exit at session Y open (or after N candles).

    config:
        entry_session: "Asia", "EU", "US"
        exit_session: "Asia", "EU", "US" (different from entry)
        direction: 1 (long) or -1 (short) or "auto" (based on historical bias)
        hold_candles: max hold in candles (safety)
        min_ret_threshold: min absolute avg return in bps to enter (filter noise)
        period: "train", "test", "all"
    """
    entry_session = config.get("entry_session", "US")
    exit_session = config.get("exit_session", "Asia")
    direction = config.get("direction", "auto")
    hold_max = config.get("hold_candles", 6)  # 24h max
    period = config.get("period", "all")
    size = config.get("size", POSITION_SIZE)
    max_pos = config.get("max_positions", 6)

    # If auto direction, compute historical bias
    if direction == "auto":
        bias = analyze_session_bias(enriched, period="train")
        # Compute avg return for entry_session across all tokens
        all_rets = []
        for coin in bias.get(entry_session, {}):
            all_rets.extend(bias[entry_session][coin])
        avg_bias = float(np.mean(all_rets)) if all_rets else 0
        # If entry session is negative on avg → short on entry, expect recovery on exit
        direction = -1 if avg_bias < 0 else 1

    trades = []

    for coin, entries in enriched.items():
        if coin in ["BTC", "ETH"]:
            continue
        i = 0
        while i < len(entries):
            e = entries[i]
            if period == "train" and e["t"] >= TRAIN_END:
                i += 1
                continue
            if period == "test" and e["t"] < TEST_START:
                i += 1
                continue

            if e["session"] != entry_session:
                i += 1
                continue

            # Enter at this candle's open
            entry_price = e["open"]
            if entry_price <= 0:
                i += 1
                continue

            # Look forward for exit session or max hold
            exit_price = None
            hold = 0
            for j in range(i + 1, min(i + hold_max + 1, len(entries))):
                hold = j - i
                if entries[j]["session"] == exit_session:
                    exit_price = entries[j]["open"]  # exit at open of target session
                    break
            else:
                # Didn't find exit session, use last candle close
                j = min(i + hold_max, len(entries) - 1)
                hold = j - i
                exit_price = entries[j]["close"]

            if exit_price and exit_price > 0:
                gross = direction * (exit_price / entry_price - 1) * 1e4
                net = gross - COST_BPS
                pnl = size * net / 1e4
                trades.append({
                    "coin": coin, "direction": "LONG" if direction == 1 else "SHORT",
                    "entry_session": entry_session, "exit_session": exit_session,
                    "hold": hold, "gross_bps": round(gross, 1),
                    "net_bps": round(net, 1), "pnl": round(pnl, 2),
                    "entry_t": e["t"], "exit_t": entries[min(j, len(entries)-1)]["t"],
                })

            i = i + max(hold, 1) + 1  # skip past this trade + cooldown

    return trades


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0, "monthly": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net_bps"] for t in trades]))
    wins = sum(1 for t in trades if t["net_bps"] > 0)
    t_min = min(t["entry_t"] for t in trades)
    t_max = max(t["exit_t"] for t in trades)
    months = max(1, (t_max - t_min) / (30.44 * 86400 * 1000))
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1),
            "win": round(wins / n * 100, 0), "monthly": round(pnl / months, 1)}


def main():
    print("=" * 60)
    print("SESSION EFFECTS BACKTEST")
    print("=" * 60)

    print("\nLoading data...")
    data = load_3y_candles()
    print(f"  {len(data)} tokens")

    print("Classifying candles by session...")
    enriched = classify_candles(data)
    total = sum(len(v) for v in enriched.values())
    print(f"  {total} candles classified")

    # ── Session bias analysis ──────────────────────────────
    print(f"\n{'─' * 50}")
    print("SESSION BIAS (avg return per 4h candle, train period)")
    print(f"{'─' * 50}")

    bias = analyze_session_bias(enriched, period="train")
    for session in SESSION_NAMES:
        all_rets = []
        per_coin = {}
        for coin in sorted(bias.get(session, {}).keys()):
            rets = bias[session][coin]
            per_coin[coin] = float(np.mean(rets))
            all_rets.extend(rets)

        avg = float(np.mean(all_rets)) if all_rets else 0
        std = float(np.std(all_rets)) if all_rets else 0
        n = len(all_rets)
        # t-stat for significance
        t_stat = avg / (std / np.sqrt(n)) if std > 0 and n > 0 else 0
        sig = "***" if abs(t_stat) > 3 else "**" if abs(t_stat) > 2 else "*" if abs(t_stat) > 1.5 else ""
        print(f"\n  {session:5s}: avg={avg:+.2f} bps/candle  std={std:.1f}  n={n:,}  t={t_stat:+.2f} {sig}")

        # Top/bottom tokens
        sorted_coins = sorted(per_coin.items(), key=lambda x: x[1])
        worst3 = sorted_coins[:3]
        best3 = sorted_coins[-3:]
        print(f"    Worst: {', '.join(f'{c}({v:+.1f})' for c,v in worst3)}")
        print(f"    Best:  {', '.join(f'{c}({v:+.1f})' for c,v in best3)}")

    # ── Test bias on test period ───────────────────────────
    print(f"\n{'─' * 50}")
    print("SESSION BIAS (test period — does it persist?)")
    print(f"{'─' * 50}")

    bias_test = analyze_session_bias(enriched, period="test")
    for session in SESSION_NAMES:
        all_rets = []
        for coin in bias_test.get(session, {}):
            all_rets.extend(bias_test[session][coin])
        avg = float(np.mean(all_rets)) if all_rets else 0
        n = len(all_rets)
        std = float(np.std(all_rets)) if all_rets else 0
        t_stat = avg / (std / np.sqrt(n)) if std > 0 and n > 0 else 0
        print(f"  {session:5s}: avg={avg:+.2f} bps/candle  n={n:,}  t={t_stat:+.2f}")

    # ── Backtest all session pairs ─────────────────────────
    print(f"\n{'─' * 50}")
    print("TRADING SESSION PAIRS")
    print(f"{'─' * 50}")

    results = []
    for entry_s in SESSION_NAMES:
        for exit_s in SESSION_NAMES:
            if entry_s == exit_s:
                continue
            for direction in [1, -1]:
                dir_label = "LONG" if direction == 1 else "SHORT"
                for period in ["train", "test"]:
                    trades = backtest_session(enriched, data, {
                        "entry_session": entry_s, "exit_session": exit_s,
                        "direction": direction, "period": period,
                    })
                    s = score(trades)
                    results.append({
                        "entry": entry_s, "exit": exit_s,
                        "dir": dir_label, "period": period, **s,
                    })

    # Show all
    print(f"\n  {'Entry':>6} → {'Exit':>6} {'Dir':>5} | {'Train':>40} | {'Test':>40}")
    for entry_s in SESSION_NAMES:
        for exit_s in SESSION_NAMES:
            if entry_s == exit_s:
                continue
            for dir_label in ["LONG", "SHORT"]:
                tr = [r for r in results if r["entry"] == entry_s and r["exit"] == exit_s
                      and r["dir"] == dir_label and r["period"] == "train"]
                te = [r for r in results if r["entry"] == entry_s and r["exit"] == exit_s
                      and r["dir"] == dir_label and r["period"] == "test"]
                if tr and te:
                    tr, te = tr[0], te[0]
                    flag = "✓" if tr["avg"] > 0 and te["avg"] > 0 else " "
                    print(f"  {entry_s:>6} → {exit_s:>6} {dir_label:>5} | "
                          f"n={tr['n']:>5} avg={tr['avg']:>+5.1f} win={tr['win']:>2.0f}% ${tr['pnl']:>7.0f} | "
                          f"n={te['n']:>5} avg={te['avg']:>+5.1f} win={te['win']:>2.0f}% ${te['pnl']:>7.0f} {flag}")

    # Passing
    print(f"\n{'=' * 60}")
    print("PASSING (avg > 0 train + test)")
    passing = []
    for r_tr in [r for r in results if r["period"] == "train" and r["avg"] > 0 and r["n"] >= 50]:
        r_te = [t for t in results if t["entry"] == r_tr["entry"] and t["exit"] == r_tr["exit"]
                and t["dir"] == r_tr["dir"] and t["period"] == "test"]
        if r_te and r_te[0]["avg"] > 0 and r_te[0]["n"] >= 20:
            passing.append({"train": r_tr, "test": r_te[0],
                            "total": r_tr["pnl"] + r_te[0]["pnl"]})

    if not passing:
        print("  None. No systematic session edge.")
    else:
        passing.sort(key=lambda x: x["total"], reverse=True)
        for p in passing:
            tr, te = p["train"], p["test"]
            print(f"  {tr['entry']}→{tr['exit']} {tr['dir']}: "
                  f"train avg={tr['avg']:+.1f} ${tr['pnl']:.0f} | test avg={te['avg']:+.1f} ${te['pnl']:.0f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
