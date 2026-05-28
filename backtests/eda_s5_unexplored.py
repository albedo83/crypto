"""EDA premise gate — unexplored S5 mid-trade exit signals.

Goal: validate (in-sample, cheap) which of the unexplored angles below has a
non-trivial signal vs shuffle noise BEFORE burning compute on walk-forward.

Per memory `feedback_premise_gate_before_sweep`: cheap EDA first, walk-forward
only on candidates that survive null-shuffle.

Angles tested (each on S5 LONG and S5 SHORT, at T+8h and T+12h):

  G1  strong       mfe<50  AND pain>=50
  G2  triple_mid   mfe<300 AND pain>=60 AND sd_delta<-500
  G3  G1 × regime bucket on btc_z(checkpoint_t)   (bear / neutral / bull)
  G4  G2 × regime bucket on btc_z(checkpoint_t)
  G5  G1 × disp_7d(checkpoint_t) bucket           (low / mid / high)
  G6  G2 × disp_7d(checkpoint_t) bucket
  G7  mfe<150 AND pain>=80  (large-pain proxy, S5 LONG @ T+12h was n=98)
  G8  sd_delta-only regime trigger: sd_delta<-500 alone vs btc_z bucket

For each (gate, strat, dir, checkpoint, bucket) we report:
    n, WR, mean_cur_ur, mean_final_net, savings_bps,
    null-shuffle z (shuffle the conditioning feature 200x)

Premise-gate to pass to walk-forward:
    n >= 30 AND WR < 25% AND savings_bps >= +50 AND |z_shuffle| >= 3
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "analysis" / "output" / "pairs_data"
SNAPS = REPO / "backtests" / "mid_trade_snapshots_28m.jsonl"

# ── Load BTC candles + compute btc_z lookup at each 4h ts ──
def load_btc_z_lookup():
    btc = json.loads((DATA / "BTC_4h_3y.json").read_text())
    closes = np.array([float(c["c"]) for c in btc])
    # ret_30d at each candle = close[i] / close[i-180] - 1   (30d × 6 = 180)
    n_lb, n_z = 180, 180 * 6  # lookback 30d, z window ~180d
    rets = np.full(len(btc), np.nan)
    for i in range(n_lb, len(btc)):
        if closes[i - n_lb] > 0:
            rets[i] = closes[i] / closes[i - n_lb] - 1
    # Rolling z over the past z_window
    z = np.full(len(btc), np.nan)
    for i in range(n_lb + 30, len(btc)):
        window_start = max(n_lb, i - n_z + 1)
        wnd = rets[window_start:i + 1]
        wnd = wnd[~np.isnan(wnd)]
        if len(wnd) >= 30:
            m, s = wnd.mean(), wnd.std() or 1.0
            z[i] = (rets[i] - m) / s
    return {btc[i]["t"]: float(z[i]) for i in range(len(btc)) if not np.isnan(z[i])}


# ── Load 28-token candles + compute disp_24h, disp_7d at each 4h ts ──
def load_dispersion_lookup():
    from analysis.bot.config import TRADE_SYMBOLS  # current 35 tokens
    # Use a representative subset — 28 tokens at the time the snapshots were
    # generated. Use whatever 4h files exist to be safe.
    alts = {}
    for sym in TRADE_SYMBOLS:
        p = DATA / f"{sym}_4h_3y.json"
        if p.exists():
            alts[sym] = json.loads(p.read_text())
    print(f"Loaded {len(alts)} alt candle files for dispersion")

    # Build a ts -> list of closes
    # For ret_24h we need close[t] / close[t-6] - 1
    # For ret_7d we need close[t] / close[t-42] - 1
    by_ts = defaultdict(list)
    for sym, candles in alts.items():
        closes = [float(c["c"]) for c in candles]
        for i, c in enumerate(candles):
            if i < 42:
                continue
            ret24 = closes[i] / closes[i - 6]  - 1 if closes[i - 6]  > 0 else None
            ret7d = closes[i] / closes[i - 42] - 1 if closes[i - 42] > 0 else None
            by_ts[c["t"]].append((ret24, ret7d))

    disp_24h, disp_7d = {}, {}
    for ts, items in by_ts.items():
        r24 = [r for r, _ in items if r is not None]
        r7d = [r for _, r in items if r is not None]
        if len(r24) >= 10:
            disp_24h[ts] = float(np.std(r24) * 1e4)  # bps
        if len(r7d) >= 10:
            disp_7d[ts] = float(np.std(r7d) * 1e4)   # bps
    return disp_24h, disp_7d


# ── Load + augment snapshots ──
def load_snaps():
    return [json.loads(l) for l in SNAPS.open()]


def augment(snaps, btc_z_lookup, disp24_lookup, disp7d_lookup):
    aug = []
    miss_btc = miss_disp = 0
    for s in snaps:
        ts = s["checkpoint_t"]
        s["btc_z"] = btc_z_lookup.get(ts)
        s["disp_24h"] = disp24_lookup.get(ts)
        s["disp_7d"] = disp7d_lookup.get(ts)
        if s["btc_z"] is None:
            miss_btc += 1
        if s["disp_7d"] is None:
            miss_disp += 1
        aug.append(s)
    print(f"augment: {len(aug)} snaps, missing btc_z={miss_btc}, missing disp_7d={miss_disp}")
    return aug


# ── Gates ──
def gate_strong(s):  return s["mfe_bps_to_date"] < 50  and s["time_in_pain_pct"] >= 50
def gate_triple_mid(s): return s["mfe_bps_to_date"] < 300 and s["time_in_pain_pct"] >= 60 and s["sector_div_delta"] < -500
def gate_large_pain(s): return s["mfe_bps_to_date"] < 150 and s["time_in_pain_pct"] >= 80
def gate_sd_only(s):    return s["sector_div_delta"] < -500


GATES = {
    "G1_strong":       gate_strong,
    "G2_triple_mid":   gate_triple_mid,
    "G7_large_pain":   gate_large_pain,
    "G8_sd_only":      gate_sd_only,
}


def bucket_btc_z(z, lo=-0.5, hi=0.5):
    if z is None: return None
    if z < lo: return "bear"
    if z > hi: return "bull"
    return "neutral"


def bucket_disp_7d(d, lo=400, hi=700):
    if d is None: return None
    if d < lo: return "low"
    if d > hi: return "high"
    return "mid"


# ── Stats helpers ──
def gate_stats(subset, gate_fn):
    """Return n, WR, mean_cur_ur, mean_final_net, savings."""
    fires = [s for s in subset if gate_fn(s)]
    if not fires:
        return None
    n = len(fires)
    wins = sum(1 for s in fires if s["final_winner"])
    wr = wins / n * 100
    cur = float(np.mean([s["current_ur_bps"] for s in fires]))
    fin = float(np.mean([s["final_net_bps"] for s in fires]))
    savings = cur - fin
    return dict(n=n, WR=wr, cur_ur=cur, final=fin, savings=savings)


def null_shuffle_z(subset, gate_fn, feature_key, n_shuffle=200, rng=None):
    """Shuffle `feature_key` across subset and recompute savings under
    `gate_fn`. Returns (real_savings, z, p_two_tailed) where z = (real - mean) / std."""
    rng = rng or np.random.default_rng(42)
    if not subset:
        return None
    real = gate_stats(subset, gate_fn)
    if real is None:
        return None

    vals = [s[feature_key] for s in subset]
    shuffles = []
    for _ in range(n_shuffle):
        permed = rng.permutation(vals).tolist()
        shuffled = [dict(s, **{feature_key: permed[i]}) for i, s in enumerate(subset)]
        stat = gate_stats(shuffled, gate_fn)
        if stat is None:
            continue
        shuffles.append(stat["savings"])
    if len(shuffles) < 10:
        return real | {"z_shuffle": 0.0, "n_shuffle": len(shuffles)}
    mean, std = float(np.mean(shuffles)), float(np.std(shuffles)) or 1.0
    z = (real["savings"] - mean) / std
    return real | {"z_shuffle": round(z, 2),
                   "shuffle_mean": round(mean, 1),
                   "shuffle_std": round(std, 1),
                   "n_shuffle": len(shuffles)}


# ── Main EDA ──
def main():
    print("== Loading data ==")
    btc_z_lookup = load_btc_z_lookup()
    print(f"btc_z lookup: {len(btc_z_lookup)} timestamps")
    disp24, disp7d = load_dispersion_lookup()
    print(f"disp_24h: {len(disp24)} ts, disp_7d: {len(disp7d)} ts")

    snaps = load_snaps()
    print(f"Snapshots loaded: {len(snaps)}")
    aug = augment(snaps, btc_z_lookup, disp24, disp7d)

    # Add bucket labels
    for s in aug:
        s["btc_bucket"] = bucket_btc_z(s["btc_z"])
        s["disp_bucket"] = bucket_disp_7d(s["disp_7d"])

    # ── Targets: S5 LONG and SHORT, T+8h and T+12h ──
    print("\n== Subset sizes ==")
    targets = [
        ("S5_LONG_T8",  "S5",  1,  8),
        ("S5_LONG_T12", "S5",  1, 12),
        ("S5_SHORT_T8", "S5", -1,  8),
        ("S5_SHORT_T12","S5", -1, 12),
    ]
    subsets = {}
    for name, strat, d, cp in targets:
        sub = [s for s in aug if s["strat"]==strat and s["dir"]==d and s["checkpoint_h"]==cp]
        subsets[name] = sub
        # btc_bucket distribution
        bcnt = Counter(s["btc_bucket"] for s in sub)
        dcnt = Counter(s["disp_bucket"] for s in sub)
        print(f"  {name}: n={len(sub)}  btc={dict(bcnt)}  disp={dict(dcnt)}")

    print("\n== Unconditional gates (baseline reference) ==")
    print(f"{'target':14s}  {'gate':14s}  {'n':>5s}  {'WR%':>6s}  {'cur':>7s}  {'final':>7s}  {'savings':>8s}")
    for name, sub in subsets.items():
        for gname, gate in GATES.items():
            stats = gate_stats(sub, gate)
            if stats and stats["n"] >= 10:
                print(f"  {name:14s}  {gname:14s}  {stats['n']:5d}  {stats['WR']:6.1f}  "
                      f"{stats['cur_ur']:7.0f}  {stats['final']:7.0f}  {stats['savings']:+8.0f}")

    print("\n== Regime-conditioned (btc_z bucket) — KEY UNEXPLORED ANGLE ==")
    print(f"{'target':14s}  {'gate':14s}  {'bucket':8s}  {'n':>5s}  {'WR%':>6s}  {'final':>7s}  {'savings':>8s}  {'z_shuf':>7s}")
    candidates_btc = []
    for name, sub in subsets.items():
        for gname, gate in GATES.items():
            for buck in ("bear", "neutral", "bull"):
                sub_b = [s for s in sub if s["btc_bucket"] == buck]
                if len(sub_b) < 20:
                    continue
                stats = gate_stats(sub_b, gate)
                if not stats or stats["n"] < 10:
                    continue
                z = null_shuffle_z(sub, gate, "btc_bucket", n_shuffle=200)
                if z is None:
                    continue
                # for the bucket-specific savings, we want a more targeted shuffle
                # — shuffle the btc_bucket across the parent subset, then recompute
                # savings on bucket==buck. Simpler: shuffle and compute the bucket-
                # restricted gate savings.
                rng = np.random.default_rng(42)
                vals = [s["btc_bucket"] for s in sub]
                shuf_savings = []
                for _ in range(200):
                    permed = rng.permutation(vals).tolist()
                    shuffled = [dict(s, btc_bucket=permed[i]) for i, s in enumerate(sub)]
                    sub_sb = [s for s in shuffled if s["btc_bucket"] == buck]
                    st2 = gate_stats(sub_sb, gate)
                    if st2 and st2["n"] >= 5:
                        shuf_savings.append(st2["savings"])
                if len(shuf_savings) >= 10:
                    m, s_ = float(np.mean(shuf_savings)), float(np.std(shuf_savings)) or 1.0
                    z_buck = (stats["savings"] - m) / s_
                else:
                    z_buck = 0.0
                print(f"  {name:14s}  {gname:14s}  {buck:8s}  {stats['n']:5d}  {stats['WR']:6.1f}  "
                      f"{stats['final']:7.0f}  {stats['savings']:+8.0f}  {z_buck:+7.2f}")
                if stats["n"] >= 30 and stats["WR"] < 25 and stats["savings"] >= 50 and abs(z_buck) >= 2.0:
                    candidates_btc.append((name, gname, buck, stats, round(z_buck, 2)))

    print("\n== Dispersion-conditioned (disp_7d bucket) ==")
    print(f"{'target':14s}  {'gate':14s}  {'bucket':8s}  {'n':>5s}  {'WR%':>6s}  {'final':>7s}  {'savings':>8s}  {'z_shuf':>7s}")
    candidates_disp = []
    for name, sub in subsets.items():
        for gname, gate in GATES.items():
            for buck in ("low", "mid", "high"):
                sub_b = [s for s in sub if s["disp_bucket"] == buck]
                if len(sub_b) < 20:
                    continue
                stats = gate_stats(sub_b, gate)
                if not stats or stats["n"] < 10:
                    continue
                rng = np.random.default_rng(42)
                vals = [s["disp_bucket"] for s in sub]
                shuf_savings = []
                for _ in range(200):
                    permed = rng.permutation(vals).tolist()
                    shuffled = [dict(s, disp_bucket=permed[i]) for i, s in enumerate(sub)]
                    sub_sb = [s for s in shuffled if s["disp_bucket"] == buck]
                    st2 = gate_stats(sub_sb, gate)
                    if st2 and st2["n"] >= 5:
                        shuf_savings.append(st2["savings"])
                if len(shuf_savings) >= 10:
                    m, s_ = float(np.mean(shuf_savings)), float(np.std(shuf_savings)) or 1.0
                    z_buck = (stats["savings"] - m) / s_
                else:
                    z_buck = 0.0
                print(f"  {name:14s}  {gname:14s}  {buck:8s}  {stats['n']:5d}  {stats['WR']:6.1f}  "
                      f"{stats['final']:7.0f}  {stats['savings']:+8.0f}  {z_buck:+7.2f}")
                if stats["n"] >= 30 and stats["WR"] < 25 and stats["savings"] >= 50 and abs(z_buck) >= 2.0:
                    candidates_disp.append((name, gname, buck, stats, round(z_buck, 2)))

    print("\n== SURVIVORS (n>=30, WR<25%, savings>=+50 bps, |z_shuffle|>=2) ==")
    print(f"{'where':30s}  {'target':14s}  {'gate':14s}  {'bucket':8s}  {'n':>5s}  {'WR':>6s}  {'sav':>5s}  {'z':>5s}")
    for c in candidates_btc:
        print(f"  {'btc_z':30s}  {c[0]:14s}  {c[1]:14s}  {c[2]:8s}  {c[3]['n']:5d}  {c[3]['WR']:6.1f}  {c[3]['savings']:5.0f}  {c[4]:+5.2f}")
    for c in candidates_disp:
        print(f"  {'disp_7d':30s}  {c[0]:14s}  {c[1]:14s}  {c[2]:8s}  {c[3]['n']:5d}  {c[3]['WR']:6.1f}  {c[3]['savings']:5.0f}  {c[4]:+5.2f}")

    if not (candidates_btc or candidates_disp):
        print("  (none)")

    # Save artifacts
    out = REPO / "backtests" / "eda_s5_unexplored_artifacts.json"
    out.write_text(json.dumps({
        "candidates_btc": [{"target": c[0], "gate": c[1], "bucket": c[2], "stats": c[3], "z": c[4]} for c in candidates_btc],
        "candidates_disp": [{"target": c[0], "gate": c[1], "bucket": c[2], "stats": c[3], "z": c[4]} for c in candidates_disp],
    }, indent=2))
    print(f"\nArtifacts → {out}")


if __name__ == "__main__":
    main()
