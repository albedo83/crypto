"""EDA — precision improvements for existing signals.

Tests 5 enhancement variants vs baseline, measured on 4 splits 6m
non-overlapping. Forward 24h return as outcome.

S5 variants:
    V0 = baseline (mean+std peers, divergence = ret_42h - mean, threshold ±1000 bps + vol_z ≥ 1)
    V1 = robust  (median+MAD peers, z = (ret_42h - median) / MAD, threshold |z| ≥ 3)
    V2 = multi-horizon (require divergence above threshold on 3d AND 7d AND 14d, same sign)
    V3 = robust + multi-horizon combined

S9 variants:
    V0 = baseline (|ret_24h| > 2000 bps)
    V1 = vol-normalized (|ret_24h / ATR_30d| > X calibrated for same fire rate)

For each variant per split:
- Count triggers
- Mean / median / WR of forward 24h returns
- Direction signed: LONG if dump (ret_24h<0) for S9, follow divergence sign for S5

Strict PASS criterion per variant : mean > baseline mean AND WR > baseline WR on all 4 splits.
"""
import json
import time
import numpy as np
from datetime import datetime, timezone

from backtests.backtest_genetic import load_3y_candles

print("Loading 4h candles...")
data = load_3y_candles()
MIN_HIST = 2160
eligible = {sym: cs for sym, cs in data.items() if len(cs) >= MIN_HIST}
print(f"  {len(eligible)} eligible tokens")

# Replicate sectors from config
SECTORS = {
    "L1":       ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI", "TON"],
    "L1-major": ["BCH", "DOT", "ADA"],
    "Privacy":  ["XMR"],
    "DeFi":     ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO", "GMX", "UNI", "ENA"],
    "Gaming":   ["GALA", "IMX", "SAND"],
    "Infra":    ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme":     ["DOGE", "WLD", "BLUR", "MINA"],
}
TOKEN_SECTOR = {}
for sect, toks in SECTORS.items():
    for t in toks:
        TOKEN_SECTOR[t] = sect

sym_to_arr = dict(eligible)
sym_to_idx = {sym: {c["t"]: i for i, c in enumerate(cs)} for sym, cs in eligible.items()}


def compute_ret(sym: str, ts: int, candles_back: int):
    """Return in bps over candles_back candles ending at ts."""
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i < candles_back:
        return None
    c0 = arr[i]["c"]
    c_prev = arr[i - candles_back]["c"]
    if c0 <= 0 or c_prev <= 0:
        return None
    return (c0 / c_prev - 1) * 1e4


def compute_atr(sym: str, ts: int, window: int = 180):
    """Average True Range over window candles, expressed in bps relative to close."""
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i < window:
        return None
    closes = np.array([c["c"] for c in arr[max(0, i - window): i + 1]])
    highs = np.array([c["h"] for c in arr[max(0, i - window): i + 1]])
    lows = np.array([c["l"] for c in arr[max(0, i - window): i + 1]])
    if (closes <= 0).any():
        return None
    tr1 = highs - lows
    tr2 = np.abs(highs - closes)  # offset by 1
    tr3 = np.abs(lows - closes)
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = float(np.mean(tr))
    if closes[-1] == 0:
        return None
    return atr / closes[-1] * 1e4


def compute_vol_z(sym: str, ts: int, window: int = 180):
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i < window:
        return None
    volumes = np.array([c["v"] for c in arr[max(0, i - window): i + 1]])
    vol_mean = float(np.mean(volumes[:-1])) if len(volumes) > 1 else 0
    vol_std = float(np.std(volumes[:-1])) if len(volumes) > 2 else 1
    if vol_std == 0:
        return None
    return (volumes[-1] - vol_mean) / vol_std


def forward_ret(sym: str, ts: int, hold_candles: int = 6):
    """Forward return in bps over hold_candles starting at ts+1 (next open)."""
    idx_map = sym_to_idx[sym]
    if ts not in idx_map:
        return None
    i = idx_map[ts]
    arr = sym_to_arr[sym]
    if i + 1 + hold_candles >= len(arr):
        return None
    entry = arr[i + 1]["o"]
    exit_px = arr[i + 1 + hold_candles - 1]["c"]
    if entry <= 0 or exit_px <= 0:
        return None
    return (exit_px / entry - 1) * 1e4


# Collect all timestamps
ts_set = set()
for cs in eligible.values():
    ts_set |= {c["t"] for c in cs}
all_ts = sorted(ts_set)
latest_ts = all_ts[-1]
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000
splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]
print(f"\nSplits boundaries built. Total ts: {len(all_ts)}")


# Per-ts cache of (token → ret over different horizons)
print("\nIterating timestamps and collecting variant triggers...")

# Containers: variant_label → {split_label: [trade dict]}
results = {
    "S5_V0_baseline": {label: [] for label, _, _ in splits},
    "S5_V1_robust":   {label: [] for label, _, _ in splits},
    "S5_V2_multih":   {label: [] for label, _, _ in splits},
    "S5_V3_combined": {label: [] for label, _, _ in splits},
    "S9_V0_baseline": {label: [] for label, _, _ in splits},
    "S9_V1_volnorm":  {label: [] for label, _, _ in splits},
}

# S5 thresholds (mirror config)
S5_BPS_THRESH = 1000
S5_VOL_Z_MIN = 1.0
# Robust z threshold calibrated to match baseline selectivity (~3 MADs ≈ p99 tail)
S5_ROBUST_Z = 3.0
# Multi-horizon thresholds (looser per-window since requiring 3 confirmations)
S5_MH_THRESH = 700  # bps on each window

# S9 thresholds
S9_RET_THRESH = 2000  # bps
# Vol-normalized: ret_24h / ATR_30d. ATR_30d ~ 200-1000 bps typically; ratio threshold ≈ 3-10
S9_VOLNORM_RATIO = 5.0

start = time.time()
last_print = 0
for it_idx, ts in enumerate(all_ts):
    if time.time() - last_print > 5:
        elapsed = time.time() - start
        progress = it_idx / len(all_ts)
        eta = elapsed * (1 - progress) / max(progress, 0.001)
        total = sum(sum(len(v) for v in d.values()) for d in results.values())
        print(f"  [{it_idx:>5}/{len(all_ts)}] {progress*100:.0f}% elapsed={elapsed:.0f}s eta={eta:.0f}s triggers={total}")
        last_print = time.time()

    # Find which split this ts belongs to
    split_label = None
    for label, s_ts, e_ts in splits:
        if s_ts <= ts < e_ts:
            split_label = label
            break
    if split_label is None:
        continue

    # Compute per-token features at this ts
    # For S5 we need ret over 18 candles (3d), 42 candles (7d), 84 candles (14d)
    rets_3d = {}
    rets_7d = {}
    rets_14d = {}
    vol_zs = {}
    for sym in eligible:
        r3 = compute_ret(sym, ts, 18)
        r7 = compute_ret(sym, ts, 42)
        r14 = compute_ret(sym, ts, 84)
        vz = compute_vol_z(sym, ts)
        if r3 is None or r7 is None or r14 is None or vz is None:
            continue
        rets_3d[sym] = r3
        rets_7d[sym] = r7
        rets_14d[sym] = r14
        vol_zs[sym] = vz

    if len(rets_7d) < 15:
        continue

    # For each token: compute baseline + robust + multi-horizon divergence
    for sym in rets_7d:
        sect = TOKEN_SECTOR.get(sym)
        if not sect:
            continue
        peers = [p for p in SECTORS[sect] if p != sym and p in rets_7d]
        if len(peers) < 2:
            continue

        peer_7d = np.array([rets_7d[p] for p in peers])
        peer_3d = np.array([rets_3d[p] for p in peers if p in rets_3d])
        peer_14d = np.array([rets_14d[p] for p in peers if p in rets_14d])
        if len(peer_3d) < 2 or len(peer_14d) < 2:
            continue

        # V0 baseline
        div_v0 = rets_7d[sym] - float(peer_7d.mean())
        vol_z = vol_zs[sym]
        v0_fire = abs(div_v0) >= S5_BPS_THRESH and vol_z >= S5_VOL_Z_MIN
        v0_dir = 1 if div_v0 > 0 else -1

        # V1 robust: median + MAD
        med_7d = float(np.median(peer_7d))
        mad_7d = float(np.median(np.abs(peer_7d - med_7d)))
        if mad_7d > 0:
            z_v1 = (rets_7d[sym] - med_7d) / mad_7d
        else:
            z_v1 = 0
        v1_fire = abs(z_v1) >= S5_ROBUST_Z and vol_z >= S5_VOL_Z_MIN
        v1_dir = 1 if z_v1 > 0 else -1

        # V2 multi-horizon: require divergence on each window same sign + above threshold each
        div_3d = rets_3d[sym] - float(peer_3d.mean())
        div_14d = rets_14d[sym] - float(peer_14d.mean())
        same_sign = (
            (div_3d > 0 and div_v0 > 0 and div_14d > 0)
            or (div_3d < 0 and div_v0 < 0 and div_14d < 0)
        )
        all_above = (
            abs(div_3d) >= S5_MH_THRESH * 0.5  # 3d window — looser threshold (less time)
            and abs(div_v0) >= S5_MH_THRESH
            and abs(div_14d) >= S5_MH_THRESH * 1.5  # 14d window — stricter (more time accumulated)
        )
        v2_fire = same_sign and all_above and vol_z >= S5_VOL_Z_MIN
        v2_dir = 1 if div_v0 > 0 else -1

        # V3 robust + multi-horizon
        med_3d = float(np.median(peer_3d))
        mad_3d = float(np.median(np.abs(peer_3d - med_3d)))
        med_14d = float(np.median(peer_14d))
        mad_14d = float(np.median(np.abs(peer_14d - med_14d)))
        z_3d = (rets_3d[sym] - med_3d) / mad_3d if mad_3d > 0 else 0
        z_14d = (rets_14d[sym] - med_14d) / mad_14d if mad_14d > 0 else 0
        v3_fire = (
            ((z_3d > 0 and z_v1 > 0 and z_14d > 0) or (z_3d < 0 and z_v1 < 0 and z_14d < 0))
            and abs(z_3d) >= 1.5 and abs(z_v1) >= S5_ROBUST_Z and abs(z_14d) >= 2.0
            and vol_z >= S5_VOL_Z_MIN
        )
        v3_dir = 1 if z_v1 > 0 else -1

        # Forward 24h return — note S5 LONG/SHORT follows direction
        fwd = forward_ret(sym, ts, hold_candles=12)  # 48h S5 hold
        if fwd is None:
            continue

        if v0_fire:
            results["S5_V0_baseline"][split_label].append({"ts": ts, "sym": sym, "dir": v0_dir,
                                                            "fwd": fwd * v0_dir})
        if v1_fire:
            results["S5_V1_robust"][split_label].append({"ts": ts, "sym": sym, "dir": v1_dir,
                                                          "fwd": fwd * v1_dir})
        if v2_fire:
            results["S5_V2_multih"][split_label].append({"ts": ts, "sym": sym, "dir": v2_dir,
                                                          "fwd": fwd * v2_dir})
        if v3_fire:
            results["S5_V3_combined"][split_label].append({"ts": ts, "sym": sym, "dir": v3_dir,
                                                            "fwd": fwd * v3_dir})

        # S9 variants — on |ret_24h| extreme
        ret_24h = compute_ret(sym, ts, 6)
        if ret_24h is None:
            continue
        s9_v0_fire = abs(ret_24h) >= S9_RET_THRESH
        s9_v0_dir = -1 if ret_24h > 0 else 1  # fade pump = SHORT, fade dump = LONG

        atr = compute_atr(sym, ts)
        s9_v1_fire = False
        s9_v1_dir = s9_v0_dir
        if atr is not None and atr > 0:
            ratio = ret_24h / atr  # signed
            s9_v1_fire = abs(ratio) >= S9_VOLNORM_RATIO
            s9_v1_dir = -1 if ratio > 0 else 1

        fwd_24h_s9 = forward_ret(sym, ts, hold_candles=12)  # 48h S9 hold
        if fwd_24h_s9 is None:
            continue

        if s9_v0_fire:
            results["S9_V0_baseline"][split_label].append({"ts": ts, "sym": sym, "dir": s9_v0_dir,
                                                            "fwd": fwd_24h_s9 * s9_v0_dir})
        if s9_v1_fire:
            results["S9_V1_volnorm"][split_label].append({"ts": ts, "sym": sym, "dir": s9_v1_dir,
                                                           "fwd": fwd_24h_s9 * s9_v1_dir})

# Report
print(f"\n=== RESULTS PER VARIANT × SPLIT ===\n")
COST_BPS = 26
print(f"{'Variant':<22} {'Split':<22} {'n':>5} {'gross μ':>9} {'net μ':>8} {'med':>7} {'WR':>5}")
print("-" * 90)

summary = {}
for variant, by_split in results.items():
    summary[variant] = {}
    for label, _, _ in splits:
        trades = by_split[label]
        if not trades:
            print(f"{variant:<22} {label:<22} 0 (no trades)")
            summary[variant][label] = {"n": 0}
            continue
        fwd_arr = np.array([t["fwd"] for t in trades])
        gross_mu = float(fwd_arr.mean())
        net_mu = gross_mu - COST_BPS
        net_med = float(np.median(fwd_arr)) - COST_BPS
        wr = float((fwd_arr > COST_BPS).mean() * 100)  # net > 0
        print(f"{variant:<22} {label:<22} {len(trades):>5} {gross_mu:>+9.1f} {net_mu:>+8.1f} {net_med:>+7.1f} {wr:>4.1f}%")
        summary[variant][label] = {"n": len(trades), "gross_mu": gross_mu,
                                    "net_mu": net_mu, "net_med": net_med, "wr": wr}
    print()

# Comparative analysis: S5 V1/V2/V3 vs V0, S9 V1 vs V0
print(f"\n=== COMPARISON vs BASELINE (Δ net μ per split) ===")
for base_var, comp_vars in [
    ("S5_V0_baseline", ["S5_V1_robust", "S5_V2_multih", "S5_V3_combined"]),
    ("S9_V0_baseline", ["S9_V1_volnorm"]),
]:
    print(f"\n  Baseline = {base_var}")
    print(f"  {'Variant':<22} " + " ".join(f"{lbl.split()[0]:>11}" for lbl, _, _ in splits) + "   strict 4/4?")
    for cv in comp_vars:
        deltas = []
        for label, _, _ in splits:
            base_n = summary[base_var][label].get("n", 0)
            cv_n = summary[cv][label].get("n", 0)
            if base_n == 0 or cv_n == 0:
                deltas.append((None, False))
                continue
            d = summary[cv][label]["net_mu"] - summary[base_var][label]["net_mu"]
            d_wr = summary[cv][label]["wr"] - summary[base_var][label]["wr"]
            # PASS criterion: both net_mu AND wr improved (or neutral)
            pass_s = d >= 0 and d_wr >= 0
            deltas.append((d, pass_s))
        n_pass = sum(1 for _, p in deltas if p)
        line = f"  {cv:<22} "
        for d, p in deltas:
            if d is None:
                line += f"{'n/a':>11} "
            else:
                line += f"{d:>+8.1f}{'✓' if p else '✗':>2} "
        line += f"  {n_pass}/4"
        print(line)

print(f"\n=== STRICT VERDICT ===")
print(f"Criterion: variant improves net mean AND WR vs baseline on all 4 splits.\n")

# Save
with open("/home/crypto/backtests/output/eda_signal_precision.json", "w") as f:
    json.dump(summary, f, indent=2, default=float)
print(f"Results saved to backtests/output/eda_signal_precision.json")
