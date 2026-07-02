"""Daily Alfred digest — santé données + flotte via Telegram.

Ships a compact summary :
  - Données     : fraîcheur ticks / WS reconnects / audits depuis market.db
  - Flotte      : balance, P&L réalisé/latent, positions par bot (state.json
                  + dernier mark des ticks market.db)
  - Cap notionnel: déclencheur de re-test quand SENIOR ≥ $1400
  - Liens dashboards

(La phase 3 « parallel-run vs legacy paper :8097 » a été retirée le 2026-06-12
avec le décommission du stack legacy — plus de bot legacy à comparer.)

Cron (08:30 UTC, après le digest régime de 08:15) :
    30 8 * * * /home/crypto/.venv/bin/python3 -m alfred.tools.daily_report \
        >> /home/crypto/alfred/data/daily_report.log 2>&1

Telegram credentials: TG_BOT_TOKEN / TG_CHAT_ID from .env (master channel,
same as the legacy bots' main alerts). --dry-run prints without sending.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

MARKET_DB = os.path.join(_REPO, "alfred", "data", "market.db")
BOTS_CONFIG = os.path.join(_REPO, "alfred", "bots.json")

def _public_base() -> str:
    """Base publique des dashboards (nginx root_path /alfred), override via
    ALFRED_PUBLIC_URL (.env). Lu ici (pas à l'import) pour que l'override,
    chargé dans main(), soit pris. Telegram linkifie les URL nues."""
    return os.environ.get("ALFRED_PUBLIC_URL",
                          "https://echonym.fr/alfred").rstrip("/")


def dashboard_footer() -> str:
    """Lien vers la supervision globale (le lien du bot concerné est sur
    la ligne de chaque bot, cf. fleet_summary)."""
    return f"\n🔗 Supervision flotte : {_public_base()}/master"


def _load_env():
    path = os.path.join(_REPO, ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def observation_summary() -> tuple[str, bool]:
    """(one-line summary, is_clean) from market.db evidence."""
    try:
        db = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
        now = int(time.time())
        first = db.execute("SELECT MIN(ts) FROM ticks").fetchone()[0] or now
        hours = (now - first) / 3600
        last_tick = db.execute("SELECT MAX(ts) FROM ticks").fetchone()[0] or 0
        tick_age = now - last_tick
        n_reconnect = db.execute(
            "SELECT COUNT(*) FROM events WHERE event='WS_RECONNECT'").fetchone()[0]
        n_audit_bad = 0
        for (data,) in db.execute(
                "SELECT data FROM events WHERE event='CANDLE_AUDIT'").fetchall():
            d = json.loads(data or "{}")
            if d.get("mismatches"):
                n_audit_bad += 1
        clean = tick_age < 300 and n_audit_bad == 0
        return (f"obs {hours:.0f}h | dernier tick {tick_age}s | "
                f"reconnects {n_reconnect} | audits KO {n_audit_bad}"), clean
    except Exception as e:
        return f"obs: erreur lecture market.db ({e})", False


def fleet_summary() -> tuple[str, bool]:
    """Résumé santé de la flotte (balance, P&L réalisé/latent, positions par
    bot). fleet_ok=False si un bot est en pause ou son état illisible. Le
    latent est calculé au dernier mark des ticks (gross, comme le dashboard)."""
    try:
        cfg = json.load(open(BOTS_CONFIG))
        bots = cfg.get("bots", []) if isinstance(cfg, dict) else cfg
    except Exception as e:
        return f"💼 Flotte : erreur lecture bots.json ({e})", False
    mk = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)

    def mark(sym: str):
        r = mk.execute("SELECT mark_px FROM ticks WHERE symbol=? "
                       "ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        return r[0] if r and r[0] else None

    lines = ["💼 Flotte :"]
    all_ok = True
    for b in bots:
        bid, label = b["id"], b.get("label", b["id"])
        try:
            st = json.load(open(os.path.join(
                _REPO, "alfred", "data", "bots", bid, "state.json")))
        except Exception:
            lines.append(f"  {label} : état illisible")
            all_ok = False
            continue
        realized = st.get("total_pnl", 0.0)
        bal = st.get("capital", 0.0) + realized
        positions = st.get("positions", [])
        unreal = 0.0
        for p in positions:
            m = mark(p.get("symbol", ""))
            ep = p.get("entry_price", 0.0)
            if m and ep > 0:
                unreal += p.get("size_usdt", 0.0) * p.get("direction", 0) * (m / ep - 1)
        paused = st.get("paused", False)
        if paused:
            all_ok = False
        lines.append(f"  {label} : ${bal:.0f}  réal {realized:+.0f}$  "
                     f"lat {unreal:+.0f}$  · {len(positions)} pos"
                     + (" ⏸PAUSE" if paused else ""))
        # Lien vers le dashboard du bot concerné (sur sa propre ligne)
        lines.append(f"     {_public_base()}/bot/{bid}/")
    return "\n".join(lines), all_ok


def agent_expiry_review(today=None) -> tuple[str, bool]:
    """Sentinelle d'expiry des agent wallets HL (chantier 6, 2026-07-02).

    Un agent expiré = bot orphelin qui ne peut plus signer (ordres rejetés,
    positions non gérées). Les dates vivent dans bots.json (`agent_expiry`,
    ISO date). Escalade : ≤21j → ligne d'avertissement quotidienne ;
    ≤7j → urgent (status ⚠️) ; dépassé → critique. Silence sinon.
    Renvoie (texte, ok) — ok=False dès qu'un agent est ≤7j ou expiré."""
    from datetime import date
    today = today or date.today()
    try:
        cfg = json.load(open(BOTS_CONFIG))
        bots = cfg.get("bots", []) if isinstance(cfg, dict) else cfg
    except Exception:
        return "", True
    lines, ok = [], True
    for b in bots:
        exp = (b.get("agent_expiry") or "").strip()
        if not exp or not b.get("enabled", True):
            continue
        try:
            d_left = (date.fromisoformat(exp) - today).days
        except ValueError:
            lines.append(f"🔑 {b.get('label', b['id'])} : agent_expiry "
                         f"illisible ({exp!r}) dans bots.json")
            ok = False
            continue
        label = b.get("label", b["id"])
        if d_left < 0:
            lines.append(f"🚨 {label} : agent EXPIRÉ depuis {-d_left}j ({exp}) "
                         f"— le bot ne peut plus signer ! Régénérer la clé, "
                         f"ré-autoriser l'agent sur HL, mettre à jour .env")
            ok = False
        elif d_left <= 7:
            lines.append(f"🚨 {label} : agent expire dans {d_left}j ({exp}) — "
                         f"régénérer MAINTENANT (clé + autorisation HL + .env "
                         f"+ restart)")
            ok = False
        elif d_left <= 21:
            lines.append(f"🔑 {label} : agent expire dans {d_left}j ({exp}) — "
                         f"planifier la régénération")
    return ("\n" + "\n".join(lines)) if lines else "", ok


def notional_cap_review() -> str:
    """Déclencheur de re-test du cap notionnel $500 (R&D 2026-06-11,
    `backtests/backtest_liquidity_cap.py`) : quand la balance SENIOR
    atteint ~2× le capital du reset ($680.58), le cap mord la majorité
    des entrées et le cap liquidity-aware doit être re-validé."""
    try:
        with open(os.path.join(_REPO, "alfred", "data", "bots", "live",
                               "state.json")) as fh:
            st = json.load(fh)
        balance = st.get("capital", 0) + st.get("total_pnl", 0)
        if balance >= 1400:
            return (f"\n📐 Balance SENIOR ${balance:.0f} ≥ $1400 — re-tester le "
                    f"cap notionnel (python3 -m backtests.backtest_liquidity_cap)")
    except Exception:
        pass
    return ""


def main() -> int:
    _load_env()
    dry = "--dry-run" in sys.argv

    obs_line, obs_ok = observation_summary()
    fleet_line, fleet_ok = fleet_summary()
    expiry_line, expiry_ok = agent_expiry_review()

    status = "✅" if (obs_ok and fleet_ok and expiry_ok) else "⚠️"
    msg = (f"{status} ALFRED — digest quotidien\n"
           f"🩺 Données : {'✓' if obs_ok else '✗'} {obs_line}\n"
           f"{fleet_line}"
           + expiry_line
           + notional_cap_review()
           + dashboard_footer())

    if dry:
        print(msg)
        return 0

    # Synchronous send — Notifier.send is fire-and-forget in a daemon thread,
    # which a short-lived cron process would kill before the HTTP call lands.
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    sent = False
    if token and chat:
        import urllib.request
        payload = json.dumps({"chat_id": chat,
                              "text": "[ALFRED] " + msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                sent = bool(json.loads(resp.read()).get("ok"))
        except Exception as e:
            print(f"telegram error: {e}", file=sys.stderr)
    print(f"{time.strftime('%F %T')} sent={sent} obs_ok={obs_ok} fleet_ok={fleet_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
