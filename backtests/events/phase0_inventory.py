"""PHASE 0 — faisabilité et définition des événements (cascades, listings).

Lectures publiques uniquement. Aucune clé, aucun ordre.

Ce script ne mesure AUCUN rendement post-événement. Il répond à trois questions
et s'arrête :

 1. la définition de cascade est-elle calculable, et sur quelle fenêtre ?
 2. combien d'événements, avant et après dé-clustering ?
 3. combien de listings de perps HL, et depuis quand ?

Garde-fou de dénominateur (leçon « +264 Md % ») : **ΔOI est mesuré en unités
absolues**, jamais en pourcentage — les percentiles étant calculés par token,
aucune comparaison inter-token n'exige de normaliser, et l'on évite ainsi
purement et simplement le risque de division par un OI proche de zéro.

Livrable : docs/event_phase0.md · analysis/output/event_phase0.json

Usage : python3 -m backtests.events.phase0_inventory
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
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from backtests.fingerprint import git_rev  # noqa: E402
from alfred.settings import DEFAULT_PARAMS as P  # noqa: E402
from measure_guards import MeasureError, require_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INFO = "https://api.hyperliquid.xyz/info"
OI_DB = os.path.join(ROOT, "backtests", "output", "oi_history.db")
CACHE = os.path.join(ROOT, "backtests", "output", "event_cache.db")

# ── LA DÉFINITION (énoncé de mission, non modifiable) ───────────────────
P_OI = 5             # ΔOI 1h ≤ percentile 5 du token
P_VOL = 95           # volume 1h ≥ percentile 95
P_WICK = 95          # amplitude de mèche ≥ percentile 95
DECLUSTER_H = 24     # absorption des événements < 24 h après un précédent
MAX_CANDLES = 5000   # plafond mesuré par requête
PAUSE = 0.15


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def _dt(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def post(payload, tries=3):
    for k in range(tries):
        try:
            r = urllib.request.Request(INFO, data=json.dumps(payload).encode(),
                                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.load(resp), None
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and k < tries - 1:
                time.sleep(2 ** k)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if k < tries - 1:
                time.sleep(1)
                continue
            return None, str(e)[:60]
    return None, "épuisé"


def db():
    con = sqlite3.connect(CACHE)
    con.execute("""CREATE TABLE IF NOT EXISTS c1h(
        symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,
        PRIMARY KEY(symbol, t))""")
    con.execute("""CREATE TABLE IF NOT EXISTS listing(
        symbol TEXT PRIMARY KEY, first_ms INTEGER, checked INTEGER)""")
    return con


def fetch_1h(con, sym, start_ms, end_ms):
    have = con.execute("SELECT COUNT(*), MAX(t) FROM c1h WHERE symbol=?",
                       (sym,)).fetchone()
    cur = (have[1] + 3_600_000) if have[1] else start_ms
    added = 0
    while cur < end_ms:
        stop = min(end_ms, cur + MAX_CANDLES * 3_600_000)
        r, err = post({"type": "candleSnapshot",
                       "req": {"coin": sym, "interval": "1h",
                               "startTime": cur, "endTime": stop}})
        time.sleep(PAUSE)
        if err or not isinstance(r, list) or not r:
            break
        rows = [(sym, int(x["t"]), float(x["o"]), float(x["h"]),
                 float(x["l"]), float(x["c"]), float(x["v"])) for x in r]
        con.executemany("INSERT OR IGNORE INTO c1h VALUES(?,?,?,?,?,?,?)", rows)
        con.commit()
        added += len(rows)
        nxt = max(x[1] for x in rows) + 3_600_000
        if nxt <= cur:
            break
        cur = nxt
    return added


def oi_hourly(sym):
    con = sqlite3.connect(OI_DB)
    rows = con.execute("SELECT ts, oi FROM asset_ctx WHERE symbol=? ORDER BY ts",
                       (sym,)).fetchall()
    con.close()
    return [(int(t) * 1000, float(o)) for t, o in rows if o is not None]


def pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(q / 100 * len(sorted_vals)))
    return sorted_vals[i]


def main() -> int:
    print(f"┌ événements phase 0 · git {git_rev()} · "
          f"run {datetime.now(timezone.utc).isoformat()[:16]}Z", flush=True)
    universe = sorted(set(P.trade_symbols))

    # ── 1. bornes de profondeur, mesurées ─────────────────────────────
    print("\n═══ 1. Profondeur servie par l'API, par intervalle ═══")
    now = int(time.time() * 1000)
    depth = {}
    for iv in ("1h", "4h", "1d"):
        lo, hi = 0, 1300
        while hi - lo > 2:
            mid = (lo + hi) // 2
            st = now - mid * 86400000
            r, _ = post({"type": "candleSnapshot",
                         "req": {"coin": "SOL", "interval": iv,
                                 "startTime": st, "endTime": st + 3 * 86400000}})
            time.sleep(0.2)
            if isinstance(r, list) and r:
                lo = mid
            else:
                hi = mid
        depth[iv] = lo
        print(f"  {iv:3s} : {lo:4d} jours  (remonte à {_d(now - lo * 86400000)})")

    # ── 2. OI en base ─────────────────────────────────────────────────
    con_oi = sqlite3.connect(OI_DB)
    n_oi, mn, mx, ns = con_oi.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT symbol) FROM asset_ctx"
    ).fetchone()
    con_oi.close()
    oi_start, oi_end = int(mn) * 1000, int(mx) * 1000
    print(f"\n═══ 2. OI horaire en base ═══")
    print(f"  {n_oi} lignes · {ns} symboles · {_d(oi_start)} → {_d(oi_end)}")
    if _d(oi_start) == _d(oi_end):
        raise MeasureError("colonne temporelle OI dégénérée — RUN NUL")

    # fenêtre calculable = intersection (1h servi) ∩ (OI en base)
    win_start = max(now - depth["1h"] * 86400000, oi_start)
    win_end = min(now, oi_end)
    win_days = (win_end - win_start) / 86400000
    print(f"\n  ⇒ fenêtre où les TROIS conditions sont calculables :")
    print(f"     {_d(win_start)} → {_d(win_end)}  =  {win_days:.0f} jours")
    if win_days < 30:
        raise MeasureError(f"fenêtre commune de {win_days:.0f} jours — "
                           f"définition non calculable, RUN NUL")

    # ── 3. collecte des bougies 1h ────────────────────────────────────
    print(f"\n═══ 3. Collecte des bougies 1 h ({len(universe)} tokens) ═══",
          flush=True)
    con = db()
    for i, s in enumerate(universe, 1):
        fetch_1h(con, s, win_start, win_end)
        if i % 10 == 0:
            print(f"  … {i}/{len(universe)}", flush=True)
    tot = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM c1h").fetchone()
    print(f"  {tot[0]} bougies 1 h · {tot[1]} tokens")

    # ── 4. détection des cascades ─────────────────────────────────────
    print(f"\n═══ 4. Cascades — ΔOI ≤ p{P_OI} ET volume ≥ p{P_VOL} "
          f"ET mèche ≥ p{P_WICK} ═══")
    print(f"  {'token':7s} {'heures':>7s} {'brut':>6s} {'dé-clust.':>10s} "
          f"{'ratio':>7s} {'1re':>12s} {'dernière':>12s}")
    per_token, all_events = {}, []
    for s in universe:
        rows = con.execute("SELECT t,o,h,l,c,v FROM c1h WHERE symbol=? "
                           "AND t>=? AND t<=? ORDER BY t",
                           (s, win_start, win_end)).fetchall()
        if len(rows) < 500:
            per_token[s] = {"emitted": False, "hours": len(rows)}
            continue
        oi = oi_hourly(s)
        oi_map = {t: v for t, v in oi}
        # ΔOI en unités ABSOLUES — aucun dénominateur (garde-fou)
        d_oi, vol, wick, ts = [], [], [], []
        prev = None
        for t, o, h, l, c, v in rows:
            if c <= 0:
                continue
            cur_oi = oi_map.get(t)
            if cur_oi is None or prev is None:
                prev = cur_oi if cur_oi is not None else prev
                continue
            d_oi.append(cur_oi - prev)
            vol.append(v)
            wick.append((h - l) / c)
            ts.append(t)
            prev = cur_oi
        if len(ts) < 500:
            per_token[s] = {"emitted": False, "hours": len(ts)}
            continue
        require_series(f"ΔOI {s}", d_oi, min_n=500)
        require_series(f"volume {s}", vol, min_n=500)
        th_oi = pctile(sorted(d_oi), P_OI)
        th_vol = pctile(sorted(vol), P_VOL)
        th_wick = pctile(sorted(wick), P_WICK)
        raw = [ts[i] for i in range(len(ts))
               if d_oi[i] <= th_oi and vol[i] >= th_vol and wick[i] >= th_wick]
        # dé-clustering : < 24 h après un événement retenu ⇒ absorbé
        kept = []
        for t in raw:
            if not kept or (t - kept[-1]) >= DECLUSTER_H * 3_600_000:
                kept.append(t)
        per_token[s] = {"emitted": True, "hours": len(ts), "raw": len(raw),
                        "declustered": len(kept),
                        "th_oi": round(th_oi, 2), "th_vol": round(th_vol, 2),
                        "th_wick": round(th_wick, 5),
                        "first": _dt(kept[0]) if kept else None,
                        "last": _dt(kept[-1]) if kept else None,
                        "events": kept}
        all_events += [(t, s) for t in kept]
        r = per_token[s]
        print(f"  {s:7s} {r['hours']:>7d} {r['raw']:>6d} {r['declustered']:>10d} "
              f"{r['raw']/max(r['declustered'],1):>7.2f} "
              f"{(r['first'] or '—')[:10]:>12s} {(r['last'] or '—')[:10]:>12s}")

    ok = [v for v in per_token.values() if v["emitted"]]
    n_raw = sum(v["raw"] for v in ok)
    n_dec = sum(v["declustered"] for v in ok)
    print(f"\n  TOTAL : {n_raw} événements bruts → {n_dec} après dé-clustering "
          f"(facteur {n_raw/max(n_dec,1):.2f})")
    print(f"  tokens émettant : {len(ok)}/{len(universe)}")

    # chevauchement inter-tokens : événements simultanés à l'heure près
    by_hour = defaultdict(list)
    for t, s in all_events:
        by_hour[t].append(s)
    multi = {h: v for h, v in by_hour.items() if len(v) > 1}
    n_agg = len(by_hour)
    print(f"  heures distinctes : {n_agg} · heures multi-tokens : {len(multi)} "
          f"({len(multi)/max(n_agg,1)*100:.1f} %)")
    if multi:
        top = sorted(multi.items(), key=lambda x: -len(x[1]))[:3]
        for h, v in top:
            print(f"    {_dt(h)} : {len(v)} tokens ({' '.join(sorted(v)[:8])})")

    # ── 5. listings ───────────────────────────────────────────────────
    print(f"\n═══ 5. Listings de perps HL ═══", flush=True)
    meta, err = post({"type": "meta"})
    if err:
        raise MeasureError(f"meta injoignable : {err}")
    perps = [u["name"] for u in meta["universe"]]
    print(f"  {len(perps)} perps listés aujourd'hui")
    have = {r[0]: r[1] for r in con.execute("SELECT symbol, first_ms FROM listing")}
    todo = [p for p in perps if p not in have]
    print(f"  {len(have)} en cache, {len(todo)} à dater (1re bougie 1 j)",
          flush=True)
    for i, p in enumerate(todo, 1):
        r, e = post({"type": "candleSnapshot",
                     "req": {"coin": p, "interval": "1d",
                             "startTime": now - 1300 * 86400000, "endTime": now}})
        time.sleep(PAUSE)
        first = int(r[0]["t"]) if (isinstance(r, list) and r) else None
        con.execute("INSERT OR REPLACE INTO listing VALUES(?,?,?)",
                    (p, first, int(time.time())))
        if i % 50 == 0:
            con.commit()
            print(f"    … {i}/{len(todo)}", flush=True)
    con.commit()
    firsts = {r[0]: r[1] for r in con.execute(
        "SELECT symbol, first_ms FROM listing WHERE first_ms IS NOT NULL")}
    if firsts and len(set(_d(v) for v in firsts.values())) == 1:
        raise MeasureError("dates de listing dégénérées (toutes identiques) "
                           "— RUN NUL")
    cutoff_28m = now - int(28 * 30.44 * 86400000)
    recent = {k: v for k, v in firsts.items() if v >= cutoff_28m}
    # la borne de l'API 1 j : un listing « daté » à cette borne est en fait
    # antérieur — on le signale plutôt que de le compter comme un listing.
    api_floor = now - depth["1d"] * 86400000
    at_floor = sum(1 for v in firsts.values() if v <= api_floor + 3 * 86400000)
    print(f"  datés : {len(firsts)}/{len(perps)}")
    print(f"  listés dans les 28 derniers mois : **{len(recent)}**")
    print(f"  dont butant sur la borne de l'API 1 j ({_d(api_floor)}) : "
          f"{at_floor} — antériorité réelle inconnue")
    in_universe = sorted(k for k in recent if k in set(universe))
    print(f"  parmi Params.trade_symbols : {len(in_universe)} → {in_universe}")

    res = {"depth_days": depth, "max_candles_per_req": MAX_CANDLES,
           "oi": {"rows": n_oi, "symbols": ns, "start": _d(oi_start),
                  "end": _d(oi_end)},
           "window": {"start": _d(win_start), "end": _d(win_end),
                      "days": round(win_days, 1)},
           "cascades": {"per_token": {k: {kk: vv for kk, vv in v.items()
                                          if kk != "events"}
                                      for k, v in per_token.items()},
                        "n_raw": n_raw, "n_declustered": n_dec,
                        "tokens_emitting": len(ok),
                        "distinct_hours": n_agg, "multi_token_hours": len(multi)},
           "listings": {"perps_now": len(perps), "dated": len(firsts),
                        "last_28m": len(recent), "at_api_floor": at_floor,
                        "in_universe": in_universe,
                        "dates": {k: _d(v) for k, v in sorted(
                            recent.items(), key=lambda x: -x[1])[:40]}},
           "fingerprint": {"git_rev": git_rev(),
                           "run_at": datetime.now(timezone.utc).isoformat()[:16]}}
    out = os.path.join(ROOT, "analysis", "output", "event_phase0.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"\nDump : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
