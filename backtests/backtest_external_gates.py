"""External-signal gates as entry filters.

Instead of reacting to the bot's own P&L history (curve-fit trap), test gates
derived from signals orthogonal to the bot's internal state:

1. funding_abs  : skip if |funding_8h| > threshold (crowded positioning)
2. funding_dir  : skip LONG if funding > +th, SHORT if funding < -th (contra-trend)
3. funding_align: skip LONG if funding < -th, SHORT if funding > +th (with-trend confirmation)
4. oi_delta_abs : skip if |Δ(OI, 24h)| > threshold (rapid OI build/flush)
5. oi_align_long: skip LONG if oi_delta_24h < 0 (longs unwinding)
6. oi_align_shrt: skip SHORT if oi_delta_24h > 0 (longs still building = no capitulation)
7. premium_abs  : skip if |premium| > th (extreme spot-perp dislocation)
8. btc_vol_high : skip all entries if BTC realized vol > th (regime filter)
9. btc_vol_low  : skip all entries if BTC realized vol < th (dead market)
10. session     : skip entries outside optimal session (per-strategy best)
11. n_signals   : skip if >N signals fire same ts (market stress)

Each gate tested alone against baseline on 28m/12m/6m/3m. A gate is VALID if
it improves P&L on ≥3 of 4 windows with the same sign.
"""
from __future__ import annotations

import json
import os
from bisect import bisect_right
from collections import defaultdict, deque
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

import numpy as np

from analysis.bot.config import (
    STRAT_Z, MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, MACRO_STRATEGIES, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8, S9_EARLY_EXIT_BPS, S9_EARLY_EXIT_HOURS,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, HOLD_HOURS_S8, HOLD_HOURS_S9, HOLD_HOURS_S10,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP, VERSION,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    detect_squeeze, strat_size, COST,
    HOLD_CANDLES, S9_EARLY_EXIT_CANDLES,
)

DATA_DIR = "backtests/output/pairs_data"


# ── External data loaders ────────────────────────────────────────────────

def load_funding():
    """Load funding data per coin → sorted list of (ts, rate_per_8h_bps, premium_bps)."""
    data = {}
    for coin in TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_funding_full.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        pts = [(int(r["time"]), float(r["fundingRate"]) * 1e4, float(r["premium"]) * 1e4)
               for r in raw]
        pts.sort()
        data[coin] = pts
    return data


def load_oi():
    """Load OI data per coin → sorted list of (ts, oi)."""
    data = {}
    for coin in TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_oi_4h.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            raw = json.load(f)
        pts = [(int(r["t"]), float(r["oi"])) for r in raw]
        pts.sort()
        data[coin] = pts
    return data


def lookup_funding(funding_data, coin, ts_ms):
    """Return (rate_bps, premium_bps) at ts_ms, or (None, None) if unavailable."""
    pts = funding_data.get(coin)
    if not pts:
        return None, None
    times = [p[0] for p in pts]
    i = bisect_right(times, ts_ms) - 1
    if i < 0:
        return None, None
    return pts[i][1], pts[i][2]


def oi_delta_24h_pct(oi_data, coin, ts_ms):
    """OI delta over last 24h as percentage (bps). None if insufficient data."""
    pts = oi_data.get(coin)
    if not pts:
        return None
    times = [p[0] for p in pts]
    i = bisect_right(times, ts_ms) - 1
    if i < 6:  # need 6 4h candles back = 24h
        return None
    oi_now = pts[i][1]
    oi_then = pts[i - 6][1]
    if oi_then <= 0:
        return None
    return (oi_now / oi_then - 1) * 1e4


def btc_vol(btc_closes, btc_idx_by_ts, ts, window_candles=42):
    """Realized volatility of BTC 4h returns over window (in bps std)."""
    if ts not in btc_idx_by_ts:
        return None
    i = btc_idx_by_ts[ts]
    if i < window_candles + 1:
        return None
    closes = btc_closes[i - window_candles: i + 1]
    rets = np.diff(closes) / closes[:-1]
    return float(np.std(rets) * 1e4)


def hour_utc(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour


# ── Engine ───────────────────────────────────────────────────────────────

def run_window(features, data, sector_features, funding_data, oi_data,
               start_ts_ms, end_ts_ms, start_capital=1000.0,
               gate_fn=None, gate_args=None):
    """Run backtest. gate_fn(ctx) returns True to SKIP entry, False to allow."""
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

    def btc_ret(ts, lookback):
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
    skipped_by_gate = 0

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        btc30 = btc_ret(ts, 180)
        btc7 = btc_ret(ts, 42)

        # ── Exits (unchanged) ──
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
            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0):
                pos["mfe"] = best_bps

            if pos["strat"] == "S8":
                stop = STOP_LOSS_S8
            elif pos.get("stop", 0) != 0:
                stop = pos["stop"]
            else:
                stop = STOP_LOSS_BPS

            exit_reason = None
            exit_price = current
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
                ur = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"
            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
                    ur = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur <= mfe - S10_TRAILING_OFFSET:
                        exit_reason = "s10_trailing"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                peak_capital = max(peak_capital, capital)
                dd = (capital - peak_capital) / peak_capital * 100 if peak_capital > 0 else 0
                max_dd_pct = min(max_dd_pct, dd)
                trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                               "strat": pos["strat"], "coin": coin,
                               "entry_t": pos["entry_t"], "exit_t": ts,
                               "reason": exit_reason, "size": pos["size"]})
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── Candidates ──
        candidates = []
        for coin in coins:
            f = feat_by_ts.get(ts, {}).get(coin)
            if not f:
                continue
            ret_24h = f.get("ret_6h", 0)
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                                   "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                   "strength": max(f.get("ret_42h", 0), 0)})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({"coin": coin,
                                   "dir": 1 if sf["divergence"] > 0 else -1,
                                   "strat": "S5",
                                   "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                   "strength": abs(sf["divergence"])})
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH
                    and btc7 < S8_BTC_7D_THRESH):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                                   "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                                   "strength": abs(f["drawdown"])})
            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = (max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8)
                           if S9_ADAPTIVE_STOP else 0)
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                                   "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                                   "strength": abs(ret_24h), "stop": s9_stop})
            if coin in coin_by_ts and ts in coin_by_ts[coin]:
                ci = coin_by_ts[coin][ts]
                sq_dir = detect_squeeze(data[coin], ci, f.get("vol_ratio", 2))
                if sq_dir:
                    blocked = ((not S10_ALLOW_LONGS and sq_dir == 1)
                               or coin not in S10_ALLOWED_TOKENS)
                    if not blocked:
                        candidates.append({"coin": coin, "dir": sq_dir, "strat": "S10",
                                           "z": STRAT_Z["S10"], "hold": HOLD_CANDLES["S10"],
                                           "strength": 1000})

        n_candidates_this_ts = len(candidates)
        btc_v = btc_vol(btc_closes, btc_by_ts, ts) if gate_fn else None

        n_long = sum(1 for p in positions.values() if p["dir"] == 1)
        n_short = sum(1 for p in positions.values() if p["dir"] == -1)
        n_macro = sum(1 for p in positions.values() if p["strat"] in macro_strats)
        n_token = sum(1 for p in positions.values() if p["strat"] not in macro_strats)

        filtered = [c for c in candidates
                    if c["coin"] not in positions
                    and not (c["coin"] in cooldown and ts < cooldown[c["coin"]])]
        filtered.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)

        seen = set()
        for cand in filtered:
            coin = cand["coin"]
            if coin in seen or coin in positions:
                continue
            seen.add(coin)

            # Apply external gate
            if gate_fn is not None:
                fund_rate, fund_prem = lookup_funding(funding_data, coin, ts)
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                ctx = {"coin": coin, "dir": cand["dir"], "strat": cand["strat"],
                       "ts": ts, "funding": fund_rate, "premium": fund_prem,
                       "oi_delta_24h": oi_d, "btc_vol": btc_v,
                       "n_signals": n_candidates_this_ts,
                       "hour_utc": hour_utc(ts), "args": gate_args}
                if gate_fn(ctx):
                    skipped_by_gate += 1
                    continue

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
            positions[coin] = {"dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                               "entry_t": data[coin][idx_f + 1]["t"],
                               "strat": cand["strat"], "hold": cand["hold"],
                               "size": size, "coin": coin,
                               "stop": cand.get("stop", 0), "mfe": 0.0}
            if cand["dir"] == 1:
                n_long += 1
            else:
                n_short += 1
            if cand["strat"] in macro_strats:
                n_macro += 1
            else:
                n_token += 1

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
            trades.append({"pnl": pnl, "net": net, "dir": pos["dir"],
                           "strat": pos["strat"], "coin": coin,
                           "entry_t": pos["entry_t"], "exit_t": last_ts,
                           "reason": "mtm_final", "size": pos["size"]})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_strat[t["strat"]]
        s["n"] += 1
        s["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            s["wins"] += 1

    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "skipped": skipped_by_gate,
        "by_strat": {k: {"n": v["n"], "pnl": round(v["pnl"], 2),
                         "wr": round(v["wins"] / v["n"] * 100, 0) if v["n"] else 0}
                     for k, v in by_strat.items()},
    }


# ── Gates ────────────────────────────────────────────────────────────────

def gate_funding_abs(ctx):
    """Skip if |funding_8h| > threshold bps."""
    f = ctx["funding"]
    if f is None:
        return False
    return abs(f) > ctx["args"]["th"]

def gate_funding_dir(ctx):
    """Contra-trend: skip LONG if funding > +th, SHORT if funding < -th."""
    f = ctx["funding"]
    if f is None:
        return False
    th = ctx["args"]["th"]
    if ctx["dir"] == 1 and f > th:
        return True
    if ctx["dir"] == -1 and f < -th:
        return True
    return False

def gate_funding_align(ctx):
    """With-trend: skip LONG if funding < -th (shorts crowded, pain continues);
    skip SHORT if funding > +th (longs crowded, pain continues)."""
    f = ctx["funding"]
    if f is None:
        return False
    th = ctx["args"]["th"]
    if ctx["dir"] == 1 and f < -th:
        return True
    if ctx["dir"] == -1 and f > th:
        return True
    return False

def gate_oi_delta_abs(ctx):
    d = ctx["oi_delta_24h"]
    if d is None:
        return False
    return abs(d) > ctx["args"]["th"]

def gate_oi_align_long(ctx):
    """Skip LONG if OI fell >th (longs unwinding, bad for LONG entry)."""
    d = ctx["oi_delta_24h"]
    if d is None or ctx["dir"] != 1:
        return False
    return d < -ctx["args"]["th"]

def gate_oi_align_short(ctx):
    """Skip SHORT if OI rose >th (longs still building, no capitulation)."""
    d = ctx["oi_delta_24h"]
    if d is None or ctx["dir"] != -1:
        return False
    return d > ctx["args"]["th"]

def gate_premium_abs(ctx):
    p = ctx["premium"]
    if p is None:
        return False
    return abs(p) > ctx["args"]["th"]

def gate_btc_vol_high(ctx):
    v = ctx["btc_vol"]
    if v is None:
        return False
    return v > ctx["args"]["th"]

def gate_btc_vol_low(ctx):
    v = ctx["btc_vol"]
    if v is None:
        return False
    return v < ctx["args"]["th"]

def gate_n_signals(ctx):
    return ctx["n_signals"] > ctx["args"]["th"]

def gate_session(ctx):
    """Skip if hour_utc not in allowed set."""
    return ctx["hour_utc"] not in ctx["args"]["allowed_hours"]


# ── Driver ───────────────────────────────────────────────────────────────

def fmt_result(r):
    return (f"${r['end_capital']:>6.0f} | P&L ${r['pnl']:+7.0f} ({r['pnl_pct']:+6.1f}%) | "
            f"DD {r['max_dd_pct']:+5.1f}% | {r['n_trades']:>3}t WR {r['win_rate']:.0f}% | "
            f"skip {r['skipped']:>3}")


def run_all():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    funding_data = load_funding()
    oi_data = load_oi()
    print(f"  candles: {len(data)} coins | funding: {len(funding_data)} coins | OI: {len(oi_data)} coins")
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data ends {end_dt.isoformat()}")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m",  end_dt - relativedelta(months=6)),
        ("3m",  end_dt - relativedelta(months=3)),
    ]

    # Test battery: (gate_name, gate_fn, args_list)
    battery = [
        ("funding_abs",    gate_funding_abs,    [{"th": 2}, {"th": 5}, {"th": 10}]),
        ("funding_dir",    gate_funding_dir,    [{"th": 1}, {"th": 2}, {"th": 5}]),
        ("funding_align",  gate_funding_align,  [{"th": 1}, {"th": 2}, {"th": 5}]),
        ("oi_delta_abs",   gate_oi_delta_abs,   [{"th": 500}, {"th": 1000}, {"th": 2000}]),
        ("oi_align_long",  gate_oi_align_long,  [{"th": 300}, {"th": 500}, {"th": 1000}]),
        ("oi_align_short", gate_oi_align_short, [{"th": 300}, {"th": 500}, {"th": 1000}]),
        ("premium_abs",    gate_premium_abs,    [{"th": 5}, {"th": 10}, {"th": 20}]),
        ("btc_vol_high",   gate_btc_vol_high,   [{"th": 150}, {"th": 200}, {"th": 300}]),
        ("btc_vol_low",    gate_btc_vol_low,    [{"th": 50}, {"th": 75}]),
        ("n_signals",      gate_n_signals,      [{"th": 5}, {"th": 8}, {"th": 10}]),
        ("session_asia",   gate_session,        [{"allowed_hours": set(range(0, 8))}]),
        ("session_eu",     gate_session,        [{"allowed_hours": set(range(8, 16))}]),
        ("session_us",     gate_session,        [{"allowed_hours": set(range(16, 24))}]),
    ]

    # Precompute baselines per window
    print("\nBaseline per window:")
    baselines = {}
    for label, start_dt in windows:
        r = run_window(features, data, sector_features, funding_data, oi_data,
                       int(start_dt.timestamp() * 1000), latest_ts)
        baselines[label] = r
        print(f"  {label:4} ({start_dt.date()}): {fmt_result(r)}")

    # Test each gate config
    print(f"\n{'='*130}")
    print(f"External gates — v{VERSION} — data thru {end_dt.date()}")
    print(f"{'='*130}")

    summary = []  # for final ranking

    for gate_name, gate_fn, args_list in battery:
        for args in args_list:
            arg_str = ",".join(f"{k}={v}" for k, v in args.items() if k != "allowed_hours")
            if "allowed_hours" in args:
                arg_str = f"hours={sorted(args['allowed_hours'])[0]}-{sorted(args['allowed_hours'])[-1]}"
            row_label = f"{gate_name:15} {arg_str:15}"
            print(f"\n── {row_label} ──")
            deltas = {}
            for wlabel, start_dt in windows:
                r = run_window(features, data, sector_features, funding_data, oi_data,
                               int(start_dt.timestamp() * 1000), latest_ts,
                               gate_fn=gate_fn, gate_args=args)
                b = baselines[wlabel]
                dp = r["pnl"] - b["pnl"]
                dd = r["max_dd_pct"] - b["max_dd_pct"]
                deltas[wlabel] = (dp, dd, r["skipped"], r)
                print(f"  {wlabel:4}: {fmt_result(r)} | Δ${dp:+8.0f} ΔDD {dd:+5.1f}pp")

            # Walk-forward score: count windows where P&L improved (dp > 0)
            wins = sum(1 for w in deltas.values() if w[0] > 0)
            dp_sum = sum(w[0] for w in deltas.values())  # over all baseline shifts
            # Normalized: % improvement per window relative to baseline
            pct_deltas = []
            for wlabel in ("28m", "12m", "6m", "3m"):
                b = baselines[wlabel]["pnl"]
                if b > 0:
                    pct_deltas.append(deltas[wlabel][0] / b * 100)
            avg_pct = sum(pct_deltas) / len(pct_deltas) if pct_deltas else 0
            summary.append({
                "gate": gate_name, "args": arg_str,
                "wins": wins, "dp_sum": dp_sum, "avg_pct": avg_pct,
                "d28m": deltas["28m"][0], "d12m": deltas["12m"][0],
                "d6m": deltas["6m"][0], "d3m": deltas["3m"][0],
            })

    # Final leaderboard
    print(f"\n\n{'='*130}")
    print("LEADERBOARD (sorted by wins desc, then avg_pct desc)")
    print(f"{'='*130}")
    print(f"{'gate':16} {'args':18} {'wins/4':7} {'avg%':>7} {'Δ28m':>10} {'Δ12m':>10} {'Δ6m':>10} {'Δ3m':>10}")
    summary.sort(key=lambda x: (-x["wins"], -x["avg_pct"]))
    for s in summary:
        print(f"{s['gate']:16} {s['args']:18} {s['wins']}/4     "
              f"{s['avg_pct']:+6.1f}% ${s['d28m']:+8.0f} ${s['d12m']:+8.0f} "
              f"${s['d6m']:+8.0f} ${s['d3m']:+8.0f}")


if __name__ == "__main__":
    run_all()
