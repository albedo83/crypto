"""S8 LONG "dead-in-water" — T+8h checkpoint exit, walk-forward 4/4 strict.

Validates the single mechanical rule pre-registered in the user spec:

    At T+8h (first 4h candle past 8 hours), if mfe_bps_to_date <= 50  → exit.

In-sample evidence (`backtests/mid_trade_profiling_eda.md`, S8 LONG @ T+8h):
    n=16/118 cuts, WR_final=6.2%, mean_cur_ur=-443bps, mean_final_net=-635bps,
    savings = +192bps per cut. Mechanically: a real S8 capitulation rebound
    triggers an immediate MFE. No MFE above +0.5% after 8h ⇒ thesis invalidated.

Constraints:
  * S8 LONG only (`strat == "S8"` AND `dir == 1`). S8 SHORT not in scope (S8 is
    LONG-only by design — explicit dir==1 guard nonetheless).
  * `apply_adaptive_modulator=True` in every run (canonical v11.10.0 prod
    config). Adaptive α for S8 = −0.5 (bear-favored); guard-rail preserved.
  * Single checkpoint T+8h, evaluated once per position (one-shot rule).
  * New exit reason: `s8_dead_in_water`.

Cohabitation avec S8_INLIFE_PARAMS (v12.5.30): the existing S8 trail activates
at MFE ≥ 300 (neutral) or ≥ 1500 (bear/bull) bps. The new rule fires at
MFE ≤ 50 bps. They operate at opposite ends of the MFE distribution; no
overlap is possible by construction. (Note: `backtest_rolling` does not apply
the v12.5.30 S8 trail — that rule was validated in `backtest_inlife_exit.py`
and lives only in production. The coexistence check here is mechanical.)

Acceptance criteria (strict, tightened vs S5 walk-forward):
  * ΔPnL > 0 on EACH of 4 windows (28m / 12m / 6m / 3m)
  * avg ΔDD ≤ +0.5pp across the 4 windows  (was +2pp on S5)
  * Both → GREEN. 3/4 → YELLOW. ≤2/4 → RED.

Parity check: with the hook installed but always returning None, the result
must be bit-identical to baseline (no mutation of the engine path).
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import (
    run_window, load_dxy, load_oi, load_funding,
)
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
)


WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
T8_HOURS = 8.0
MFE_MAX_BPS = 50.0
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)


# ── Data loading ───────────────────────────────────────────────────────
def load_all():
    print("Loading data...")
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    fund = load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    out = []
    for label, months in WINDOWS:
        start = int((end_dt - relativedelta(months=months)).timestamp() * 1000)
        out.append((label, start, end_ts_ms))
    return out


# ── Hook factory ───────────────────────────────────────────────────────
def _make_dead_in_water_hook(variant: str):
    """variant ∈ {'active', 'parity'}.

    'active' fires on first snapshot where hold_h ≥ 8 if MFE-to-date ≤ 50 bps.
    'parity' installs the hook but always returns None (no engine mutation).

    Per-position state is closure-local, keyed by `trade_id`. The S8 LONG
    position is marked `_s8_dead_checked` on the first candle past T+8h
    regardless of outcome — never re-evaluated.

    Records `audit` rows for each evaluated position so we can do the
    per-trade analysis (was the cut a real loser, or a straggler?).
    """
    state = {
        "evaluated": set(),
        "fired": 0,
        "evaluated_count": 0,
        "audit": [],  # list of dicts: per S8 LONG evaluated at T+8h
    }

    def hook(snap):
        if variant == "parity":
            return None

        if snap.get("strat") != "S8" or snap.get("dir") != 1:
            return None

        tid = snap.get("trade_id")
        if tid is None:
            return None
        if tid in state["evaluated"]:
            return None

        hold_h = snap.get("hold_h", 0.0)
        if hold_h < T8_HOURS:
            return None

        # First time we see this S8 LONG at hold_h ≥ 8 — evaluate once.
        state["evaluated"].add(tid)
        state["evaluated_count"] += 1

        mfe = snap.get("mfe_bps", 0.0)
        cur = snap.get("cur_bps", 0.0)
        fire = (mfe <= MFE_MAX_BPS)

        state["audit"].append({
            "trade_id": tid,
            "symbol": snap.get("symbol"),
            "hold_h_at_check": hold_h,
            "mfe_at_cut": mfe,
            "cur_ur_at_cut": cur,
            "mae_at_cut": snap.get("mae_bps", 0.0),
            "fired": fire,
            "ts_ms_at_check": snap.get("ts_ms"),
        })

        if fire:
            state["fired"] += 1
            return (True, "s8_dead_in_water")
        return None

    return hook, state


# ── Backtest runner ────────────────────────────────────────────────────
def run_one(ctx, start_ts, end_ts, *, hook=None):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"],
        start_ts, end_ts,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=True,
        inlife_exit_extra=hook,
    )


def _exit_distribution(trades):
    c = Counter(t["reason"] for t in trades)
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def _strat_dir_breakdown(trades, reason="s8_dead_in_water"):
    n_cut = sum(1 for t in trades if t["reason"] == reason)
    n_s8_long = sum(1 for t in trades if t["strat"] == "S8" and t["dir"] == 1)
    return n_cut, n_s8_long


def run_window_set(ctx, hook, label_extra="", collect_trades=False):
    specs = window_specs(ctx["end_ts"])
    out = {}
    for label, s, e in specs:
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        exit_dist = _exit_distribution(r["trades"])
        n_cut, n_s8_long = _strat_dir_breakdown(r["trades"])
        row = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], win_rate=r["win_rate"],
            by_strat=r["by_strat"], exit_dist=exit_dist,
            n_dead_in_water=n_cut, n_s8_long=n_s8_long,
            elapsed=time.time() - t0,
        )
        if collect_trades:
            # Keep S8 LONG trades only (small footprint, used for audit)
            row["s8_long_trades"] = [
                {k: t.get(k) for k in (
                    "trade_id", "coin", "entry_t", "exit_t", "reason",
                    "mfe_bps", "mae_bps", "net", "pnl", "dir", "strat",
                )}
                for t in r["trades"]
                if t["strat"] == "S8" and t["dir"] == 1
            ]
        out[label] = row
        print(f"  {label_extra} {label}: pnl={r['pnl_pct']:+.2f}% "
              f"DD={r['max_dd_pct']:.2f}% trades={r['n_trades']} "
              f"S8L={n_s8_long} cut={n_cut} ({time.time()-t0:.1f}s)")
    return out


# ── Verdict ────────────────────────────────────────────────────────────
def verdict(baseline, variant_res, *, dd_threshold_pp=0.5):
    deltas = {}
    pass_pnl_count = 0
    sum_d_dd = 0.0
    for label, _ in WINDOWS:
        d_pnl = variant_res[label]["pnl_pct"] - baseline[label]["pnl_pct"]
        d_dd = variant_res[label]["max_dd_pct"] - baseline[label]["max_dd_pct"]
        deltas[label] = dict(d_pnl=d_pnl, d_dd=d_dd)
        if d_pnl > 0:
            pass_pnl_count += 1
        sum_d_dd += d_dd
    avg_dd = sum_d_dd / 4
    pnl_strict = (pass_pnl_count == 4)
    dd_strict = (avg_dd <= dd_threshold_pp)
    if pnl_strict and dd_strict:
        v = "GREEN"
    elif pass_pnl_count == 3 and dd_strict:
        v = "YELLOW"
    else:
        v = "RED"
    return dict(verdict=v, pass_pnl_count=pass_pnl_count,
                avg_dd=avg_dd, deltas=deltas,
                dd_threshold_pp=dd_threshold_pp)


# ── Per-trade audit (stragglers) ───────────────────────────────────────
def per_trade_audit(audit_rows, baseline_s8_trades, variant_s8_trades):
    """For each fire of `s8_dead_in_water`, look up the same trade_id in the
    baseline run (where it ran to completion) and capture final_net_bps.

    A "straggler" = a cut trade whose baseline-final outcome would have been
    a winner if not cut.
    """
    bl_by_tid = {t["trade_id"]: t for t in baseline_s8_trades if t.get("trade_id") is not None}
    va_by_tid = {t["trade_id"]: t for t in variant_s8_trades if t.get("trade_id") is not None}
    rows = []
    n_stragglers = 0
    for a in audit_rows:
        if not a["fired"]:
            continue
        tid = a["trade_id"]
        bl = bl_by_tid.get(tid, {})
        va = va_by_tid.get(tid, {})
        baseline_final_net = bl.get("net")
        variant_final_net = va.get("net")
        baseline_pnl = bl.get("pnl")
        variant_pnl = va.get("pnl")
        is_straggler = (baseline_final_net is not None
                        and baseline_final_net > 0)
        if is_straggler:
            n_stragglers += 1
        rows.append({
            "trade_id": tid,
            "symbol": a["symbol"],
            "entry_t": bl.get("entry_t") or va.get("entry_t"),
            "mfe_at_cut": a["mfe_at_cut"],
            "cur_ur_at_cut": a["cur_ur_at_cut"],
            "mae_at_cut": a["mae_at_cut"],
            "baseline_final_net": baseline_final_net,
            "variant_final_net": variant_final_net,
            "baseline_pnl_usd": baseline_pnl,
            "variant_pnl_usd": variant_pnl,
            "savings_bps": ((baseline_final_net - variant_final_net)
                            if (baseline_final_net is not None
                                and variant_final_net is not None) else None),
            "is_straggler": is_straggler,
            "baseline_reason": bl.get("reason"),
        })
    return rows, n_stragglers


# ── Main pipeline ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/home/crypto/backtests/s8_dead_in_water_artifacts.json")
    args = ap.parse_args()

    ctx = load_all()

    print("\n[1/4] Parity check (hook installed, always returns None)")
    parity_hook, _ = _make_dead_in_water_hook("parity")
    print("  baseline 4 windows...")
    baseline = run_window_set(ctx, hook=None, label_extra="baseline",
                               collect_trades=True)
    print("  parity (hook = None-returning) 4 windows...")
    parity = run_window_set(ctx, hook=parity_hook, label_extra="parity  ")

    parity_ok = True
    for label, _ in WINDOWS:
        b, p = baseline[label], parity[label]
        if (b["n_trades"] != p["n_trades"]
                or abs(b["pnl_pct"] - p["pnl_pct"]) > 1e-6
                or abs(b["max_dd_pct"] - p["max_dd_pct"]) > 1e-6):
            print(f"  ✗ PARITY FAIL on {label}: "
                  f"baseline={b['n_trades']}/{b['pnl_pct']:.4f}/{b['max_dd_pct']:.4f} "
                  f"parity={p['n_trades']}/{p['pnl_pct']:.4f}/{p['max_dd_pct']:.4f}")
            parity_ok = False
        else:
            print(f"  ✓ parity {label} matches baseline "
                  f"({b['n_trades']} trades, {b['pnl_pct']:+.2f}%, {b['max_dd_pct']:.2f}% DD)")

    if not parity_ok:
        print("\n!!! PARITY FAILED — aborting before variant runs.")
        return

    print("\n[2/4] Running variant (s8_dead_in_water) 4 windows")
    # We need one fresh hook per window because state is closure-scoped.
    # Easier: per-window deep iteration with separate hooks/audits.
    specs = window_specs(ctx["end_ts"])
    variant = {}
    per_window_state = {}
    for label, s, e in specs:
        hook, state = _make_dead_in_water_hook("active")
        t0 = time.time()
        r = run_one(ctx, s, e, hook=hook)
        exit_dist = _exit_distribution(r["trades"])
        n_cut, n_s8_long = _strat_dir_breakdown(r["trades"])
        variant[label] = dict(
            pnl_pct=r["pnl_pct"], max_dd_pct=r["max_dd_pct"],
            n_trades=r["n_trades"], win_rate=r["win_rate"],
            by_strat=r["by_strat"], exit_dist=exit_dist,
            n_dead_in_water=n_cut, n_s8_long=n_s8_long,
            elapsed=time.time() - t0,
            s8_long_trades=[
                {k: t.get(k) for k in (
                    "trade_id", "coin", "entry_t", "exit_t", "reason",
                    "mfe_bps", "mae_bps", "net", "pnl", "dir", "strat",
                )}
                for t in r["trades"]
                if t["strat"] == "S8" and t["dir"] == 1
            ],
        )
        per_window_state[label] = dict(
            audit=state["audit"],
            evaluated=state["evaluated_count"],
            fired=state["fired"],
        )
        print(f"  variant {label}: pnl={r['pnl_pct']:+.2f}% "
              f"DD={r['max_dd_pct']:.2f}% trades={r['n_trades']} "
              f"S8L={n_s8_long} eval={state['evaluated_count']} "
              f"cut={n_cut} ({time.time()-t0:.1f}s)")

    print("\n[3/4] Per-trade audit & stragglers")
    audits_by_window = {}
    for label, _ in WINDOWS:
        bl_trades = baseline[label].get("s8_long_trades", [])
        va_trades = variant[label].get("s8_long_trades", [])
        rows, n_strag = per_trade_audit(per_window_state[label]["audit"],
                                          bl_trades, va_trades)
        audits_by_window[label] = dict(rows=rows, n_stragglers=n_strag)
        n_cut = variant[label]["n_dead_in_water"]
        print(f"  {label}: cut={n_cut} stragglers={n_strag}")

    print("\n[3/4] Verdict")
    v = verdict(baseline, variant)

    print(f"\n  verdict={v['verdict']} "
          f"({v['pass_pnl_count']}/4 PnL pos, ΔDD avg={v['avg_dd']:+.2f}pp; "
          f"threshold ≤{v['dd_threshold_pp']:+.2f}pp)")
    for label, _ in WINDOWS:
        d = v["deltas"][label]
        print(f"    {label}: ΔPnL={d['d_pnl']:+9.2f}pp  ΔDD={d['d_dd']:+6.2f}pp")

    artifacts = dict(
        ts=datetime.utcnow().isoformat(),
        spec=dict(
            target="S8 LONG only",
            checkpoint_hours=T8_HOURS,
            mfe_max_bps=MFE_MAX_BPS,
            reason="s8_dead_in_water",
            dd_threshold_pp=0.5,
        ),
        baseline=baseline,
        parity=parity, parity_ok=parity_ok,
        variant=variant,
        verdict_obj=v,
        per_window_state=per_window_state,
        audits_by_window=audits_by_window,
    )
    with open(args.out, "w") as f:
        json.dump(artifacts, f, indent=2, default=str)
    print(f"\n[4/4] Artifacts: {args.out}")


if __name__ == "__main__":
    main()
