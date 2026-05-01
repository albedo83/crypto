"""Admin panel — multi-bot overview on :8080."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import secrets as _secrets

import aiohttp
from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path

load_dotenv()
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")
AUTH_SALT = os.environ.get("AUTH_SALT", "")  # v11.4.6: salt mixed into bot HMAC secrets
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8090"))
ROOT_PATH = os.environ.get("ADMIN_ROOT_PATH", "")  # e.g. "/crypto" when behind nginx subpath

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "admin_config.json")
HTML_PATH = os.path.join(os.path.dirname(__file__), "admin.html")

log = logging.getLogger("admin")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ADMIN] %(message)s", datefmt="%H:%M:%S")

# Load bot config
with open(CONFIG_PATH) as f:
    _config = json.load(f)
_allowed_ports = {b["port"] for b in _config["bots"]}

# Per-bot credentials for authenticating admin → bot HTTP calls.
# admin_config.json stores env var NAMES (not values) per bot; we resolve them
# here. Missing values fall back to DASHBOARD_USER/PASS for backward-compat.
_bot_creds: dict[int, tuple[str, str]] = {}
for _b in _config["bots"]:
    _env = _b.get("auth_env", {})
    _u = os.environ.get(_env.get("user", "DASHBOARD_USER"), DASHBOARD_USER)
    _p = os.environ.get(_env.get("pass", "DASHBOARD_PASS"), DASHBOARD_PASS)
    _bot_creds[_b["port"]] = (_u, _p)
    if not _p:
        log.warning("No password configured for bot on port %d (env %s)",
                    _b["port"], _env.get("pass", "DASHBOARD_PASS"))

# ── Stateless signed sessions (survive restarts) ──
_SECRET = hashlib.sha256(DASHBOARD_PASS.encode()).digest() if DASHBOARD_PASS else b""
# Per-bot HMAC secrets for signing auto-login tokens. Each bot computes its own
# secret as sha256(password + AUTH_SALT); the admin signs tokens identically so
# the bot's /auth endpoint accepts them without a fresh login.
_bot_secrets: dict[int, bytes] = {}
for _p, (_u, _pw) in _bot_creds.items():
    _bot_secrets[_p] = (hashlib.sha256((_pw + AUTH_SALT).encode()).digest()
                        if _pw else b"")
_SESSION_MAX_AGE = 30 * 86400
_login_attempts: dict[str, list[float]] = {}
_LOGIN_RATE_WINDOW = 300
_LOGIN_RATE_MAX = 10

def _sign_token(ts: float) -> str:
    msg = str(int(ts)).encode()
    sig = hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()[:16]
    return f"{int(ts)}:{sig}"

def _verify_token(token: str) -> bool:
    if not token or ":" not in token:
        return False
    parts = token.split(":", 1)
    if len(parts) != 2:
        return False
    ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > _SESSION_MAX_AGE:
        return False
    expected = hmac.new(_SECRET, ts_str.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(sig, expected)

def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_RATE_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_RATE_MAX

# ── Bot proxy auth ──
_bot_cookies: dict[int, str] = {}  # port -> session cookie

async def _bot_auth(port: int) -> str:
    """Login to a bot instance and cache its session cookie.

    Uses per-bot credentials from _bot_creds (populated from admin_config.json's
    auth_env mapping). Falls back to DASHBOARD_USER/PASS if no mapping exists.
    """
    user, password = _bot_creds.get(port, (DASHBOARD_USER, DASHBOARD_PASS))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            resp = await session.post(
                f"http://localhost:{port}/login",
                data={"username": user, "password": password},
                allow_redirects=False,
            )
            cookie = resp.cookies.get("session")
            if cookie:
                _bot_cookies[port] = cookie.value
                return cookie.value
    except Exception:
        pass
    raise HTTPException(502, f"Cannot authenticate with bot on :{port}")

async def _bot_fetch(port: int, path: str) -> dict | None:
    """Fetch JSON from a bot API, handling auth and errors."""
    try:
        if port not in _bot_cookies:
            await _bot_auth(port)
    except Exception:
        return None
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession(cookies={"session": _bot_cookies.get(port, "")}) as session:
                async with session.get(f"http://localhost:{port}/api/{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 401 and attempt == 0:
                        try:
                            await _bot_auth(port)
                        except Exception:
                            return None
                        continue
                    if resp.status < 400:
                        return await resp.json()
                    return None
        except Exception:
            return None
    return None

# ── FastAPI App ──
app = FastAPI(root_path=ROOT_PATH)

if DASHBOARD_USER:
    from starlette.middleware.base import BaseHTTPMiddleware
    class _AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path in ("/login", "/favicon.ico"):
                return await call_next(request)
            token = request.cookies.get("admin_session")
            if not token or not _verify_token(token):
                if path.startswith("/api/") or path.startswith("/proxy/"):
                    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
                return RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
            return await call_next(request)
    app.add_middleware(_AuthMiddleware)

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Admin Panel</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:360px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.login-box h1{font-size:20px;margin-bottom:8px;text-align:center}
.login-box .sub{color:#7d8590;font-size:13px;text-align:center;margin-bottom:24px}
.login-box label{display:block;font-size:13px;color:#7d8590;margin-bottom:4px}
.login-box input[type=text],.login-box input[type=password]{
  width:100%;padding:10px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;
  color:#e6edf3;font-size:15px;margin-bottom:16px;outline:none;transition:border .2s}
.login-box input:focus{border-color:#58a6ff}
.login-box button{width:100%;padding:10px;background:#238636;color:#fff;border:none;border-radius:6px;
  font-size:15px;font-weight:600;cursor:pointer;transition:background .2s}
.login-box button:hover{background:#2ea043}
.error{background:#da363322;border:1px solid #da363388;color:#f85149;padding:8px 12px;
  border-radius:6px;margin-bottom:16px;font-size:13px;text-align:center;display:none}
</style>
</head><body>
<form class="login-box" method="POST" action="login" autocomplete="on">
  <h1>Admin Panel</h1>
  <div class="sub">Trading Bots</div>
  <div class="error" id="err">{{ERROR}}</div>
  <label for="username">Username</label>
  <input type="text" id="username" name="username" autocomplete="username" required autofocus>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
</form>
<script>if(document.getElementById('err').textContent.trim())document.getElementById('err').style.display='block'</script>
</body></html>"""

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_HTML.replace("{{ERROR}}", "")

@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        return HTMLResponse(_LOGIN_HTML.replace("{{ERROR}}", "Too many attempts"), status_code=429)
    _login_attempts.setdefault(client_ip, []).append(time.time())
    if (_secrets.compare_digest(username, DASHBOARD_USER)
            and _secrets.compare_digest(password, DASHBOARD_PASS)):
        token = _sign_token(time.time())
        resp = RedirectResponse(f"{ROOT_PATH}/", status_code=303)
        resp.set_cookie("admin_session", token, httponly=True, samesite="strict", max_age=30 * 86400)
        return resp
    return HTMLResponse(_LOGIN_HTML.replace("{{ERROR}}", "Invalid credentials"), status_code=401)

@app.get("/logout")
async def logout(admin_session: str | None = Cookie(None)):
    _ = admin_session  # unused, token is stateless
    resp = RedirectResponse(f"{ROOT_PATH}/login", status_code=303)
    resp.delete_cookie("admin_session")
    return resp

_html_cache: str | None = None

@app.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        _html_cache = Path(HTML_PATH).read_text()
    return _html_cache

@app.get("/api/auth-token")
async def api_auth_token(port: int = 0):
    """Generate a signed token for bot auto-login.

    Without `port`: signed with admin's own secret (legacy, does not work for
    bot `/auth` endpoints now that each bot uses a salted secret).
    With `port`: signed with that specific bot's secret so the bot's `/auth`
    endpoint accepts it and sets its session cookie.
    """
    if port and port in _bot_secrets:
        secret = _bot_secrets[port]
        if not secret:
            return JSONResponse({"error": "no password for bot"}, status_code=400)
        ts = int(time.time())
        sig = hmac.new(secret, str(ts).encode(), hashlib.sha256).hexdigest()[:16]
        return JSONResponse({"token": f"{ts}:{sig}"})
    return JSONResponse({"token": _sign_token(time.time())})

@app.get("/api/bots")
async def api_bots():
    """Fetch health + state from each bot, return combined."""
    results = []
    for bot in _config["bots"]:
        port = bot["port"]
        info = {"port": port, "label": bot["label"], "mode": bot["mode"],
                "path": bot.get("path", ""), "online": False}
        health = await _bot_fetch(port, "health")
        if health:
            info["online"] = True
            info["status"] = health.get("status", "unknown")
            info["positions_count"] = health.get("positions_count", 0)
            info["paused"] = health.get("paused", False)
            info["price_age_s"] = health.get("price_age_s", 9999)
            state = await _bot_fetch(port, "state")
            if state:
                info["version"] = state.get("version", "?")
                # Source of truth for the mode is the bot itself (its
                # EXECUTION_MODE env). admin_config.json is just a fallback
                # when the bot is offline. This auto-corrects misconfigured
                # static mode labels (e.g. Junior was "paper" before it
                # went live in v11.7.18).
                _exec = state.get("execution_mode")
                if _exec in ("live", "paper"):
                    info["mode"] = _exec
                info["balance"] = state.get("balance", 0)
                info["capital"] = state.get("capital", 0)
                info["total_pnl"] = state.get("total_pnl", 0)
                info["total_trades"] = state.get("total_trades", 0)
                info["win_rate"] = state.get("win_rate", 0)
                info["n_positions"] = state.get("n_positions", 0)
                info["max_positions"] = state.get("max_positions", 6)
                info["uptime_s"] = state.get("uptime_s", 0)
                info["drawdown_pct"] = state.get("drawdown_pct", 0)
                info["peak_balance"] = state.get("peak_balance", 0)
                info["pnl_pct"] = state.get("pnl_pct", 0)
                info["first_trade_date"] = state.get("first_trade_date")
                info["exchange_account"] = state.get("exchange_account")
                info["active_signals"] = state.get("active_signals", [])
                info["bot_label_color"] = state.get("bot_label_color")
                # Forward open positions (essentials only — admin mobile view)
                positions = state.get("positions", [])
                info["positions"] = [{
                    "symbol": p.get("symbol"),
                    "direction": p.get("direction"),
                    "strategy": p.get("strategy"),
                    "pnl_usdt": p.get("pnl_usdt", 0),
                    "unrealized_bps": p.get("unrealized_bps", 0),
                    "remaining": p.get("remaining", ""),
                    "remaining_hours": p.get("remaining_hours", 0),
                    "hold_hours": p.get("hold_hours", 0),
                    "stop_bps": p.get("stop_bps"),
                    "mae_bps": p.get("mae_bps", 0),
                    "mfe_bps": p.get("mfe_bps", 0),
                    "trailing_active": p.get("trailing_active", False),
                    "trailing_floor_bps": p.get("trailing_floor_bps"),
                } for p in positions]
        results.append(info)
    return JSONResponse(results)

@app.get("/proxy/{port}/api/{path:path}")
async def proxy_get(port: int, path: str):
    if port not in _allowed_ports:
        raise HTTPException(403, "Port not allowed")
    data = await _bot_fetch(port, path)
    if data is None:
        raise HTTPException(502, f"Bot on :{port} unreachable")
    return JSONResponse(data)


_ADMIN_POST_WHITELIST = {"pause", "resume", "reset"}

@app.post("/proxy/{port}/api/{path:path}")
async def proxy_post(port: int, path: str):
    """Admin-only control plane: forward STOP/RESUME/RAZ to a specific bot."""
    if port not in _allowed_ports:
        raise HTTPException(403, "Port not allowed")
    if path not in _ADMIN_POST_WHITELIST:
        raise HTTPException(403, f"Path not allowed: {path}")
    # Ensure we have a session cookie for the bot
    if port not in _bot_cookies:
        await _bot_auth(port)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            resp = await session.post(
                f"http://localhost:{port}/api/{path}",
                cookies={"session": _bot_cookies[port]},
            )
            if resp.status == 401:  # cookie expired — reauth once
                await _bot_auth(port)
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s2:
                    resp = await s2.post(
                        f"http://localhost:{port}/api/{path}",
                        cookies={"session": _bot_cookies[port]},
                    )
                    return JSONResponse(await resp.json(), status_code=resp.status)
            data = await resp.json()
            return JSONResponse(data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(502, f"Bot on :{port} unreachable: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=ADMIN_PORT, log_level="warning")
