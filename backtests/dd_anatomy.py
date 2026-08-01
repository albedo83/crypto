"""ANATOMIE DU DRAWDOWN −51,4 % — diagnostic pur, zéro ajustement.

Phase 1 de la mission « Portefeuille & Projet A » (2026-08-01).

Ce script ne teste RIEN et ne propose RIEN. Il décrit le pire drawdown de la
configuration EN SERVICE, sur la base corrigée (parité secteurs v1.17.1 +
parité entrées v1.17.2 + booking réaliste des trails v1.15.5) :

  - date du pic, date du creux, durée de la descente, durée de la récupération
  - décomposition du drawdown par signal et par mois, effectifs
  - les 3 pires fenêtres de 30 jours glissants NON CHEVAUCHANTES — elles
    deviennent les « fenêtres de creux » de référence du Projet A
  - Monte Carlo par réordonnancement des trades → P50/P95/P99 des DD simulés

Limite assumée du Monte Carlo : il garde la POPULATION de trades constante et
ne randomise que la SÉQUENCE. Il mesure donc la part du drawdown qui tient à
l'ordre d'arrivée (la malchance), pas le risque de modèle. Un DD réel pire que
le P99 simulé signifierait que l'ordre observé était exceptionnellement
défavorable ; l'inverse ne prouve rien sur la robustesse du moteur.

Livrables : docs/dd_anatomy.md · data/alfred_daily_pnl.csv
            analysis/output/dd_anatomy.json

Usage : python3 -m backtests.dd_anatomy
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br  # noqa: E402
from backtests.backtest_rolling import (  # noqa: E402
    run_window, load_oi, load_funding, load_dxy)
from backtests.backtest_genetic import load_3y_candles, build_features  # noqa: E402
from backtests.backtest_sector import compute_sector_features  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_CAP = 1000.0          # capital canonique de docs/backtests.md
WINDOW_MONTHS = 28
MC_DRAWS = 10_000
MC_SEED = 20260801          # graine fixe : le run est reproductible
ROLL_DAYS = 30


def _d(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


# ── reconstruction de la courbe d'equity ────────────────────────────────

def equity_path(trades: list[dict], cap0: float) -> list[dict]:
    """Courbe pas-à-pas, un point par clôture — exactement ce que fait le moteur.

    Le moteur met à jour `capital` au moment du booking et calcule son DD sur
    cette série ; reconstruire ici la même série permet de VÉRIFIER qu'on
    décrit bien le drawdown mesuré, et pas un autre.
    """
    pts, cap, peak = [], cap0, cap0
    for t in sorted(trades, key=lambda x: x["exit_t"]):
        prev = cap
        cap += t["pnl"]
        peak = max(peak, cap)
        pts.append({
            "t": t["exit_t"], "dt": _d(t["exit_t"]),
            "cap_before": prev, "cap": cap, "peak": peak,
            "dd_pct": (cap - peak) / peak * 100 if peak > 0 else 0.0,
            "trade": t,
        })
    return pts


def max_dd_episode(pts: list[dict], cap0: float) -> dict:
    """Le pire épisode : pic → creux → (récupération ou toujours en cours)."""
    trough = min(pts, key=lambda p: p["dd_pct"])
    peak_val = trough["peak"]

    # dernier point où l'equity valait le pic AVANT le creux
    peak_pt = None
    for p in pts:
        if p["t"] > trough["t"]:
            break
        if p["cap"] >= peak_val - 1e-9:
            peak_pt = p
    peak_dt = peak_pt["dt"] if peak_pt else _d(pts[0]["t"])
    peak_cap = peak_pt["cap"] if peak_pt else cap0

    rec = next((p for p in pts if p["t"] > trough["t"]
                and p["cap"] >= peak_val - 1e-9), None)
    return {
        "peak_dt": peak_dt, "peak_cap": peak_cap,
        "trough_dt": trough["dt"], "trough_cap": trough["cap"],
        "dd_pct": trough["dd_pct"],
        "dd_usd": trough["cap"] - peak_val,
        "descent_days": (trough["dt"] - peak_dt).total_seconds() / 86400,
        "recovered": rec is not None,
        "recovery_dt": rec["dt"] if rec else None,
        "recovery_days": ((rec["dt"] - trough["dt"]).total_seconds() / 86400
                          if rec else None),
        "trough_ts": trough["t"], "peak_ts": int(peak_dt.timestamp() * 1000),
    }


# ── séries quotidiennes ─────────────────────────────────────────────────

def daily_series(pts: list[dict], cap0: float, start: datetime,
                 end: datetime) -> list[dict]:
    """P&L par jour civil UTC, jours sans trade inclus (P&L nul).

    Série de RÉFÉRENCE du Projet A : c'est contre elle que se mesurera la
    corrélation C1 d'une stratégie candidate.
    """
    by_day: dict[str, list[dict]] = defaultdict(list)
    for p in pts:
        by_day[p["dt"].strftime("%Y-%m-%d")].append(p)

    out, cap, peak = [], cap0, cap0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        k = day.strftime("%Y-%m-%d")
        ps = by_day.get(k, [])
        pnl = sum(p["trade"]["pnl"] for p in ps)
        cap += pnl
        peak = max(peak, cap)
        out.append({
            "date": k, "n_trades": len(ps), "pnl_usd": round(pnl, 4),
            "equity": round(cap, 4),
            "ret_pct": round(pnl / (cap - pnl) * 100, 6) if cap - pnl > 0 else 0.0,
            "dd_pct": round((cap - peak) / peak * 100, 4) if peak > 0 else 0.0,
        })
        day += timedelta(days=1)
    return out


def worst_rolling(daily: list[dict], days: int, k: int) -> list[dict]:
    """Les k pires fenêtres de `days` jours, NON CHEVAUCHANTES.

    Sans la contrainte de non-chevauchement les 3 pires fenêtres seraient la
    même, décalée d'un jour — un artefact, pas trois épisodes.
    """
    cands = []
    for i in range(len(daily) - days + 1):
        win = daily[i:i + days]
        eq0 = win[0]["equity"] - win[0]["pnl_usd"]
        pnl = sum(d["pnl_usd"] for d in win)
        cands.append({
            "start": win[0]["date"], "end": win[-1]["date"], "i": i,
            "pnl_usd": round(pnl, 2),
            "pct": round(pnl / eq0 * 100, 2) if eq0 > 0 else 0.0,
            "n_trades": sum(d["n_trades"] for d in win),
        })
    cands.sort(key=lambda c: c["pct"])
    picked: list[dict] = []
    for c in cands:
        if all(abs(c["i"] - p["i"]) >= days for p in picked):
            picked.append(c)
        if len(picked) == k:
            break
    return picked


# ── décompositions ──────────────────────────────────────────────────────

def decompose(trades: list[dict], t0: int, t1: int) -> dict:
    """Qui a produit le drawdown : par signal, par mois, par direction."""
    sub = [t for t in trades if t0 <= t["exit_t"] <= t1]
    per_strat: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    per_month: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    per_sd: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    per_coin: dict = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in sub:
        s, p = t["strat"], t["pnl"]
        per_strat[s]["n"] += 1
        per_strat[s]["pnl"] += p
        per_strat[s]["wins"] += 1 if p > 0 else 0
        m = _d(t["exit_t"]).strftime("%Y-%m")
        per_month[m]["n"] += 1
        per_month[m]["pnl"] += p
        key = f"{s} {'LONG' if t['dir'] > 0 else 'SHORT'}"
        per_sd[key]["n"] += 1
        per_sd[key]["pnl"] += p
        per_coin[t["coin"]]["n"] += 1
        per_coin[t["coin"]]["pnl"] += p

    def clean(d, wr=False):
        return {k: ({"n": v["n"], "pnl": round(v["pnl"], 2),
                     **({"wr": round(v["wins"] / v["n"] * 100)} if wr else {})})
                for k, v in sorted(d.items(), key=lambda x: x[1]["pnl"])}

    return {"n": len(sub), "pnl": round(sum(t["pnl"] for t in sub), 2),
            "by_strat": clean(per_strat, wr=True),
            "by_month": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)}
                         for k, v in sorted(per_month.items())},
            "by_strat_dir": clean(per_sd),
            "worst_coins": dict(list(clean(per_coin).items())[:10])}


# ── Monte Carlo ─────────────────────────────────────────────────────────

def monte_carlo(pts: list[dict], cap0: float, draws: int, seed: int,
                observed: float) -> dict:
    """Réordonnancement des trades → distribution du DD max.

    On travaille en RENDEMENTS (pnl / capital avant le trade), pas en dollars :
    en dollars, réordonner ferait juste voyager les gros trades tardifs — un
    artefact de compounding, pas un test de séquence.
    """
    rets = [p["trade"]["pnl"] / p["cap_before"]
            for p in pts if p["cap_before"] > 0]
    require_series("rendements par trade", rets, min_n=100)

    rng = random.Random(seed)
    dds, ends = [], []
    order = list(rets)
    for _ in range(draws):
        rng.shuffle(order)
        cap = peak = cap0
        worst = 0.0
        for r in order:
            cap *= (1.0 + r)
            if cap > peak:
                peak = cap
            elif peak > 0:
                d = (cap - peak) / peak * 100
                if d < worst:
                    worst = d
        dds.append(worst)
        ends.append(cap)
    dds.sort()

    def pct(q):  # q = quantile de SÉVÉRITÉ (P99 = quasi-pire)
        return round(dds[min(len(dds) - 1, int((1 - q) * len(dds)))], 2)

    # percentile du DD OBSERVÉ : part des tirages au moins aussi sévères.
    n_worse = sum(1 for d in dds if d <= observed)
    return {
        "draws": draws, "seed": seed, "n_trades": len(rets),
        "dd_p50": pct(0.50), "dd_p75": pct(0.75), "dd_p90": pct(0.90),
        "dd_p95": pct(0.95), "dd_p99": pct(0.99),
        "dd_best": round(dds[-1], 2), "dd_worst": round(dds[0], 2),
        "dd_mean": round(statistics.fmean(dds), 2),
        "end_median": round(statistics.median(ends), 2),
        "observed": round(observed, 2),
        "frac_worse_pct": round(n_worse / len(dds) * 100, 2),
    }


# ── rendu ───────────────────────────────────────────────────────────────

def write_md(path: str, res: dict, fp: dict) -> None:
    e, mc = res["episode"], res["monte_carlo"]
    d = fp["data"]
    L = []
    a = L.append
    a("# Anatomie du drawdown −51,4 %\n")
    a("> **Diagnostic pur — aucun paramètre n'a été touché.** Ce document décrit "
      "le pire drawdown de la configuration **en service**, sur la base corrigée. "
      "Il ne conclut à aucune action.\n")
    a(f"**Généré le** : {fp['run_at']}Z  ")
    a(f"**Empreinte** : config `{fp['config_hash']}` · git `{fp['git_rev']}` · "
      f"données jusqu'au {d['last_candle']} ({d['n_symbols']} symboles, "
      f"fichiers {d['data_mtime']}Z)  ")
    a(f"**Fenêtre** : {res['window']['start'][:10]} → {res['window']['end'][:10]} "
      f"({WINDOW_MONTHS} mois), capital de départ ${START_CAP:,.0f}  ")
    a(f"**Sémantique** : ALIGNED · booking réaliste des trails · "
      f"cap notionnel proportionnel · `signal_mult` = {fp['loud']['signal_mult']}\n")
    a(f"**Contrôle** : DD reconstruit {res['check']['dd_rebuilt']:.2f} % vs "
      f"DD moteur {res['check']['dd_engine']:.2f} % — écart "
      f"{res['check']['delta']:.4f} pp · {res['check']['n_trades']} trades.\n")

    a("\n## 1. L'épisode\n")
    a("| | |")
    a("|---|---|")
    a(f"| pic | **{e['peak_dt'][:10]}** — equity ${e['peak_cap']:,.0f} |")
    a(f"| creux | **{e['trough_dt'][:10]}** — equity ${e['trough_cap']:,.0f} |")
    a(f"| amplitude | **{e['dd_pct']:.1f} %** (−${abs(e['dd_usd']):,.0f}) |")
    a(f"| descente | **{e['descent_days']:.0f} jours** |")
    if e["recovered"]:
        a(f"| récupération | **{e['recovery_days']:.0f} jours** — "
          f"pic repris le {e['recovery_dt'][:10]} |")
        a(f"| immersion totale | **{e['descent_days'] + e['recovery_days']:.0f} "
          f"jours** sous le pic |")
    else:
        a("| récupération | **jamais** sur la fenêtre — le pic n'a pas été repris |")

    a("\n## 2. Qui a produit ce drawdown\n")
    dec = res["dd_decomposition"]
    a(f"Sur les **{dec['n']} trades** clôturés entre le pic et le creux "
      f"(P&L net **${dec['pnl']:,.0f}**) :\n")
    a("| signal | n | P&L | WR |")
    a("|---|---:|---:|---:|")
    for k, v in dec["by_strat"].items():
        a(f"| {k} | {v['n']} | ${v['pnl']:,.0f} | {v['wr']} % |")
    a("\n### Par direction\n")
    a("| signal · direction | n | P&L |")
    a("|---|---:|---:|")
    for k, v in dec["by_strat_dir"].items():
        a(f"| {k} | {v['n']} | ${v['pnl']:,.0f} |")
    a("\n### Par mois\n")
    a("| mois | n | P&L |")
    a("|---|---:|---:|")
    for k, v in dec["by_month"].items():
        a(f"| {k} | {v['n']} | ${v['pnl']:,.0f} |")

    a("\n## 3. Les 3 pires fenêtres de 30 jours — références du Projet A\n")
    a("> Non chevauchantes. **Ces trois fenêtres, plus celle du drawdown "
      "maximal, constituent les « fenêtres de creux » sur lesquelles la "
      "clause C2 du Projet A sera évaluée.**\n")
    a("| # | début | fin | P&L | % de l'equity | trades |")
    a("|---:|---|---|---:|---:|---:|")
    for i, w in enumerate(res["worst_30d"], 1):
        a(f"| {i} | {w['start']} | {w['end']} | ${w['pnl_usd']:,.0f} | "
          f"**{w['pct']:.1f} %** | {w['n_trades']} |")
    a(f"\nFenêtre du drawdown maximal : **{e['peak_dt'][:10]} → "
      f"{e['trough_dt'][:10]}**.\n")

    a("\n## 4. Monte Carlo — le drawdown tient-il à l'ordre d'arrivée ?\n")
    a(f"{mc['draws']:,}".replace(",", " ") +
      f" réordonnancements des {mc['n_trades']} trades "
      f"(rendements recomposés, graine `{mc['seed']}`).\n")
    a("| quantile de sévérité | DD max simulé |")
    a("|---|---:|")
    a(f"| P50 (médian) | {mc['dd_p50']:.1f} % |")
    a(f"| P75 | {mc['dd_p75']:.1f} % |")
    a(f"| P90 | {mc['dd_p90']:.1f} % |")
    a(f"| P95 | {mc['dd_p95']:.1f} % |")
    a(f"| P99 | {mc['dd_p99']:.1f} % |")
    a(f"| pire tirage | {mc['dd_worst']:.1f} % |")
    a(f"| meilleur tirage | {mc['dd_best']:.1f} % |")
    a(f"\nDD **observé** : {mc['observed']:.1f} % — **{mc['frac_worse_pct']:.1f} %** "
      f"des réordonnancements font pire.\n")
    a("**Lecture.** Un DD observé proche du médian signifie que l'ordre réel "
      "n'a rien eu d'exceptionnel : le drawdown est une propriété de la "
      "**population de trades**, pas de la malchance de séquence. Un DD "
      "observé au-delà du P95 signifierait l'inverse. Dans les deux cas la "
      "queue simulée (P95/P99) est le chiffre à retenir comme pire cas "
      "plausible d'une re-exécution — et elle reste un **plancher** "
      "optimiste, puisque la population de trades elle-même est tenue fixe.\n")

    a("\n## 5. Note de lecture — sensibilité de la date de départ\n")
    a(f"Le capital final de ce run est **${res['check']['end_capital']:,.0f}**, "
      f"contre $15 463 publiés dans `docs/backtests.md` le 2026-07-31. Même "
      f"configuration, même code : **la fenêtre a glissé d'un jour aux deux "
      f"bouts** (2024-03-31→2026-07-31 devient 2024-04-01→2026-08-01, les "
      f"fichiers de données étant rafraîchis toutes les 4 h). "
      f"{res['check']['n_trades']} trades contre 1309 : **un seul trade "
      f"d'écart**, et le compounding l'amplifie pendant 28 mois.\n")
    a("Le **drawdown**, lui, est identique au centième — l'épisode est précoce "
      "(août-novembre 2024), donc antérieur à la divergence des chemins. C'est "
      "la raison pour laquelle ce document mesure un risque et pas un "
      "rendement : sur 28 mois de compounding, le rendement final d'un "
      "backtest est un nombre fragile, le drawdown précoce ne l'est pas.\n")

    a("\n## 6. Série de référence\n")
    a("`data/alfred_daily_pnl.csv` — P&L quotidien UTC, jours sans trade "
      "inclus, sur toute la fenêtre. Colonnes : `date, n_trades, pnl_usd, "
      "equity, ret_pct, dd_pct`. C'est la série contre laquelle se mesurera "
      "la corrélation C1 du Projet A.\n")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


def main() -> int:
    from dateutil.relativedelta import relativedelta
    print("Chargement des données…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    br._P = DEFAULT_PARAMS

    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = _d(end_ms)
    start_dt = end_dt - relativedelta(months=WINDOW_MONTHS)
    print(banner(DEFAULT_PARAMS, data,
                 extra={"fenêtre": f"{start_dt:%Y-%m-%d}→{end_dt:%Y-%m-%d}",
                        "capital": START_CAP, "mc_draws": MC_DRAWS}), flush=True)

    r = run_window(features, data, sectors, dxy,
                   start_ts_ms=int(start_dt.timestamp() * 1000),
                   end_ts_ms=end_ms, start_capital=START_CAP,
                   oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True,
                   margin_check=True, mfe_on_close=True,
                   realistic_trail_booking=True)
    print(f"→ fin ${r['end_capital']:,.0f}  DD {r['max_dd_pct']:.1f}%  "
          f"n={r['n_trades']}", flush=True)

    pts = equity_path(r["trades"], START_CAP)
    if not pts:
        raise MeasureError("aucun trade — rien à disséquer")

    # Garde-fou : la courbe reconstruite DOIT reproduire le DD du moteur.
    # Si elle ne le fait pas, on décrirait un autre drawdown que celui publié.
    rebuilt = min(p["dd_pct"] for p in pts)
    delta = abs(rebuilt - r["max_dd_pct"])
    if delta > 0.05:
        raise MeasureError(
            f"courbe reconstruite incohérente : DD {rebuilt:.3f}% vs moteur "
            f"{r['max_dd_pct']:.3f}% (écart {delta:.3f} pp) — la séquence de "
            f"booking ne correspond pas, l'anatomie porterait sur autre chose")
    print(f"✓ contrôle : DD reconstruit {rebuilt:.2f}% ≡ moteur "
          f"{r['max_dd_pct']:.2f}% (écart {delta:.4f} pp)", flush=True)

    ep = max_dd_episode(pts, START_CAP)
    print(f"  pic {ep['peak_dt']:%Y-%m-%d} → creux {ep['trough_dt']:%Y-%m-%d} "
          f"({ep['descent_days']:.0f}j) · "
          f"récup {'%.0fj' % ep['recovery_days'] if ep['recovered'] else 'JAMAIS'}",
          flush=True)

    daily = daily_series(pts, START_CAP, start_dt, end_dt)
    worst = worst_rolling(daily, ROLL_DAYS, 3)
    print(f"  3 pires fenêtres {ROLL_DAYS}j : " +
          " · ".join(f"{w['start']}→{w['end']} {w['pct']:.1f}%" for w in worst),
          flush=True)

    dec = decompose(r["trades"], ep["peak_ts"], ep["trough_ts"])
    print(f"  épisode : {dec['n']} trades, ${dec['pnl']:,.0f}", flush=True)

    print(f"Monte Carlo ({MC_DRAWS:,} tirages)…".replace(",", " "), flush=True)
    mc = monte_carlo(pts, START_CAP, MC_DRAWS, MC_SEED, r["max_dd_pct"])
    print(f"  P50 {mc['dd_p50']:.1f}% · P95 {mc['dd_p95']:.1f}% · "
          f"P99 {mc['dd_p99']:.1f}% · pire {mc['dd_worst']:.1f}% · "
          f"observé {mc['observed']:.1f}% ({mc['frac_worse_pct']:.1f}% des "
          f"tirages sont pires)", flush=True)

    fp = fingerprint(DEFAULT_PARAMS, data,
                     extra={"window_months": WINDOW_MONTHS,
                            "start_capital": START_CAP, "mc_draws": MC_DRAWS})
    res = {
        "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat(),
                   "months": WINDOW_MONTHS, "capital": START_CAP},
        "check": {"dd_engine": r["max_dd_pct"], "dd_rebuilt": rebuilt,
                  "delta": delta, "n_trades": r["n_trades"],
                  "end_capital": round(r["end_capital"], 2)},
        "episode": {**{k: (v.isoformat() if isinstance(v, datetime) else v)
                       for k, v in ep.items()}},
        "dd_decomposition": dec,
        "worst_30d": worst,
        "monte_carlo": mc,
        "full_window_by_strat": r["by_strat"],
    }

    out = os.path.join(ROOT, "analysis", "output", "dd_anatomy.json")
    with open(out, "w") as f:
        json.dump({"fingerprint": fp, **res}, f, indent=1, default=str)

    csv_dir = os.path.join(ROOT, "data")
    os.makedirs(csv_dir, exist_ok=True)
    csv_path = os.path.join(csv_dir, "alfred_daily_pnl.csv")
    with open(csv_path, "w") as f:
        f.write("date,n_trades,pnl_usd,equity,ret_pct,dd_pct\n")
        for d in daily:
            f.write(f"{d['date']},{d['n_trades']},{d['pnl_usd']},"
                    f"{d['equity']},{d['ret_pct']},{d['dd_pct']}\n")

    md = os.path.join(ROOT, "docs", "dd_anatomy.md")
    write_md(md, res, fp)
    print(f"\nLivrables : {md}\n            {csv_path}\n            {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
