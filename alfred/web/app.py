"""Unified FastAPI app — one port for the admin view and every bot dashboard.

  GET  /                       → admin view (all bots, in-process — no proxy)
  GET  /api/admin              → per-bot summaries
  GET  /bot/{id}/              → reversal.html (unchanged, relative fetches)
  *    /bot/{id}/api/...       → every legacy per-bot endpoint

Auth: single HMAC session (DASHBOARD_USER/PASS + AUTH_SALT from .env) — the
per-bot credential matrix of the legacy admin proxy is gone. Ported from
analysis/bot/web.py:create_app (v12.17.3): stateless signed cookies, login
backoff, logout revocation epoch, mutation rate-limit, security headers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets as _secrets
import struct
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .. import rules
from ..models import Position
from . import views

log = logging.getLogger("alfred")

_STATIC = Path(__file__).parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SESSION_MAX_AGE = 30 * 86400
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 300.0
_BACKOFF_RESET = 3600
_MUT_LIMIT_PER_MIN = 30
_MUT_WINDOW = 60.0


def create_app(bots: dict, master) -> FastAPI:
    """bots: {bot_id: BotInstance}; master: MarketDataMaster."""
    DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
    DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")
    AUTH_SALT = os.environ.get("AUTH_SALT", "")
    ROOT_PATH = os.environ.get("ALFRED_ROOT_PATH", "")
    if DASHBOARD_USER and not DASHBOARD_PASS:
        raise RuntimeError("DASHBOARD_USER set but DASHBOARD_PASS empty — refusing "
                           "to start (auth would accept any password)")
    _SECRET = (hashlib.sha256((DASHBOARD_PASS + AUTH_SALT).encode()).digest()
               if DASHBOARD_PASS else b"")
    # ── Comptes & rôles ───────────────────────────────────────────────
    # "admin" voit tout ; "bot:<id>" est scopé au dashboard de SON bot
    # (parité avec ce que le testeur Junior avait sur le legacy :8099).
    # Mapping en dur JUNIOR_* → bot:junior — généraliser si un jour un
    # testeur Apprenti etc. (les credentials restent des NOMS d'env).
    # TOTP (RFC 6238) optionnel par compte : secret base32 dans .env
    # (DASHBOARD_TOTP_SECRET / JUNIOR_TOTP_SECRET). Vide = pas de 2FA pour
    # ce compte (kill-switch). Exigé seulement pour les requêtes passées
    # par nginx — les sentinelles cron en 127.0.0.1 direct sont exemptées.
    _ACCOUNTS: dict[str, tuple[str, str, str]] = {}  # user → (pass, role, totp)
    if DASHBOARD_USER:
        _ACCOUNTS[DASHBOARD_USER] = (DASHBOARD_PASS, "admin",
                                     os.environ.get("DASHBOARD_TOTP_SECRET", ""))
    _junior_user = os.environ.get("JUNIOR_USER", "")
    _junior_pass = os.environ.get("JUNIOR_PASS", "")
    if _junior_user and _junior_pass and "junior" in bots:
        _ACCOUNTS[_junior_user] = (_junior_pass, "bot:junior",
                                   os.environ.get("JUNIOR_TOTP_SECRET", ""))
    _login_failures: dict[str, tuple[int, float]] = {}
    _revoked_before = {"ts": 0.0}
    _mutation_log: dict[str, deque] = {}
    _html_cache: dict[str, str] = {}

    from .. import ALFRED_VERSION

    def _sign_token(ts: float, role: str = "admin") -> str:
        """Token v2 : ts:role:sig — le rôle est DANS la charge signée."""
        msg = f"{int(ts)}:{role}".encode()
        sig = hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()[:16]
        return f"{int(ts)}:{role}:{sig}"

    def _verify_token(token: str) -> str | None:
        """Retourne le rôle ("admin" | "bot:<id>") ou None si invalide.
        L'ancien format ts:sig (2 champs) est rejeté → simple re-login."""
        if not token:
            return None
        parts = token.split(":")
        if len(parts) == 3:
            ts_str, role, sig = parts
        elif len(parts) == 4 and parts[1] == "bot":   # ts:bot:<id>:sig
            ts_str, role, sig = parts[0], f"{parts[1]}:{parts[2]}", parts[3]
        else:
            return None
        try:
            ts = int(ts_str)
        except ValueError:
            return None
        if time.time() - ts > _SESSION_MAX_AGE:
            return None
        if ts < _revoked_before["ts"]:
            return None
        expected = hmac.new(_SECRET, f"{ts_str}:{role}".encode(),
                            hashlib.sha256).hexdigest()[:16]
        return role if hmac.compare_digest(sig, expected) else None

    def _role_home(role: str) -> str:
        """Page d'atterrissage par rôle."""
        if role.startswith("bot:"):
            return f"{ROOT_PATH}/bot/{role[4:]}/"
        return f"{ROOT_PATH}/master"

    def _client_ip(request: Request) -> str:
        xff = request.headers.get("x-forwarded-for", "")
        direct_ip = request.client.host if request.client else "unknown"
        return xff.split(",")[0].strip() if xff and direct_ip == "127.0.0.1" else direct_ip

    def _is_local_direct(request: Request) -> bool:
        """Vrai pour une connexion 127.0.0.1 SANS X-Forwarded-For = sentinelle
        cron locale. Tout ce qui passe par nginx porte un XFF (proxy_add_…
        APPEND au header client, donc non-vide même si le client le forge) —
        ne PAS utiliser _client_ip() ici, son premier élément est spoofable."""
        direct_ip = request.client.host if request.client else ""
        return direct_ip == "127.0.0.1" and not request.headers.get("x-forwarded-for")

    def _verify_totp(secret_b32: str, code: str) -> bool:
        """RFC 6238 (SHA-1, 6 chiffres, pas de 30s), fenêtre ±1 pas."""
        code = code.strip().replace(" ", "")
        if not code.isdigit() or len(code) != 6:
            return False
        try:
            key = base64.b32decode(secret_b32.strip().upper()
                                   + "=" * (-len(secret_b32.strip()) % 8))
        except Exception:
            return False
        now = int(time.time())
        for off in (-30, 0, 30):
            h = hmac.new(key, struct.pack(">Q", (now + off) // 30),
                         hashlib.sha1).digest()
            o = h[19] & 0x0F
            expected = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
            if hmac.compare_digest(f"{expected:06d}", code):
                return True
        return False

    def _check_mutation_rate(ip: str) -> bool:
        now = time.time()
        dq = _mutation_log.get(ip)
        if dq is None:
            if _mutation_log:
                stale = [k for k, v in _mutation_log.items()
                         if not v or now - v[-1] > _MUT_WINDOW * 10]
                for k in stale:
                    _mutation_log.pop(k, None)
            dq = deque(maxlen=_MUT_LIMIT_PER_MIN * 4)
            _mutation_log[ip] = dq
        while dq and now - dq[0] > _MUT_WINDOW:
            dq.popleft()
        if len(dq) >= _MUT_LIMIT_PER_MIN:
            return False
        dq.append(now)
        return True

    def _backoff_delay(ip: str) -> float:
        rec = _login_failures.get(ip)
        if not rec:
            return 0.0
        n_fails, last_ts = rec
        if time.time() - last_ts > _BACKOFF_RESET:
            _login_failures.pop(ip, None)
            return 0.0
        required = min(_BACKOFF_BASE * (2 ** (n_fails - 1)), _BACKOFF_MAX)
        return max(0.0, required - (time.time() - last_ts))

    app = FastAPI(root_path=ROOT_PATH)

    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if request.headers.get("x-forwarded-proto") == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
            return response

    class _MutationRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                ip = _client_ip(request)
                if not _check_mutation_rate(ip):
                    log.warning("MUTATION RATE LIMIT: ip=%s path=%s", ip, request.url.path)
                    return JSONResponse({"error": "rate limit exceeded — slow down"},
                                        status_code=429)
            return await call_next(request)

    if DASHBOARD_USER:
        class _AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                # Accès port direct : les redirects portent le ROOT_PATH
                # (/alfred/...) alors que nginx le strippe — normalise pour
                # que les contrôles de scope voient le même chemin partout.
                if ROOT_PATH and path.startswith(ROOT_PATH):
                    path = path[len(ROOT_PATH):] or "/"
                if path in ("/login", "/favicon.ico") or path.startswith("/auth"):
                    return await call_next(request)
                token = request.cookies.get("alfred_session")
                role = _verify_token(token) if token else None
                if role is None:
                    if "/api/" in path:
                        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
                    return RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
                # ── Scope par rôle : bot:<id> ne voit QUE son bot ─────
                if role.startswith("bot:"):
                    bot_id = role[4:]
                    allowed = (path.startswith(f"/bot/{bot_id}/")
                               or path == "/logout")
                    if not allowed:
                        if "/api/" in path:
                            return JSONResponse(
                                {"detail": f"Forbidden — role limited to bot {bot_id}"},
                                status_code=403)
                        return RedirectResponse(_role_home(role), status_code=303)
                    # journal d'audit : toute mutation d'un rôle non-admin
                    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                        try:
                            master.db.log_admin_action(
                                _client_ip(request), path, bot_id, None,
                                f"role={role}")
                        except Exception:
                            pass
                request.state.role = role
                return await call_next(request)
        app.add_middleware(_AuthMiddleware)
    app.add_middleware(_MutationRateLimitMiddleware)
    app.add_middleware(_SecurityHeadersMiddleware)

    _LOGIN_HTML = (_STATIC / "login.html").read_text()

    # ── Auth routes ───────────────────────────────────────────────────

    @app.get("/auth")
    async def auth_bridge(token: str = ""):
        role = _verify_token(token)
        if role:
            resp = RedirectResponse(_role_home(role), status_code=303)
            resp.set_cookie("alfred_session", token, httponly=True, samesite="strict",
                            max_age=_SESSION_MAX_AGE)
            return resp
        return RedirectResponse(f"{ROOT_PATH}/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return (_LOGIN_HTML.replace("{{VERSION}}", ALFRED_VERSION)
                .replace("{{MODE}}", "ALFRED").replace("{{ERROR}}", ""))

    @app.post("/login")
    async def login_submit(request: Request, username: str = Form(...),
                           password: str = Form(...), totp: str = Form("")):
        client_ip = _client_ip(request)
        delay = _backoff_delay(client_ip)
        if delay > 0:
            html = (_LOGIN_HTML.replace("{{VERSION}}", ALFRED_VERSION)
                    .replace("{{MODE}}", "ALFRED")
                    .replace("{{ERROR}}", f"Too many failed attempts — retry in {int(delay)}s"))
            return HTMLResponse(html, status_code=429)
        acct = _ACCOUNTS.get(username)
        pass_ok = bool(acct) and _secrets.compare_digest(password, acct[0])
        # TOTP exigé si le compte a un secret ET que la requête vient de
        # l'extérieur (via nginx). Vérifié même si le password est faux
        # (pas d'oracle sur lequel des deux facteurs a échoué).
        totp_ok = True
        if acct and acct[2] and not _is_local_direct(request):
            totp_ok = _verify_totp(acct[2], totp)
        if pass_ok and totp_ok:
            role = acct[1]
            _login_failures.pop(client_ip, None)
            token = _sign_token(time.time(), role)
            resp = RedirectResponse(_role_home(role), status_code=303)
            resp.set_cookie("alfred_session", token, httponly=True, samesite="strict",
                            max_age=_SESSION_MAX_AGE)
            log.info("LOGIN OK: user=%s role=%s ip=%s", username, role, client_ip)
            # Les sentinelles cron (hedge_monitor 5 min, regime_alert horaire,
            # supervisor quotidien) se loguent depuis 127.0.0.1 — pas de TG
            # pour ces logins locaux, sinon spam. Un humain passe par nginx
            # (X-Forwarded-For → vraie IP). Les FAIL restent tous notifiés.
            if not _is_local_direct(request):
                master.notifier.send(f"🔑 Login OK Alfred — user={username} "
                                     f"role={role} ip={client_ip}",
                                     category="security")
            return resp
        n_fails, _ = _login_failures.get(client_ip, (0, 0.0))
        _login_failures[client_ip] = (n_fails + 1, time.time())
        log.warning("LOGIN FAIL: user=%s ip=%s attempts=%d", username, client_ip, n_fails + 1)
        master.notifier.send(f"⚠️ Login FAIL Alfred — user={username} ip={client_ip} "
                             f"(attempt #{n_fails + 1})", category="security")
        html = (_LOGIN_HTML.replace("{{VERSION}}", ALFRED_VERSION)
                .replace("{{MODE}}", "ALFRED")
                .replace("{{ERROR}}", "Invalid credentials"))
        return HTMLResponse(html, status_code=401)

    @app.get("/logout")
    async def logout():
        _revoked_before["ts"] = time.time()
        resp = RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
        resp.delete_cookie("alfred_session")
        return resp

    # ── Admin view ────────────────────────────────────────────────────

    @app.get("/")
    async def admin_index():
        # L'ancien carousel (admin.html) est fusionné dans /master
        # (onglet "Vue globale") — un seul écran de supervision.
        return RedirectResponse(f"{ROOT_PATH}/master", status_code=303)

    @app.get("/api/admin")
    def api_admin():
        return JSONResponse(views.build_admin_summary(bots, master))

    @app.get("/api/master")
    async def api_master():
        snap = master.snapshot
        return JSONResponse({
            "version": ALFRED_VERSION,
            "ws_connected": master.ws_connected,
            "ws_reconnects": master.ws_reconnects,
            "last_price_s": (round(time.time() - master.last_price_fetch, 0)
                             if master.last_price_fetch else None),
            "snapshot_version": snap.version if snap else None,
            "snapshot_age_s": round(time.time() - snap.ts, 0) if snap else None,
            "btc_z": round(snap.btc_z, 3) if snap and snap.btc_z is not None else None,
            "n_bots": len(bots),
        })

    # ── /master : supervision 3 niveaux ───────────────────────────────

    _bots_cfg_path = os.environ.get(
        "ALFRED_BOTS_CONFIG",
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "alfred", "bots.json"))

    def _audit(request: Request, route: str, bot_id: str | None,
               payload: dict | None, result: str) -> None:
        ip = request.client.host if request.client else "?"
        master.db.log_admin_action(ip, route, bot_id, payload, result)

    @app.get("/master", response_class=HTMLResponse)
    async def master_page():
        return (_STATIC / "master.html").read_text()

    @app.get("/api/master/health")
    def api_master_health():
        return JSONResponse(views._to_py(
            views.build_master_health(master, bots, _bots_cfg_path)))

    @app.get("/api/master/gates")
    def api_master_gates():
        return JSONResponse(views.build_gates_status())

    @app.get("/api/master/events")
    def api_master_events(limit: int = 50, kinds: str = ""):
        limit = max(1, min(limit, 500))
        try:
            with master.db.lock:
                if kinds:
                    klist = [k.strip() for k in kinds.split(",") if k.strip()]
                    q = ",".join("?" for _ in klist)
                    rows = master.db.conn.execute(
                        f"SELECT ts, event, symbol, data FROM events "
                        f"WHERE event IN ({q}) ORDER BY ts DESC LIMIT ?",
                        (*klist, limit)).fetchall()
                else:
                    rows = master.db.conn.execute(
                        "SELECT ts, event, symbol, data FROM events "
                        "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return JSONResponse([{"ts": r[0], "event": r[1],
                                  "symbol": r[2], "data": r[3]} for r in rows])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/fleet")
    def api_fleet():
        return JSONResponse(views._to_py(views.build_fleet_response(bots, master)))

    @app.get("/api/admin/audit")
    def api_admin_audit(limit: int = 100):
        return JSONResponse(views.build_audit_trail(master, max(1, min(limit, 500))))

    @app.post("/api/bot/{bot_id}/lifecycle")
    async def api_bot_lifecycle(bot_id: str, request: Request):
        """Stop/start d'un BotInstance au runtime. stop ≠ pause : le
        scheduler saute ENTIÈREMENT le bot (exits gelés aussi)."""
        bot = _bot(bot_id)
        if bot is None:
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        action = body.get("action")
        if action not in ("stop", "start"):
            return JSONResponse({"error": "action must be stop|start"},
                                status_code=400)
        if action == "stop":
            bot.status = "stopped"
        else:
            bot.status = "running"
        bot._save_state()
        bot.db.log_event("LIFECYCLE", None, {"action": action})
        _audit(request, f"/api/bot/{bot_id}/lifecycle", bot_id,
               {"action": action}, "ok")
        log.info("[%s] lifecycle: %s", bot_id, action)
        n_pos = len(bot.positions)
        return JSONResponse({"status": "ok", "bot_status": bot.status,
                             "open_positions": n_pos,
                             "warning": (f"{n_pos} position(s) ouverte(s) — "
                                         f"exits GELÉS tant que stopped"
                                         if action == "stop" and n_pos else None)})

    @app.get("/api/botsconfig")
    def api_botsconfig_get():
        try:
            with open(_bots_cfg_path) as fh:
                content = fh.read()
            return JSONResponse({"path": _bots_cfg_path, "content": content})
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    def _validate_bots_payload(text: str) -> tuple[list | None, str | None]:
        from ..settings import parse_bots_config
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"JSON invalide : {e}"
        try:
            cfgs = parse_bots_config(raw)
        except (ValueError, KeyError, TypeError) as e:
            return None, f"Config invalide : {e}"
        return cfgs, None

    @app.post("/api/botsconfig/validate")
    async def api_botsconfig_validate(request: Request):
        body = await request.body()
        cfgs, err = _validate_bots_payload(body.decode())
        if err:
            return JSONResponse({"valid": False, "error": err})
        loaded_ids = set(bots.keys())
        new_ids = {c.id for c in cfgs}
        return JSONResponse({
            "valid": True,
            "bots": [{"id": c.id, "mode": c.mode, "label": c.label,
                      "enabled": c.enabled} for c in cfgs],
            "diff": {"added": sorted(new_ids - loaded_ids),
                     "removed": sorted(loaded_ids - new_ids),
                     "kept": sorted(new_ids & loaded_ids)}})

    @app.post("/api/botsconfig")
    async def api_botsconfig_save(request: Request):
        body = await request.body()
        text = body.decode()
        cfgs, err = _validate_bots_payload(text)
        if err:
            _audit(request, "/api/botsconfig", None, None, f"rejected: {err}")
            return JSONResponse({"saved": False, "error": err}, status_code=400)
        try:
            # backup + écriture atomique
            if os.path.exists(_bots_cfg_path):
                import shutil
                shutil.copy2(_bots_cfg_path, _bots_cfg_path + ".bak")
            tmp = _bots_cfg_path + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(text)
            os.replace(tmp, _bots_cfg_path)
        except OSError as e:
            _audit(request, "/api/botsconfig", None, None, f"io_error: {e}")
            return JSONResponse({"saved": False, "error": str(e)}, status_code=500)
        _audit(request, "/api/botsconfig", None,
               {"bots": [c.id for c in cfgs]}, "saved")
        log.info("bots.json sauvegardé (%d bots) — effet au prochain restart",
                 len(cfgs))
        return JSONResponse({"saved": True,
                             "note": "effet au prochain restart d'Alfred",
                             "bots": [c.id for c in cfgs]})

    # ── Per-bot helpers ───────────────────────────────────────────────

    def _bot(bot_id: str):
        return bots.get(bot_id)

    def _bot_html(bot) -> str:
        if bot.id not in _html_cache:
            syms_json = json.dumps(["BTC", "ETH"] + list(bot.p.trade_symbols))
            sectors_json = json.dumps({k: list(v) for k, v in bot.p.sectors.items()})
            _html_cache[bot.id] = ((_STATIC / "reversal.html").read_text()
                                   .replace("{{VERSION}}", bot.version)
                                   .replace("{{MODE}}", bot.label)
                                   .replace("{{MODE_COLOR}}", bot.color or "#58a6ff")
                                   .replace("{{TRADE_SYMBOLS_JSON}}", syms_json)
                                   .replace("{{SECTORS_JSON}}", sectors_json))
        return _html_cache[bot.id]

    NOT_FOUND = JSONResponse({"error": "unknown bot"}, status_code=404)

    @app.get("/bot/{bot_id}", response_class=HTMLResponse)
    async def bot_index_noslash(bot_id: str):
        # Relative fetches need the trailing slash to resolve under /bot/{id}/
        return RedirectResponse(f"{ROOT_PATH}/bot/{bot_id}/", status_code=307)

    @app.get("/bot/{bot_id}/", response_class=HTMLResponse)
    async def bot_index(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return HTMLResponse("unknown bot", status_code=404)
        return _bot_html(bot)

    @app.get("/bot/{bot_id}/api/health")
    async def api_health(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        pa = time.time() - master.last_price_fetch if master.last_price_fetch else 9999
        sa = time.time() - bot._last_scan if bot._last_scan else 9999
        stale = pa > 300 or sa > 7200
        status = "stale" if stale else ("degraded" if master._degraded else "ok")
        return JSONResponse({
            "status": status, "price_age_s": round(pa, 0), "scan_age_s": round(sa, 0),
            "exchange_ok": True, "degraded": list(master._degraded),
            "positions_count": len(bot.positions), "paused": bot._paused,
        }, status_code=503 if stale else 200)

    @app.get("/bot/{bot_id}/api/state")
    def api_state(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        return JSONResponse(views.build_state_response(bot))

    @app.get("/bot/{bot_id}/api/signals")
    def api_signals(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        return JSONResponse(views.build_signals_response(bot))

    @app.get("/bot/{bot_id}/api/trades")
    async def api_trades(bot_id: str, limit: int = 50):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        return JSONResponse(views.build_trades_list(bot.trades, limit))

    @app.get("/bot/{bot_id}/api/pnl")
    async def api_pnl(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        perf_ts = bot._perf_track_start_ts
        if perf_ts > 0:
            baseline = bot._capital_at_perf_reset or bot._capital
            total_at_reset = bot._total_pnl_at_perf_reset
        else:
            baseline = bot.cfg.capital_initial
            total_at_reset = 0.0
        return JSONResponse(views.build_pnl_curve(bot.trades, baseline,
                                                  perf_ts, total_at_reset))

    @app.get("/bot/{bot_id}/api/events")
    def api_events(bot_id: str, limit: int = 30):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        limit = max(1, min(limit, 200))
        try:
            cur = bot.db.conn.execute(
                "SELECT ts, event, symbol, data FROM events ORDER BY ts DESC LIMIT ?",
                (limit,))
            return JSONResponse([{"ts": r[0], "event": r[1], "symbol": r[2],
                                  "data": r[3]} for r in cur])
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/bot/{bot_id}/api/chart/{symbol}")
    async def api_chart(bot_id: str, symbol: str, hours: int = 24):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        if symbol not in bot.p.all_symbols:
            return JSONResponse({"symbol": symbol, "points": [], "position": None})
        hours = max(1, min(hours, 168))
        MAX_POINTS = 200

        def _build_chart():
            """Grille temporelle UNIFORME (un point par bucket sur toute la
            fenêtre). lightweight-charts espace les points par INDEX : mixer
            bougies 4h et buckets ticks 21 min rendait l'axe non-linéaire en
            temps (l'entrée paraissait au mauvais endroit). Les buckets sans
            tick héritent du dernier close de bougie connu (marches plates
            sur l'ère pré-ticks / les trous)."""
            st = master.states.get(symbol)
            now_ts = int(time.time())
            cutoff_ts = now_ts - hours * 3600
            bucket_s = max(60, (hours * 3600) // MAX_POINTS)
            tickmap = {}
            try:
                rows = master.db.conn.execute(
                    """SELECT (ts / ?) * ? AS bucket, AVG(mark_px)
                       FROM ticks WHERE symbol = ? AND ts > ?
                       GROUP BY bucket ORDER BY bucket""",
                    (bucket_s, bucket_s, symbol, cutoff_ts)).fetchall()
                tickmap = {int(r[0]): float(r[1]) for r in rows}
            except Exception:
                pass
            candles = ([(c["t"] // 1000, c["c"]) for c in st.candles_4h]
                       if st and st.candles_4h else [])
            import bisect as _bi
            c_ts = [c[0] for c in candles]
            pts = []
            start = ((cutoff_ts // bucket_s) + 1) * bucket_s
            for ts in range(start, now_ts + 1, bucket_s):
                price = tickmap.get(ts)
                if price is None and candles:
                    # interpolation linéaire entre closes 4h : préserve le
                    # rendu en diagonales de l'ère pré-ticks (un forward-fill
                    # plat donnait des marches toutes les 4h).
                    i = _bi.bisect_right(c_ts, ts) - 1
                    if i >= 0:
                        t0c, p0c = candles[i]
                        if i + 1 < len(candles):
                            t1c, p1c = candles[i + 1]
                            price = p0c + (p1c - p0c) * (ts - t0c) / (t1c - t0c)
                        else:
                            price = p0c
                if price is not None:
                    pts.append({"ts": ts, "price": price})
            return pts

        import asyncio
        pts = await asyncio.to_thread(_build_chart)
        pos_info = None
        pos = bot.positions.get(symbol)
        if pos:
            from .. import rules as _rules
            _stop_bps = _rules.effective_stop(_rules.PosView(
                strategy=pos.strategy, direction=pos.direction,
                entry_price=pos.entry_price, size_usdt=pos.size_usdt,
                stop_bps=pos.stop_bps, mfe_bps=pos.mfe_bps, mae_bps=pos.mae_bps,
                hours_held=0, hours_to_timeout=0, mfe_at_h=0), bot.p)
            _px = lambda bps: pos.entry_price * (1 + pos.direction * bps / 1e4)
            ms_price = None
            if pos.manual_stop_usdt is not None and pos.size_usdt > 0:
                # prix où le P&L NET touche le stop manuel ($) : gross = ms/size + coûts
                ms_price = _px(pos.manual_stop_usdt / pos.size_usdt * 1e4
                               + bot.p.cost_bps)
            pos_info = {"entry_price": pos.entry_price,
                        "direction": "LONG" if pos.direction == 1 else "SHORT",
                        "strategy": pos.strategy,
                        "entry_ts": int(pos.entry_time.timestamp()),
                        "stop_price": _px(_stop_bps),
                        "manual_stop_price": ms_price,
                        "opp_floor_price": (_px(pos.opp_floor_bps)
                                            if pos.opp_floor_bps is not None else None)}
        return JSONResponse({"symbol": symbol, "points": pts, "position": pos_info})

    @app.get("/bot/{bot_id}/api/changelog")
    def api_changelog(bot_id: str):
        # Changelog propre à Alfred (remis à zéro à la v1.0.0) — l'historique
        # legacy v10-v12 reste dans le CHANGELOG.md racine.
        try:
            return JSONResponse({"content": (
                _REPO_ROOT / "alfred" / "CHANGELOG.md").read_text()})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/bot/{bot_id}/api/backtests")
    def api_backtests(bot_id: str):
        # Same sanitizer as the legacy dashboard (strategy details stripped)
        try:
            import sys
            sys.path.insert(0, str(_REPO_ROOT))
            from analysis.bot.web import sanitize_backtests_md
            raw = (_REPO_ROOT / "docs" / "backtests.md").read_text()
            return JSONResponse({"content": sanitize_backtests_md(raw)})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Per-bot mutations ─────────────────────────────────────────────

    @app.post("/bot/{bot_id}/api/close/{symbol}")
    def api_close_symbol(bot_id: str, symbol: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        sym = symbol.upper()
        if sym not in bot.positions:
            return JSONResponse({"error": f"{sym} not in positions"}, status_code=404)
        st = bot.states.get(sym)
        if not st or st.price <= 0:
            return JSONResponse({"error": f"no price for {sym}"}, status_code=400)
        if not bot.close_and_check(sym, st.price, datetime.now(timezone.utc), "manual_close"):
            return JSONResponse({"error": f"close failed for {sym}, will retry"},
                                status_code=500)
        bot._save_state()
        return JSONResponse({"status": "closed", "symbol": sym})

    @app.post("/bot/{bot_id}/api/manual_stop/{symbol}")
    async def api_manual_stop(bot_id: str, symbol: str, request: Request):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        p = bot.p
        sym = symbol.upper()
        if sym not in p.trade_symbols:
            return JSONResponse({"error": "unknown symbol"}, status_code=400)
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        if sym not in bot.positions:
            return JSONResponse({"error": f"{sym} not in positions"}, status_code=404)
        pos = bot.positions[sym]

        if body.get("clear"):
            with bot._pos_lock:
                pos.manual_stop_usdt = None
            bot._save_state()
            return JSONResponse({"status": "cleared", "symbol": sym})

        stop_usdt = body.get("stop_usdt")
        if stop_usdt is None:
            return JSONResponse({"error": "missing 'stop_usdt' or 'clear' field"},
                                status_code=400)
        try:
            stop_usdt = float(stop_usdt)
        except (TypeError, ValueError):
            return JSONResponse({"error": "stop_usdt must be a number"}, status_code=400)
        if not math.isfinite(stop_usdt):
            return JSONResponse({"error": "stop_usdt must be finite"}, status_code=400)
        if pos.size_usdt <= 0:
            return JSONResponse({"error": "position has invalid size"}, status_code=400)
        trigger_gross_bps = stop_usdt / pos.size_usdt * 1e4 + p.cost_bps
        st = bot.states.get(sym)
        if not st or st.price <= 0 or pos.entry_price <= 0:
            return JSONResponse({"error": "no current price"}, status_code=400)
        current_bps = pos.direction * (st.price / pos.entry_price - 1) * 1e4
        current_pnl_net = pos.size_usdt * (current_bps - p.cost_bps) / 1e4
        if stop_usdt >= current_pnl_net:
            return JSONResponse({"error": (
                f"stop ${stop_usdt:.2f} is at or above current net pnl "
                f"${current_pnl_net:.2f} ({current_bps:+.0f} bps gross) — would "
                f"trigger immediately. Use /api/close instead.")}, status_code=400)
        cata_stop = rules.effective_stop(
            rules.PosView(strategy=pos.strategy, direction=pos.direction,
                          entry_price=pos.entry_price, size_usdt=pos.size_usdt,
                          stop_bps=pos.stop_bps, mfe_bps=0, mae_bps=0,
                          hours_held=0, hours_to_timeout=1, mfe_at_h=0), p)
        if trigger_gross_bps <= cata_stop:
            return JSONResponse({"error": (
                f"stop ${stop_usdt:.2f} ({trigger_gross_bps:+.0f} bps gross) is at "
                f"or below the catastrophe stop {cata_stop:+.0f} bps — redundant.")},
                status_code=400)
        with bot._pos_lock:
            pos.manual_stop_usdt = stop_usdt
        bot._save_state()
        log.info("[%s] MANUAL_STOP %s: set at $%.2f", bot.id, sym, stop_usdt)
        return JSONResponse({"status": "set", "symbol": sym,
                             "stop_usdt": round(stop_usdt, 2),
                             "stop_bps": round(trigger_gross_bps, 0)})

    @app.post("/bot/{bot_id}/api/manual_open")
    async def api_manual_open(bot_id: str, request: Request):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        p = bot.p
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        sym = (body.get("symbol") or "").upper()
        dir_str = (body.get("direction") or "").upper()
        size = body.get("size_usdt")
        strategy = (body.get("strategy") or "MANUAL").upper()
        hold_h = body.get("hold_hours", p.hold_hours_default)
        user_stop = body.get("stop_bps")
        stop_bps_in = user_stop if user_stop is not None else p.stop_loss_bps

        if sym not in p.trade_symbols:
            return JSONResponse({"error": f"symbol must be one of {sorted(p.trade_symbols)}"},
                                status_code=400)
        if dir_str not in ("LONG", "SHORT"):
            return JSONResponse({"error": "direction must be 'LONG' or 'SHORT'"},
                                status_code=400)
        try:
            size = float(size)
        except (TypeError, ValueError):
            return JSONResponse({"error": "size_usdt must be a number"}, status_code=400)
        if not math.isfinite(size) or size < 10:
            return JSONResponse({"error": "size_usdt must be >= $10"}, status_code=400)
        if 0 < p.max_notional_per_trade < size:
            return JSONResponse({"error": (
                f"size_usdt ${size:.0f} exceeds max_notional_per_trade "
                f"${p.max_notional_per_trade:.0f}.")}, status_code=400)
        try:
            hold_h = float(hold_h)
        except (TypeError, ValueError):
            return JSONResponse({"error": "hold_hours must be a number"}, status_code=400)
        if not math.isfinite(hold_h) or hold_h <= 0 or hold_h > 168:
            return JSONResponse({"error": "hold_hours must be in (0, 168]"}, status_code=400)
        try:
            stop_bps_in = float(stop_bps_in)
        except (TypeError, ValueError):
            return JSONResponse({"error": "stop_bps must be a number"}, status_code=400)
        if not math.isfinite(stop_bps_in) or stop_bps_in >= 0:
            return JSONResponse({"error": "stop_bps must be a finite negative number"},
                                status_code=400)
        if strategy == "S8" and user_stop is not None and float(user_stop) != p.stop_loss_s8:
            return JSONResponse({"error": (
                f"S8 stop is hardcoded to {p.stop_loss_s8:+.0f} bps by the exit chain; "
                f"use strategy='MANUAL' for a custom stop.")}, status_code=400)
        if bot._paused:
            return JSONResponse({"error": "bot is paused (halted) — resume entries first"},
                                status_code=400)
        st = bot.states.get(sym)
        if not st or st.price <= 0:
            return JSONResponse({"error": f"no live price for {sym} yet"}, status_code=400)

        direction = 1 if dir_str == "LONG" else -1
        now = datetime.now(timezone.utc)
        with bot._pos_lock:
            if sym in bot.positions:
                return JSONResponse({"error": f"{sym} already has an open position"},
                                    status_code=400)
            if sym in bot._inflight_open:
                return JSONResponse({"error": f"{sym} has an in-flight manual_open"},
                                    status_code=400)
            if len(bot.positions) + len(bot._inflight_open) >= p.max_positions:
                return JSONResponse({"error": f"max_positions={p.max_positions} reached"},
                                    status_code=400)
            bot._inflight_open.add(sym)
        try:
            # SDK bloquant (timeout 20s + retries 429 ~20s) — JAMAIS sur
            # l'event loop : il porte les ticks d'exit de tous les bots.
            fill = await asyncio.to_thread(
                bot.broker.open, sym, direction, size, st.price)
            entry_price, filled_size = fill.avg_px, fill.size_usdt
            target_exit = now + timedelta(hours=hold_h)
            with bot._pos_lock:
                bot.positions[sym] = Position(
                    symbol=sym, direction=direction, strategy=strategy,
                    entry_price=entry_price, entry_time=now,
                    size_usdt=filled_size,
                    signal_info=f"manual_open hold={hold_h}h",
                    target_exit=target_exit, trajectory=[(0.0, 0.0)],
                    stop_bps=stop_bps_in)
            bot.db.log_event("OPEN", sym, {
                "strategy": strategy, "dir": dir_str,
                "entry_price": round(entry_price, 6),
                "size_usdt": round(filled_size, 2),
                "target_exit": target_exit.isoformat(),
                "stop_bps": round(stop_bps_in, 1), "manual": True})
            bot._save_state()
            log.info("[%s] MANUAL OPEN %s %s %s @ $%.4f | $%.0f", bot.id,
                     strategy, dir_str, sym, entry_price, filled_size)
            return JSONResponse({
                "status": "ok", "symbol": sym, "direction": dir_str,
                "strategy": strategy, "entry_price": round(entry_price, 6),
                "size_usdt": round(filled_size, 2),
                "stop_bps": round(stop_bps_in, 1),
                "target_exit": target_exit.isoformat()})
        finally:
            with bot._pos_lock:
                bot._inflight_open.discard(sym)

    @app.post("/bot/{bot_id}/api/capital")
    async def api_capital(bot_id: str, request: Request):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        amount = body.get("amount")
        if amount is None:
            return JSONResponse({"error": "missing 'amount' field"}, status_code=400)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return JSONResponse({"error": "amount must be a number"}, status_code=400)
        if not math.isfinite(amount) or amount == 0:
            return JSONResponse({"error": "amount must be finite and non-zero"},
                                status_code=400)
        cap = bot.cfg.capital_cap
        if cap > 0 and amount > 0 and bot._capital + amount > cap:
            room = round(cap - bot._capital, 2)
            return JSONResponse({"error": (
                f"Deposit refused: capital cap ${cap:.0f}. "
                f"Maximum deposit right now: ${max(room, 0):.0f}."),
                "max_dca": max(room, 0.0)}, status_code=400)
        if amount < 0 and bot._capital + amount < 0:
            return JSONResponse({"error": (
                f"Withdrawal ${-amount:.0f} exceeds capital ${bot._capital:.0f}.")},
                status_code=400)
        old_capital = bot._capital
        with bot._pos_lock:
            bot._capital += amount
            bot._peak_balance = bot._capital + bot._total_pnl  # DCA rebases peak
        bot._save_state()
        log.info("[%s] CAPITAL: $%.0f → $%.0f (%+.0f)", bot.id, old_capital,
                 bot._capital, amount)
        bot.notifier.send(f"💰 Capital adjusted: ${old_capital:.0f} → "
                          f"${bot._capital:.0f} ({amount:+.0f})", category="admin")
        return JSONResponse({"status": "ok", "old": round(old_capital, 2),
                             "new": round(bot._capital, 2), "amount": amount})

    @app.post("/bot/{bot_id}/api/pause")
    def api_pause(bot_id: str):
        """Close ALL positions + pause (legacy destructive pause)."""
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        now, failed = datetime.now(timezone.utc), []
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0:
                if not bot.close_and_check(sym, st.price, now, "manual_stop"):
                    failed.append(sym)
        bot._paused = True
        bot._save_state()
        resp = {"status": "paused"}
        if failed:
            resp["warning"] = f"failed to close: {failed}"
        return JSONResponse(resp)

    @app.post("/bot/{bot_id}/api/resume")
    async def api_resume(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        bot._paused = False
        bot._save_state()
        return JSONResponse({"status": "resumed"})

    @app.post("/bot/{bot_id}/api/halt_entries")
    async def api_halt_entries(bot_id: str):
        """Non-destructive pause: blocks NEW entries, exits keep running."""
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        bot._paused = True
        bot._save_state()
        log.info("[%s] HALT_ENTRIES", bot.id)
        return JSONResponse({"status": "halted"})

    @app.post("/bot/{bot_id}/api/resume_entries")
    async def api_resume_entries(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        bot._paused = False
        bot._save_state()
        log.info("[%s] RESUME_ENTRIES", bot.id)
        return JSONResponse({"status": "resumed"})

    @app.post("/bot/{bot_id}/api/strategy_toggle")
    async def api_strategy_toggle(bot_id: str, payload: dict):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        strat = str(payload.get("strat", "")).strip().upper()
        direction = str(payload.get("dir", "")).strip().upper()
        paused = bool(payload.get("paused"))
        if strat not in {"S1", "S5", "S8", "S9", "S10"} or direction not in {"LONG", "SHORT"}:
            return JSONResponse({"status": "error",
                                 "error": f"invalid strat={strat} or dir={direction}"},
                                status_code=400)
        key = (strat, direction)
        if paused:
            bot._paused_strats.add(key)
        else:
            bot._paused_strats.discard(key)
        bot._save_state()
        log.info("[%s] strategy toggle: %s %s → paused=%s", bot.id, strat, direction, paused)
        return JSONResponse({"status": "ok",
                             "paused_strategies": sorted([list(q) for q in bot._paused_strats])})

    @app.post("/bot/{bot_id}/api/reset")
    def api_reset(bot_id: str):
        bot = _bot(bot_id)
        if not bot:
            return NOT_FOUND
        now = datetime.now(timezone.utc)
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0:
                bot.close_position(sym, st.price, now, "reset")
        with bot._pos_lock:
            bot._total_pnl, bot._wins, bot._peak_balance = 0.0, 0, bot._capital
            bot._consecutive_losses = 0
            bot._paused = False
            bot._cooldowns.clear()
            bot.trades.clear()
            bot._signal_first_seen.clear()
        with bot.db.lock:
            bot.db.conn.execute("DELETE FROM trades")
            bot.db.conn.execute("DELETE FROM trajectories")
            bot.db.conn.commit()
        bot._save_state()
        log.info("[%s] RESET: capital $%.0f, all state cleared", bot.id, bot._capital)
        return JSONResponse({"status": "reset"})

    return app
