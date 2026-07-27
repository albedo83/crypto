#!/usr/bin/env python3
"""Auditeur SYSTÈME — l'IA cherche les anomalies du BOT, pas des trades.

Rôle distinct de tous les autres outils IA : il ne juge AUCUNE position et ne
peut RIEN déclencher côté trading. Il lit les données de cohérence du système
(live vs paper vs backtest, distribution des sorties, comptabilité) et répond à
une seule question : **qu'est-ce qui est structurellement anormal ici ?**

Pourquoi ce rôle vaut le coup (2026-07-25) : le biais de booking des trails
valait ~50 % du P&L mesuré, a mis 3 semaines à émerger, et s'est révélé en
comparant deux bases ligne à ligne — une tâche de reconnaissance de motif. Sa
signature était visible dans les données : « même trade, même règle de sortie,
P&L opposés, prix de sortie à 4.8 % d'écart ».

L'asymétrie justifie l'outil sans échantillon : une fausse alerte coûte 5 min de
lecture, une vraie évite des semaines à courir après un mirage. C'est le seul
rôle IA où l'erreur est bon marché et le gain énorme.

Usage :
    ./ai_system_audit.py --dry-run     # contexte + prompt, aucun appel API
    ./ai_system_audit.py --no-telegram # vrai appel, sortie console
    ./ai_system_audit.py               # + Telegram + event AI_SYSTEM_AUDIT
"""
from __future__ import annotations

import argparse
import hashlib as _hl
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_doctrine import DOCTRINE_DIGEST  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BOTS = {b: os.path.join(REPO_ROOT, "alfred", "data", "bots", b, "bot.db")
        for b in ("live", "paper")}
DEFAULT_MODEL = "claude-opus-4-8"
WINDOW_DAYS = 14          # profondeur d'analyse
MATCH_SLACK = 4 * 3600    # appariement live/paper sur l'heure d'entrée (s)

SYSTEM_PROMPT = """\
Tu es l'AUDITEUR SYSTÈME d'un bot de trading. Tu ne juges AUCUNE position et tu
ne déclenches AUCUN trade. Tu cherches les défauts du SYSTÈME lui-même : bugs,
biais de mesure, dérives de configuration, incohérences comptables.

CE QUE TU CHERCHES (par ordre de gravité) :
1. BIAIS DE MESURE — le bot se ment à lui-même. Signature typique : deux
   instances qui exécutent le MÊME code sur le MÊME trade (même symbole, même
   heure d'entrée) et obtiennent des résultats incohérents, surtout avec la
   MÊME raison de sortie. Un écart de prix de sortie important sur un trade
   apparié est le signal le plus fort qui existe : l'un des deux booke un prix
   qui n'était pas disponible.
2. BUG / RÉGRESSION — une règle qui ne se déclenche plus, une raison de sortie
   qui disparaît ou explose d'un coup, une comptabilité qui ne réconcilie pas.
3. DÉRIVE — la distribution des sorties, des tailles ou des causes de skip
   change de façon marquée sans changement de configuration connu.

CE QUE TU NE FAIS PAS :
- juger si un trade était bon (ce n'est pas ton rôle, d'autres outils le font) ;
- commenter la performance ou le régime de marché : perdre de l'argent n'est PAS
  une anomalie système, c'est du trading. Un book qui perd avec des données
  cohérentes = RAS ;
- suggérer des changements de stratégie ou de paramètres de trading.

DIFFÉRENCES STRUCTURELLES CONNUES — ne les signale PAS comme anomalies :
- SENIOR est piloté par un admin qui pose des stops manuels : une sortie
  `manual_stop_set` côté SENIOR face à un `timeout` côté PAPER est ATTENDUE et
  normale, quelle que soit sa fréquence. PAPER n'a aucune intervention humaine.
- SENIOR passe par l'arbitre IA (tailles réduites) et par la marge réelle de
  l'exchange ; PAPER non. Les tailles diffèrent par construction.
- SENIOR exécute au prix réel du marché ; PAPER simule.
- La fenêtre d'analyse peut CHEVAUCHER un changement de configuration : un trade
  clos AVANT une modification porte encore l'ancien comportement. Compare
  toujours la date du trade à la date du changement avant de conclure qu'une
  règle retirée « fonctionne encore ». La date du jour est dans le contexte.

CODE EN VIGUEUR :
`runtime_depuis` est l'heure de mise en service du code qui tourne actuellement.
Les données qui te sont fournies sont DÉJÀ restreintes aux trades clos après
cette heure : tout ce que tu vois a été produit par le code actuel. Tu ne peux
donc rien conclure sur ce qui précède, et tu n'as pas à le commenter.
Corollaire : après un redéploiement récent, l'échantillon est petit. Peu de
trades = peu de conclusions. Dis-le plutôt que de forcer une anomalie.

MÉMOIRE — NE TE RÉPÈTE PAS :
`audits_precedents` contient les titres de tes derniers rapports. Ne resignale
pas une anomalie déjà remontée, sauf si tu disposes d'un cas NOUVEAU postérieur
au rapport où elle figurait. Répéter un constat déjà transmis n'apporte rien et
noie les vraies alertes.

DISCIPLINE :
- Une différence de TAILLE de position entre instances est ATTENDUE. Ne la
  signale que si elle est extrême ou nouvelle.
- Un écart de P&L proportionnel à l'écart de taille est NORMAL. Ce qui est
  anormal, c'est un écart de rendement (bps), taille neutralisée.
- Distingue toujours « les données divergent » (anomalie) de « le marché a été
  défavorable » (pas une anomalie).
- Si tu ne vois rien de solide, dis-le. Un rapport vide est un bon rapport.
  N'invente pas d'anomalie pour remplir.

Réponds UNIQUEMENT en JSON :
{"anomalies": [{"severite": "critique|moyenne|faible",
                "titre": "<8 mots max>",
                "constat": "<le fait chiffré, précis, vérifiable>",
                "hypothese": "<mécanisme suspecté>",
                "verification": "<ce qu'un humain doit regarder pour trancher>"}],
 "ras": "<si aucune anomalie : ce que tu as vérifié et trouvé sain>"}
"""
PROMPT_HASH = _hl.sha256((SYSTEM_PROMPT + DOCTRINE_DIGEST).encode()).hexdigest()[:10]


def _iso_to_ts(iso):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(iso).replace(
            tzinfo=dt.timezone.utc).timestamp()
    except Exception:
        return 0.0


def _runtime_since():
    """Heure de démarrage du process Alfred = mise en service du code actuel.
    Un trade clos avant tourne sur du code qui n'existe plus."""
    import datetime as dt
    import subprocess
    try:
        pids = subprocess.run(["pgrep", "-f", "python3 -m alfred"],
                              capture_output=True, text=True,
                              timeout=5).stdout.split()
        if not pids:
            return None
        st = min(os.stat(f"/proc/{p}").st_ctime for p in pids)
        return dt.datetime.fromtimestamp(st, dt.timezone.utc).isoformat()[:16]
    except Exception:
        return None


def _previous_audits(n=3):
    """Titres des derniers rapports : évite de resignaler la même chose."""
    import datetime as dt
    try:
        c = sqlite3.connect(BOTS["live"])
        rows = c.execute("SELECT ts,data FROM events WHERE event='AI_SYSTEM_AUDIT'"
                         " ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for ts, data in rows:
        try:
            d = json.loads(data)
        except Exception:
            continue
        out.append({"le": dt.datetime.fromtimestamp(
                        ts, dt.timezone.utc).isoformat()[:16],
                    "titres": [a.get("titre") for a in (d.get("anomalies") or [])]})
    return out


def _rows(db, since_iso, since_exit):
    if not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    try:
        return [dict(symbol=r[0], strategy=r[1], direction=r[2], entry=r[3],
                     exit=r[4], entry_px=r[5], exit_px=r[6], size=r[7],
                     gross=r[8], pnl=r[9], reason=r[10])
                for r in c.execute(
                    "SELECT symbol,strategy,direction,entry_time,exit_time,"
                    "entry_price,exit_price,size_usdt,gross_bps,pnl_usdt,reason "
                    "FROM trades WHERE exit_time IS NOT NULL AND entry_time>=? "
                    "AND exit_time>=?",
                    (since_iso, since_exit))]
    finally:
        c.close()


def build_context() -> dict:
    """Assemble les données de cohérence système. Aucune donnée de marché :
    l'auditeur regarde le BOT, pas le marché."""
    import datetime as dt
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    # Les trades clos avant le démarrage du process ont été produits par du code
    # qui n'existe plus : les auditer revient à resignaler de l'histoire déjà
    # corrigée à chaque exécution. On ne les charge pas.
    rt = _runtime_since()
    since_exit = max(since, rt) if rt else since
    L = _rows(BOTS["live"], since, since_exit)
    P = _rows(BOTS["paper"], since, since_exit)

    # 1. Paires appariées live/paper : même symbole, entrée à moins de MATCH_SLACK
    def key(t):
        return (t["symbol"], t["entry"][:13])
    pidx = {key(t): t for t in P}
    pairs = []
    for lt in L:
        pt = pidx.get(key(lt))
        if not pt:
            continue
        # Écart de RENDEMENT (bps) : taille neutralisée, c'est le signal propre.
        pairs.append({
            "coin": lt["symbol"], "strat": lt["strategy"], "dir": lt["direction"],
            "entree": lt["entry"][:16],
            "sortie": (lt["exit"] or "")[:16],
            "live": {"sortie_px": lt["exit_px"], "gross_bps": lt["gross"],
                     "raison": lt["reason"], "taille": round(lt["size"], 1)},
            "paper": {"sortie_px": pt["exit_px"], "gross_bps": pt["gross"],
                      "raison": pt["reason"], "taille": round(pt["size"], 1)},
            "ecart_gross_bps": round((lt["gross"] or 0) - (pt["gross"] or 0), 1),
            "ecart_prix_sortie_pct": (
                round((lt["exit_px"] / pt["exit_px"] - 1) * 100, 2)
                if lt["exit_px"] and pt["exit_px"] else None),
            "meme_raison": lt["reason"] == pt["reason"],
        })
    pairs.sort(key=lambda p: -abs(p["ecart_gross_bps"]))

    # 2. Distribution des raisons de sortie (dérive / régression de règle)
    dist = {b: dict(Counter(t["reason"] for t in rows))
            for b, rows in (("live", L), ("paper", P))}

    # 3. Cohérence comptable : somme DB vs state.json
    coherence = {}
    for b, db in BOTS.items():
        sp = os.path.join(os.path.dirname(db), "state.json")
        try:
            with open(sp) as f:
                st = json.load(f)
            c = sqlite3.connect(db)
            s = c.execute("SELECT COALESCE(SUM(pnl_usdt),0) FROM trades").fetchone()[0]
            c.close()
            coherence[b] = {"somme_db": round(s, 4),
                            "state_total_pnl": round(st.get("total_pnl", 0), 4),
                            "ecart": round(s - st.get("total_pnl", 0), 4),
                            "n_positions_ouvertes": len(st.get("positions", []))}
        except Exception as e:
            coherence[b] = {"erreur": str(e)[:120]}

    # 4. Dernier rapport de divergence live-vs-BT (déjà calculé toutes les 4h)
    div = None
    try:
        c = sqlite3.connect(BOTS["live"])
        r = c.execute("SELECT data FROM events WHERE event='BT_DIVERGENCE' "
                      "ORDER BY ts DESC LIMIT 1").fetchone()
        c.close()
        if r:
            d = json.loads(r[0])
            div = {k: d.get(k) for k in (
                "window_start", "live_n", "bt_n", "matched_n", "live_pnl",
                "bt_pnl", "gap", "bt_equity", "missed_winner_n",
                "missed_winner_pnl", "avoided_loser_pnl", "bt_only_by_cause")}
    except Exception:
        pass

    # 5. Causes de SKIP (dérive des gates)
    skips = {}
    try:
        c = sqlite3.connect(BOTS["live"])
        cnt = defaultdict(int)
        for (data,) in c.execute(
                "SELECT data FROM events WHERE event='SKIP' AND ts>=?",
                (max(time.time() - WINDOW_DAYS * 86400,
                     _iso_to_ts(since_exit)),)):
            try:
                cnt[json.loads(data).get("reason", "?")] += 1
            except Exception:
                pass
        c.close()
        skips = dict(cnt)
    except Exception:
        pass

    return {"date_du_jour": dt.datetime.now(dt.timezone.utc).isoformat()[:16],
            "runtime_depuis": _runtime_since(),
            "audits_precedents": _previous_audits(),
            "fenetre_jours": WINDOW_DAYS,
            "n_trades": {"live": len(L), "paper": len(P), "apparies": len(pairs)},
            "paires_live_vs_paper": pairs[:25],
            "distribution_raisons_sortie": dist,
            "coherence_comptable": coherence,
            "divergence_live_vs_backtest": div,
            "skips_live": skips}


def call_claude(ctx: dict, model: str) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    system = [{"type": "text", "text": SYSTEM_PROMPT},
              {"type": "text",
               "text": "# Référence stratégies & sorties du bot\n\n" + DOCTRINE_DIGEST}]
    user = ("Données de cohérence système (les deux instances font tourner le "
            "MÊME code ; SENIOR est en argent réel avec l'arbitre IA, PAPER est "
            "une simulation sans arbitre) :\n```json\n"
            + json.dumps(ctx, indent=1, default=str, ensure_ascii=False)
            + "\n```\n\nRends ton JSON d'audit.")
    resp = client.messages.create(model=model, max_tokens=1500, system=system,
                                  messages=[{"role": "user", "content": user}])
    raw = "".join(b.text for b in resp.content
                  if getattr(b, "type", None) == "text").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise RuntimeError(f"pas de JSON:\n{raw[:400]}")
    out = json.loads(m.group(0))
    u = getattr(resp, "usage", None)
    out["_usage"] = ({"input_tokens": getattr(u, "input_tokens", 0),
                      "output_tokens": getattr(u, "output_tokens", 0),
                      "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
                      "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0)}
                     if u else {})
    out["_model"] = model
    return out


def format_tg(res: dict) -> str:
    an = res.get("anomalies") or []
    if not an:
        return ("🔎 Audit système — RAS\n"
                + (res.get("ras") or "Aucune anomalie détectée."))[:1500]
    ic = {"critique": "🔴", "moyenne": "🟠", "faible": "🟡"}
    out = [f"🔎 Audit système — {len(an)} anomalie(s)"]
    for a in an[:5]:
        out.append(f"\n{ic.get(a.get('severite'), '•')} {a.get('titre')}"
                   f"\n  constat : {a.get('constat')}"
                   f"\n  piste : {a.get('hypothese')}"
                   f"\n  à vérifier : {a.get('verification')}")
    return "\n".join(out)[:3500]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="contexte + prompt seulement, aucun appel API")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--model", default=os.environ.get("AI_AUDIT_MODEL", DEFAULT_MODEL))
    args = ap.parse_args()

    from supervisor import load_env
    load_env()

    if os.environ.get("AI_SYSTEM_AUDIT_ENABLED", "1") == "0":
        print("[audit] désactivé (AI_SYSTEM_AUDIT_ENABLED=0)")
        return 0

    ctx = build_context()
    print(f"[audit] fenêtre {ctx['fenetre_jours']}j · trades live={ctx['n_trades']['live']} "
          f"paper={ctx['n_trades']['paper']} appariés={ctx['n_trades']['apparies']}")
    if args.dry_run:
        print(json.dumps(ctx, indent=1, default=str, ensure_ascii=False)[:6000])
        print(f"\n[audit] --dry-run : arrêt avant Claude (prompt_hash={PROMPT_HASH})")
        return 0
    if not ctx["n_trades"]["apparies"]:
        print("[audit] aucun trade apparié sur la fenêtre — rien à auditer")
        return 0

    try:
        res = call_claude(ctx, args.model)
    except Exception as e:                     # fail-open : n'impacte rien
        print(f"[audit] échec appel: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    an = res.get("anomalies") or []
    print(f"[audit] {len(an)} anomalie(s)")
    for a in an:
        print(f"  [{a.get('severite')}] {a.get('titre')} — {a.get('constat')}")
    if not an:
        print(f"  RAS : {res.get('ras')}")

    # journal d'audit (event) + coût
    try:
        c = sqlite3.connect(BOTS["live"], timeout=5)
        payload = {k: v for k, v in res.items() if k != "_usage"}
        payload["prompt_hash"] = PROMPT_HASH
        c.execute("INSERT INTO events (ts,event,symbol,data) VALUES (?,?,?,?)",
                  (int(time.time()), "AI_SYSTEM_AUDIT", None,
                   json.dumps(payload, default=str)))
        if res.get("_usage"):
            import ai_cost
            c.execute("INSERT INTO events (ts,event,symbol,data) VALUES (?,?,?,?)",
                      (int(time.time()), "AI_COST", None,
                       json.dumps(ai_cost.cost_event("audit", res.get("_model"),
                                                     res["_usage"]))))
        c.commit(); c.close()
    except Exception as e:
        print(f"[audit] log DB échoué: {e}", file=sys.stderr)

    if not args.no_telegram:
        from ai_notify import send_telegram
        send_telegram(format_tg(res), source="system_audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
