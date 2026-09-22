"""JEV (TypeSafe AI « System One ») — juge typé, SHADOW uniquement (v1.29.0).

JEV ne rédige pas : il reçoit un état texte et des questions typées, et rend
des probabilités (choice / noul). On lui soumet EXACTEMENT les populations que
jugent déjà les arbitres Live (candidats d'entrée après préflight, positions
examinées par l'arbitre de sortie), pour une comparaison à périmètre égal.

Doctrine : aucun pouvoir. Ce module n'a pas de mode « act » — `config()`
ne connaît que off|shadow. Les réponses sont journalisées (JEV_ENTRY_SHADOW,
JEV_EXIT_SHADOW) et scorées en contrefactuel par `scorecard()` contre l'issue
réelle des trades. Promotion éventuelle = décision humaine, n ≥ 50, preuve.
Fail-safe : toute erreur ou timeout ⇒ événement JEV_FAILOPEN, rien d'autre.

API : POST https://api.typesafe.ai/v1/systemone, Bearer TYPESAFE_API_KEY.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jev")

ENTRY_QUESTIONS = {
    "decision": {"type": "choice",
                 "instructions": "Ce candidat du moteur doit-il être pris maintenant ?",
                 "criteria": {"GO": "prendre le trade tel que proposé",
                              "HOLD": "ne pas le prendre maintenant"}},
    "win": {"type": "noul",
            "instructions": "Ce trade, s'il est pris, finira-t-il avec un P&L net "
                            "positif après frais, avec les sorties du moteur ?"},
}
EXIT_QUESTIONS = {
    "action": {"type": "choice",
               "instructions": "Pour cette position ouverte, faut-il la garder "
                               "jusqu'aux sorties du moteur ou la couper maintenant ?",
               "criteria": {"HOLD": "garder, laisser le moteur gérer la sortie",
                            "CUT": "couper la position maintenant"}},
    "better_exit_now": {"type": "noul",
                        "instructions": "Sortir maintenant donnerait-il un P&L net "
                                        "meilleur que la sortie finale du moteur ?"},
}
QUESTIONS_HASH = hashlib.sha256(json.dumps(
    [ENTRY_QUESTIONS, EXIT_QUESTIONS], sort_keys=True).encode()).hexdigest()[:10]


def config() -> dict:
    """off|shadow seulement. Sans clé ⇒ off. Aucun mode d'action n'existe."""
    env = os.environ.get
    mode = env("AI_JEV_MODE", "off").strip().lower()
    if mode != "shadow" or not env("TYPESAFE_API_KEY"):
        mode = "off"
    return {"mode": mode,
            "model": env("AI_JEV_MODEL", DEFAULT_MODEL),
            "timeout": float(env("AI_JEV_TIMEOUT", "15")),
            "max_items": int(env("AI_JEV_MAX_ITEMS", "12"))}


def _post(payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        URL, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": "alfred"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _state(phase: str, item: dict, market: dict, facts: list) -> str:
    from ai_doctrine import DOCTRINE_DIGEST
    return json.dumps({
        "role": ("candidat d'entrée proposé par le moteur" if phase == "entry"
                 else "position ouverte gérée par le moteur"),
        "item": item, "market": market,
        "external_facts": facts,
        "engine_doctrine": DOCTRINE_DIGEST,
    }, ensure_ascii=False, default=str)


def _one(phase: str, item: dict, market: dict, facts: list,
         model: str, timeout: float) -> dict:
    questions = ENTRY_QUESTIONS if phase == "entry" else EXIT_QUESTIONS
    out = _post({"model": model, "state": _state(phase, item, market, facts),
                 "questions": questions}, timeout)
    ans = out.get("answers") or {}
    missing = [q for q in questions if q not in ans]
    if missing:
        raise ValueError(f"réponse incomplète: {missing}")
    v = {"model": out.get("model"), "usage": out.get("usage") or {}}
    for q, a in ans.items():
        if a.get("type") == "choice":
            v[q] = a.get("choice")
            v[q + "_probs"] = a.get("probabilities")
            v[q + "_confidence"] = a.get("confidence")
        elif a.get("type") == "noul":
            v[q] = a.get("noul")
    return v


def judge(phase: str, items: list[dict], market: dict, *, model: str,
          timeout: float, max_items: int) -> dict:
    """Un appel par item, en parallèle, borné par `timeout` au total.
    Ne lève jamais : {"verdicts": {sym: v}, "errors": {sym|'*': str}}."""
    verdicts, errors = {}, {}
    items = items[:max_items]
    if not items:
        return {"verdicts": verdicts, "errors": errors}
    try:
        import ai_external_context as external
        ctx = external.context_for([i["symbol"] for i in items])
        all_facts = ctx.get("facts") or []
    except Exception as e:
        all_facts = []
        errors["*external"] = f"{type(e).__name__}:{str(e)[:120]}"
    futs = {}
    for it in items:
        facts = [f for f in all_facts if f.get("symbol") in (it["symbol"], "MACRO")]
        clean = {k: v for k, v in it.items() if k != "prior_decision"}
        futs[_EXECUTOR.submit(_one, phase, clean, market, facts,
                              model, timeout)] = it["symbol"]
    done, _ = wait(futs, timeout=timeout + 1)
    for f, sym in futs.items():
        if f not in done:
            f.cancel()
            errors[sym] = f"timeout>{timeout}s"
            continue
        try:
            verdicts[sym] = f.result()
        except Exception as e:
            errors[sym] = f"{type(e).__name__}:{str(e)[:200]}"
    return {"verdicts": verdicts, "errors": errors}


# ── Scorecard contrefactuel (lecture seule, Live) ───────────────────────


def _ts(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except Exception:
        return None


def scorecard(conn) -> dict:
    """Confronte les verdicts JEV aux trades réels de Live.

    Entrée : candidat rapproché du trade ouvert au même scan (même symbole,
    stratégie, sens, ±15 min). HOLD « agi » = trade évité ⇒ Δ = −P&L réel.
    Sortie : première réponse CUT par position ⇒ Δ ≈ P&L net au moment du
    verdict − P&L final (sans glissement ; approximation déclarée).
    Brier : p(win) / p(better_exit_now) contre l'issue, vs taux de base.
    """
    trades = conn.execute(
        "SELECT symbol, strategy, direction, entry_time, exit_time, pnl_usdt "
        "FROM trades").fetchall()
    tr = [{"symbol": t[0], "strategy": t[1], "dir": t[2], "entry": _ts(t[3]),
           "closed": t[4] is not None, "pnl": t[5]} for t in trades]
    def rows(event):
        out = []
        for ts, sym, data in conn.execute(
                "SELECT ts, symbol, data FROM events WHERE event=? ORDER BY ts",
                (event,)).fetchall():
            try:
                out.append((ts, sym, json.loads(data or "{}")))
            except Exception:
                continue
        return out

    def brier(pairs):
        if not pairs:
            return None, None
        base = sum(y for _, y in pairs) / len(pairs)
        b = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
        b0 = sum((base - y) ** 2 for _, y in pairs) / len(pairs)
        return round(b, 4), round(b0, 4)

    # Entrées
    ent = {"n": 0, "not_entered": 0, "pending": 0, "resolved": 0,
           "GO": {"n": 0, "wins": 0, "pnl": 0.0},
           "HOLD": {"n": 0, "wins": 0, "pnl": 0.0}}
    pairs = []
    for ts, sym, d in rows("JEV_ENTRY_SHADOW"):
        ent["n"] += 1
        m = [t for t in tr if t["symbol"] == sym and t["strategy"] == d.get("strategy")
             and t["dir"] == d.get("dir") and t["entry"]
             and abs(t["entry"] - ts) <= 900]
        if not m:
            ent["not_entered"] += 1
            continue
        t = m[0]
        if not t["closed"]:
            ent["pending"] += 1
            continue
        ent["resolved"] += 1
        k = d.get("decision") if d.get("decision") in ("GO", "HOLD") else None
        win = 1 if (t["pnl"] or 0) > 0 else 0
        if k:
            ent[k]["n"] += 1
            ent[k]["wins"] += win
            ent[k]["pnl"] = round(ent[k]["pnl"] + (t["pnl"] or 0), 2)
        if isinstance(d.get("win"), (int, float)):
            pairs.append((float(d["win"]), win))
    ent["delta_if_hold_vetoed"] = round(0.0 - ent["HOLD"]["pnl"], 2)
    ent["brier"], ent["brier_base"] = brier(pairs)

    # Sorties
    ex = {"n": 0, "positions": 0, "pending": 0, "resolved": 0,
          "cut_positions": 0, "delta_if_cut": 0.0}
    first, pairs = {}, []
    for ts, sym, d in rows("JEV_EXIT_SHADOW"):
        ex["n"] += 1
        key = (sym, d.get("entry_ts_ms"))
        first.setdefault(key, {"cut": None, "judgments": []})
        first[key]["judgments"].append(d)
        if d.get("action") == "CUT" and first[key]["cut"] is None:
            first[key]["cut"] = d
    ex["positions"] = len(first)
    for (sym, ems), g in first.items():
        m = [t for t in tr if t["symbol"] == sym and t["entry"] and ems
             and abs(t["entry"] * 1000 - ems) <= 5000]
        if not m or not m[0]["closed"]:
            ex["pending"] += 1
            continue
        final = m[0]["pnl"] or 0.0
        ex["resolved"] += 1
        for d in g["judgments"]:
            if isinstance(d.get("better_exit_now"), (int, float)) and d.get("net_pnl") is not None:
                pairs.append((float(d["better_exit_now"]),
                              1 if d["net_pnl"] > final else 0))
        if g["cut"] is not None and g["cut"].get("net_pnl") is not None:
            ex["cut_positions"] += 1
            ex["delta_if_cut"] = round(ex["delta_if_cut"]
                                       + g["cut"]["net_pnl"] - final, 2)
    ex["brier"], ex["brier_base"] = brier(pairs)
    fails = conn.execute("SELECT COUNT(*) FROM events WHERE event='JEV_FAILOPEN'"
                         ).fetchone()[0]
    return {"entry": ent, "exit": ex, "failopen": fails,
            "questions_hash": QUESTIONS_HASH}


if __name__ == "__main__":
    import sqlite3
    root = os.path.dirname(os.path.abspath(__file__))
    con = sqlite3.connect(f"file:{root}/alfred/data/bots/live/bot.db?mode=ro", uri=True)
    print(json.dumps(scorecard(con), indent=2, ensure_ascii=False))
