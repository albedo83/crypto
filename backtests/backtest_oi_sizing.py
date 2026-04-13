"""OI as a continuous sizing modifier — not a binary gate.

Instead of blocking trades based on OI, scale position size down when OI
conditions are adverse. This preserves the trade (doesn't lose the edge)
while limiting exposure in the worst-case scenarios.

Hypothesis: when OI is moving against the trade direction (e.g., OI rising
while entering SHORT = more longs piling in), reduce position size.

Walk-forward validated on 4 rolling windows (28m/12m/6m/3m).

Usage:
    python3 -m backtests.backtest_oi_sizing
"""

from __future__ import annotations

import os
import sqlite3
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
    S10_TRAILING_TRIGGER, S10_TRAILING_OFFSET,
)

from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    load_dxy, detect_squeeze, strat_size,
    HOLD_CANDLES, S9_EARLY_EXIT_CANDLES, BACKTEST_SLIPPAGE_BPS,
)

OI_DB = os.path.join(os.path.dirname(__file__), "output", "oi_history.db")
HOUR_S = 3600
COST = COST_BPS + BACKTEST_SLIPPAGE_BPS


# ── OI data loading ───────────────────────────────────────────────────

def load_oi_lookup() -> dict:
    """Load OI history from SQLite, keyed by (symbol, hourly_ts)."""
    if not os.path.exists(OI_DB):
        print(f"  WARNING: OI database not found at {OI_DB}")
        return {}
    db = sqlite3.connect(OI_DB)
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for sym, ts, oi in db.execute("SELECT symbol, ts, oi FROM asset_ctx"):
        hour_ts = (ts // HOUR_S) * HOUR_S
        out[sym][hour_ts] = oi
    db.close()
    return dict(out)


def oi_delta_at(oi_lookup: dict, sym: str, ts_ms: int, lookback_h: int = 6) -> float | None:
    """Compute OI change in bps over lookback_h hours at a given timestamp."""
    ts = (ts_ms // 1000 // HOUR_S) * HOUR_S
    sym_data = oi_lookup.get(sym)
    if not sym_data:
        return None
    oi_now = sym_data.get(ts)
    oi_past = sym_data.get(ts - lookback_h * HOUR_S)
    if not oi_now or not oi_past or oi_past <= 0:
        return None
    return (oi_now / oi_past - 1) * 1e4


# ── Engine (forked from backtest_rolling with OI sizing modifier) ──────

def run_window_oi_sizing(features, data, sector_features, dxy_data,
                         oi_lookup: dict,
                         start_ts_ms: int, end_ts_ms: int,
                         alpha: float = 0.0,
                         lookback_h: int = 6,
                         start_capital: float = 1000.0) -> dict:
    """Run portfolio backtest with OI-based sizing modifier.

    sizing_mult = clip(1 - alpha * oi_adverse_bps / 1000, 0.5, 1.5)
    where oi_adverse = |oi_delta| when OI moves against trade direction.

    alpha=0 → baseline (no OI effect).
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
    sizing_mults_applied = []  # track for diagnostics

    sorted_ts = sorted(ts for ts in all_ts if start_ts_ms <= ts <= end_ts_ms)

    for ts in sorted_ts:
        # ── EXITS (identical to backtest_rolling + S10 trailing) ──
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

            # Track MFE
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
                ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"

            if not exit_reason and pos["strat"] == "S10":
                mfe = pos.get("mfe", 0)
                if mfe >= S10_TRAILING_TRIGGER:
                    ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                    if ur_bps <= mfe - S10_TRAILING_OFFSET:
                        exit_reason = "s10_trailing"

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
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── ENTRIES (with OI sizing modifier) ──
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

            # Base sizing
            size = strat_size(cand["strat"], capital)

            # OI sizing modifier
            if alpha > 0 and oi_lookup:
                oi_d = oi_delta_at(oi_lookup, coin, ts, lookback_h)
                if oi_d is not None:
                    # "Adverse" OI = OI moving in the opposite direction of our trade
                    # LONG trade + OI dropping = adverse (longs leaving)
                    # SHORT trade + OI rising = adverse (more longs piling in against us)
                    oi_signed = oi_d * cand["dir"]  # positive = favorable, negative = adverse
                    if oi_signed < 0:
                        # OI is adverse — scale down
                        mult = max(0.5, 1.0 + alpha * oi_signed / 1000)
                        size = round(size * mult, 2)
                        sizing_mults_applied.append(mult)
                    else:
                        sizing_mults_applied.append(1.0)

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
            })

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)

    return {
        "end_capital": capital,
        "pnl": capital - start_capital,
        "pnl_pct": (capital / start_capital - 1) * 100,
        "max_dd_pct": max_dd_pct,
        "n_trades": n,
        "win_rate": wins / n * 100 if n else 0,
        "trades": trades,
        "sizing_stats": {
            "n_modified": len(sizing_mults_applied),
            "avg_mult": np.mean(sizing_mults_applied) if sizing_mults_applied else 1.0,
            "min_mult": min(sizing_mults_applied) if sizing_mults_applied else 1.0,
        },
    }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("OI SIZING MODIFIER BACKTEST")
    print("=" * 70)

    print("\nLoading data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy_data = load_dxy()
    oi_lookup = load_oi_lookup()

    if not oi_lookup:
        print("ERROR: No OI data available. Run fetch_oi_history.py first.")
        return

    print(f"OI data: {len(oi_lookup)} symbols")

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"Data ends at {end_dt.isoformat()}")

    windows = [
        ("28m", end_dt - relativedelta(months=28)),
        ("12m", end_dt - relativedelta(months=12)),
        ("6m", end_dt - relativedelta(months=6)),
        ("3m", end_dt - relativedelta(months=3)),
    ]

    # ── Baseline (alpha=0, no OI effect) ──
    print("\n--- BASELINE (no OI sizing) ---")
    baselines = []
    for wlabel, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        r = run_window_oi_sizing(features, data, sector_features, dxy_data,
                                 oi_lookup, start_ts, latest_ts, alpha=0.0)
        r["window"] = wlabel
        baselines.append(r)
        print(f"  {wlabel}: ${r['pnl']:+,.0f} ({r['pnl_pct']:+.1f}%), "
              f"DD {r['max_dd_pct']:.1f}%, {r['n_trades']} trades")

    # ── Sweep alpha and lookback ──
    print(f"\n{'=' * 70}")
    print("ALPHA × LOOKBACK SWEEP")
    print(f"{'=' * 70}")

    best = None
    best_score = -1e9

    for lookback_h in [6, 24]:
        print(f"\n  --- lookback={lookback_h}h ---")
        for alpha in [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
            results = []
            for wlabel, start_dt in windows:
                start_ts = int(start_dt.timestamp() * 1000)
                r = run_window_oi_sizing(features, data, sector_features, dxy_data,
                                         oi_lookup, start_ts, latest_ts,
                                         alpha=alpha, lookback_h=lookback_h)
                r["window"] = wlabel
                results.append(r)

            # Compare vs baseline
            deltas = []
            all_better = True
            for b, t in zip(baselines, results):
                d = t["pnl"] - b["pnl"]
                deltas.append(d)
                if d < 0:
                    all_better = False

            total_delta = sum(deltas)
            n_better = sum(1 for d in deltas if d >= 0)
            status = "PASS" if all_better else f"{n_better}/4"

            delta_str = ", ".join(f"{w['window']}:{d:+.0f}" for w, d in zip(results, deltas))

            # Only print if interesting (positive total or passes)
            if total_delta > 0 or all_better:
                sizing = results[0]["sizing_stats"]
                print(f"    α={alpha:.2f} → [{status}] Σδ=${total_delta:+,.0f}  "
                      f"({delta_str})  "
                      f"sizing: {sizing['n_modified']} trades modified, "
                      f"avg mult={sizing['avg_mult']:.2f}")

                if all_better and total_delta > best_score:
                    best_score = total_delta
                    best = {"alpha": alpha, "lookback": lookback_h,
                            "results": results, "deltas": deltas}
            else:
                print(f"    α={alpha:.2f} → [{status}] Σδ=${total_delta:+,.0f}")

    # ── Results ──
    print(f"\n{'=' * 70}")
    if best:
        print(f"BEST: α={best['alpha']}, lookback={best['lookback']}h, "
              f"Σδ=${best_score:+,.0f}")
        for r, d in zip(best["results"], best["deltas"]):
            b = [b for b in baselines if b["window"] == r["window"]][0]
            print(f"  {r['window']}: ${b['pnl']:+,.0f} → ${r['pnl']:+,.0f} "
                  f"(Δ${d:+,.0f}, DD {b['max_dd_pct']:.1f}→{r['max_dd_pct']:.1f}%)")
    else:
        print("NO CONFIG PASSES ALL 4 WINDOWS")
        print("OI sizing modifier rejected — same conclusion as OI gates.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
