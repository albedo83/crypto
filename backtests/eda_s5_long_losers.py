"""EDA — S5 LONG losers vs winners, 6-month window.

Question: do the 5 recent S5 LONG losers (WLD/INJ/NEAR/LDO/CRV) share a
common entry-state signature that distinguishes them from S5 LONG winners?

Cohort: all closed S5 LONG trades from live + paper + junior (all 6m).
Tests:  Mann-Whitney U (continuous) + Fisher exact (categorical).
Effect: Cliff's delta + rank-biserial correlation.

Output: ranked table of features by effect size, with raw and Bonferroni-
corrected p-values. Verdict at the end: gate candidate vs variance.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DBS = [
    ("/home/crypto/analysis/output_live/reversal_ticks.db", "live"),
    ("/home/crypto/analysis/output/reversal_ticks.db", "paper"),
    ("/home/crypto/analysis/output_live2/reversal_ticks.db", "junior"),
]

WINDOW_START = "2025-11-28T00:00:00+00:00"  # 6 months back from 2026-05-28


# ── signal_info parser ───────────────────────────────────────────────

SIGNAL_INFO_PATTERNS = {
    "sector":   re.compile(r"^([A-Za-z][A-Za-z-]*)"),
    "div":      re.compile(r"div=([+-]?\d+(?:\.\d+)?)"),
    "vz":       re.compile(r"vz=([+-]?\d+(?:\.\d+)?)"),
    "oi1h":     re.compile(r"OI1h=([+-]?\d+(?:\.\d+)?)%"),
    "cs":       re.compile(r"CS=(\d+)"),
    "stress":   re.compile(r"str=(\d+)/(\d+)"),
    "disp1":    re.compile(r"disp=(\d+)/(\d+)"),
    "shock":    re.compile(r"shk=([+-]?\d+(?:\.\d+)?)"),
    "clean":    re.compile(r"cln=([+-]?\d+(?:\.\d+)?)"),
    "lever":    re.compile(r"le[a-z]*=([+-]?\d+(?:\.\d+)?)"),
}


def parse_signal_info(s: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not s:
        return out
    for name, rx in SIGNAL_INFO_PATTERNS.items():
        m = rx.search(s)
        if m:
            if name == "sector":
                out["sector"] = m.group(1)
            elif name == "stress":
                out["stress_num"] = int(m.group(1))
                out["stress_den"] = int(m.group(2))
            elif name == "disp1":
                out["disp_num"] = int(m.group(1))
                out["disp_den"] = int(m.group(2))
            else:
                try:
                    out[name] = float(m.group(1))
                except ValueError:
                    pass
    return out


# ── OPEN event joiner ────────────────────────────────────────────────

def load_open_events(con: sqlite3.Connection) -> dict[tuple[str, int], dict]:
    """Map (symbol, ts_floor_minute) → OPEN event data dict."""
    out: dict[tuple[str, int], dict] = {}
    for r in con.execute(
        "SELECT ts, symbol, data FROM events WHERE event='OPEN'"
    ):
        try:
            data = json.loads(r["data"])
            ts_min = int(r["ts"]) // 60  # round to minute
            out[(r["symbol"], ts_min)] = data
        except Exception:
            pass
    return out


def find_open_data(opens, symbol, entry_time_iso):
    """OPEN event timestamps are seconds, trade entry_time is ISO with us.
    Match within ±2 min to be safe (scan latency).
    """
    try:
        dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
        ts_min = int(dt.timestamp()) // 60
    except Exception:
        return {}
    for delta in range(-2, 3):
        d = opens.get((symbol, ts_min + delta))
        if d:
            return d
    return {}


# ── Pull cohort ──────────────────────────────────────────────────────

def load_btc_candles() -> list[dict]:
    """Load BTC 4h candles for btc_z reconstruction."""
    p = Path("/home/crypto/backtests/output/pairs_data/BTC_4h_3y.json")
    if not p.exists():
        return []
    return json.loads(p.read_text())


def reconstruct_btc_z(btc_candles: list[dict], entry_iso: str) -> float | None:
    """Rolling 6-month z-score of BTC 30d return at entry time.

    Mirror of features.compute_btc_z used by the live bot.
    Returns None if insufficient history.
    """
    if not btc_candles:
        return None
    try:
        ts_entry = datetime.fromisoformat(entry_iso.replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return None
    # Find the candle at or before entry time
    idx = None
    for i in range(len(btc_candles) - 1, -1, -1):
        if btc_candles[i]["t"] <= ts_entry:
            idx = i
            break
    if idx is None or idx < 180 * 6:  # need 30d (180 bars) + 6m baseline (1080 bars)
        return None
    closes = [float(c["c"]) for c in btc_candles]
    # 30d return at each historical point (last 6m = 1080 4h bars)
    rets = []
    lookback_bars = 30 * 6   # 30 days * 6 4h-candles/day = 180
    baseline_bars = 180 * 6  # 6m = 1080 bars
    if idx - lookback_bars - baseline_bars < 0:
        return None
    for j in range(idx - baseline_bars, idx + 1):
        if j - lookback_bars >= 0:
            r = (closes[j] / closes[j - lookback_bars] - 1) * 1e4
            rets.append(r)
    if len(rets) < 100:
        return None
    cur_ret = rets[-1]
    hist = rets[:-1]
    mean = sum(hist) / len(hist)
    var = sum((r - mean) ** 2 for r in hist) / len(hist)
    if var <= 0:
        return None
    return (cur_ret - mean) / (var ** 0.5)


def pull_cohort() -> list[dict]:
    """Pull S5 LONG trades. Deduplicate by (symbol, entry_hour): the same
    signal fired across paper/live/junior is ONE observation — keep the live
    row preferentially, otherwise pick the row with the largest size_usdt.
    """
    btc_candles = load_btc_candles()
    raw: list[dict] = []
    for db, label in DBS:
        if not Path(db).exists():
            continue
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        opens = load_open_events(con)
        for r in con.execute(
            "SELECT * FROM trades WHERE strategy='S5' AND direction='LONG' "
            "AND entry_time >= ? ORDER BY entry_time",
            (WINDOW_START,),
        ):
            row = dict(r)
            row["_bot"] = label
            row.update(parse_signal_info(row.get("signal_info") or ""))
            open_data = find_open_data(opens, row["symbol"], row["entry_time"])
            for k in ("btc_z", "mult", "basket_mean_corr_btc"):
                if k in open_data and open_data[k] is not None:
                    row[f"open_{k}"] = open_data[k]
            try:
                dt = datetime.fromisoformat(row["entry_time"].replace("Z", "+00:00"))
                row["hour_utc"] = dt.hour
                row["dow"] = dt.weekday()
                bucket_4h = dt.replace(minute=0, second=0, microsecond=0).hour // 4
                row["_entry_key"] = (row["symbol"], dt.strftime("%Y-%m-%d"), bucket_4h)
            except Exception:
                row["_entry_key"] = (row["symbol"], row.get("entry_time", "?"))
            # Reconstruct btc_z at entry (from BTC candles, regardless of OPEN event coverage)
            btc_z = reconstruct_btc_z(btc_candles, row["entry_time"])
            if btc_z is not None:
                row["btc_z_reconstructed"] = btc_z
            # Derived dispersion ratio
            dn = row.get("disp_num")
            dd = row.get("disp_den")
            if dn is not None and dd is not None and dd > 0:
                row["disp_ratio"] = dn / dd
            raw.append(row)
        con.close()

    # Dedupe by (symbol, entry-hour). Prefer live > junior > paper, tiebreak by size.
    bot_pref = {"live": 0, "junior": 1, "paper": 2}
    bucket: dict[tuple, list[dict]] = {}
    for r in raw:
        bucket.setdefault(r["_entry_key"], []).append(r)
    deduped: list[dict] = []
    for key, candidates in bucket.items():
        candidates.sort(key=lambda r: (bot_pref.get(r["_bot"], 99), -r.get("size_usdt", 0)))
        winner = candidates[0]
        winner["_dup_bots"] = sorted(c["_bot"] for c in candidates)
        deduped.append(winner)
    deduped.sort(key=lambda r: r["entry_time"])
    return deduped


# ── Stat tests ───────────────────────────────────────────────────────

def mann_whitney_u(x: list[float], y: list[float]) -> tuple[float, float]:
    """Returns (U, p_two_sided) using normal approx. Treats ties via mid-rank."""
    x = [float(v) for v in x if v is not None and not isinstance(v, (bytes, bytearray))]
    y = [float(v) for v in y if v is not None and not isinstance(v, (bytes, bytearray))]
    n_x, n_y = len(x), len(y)
    if n_x < 3 or n_y < 3:
        return float("nan"), float("nan")
    pooled = [(v, 0) for v in x] + [(v, 1) for v in y]
    pooled.sort(key=lambda t: t[0])
    ranks = [0.0] * len(pooled)
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        avg_rank = (i + j + 2) / 2.0  # 1-based
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    r_x = sum(ranks[k] for k in range(len(pooled)) if pooled[k][1] == 0)
    u_x = r_x - n_x * (n_x + 1) / 2.0
    u_y = n_x * n_y - u_x
    u = min(u_x, u_y)
    mean = n_x * n_y / 2.0
    var = n_x * n_y * (n_x + n_y + 1) / 12.0
    if var <= 0:
        return u, float("nan")
    z = (u - mean) / (var ** 0.5)
    # Normal approx two-sided p
    p = 2 * (1 - _phi(abs(z)))
    return u, p


def _phi(z: float) -> float:
    """Standard normal CDF via Abramowitz & Stegun approximation."""
    import math
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def cliffs_delta(x: list[float], y: list[float]) -> float:
    """Effect size: P(X>Y) − P(X<Y). Range [-1, 1]."""
    x = [float(v) for v in x if v is not None and not isinstance(v, (bytes, bytearray))]
    y = [float(v) for v in y if v is not None and not isinstance(v, (bytes, bytearray))]
    if not x or not y:
        return float("nan")
    gt = lt = 0
    for xi in x:
        for yj in y:
            if xi > yj:
                gt += 1
            elif xi < yj:
                lt += 1
    return (gt - lt) / (len(x) * len(y))


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """2x2 Fisher exact, two-sided. Tables [[a,b],[c,d]] — a/c hits per cohort."""
    from math import comb, exp, lgamma

    def log_hyp(a, b, c, d):
        n = a + b + c + d
        return (lgamma(a + b + 1) + lgamma(c + d + 1) +
                lgamma(a + c + 1) + lgamma(b + d + 1) -
                lgamma(n + 1) - lgamma(a + 1) - lgamma(b + 1) -
                lgamma(c + 1) - lgamma(d + 1))

    n_row1 = a + b
    n_col1 = a + c
    n = a + b + c + d
    obs = log_hyp(a, b, c, d)
    total = 0.0
    a_min = max(0, n_col1 - (n - n_row1))
    a_max = min(n_row1, n_col1)
    for ai in range(a_min, a_max + 1):
        bi = n_row1 - ai
        ci = n_col1 - ai
        di = n - ai - bi - ci
        lp = log_hyp(ai, bi, ci, di)
        if lp <= obs + 1e-9:
            total += exp(lp)
    z = sum(exp(log_hyp(ai, n_row1 - ai, n_col1 - ai,
                        n - ai - (n_row1 - ai) - (n_col1 - ai)))
            for ai in range(a_min, a_max + 1))
    return min(total / z, 1.0)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    rows = pull_cohort()
    print(f"S5 LONG cohort 6m ({WINDOW_START[:10]} → now): n={len(rows)}")
    if not rows:
        return

    winners = [r for r in rows if r["pnl_usdt"] > 0]
    losers = [r for r in rows if r["pnl_usdt"] <= 0]
    print(f"  Winners n={len(winners)}, sum P&L=${sum(r['pnl_usdt'] for r in winners):+.2f}")
    print(f"  Losers  n={len(losers)}, sum P&L=${sum(r['pnl_usdt'] for r in losers):+.2f}")
    print(f"  Net: ${sum(r['pnl_usdt'] for r in rows):+.2f}")
    print()
    print(f"  Coins involved: {sorted(set(r['symbol'] for r in rows))}")
    print()

    # Show top recent losers
    recent_losers = sorted(losers, key=lambda r: r["entry_time"], reverse=True)[:10]
    print("Top 10 most recent losers:")
    print(f"  {'date':12} {'sym':6} {'bot':7} {'pnl':>7} {'mae':>6} {'mfe':>6} {'reason':22}")
    for r in recent_losers:
        print(f"  {r['entry_time'][:10]:12} {r['symbol']:6} {r['_bot']:7} "
              f"{r['pnl_usdt']:+7.2f} {r['mae_bps']:+6.0f} {r['mfe_bps']:+6.0f} {r['reason']:22}")
    print()

    # ── Tests on continuous features ─────────────────────────────────
    continuous_features = [
        "entry_oi_delta", "entry_crowding", "entry_confluence",
        "div", "vz", "oi1h", "cs", "stress_num", "stress_den",
        "disp_num", "disp_den", "disp_ratio", "shock", "clean", "lever",
        "btc_z_reconstructed", "open_btc_z", "open_mult", "open_basket_mean_corr_btc",
        "size_usdt", "entry_price",
        "hour_utc", "dow",
    ]

    def _floats(rows: list, feat: str) -> list[float]:
        out = []
        for r in rows:
            v = r.get(feat)
            if v is None or isinstance(v, (bytes, bytearray)):
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
        return out

    results = []
    for feat in continuous_features:
        w_vals = _floats(winners, feat)
        l_vals = _floats(losers, feat)
        if len(w_vals) < 3 or len(l_vals) < 3:
            continue
        u, p = mann_whitney_u(l_vals, w_vals)
        d = cliffs_delta(l_vals, w_vals)
        w_med = statistics.median(w_vals)
        l_med = statistics.median(l_vals)
        results.append({
            "feature": feat,
            "w_n": len(w_vals), "l_n": len(l_vals),
            "w_med": w_med, "l_med": l_med,
            "delta": d, "p": p,
        })

    results.sort(key=lambda r: abs(r["delta"]), reverse=True)

    n_tests = len(results)
    print(f"{'CONTINUOUS FEATURES — Mann-Whitney U (L vs W)':^88}")
    print("=" * 88)
    print(f"{'feature':28} {'W_med':>9} {'L_med':>9} {'Δ med':>9} {'cliffsd':>9} "
          f"{'p_raw':>9} {'p_bonf':>9}")
    print("-" * 88)
    for r in results:
        p_bonf = min(1.0, r["p"] * n_tests) if r["p"] == r["p"] else float("nan")
        marker = "***" if p_bonf < 0.01 else "**" if p_bonf < 0.05 else "*" if r["p"] < 0.05 else ""
        print(f"{r['feature']:28} "
              f"{r['w_med']:9.2f} {r['l_med']:9.2f} {r['l_med']-r['w_med']:+9.2f} "
              f"{r['delta']:+9.3f} {r['p']:9.4f} {p_bonf:9.4f} {marker}")
    print()
    print("  Cliff's delta: 0=no diff, ±1=perfect separation. |d|>0.33=moderate, >0.47=large.")
    print("  Bonferroni-corrected p = raw_p × n_tests (here n=%d)" % n_tests)
    print()

    # ── Tests on categorical features ────────────────────────────────
    cat_features = ["symbol", "_bot", "sector", "entry_session", "reason"]
    print(f"{'CATEGORICAL FEATURES — share of losers per category':^88}")
    print("=" * 88)
    for feat in cat_features:
        all_vals = sorted(set(r.get(feat) for r in rows if r.get(feat)))
        if not all_vals:
            continue
        print(f"\n  {feat}:")
        print(f"    {'value':20} {'W':>5} {'L':>5} {'L_share':>9} {'p_fisher':>10}")
        for v in all_vals:
            w_n = sum(1 for r in winners if r.get(feat) == v)
            l_n = sum(1 for r in losers if r.get(feat) == v)
            w_other = len(winners) - w_n
            l_other = len(losers) - l_n
            if l_n + w_n < 2:
                continue
            share = l_n / (l_n + w_n) if (l_n + w_n) else 0
            try:
                p = fisher_exact_2x2(l_n, w_n, l_other, w_other)
            except Exception:
                p = float("nan")
            marker = " *" if p < 0.05 else ""
            print(f"    {str(v):20} {w_n:5d} {l_n:5d} {share:9.0%} {p:10.4f}{marker}")
    print()

    # ── Threshold analysis: where does loser rate jump? ─────────────
    print(f"{'THRESHOLD ANALYSIS — loser rate above/below cutoff':^88}")
    print("=" * 88)
    for feat, cutoffs in [
        ("disp_den", [500, 700, 800, 1000, 1200, 1500]),
        ("disp_num", [200, 300, 400, 500, 700]),
        ("disp_ratio", [0.20, 0.30, 0.40, 0.50, 0.60]),
        ("btc_z_reconstructed", [-1.0, -0.5, 0, 0.5, 1.0]),
        ("entry_oi_delta", [-5, -2, 0, 2, 5]),
    ]:
        rel = [r for r in rows if r.get(feat) is not None]
        if len(rel) < 10:
            continue
        print(f"\n  {feat} (n_data={len(rel)}):")
        print(f"    {'cutoff':>10} {'W_below':>8} {'L_below':>8} {'L%_below':>9}"
              f"  |  {'W_above':>8} {'L_above':>8} {'L%_above':>9}  {'p_fish':>8}")
        for c in cutoffs:
            below_w = sum(1 for r in rel if r["pnl_usdt"] > 0 and float(r[feat]) < c)
            below_l = sum(1 for r in rel if r["pnl_usdt"] <= 0 and float(r[feat]) < c)
            above_w = sum(1 for r in rel if r["pnl_usdt"] > 0 and float(r[feat]) >= c)
            above_l = sum(1 for r in rel if r["pnl_usdt"] <= 0 and float(r[feat]) >= c)
            lr_below = below_l / (below_l + below_w) if (below_l + below_w) else 0
            lr_above = above_l / (above_l + above_w) if (above_l + above_w) else 0
            try:
                p = fisher_exact_2x2(above_l, above_w, below_l, below_w)
            except Exception:
                p = float("nan")
            star = " *" if p < 0.05 else ""
            print(f"    {c:10.2f} {below_w:8d} {below_l:8d} {lr_below:9.0%}"
                  f"  |  {above_w:8d} {above_l:8d} {lr_above:9.0%}  {p:8.4f}{star}")
    print()

    # PnL stratification by best feature
    print(f"{'PnL STRATIFICATION by disp_den':^88}")
    print("=" * 88)
    for cutoff in [700, 900, 1100]:
        below = [r["pnl_usdt"] for r in rows if r.get("disp_den") is not None and float(r["disp_den"]) < cutoff]
        above = [r["pnl_usdt"] for r in rows if r.get("disp_den") is not None and float(r["disp_den"]) >= cutoff]
        print(f"  cutoff={cutoff}: below n={len(below):3d} pnl=${sum(below):+8.2f}  "
              f"|  above n={len(above):3d} pnl=${sum(above):+8.2f}")
    print()

    # ── Null-shuffle robustness for disp_den ≥ 700 ─────────────────
    import random
    print(f"{'NULL-SHUFFLE on disp_den ≥ 700 gate':^88}")
    print("=" * 88)
    rel = [r for r in rows if r.get("disp_den") is not None]
    pnls = [r["pnl_usdt"] for r in rel]
    flags = [float(r["disp_den"]) >= 700 for r in rel]
    real_below_pnl = sum(p for p, f in zip(pnls, flags) if not f)
    real_above_pnl = sum(p for p, f in zip(pnls, flags) if f)
    real_below_l = sum(1 for p, f in zip(pnls, flags) if not f and p <= 0)
    real_above_l = sum(1 for p, f in zip(pnls, flags) if f and p <= 0)
    print(f"  Real cohort: below 700 → ${real_below_pnl:+.2f} ({real_below_l} losers);  "
          f"above 700 → ${real_above_pnl:+.2f} ({real_above_l} losers)")
    print()

    random.seed(42)
    n_trials = 5000
    # Test statistic: loser rate diff (above − below)
    real_lr_diff = (real_above_l / sum(flags) if sum(flags) else 0) - \
                   (real_below_l / (len(flags) - sum(flags)) if (len(flags) - sum(flags)) else 0)
    extreme = 0
    for _ in range(n_trials):
        sh_flags = flags[:]
        random.shuffle(sh_flags)
        b_l = sum(1 for p, f in zip(pnls, sh_flags) if not f and p <= 0)
        a_l = sum(1 for p, f in zip(pnls, sh_flags) if f and p <= 0)
        lr_a = a_l / sum(sh_flags) if sum(sh_flags) else 0
        lr_b = b_l / (len(sh_flags) - sum(sh_flags)) if (len(sh_flags) - sum(sh_flags)) else 0
        if (lr_a - lr_b) >= real_lr_diff:
            extreme += 1
    print(f"  Null shuffle p (loser-rate diff above − below ≥ real): {extreme/n_trials:.4f} "
          f"({extreme}/{n_trials})")
    print()

    # Temporal split: first half / second half
    print(f"{'TEMPORAL CROSS-VALIDATION on disp_den ≥ 700':^88}")
    print("=" * 88)
    rel_sorted = sorted(rel, key=lambda r: r["entry_time"])
    mid = len(rel_sorted) // 2
    for half_label, half in [("first half", rel_sorted[:mid]), ("second half", rel_sorted[mid:])]:
        bw = sum(1 for r in half if r["pnl_usdt"] > 0 and float(r["disp_den"]) < 700)
        bl = sum(1 for r in half if r["pnl_usdt"] <= 0 and float(r["disp_den"]) < 700)
        aw = sum(1 for r in half if r["pnl_usdt"] > 0 and float(r["disp_den"]) >= 700)
        al = sum(1 for r in half if r["pnl_usdt"] <= 0 and float(r["disp_den"]) >= 700)
        bp = sum(r["pnl_usdt"] for r in half if float(r["disp_den"]) < 700)
        ap = sum(r["pnl_usdt"] for r in half if float(r["disp_den"]) >= 700)
        date_min = half[0]["entry_time"][:10] if half else "?"
        date_max = half[-1]["entry_time"][:10] if half else "?"
        print(f"  {half_label:14} ({date_min} → {date_max}, n={len(half)}):")
        print(f"    below 700: {bw}W/{bl}L, ${bp:+.2f}  |  above 700: {aw}W/{al}L, ${ap:+.2f}")
    print()

    # ── Sliding window — has the loser rate increased recently? ──────
    print(f"{'TEMPORAL DRIFT — loser rate per month':^88}")
    print("=" * 88)
    bins: dict[str, list[float]] = {}
    for r in rows:
        ym = r["entry_time"][:7]
        bins.setdefault(ym, []).append(r["pnl_usdt"])
    print(f"  {'month':9} {'n':>4} {'W':>4} {'L':>4} {'L%':>6} {'sum_pnl':>10}")
    for ym in sorted(bins):
        pnls = bins[ym]
        n = len(pnls)
        w = sum(1 for p in pnls if p > 0)
        l = n - w
        print(f"  {ym:9} {n:4d} {w:4d} {l:4d} {l/n:6.0%} {sum(pnls):+10.2f}")


if __name__ == "__main__":
    main()
