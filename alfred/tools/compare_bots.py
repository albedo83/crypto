"""Audit de divergence entre DEUX BOTS ALFRED (par défaut live vs paper).

DOCTRINE : entre deux bots Alfred, la divergence est ATTENDUE — ils n'ont ni
le même capital, ni les mêmes positions ouvertes, donc ni les mêmes slots
libres. Une seule entrée divergente rebat l'occupation des slots pour des
semaines. L'objectif de cet outil n'est donc PAS de constater l'écart (le
solde le fait déjà) mais de l'ATTRIBUER : chaque trade pris d'un seul côté
doit s'expliquer par l'état propre du bot, sinon c'est un bug.

C'est la réponse au piège documenté dans `docs/bilan_2026_09.md` § 9 : ne
jamais diagnostiquer par différence de soldes, seulement par contrefactuel
apparié.

AUTO-CLASSIFICATION :
  STATE   — état propre du bot (cooldowns, positions, slots, capital, frein
            equity, marge). ATTENDU entre deux bots de capitaux différents.
  DATA    — historique de données propre au bot (oi_gate, disp_gate).
  PREBOOT — l'autre bot n'existait pas encore. Ignoré.
  CASCADE — CLOSE dont l'OPEN était divergent. Conséquence, pas cause.
  MANUAL  — sortie décidée à la main sur le live. Attendue : le paper n'a
            pas d'opérateur humain.
  ISO     — entrée commune avec stop/hold/mult/btc_z identiques. La preuve
            positive que le noyau de règles est le même des deux côtés.
  LOGIC   — divergence inexpliquée par ce qui précède → À AUDITER.
            Exit code 1 = attribution incomplète, pas nécessairement bug.

La TAILLE de position n'est pas un invariant : le cap vaut 0,3 × equity et
les equity ont divergé. Elle est donc comparée au RATIO DES SOLDES des deux
bots — seul un écart à ce ratio révèle un vrai défaut de sizing.

Usage:
    python3 -m alfred.tools.compare_bots [--a live] [--b paper] [--hours 168]

Lit, pour chaque bot : alfred/data/bots/<id>/bot.db (events + trades)
                       alfred/data/bots/<id>/state.json (capital de base)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Une décision appartient à la période 4h de son horodatage ; les entrées sont
# calées sur les closes 4h des deux côtés, donc l'appariement par période est exact.
PERIOD = 14400

# SKIP expliqués par l'état PROPRE du bot (positions/cooldowns/slots/capital).
# Tous attendus entre deux bots de capitaux et de positions différents.
STATE_REASONS = {
    "cooldown", "cooldown_near_boundary", "already_in_position",
    "max_long", "max_short", "max_direction", "max_macro", "max_token",
    "max_positions", "max_pos", "max_sector", "max_dca",
    "paused", "paused_strategy", "modulator_floor",
    "insufficient_margin", "equity_brake",
}
# Raisons de SATURATION : quand elles tombent, la boucle de candidats
# s'interrompt — les symboles suivants ne reçoivent AUCUN SKIP. L'absence de
# trace sur un symbole donné est alors expliquée par l'état, pas inexpliquée.
SATURATION_REASONS = {
    "max_positions", "max_pos", "max_macro", "max_long", "max_short",
    "max_direction", "max_items", "insufficient_margin", "equity_brake",
}
# SKIP dérivés de l'historique de données accumulé par chaque bot.
DATA_REASONS = {"oi_gate", "oi_gate_no_data", "disp_gate"}

# Sorties déclenchées par la main humaine : live-only par construction (le
# paper n'a pas d'opérateur). Une divergence sur ces raisons n'est pas un bug.
# Préfixe plutôt qu'énumération : le code émet manual_stop, manual_stop_set,
# manual_stop_price, manual_stop_rule, manual_stop_usdt, manual_close — une
# liste figée se désynchroniserait en silence.
MANUAL_PREFIX = "manual"

# Écart relatif toléré entre le ratio des tailles et le ratio des soldes.
SIZE_RATIO_TOL = 0.10


def bot_db(bot_id: str) -> str:
    return os.path.join(_REPO, "alfred", "data", "bots", bot_id, "bot.db")


def bot_capital(bot_id: str) -> float:
    """Capital de base du bot (state.json) — socle du calcul de solde."""
    path = os.path.join(_REPO, "alfred", "data", "bots", bot_id, "state.json")
    try:
        with open(path) as fh:
            d = json.load(fh)
        return float(d.get("capital") or d.get("_capital") or 0.0)
    except (OSError, ValueError, TypeError):
        return 0.0


def load_events(path: str, since_ts: int, kinds: tuple) -> list[dict]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    qmarks = ",".join("?" for _ in kinds)
    rows = db.execute(
        f"SELECT ts, event, symbol, data FROM events "
        f"WHERE ts >= ? AND event IN ({qmarks}) ORDER BY ts",
        (since_ts, *kinds),
    ).fetchall()
    out = []
    for ts, event, symbol, data in rows:
        d = json.loads(data or "{}")
        out.append({"ts": ts, "event": event, "symbol": symbol,
                    "strategy": d.get("strategy"), "dir": d.get("dir"),
                    "reason": d.get("reason"), "period": ts // PERIOD,
                    "size_usdt": d.get("size_usdt"),
                    "entry_price": d.get("entry_price"),
                    "stop_bps": d.get("stop_bps"),
                    "mult": d.get("mult"),
                    "btc_z": d.get("btc_z"),
                    "target_exit": d.get("target_exit")})
    return out


def first_event_ts(path: str) -> int:
    """Premier event de la DB — proxy de l'existence du bot."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    row = db.execute("SELECT MIN(ts) FROM events").fetchone()
    return int(row[0]) if row and row[0] else 0


def balance_at(path: str, capital: float, ts: int) -> float:
    """Solde du bot à l'instant ts = capital + réalisé des trades déjà clos.

    Même définition que la « Balance » du dashboard (réalisé seul), qui est
    l'assiette du cap de sizing.
    """
    iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    row = db.execute("SELECT COALESCE(SUM(pnl_usdt), 0) FROM trades "
                     "WHERE exit_time < ?", (iso,)).fetchone()
    return capital + float(row[0] or 0.0)


def key_open(e: dict) -> tuple:
    return (e["period"], e["symbol"], e["strategy"], e["dir"])


def classify_only(k: tuple, other_skips: list[dict], other_first_ts: int) -> tuple[str, str]:
    """Classe un OPEN présent d'un seul côté. Renvoie (catégorie, détail)."""
    period, symbol, strategy, dirn = k
    period_start = period * PERIOD
    if period_start + PERIOD <= other_first_ts:
        return "PREBOOT", "l'autre bot n'existait pas encore"
    cands = [s for s in other_skips
             if s["period"] == period and s["symbol"] == symbol]
    for s in cands:
        r = s["reason"] or ""
        if r in STATE_REASONS:
            return "STATE", f"l'autre côté a skippé : {r}"
        if r in DATA_REASONS:
            return "DATA", f"l'autre côté a skippé : {r}"
    if cands:
        return "LOGIC", f"l'autre côté a skippé pour une raison hors état : {cands[0]['reason']}"
    sat = [s["reason"] for s in other_skips
           if s["period"] == period and (s["reason"] or "") in SATURATION_REASONS]
    if sat:
        return "STATE", (f"l'autre côté était saturé sur la période ({sat[0]}) — "
                         f"boucle de candidats interrompue avant ce symbole")
    return "LOGIC", "aucun SKIP de l'autre côté — signal même pas détecté"


def _hold_hours(e: dict) -> float | None:
    try:
        return (datetime.fromisoformat(e["target_exit"]).timestamp() - e["ts"]) / 3600
    except (TypeError, ValueError, KeyError):
        return None


def main() -> int:
    hours = 168
    a_id, b_id = "live", "paper"
    if "--hours" in sys.argv:
        hours = int(sys.argv[sys.argv.index("--hours") + 1])
    if "--a" in sys.argv:
        a_id = sys.argv[sys.argv.index("--a") + 1]
    if "--b" in sys.argv:
        b_id = sys.argv[sys.argv.index("--b") + 1]
    since = int(time.time()) - hours * 3600

    a_db, b_db = bot_db(a_id), bot_db(b_id)
    for bid, path in ((a_id, a_db), (b_id, b_db)):
        if not os.path.exists(path):
            print(f"DB introuvable pour le bot '{bid}': {path}")
            return 2
    a_cap, b_cap = bot_capital(a_id), bot_capital(b_id)

    a = load_events(a_db, since, ("OPEN", "CLOSE"))
    b = load_events(b_db, since, ("OPEN", "CLOSE"))
    a_skips = load_events(a_db, since, ("SKIP",))
    b_skips = load_events(b_db, since, ("SKIP",))
    a_boot, b_boot = first_event_ts(a_db), first_event_ts(b_db)

    print(f"Fenêtre {hours}h — {a_id}: {len(a)} OPEN/CLOSE, {len(a_skips)} SKIP "
          f"| {b_id}: {len(b)} OPEN/CLOSE, {len(b_skips)} SKIP")
    print(f"Capital de base — {a_id}: ${a_cap:.2f} | {b_id}: ${b_cap:.2f}")

    ao = {key_open(e) for e in a if e["event"] == "OPEN"}
    bo = {key_open(e) for e in b if e["event"] == "OPEN"}
    common = ao & bo
    n_logic = 0
    counts = {"STATE": 0, "DATA": 0, "PREBOOT": 0, "LOGIC": 0}
    divergent_opens: set[tuple] = set()

    print(f"\nENTRÉES — communes: {len(common)} | {a_id}-seulement: {len(ao - bo)} "
          f"| {b_id}-seulement: {len(bo - ao)}")
    for k in sorted(ao - bo):
        cat, detail = classify_only(k, b_skips, b_boot)
        counts[cat] += 1
        divergent_opens.add((k[1], k[2]))
        mark = "✗" if cat == "LOGIC" else "·"
        print(f"  {mark} [{cat:7}] {a_id} seul: {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]} — {detail}")
    for k in sorted(bo - ao):
        cat, detail = classify_only(k, a_skips, a_boot)
        counts[cat] += 1
        divergent_opens.add((k[1], k[2]))
        mark = "✗" if cat == "LOGIC" else "·"
        print(f"  {mark} [{cat:7}] {b_id} seul: {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
              f"{k[1]} {k[2]} {k[3]} — {detail}")
    n_logic += counts["LOGIC"]

    # Entrées communes : comparer les invariants INDÉPENDANTS DE L'EQUITY
    # (stop en bps, hold, multiplicateur, btc_z). La taille est comparée au
    # ratio des soldes — elle DOIT diverger, pas dans n'importe quelle proportion.
    n_param_diff = 0
    if common:
        a_by_key = {key_open(e): e for e in a if e["event"] == "OPEN"}
        b_by_key = {key_open(e): e for e in b if e["event"] == "OPEN"}
        for k in sorted(common):
            ae, be = a_by_key[k], b_by_key[k]
            diffs = []
            if ae.get("stop_bps") is not None and be.get("stop_bps") is not None:
                if abs(ae["stop_bps"] - be["stop_bps"]) > 0.1:
                    diffs.append(f"stop {ae['stop_bps']} vs {be['stop_bps']}")
            ah, bh = _hold_hours(ae), _hold_hours(be)
            if ah is not None and bh is not None and abs(ah - bh) > 0.17:
                diffs.append(f"hold {ah:.1f}h vs {bh:.1f}h")
            if (ae.get("mult") is None) != (be.get("mult") is None):
                diffs.append(f"mult {ae.get('mult')} vs {be.get('mult')}")
            elif ae.get("mult") is not None and abs(ae["mult"] - be["mult"]) > 0.01:
                diffs.append(f"mult {ae['mult']} vs {be['mult']}")
            if ae.get("btc_z") is not None and be.get("btc_z") is not None:
                if abs(ae["btc_z"] - be["btc_z"]) > 0.02:
                    diffs.append(f"btc_z {ae['btc_z']} vs {be['btc_z']}")
            # Taille : attendue proportionnelle au solde de chaque bot.
            size_note = ""
            if ae.get("size_usdt") and be.get("size_usdt"):
                bal_a = balance_at(a_db, a_cap, ae["ts"])
                bal_b = balance_at(b_db, b_cap, be["ts"])
                actual = ae["size_usdt"] / be["size_usdt"]
                if bal_b > 0 and bal_a > 0:
                    expected = bal_a / bal_b
                    dev = actual / expected - 1
                    size_note = (f" | taille ×{actual:.2f} pour un ratio de soldes "
                                 f"×{expected:.2f} ({dev:+.0%})")
                    if abs(dev) > SIZE_RATIO_TOL:
                        diffs.append(f"sizing hors ratio de soldes ({dev:+.0%})")
            if diffs:
                n_param_diff += 1
                print(f"  ✗ [LOGIC  ] commune {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
                      f"{k[1]} {k[2]} {k[3]} — "
                      f"{'; '.join(diffs)}{size_note}")
            else:
                print(f"  ✓ [ISO    ] commune {time.strftime('%m-%d %Hh', time.gmtime(k[0]*PERIOD))} "
                      f"{k[1]} {k[2]} {k[3]} — stop/hold/mult/btc_z identiques{size_note}")
    n_logic += n_param_diff

    # Sorties : appariement par (symbole, stratégie) et comparaison des raisons.
    ac = [e for e in a if e["event"] == "CLOSE"]
    bc = [e for e in b if e["event"] == "CLOSE"]
    print(f"\nSORTIES — {a_id}: {len(ac)} | {b_id}: {len(bc)}")
    b_by_sym: dict[str, list] = {}
    for e in bc:
        b_by_sym.setdefault(e["symbol"], []).append(e)
    # Une position ouverte AVANT la fenêtre n'est pas attribuable : son OPEN
    # n'a pas été chargé, donc l'absence de pendant ne prouve rien.
    a_opened_in_window = {(e["symbol"], e["strategy"])
                          for e in a if e["event"] == "OPEN"}
    n_match = n_reason_diff = n_cascade = n_unmatched = n_outwin = n_manual = 0
    for e in ac:
        cands = [x for x in b_by_sym.get(e["symbol"], [])
                 if x["strategy"] == e["strategy"]
                 and abs(x["ts"] - e["ts"]) <= 2 * 3600]
        if not cands:
            if (e["reason"] or "").startswith(MANUAL_PREFIX):
                n_manual += 1
                print(f"  · [MANUAL ] close {a_id} sans pendant {b_id}: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))} "
                      f"(intervention humaine — pas de pendant possible)")
            elif (e["symbol"], e["strategy"]) in divergent_opens or e["ts"] < b_boot:
                n_cascade += 1
                print(f"  · [CASCADE] close {a_id} sans pendant {b_id}: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))} "
                      f"(OPEN divergent ou antérieur)")
            elif (e["symbol"], e["strategy"]) not in a_opened_in_window:
                n_outwin += 1
                print(f"  · [HORS-FEN] close {a_id} sans pendant {b_id}: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))} "
                      f"(entrée antérieure à la fenêtre — non attribuable)")
            else:
                n_unmatched += 1
                print(f"  ✗ [LOGIC  ] close {a_id} sans pendant {b_id}: {e['symbol']} "
                      f"{e['strategy']} {e['reason']} @ "
                      f"{time.strftime('%m-%d %H:%M', time.gmtime(e['ts']))}")
            continue
        best = min(cands, key=lambda x: abs(x["ts"] - e["ts"]))
        if best["reason"] == e["reason"]:
            n_match += 1
        elif ((e["reason"] or "").startswith(MANUAL_PREFIX)
              or (best["reason"] or "").startswith(MANUAL_PREFIX)):
            n_manual += 1
            print(f"  · [MANUAL ] {e['symbol']} {e['strategy']}: {a_id}={e['reason']} "
                  f"vs {b_id}={best['reason']} (Δt={best['ts'] - e['ts']:+d}s) — "
                  f"intervention humaine")
        else:
            n_reason_diff += 1
            print(f"  ✗ [LOGIC  ] raison divergente {e['symbol']} {e['strategy']}: "
                  f"{a_id}={e['reason']} vs {b_id}={best['reason']} "
                  f"(Δt={best['ts'] - e['ts']:+d}s)")
    n_logic += n_reason_diff + n_unmatched

    print(f"\n  sorties: appariées={n_match} | raisons divergentes={n_reason_diff} "
          f"| cascade={n_cascade} | non appariées (logic)={n_unmatched}")
    print(f"  entrées divergentes: STATE={counts['STATE']} DATA={counts['DATA']} "
          f"PREBOOT={counts['PREBOOT']} LOGIC={counts['LOGIC']}")

    clean = n_logic == 0
    if clean:
        print(f"\nVerdict: ✓ écart {a_id}/{b_id} entièrement ATTRIBUÉ "
              f"(état, données ou cascade) — aucun défaut de noyau")
    else:
        print(f"\nVerdict: ⚠ {n_logic} divergence(s) non attribuée(s) — auditer "
              f"contre les règles et les données de chaque bot avant d'en tirer "
              f"une conclusion de performance")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
