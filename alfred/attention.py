"""Routeur d'attention — supervision v2 phase 1 (2026-07-04).

Code PUR, zéro LLM : écoute les events/état et décide QUAND dépenser un appel
de revue ciblée. « Opportune » = l'attention suit l'information, pas
l'horloge — le PHD2 de la supervision : il ne réfléchit pas, il mesure et
déclenche ; le LLM accourt quand ça sonne.

Déclencheurs v1 (tous lisent des sorties de CODE — jamais d'event produit par
un LLM : pas de boucle de larsen facturée au token) :
  - net_fired    : trade clos en exchange_stop|liquidation|adl (l'event le plus
                   dense du système) → revue LLM des survivants + TG.
  - failopen     : ≥3 ARBITER_FAILOPEN en 1h → TG (infra, pas de LLM).
  - trip         : apparition d'un drapeau disjoncteur → event seul (le
                   scorecard a déjà alerté — économie Telegram).
  - btc_z_band   : franchissement de bande ±0.5/±1.5 → revue LLM du book + TG.
  - breadth      : down20_pct ≥ 10 % (hystérésis off < 5 %) → revue LLM + TG.
  - near_stop    : position à ≤200 bps de son stop → revue LLM ciblée + TG
                   (cooldown 4h/symbole).

Garde-fous : cooldown par trigger, cap journalier d'appels LLM
(ATTENTION_LLM_CAP, déf. 8), kill-switch ATTENTION_ENABLED=0, events
ATTENTION_TRIGGER audités dans live/bot.db. La revue LLM = position_review.py
--focus-symbols --trigger-context (modèle haiku ≈ $0.01/appel → cap 8/j ≈
$2.5/mois, très sous AI_BUDGET_MONTHLY_USD).

Cron : */2 * * * * cd /home/crypto && .venv/bin/python3 -m alfred.attention
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.parse

ROOT = "/home/crypto"
LIVE_DB = os.path.join(ROOT, "alfred", "data", "bots", "live", "bot.db")
STATE_JSON = os.path.join(ROOT, "alfred", "data", "bots", "live", "state.json")
MARKET_DB = os.path.join(ROOT, "alfred", "data", "market.db")
ATT_STATE = os.path.join(ROOT, "alfred", "data", "attention_state.json")
TRIPS = [os.path.join(ROOT, "alfred", "data", "bots", "live", f)
         for f in ("arbiter_tripped.json", "exit_arbiter_tripped.json")]

BTC_Z_BANDS = (-1.5, -0.5, 0.5, 1.5)
BREADTH_ON, BREADTH_OFF = 10.0, 5.0      # % d'alts ≤ −20 %/24h (hystérésis)
NEAR_STOP_BPS = 200.0
FAILOPEN_N, FAILOPEN_WINDOW_S = 3, 3600
NEAR_STOP_COOLDOWN_S = 4 * 3600
BAND_COOLDOWN_S = 6 * 3600
BREADTH_COOLDOWN_S = 12 * 3600


def env(k, d=""):
    v = os.environ.get(k)
    if v is not None:
        return v
    try:
        for line in open(os.path.join(ROOT, ".env")):
            line = line.strip()
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return d


def send_tg(text):
    tok, chat = env("TG_BOT_TOKEN"), env("TG_CHAT_ID")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
    except Exception as e:
        print(f"TG failed: {e}", file=sys.stderr)


def log_event(payload: dict):
    try:
        db = sqlite3.connect(LIVE_DB, timeout=5)
        db.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                   (int(time.time()), "ATTENTION_TRIGGER",
                    payload.get("symbol"), json.dumps(payload, default=str)))
        db.commit(); db.close()
    except Exception as e:
        print(f"log_event failed: {e}", file=sys.stderr)


def llm_review(st: dict, trigger: str, context: str, symbols: list[str] | None):
    """Revue ciblée via position_review --focus. Respecte le cap journalier."""
    today = time.strftime("%F")
    daily = st.setdefault("daily", {})
    if daily.get("date") != today:
        daily.update({"date": today, "llm_calls": 0})
    cap = int(env("ATTENTION_LLM_CAP", "8"))
    if daily["llm_calls"] >= cap:
        print(f"cap LLM journalier atteint ({cap}) — trigger {trigger} loggé sans revue")
        log_event({"trigger": trigger, "context": context, "llm": "capped"})
        return None
    daily["llm_calls"] += 1
    cmd = [os.path.join(ROOT, ".venv", "bin", "python3"),
           os.path.join(ROOT, "position_review.py"),
           "--trigger-context", f"[{trigger}] {context}"]
    if symbols:
        cmd += ["--focus-symbols", ",".join(symbols)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           cwd=ROOT)
        out = r.stdout.strip()
        advices = [l.strip() for l in out.splitlines()
                   if l.strip().split(" ")[0].isupper() and ("conf=" in l)]
        return advices or [out[-300:]] if out else None
    except Exception as e:
        print(f"llm_review failed: {e}", file=sys.stderr)
        return None


def main() -> int:
    if env("ATTENTION_ENABLED", "1") == "0":
        return 0
    now = time.time()
    st = {}
    if os.path.exists(ATT_STATE):
        try:
            st = json.load(open(ATT_STATE))
        except Exception:
            st = {}
    last_scan = st.get("last_scan", now - 300)

    state = {}
    try:
        state = json.load(open(STATE_JSON))
    except Exception:
        pass
    positions = state.get("positions") or []
    open_syms = [p["symbol"] for p in positions]

    ldb = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    ldb.row_factory = sqlite3.Row

    # ── 1. net_fired : fermeture exchange-side depuis le dernier scan ──
    rows = ldb.execute(
        "SELECT symbol, reason, pnl_usdt, exit_time FROM trades WHERE reason IN "
        "('exchange_stop','liquidation','adl') AND "
        "strftime('%s', exit_time) > ?", (int(last_scan),)).fetchall()
    for r in rows:
        ctx = (f"le FILET a parlé : {r['symbol']} fermée côté exchange "
               f"({r['reason']}, {r['pnl_usdt']:+.2f}$). Le process était mort "
               f"ou le marché plus rapide que 20s — juger les SURVIVANTS dans "
               f"ce contexte.")
        advices = llm_review(st, "net_fired", ctx, open_syms or None)
        send_tg(f"⚡ ATTENTION — filet déclenché : {r['symbol']} {r['reason']} "
                f"{r['pnl_usdt']:+.2f}$\n"
                + ("\n".join(advices[:5]) if advices else "(revue indisponible)"))
        log_event({"trigger": "net_fired", "symbol": r["symbol"],
                   "reason": r["reason"], "pnl": r["pnl_usdt"]})

    # ── 2. failopen burst (infra — pas de LLM) ──
    n_fo = ldb.execute(
        "SELECT COUNT(*) FROM events WHERE event='ARBITER_FAILOPEN' AND ts > ?",
        (int(now - FAILOPEN_WINDOW_S),)).fetchone()[0]
    if n_fo >= FAILOPEN_N and now - st.get("last_failopen_alert", 0) > FAILOPEN_WINDOW_S:
        send_tg(f"⚡ ATTENTION — {n_fo} FAILOPEN arbitre en 1h : l'IA ne répond "
                f"plus (API/timeout), le bot trade règles-seules. Vérifier "
                f"ANTHROPIC_API_KEY / statut API.")
        log_event({"trigger": "failopen_burst", "n": n_fo})
        st["last_failopen_alert"] = now

    # ── 3. trips disjoncteur (event seul — le scorecard a déjà alerté) ──
    for tf in TRIPS:
        key = f"trip_seen_{os.path.basename(tf)}"
        exists = os.path.exists(tf)
        if exists and not st.get(key):
            log_event({"trigger": "breaker_trip", "file": os.path.basename(tf)})
        st[key] = exists

    # ── 4. bande btc_z ──
    bz = state.get("_btc_z")
    if bz is not None:
        band = sum(1 for b in BTC_Z_BANDS if bz > b)   # 0..4
        prev = st.get("btc_z_band")
        if prev is not None and band != prev and \
                now - st.get("last_band_review", 0) > BAND_COOLDOWN_S:
            direction = "haussier" if band > prev else "baissier"
            ctx = (f"btc_z vient de franchir une bande de régime ({direction}, "
                   f"z={bz:+.2f}) — le modulateur et les règles régime-"
                   f"conditionnées changent de comportement. Revoir le book.")
            advices = llm_review(st, "btc_z_band", ctx, open_syms or None)
            send_tg(f"⚡ ATTENTION — btc_z {bz:+.2f} change de bande ({direction})\n"
                    + ("\n".join(advices[:5]) if advices else ""))
            log_event({"trigger": "btc_z_band", "z": bz, "band": band, "prev": prev})
            st["last_band_review"] = now
        st["btc_z_band"] = band

    # ── 5. breadth capitulation (calculée ici — pas dispo hors mémoire master) ──
    try:
        from alfred.market import _compute_breadth, _STABLE_SYMBOLS
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            meta, ctxs = json.loads(resp.read())
        rets = []
        for a, c in zip(meta["universe"], ctxs):
            if a["name"].upper() in _STABLE_SYMBOLS:
                continue
            try:
                mark, prev = float(c["markPx"]), float(c["prevDayPx"])
                if prev > 0:
                    rets.append((mark / prev - 1) * 1e4)
            except (KeyError, TypeError, ValueError):
                continue
        br = _compute_breadth(rets)
        d20 = br.get("down20_pct") or 0.0
        was_on = st.get("breadth_on", False)
        if not was_on and d20 >= BREADTH_ON and \
                now - st.get("last_breadth_review", 0) > BREADTH_COOLDOWN_S:
            ctx = (f"CAPITULATION marché-large : {d20:.0f}% des perps HL à "
                   f"≤−20%/24h (médiane {br.get('median_24h_bps'):+.0f} bps). "
                   f"Les LONGs du book sont en première ligne.")
            longs = [p["symbol"] for p in positions if p.get("direction") == 1]
            advices = llm_review(st, "breadth", ctx, longs or open_syms or None)
            send_tg(f"⚡ ATTENTION — capitulation large : {d20:.0f}% des alts "
                    f"≤−20%/24h\n" + ("\n".join(advices[:5]) if advices else ""))
            log_event({"trigger": "breadth", "down20_pct": d20})
            st["last_breadth_review"] = now
            st["breadth_on"] = True
        elif was_on and d20 < BREADTH_OFF:
            st["breadth_on"] = False
    except Exception as e:
        print(f"breadth check failed: {e}", file=sys.stderr)

    # ── 6. near_stop : position à ≤200 bps de son stop ──
    mdb = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    for p in positions:
        sym = p["symbol"]
        row = mdb.execute("SELECT mark_px FROM ticks WHERE symbol=? "
                          "ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        if not row or not p.get("entry_price"):
            continue
        d = p.get("direction", 1)
        ur = d * (row[0] / p["entry_price"] - 1) * 1e4
        stop = (p.get("stop_bps") or 0) or \
               (-750.0 if p.get("strategy") == "S8" else -1250.0)
        dist = ur - stop
        key = f"near_stop_{sym}"
        if 0 < dist <= NEAR_STOP_BPS and now - st.get(key, 0) > NEAR_STOP_COOLDOWN_S:
            ctx = (f"{sym} {p.get('strategy')} à {dist:.0f} bps de son stop "
                   f"catastrophe ({stop:.0f}) — ur {ur:+.0f} bps. Doomed à "
                   f"couper avant le stop, ou survivant à laisser respirer ?")
            advices = llm_review(st, "near_stop", ctx, [sym])
            send_tg(f"⚡ ATTENTION — {sym} à {dist:.0f} bps du stop\n"
                    + ("\n".join(advices[:3]) if advices else ""))
            log_event({"trigger": "near_stop", "symbol": sym,
                       "ur_bps": round(ur), "dist_bps": round(dist)})
            st[key] = now

    st["last_scan"] = now
    tmp = ATT_STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, ATT_STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
