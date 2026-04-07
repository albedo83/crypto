"""Flow signals Phase 3 — LONG-only validation + overlap analysis with S8/S9/S10.

Tests:
1. Volume Divergence LONG-only (selling exhaustion) — parameter sweep
2. Volume Spike Reversal LONG-only (flush + wick) — parameter sweep
3. Overlap analysis: how often do these fire at the same time as S8/S9/S10?
4. Combined portfolio: existing signals + new signals together

Usage:
    python3 -m backtests.backtest_flow3
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import (
    load_3y_candles, build_features,
    TOKENS, COST_BPS, POSITION_SIZE,
    TRAIN_END, TEST_START,
)

COST = COST_BPS
SIZE = POSITION_SIZE
MAX_POS = 6
COOLDOWN_MS = 24 * 3600 * 1000  # 24h


def score(trades):
    if not trades:
        return {"n": 0, "pnl": 0, "avg": 0, "win": 0}
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"] > 0)
    return {"n": n, "pnl": round(pnl, 2), "avg": round(avg, 1), "win": round(wins / n * 100, 0)}


def monte_carlo(real_trades, all_entries, hold, n_sims=500):
    if len(real_trades) < 10 or not all_entries:
        return None
    real_pnl = sum(t["pnl"] for t in real_trades)
    n_trades = len(real_trades)
    sim_pnls = []
    for _ in range(n_sims):
        sim = 0
        sampled = random.sample(all_entries, min(n_trades, len(all_entries)))
        for coin, idx, candles in sampled:
            if idx + hold >= len(candles):
                continue
            entry = candles[idx]["o"] if candles[idx]["o"] > 0 else candles[idx]["c"]
            exit_p = candles[idx + hold]["c"]
            if entry <= 0:
                continue
            gross = (exit_p / entry - 1) * 1e4  # LONG only
            sim += SIZE * (gross - COST) / 1e4
        sim_pnls.append(sim)
    sim_mean = float(np.mean(sim_pnls))
    sim_std = float(np.std(sim_pnls)) if len(sim_pnls) > 1 else 1
    z = (real_pnl - sim_mean) / sim_std if sim_std > 0 else 0
    return round(z, 2)


def _all_entries(data, hold):
    entries = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(len(candles) - hold):
            entries.append((coin, i, candles))
    return entries


# ── Signal Detectors ─────────────────────────────────────────

def detect_voldiv_long(data, pw, vw, thr, vol_drop=-0.3):
    """Selling exhaustion: price dropped >thr bps over pw candles, volume declining."""
    signals = []
    half = vw // 2
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(max(pw, vw) + 1, len(candles) - 1):
            p_now = candles[i]["c"]
            p_prev = candles[i - pw]["c"]
            if p_prev <= 0 or p_now <= 0:
                continue
            price_ret = (p_now / p_prev - 1) * 1e4
            if price_ret >= -thr:  # not enough drop
                continue

            vol_recent = np.mean([candles[i - j]["v"] for j in range(half)])
            vol_prior = np.mean([candles[i - half - j]["v"] for j in range(half)])
            if vol_prior <= 0:
                continue
            vol_change = vol_recent / vol_prior - 1
            if vol_change >= vol_drop:  # volume not declining enough
                continue

            signals.append({
                "coin": coin, "t": candles[i]["t"],
                "entry_idx": i + 1, "direction": 1,
                "strength": abs(price_ret) * abs(vol_change),
                "type": "VD",
                "price_ret": price_ret, "vol_change": vol_change,
            })
    return signals


def detect_volspike_long(data, vlb, vm, wr):
    """Flush reversal: volume spike + long lower wick on down candle → LONG."""
    signals = []
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        for i in range(vlb + 1, len(candles) - 1):
            c = candles[i]
            o, h, l, cl, v = c["o"], c["h"], c["l"], c["c"], c["v"]
            if o <= 0 or v <= 0:
                continue
            rng = h - l
            if rng <= 0:
                continue
            if cl >= o:  # only red/down candles (flush)
                continue

            avg_vol = np.mean([candles[i - j]["v"] for j in range(1, vlb + 1)])
            if avg_vol <= 0 or v < avg_vol * vm:
                continue

            lower_wick = min(o, cl) - l
            if lower_wick / rng < wr:
                continue

            signals.append({
                "coin": coin, "t": candles[i]["t"],
                "entry_idx": i + 1, "direction": 1,
                "strength": v / avg_vol,
                "type": "VS",
                "vol_ratio": v / avg_vol, "wick_pct": lower_wick / rng,
            })
    return signals


# ── Existing Signals (for overlap) ───────────────────────────

def detect_s8(data, features):
    """S8 capitulation: drawdown < -40%, vol_z > 1.0, ret_24h < -800, btc_7d < -300."""
    signals = []
    for coin in TOKENS:
        if coin not in features or coin not in data:
            continue
        candles = data[coin]
        for f in features[coin]:
            if (f.get("drawdown", 0) < -4000
                    and f.get("vol_ratio", 0) > 1.0  # proxy for vol_z
                    and f.get("ret_6h", 0) < -800
                    and f.get("btc_7d", 0) < -300):
                signals.append({
                    "coin": coin, "t": f["t"],
                    "entry_idx": f["_idx"] + 1, "direction": 1,
                    "type": "S8",
                })
    return signals


def detect_s9(data, features):
    """S9 fade extreme: |ret_24h| > 2000 bps → fade."""
    signals = []
    for coin in TOKENS:
        if coin not in features or coin not in data:
            continue
        for f in features[coin]:
            ret = f.get("ret_6h", 0)  # ret_6h ≈ 24h on 4h candles (6 candles)
            if abs(ret) > 2000:
                signals.append({
                    "coin": coin, "t": f["t"],
                    "entry_idx": f["_idx"] + 1,
                    "direction": -1 if ret > 0 else 1,
                    "type": "S9",
                })
    return signals


def detect_s10(data, features):
    """S10 squeeze: low vol_ratio + breakout → fade breakout."""
    signals = []
    for coin in TOKENS:
        if coin not in features or coin not in data:
            continue
        candles = data[coin]
        for f in features[coin]:
            idx = f["_idx"]
            if f.get("vol_ratio", 1) > 0.7:  # not compressed enough
                continue
            if idx < 6 or idx >= len(candles) - 1:
                continue
            # Check for breakout in last candle
            c = candles[idx]
            rng = (c["h"] - c["l"]) / c["c"] * 100 if c["c"] > 0 else 0
            if rng < 2.0:  # not enough expansion
                continue
            # Fade the breakout direction
            direction = -1 if c["c"] > c["o"] else 1
            signals.append({
                "coin": coin, "t": f["t"],
                "entry_idx": idx + 1, "direction": direction,
                "type": "S10",
            })
    return signals


# ── Simple Backtest ──────────────────────────────────────────

def simple_bt(signals, data, hold, period="all"):
    """Backtest with position limits and cooldown."""
    sorted_sigs = sorted(signals, key=lambda s: (s["t"], -s.get("strength", 0)))
    cooldowns = {}
    active = {}
    trades = []

    for sig in sorted_sigs:
        t = sig["t"]
        if period == "train" and t >= TRAIN_END:
            continue
        if period == "test" and t < TEST_START:
            continue

        # Close expired positions
        for c in list(active.keys()):
            pos = active[c]
            candles = data[c]
            exit_idx = pos["entry_idx"] + hold
            if exit_idx < len(candles) and candles[exit_idx]["t"] <= t:
                exit_p = candles[exit_idx]["c"]
                if exit_p > 0:
                    gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
                    net = gross - COST
                    trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                                   "t": candles[exit_idx]["t"], "direction": pos["direction"],
                                   "type": pos.get("type", "?")})
                del active[c]
                cooldowns[c] = candles[exit_idx]["t"] + COOLDOWN_MS

        coin = sig["coin"]
        if len(active) >= MAX_POS or coin in active:
            continue
        if coin in cooldowns and t < cooldowns[coin]:
            continue
        candles = data[coin]
        entry_idx = sig["entry_idx"]
        if entry_idx >= len(candles) or entry_idx + hold >= len(candles):
            continue
        entry_price = candles[entry_idx]["o"]
        if entry_price <= 0:
            continue

        active[coin] = {"entry_idx": entry_idx, "entry_price": entry_price,
                        "direction": sig["direction"], "type": sig.get("type", "?")}

    for c, pos in active.items():
        candles = data[c]
        exit_idx = min(pos["entry_idx"] + hold, len(candles) - 1)
        exit_p = candles[exit_idx]["c"]
        if exit_p > 0:
            gross = pos["direction"] * (exit_p / pos["entry_price"] - 1) * 1e4
            net = gross - COST
            trades.append({"coin": c, "net": net, "pnl": SIZE * net / 1e4,
                           "t": candles[exit_idx]["t"], "direction": pos["direction"],
                           "type": pos.get("type", "?")})

    return trades


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Loading candles...")
    data = load_3y_candles()
    print(f"Loaded {len(data)} tokens")

    for coin in list(data.keys()):
        for c in data[coin]:
            for k in ("o", "h", "l", "c", "v"):
                if k in c:
                    c[k] = float(c[k])

    print("Building features...")
    features = build_features(data)

    # ── 1. Volume Divergence LONG-only — sweep ───────────────
    print("\n" + "=" * 60)
    print("1. VOLUME DIVERGENCE (LONG-only) — Parameter Sweep")
    print("=" * 60)

    best_vd = None
    best_vd_z = 0
    for pw in [6, 9, 12, 15, 18]:
        for vw in [6, 9, 12]:
            for thr in [1000, 1500, 2000, 2500]:
                for vdrop in [-0.2, -0.3, -0.4]:
                    for hold in [6, 12]:
                        signals = detect_voldiv_long(data, pw, vw, thr, vdrop)
                        tr = simple_bt(signals, data, hold, "train")
                        te = simple_bt(signals, data, hold, "test")
                        str_tr, str_te = score(tr), score(te)
                        if (str_tr["n"] >= 20 and str_te["n"] >= 10
                                and str_tr["pnl"] > 0 and str_te["pnl"] > 0
                                and str_tr["avg"] > 0 and str_te["avg"] > 0):
                            all_t = simple_bt(signals, data, hold, "all")
                            entries = _all_entries(data, hold)
                            z = monte_carlo(all_t, entries, hold)
                            if z and z > best_vd_z:
                                best_vd_z = z
                                best_vd = {
                                    "pw": pw, "vw": vw, "thr": thr, "vdrop": vdrop,
                                    "hold": hold, "z": z,
                                    "train": str_tr, "test": str_te,
                                    "signals": signals,
                                }
                                print(f"  NEW BEST z={z} | pw={pw} vw={vw} thr={thr} vd={vdrop} hold={hold}"
                                      f" | Tr: {str_tr['n']}t {str_tr['avg']:+.0f}bps ${str_tr['pnl']:+.0f}"
                                      f" | Te: {str_te['n']}t {str_te['avg']:+.0f}bps ${str_te['pnl']:+.0f}")

    if best_vd:
        print(f"\n  BEST VD: z={best_vd['z']}")
        print(f"    pw={best_vd['pw']} vw={best_vd['vw']} thr={best_vd['thr']} vdrop={best_vd['vdrop']} hold={best_vd['hold']}")
        print(f"    Train: {best_vd['train']}")
        print(f"    Test:  {best_vd['test']}")
    else:
        print("  No VD config passed all filters")

    # ── 2. Volume Spike Reversal LONG-only — sweep ───────────
    print("\n" + "=" * 60)
    print("2. VOLUME SPIKE REVERSAL (LONG-only) — Parameter Sweep")
    print("=" * 60)

    best_vs = None
    best_vs_z = 0
    for vlb in [20, 30, 42, 60]:
        for vm in [3.0, 4.0, 5.0, 6.0]:
            for wr in [0.3, 0.4, 0.5, 0.6]:
                for hold in [3, 6, 12]:
                    signals = detect_volspike_long(data, vlb, vm, wr)
                    tr = simple_bt(signals, data, hold, "train")
                    te = simple_bt(signals, data, hold, "test")
                    str_tr, str_te = score(tr), score(te)
                    if (str_tr["n"] >= 15 and str_te["n"] >= 8
                            and str_tr["pnl"] > 0 and str_te["pnl"] > 0
                            and str_tr["avg"] > 0 and str_te["avg"] > 0):
                        all_t = simple_bt(signals, data, hold, "all")
                        entries = _all_entries(data, hold)
                        z = monte_carlo(all_t, entries, hold)
                        if z and z > best_vs_z:
                            best_vs_z = z
                            best_vs = {
                                "vlb": vlb, "vm": vm, "wr": wr,
                                "hold": hold, "z": z,
                                "train": str_tr, "test": str_te,
                                "signals": signals,
                            }
                            print(f"  NEW BEST z={z} | vlb={vlb} vm={vm}x wr={wr} hold={hold}"
                                  f" | Tr: {str_tr['n']}t {str_tr['avg']:+.0f}bps ${str_tr['pnl']:+.0f}"
                                  f" | Te: {str_te['n']}t {str_te['avg']:+.0f}bps ${str_te['pnl']:+.0f}")

    if best_vs:
        print(f"\n  BEST VS: z={best_vs['z']}")
        print(f"    vlb={best_vs['vlb']} vm={best_vs['vm']}x wr={best_vs['wr']} hold={best_vs['hold']}")
        print(f"    Train: {best_vs['train']}")
        print(f"    Test:  {best_vs['test']}")
    else:
        print("  No VS config passed all filters")

    # ── 3. Overlap Analysis ──────────────────────────────────
    print("\n" + "=" * 60)
    print("3. OVERLAP ANALYSIS — New signals vs S8/S9/S10")
    print("=" * 60)

    s8_sigs = detect_s8(data, features)
    s9_sigs = detect_s9(data, features)
    s10_sigs = detect_s10(data, features)

    print(f"  Existing signals: S8={len(s8_sigs)} S9={len(s9_sigs)} S10={len(s10_sigs)}")

    # Build lookup: (coin, candle_t) → set of signal types
    existing_events = defaultdict(set)
    for s in s8_sigs:
        existing_events[(s["coin"], s["t"])].add("S8")
    for s in s9_sigs:
        existing_events[(s["coin"], s["t"])].add("S9")
    for s in s10_sigs:
        existing_events[(s["coin"], s["t"])].add("S10")

    # Also check ±1 candle (4h) window for near-overlaps
    WINDOW_MS = 4 * 3600 * 1000  # 1 candle

    for label, sigs in [("VD", best_vd["signals"] if best_vd else []),
                        ("VS", best_vs["signals"] if best_vs else [])]:
        if not sigs:
            continue
        exact = 0
        near = 0
        unique = 0
        overlap_detail = defaultdict(int)

        for s in sigs:
            key = (s["coin"], s["t"])
            if key in existing_events:
                exact += 1
                for ex_type in existing_events[key]:
                    overlap_detail[f"{label}+{ex_type}"] += 1
            else:
                # Check ±1 candle
                found = False
                for dt in [-WINDOW_MS, WINDOW_MS]:
                    near_key = (s["coin"], s["t"] + dt)
                    if near_key in existing_events:
                        near += 1
                        found = True
                        for ex_type in existing_events[near_key]:
                            overlap_detail[f"{label}~{ex_type}"] += 1
                        break
                if not found:
                    unique += 1

        total = len(sigs)
        print(f"\n  {label} ({total} signals):")
        print(f"    Exact overlap:  {exact} ({exact/total*100:.1f}%)")
        print(f"    Near (±4h):     {near} ({near/total*100:.1f}%)")
        print(f"    Unique:         {unique} ({unique/total*100:.1f}%)")
        if overlap_detail:
            print(f"    Detail: {dict(overlap_detail)}")

    # ── 4. Unique-only backtest ──────────────────────────────
    print("\n" + "=" * 60)
    print("4. UNIQUE-ONLY BACKTEST (no overlap with S8/S9/S10)")
    print("=" * 60)

    for label, sigs_all, hold in [
        ("VD", best_vd["signals"] if best_vd else [], best_vd["hold"] if best_vd else 6),
        ("VS", best_vs["signals"] if best_vs else [], best_vs["hold"] if best_vs else 12),
    ]:
        if not sigs_all:
            continue
        # Filter to unique signals only
        unique_sigs = []
        for s in sigs_all:
            key = (s["coin"], s["t"])
            if key in existing_events:
                continue
            near_overlap = False
            for dt in [-WINDOW_MS, WINDOW_MS]:
                if (s["coin"], s["t"] + dt) in existing_events:
                    near_overlap = True
                    break
            if not near_overlap:
                unique_sigs.append(s)

        print(f"\n  {label} unique signals: {len(unique_sigs)} / {len(sigs_all)}")
        for period in ["train", "test"]:
            trades = simple_bt(unique_sigs, data, hold, period)
            s = score(trades)
            print(f"    {period:5s}: {s['n']}t avg={s['avg']:+.0f}bps WR={s['win']:.0f}% ${s['pnl']:+.0f}")

        all_t = simple_bt(unique_sigs, data, hold, "all")
        entries = _all_entries(data, hold)
        z = monte_carlo(all_t, entries, hold)
        print(f"    MC z-score (unique): {z}")

    # ── 5. Summary ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if best_vd:
        print(f"\n  Volume Divergence LONG:")
        print(f"    Best config: pw={best_vd['pw']} vw={best_vd['vw']} thr={best_vd['thr']} vdrop={best_vd['vdrop']} hold={best_vd['hold']}")
        print(f"    z={best_vd['z']} | Train: {best_vd['train']['n']}t {best_vd['train']['avg']:+.0f}bps | Test: {best_vd['test']['n']}t {best_vd['test']['avg']:+.0f}bps")
    if best_vs:
        print(f"\n  Volume Spike Reversal LONG:")
        print(f"    Best config: vlb={best_vs['vlb']} vm={best_vs['vm']}x wr={best_vs['wr']} hold={best_vs['hold']}")
        print(f"    z={best_vs['z']} | Train: {best_vs['train']['n']}t {best_vs['train']['avg']:+.0f}bps | Test: {best_vs['test']['n']}t {best_vs['test']['avg']:+.0f}bps")
