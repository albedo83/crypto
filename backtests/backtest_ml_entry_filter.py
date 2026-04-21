"""Multi-feature entry filter for S5 (logistic regression, walk-forward).

Motivation: univariate thresholds on btc30, oi_delta, vol_z, etc. all fail
4/4 walk-forward. But the rollback audit shows *some* signal in several
features jointly (btc30 entry +168 rollback vs -68 kept; oi_delta +637 vs
+295; Δ div peak-exit -462 vs -21). The question: can a linear combination
of entry features separate rollbacks from kept winners cleanly enough to
beat baseline when used as an entry skip filter?

Method:
1. Run instrumented backtest on the full 3y history to capture per-trade
   entry features + outcome.
2. Walk-forward: for each rolling window (28m/12m/6m/3m), train logistic
   regression on trades BEFORE window start, test threshold sweep on window.
3. For each threshold (0.4 .. 0.8), simulate "skip entry if p(loss) > T".
4. Measure end-of-window capital for each (threshold, window) pair.
5. Variant passes if it beats baseline on all 4 windows.

Uses sklearn if available, else a hand-rolled logistic via gradient descent
(no external dep required).
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from dateutil.relativedelta import relativedelta

from analysis.bot.config import (
    MACRO_STRATEGIES, TRADE_BLACKLIST, TOKEN_SECTOR,
    STOP_LOSS_BPS, STOP_LOSS_S8,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH, S9_ADAPTIVE_STOP,
    MAX_POSITIONS, MAX_SAME_DIRECTION, MAX_PER_SECTOR,
    MAX_MACRO_SLOTS, MAX_TOKEN_SLOTS, OI_LONG_GATE_BPS,
    S10_ALLOW_LONGS, S10_ALLOWED_TOKENS,
)
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    rolling_windows, load_oi, oi_delta_24h_pct,
    detect_squeeze, strat_size, COST,
    HOLD_CANDLES, STRAT_Z, S9_EARLY_EXIT_CANDLES, S9_EARLY_EXIT_BPS,
)


# ── Minimal logistic regression (no sklearn) ──────────────────────────
def _sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))

def logistic_fit(X, y, lr=0.05, epochs=3000, l2=0.01):
    """Fit logistic regression via gradient descent with L2."""
    n, d = X.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        z = X @ w + b
        p = _sigmoid(z)
        grad_w = X.T @ (p - y) / n + l2 * w
        grad_b = np.mean(p - y)
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b

def logistic_predict(X, w, b):
    return _sigmoid(X @ w + b)


# ── Instrumented backtest (S5 focus) ──────────────────────────────────
FEATURE_NAMES = [
    "btc30", "btc7", "oi_delta", "vol_z", "div_signed",
    "sector_momentum", "div_magnitude",
]


def capture_entry_snap(coin, ts, direction, sector_features, oi_data,
                        feat_by_ts, btc_ret):
    sf = sector_features.get((ts, coin))
    f = feat_by_ts.get(ts, {}).get(coin, {})
    div = sf.get("divergence") if sf else 0.0
    div_signed = direction * div
    oi_d = oi_delta_24h_pct(oi_data, coin, ts) if oi_data is not None else 0.0
    return {
        "btc30": btc_ret(ts, 180),
        "btc7": btc_ret(ts, 42),
        "oi_delta": oi_d if oi_d is not None else 0.0,
        "vol_z": f.get("vol_z", 0.0),
        "div_signed": div_signed,
        "sector_momentum": sf.get("sector_ret", 0.0) if sf else 0.0,
        "div_magnitude": abs(div),
    }


def run_capture(features, data, sector_features, oi_data,
                start_ts_ms, end_ts_ms):
    """Run full backtest, return per-trade (entry_snap, pnl, strat, entry_t)."""
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
        if ts not in btc_by_ts: return 0.0
        i = btc_by_ts[ts]
        if i < lookback or btc_closes[i - lookback] <= 0: return 0.0
        return (btc_closes[i] / btc_closes[i - lookback] - 1) * 1e4

    positions = {}
    trades = []
    cooldown = {}
    capital = 1000.0

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
            if current <= 0: continue

            if pos["dir"] == 1:
                best_bps = (candle["h"] / pos["entry"] - 1) * 1e4
                worst_bps = (candle["l"] / pos["entry"] - 1) * 1e4
            else:
                best_bps = -(candle["l"] / pos["entry"] - 1) * 1e4
                worst_bps = -(candle["h"] / pos["entry"] - 1) * 1e4
            if best_bps > pos.get("mfe", 0): pos["mfe"] = best_bps
            if worst_bps < pos.get("mae", 0): pos["mae"] = worst_bps

            stop = STOP_LOSS_S8 if pos["strat"] == "S8" else (pos.get("stop", 0) or STOP_LOSS_BPS)
            exit_reason = None
            exit_price = current
            if pos["dir"] == 1:
                worst = (candle["l"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"; exit_price = pos["entry"] * (1 + stop / 1e4)
            else:
                worst = -(candle["h"] / pos["entry"] - 1) * 1e4
                if worst < stop:
                    exit_reason = "stop"; exit_price = pos["entry"] * (1 - stop / 1e4)

            # D2 dead-timeout
            if not exit_reason and held >= pos["hold"] - 3:
                cur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                mfe = pos.get("mfe", 0.0)
                mae = pos.get("mae", 0.0)
                if mfe <= 150 and mae <= -1000 and cur_bps <= mae + 300:
                    exit_reason = "dead_timeout"

            if held >= pos["hold"]:
                exit_reason = exit_reason or "timeout"

            if not exit_reason and pos["strat"] == "S9" and held >= S9_EARLY_EXIT_CANDLES:
                ur_bps = pos["dir"] * (current / pos["entry"] - 1) * 1e4
                if ur_bps < S9_EARLY_EXIT_BPS:
                    exit_reason = "s9_early_exit"

            if exit_reason:
                gross = pos["dir"] * (exit_price / pos["entry"] - 1) * 1e4
                net = gross - COST
                pnl = pos["size"] * net / 1e4
                capital += pnl
                trades.append({
                    "coin": coin, "strat": pos["strat"], "dir": pos["dir"],
                    "entry_t": pos["entry_t"], "exit_t": ts,
                    "pnl": pnl, "net": net, "mfe": pos["mfe"], "mae": pos["mae"],
                    "size": pos["size"], "entry_snap": pos["entry_snap"],
                })
                del positions[coin]
                cooldown[coin] = ts + 24 * 3600 * 1000

        # ── ENTRIES (mirror run_window) ──
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
            if not f: continue
            ret_24h = f.get("ret_6h", 0)
            if btc30 > 2000:
                candidates.append({"coin": coin, "dir": 1, "strat": "S1",
                                   "z": STRAT_Z["S1"], "hold": HOLD_CANDLES["S1"],
                                   "strength": max(f.get("ret_42h", 0), 0)})
            sf = sector_features.get((ts, coin))
            if sf and abs(sf["divergence"]) >= S5_DIV_THRESHOLD and sf["vol_z"] >= S5_VOL_Z_MIN:
                candidates.append({"coin": coin, "dir": 1 if sf["divergence"] > 0 else -1,
                                   "strat": "S5", "z": STRAT_Z["S5"], "hold": HOLD_CANDLES["S5"],
                                   "strength": abs(sf["divergence"])})
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH
                    and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and ret_24h < S8_RET_24H_THRESH and btc7 < S8_BTC_7D_THRESH):
                candidates.append({"coin": coin, "dir": 1, "strat": "S8",
                                   "z": STRAT_Z["S8"], "hold": HOLD_CANDLES["S8"],
                                   "strength": abs(f.get("drawdown", 0))})
            if abs(ret_24h) >= S9_RET_THRESH:
                s9_dir = -1 if ret_24h > 0 else 1
                s9_stop = max(STOP_LOSS_BPS, -500 - abs(ret_24h) / 8) if S9_ADAPTIVE_STOP else 0
                candidates.append({"coin": coin, "dir": s9_dir, "strat": "S9",
                                   "z": STRAT_Z["S9"], "hold": HOLD_CANDLES["S9"],
                                   "strength": abs(ret_24h), "stop": s9_stop})
            # S10 candidates skipped: this script only filters S5 entries. Capturing
            # S10 would require vol_ratio signature matching and doesn't inform S5
            # ML. S5 slot allocation is independent.

        candidates.sort(key=lambda x: (x["z"], x["strength"]), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions: continue
            seen.add(coin)
            if coin in TRADE_BLACKLIST: continue
            if cand["dir"] == 1 and oi_data is not None:
                oi_d = oi_delta_24h_pct(oi_data, coin, ts)
                if oi_d is not None and oi_d < -OI_LONG_GATE_BPS: continue
            if len(positions) >= MAX_POSITIONS: break
            if cand["dir"] == 1 and n_long >= MAX_SAME_DIRECTION: continue
            if cand["dir"] == -1 and n_short >= MAX_SAME_DIRECTION: continue
            if cand["strat"] in macro_strats and n_macro >= MAX_MACRO_SLOTS: continue
            if cand["strat"] not in macro_strats and n_token >= MAX_TOKEN_SLOTS: continue
            sec = TOKEN_SECTOR.get(coin)
            if sec:
                sc = sum(1 for p in positions.values() if TOKEN_SECTOR.get(p["coin"]) == sec)
                if sc >= MAX_PER_SECTOR: continue
            f = feat_by_ts.get(ts, {}).get(coin)
            idx_f = f.get("_idx") if f else None
            if idx_f is None or idx_f + 1 >= len(data[coin]): continue
            entry = data[coin][idx_f + 1]["o"]
            if entry <= 0: continue
            size = strat_size(cand["strat"], capital)
            snap = capture_entry_snap(coin, ts, cand["dir"], sector_features, oi_data,
                                      feat_by_ts, btc_ret)
            positions[coin] = {
                "dir": cand["dir"], "entry": entry, "idx": idx_f + 1,
                "entry_t": data[coin][idx_f + 1]["t"],
                "strat": cand["strat"], "hold": cand["hold"],
                "size": size, "coin": coin,
                "stop": cand.get("stop", 0),
                "mfe": 0.0, "mae": 0.0, "entry_snap": snap,
            }
            if cand["dir"] == 1: n_long += 1
            else: n_short += 1
            if cand["strat"] in macro_strats: n_macro += 1
            else: n_token += 1

    return trades, capital


# ── Walk-forward classifier + threshold sweep ─────────────────────────
def simulate_with_filter(trades, start_ts, end_ts, skip_fn):
    """Replay trades with optional skip filter.

    Returns (total_pnl, kept_trades, n_skipped, precision, recall).
    Metrics are intentionally non-compounded: raw sum of pnl, so the
    filter's added value is unambiguous regardless of capital path.
    """
    kept, skipped = [], []
    for t in sorted(trades, key=lambda x: x["entry_t"]):
        if t["entry_t"] < start_ts or t["entry_t"] > end_ts:
            continue
        if skip_fn is not None and skip_fn(t):
            skipped.append(t)
        else:
            kept.append(t)
    total_pnl = sum(t["pnl"] for t in kept)
    # Precision = of skipped trades, how many were actual losers?
    # Recall    = of actual losers in window, how many did we skip?
    n_skip_loss = sum(1 for t in skipped if t["pnl"] < 0)
    n_all_loss = sum(1 for t in kept + skipped if t["pnl"] < 0)
    prec = n_skip_loss / len(skipped) if skipped else 0.0
    rec = n_skip_loss / n_all_loss if n_all_loss else 0.0
    return total_pnl, kept, len(skipped), prec, rec


def main():
    print("Loading data…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    oi_data = load_oi()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    # Capture all trades from the full 3y history
    start_3y_dt = end_dt - relativedelta(months=36)
    start_3y_ts = int(start_3y_dt.timestamp() * 1000)
    print(f"Capturing trades from {start_3y_dt.date()} to {end_dt.date()}…")
    trades, _ = run_capture(features, data, sector_features, oi_data,
                             start_3y_ts, latest_ts)
    print(f"Captured {len(trades)} trades total\n")

    # Split trades for training: use strictly earlier trades than each test window
    S5 = [t for t in trades if t["strat"] == "S5"]
    print(f"S5 trades: {len(S5)}")
    # Label: rollback if (MFE>=300 and pnl<0) OR (outright loss)
    # We want a classifier that flags LIKELY LOSSES so we can skip them.
    # Two label variants:
    #   A) trade is a net loss (pnl<0)
    #   B) trade is a "rollback" (MFE>=300 AND pnl<0)
    # Variant A is more actionable (covers all losers); B is narrower.
    print(f"  S5 net losers: {sum(1 for t in S5 if t['pnl'] < 0)} "
          f"({sum(1 for t in S5 if t['pnl'] < 0) / len(S5) * 100:.0f}%)")
    print(f"  S5 rollbacks (MFE>=300 & pnl<0): "
          f"{sum(1 for t in S5 if t['mfe'] >= 300 and t['pnl'] < 0)}")
    print()

    WIN_LABELS = ["28 mois", "12 mois", "6 mois", "3 mois"]
    windows = [(lbl, s) for (lbl, s) in rolling_windows(end_dt) if lbl in WIN_LABELS]

    # Baseline: no filter — raw sum of S5 pnl per window
    print("=" * 110)
    print("BASELINE S5 sum of PnL (no filter):")
    base_ends = {}
    for lbl, start_dt in windows:
        start_ts = int(start_dt.timestamp() * 1000)
        pnl, kept, nskip, prec, rec = simulate_with_filter(S5, start_ts, latest_ts, None)
        base_ends[lbl] = pnl
        print(f"  {lbl}: ${pnl:+.0f} (n={len(kept)})")
    print()

    # Walk-forward classifier sweep
    LABEL_VARIANTS = {
        "loss":     lambda t: 1 if t["pnl"] < 0 else 0,
        "rollback": lambda t: 1 if (t["mfe"] >= 300 and t["pnl"] < 0) else 0,
    }
    THRESHOLDS = [0.40, 0.50, 0.55, 0.60, 0.65, 0.70]

    print("=" * 110)
    for label_name, label_fn in LABEL_VARIANTS.items():
        print(f"\n=== Target = {label_name} (1 means trade should be filtered out) ===")
        for thresh in THRESHOLDS:
            row = {}
            for lbl, start_dt in windows:
                start_ts = int(start_dt.timestamp() * 1000)
                # Train on trades strictly earlier than window start
                train = [t for t in S5 if t["entry_t"] < start_ts]
                if len(train) < 30:
                    row[lbl] = None
                    continue
                X_train = np.array([[t["entry_snap"][k] for k in FEATURE_NAMES] for t in train])
                y_train = np.array([label_fn(t) for t in train], dtype=float)
                if y_train.sum() < 5:
                    row[lbl] = None
                    continue
                mu = X_train.mean(axis=0)
                sd = X_train.std(axis=0) + 1e-8
                X_train_std = (X_train - mu) / sd
                w, b = logistic_fit(X_train_std, y_train)

                def skip_fn(trade, w=w, b=b, mu=mu, sd=sd, thresh=thresh):
                    x = np.array([trade["entry_snap"][k] for k in FEATURE_NAMES])
                    x_std = (x - mu) / sd
                    p = _sigmoid(x_std @ w + b)
                    return p > thresh

                pnl, kept, nskip, prec, rec = simulate_with_filter(S5, start_ts, latest_ts, skip_fn)
                row[lbl] = {"pnl": pnl, "skip": nskip, "kept": len(kept),
                            "prec": prec, "rec": rec, "n_train": len(train)}

            deltas = []
            for lbl in WIN_LABELS:
                if row[lbl] is None:
                    deltas.append("n/a         ")
                else:
                    d = row[lbl]["pnl"] - base_ends[lbl]
                    deltas.append(f"Δ{d:+7.0f} skip={row[lbl]['skip']:>2}")
            all_positive = all(row[w] is not None and row[w]["pnl"] > base_ends[w]
                                for w in WIN_LABELS)
            flag = "✓" if all_positive else "-"
            print(f"  {flag} thresh={thresh:.2f}:  " + "  ".join(f"{w}:{d}" for w, d in zip(WIN_LABELS, deltas)))

    print()
    print("=" * 110)
    print("Note: end capitals here reflect S5-trade-only capital path, not full")
    print("portfolio. A passing filter here means the classifier separates losers")
    print("from winners BETTER than chance on out-of-sample S5 entries.")


if __name__ == "__main__":
    main()
