"""Discovery-bias test — would we have found the same signals on 12m only?

For each shipped/tested rule, recompute its EDA-equivalent in-sample
signature using ONLY the last 12 months of mid-trade snapshots.

Question : si on n'avait eu que les 12 derniers mois de data, est-ce que
l'EDA aurait identifié la signature qu'on a trouvée sur 28m ?

Three possible outcomes per rule :
    A. Same signal discovered (n≥30, WR<25%, savings>+50, |z|≥2)
       → robust discovery
    B. Direction preserved but weaker (right sign, sub-threshold)
       → marginal robust
    C. No signal or wrong direction
       → 28m artefact even if 12m walk-forward passes

Rules tested (signatures derived from snapshots only) :
  S8 dead-in-water       : S8 LONG T+8h, mfe ≤ 50           (shipped v12.6.0)
  S8 in-life trail bear  : S8 LONG, btc_z<-0.5, mfe≥1500    (shipped v12.5.30)
  S5 disp_7d gate (R1)   : S5 LONG T+8h, mfe<50&pain≥50&disp≥700 (failed today)
  S5 dead-t8h strong     : S5 LONG T+8h, mfe<50&pain≥50    (failed 2026-05-15)
  S5 dead-t8h triple_mid : S5 LONG T+8h, mfe<300&pain>60&sd<-500 (failed 2026-05-15)
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SNAPS = REPO / "backtests" / "mid_trade_snapshots_28m.jsonl"

# Reuse lookups from eda_s5_unexplored
from backtests.eda_s5_unexplored import (
    load_btc_z_lookup, load_dispersion_lookup,
    bucket_btc_z, bucket_disp_7d,
)


def load_aug_snaps():
    btc_z = load_btc_z_lookup()
    _, disp7d = load_dispersion_lookup()
    snaps = [json.loads(l) for l in SNAPS.open()]
    for s in snaps:
        ts = s["checkpoint_t"]
        s["btc_z"] = btc_z.get(ts)
        s["disp_7d"] = disp7d.get(ts)
    return snaps


def split_by_window(snaps, months_back):
    """Return (snaps_in_window, end_ts_ms). months_back = window length in months."""
    end_ts = max(s["checkpoint_t"] for s in snaps)
    # ~30.5 days/month × 86400 × 1000 = ms
    cutoff = end_ts - int(months_back * 30.5 * 86400 * 1000)
    return [s for s in snaps if s["checkpoint_t"] >= cutoff], end_ts


# ── Rules (each returns fires + gate label) ──
def rule_s8_dead_t8h(snaps):
    """Shipped v12.6.0 trigger signature : S8 LONG @ T+8h, mfe_bps ≤ 50."""
    return [s for s in snaps
            if s["strat"] == "S8" and s["dir"] == 1
            and s["checkpoint_h"] == 8
            and s["mfe_bps_to_date"] <= 50]


def rule_s8_inlife_bear_t12(snaps):
    """Approximation of v12.5.30 S8 inlife trail bear bucket trigger zone.
    At T+12h, S8 LONG, btc_z<-0.5 AND mfe ≥ 1500 (post-trigger zone)."""
    return [s for s in snaps
            if s["strat"] == "S8" and s["dir"] == 1
            and s["checkpoint_h"] == 12
            and s.get("btc_z") is not None and s["btc_z"] < -0.5
            and s["mfe_bps_to_date"] >= 1500]


def rule_s5_disp_strong_t8(snaps):
    """R1 from today (failed) : S5 LONG @ T+8h, mfe<50 & pain≥50 & disp_7d≥700."""
    return [s for s in snaps
            if s["strat"] == "S5" and s["dir"] == 1
            and s["checkpoint_h"] == 8
            and s["mfe_bps_to_date"] < 50
            and s["time_in_pain_pct"] >= 50
            and s.get("disp_7d") is not None and s["disp_7d"] >= 700]


def rule_s5_dead_strong_t8(snaps):
    """Failed 2026-05-15 : S5 LONG @ T+8h, mfe<50 & pain≥50."""
    return [s for s in snaps
            if s["strat"] == "S5" and s["dir"] == 1
            and s["checkpoint_h"] == 8
            and s["mfe_bps_to_date"] < 50
            and s["time_in_pain_pct"] >= 50]


def rule_s5_dead_triple_t8(snaps):
    """Failed 2026-05-15 : S5 LONG @ T+8h, mfe<300 & pain>60 & sd_delta<-500."""
    return [s for s in snaps
            if s["strat"] == "S5" and s["dir"] == 1
            and s["checkpoint_h"] == 8
            and s["mfe_bps_to_date"] < 300
            and s["time_in_pain_pct"] > 60
            and s.get("sector_div_delta") is not None
            and s["sector_div_delta"] == s["sector_div_delta"]  # not NaN
            and s["sector_div_delta"] < -500]


def stats(fires, pop):
    """Return n, WR, mean_cur, mean_final, savings, null-shuffle z over `pop`."""
    if not fires:
        return None
    n = len(fires)
    wins = sum(1 for s in fires if s["final_winner"])
    wr = wins / n * 100
    cur = float(np.mean([s["current_ur_bps"] for s in fires]))
    fin = float(np.mean([s["final_net_bps"] for s in fires]))
    savings = cur - fin

    # Null-shuffle z : pick n random snapshots from the parent pop and measure
    # mean savings on that random subset.
    if len(pop) < n + 10:
        return dict(n=n, WR=wr, cur=cur, final=fin, savings=savings, z=0.0)
    rng = np.random.default_rng(42)
    shuf_savings = []
    for _ in range(500):
        idxs = rng.choice(len(pop), n, replace=False)
        subset = [pop[i] for i in idxs]
        c = float(np.mean([s["current_ur_bps"] for s in subset]))
        f = float(np.mean([s["final_net_bps"] for s in subset]))
        shuf_savings.append(c - f)
    m, s_ = float(np.mean(shuf_savings)), float(np.std(shuf_savings)) or 1.0
    z = (savings - m) / s_
    return dict(n=n, WR=wr, cur=cur, final=fin, savings=savings, z=round(z, 2),
                shuf_mean=round(m, 1), shuf_std=round(s_, 1))


def evaluate_rule(rule_fn, all_snaps, label):
    print(f"\n── {label} ──")
    # window populations for the parent (snap level) — pool used by the
    # rule's parent strat × dir × checkpoint
    one = (rule_fn(all_snaps)[:1] or [None])[0]
    if one is None:
        # rule never fires anywhere; still report per window for traceability
        strat, d, cp = "?", "?", "?"
    else:
        strat, d, cp = one["strat"], one["dir"], one["checkpoint_h"]

    print(f"  Parent: strat={strat} dir={d} cp={cp}")
    print(f"  {'window':10s}  {'pop':>5s}  {'n':>4s}  {'WR%':>6s}  "
          f"{'cur':>6s}  {'final':>7s}  {'sav':>5s}  {'z':>6s}")

    rows = {}
    for w in (28, 24, 18, 12, 6, 3):
        sub, _ = split_by_window(all_snaps, w)
        pop = [s for s in sub
               if s["strat"] == strat and s["dir"] == d and s["checkpoint_h"] == cp]
        fires = rule_fn(sub)
        st = stats(fires, pop)
        if st is None:
            print(f"  {w}m{'':6s}  {len(pop):>5d}  {'-':>4s}  {'-':>6s}  "
                  f"{'-':>6s}  {'-':>7s}  {'-':>5s}  {'-':>6s}")
            continue
        rows[w] = st
        verdict = ""
        # discovery criterion: n>=30, WR<25, savings>=+50, |z|>=2
        if st["n"] >= 30 and st["WR"] < 25 and st["savings"] >= 50 and abs(st["z"]) >= 2:
            verdict = " ✓ FOUND"
        elif st["n"] >= 15 and st["savings"] >= 50 and abs(st["z"]) >= 1.5:
            verdict = " ~ marginal"
        elif st["n"] >= 5 and st["savings"] >= 0:
            verdict = " · weak"
        else:
            verdict = " ✗ absent"
        print(f"  {w}m{'':6s}  {len(pop):>5d}  {st['n']:>4d}  {st['WR']:6.1f}  "
              f"{st['cur']:6.0f}  {st['final']:7.0f}  {st['savings']:+5.0f}  "
              f"{st['z']:+6.2f}{verdict}")
    return rows


def main():
    print("Loading data + augmenting snapshots ...")
    all_snaps = load_aug_snaps()
    print(f"Total snapshots: {len(all_snaps)}")
    end_ts = max(s["checkpoint_t"] for s in all_snaps)
    import time as _t
    print(f"Snapshot window: ... to {_t.strftime('%Y-%m-%d', _t.gmtime(end_ts/1000))}")

    rules = [
        ("S8 dead-in-water (shipped v12.6.0)",        rule_s8_dead_t8h),
        ("S8 in-life trail bear zone (v12.5.30)",     rule_s8_inlife_bear_t12),
        ("S5 disp_7d strong R1 (failed today)",       rule_s5_disp_strong_t8),
        ("S5 dead-t8h strong (failed 2026-05-15)",    rule_s5_dead_strong_t8),
        ("S5 dead-t8h triple_mid (failed 2026-05)",   rule_s5_dead_triple_t8),
    ]
    summary = []
    for label, fn in rules:
        rows = evaluate_rule(fn, all_snaps, label)
        summary.append((label, rows))

    print("\n\n== SUMMARY ==")
    print(f"{'rule':50s}  {'28m':>10s}  {'12m':>10s}  {'6m':>10s}  {'3m':>10s}")
    for label, rows in summary:
        cells = []
        for w in (28, 12, 6, 3):
            if w in rows:
                cells.append(f"z={rows[w]['z']:+.1f} n={rows[w]['n']}")
            else:
                cells.append("-")
        print(f"  {label:50s}  {cells[0]:>10s}  {cells[1]:>10s}  {cells[2]:>10s}  {cells[3]:>10s}")

    print("\nDiscovery threshold : n≥30, WR<25%, savings≥+50, |z|≥2")
    print("Reads: would the EDA have flagged this rule, given only that window's data?")


if __name__ == "__main__":
    main()
