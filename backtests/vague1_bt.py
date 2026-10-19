"""Vague 1 — tests 1a, 1b, 2 et 6 sur le moteur (docs/vague1_2026_10_grid.md).

Moteur = configuration live : S1, S8, S9, S10 (S5 coupé). Backtest aligned.
Découverte 2024-04-19 → 2025-12-31, validation 2026-01-01 → fin, lue une fois.

    nice -n 19 python3 -m backtests.vague1_bt
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from dateutil.relativedelta import relativedelta
from scipy.stats import chi2_contingency

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import (run_window, load_dxy, load_oi, load_funding,
                                        SECTORS, TOKEN_SECTOR)
from backtests.backtest_sector import compute_sector_features

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = timezone.utc
START = datetime(2024, 4, 19, tzinfo=UTC)
SPLIT = datetime(2026, 1, 1, tzinfo=UTC)
NOTIONAL = 150.0

print("chargement…", flush=True)
data = load_3y_candles()
F = build_features(data)
SEC = compute_sector_features(F, data)
DXY, OI, FU = load_dxy(), load_oi(), load_funding()
END = datetime.fromtimestamp(max(c["t"] for c in data["BTC"]) / 1000, tz=UTC)
FIDX = {coin: {f["t"]: f for f in fl} for coin, fl in F.items()}
FEAT_BY_TS = defaultdict(dict)
for coin, d in FIDX.items():
    for t, f in d.items():
        FEAT_BY_TS[t][coin] = f
ms = lambda d: int(d.timestamp() * 1000)


def base_skip(coin, ts, strat, d):
    return strat == "S5"


def run(s, e, skip=base_skip, size_fn=None, cap=1000.0):
    return run_window(F, data, SEC, DXY, ms(s), ms(e), start_capital=cap, oi_data=OI, funding_data=FU,
                      apply_adaptive_modulator=True, aligned=True, margin_check=True,
                      skip_fn=skip, size_fn=size_fn)


def entry_feats_at(coin, ts):
    """Reproduit backtest_rolling (features d'entrée) à partir des données de marché."""
    f = FIDX.get(coin, {}).get(ts)
    if f is None:
        return None
    r24, dd, rg = abs(f.get("ret_6h", 0)), abs(f.get("drawdown", 0)), f.get("range_pct", 0)
    peers = [abs(FEAT_BY_TS[ts][p].get("ret_42h", 0)) for p in SECTORS.get(TOKEN_SECTOR.get(coin), [])
             if p != coin and p in FEAT_BY_TS[ts]]
    pavg = float(np.mean(peers)) if peers else 0.0
    allf = FEAT_BY_TS[ts].values()
    return {"entry_shock": r24 / dd if dd > 100 else 0.0, "entry_clean": rg / r24 if r24 > 50 else 0.0,
            "entry_lead": abs(f.get("ret_42h", 0)) / pavg if pavg > 100 else 0.0,
            "entry_vol_z": f.get("vol_z", 0), "entry_range_pct": rg,
            "entry_disp_24h": round(float(np.std([x.get("ret_6h", 0) for x in allf])), 0),
            "entry_disp_7d": round(float(np.std([x.get("ret_42h", 0) for x in allf])), 0),
            "entry_ret24h_abs": r24, "entry_drawdown_abs": dd}


def summary(r):
    return dict(pnl=r["pnl_pct"], dd=r["max_dd_pct"], n=r["n_trades"],
                cat=sum(1 for t in r["trades"] if t["reason"] == "catastrophe_stop"))


out = {}
print("run continu de référence…", flush=True)
R = run(START, END)
T = R["trades"]
print(f"  {len(T)} trades, {R['pnl_pct']:+.1f} %, DD {R['max_dd_pct']:.1f} %", flush=True)
for t in T:
    t["bps"] = t["pnl"] / t["size"] * 1e4 if t["size"] else 0.0
    t["cat"] = t["reason"] == "catastrophe_stop"
disc = [t for t in T if t["entry_t"] < ms(SPLIT) and t.get("entry_feats")]
val = [t for t in T if t["entry_t"] >= ms(SPLIT)]
cat_bps = [t["bps"] for t in T if t["cat"]]
print(f"  stops catastrophe : {sum(t['cat'] for t in T)} ({np.mean([t['cat'] for t in T]):.1%}), "
      f"coût moyen {np.mean(cat_bps):+.0f} bp, part du P&L perdu : "
      f"{sum(t['pnl'] for t in T if t['cat']):+.0f} $ vs reste {sum(t['pnl'] for t in T if not t['cat']):+.0f} $")
by = defaultdict(lambda: [0, 0])
for t in T:
    by[(t["strat"], t["dir"])][0] += 1
    by[(t["strat"], t["dir"])][1] += t["cat"]
print("  taux par stratégie/sens :", {f"{s}{'+' if d > 0 else '-'}": f"{c}/{n}" for (s, d), (n, c) in sorted(by.items())})

# ── Test 1a — anatomie ────────────────────────────────────────────────
print("\n=== Test 1a — anatomie des stops catastrophe (découverte) ===", flush=True)
keys = sorted(k for k in (disc[0].get("entry_feats") or {}) if k != "entry_n_stress")
k_tests = len(keys)
best = None
for k in keys:
    x = np.array([t["entry_feats"][k] for t in disc], dtype=float)
    y = np.array([t["cat"] for t in disc])
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    edges = np.quantile(x, [0.2, 0.4, 0.6, 0.8])
    b = np.searchsorted(edges, x, side="right")
    rates = [y[b == i].mean() if (b == i).any() else 0 for i in range(5)]
    w = int(np.argmax(rates))
    inw = b == w
    rest = y[~inw].mean() if (~inw).any() else 0
    ratio = rates[w] / rest if rest > 0 else float("inf")
    tab = [[int(y[inw].sum()), int((~y[inw]).sum())], [int(y[~inw].sum()), int((~y[~inw]).sum())]]
    p = chi2_contingency(tab)[1]
    lo = -math.inf if w == 0 else float(edges[w - 1])
    hi = math.inf if w == 4 else float(edges[w])
    flag = p < 0.01 / k_tests
    print(f"  {k:<20} quintiles {' '.join(f'{r:5.1%}' for r in rates)} | pire Q{w+1} ×{ratio:4.2f} p={p:.4f}"
          f"{'  ← significatif' if flag else ''}")
    if flag and (best is None or ratio > best[1]):
        best = (k, ratio, lo, hi, p, w)
note = "entry_n_stress exclu : non recalculable hors du moteur (déviation de grille, rapportée)"
print("  " + note)
out["1a"] = {"note": note, "k": k_tests}
if best is None:
    print("  → aucune feature significative après Bonferroni : test 1a REJETÉ (pas de règle à valider)")
    out["1a"]["verdict"] = "REJETÉ — aucune feature significative"
else:
    k, ratio, lo, hi, p, w = best
    print(f"  → règle figée : ne pas entrer si {lo:.1f} ≤ {k} < {hi:.1f} (Q{w+1}, ×{ratio:.2f}, p={p:.2g})")

    def skip_rule(coin, ts, strat, d):
        if strat == "S5":
            return True
        ef = entry_feats_at(coin, ts)
        return ef is not None and lo <= ef[k] < hi

    b0, b1 = summary(run(SPLIT, END)), summary(run(SPLIT, END, skip=skip_rule))
    ok_ = b1["pnl"] >= b0["pnl"] and b1["cat"] < b0["cat"] and b1["dd"] >= b0["dd"] - 2
    print(f"  validation 2026 : référence {b0} | règle {b1} → {'VALIDÉ' if ok_ else 'REJETÉ'}")
    out["1a"].update(rule=[k, lo, hi], ref=b0, rule_res=b1, verdict="VALIDÉ" if ok_ else "REJETÉ")

# ── Test 1b — taille inverse à la volatilité ──────────────────────────
print("\n=== Test 1b — taille inverse à la volatilité ===", flush=True)
MEDVOL = {t: float(np.median([f.get("vol_7d", 0) for f in d.values() if f.get("vol_7d")]))
          for t, d in FEAT_BY_TS.items() if d}


def vol_size(cand, f, n):
    v, m = f.get("vol_7d", 0), MEDVOL.get(f.get("t"), 0)
    return 1.0 if not v or not m else min(1.0, max(0.5, m / v))


qs, d = [], END
for _ in range(10):
    s = d - relativedelta(months=3)
    qs.append((s, d))
    d = s
qs.reverse()
rows = []
for s, e in qs:
    a, b = summary(run(s, e)), summary(run(s, e, size_fn=vol_size))
    rows.append((s, a, b))
    print(f"  {s:%Y-%m-%d} réf {a['pnl']:+7.1f} % DD {a['dd']:6.1f} % cat {a['cat']:2} | vol {b['pnl']:+7.1f} % "
          f"DD {b['dd']:6.1f} % cat {b['cat']:2}", flush=True)
ra = sum(r[1]["pnl"] for r in rows) / abs(min(r[1]["dd"] for r in rows))
rb = sum(r[2]["pnl"] for r in rows) / abs(min(r[2]["dd"] for r in rows))
y0, y1 = summary(run(SPLIT, END)), summary(run(SPLIT, END, size_fn=vol_size))
ok_ = rb > ra and y1["pnl"] >= 0.9 * y0["pnl"]
print(f"  somme/|pire DD| : réf {ra:.2f} vs vol {rb:.2f} | 2026 : réf {y0['pnl']:+.1f} % vs vol {y1['pnl']:+.1f} % "
      f"→ {'VALIDÉ' if ok_ else 'REJETÉ'}")
out["1b"] = dict(ratio_ref=ra, ratio_vol=rb, y2026_ref=y0, y2026_vol=y1, verdict="VALIDÉ" if ok_ else "REJETÉ")

# ── Test 2 — la courbe de la stratégie comme signal ───────────────────
print("\n=== Test 2 — filtre sur la courbe de la stratégie ===", flush=True)
day0 = START.date()
nd = (END.date() - day0).days + 1
day_of = lambda ms_: (datetime.fromtimestamp(ms_ / 1000, tz=UTC).date() - day0).days
pnl_day = np.zeros(nd)
for t in T:
    pnl_day[min(nd - 1, day_of(t["exit_t"]))] += t["bps"] / 1e4 * NOTIONAL
eq = np.cumsum(pnl_day)


def stats_series(p):
    e = 1000 + np.cumsum(p)
    return float(p.sum()), float(((e - np.maximum.accumulate(e)) / np.maximum.accumulate(e)).min() * 100)


i26 = (SPLIT.date() - day0).days
ref_tot, ref_dd = stats_series(pnl_day)
ref_26 = float(pnl_day[i26:].sum())
print(f"  référence : {ref_tot:+.0f} $, DD {ref_dd:.1f} %, 2026 {ref_26:+.0f} $")
out["2"] = {"ref": [ref_tot, ref_dd, ref_26]}
for N in (10, 20, 40):
    ma = np.array([eq[max(0, i - N + 1):i + 1].mean() for i in range(nd)])
    on = np.r_[True, (eq > ma)[:-1]]
    p = np.zeros(nd)
    for t in T:
        if on[day_of(t["entry_t"])]:
            p[min(nd - 1, day_of(t["exit_t"]))] += t["bps"] / 1e4 * NOTIONAL
    tot, dd = stats_series(p)
    p26 = float(p[i26:].sum())
    ok_ = dd >= ref_dd * 0.75 and tot >= 0.8 * ref_tot and p26 >= 0.8 * ref_26
    tag = (" → " + ("VALIDÉ" if ok_ else "REJETÉ")) if N == 20 else " (sensibilité)"
    print(f"  N={N:2} : {tot:+.0f} $, DD {dd:.1f} %, 2026 {p26:+.0f} $, actif {on.mean():.0%} des jours{tag}")
    out["2"][N] = [tot, dd, p26, ok_]

# ── Test 6 — jours macro ──────────────────────────────────────────────
print("\n=== Test 6 — jours FOMC / CPI ===", flush=True)
md = json.load(open(os.path.join(HERE, "macro_dates.json")))
ev = [datetime.fromisoformat(x).replace(tzinfo=UTC) for x in md["fomc"] + md["cpi"]]
win = [(ms(e + timedelta(hours=12)), ms(e + timedelta(hours=21))) for e in ev]
exp = [any(t["entry_t"] <= b and t["exit_t"] >= a for a, b in win) for t in T]
for lab, sel in (("exposés", True), ("non exposés", False)):
    xs = [t for t, x in zip(T, exp) if x == sel]
    print(f"  {lab:12} n={len(xs):4}  moy {np.mean([t['bps'] for t in xs]):+6.0f} bp  "
          f"stops catastrophe {np.mean([t['cat'] for t in xs]):.1%}")
blocked = [(ms(e - timedelta(hours=12)), ms(e + timedelta(hours=21))) for e in ev]


def skip_macro(coin, ts, strat, d):
    return strat == "S5" or any(a <= ts <= b for a, b in blocked)


res6 = {}
for lab, (s, e) in (("découverte", (START, SPLIT)), ("validation", (SPLIT, END))):
    a, b = summary(run(s, e)), summary(run(s, e, skip=skip_macro))
    res6[lab] = (a, b)
    print(f"  {lab} : réf {a} | sans entrée pré-événement {b}")
ok_ = all(b["pnl"] >= a["pnl"] and b["cat"] < a["cat"] for a, b in res6.values())
print(f"  → {'VALIDÉ' if ok_ else 'REJETÉ'}")
out["6"] = dict(res=res6, verdict="VALIDÉ" if ok_ else "REJETÉ")

json.dump(out, open(os.path.join(HERE, "output", "vague1_bt.json"), "w"), indent=1, default=str)
print("\n→ backtests/output/vague1_bt.json")
