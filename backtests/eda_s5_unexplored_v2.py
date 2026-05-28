"""EDA v2 — consolidate T+8h ∪ T+12h checkpoints to grow n, focus on disp_7d gate.

v1 (eda_s5_unexplored.py) found z=+3.34 for S5 LONG G2 × disp_7d=high but n=15.
v2 unifies T+8h and T+12h: one rule fires at either checkpoint per trade
(dedup by trade_id), so n grows ~2× while keeping the same mechanic.

We also test combined rules (one feature gate combined with disp_7d bucket
in a single boolean) and report per-trade-once results.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "analysis" / "output" / "pairs_data"
SNAPS = REPO / "backtests" / "mid_trade_snapshots_28m.jsonl"

# Reuse btc_z + disp lookups from v1 by import
from backtests.eda_s5_unexplored import (
    load_btc_z_lookup, load_dispersion_lookup,
    bucket_btc_z, bucket_disp_7d,
    gate_strong, gate_triple_mid, gate_large_pain, gate_sd_only,
)


def consolidate(snaps, strat, direction, checkpoints=(8, 12)):
    """Group snapshots by trade_id and return list of {trade_id, final_*,
    cp8: snap?, cp12: snap?}. Each trade is one record so the rule below
    fires at most once per trade."""
    by_trade = defaultdict(dict)
    for s in snaps:
        if s["strat"] != strat or s["dir"] != direction:
            continue
        if s["checkpoint_h"] in checkpoints:
            by_trade[s["trade_id"]][s["checkpoint_h"]] = s
    out = []
    for tid, cps in by_trade.items():
        # pick the "primary" snapshot for final_* fields — they're all the
        # same since they refer to the same trade
        sample = next(iter(cps.values()))
        out.append(dict(
            trade_id=tid,
            symbol=sample["symbol"],
            final_winner=sample["final_winner"],
            final_net_bps=sample["final_net_bps"],
            cps=cps,  # {8: snap, 12: snap}
        ))
    return out


def rule_fires_consolidated(record, gate_fn, disp_bucket_required=None, btc_bucket_required=None):
    """Rule fires at any of the available checkpoints when gate_fn(snap)
    is True AND optional bucket filters match. Returns (fires, cur_ur, cp_h)
    or (False, None, None)."""
    for cp_h in sorted(record["cps"].keys()):
        snap = record["cps"][cp_h]
        if not gate_fn(snap):
            continue
        if disp_bucket_required is not None and snap.get("disp_bucket") != disp_bucket_required:
            continue
        if btc_bucket_required is not None and snap.get("btc_bucket") != btc_bucket_required:
            continue
        return True, snap["current_ur_bps"], cp_h
    return False, None, None


def evaluate_rule(records, gate_fn, disp_bucket=None, btc_bucket=None):
    """Compute per-trade stats for a consolidated rule."""
    fires = []
    for r in records:
        f, cur, cp_h = rule_fires_consolidated(r, gate_fn,
                                                disp_bucket_required=disp_bucket,
                                                btc_bucket_required=btc_bucket)
        if f:
            fires.append((r, cur, cp_h))
    if not fires:
        return None
    n = len(fires)
    wins = sum(1 for (r, _, _) in fires if r["final_winner"])
    cur_mean = float(np.mean([cur for (_, cur, _) in fires]))
    final_mean = float(np.mean([r["final_net_bps"] for (r, _, _) in fires]))
    cp_dist = Counter(cp for (_, _, cp) in fires)
    return dict(
        n=n, WR=wins / n * 100,
        cur_ur=cur_mean, final=final_mean,
        savings=cur_mean - final_mean,
        cp_dist=dict(cp_dist),
        fires=fires,
    )


def null_shuffle_consolidated(records, gate_fn, feature_key, n_shuffle=500,
                              bucket_fn=bucket_disp_7d, bucket_required=None,
                              kind="disp", seed=42):
    """Shuffle the feature_key across the snapshot population (per checkpoint),
    re-bucket, and recompute rule fires. Returns z vs real."""
    rng = np.random.default_rng(seed)
    real = evaluate_rule(records, gate_fn,
                          disp_bucket=bucket_required if kind == "disp" else None,
                          btc_bucket=bucket_required if kind == "btc" else None)
    if real is None:
        return None

    # collect all snapshot values per checkpoint
    snaps_by_cp = defaultdict(list)
    for r in records:
        for cp_h, snap in r["cps"].items():
            snaps_by_cp[cp_h].append((r["trade_id"], snap))

    shuf_savings = []
    for _ in range(n_shuffle):
        # shuffle the feature within each checkpoint pool
        new_buckets = {}  # (trade_id, cp_h) -> bucket
        for cp_h, items in snaps_by_cp.items():
            vals = [snap[feature_key] for _, snap in items]
            permed = rng.permutation(vals).tolist()
            for (tid, snap), v in zip(items, permed):
                new_buckets[(tid, cp_h)] = bucket_fn(v)

        # rebuild records with shuffled buckets
        fires = []
        for r in records:
            for cp_h, snap in sorted(r["cps"].items()):
                if not gate_fn(snap):
                    continue
                b = new_buckets[(r["trade_id"], cp_h)]
                if bucket_required is not None and b != bucket_required:
                    continue
                fires.append((r, snap["current_ur_bps"]))
                break
        if not fires:
            continue
        cur_m = float(np.mean([c for _, c in fires]))
        fin_m = float(np.mean([r["final_net_bps"] for r, _ in fires]))
        shuf_savings.append(cur_m - fin_m)
    if len(shuf_savings) < 10:
        return real | {"z_shuffle": 0.0, "n_shuffle": len(shuf_savings)}
    m, s_ = float(np.mean(shuf_savings)), float(np.std(shuf_savings)) or 1.0
    z = (real["savings"] - m) / s_
    return real | {
        "z_shuffle": round(z, 2),
        "shuffle_mean": round(m, 1),
        "shuffle_std": round(s_, 1),
        "n_shuffle": len(shuf_savings),
    }


def main():
    print("== Loading data ==")
    btc_z_lookup = load_btc_z_lookup()
    disp24, disp7d = load_dispersion_lookup()
    snaps = [json.loads(l) for l in SNAPS.open()]
    print(f"Snapshots: {len(snaps)}")
    for s in snaps:
        ts = s["checkpoint_t"]
        s["btc_z"] = btc_z_lookup.get(ts)
        s["disp_7d"] = disp7d.get(ts)
        s["btc_bucket"] = bucket_btc_z(s["btc_z"])
        s["disp_bucket"] = bucket_disp_7d(s["disp_7d"])

    GATES = {
        "G1_strong":       gate_strong,
        "G2_triple_mid":   gate_triple_mid,
        "G7_large_pain":   gate_large_pain,
        "G8_sd_only":      gate_sd_only,
    }

    print("\n=== CONSOLIDATED T+8h ∪ T+12h, dedup by trade_id ===")
    print(f"{'target':14s}  {'gate':14s}  {'cond':16s}  {'n':>5s}  {'WR%':>6s}  {'cur':>7s}  "
          f"{'final':>7s}  {'sav':>6s}  {'z':>6s}  {'cps':>15s}")

    for strat, d, label in [("S5", 1, "S5_LONG"), ("S5", -1, "S5_SHORT")]:
        records = consolidate(snaps, strat, d, checkpoints=(8, 12))
        print(f"\n-- {label}  records={len(records)} --")
        rows = []

        # unconditional baseline
        for gname, gate in GATES.items():
            r = evaluate_rule(records, gate)
            if not r or r["n"] < 5: continue
            rows.append((gname, "none", r, 0.0))

        # disp_7d conditioning
        for gname, gate in GATES.items():
            for buck in ("high", "mid", "low"):
                r = null_shuffle_consolidated(records, gate, "disp_7d",
                                              bucket_fn=bucket_disp_7d,
                                              bucket_required=buck, kind="disp",
                                              n_shuffle=400)
                if not r or r["n"] < 5: continue
                rows.append((gname, f"disp={buck}", r, r["z_shuffle"]))

        # btc_z conditioning
        for gname, gate in GATES.items():
            for buck in ("bear", "neutral", "bull"):
                r = null_shuffle_consolidated(records, gate, "btc_z",
                                              bucket_fn=bucket_btc_z,
                                              bucket_required=buck, kind="btc",
                                              n_shuffle=400)
                if not r or r["n"] < 5: continue
                rows.append((gname, f"btc={buck}", r, r["z_shuffle"]))

        for gname, cond, r, z in rows:
            print(f"  {label:14s}  {gname:14s}  {cond:16s}  {r['n']:5d}  {r['WR']:6.1f}  "
                  f"{r['cur_ur']:7.0f}  {r['final']:7.0f}  {r['savings']:+6.0f}  {z:+6.2f}  "
                  f"{str(r.get('cp_dist', {})):>15s}")

        # mark survivors
        survivors = [(gname, cond, r, z) for gname, cond, r, z in rows
                     if r["n"] >= 30 and r["WR"] < 25 and r["savings"] >= 50 and abs(z) >= 2.0]
        print(f"\n  SURVIVORS for {label}: {len(survivors)}")
        for gname, cond, r, z in survivors:
            print(f"    {gname:14s}  {cond:16s}  n={r['n']:>3d}  WR={r['WR']:.1f}%  "
                  f"sav=+{r['savings']:.0f}  z={z:+.2f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
