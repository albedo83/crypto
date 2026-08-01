"""PROJET A — TREND-v0 : suivi de tendance, spécification GELÉE.

Ce harnais implémente **exactement** la spécification de `docs/projet_a_trend_v0.md`,
écrite et committée AVANT toute exécution (commits 514c249 puis c0605f5).

Il n'expose **aucun paramètre réglable**. Les constantes ci-dessous sont la
spec, pas des valeurs par défaut : les modifier n'est pas un usage prévu, c'est
une violation de la § 4 « Interdictions explicites ».

Usage :
    python3 -m backtests.backtest_trend_v0            # RUN DE VERDICT
    python3 -m backtests.backtest_trend_v0 --sandbox  # mise au point (§ 2.5)
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br  # noqa: E402
from backtests.backtest_rolling import (  # noqa: E402
    load_funding, compute_funding_cost, TAKER_FEE_BPS, BACKTEST_SLIPPAGE_BPS)
from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series, require_match  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── LA SPEC (docs/projet_a_trend_v0.md § 1) ─────────────────────────────
DONCHIAN_DAYS = 20          # § 1.3 — canal, barre courante exclue
MOMENTUM_DAYS = 30          # § 1.3 — confirmation par le SIGNE
ATR_PERIOD = 14             # § 1.4 — Wilder
CHANDELIER_MULT = 3.0       # § 1.4
RISK_PER_POS = 0.01         # § 1.5 — 1,0 % de l'equity
MAX_POSITIONS = P.max_positions          # § 1.5 — parité Alfred (6)
MAX_NOTIONAL_FRAC = P.max_notional_frac  # § 1.5 — parité Alfred (0.3)
MAX_GROSS_LEVERAGE = P.leverage          # § 1.5 — parité Alfred (2.0)
MIN_BARS = 50               # § 1.1 — éligibilité
COST_BPS = TAKER_FEE_BPS + BACKTEST_SLIPPAGE_BPS   # § 1.6 — 13 bps RT
FUNDING_UNCOVERED_MAX = 0.05                       # § 1.6 — gate de couverture
START_CAP = 1000.0

# ── LES FENÊTRES (§ 2) ──────────────────────────────────────────────────
WIN_LONG = ("2024-07-01", "2026-08-01")       # § 2.3 — C1 et C4
SANDBOX = ("2024-04-01", "2024-06-30")        # § 2.5 — mise au point
TROUGHS = [                                   # § 2.1 — C2
    ("A", "2024-08-03", "2024-11-06", "drawdown maximal, −51,4 %"),
    ("B", "2024-09-22", "2024-10-21", "pire fenêtre 30 j, −29,5 %"),
    ("C", "2024-08-03", "2024-09-01", "2ᵉ pire 30 j, −26,2 %"),
    ("D", "2025-07-21", "2025-08-19", "3ᵉ pire 30 j, −25,2 %"),
]
WF_OFFSETS = [0, 6, 12, 18]                   # § 2.2 — C3
CORR_THRESHOLD = 0.30                         # C1
ALLOCATIONS = [(0.70, 0.30), (0.50, 0.50)]    # C4

DAY_MS = 86_400_000


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


# ── barres journalières (§ 1.2) ─────────────────────────────────────────

def daily_bars(candles: list[dict]) -> list[dict]:
    """6 bougies 4 h → 1 barre, frontière 00:00 UTC."""
    by_day: dict[int, list[dict]] = defaultdict(list)
    for c in candles:
        by_day[c["t"] - c["t"] % DAY_MS].append(c)
    out = []
    for d in sorted(by_day):
        cs = sorted(by_day[d], key=lambda x: x["t"])
        out.append({"t": d, "close_t": d + DAY_MS,
                    "o": cs[0]["o"], "c": cs[-1]["c"],
                    "h": max(x["h"] for x in cs),
                    "l": min(x["l"] for x in cs)})
    return out


def atr_series(bars: list[dict], period: int) -> list[float | None]:
    """ATR de Wilder — None tant que la fenêtre d'amorçage n'est pas pleine."""
    out: list[float | None] = [None] * len(bars)
    trs = []
    atr = None
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
            continue
        pc = bars[i - 1]["c"]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
        if i == period:
            atr = sum(trs[1:period + 1]) / period
        elif i > period:
            atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


# ── le moteur ───────────────────────────────────────────────────────────

def simulate(bars_by_sym: dict, atr_by_sym: dict, funding: dict,
             start: str, end: str, cap0: float = START_CAP) -> dict:
    """Une exécution de TREND-v0 sur [start, end]. Rien de réglable."""
    t0, t1 = _ms(start), _ms(end)
    symbols = sorted(bars_by_sym)
    idx = {s: {b["t"]: i for i, b in enumerate(bars_by_sym[s])} for s in symbols}

    days = sorted({b["t"] for s in symbols for b in bars_by_sym[s]
                   if t0 <= b["t"] <= t1})
    cap = cap0
    pos: dict[str, dict] = {}
    trades: list[dict] = []
    daily: list[dict] = []
    pos_hours = cov_hours = 0.0
    uncovered_syms: set[str] = set()

    for d in days:
        realized = 0.0

        # 1 ── sorties : chandelier évalué à la clôture (§ 1.4)
        for sym in sorted(pos):
            i = idx[sym].get(d)
            if i is None:
                continue
            b, atr = bars_by_sym[sym][i], atr_by_sym[sym][i]
            if atr is None:
                continue
            p = pos[sym]
            p["hi"] = max(p["hi"], b["h"])
            p["lo"] = min(p["lo"], b["l"])
            stop = (p["hi"] - CHANDELIER_MULT * atr if p["dir"] > 0
                    else p["lo"] + CHANDELIER_MULT * atr)
            p["stop"] = (max(p["stop"], stop) if p["dir"] > 0
                         else min(p["stop"], stop))     # cliquet
            hit = (b["c"] <= p["stop"]) if p["dir"] > 0 else (b["c"] >= p["stop"])
            if not hit:
                continue

            exit_px = b["c"]                              # booking au MARK
            gross = p["dir"] * (exit_px / p["entry"] - 1) * p["notional"]
            cost = p["notional"] * COST_BPS / 1e4
            fund = compute_funding_cost(funding, sym, p["dir"],
                                        p["entry_t"], b["close_t"], p["notional"])
            pnl = gross - cost - fund
            realized += pnl
            hrs = (b["close_t"] - p["entry_t"]) / 3_600_000
            pos_hours += hrs
            cov_hours += _covered_hours(funding, sym, p["entry_t"], b["close_t"])
            if sym not in funding:
                uncovered_syms.add(sym)
            trades.append({
                "coin": sym, "dir": p["dir"], "entry_t": p["entry_t"],
                "exit_t": b["close_t"], "entry": p["entry"], "exit": exit_px,
                "notional": round(p["notional"], 2), "hold_h": round(hrs, 1),
                "gross": round(gross, 4), "cost": round(cost, 4),
                "funding": round(fund, 4), "pnl": round(pnl, 4),
            })
            del pos[sym]

        cap += realized

        # 2 ── entrées : cassure Donchian + signe du momentum (§ 1.3)
        if len(pos) < MAX_POSITIONS:
            gross_open = sum(p["notional"] for p in pos.values())
            for sym in symbols:                    # ordre alphabétique (§ 1.5)
                if len(pos) >= MAX_POSITIONS:
                    break
                if sym in pos:
                    continue
                i = idx[sym].get(d)
                if i is None or i < MIN_BARS:
                    continue
                bars = bars_by_sym[sym]
                atr = atr_by_sym[sym][i]
                if not atr or atr <= 0:
                    continue
                b = bars[i]
                prev = bars[i - DONCHIAN_DAYS:i]
                if len(prev) < DONCHIAN_DAYS:
                    continue
                mom = b["c"] / bars[i - MOMENTUM_DAYS]["c"] - 1

                if b["c"] > max(x["h"] for x in prev) and mom > 0:
                    direction = 1
                elif b["c"] < min(x["l"] for x in prev) and mom < 0:
                    direction = -1
                else:
                    continue

                # sizing : vol-targeting à budget de risque égal (§ 1.5)
                stop_frac = CHANDELIER_MULT * atr / b["c"]
                if stop_frac <= 0:
                    continue
                notional = RISK_PER_POS * cap / stop_frac
                notional = min(notional, MAX_NOTIONAL_FRAC * cap)
                notional = min(notional, MAX_GROSS_LEVERAGE * cap - gross_open)
                if notional <= 0:
                    continue
                gross_open += notional
                pos[sym] = {"dir": direction, "entry": b["c"],
                            "entry_t": b["close_t"], "notional": notional,
                            "hi": b["h"], "lo": b["l"],
                            "stop": (b["h"] - CHANDELIER_MULT * atr if direction > 0
                                     else b["l"] + CHANDELIER_MULT * atr)}

        daily.append({"date": _day(d), "pnl_usd": round(realized, 4),
                      "equity": round(cap, 4), "n_open": len(pos),
                      "ret_pct": round(realized / (cap - realized) * 100, 6)
                      if cap - realized > 0 else 0.0})

    # Positions encore ouvertes à la fin : NON bookées, comme le fait
    # `run_window` pour Alfred. Convention conservée pour la comparabilité —
    # mais comptée et publiée, parce qu'un hold de plusieurs semaines la rend
    # bien plus lourde de conséquences que sur des positions de 48 h.
    open_notional = sum(p["notional"] for p in pos.values())
    return {"trades": trades, "daily": daily, "end_capital": cap,
            "n_trades": len(trades),
            "n_open_at_end": len(pos),
            "open_notional_at_end": round(open_notional, 2),
            "funding_uncovered_frac": (1 - cov_hours / pos_hours) if pos_hours else 0.0,
            "uncovered_symbols": sorted(uncovered_syms),
            "pos_hours": round(pos_hours, 1)}


def _covered_hours(funding: dict, sym: str, t_in: int, t_out: int) -> float:
    """Heures de détention réellement couvertes par la donnée de funding."""
    if sym not in funding:
        return 0.0
    ts = funding[sym][0]
    lo, hi = max(t_in, int(ts[0])), min(t_out, int(ts[-1]))
    return max(0.0, (hi - lo) / 3_600_000)


# ── statistiques ────────────────────────────────────────────────────────

def pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        raise MeasureError("corrélation : une des deux séries est constante")
    return num / (da * db)


def spearman(a: list[float], b: list[float]) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(a), rank(b))


def curve_stats(rets: list[float], months: float) -> dict:
    """CAGR, DD max et Calmar d'une série de rendements quotidiens (%)."""
    cap = peak = 1.0
    dd = 0.0
    for r in rets:
        cap *= (1 + r / 100)
        peak = max(peak, cap)
        dd = min(dd, (cap - peak) / peak * 100)
    cagr = (cap ** (12 / months) - 1) * 100 if cap > 0 else -100.0
    return {"end_mult": round(cap, 4), "cagr_pct": round(cagr, 2),
            "dd_pct": round(dd, 2),
            "calmar": round(cagr / abs(dd), 3) if dd < 0 else float("inf")}


def alfred_daily(start: str, end: str) -> list[dict]:
    import csv
    path = os.path.join(ROOT, "data", "alfred_daily_pnl.csv")
    with open(path) as f:
        rows = [r for r in csv.DictReader(f) if start <= r["date"] <= end]
    if not rows:
        raise MeasureError(f"série Alfred vide sur {start}→{end}")
    return [{"date": r["date"], "ret_pct": float(r["ret_pct"])} for r in rows]


# ── programme ───────────────────────────────────────────────────────────

def main() -> int:
    from dateutil.relativedelta import relativedelta
    sandbox = "--sandbox" in sys.argv

    print("Chargement des données…", flush=True)
    data = load_3y_candles()
    funding = load_funding()

    # § 1.1 — gate de parité d'univers. Mismatch = le run refuse d'émettre.
    universe = sorted(set(P.trade_symbols) & set(data))
    require_match("univers TREND vs Params.trade_symbols",
                  universe, sorted(P.trade_symbols),
                  what_a="simulé", what_b="Params.trade_symbols")

    bars = {s: daily_bars(data[s]) for s in universe}
    atrs = {s: atr_series(bars[s], ATR_PERIOD) for s in universe}
    print(banner(P, data, extra={
        "mode": "SANDBOX (mise au point)" if sandbox else "RUN DE VERDICT",
        "univers": len(universe), "risk": RISK_PER_POS,
        "donchian": DONCHIAN_DAYS, "atr_mult": CHANDELIER_MULT}), flush=True)

    if sandbox:
        r = simulate(bars, atrs, funding, *SANDBOX)
        print(f"\nSANDBOX {SANDBOX[0]}→{SANDBOX[1]} : {r['n_trades']} trades, "
              f"fin ${r['end_capital']:,.0f}, "
              f"funding non couvert {r['funding_uncovered_frac']*100:.2f}%")
        for t in r["trades"][:10]:
            print(f"  {_day(t['entry_t'])}→{_day(t['exit_t'])} {t['coin']:6s} "
                  f"{'L' if t['dir'] > 0 else 'S'} ${t['notional']:>7.0f} "
                  f"{t['hold_h']/24:>5.1f}j  brut {t['gross']:>+8.2f} "
                  f"frais {t['cost']:>5.2f} fund {t['funding']:>+7.2f} "
                  f"→ {t['pnl']:>+8.2f}")
        return 0

    # ═══ RUN DE VERDICT ═══════════════════════════════════════════════
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000, timezone.utc)

    print(f"\nFenêtre longue {WIN_LONG[0]} → {WIN_LONG[1]} (C1, C4)…", flush=True)
    long_run = simulate(bars, atrs, funding, *WIN_LONG)
    print(f"  {long_run['n_trades']} trades, fin ${long_run['end_capital']:,.0f}",
          flush=True)

    # gate de couverture funding (§ 1.6)
    unc = long_run["funding_uncovered_frac"]
    print(f"  couverture funding : {(1-unc)*100:.2f}% des heures-position"
          + (f"  (symboles absents : {long_run['uncovered_symbols']})"
             if long_run["uncovered_symbols"] else ""), flush=True)
    if unc > FUNDING_UNCOVERED_MAX:
        raise MeasureError(
            f"couverture funding insuffisante : {unc*100:.1f} % d'heures-position "
            f"non couvertes (> {FUNDING_UNCOVERED_MAX*100:.0f} %) — RUN NUL, "
            f"pas de verdict (docs/projet_a_trend_v0.md § 1.6)")

    res: dict = {"windows": {}, "clauses": {}}

    # ── C1 : décorrélation ────────────────────────────────────────────
    alf = alfred_daily(*WIN_LONG)
    tr_by_day = {d["date"]: d["ret_pct"] for d in long_run["daily"]}
    a_r = [d["ret_pct"] for d in alf]
    t_r = [tr_by_day.get(d["date"], 0.0) for d in alf]
    require_series("rendements quotidiens Alfred", a_r, min_n=300)
    require_series("rendements quotidiens TREND", t_r, min_n=300)
    rho = pearson(a_r, t_r)
    rho_s = spearman(a_r, t_r)
    both = [(x, y) for x, y in zip(a_r, t_r) if x != 0 and y != 0]
    rho_both = pearson([x for x, _ in both], [y for _, y in both]) if len(both) > 30 else None
    c1 = rho < CORR_THRESHOLD
    res["clauses"]["C1"] = {
        "pass": c1, "pearson": round(rho, 4), "spearman": round(rho_s, 4),
        "pearson_co_actifs": round(rho_both, 4) if rho_both is not None else None,
        "n_jours": len(a_r), "n_jours_co_actifs": len(both),
        "seuil": CORR_THRESHOLD}
    print(f"\nC1  ρ Pearson = {rho:+.4f}  (seuil < {CORR_THRESHOLD}) → "
          f"{'PASSE' if c1 else 'ÉCHOUE'}", flush=True)

    # ── C2 : fenêtres de creux ────────────────────────────────────────
    c2_rows = []
    for name, s, e, why in TROUGHS:
        sub = [d for d in long_run["daily"] if s <= d["date"] <= e]
        pnl = sum(d["pnl_usd"] for d in sub)
        ntr = sum(1 for t in long_run["trades"] if s <= _day(t["exit_t"]) <= e)
        c2_rows.append({"fenetre": name, "start": s, "end": e, "why": why,
                        "pnl": round(pnl, 2), "n_trades_clos": ntr,
                        "pass": pnl >= 0})
    c2 = all(r["pass"] for r in c2_rows)
    res["clauses"]["C2"] = {"pass": c2, "par_fenetre": c2_rows,
                            "non_informative": all(r["n_trades_clos"] == 0
                                                   for r in c2_rows)}
    print(f"C2  " + " · ".join(
        f"{r['fenetre']} ${r['pnl']:+,.0f}({r['n_trades_clos']}tr)"
        for r in c2_rows) + f" → {'PASSE' if c2 else 'ÉCHOUE'}", flush=True)

    # ── C3 : walk-forward, net du portage ─────────────────────────────
    c3_rows = []
    for off in WF_OFFSETS:
        e = end_dt - relativedelta(months=off)
        s = e - relativedelta(months=6)
        r = simulate(bars, atrs, funding, f"{s:%Y-%m-%d}", f"{e:%Y-%m-%d}")
        pnl = r["end_capital"] - START_CAP
        c3_rows.append({
            "offset": off, "start": f"{s:%Y-%m-%d}", "end": f"{e:%Y-%m-%d}",
            "n_trades": r["n_trades"], "pnl_net": round(pnl, 2),
            "gross": round(sum(t["gross"] for t in r["trades"]), 2),
            "cost": round(sum(t["cost"] for t in r["trades"]), 2),
            "funding": round(sum(t["funding"] for t in r["trades"]), 2),
            "n_open_at_end": r["n_open_at_end"],
            "pass": pnl >= 0})
        c = c3_rows[-1]
        print(f"C3  OOS-{off:<2d} {c['start']}→{c['end']}  n={c['n_trades']:>3d}  "
              f"brut {c['gross']:>+9.0f}  frais {c['cost']:>8.0f}  "
              f"portage {c['funding']:>+8.0f}  net {c['pnl_net']:>+9.0f}  "
              f"{'✓' if c['pass'] else '✗'}", flush=True)
    c3 = all(r["pass"] for r in c3_rows)
    res["clauses"]["C3"] = {"pass": c3, "par_fenetre": c3_rows}
    print(f"C3  → {sum(r['pass'] for r in c3_rows)}/4 "
          f"{'PASSE' if c3 else 'ÉCHOUE'}", flush=True)

    # ── C4 : apport au portefeuille ───────────────────────────────────
    months = 25.0
    a_stats = curve_stats(a_r, months)
    t_stats = curve_stats(t_r, months)
    combos = []
    for wa, wt in ALLOCATIONS:
        ca, ct, tot, peak, dd = wa, wt, wa + wt, wa + wt, 0.0
        for ra, rt in zip(a_r, t_r):
            ca *= (1 + ra / 100)
            ct *= (1 + rt / 100)
            tot = ca + ct
            peak = max(peak, tot)
            dd = min(dd, (tot - peak) / peak * 100)
        cagr = (tot ** (12 / months) - 1) * 100 if tot > 0 else -100.0
        cal = cagr / abs(dd) if dd < 0 else float("inf")
        combos.append({"alloc": f"{wa:.0%}/{wt:.0%}", "end_mult": round(tot, 4),
                       "cagr_pct": round(cagr, 2), "dd_pct": round(dd, 2),
                       "calmar": round(cal, 3),
                       "pass": cal > a_stats["calmar"]})
        c = combos[-1]
        print(f"C4  {c['alloc']:9s} CAGR {c['cagr_pct']:>+8.1f}%  "
              f"DD {c['dd_pct']:>7.2f}%  Calmar {c['calmar']:>6.3f}  "
              f"(Alfred seul {a_stats['calmar']:.3f})  "
              f"{'✓' if c['pass'] else '✗'}", flush=True)
    c4 = all(c["pass"] for c in combos)
    res["clauses"]["C4"] = {"pass": c4, "alfred_seul": a_stats,
                            "trend_seul": t_stats, "portefeuilles": combos}
    print(f"C4  → {'PASSE' if c4 else 'ÉCHOUE'}", flush=True)

    verdict = all(res["clauses"][k]["pass"] for k in ("C1", "C2", "C3", "C4"))
    res["verdict"] = "CANDIDAT PAPER" if verdict else "REJET"
    res["window_long"] = {"start": WIN_LONG[0], "end": WIN_LONG[1],
                          "months": months}
    res["long_run"] = {k: long_run[k] for k in
                       ("n_trades", "end_capital", "funding_uncovered_frac",
                        "uncovered_symbols", "pos_hours", "n_open_at_end",
                        "open_notional_at_end")}
    res["long_run"]["gross"] = round(sum(t["gross"] for t in long_run["trades"]), 2)
    res["long_run"]["cost"] = round(sum(t["cost"] for t in long_run["trades"]), 2)
    res["long_run"]["funding"] = round(sum(t["funding"] for t in long_run["trades"]), 2)
    res["long_run"]["n_long"] = sum(1 for t in long_run["trades"] if t["dir"] > 0)
    res["long_run"]["hold_median_d"] = round(sorted(
        t["hold_h"] for t in long_run["trades"])[len(long_run["trades"]) // 2] / 24, 1)
    res["long_run"]["win_rate"] = round(
        sum(1 for t in long_run["trades"] if t["pnl"] > 0)
        / len(long_run["trades"]) * 100, 1)
    res["fingerprint"] = fingerprint(P, data, extra={
        "spec": "TREND-v0", "donchian": DONCHIAN_DAYS, "mom": MOMENTUM_DAYS,
        "atr": ATR_PERIOD, "mult": CHANDELIER_MULT, "risk": RISK_PER_POS,
        "window": WIN_LONG})

    out = os.path.join(ROOT, "analysis", "output", "trend_v0_verdict.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    csv_path = os.path.join(ROOT, "data", "trend_v0_daily_pnl.csv")
    with open(csv_path, "w") as f:
        f.write("date,pnl_usd,equity,n_open,ret_pct\n")
        for d in long_run["daily"]:
            f.write(f"{d['date']},{d['pnl_usd']},{d['equity']},"
                    f"{d['n_open']},{d['ret_pct']}\n")

    print(f"\n{'='*70}\nVERDICT : {res['verdict']}   "
          f"(C1 {'✓' if res['clauses']['C1']['pass'] else '✗'} · "
          f"C2 {'✓' if c2 else '✗'} · C3 {'✓' if c3 else '✗'} · "
          f"C4 {'✓' if c4 else '✗'})\n{'='*70}")
    print(f"Dump : {out}\n       {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
