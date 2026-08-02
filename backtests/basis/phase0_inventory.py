"""PHASE 0 — faisabilité DONNÉES d'une étude de basis inter-venues.

Leçon du projet market making : on établit que la donnée existe, qu'elle est
alignable et à quel coût, AVANT de concevoir quoi que ce soit. Ce script ne
mesure aucun basis, ne calcule aucun différentiel, ne conclut sur aucun edge.

Il répond à quatre questions et s'arrête :

 1. quels tokens de `Params.trade_symbols` existent des DEUX côtés ?
 2. quelle profondeur d'historique de chaque côté, token par token ?
 3. les horodatages sont-ils alignables sur une grille horaire commune ?
 4. quelle est la sémantique EXACTE du taux de funding de chaque venue ?

La question 4 n'est pas cosmétique : Hyperliquid publie un taux **horaire**,
Binance et Bybit un taux **par période** — 8 h le plus souvent, 4 h pour
certains tokens, et pas forcément la même des deux côtés. Comparer sans
convertir crée un différentiel fantôme d'un facteur 4 à 8.

Lectures publiques uniquement. Aucune clé, aucun ordre, aucun compte.

Livrable : docs/basis_phase0_inventory.md · analysis/output/basis_phase0.json

Usage : python3 -m backtests.basis.phase0_inventory
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.backtest_genetic import load_3y_candles  # noqa: E402
from backtests.fingerprint import banner, fingerprint  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FUNDING_DB = os.path.join(ROOT, "backtests", "output", "funding_history.db")
PAUSE = 0.12          # politesse envers les API publiques
MIN_COVERAGE = 0.95   # sous ce taux, la cellule de phase 1 sera NON ÉMISE


def _ms(y, m, d=1) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def gj(url: str, tries: int = 3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429) and k < tries - 1:
                time.sleep(2 ** k)
                continue
            return {"__error__": f"HTTP {e.code}"}
        except Exception as e:
            if k < tries - 1:
                time.sleep(1)
                continue
            return {"__error__": str(e)[:80]}
    return {"__error__": "épuisé"}


# ── inventaire Hyperliquid (côté déjà en base) ──────────────────────────

def hl_inventory(universe: list[str]) -> dict:
    data = load_3y_candles()
    out = {}
    con = sqlite3.connect(FUNDING_DB)
    for s in universe:
        c = data.get(s) or []
        rows = con.execute(
            "SELECT ts FROM funding WHERE symbol=? ORDER BY ts", (s,)).fetchall()
        ts = [r[0] for r in rows]
        gaps = None
        if len(ts) > 10:
            d = [(ts[i + 1] - ts[i]) / 3_600_000 for i in range(len(ts) - 1)]
            span_h = (ts[-1] - ts[0]) / 3_600_000
            gaps = {"median_spacing_h": round(statistics.median(d), 3),
                    "max_spacing_h": round(max(d), 2),
                    "n_gaps_gt_2h": sum(1 for x in d if x > 2.0),
                    "hours_missing": round(span_h - len(ts) + 1, 1),
                    "coverage_pct": round(len(ts) / max(span_h, 1) * 100, 2)}
        out[s] = {
            "candles_n": len(c),
            "candles_first": _d(c[0]["t"]) if c else None,
            "candles_last": _d(c[-1]["t"]) if c else None,
            "funding_n": len(ts),
            "funding_first": _d(ts[0]) if ts else None,
            "funding_last": _d(ts[-1]) if ts else None,
            "funding_gaps": gaps}
    con.close()
    return out


# ── inventaire Binance ──────────────────────────────────────────────────

def binance_inventory(universe: list[str]) -> dict:
    spot_info = gj("https://api.binance.com/api/v3/exchangeInfo")
    fut_info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
    if "__error__" in spot_info or "__error__" in fut_info:
        raise MeasureError(f"Binance exchangeInfo injoignable : "
                           f"{spot_info.get('__error__') or fut_info.get('__error__')}")
    spot = {x["symbol"]: x for x in spot_info["symbols"]
            if x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT"}
    fut = {x["symbol"]: x for x in fut_info["symbols"]
           if x.get("status") == "TRADING" and x.get("contractType") == "PERPETUAL"
           and x.get("quoteAsset") == "USDT"}
    fi = {x["symbol"]: x for x in gj("https://fapi.binance.com/fapi/v1/fundingInfo")
          if isinstance(x, dict) and "symbol" in x}

    out = {}
    for s in universe:
        sym = f"{s}USDT"
        rec = {"spot": sym in spot, "perp": sym in fut,
               "funding_interval_h": None, "spot_first": None,
               "perp_funding_first": None}
        if sym in fut:
            rec["funding_interval_h"] = int(
                fi.get(sym, {}).get("fundingIntervalHours", 8))
        if sym in spot:
            k = gj(f"https://api.binance.com/api/v3/klines?symbol={sym}"
                   f"&interval=1d&startTime={_ms(2017, 7)}&limit=1")
            time.sleep(PAUSE)
            if isinstance(k, list) and k:
                rec["spot_first"] = _d(k[0][0])
        if sym in fut:
            # ⚠ startTime=0 est IGNORÉ par Binance : l'endpoint renvoie alors
            # les enregistrements les PLUS RÉCENTS. Le premier passage de cette
            # étude a ainsi daté toutes les origines de funding à aujourd'hui —
            # un nombre plausible et faux. On ancre sur le lancement de Binance
            # Futures (2019-09) pour obtenir le vrai premier enregistrement.
            fr = gj(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}"
                    f"&startTime={_ms(2019, 9)}&limit=1")
            time.sleep(PAUSE)
            if isinstance(fr, list) and fr:
                rec["perp_funding_first"] = _d(fr[0]["fundingTime"])
        out[s] = rec
    return out


# ── inventaire Bybit ────────────────────────────────────────────────────

def bybit_inventory(universe: list[str]) -> dict:
    def instruments(cat):
        acc, cur = {}, None
        while True:
            u = (f"https://api.bybit.com/v5/market/instruments-info?category={cat}"
                 f"&limit=1000" + (f"&cursor={cur}" if cur else ""))
            r = gj(u)
            if "__error__" in r or r.get("retCode") != 0:
                raise MeasureError(f"Bybit instruments-info {cat} : {r}")
            for x in r["result"]["list"]:
                acc[x["symbol"]] = x
            cur = r["result"].get("nextPageCursor")
            if not cur:
                return acc
            time.sleep(PAUSE)

    spot = instruments("spot")
    lin = instruments("linear")
    out = {}
    for s in universe:
        sym = f"{s}USDT"
        sp, ln = spot.get(sym), lin.get(sym)
        out[s] = {
            "spot": bool(sp) and sp.get("status") == "Trading",
            "perp": bool(ln) and ln.get("status") == "Trading",
            "funding_interval_h": (int(ln["fundingInterval"]) / 60
                                   if ln and ln.get("fundingInterval") else None),
            "perp_launch": _d(ln["launchTime"]) if ln and ln.get("launchTime") else None,
            "spot_launch": _d(sp["launchTime"]) if sp and sp.get("launchTime") else None,
        }
    return out


# ── alignement horaire ──────────────────────────────────────────────────

def alignment_check(sample: list[str]) -> dict:
    """Les horodatages tombent-ils sur des frontières exploitables ?"""
    res = {"samples": {}, "verdict": None}
    problems = []
    for s in sample:
        sym = f"{s}USDT"
        rec = {}
        k = gj(f"https://api.binance.com/api/v3/klines?symbol={sym}"
               f"&interval=1h&startTime={_ms(2026, 7, 1)}&limit=6")
        time.sleep(PAUSE)
        if isinstance(k, list) and k:
            rec["binance_spot_1h"] = [_hhmm(x[0]) for x in k[:4]]
            rec["binance_spot_on_hour"] = all(x[0] % 3_600_000 == 0 for x in k)
        fr = gj(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}"
                f"&startTime={_ms(2026, 7, 1)}&limit=6")
        time.sleep(PAUSE)
        if isinstance(fr, list) and fr:
            rec["binance_funding"] = [_hhmm(x["fundingTime"]) for x in fr[:4]]
            rec["binance_funding_on_8h"] = all(
                x["fundingTime"] % (8 * 3_600_000) == 0 for x in fr)
        y = gj(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}"
               f"&interval=60&start={_ms(2026, 7, 1)}&limit=6")
        time.sleep(PAUSE)
        if y.get("retCode") == 0 and y["result"]["list"]:
            ts = [int(x[0]) for x in y["result"]["list"]]
            rec["bybit_spot_1h"] = [_hhmm(t) for t in sorted(ts)[:4]]
            rec["bybit_spot_on_hour"] = all(t % 3_600_000 == 0 for t in ts)
        yf = gj(f"https://api.bybit.com/v5/market/funding/history?category=linear"
                f"&symbol={sym}&startTime={_ms(2026, 7, 1)}"
                f"&endTime={_ms(2026, 7, 3)}&limit=6")
        time.sleep(PAUSE)
        if yf.get("retCode") == 0 and yf["result"]["list"]:
            ts = [int(x["fundingRateTimestamp"]) for x in yf["result"]["list"]]
            rec["bybit_funding"] = [_hhmm(t) for t in sorted(ts)[:4]]
            rec["bybit_funding_on_8h"] = all(t % (8 * 3_600_000) == 0 for t in ts)
        for k2, v in rec.items():
            if k2.endswith(("on_hour", "on_8h")) and v is False:
                problems.append(f"{s}:{k2}")
        res["samples"][s] = rec
    res["problems"] = problems
    res["verdict"] = "ALIGNABLE" if not problems else "DÉSALIGNÉ"
    return res


def _hhmm(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%m-%d %H:%M")


# ── programme ───────────────────────────────────────────────────────────

def main() -> int:
    universe = sorted(P.trade_symbols)
    _data = load_3y_candles()
    print(banner(P, _data, extra={"étude": "basis phase 0 — inventaire données",
                                  "univers": len(universe)}), flush=True)
    print(f"Univers Params.trade_symbols : {len(universe)} tokens", flush=True)

    print("\n── Hyperliquid (déjà en base) ──", flush=True)
    hl = hl_inventory(universe)
    n_hl = sum(1 for v in hl.values() if v["candles_n"] and v["funding_n"])
    print(f"  {n_hl}/{len(universe)} tokens avec bougies ET funding", flush=True)

    print("── Binance (API publique) ──", flush=True)
    bn = binance_inventory(universe)
    print(f"  spot {sum(1 for v in bn.values() if v['spot'])} · "
          f"perp {sum(1 for v in bn.values() if v['perp'])}", flush=True)

    print("── Bybit (API publique) ──", flush=True)
    by = bybit_inventory(universe)
    print(f"  spot {sum(1 for v in by.values() if v['spot'])} · "
          f"perp {sum(1 for v in by.values() if v['perp'])}", flush=True)

    # intersection exploitable : HL perp (mark+funding) × venue B (spot + perp)
    usable = {}
    for s in universe:
        h = hl[s]
        hl_ok = bool(h["candles_n"]) and bool(h["funding_n"])
        b_spot = bn[s]["spot"] or by[s]["spot"]
        b_perp = bn[s]["perp"] or by[s]["perp"]
        usable[s] = {"hl": hl_ok, "b_spot": b_spot, "b_perp": b_perp,
                     "both": hl_ok and b_spot and b_perp}
    n_both = sum(1 for v in usable.values() if v["both"])

    print(f"\n── Intersection : {n_both}/{len(universe)} tokens des DEUX côtés "
          f"(HL mark+funding × venue B spot+perp) ──")
    print(f"  {'token':7s} {'HL bougies':>18s} {'HL funding':>18s} "
          f"{'HL couv.':>9s} {'BN':>7s} {'BY':>7s} {'exploitable':>12s}")
    for s in universe:
        h, u = hl[s], usable[s]
        g = h["funding_gaps"] or {}
        cov = g.get("coverage_pct")
        bnm = ("s" if bn[s]["spot"] else "-") + ("p" if bn[s]["perp"] else "-")
        bym = ("s" if by[s]["spot"] else "-") + ("p" if by[s]["perp"] else "-")
        flag = "OUI" if u["both"] else "non"
        if u["both"] and cov is not None and cov < MIN_COVERAGE * 100:
            flag = "couv. <95%"
        print(f"  {s:7s} {str(h['candles_first']) + '→' + str(h['candles_last']):>18s} "
              f"{str(h['funding_first']) + '→' + str(h['funding_last']):>18s} "
              f"{(f'{cov:.1f}%' if cov is not None else '—'):>9s} "
              f"{bnm:>7s} {bym:>7s} {flag:>12s}")

    print("\n── Sémantique du funding — LE piège ──")
    sem = {
        "hyperliquid": {"interval_h": 1.0, "rate_basis": "par heure",
                        "source": "espacement mesuré dans funding_history.db"},
        "binance": {"interval_h": None, "rate_basis": "par période de 8 h",
                    "source": "fapi/v1/fundingInfo.fundingIntervalHours"},
        "bybit": {"interval_h": None, "rate_basis": "par période de 8 h",
                  "source": "v5/market/instruments-info.fundingInterval (minutes)"},
    }
    bn_iv = [v["funding_interval_h"] for v in bn.values()
             if v["funding_interval_h"]]
    by_iv = [v["funding_interval_h"] for v in by.values()
             if v["funding_interval_h"]]
    sem["binance"]["interval_h"] = sorted(set(bn_iv))
    sem["bybit"]["interval_h"] = sorted(set(by_iv))
    hl_sp = [v["funding_gaps"]["median_spacing_h"] for v in hl.values()
             if v["funding_gaps"]]
    require_series("espacement HL", hl_sp, min_n=10, allow_constant=True)
    sem["hyperliquid"]["measured_median_spacing_h"] = round(
        statistics.median(hl_sp), 3)
    print(f"  Hyperliquid : espacement médian mesuré "
          f"{sem['hyperliquid']['measured_median_spacing_h']} h → taux HORAIRE")
    print(f"  Binance     : fundingIntervalHours = {sem['binance']['interval_h']}")
    print(f"  Bybit       : fundingInterval      = {sem['bybit']['interval_h']} h")
    print("  ⚠ le facteur de conversion n'est PAS uniforme : il vaut l'intervalle")
    print("    de la venue B, qui varie par token ET entre venues (voir groupes).")
    from collections import defaultdict as _dd
    _g = _dd(list)
    for _s in universe:
        _k = (bn[_s]["funding_interval_h"], by[_s]["funding_interval_h"])
        if _k[0] or _k[1]:
            _g[_k].append(_s)
    for _k, _v in sorted(_g.items(), key=lambda x: -len(x[1])):
        print(f"      BN={_k[0]}h BY={_k[1]}h  n={len(_v):2d} : {' '.join(sorted(_v))}")
    print("    ⚠ ces intervalles CHANGENT dans le temps et `fundingInfo` ne donne")
    print("      que la valeur COURANTE. Une étude historique doit dériver")
    print("      l'intervalle de l'espacement des horodatages, pas du métadonné.")

    print("\n── Alignement horaire (échantillon) ──")
    sample = [s for s in ("SOL", "AAVE", "GMX", "TON") if usable[s]["both"]][:4]
    al = alignment_check(sample) if sample else {"verdict": "NON TESTÉ"}
    print(f"  verdict : {al['verdict']}" +
          (f"  problèmes : {al.get('problems')}" if al.get("problems") else ""))
    for s, rec in al.get("samples", {}).items():
        print(f"    {s:6s} BN spot {rec.get('binance_spot_1h', ['—'])[:2]} "
              f"BN fund {rec.get('binance_funding', ['—'])[:2]} "
              f"BY spot {rec.get('bybit_spot_1h', ['—'])[:2]} "
              f"BY fund {rec.get('bybit_funding', ['—'])[:2]}")

    res = {"universe": universe, "n_universe": len(universe),
           "hyperliquid": hl, "binance": bn, "bybit": by,
           "usable": usable, "n_usable": n_both,
           "funding_semantics": sem, "alignment": al,
           "min_coverage": MIN_COVERAGE,
           "fingerprint": fingerprint(P, extra={"étude": "basis_phase0"})}
    out = os.path.join(ROOT, "analysis", "output", "basis_phase0.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
