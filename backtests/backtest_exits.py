"""Exit optimization backtest — trailing stop, flat exit, adaptive hold.

Tests whether smarter exit rules improve on the baseline fixed-stop + timeout.
Uses the same walk-forward validation as backtest_oi_gates.py: must improve on
ALL 4 rolling windows (28m, 12m, 6m, 3m) to be accepted.

Hypotheses tested:
  H1 — Trailing stop on MFE: lock in gains when MFE exceeds a trigger
  H2 — Flat exit: close early if trade is stuck near zero after N candles
  H3 — Combined H1 + H2

Usage:
    python3 -m backtests.backtest_exits
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from analysis.bot.config import (
    SIZE_PCT, SIZE_BONUS, STRAT_Z, SIGNAL_MULT, LIQUIDITY_HAIRCUT,
    LEVERAGE, COST_BPS, TAKER_FEE_BPS, FUNDING_DRAG_BPS,
    MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8, S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP,
    S10_SQUEEZE_WINDOW, S10_VOL_RATIO_MAX, S10_BREAKOUT_PCT, S10_REINT_CANDLES,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
)

from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    load_dxy, detect_squeeze, strat_size,
    HOLD_CANDLES, S9_EARLY_EXIT_CANDLES, BACKTEST_SLIPPAGE_BPS,
)

COST = COST_BPS + BACKTEST_SLIPPAGE_BPS


# ── Exit rule definitions ─────────────────────────────────────────────

def make_trailing_stop_fn(trigger_bps: float, offset_bps: float):
    """Factory: trailing stop that activates when MFE > trigger.

    Once activated, the position exits if unrealized drops below MFE - offset.
    """
    def exit_fn(pos: dict, candle: dict, held: int, ur_bps: float) -> str | None:
        # Update MFE
        if pos["dir"] == 1:
            best = (candle["h"] / pos["entry"] - 1) * 1e4
        else:
            best = -(candle["l"] / pos["entry"] - 1) * 1e4
        if best > pos.get("mfe", 0):
            pos["mfe"] = best

        mfe = pos.get("mfe", 0)
        if mfe >= trigger_bps:
            floor = mfe - offset_bps
            if ur_bps <= floor:
                return "trailing_stop"
        return None
    return exit_fn


def make_flat_exit_fn(min_candles: int, threshold_bps: float):
    """Factory: exit if trade is flat (|unrealized| < threshold) after N candles."""
    def exit_fn(pos: dict, candle: dict, held: int, ur_bps: float) -> str | None:
        if held >= min_candles and abs(ur_bps) < threshold_bps:
            return "flat_exit"
        return None
    return exit_fn


def make_combined_fn(*fns):
    """Chain multiple exit functions — first non-None wins."""
    def exit_fn(pos: dict, candle: dict, held: int, ur_bps: float) -> str | None:
        for fn in fns:
            result = fn(pos, candle, held, ur_bps)
            if result:
                return result
        return None
    return exit_fn


# ── Engine (forked from backtest_rolling.run_window with exit hook) ────

def run_window_with_exits(features, data, sector_features, dxy_data,
                          start_ts_ms: int, end_ts_ms: int,
                          exit_fn=None,
                          start_capital: float = 1000.0) -> dict:
    """Run portfolio backtest with optional custom exit function.

    exit_fn(pos, candle, held, ur_bps) -> str|None
    If it returns a string, the position is closed with that reason.
    Called BEFORE the standard stop/timeout/S9 checks.
    """
    coins = [c for c in TOKENS if c in features and c in data]
    macro_strats = set(MACRO_STRATEGIES)

    all_ts = set()
    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            all_ts.add(c["t"])
            coin_by_ts[coin][c["t"]] = i

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features.get(coin, []):
            feat_by_ts[f["t"]][coin] = f

    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles])
    btc_by_ts = {c["t"]: i for i, c in enumerate(btc_candles)}

    def btc_ret(ts: int, lookback: int) -> float:
        if ts not in btc_by_ts:
            return 0.0
        i = btc_by_ts[ts]
        if i < lookback or btc_closes[i - lookback] <= 0:
            return 0.0
        return (btc_closes[i] / btc_closes[i - lookback] - 1) * 1e4

    positions = {}
    trades = []
    cooldown = {}
    capital = start_capital
    peak_capital = start_capital
    max_dd_pct = 0.0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        # ── EXITS ──
        for coin in list(positions.keys()):
            pos = positions[coin]
            if ts not in coin_by_ts.get(coin, {}):
                continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0:
                continue
            candle = data[coin][ci]
            current = candle["c"]
            if current <= 0:
                continue

            ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4

            # Always track MFE (best unrealized during the trade)
            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0):
                pos["mfe"] = best_bps

            # Custom exit check (trailing stop, flat exit, etc.)
            exit_reason = None
            if exit_fn is not None:
                exit_reason = exit_fn(pos, candle, held, ur_bps)

            # Standard stop loss
            if pos["strat"] == "S8":
                stop = STOP_LOSS_S8
            elif pos.get("stop", 0) != 0:
                stop = pos["stop"]
            else:
                stop = STOP_LOSS_BPS

            exit_price = current
            if not exit_reason:
                if pos["dir"] == 1:
                    worst = (candle["l"] / pos["entry"] - 1) * 1e4
                    if worst < stop:
                        exit_reason = "stop"
                        exit_price = pos["entry"] * (1 + stop / 1e4)
                else:
                    worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                    if worst < stop:
                        exit_reason = "stop"
                        exit_price = pos["entry"] * (1 - stop / 1e4)

            if held >= pos["hold"]:
                exit_reason = exit_reason or "timeout"

            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({
                    "pnl": pnl, "net": net, "dir": pos["dir"],
                    "strat": pos["strat"], "coin": coin,
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "reason": exit_reason, "size": pos["size"],
                    "mfe": pos.get("mfe", 0),
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── ENTRIES (identical to backtest_rolling) ──
        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        candidates = []
        for coin in coins:
            if coin in positions or (coin in cooldown and ts < cooldown[coin]):
                continue
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue

            ret_24h = f.get("ret_6h", 0)

            if btc30 > 2000:
                candidates.append({
                    "coin": coin, "dir": 1, "strat": "S1",
                    "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                    "strength": max(f.get("ret_42h", 0), 0),
                })

            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({
                    "coin": coin, "dir": 1 if sf["divergence"] > 0 else -1, "strat": "S5",
                    "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                    "strength": abs(sf["divergence"]),
                })

            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                candidates.append({
                    "coin": coin, "dir": 1, "strat": "S8",
                    "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                    "strength": abs(f["drawdown"]),
                })

            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                candidates.append({
                    "coin": coin, "dir": s9_dir, "strat": "S9",
                    "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                    "strength": abs(ret_24h), "stop": s9_stop,
                })

            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    s10_block = ((not S10_ALLOW_LONGS and sq_dir == 1)
                                 or coin not in S10_ALLOWED_TOKENS)
                    if not s10_block:
                        candidates.append({
                            "coin": coin, "dir": sq_dir, "strat": "S10",
                            "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                            "strength": 1000,
                        })

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)
            if len(positions) >= MAX_POSITIONS:
                break
            if cand["dir"] == 1 and n_long >= MAX_SAME_DIRECTION:
                continue
            if cand["dir"] == -1 and n_short >= MAX_SAME_DIRECTION:
                continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO_SLOTS:
                continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN_SLOTS:
                continue

            sym_sector = TOKEN_SECTOR.get(coin)
            if sym_sector:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sym_sector)
                if sc >= MAX_PER_SECTOR:
                    continue

            f = feat_by_ts.get(ts, {}).get(coin)
            idx_f = f.get("_idx") if f else None
            if idx_f is None or idx_f + 1 >= len(data[coin]):
                continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0:
                continue

            size = strat_size(cand["strat"], capital)
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0,
            }
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1

    # Close remaining positions (mark-to-market)
    for coin in list(positions.keys()):
        pos = positions[coin]
        last_ts = max(t for t in coin_by_ts[coin] if t <= end_ts_ms)
        last_idx = coin_by_ts[coin][last_ts]
        exit_p = data[coin][last_idx]["c"]
        if exit_p > 0:
            gross = pos["dir"] * (exit_p / pos["entry"] - 1) * 1e4
            net = gross - COST
            pnl = pos["size"] * net / 1e4
            capital += pnl
            trades.append({
                "pnl": pnl, "net": net, "dir": pos["dir"],
                "strat": pos["strat"], "coin": coin,
                "entry_t": pos["entry_t"], "exit_t": last_ts,
                "reason": "mtm_final", "size": pos["size"],
                "mfe": pos.get("mfe", 0),
            })

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1

    by_reason = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in trades:
        r = by_reason[t["reason"]]
        r["n"] += 1
        r["pnl"] += t["pnl"]

    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "by_strat": dict(by_strat),
        "by_reason": dict(by_reason),
        "trades": trades,
    }


# ── Walk-forward validation ───────────────────────────────────────────

def run_all_windows(features, data, sector_features, dxy_data,
                    end_dt: datetime, exit_fn=None, label: str = "") -> list[dict]:
    """Run on 4 standard rolling windows. Returns list of results."""
    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]
    latest_ts = int(end_dt.timestamp() * 1000)
    results = []
    for wlabel, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window_with_exits(features, data, sector_features, dxy_data,
                                  start_ts, latest_ts, exit_fn=exit_fn)
        r["window"] = wlabel
        results.append(r)
    return results


def compare_results(baseline: list[dict], test: list[dict]) -> dict:
    """Compare test vs baseline across all windows."""
    comparison = []
    all_better = True
    for b, t in zip(baseline, test):
        delta_pnl = t["pnl"] - b["pnl"]
        delta_pct = t["pnl_pct"] - b["pnl_pct"]
        delta_dd = t["max_dd_pct"] - b["max_dd_pct"]  # less negative = better
        better = delta_pnl > 0
        if not better:
            all_better = False
        comparison.append({
            "window": b["window"],
            "base_pnl": b["pnl"],
            "test_pnl": t["pnl"],
            "delta_pnl": delta_pnl,
            "delta_pct": delta_pct,
            "delta_dd": delta_dd,
            "base_wr": b["win_rate"],
            "test_wr": t["win_rate"],
            "better": better,
        })
    return {"comparison": comparison, "all_better": all_better}


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("EXIT OPTIMIZATION BACKTEST")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.isoformat()}")

    # ── Baseline (current bot rules) ──
    print("\n--- BASELINE (current exit rules) ---")
    baseline = run_all_windows(features, data, sector_features, dxy_data, end_dt)
    for r in baseline:
        print(f"  {r['window']}: ${r['pnl']:+,.0f} ({r['pnl_pct']:+.1f}%), "
              f"DD {r['max_dd_pct']:.1f}%, {r['n_trades']} trades, WR {r['win_rate']:.0f}%")
        reasons = r.get("by_reason", {})
        if reasons:
            parts = [f"{k}={v['n']}" for k, v in sorted(reasons.items())]
            print(f"         exits: {', '.join(parts)}")

    # ── H1: Trailing stop sweep ──
    print("\n" + "=" * 70)
    print("H1 — TRAILING STOP ON MFE")
    print("=" * 70)

    trailing_configs = []
    for trigger in [200, 300, 500, 800]:
        for offset in [100, 200, 300]:
            if offset >= trigger:
                continue
            trailing_configs.append((trigger, offset))

    best_trailing = None
    best_trailing_score = -1e9

    for trigger, offset in trailing_configs:
        exit_fn = make_trailing_stop_fn(trigger, offset)
        results = run_all_windows(features, data, sector_features, dxy_data,
                                  end_dt, exit_fn=exit_fn)
        comp = compare_results(baseline, results)

        # Score: sum of delta_pnl across windows (only meaningful if all_better)
        total_delta = sum(c["delta_pnl"] for c in comp["comparison"])
        status = "PASS" if comp["all_better"] else "FAIL"

        deltas = [f"{c['window']}:{c['delta_pnl']:+.0f}" for c in comp["comparison"]]
        print(f"  trigger={trigger:4d} offset={offset:3d} → [{status}] "
              f"Σδ=${total_delta:+,.0f}  ({', '.join(deltas)})")

        if comp["all_better"] and total_delta > best_trailing_score:
            best_trailing_score = total_delta
            best_trailing = {
                "trigger": trigger, "offset": offset,
                "results": results, "comparison": comp,
            }

    if best_trailing:
        print(f"\n  ✓ BEST TRAILING: trigger={best_trailing['trigger']}, "
              f"offset={best_trailing['offset']}, Σδ=${best_trailing_score:+,.0f}")
        for c in best_trailing["comparison"]["comparison"]:
            print(f"    {c['window']}: ${c['base_pnl']:+,.0f} → ${c['test_pnl']:+,.0f} "
                  f"(Δ${c['delta_pnl']:+,.0f}, DD {c['delta_dd']:+.1f}pp, "
                  f"WR {c['base_wr']:.0f}→{c['test_wr']:.0f}%)")
    else:
        print("\n  ✗ No trailing stop config passes all 4 windows")

    # ── H2: Flat exit sweep ──
    print("\n" + "=" * 70)
    print("H2 — FLAT EXIT (close if stuck near zero)")
    print("=" * 70)

    flat_configs = []
    for min_candles in [3, 4, 6, 8]:   # 12h, 16h, 24h, 32h
        for threshold in [50, 100, 150]:
            flat_configs.append((min_candles, threshold))

    best_flat = None
    best_flat_score = -1e9

    for min_candles, threshold in flat_configs:
        exit_fn = make_flat_exit_fn(min_candles, threshold)
        results = run_all_windows(features, data, sector_features, dxy_data,
                                  end_dt, exit_fn=exit_fn)
        comp = compare_results(baseline, results)

        total_delta = sum(c["delta_pnl"] for c in comp["comparison"])
        status = "PASS" if comp["all_better"] else "FAIL"

        deltas = [f"{c['window']}:{c['delta_pnl']:+.0f}" for c in comp["comparison"]]
        hours = min_candles * 4
        print(f"  after={hours:2d}h thresh={threshold:3d}bps → [{status}] "
              f"Σδ=${total_delta:+,.0f}  ({', '.join(deltas)})")

        if comp["all_better"] and total_delta > best_flat_score:
            best_flat_score = total_delta
            best_flat = {
                "min_candles": min_candles, "threshold": threshold,
                "results": results, "comparison": comp,
            }

    if best_flat:
        hours = best_flat["min_candles"] * 4
        print(f"\n  ✓ BEST FLAT: after={hours}h, threshold={best_flat['threshold']}bps, "
              f"Σδ=${best_flat_score:+,.0f}")
        for c in best_flat["comparison"]["comparison"]:
            print(f"    {c['window']}: ${c['base_pnl']:+,.0f} → ${c['test_pnl']:+,.0f} "
                  f"(Δ${c['delta_pnl']:+,.0f}, DD {c['delta_dd']:+.1f}pp, "
                  f"WR {c['base_wr']:.0f}→{c['test_wr']:.0f}%)")
    else:
        print("\n  ✗ No flat exit config passes all 4 windows")

    # ── H3: Combined best trailing + best flat ──
    if best_trailing and best_flat:
        print("\n" + "=" * 70)
        print("H3 — COMBINED (best trailing + best flat)")
        print("=" * 70)

        combined_fn = make_combined_fn(
            make_trailing_stop_fn(best_trailing["trigger"], best_trailing["offset"]),
            make_flat_exit_fn(best_flat["min_candles"], best_flat["threshold"]),
        )
        results = run_all_windows(features, data, sector_features, dxy_data,
                                  end_dt, exit_fn=combined_fn)
        comp = compare_results(baseline, results)

        total_delta = sum(c["delta_pnl"] for c in comp["comparison"])
        status = "PASS" if comp["all_better"] else "FAIL"

        print(f"  [{status}] Σδ=${total_delta:+,.0f}")
        for c in comp["comparison"]:
            print(f"    {c['window']}: ${c['base_pnl']:+,.0f} → ${c['test_pnl']:+,.0f} "
                  f"(Δ${c['delta_pnl']:+,.0f}, DD {c['delta_dd']:+.1f}pp, "
                  f"WR {c['base_wr']:.0f}→{c['test_wr']:.0f}%)")

        # Show exit reason breakdown for combined
        if comp["all_better"]:
            longest = results[0]  # 28m window
            print(f"\n  Exit breakdown (28m window):")
            for reason, stats in sorted(longest["by_reason"].items()):
                print(f"    {reason}: {stats['n']} trades, P&L ${stats['pnl']:+,.0f}")

    # ── Per-strategy trailing stop ──
    print("\n" + "=" * 70)
    print("H1b — PER-STRATEGY TRAILING STOP")
    print("=" * 70)

    best_per_strat = {}

    for target_strat in ["S10", "S5", "S9", "S1"]:
        print(f"\n  --- {target_strat} only ---")
        strat_best = None
        strat_best_score = -1e9

        for trigger in [200, 300, 400, 500, 600, 800, 1000]:
            for offset in [100, 150, 200, 300, 400]:
                if offset >= trigger:
                    continue

                def make_strat_trailing(strat, trig, off):
                    base_fn = make_trailing_stop_fn(trig, off)
                    def fn(pos, candle, held, ur_bps):
                        if pos["strat"] != strat:
                            return None
                        return base_fn(pos, candle, held, ur_bps)
                    return fn

                exit_fn = make_strat_trailing(target_strat, trigger, offset)
                results = run_all_windows(features, data, sector_features,
                                          dxy_data, end_dt, exit_fn=exit_fn)
                comp = compare_results(baseline, results)
                total_delta = sum(c["delta_pnl"] for c in comp["comparison"])

                # Count how many windows improve
                n_better = sum(1 for c in comp["comparison"] if c["delta_pnl"] >= 0)
                worst_delta = min(c["delta_pnl"] for c in comp["comparison"])
                status = "PASS" if comp["all_better"] else f"{n_better}/4"

                if total_delta > 0 or comp["all_better"]:
                    deltas = [f"{c['window']}:{c['delta_pnl']:+.0f}"
                              for c in comp["comparison"]]
                    print(f"    trig={trigger:4d} off={offset:3d} → [{status}] "
                          f"Σδ=${total_delta:+,.0f}  ({', '.join(deltas)})")

                if comp["all_better"] and total_delta > strat_best_score:
                    strat_best_score = total_delta
                    strat_best = {"trigger": trigger, "offset": offset,
                                  "results": results, "comparison": comp}

                # Track near-passes (3/4 with tiny regression on the 4th)
                if n_better >= 3 and worst_delta > -50 and total_delta > strat_best_score:
                    if strat_best is None:
                        strat_best_score = total_delta
                        strat_best = {"trigger": trigger, "offset": offset,
                                      "results": results, "comparison": comp,
                                      "near_pass": True}

        if strat_best:
            tag = " (NEAR-PASS)" if strat_best.get("near_pass") else ""
            best_per_strat[target_strat] = strat_best
            print(f"  ✓ BEST {target_strat}: trig={strat_best['trigger']}, "
                  f"off={strat_best['offset']}{tag}")
        else:
            print(f"  ✗ No config improves {target_strat}")

    # ── MFE analysis (diagnostic) ──
    print("\n" + "=" * 70)
    print("MFE ANALYSIS — How much gain are we leaving on the table?")
    print("=" * 70)

    # Run baseline on 28m with MFE tracking
    longest_baseline = baseline[0]
    trades_28m = longest_baseline["trades"]

    # Compute MFE distribution
    mfes = [t.get("mfe", 0) for t in trades_28m if t["reason"] == "timeout"]
    if mfes:
        mfes_arr = np.array(mfes)
        nets = np.array([t["net"] for t in trades_28m if t["reason"] == "timeout"])
        print(f"  Timeout trades: {len(mfes)}")
        print(f"  MFE: mean={np.mean(mfes_arr):.0f} bps, "
              f"median={np.median(mfes_arr):.0f} bps, "
              f"p25={np.percentile(mfes_arr, 25):.0f} bps, "
              f"p75={np.percentile(mfes_arr, 75):.0f} bps")
        print(f"  Net at exit: mean={np.mean(nets):.0f} bps, "
              f"median={np.median(nets):.0f} bps")
        gave_back = mfes_arr - nets
        print(f"  Gave back: mean={np.mean(gave_back):.0f} bps, "
              f"median={np.median(gave_back):.0f} bps")
        pct_gave_back = gave_back / np.maximum(mfes_arr, 1) * 100
        print(f"  % of MFE given back: mean={np.mean(pct_gave_back):.0f}%, "
              f"median={np.median(pct_gave_back):.0f}%")

        # By strategy
        by_strat_mfe = defaultdict(list)
        for t in trades_28m:
            if t["reason"] == "timeout" and t.get("mfe", 0) > 0:
                by_strat_mfe[t["strat"]].append({
                    "mfe": t["mfe"], "net": t["net"],
                    "gave_back": t["mfe"] - t["net"],
                })
        print(f"\n  Per-strategy MFE analysis (timeout trades only):")
        for strat in sorted(by_strat_mfe.keys()):
            trades_s = by_strat_mfe[strat]
            mfes_s = [t["mfe"] for t in trades_s]
            gave_s = [t["gave_back"] for t in trades_s]
            print(f"    {strat}: n={len(trades_s)}, "
                  f"avg MFE={np.mean(mfes_s):.0f}, "
                  f"avg gave back={np.mean(gave_s):.0f} bps "
                  f"({np.mean(gave_s)/np.mean(mfes_s)*100:.0f}%)")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
