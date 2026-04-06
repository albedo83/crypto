"""FastAPI app, routes, auth, and API response builders."""
from __future__ import annotations
import logging, os, time, secrets as _secrets
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .config import (
    VERSION, EXECUTION_MODE, CAPITAL_USDT, LEVERAGE, DASHBOARD_USER,
    DASHBOARD_PASS, HTML_PATH, TRADE_SYMBOLS, TOKEN_SECTOR, TRADES_CSV,
    MAX_POSITIONS, TOTAL_LOSS_CAP, SCAN_INTERVAL,
    HOLD_HOURS_DEFAULT, HOLD_HOURS_S5, COST_BPS, STOP_LOSS_BPS,
    S5_DIV_THRESHOLD, S5_VOL_Z_MIN,
    S8_DRAWDOWN_THRESH, S8_VOL_Z_MIN, S8_RET_24H_THRESH, S8_BTC_7D_THRESH,
    S9_RET_THRESH,
)

log = logging.getLogger("multisignal")

# DRY: shared helpers live in trading.py
from .trading import is_bot_trade, compute_signal_drift

def _collect_active_signals(bot, btc_f) -> list:
    """Scan all symbols and return active signal descriptions for the dashboard."""
    active = []
    if btc_f.get("btc_30d", 0) > 2000:
        active.append(f"S1: BTC 30d = {btc_f['btc_30d']:+.0f}bps \u2192 LONG")
    s5, s8, s9, s10 = [], [], [], []
    for sym in TRADE_SYMBOLS:
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
            s5.append(f"{sym}({'L' if sd['divergence'] > 0 else 'S'})")
        f = bot._get_cached_features(sym) or bot._compute_features(sym)
        if f:
            if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH and f.get("vol_z", 0) > S8_VOL_Z_MIN
                    and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                    and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
                s8.append(sym)
            if abs(f.get("ret_24h", 0)) >= S9_RET_THRESH:
                s9.append(f"{sym}({'S' if f['ret_24h'] > 0 else 'L'})")
        sq = bot._detect_squeeze(sym)
        if sq:
            s10.append(f"{sym}({'L' if sq['direction'] == 1 else 'S'})")
    if s5:  active.append(f"S5: {', '.join(s5[:5])} sector divergence")
    if s8:  active.append(f"S8: {', '.join(s8[:5])} capitulation flush")
    if s9:  active.append(f"S9: {', '.join(s9[:5])} fade extreme")
    if s10: active.append(f"S10: {', '.join(s10[:5])} squeeze")
    return active

# ── Response Builders ─────────────────────────────────────────────────

def build_daily_summary(bot) -> str:
    """Build the daily summary message string. Caller sends via Telegram."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()[:10]
    dt = [t for t in bot.trades if t.exit_time[:10] == yesterday and is_bot_trade(t)]
    dp = sum(t.pnl_usdt for t in dt)
    dw = sum(1 for t in dt if t.pnl_usdt > 0) / len(dt) * 100 if dt else 0
    by_s: dict[str, float] = defaultdict(float)
    for t in dt: by_s[t.strategy] += t.pnl_usdt
    sl = " | ".join(f"{s}: ${p:+.1f}" for s, p in sorted(by_s.items())) if by_s else "no trades"
    bal = CAPITAL_USDT + bot._total_pnl
    dd = (bal - bot._peak_balance) / bot._peak_balance * 100 if bot._peak_balance > 0 else 0
    ab = [t for t in bot.trades if is_bot_trade(t)]
    n, wr = len(ab), (sum(1 for t in ab if t.pnl_usdt > 0) / len(ab) * 100 if ab else 0)
    drift = compute_signal_drift(bot.trades)
    q = [s for s, d in drift.items() if d.get("n", 0) >= 10 and d.get("win_rate", 1) < 0.20]
    dg = f" | DEGRADED: {', '.join(q)}" if q else ""
    return (f"\U0001f4ca Daily {yesterday}\n"
            f"{len(dt)} trades | ${dp:+.2f} | {dw:.0f}% win\n{sl}\n"
            f"Balance: ${bal:.0f} | P&L: ${bot._total_pnl:+.2f} ({wr:.0f}% on {n})\n"
            f"DD: {dd:+.1f}% from peak | {len(bot.positions)} pos open{dg}")

def build_state_response(bot) -> dict:
    """Full dashboard state dict."""
    now = datetime.now(timezone.utc)
    bt = [t for t in bot.trades if is_bot_trade(t)]
    n_bot, wins = len(bt), sum(1 for t in bt if t.pnl_usdt > 0)
    balance = CAPITAL_USDT + bot._total_pnl
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()
    positions = []
    with bot._pos_lock:
        pos_snapshot = dict(bot.positions)
    for sym, pos in pos_snapshot.items():
        st = bot.states.get(sym)
        px = st.price if st else pos.entry_price
        ur = pos.direction * (px / pos.entry_price - 1) * 1e4 * LEVERAGE if pos.entry_price > 0 else 0
        rem = max(0, (pos.target_exit - now).total_seconds() / 3600)
        positions.append({
            "symbol": sym, "direction": "LONG" if pos.direction == 1 else "SHORT",
            "strategy": pos.strategy, "entry_price": pos.entry_price,
            "current_price": px, "size_usdt": pos.size_usdt,
            "signal_info": pos.signal_info, "unrealized_bps": round(ur, 1),
            "pnl_usdt": round(pos.size_usdt * ur / 1e4, 2),
            "hold_hours": round((now - pos.entry_time).total_seconds() / 3600, 1),
            "remaining_hours": round(rem, 1),
            "remaining": f"{int(rem)}h{int((rem % 1) * 60):02d}m",
            "mae_bps": round(pos.mae_bps, 1), "mfe_bps": round(pos.mfe_bps, 1),
        })
    return {
        "version": VERSION, "strategy": "Multi-Signal (S1+S5+S8+S9+S10)",
        "execution_mode": EXECUTION_MODE,
        "paused": bot._paused, "running": bot.running,
        "degraded": list(bot._degraded), "loss_streak": bot._consecutive_losses,
        "kill_switch_active": bot._total_pnl <= TOTAL_LOSS_CAP,
        "balance": round(balance, 2), "capital": CAPITAL_USDT,
        "total_pnl": round(bot._total_pnl, 2), "total_trades": n_bot,
        "win_rate": round(wins / n_bot, 3) if n_bot > 0 else 0,
        "n_positions": len(pos_snapshot), "max_positions": MAX_POSITIONS,
        "positions": positions, "active_signals": _collect_active_signals(bot, btc_f),
        "market": {
            "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "btc_7d": round(btc_f.get("btc_7d", 0), 0),
            "alt_index_7d": round(alt_idx, 0), "dxy_7d": round(bot._dxy_cache[0], 0),
            "oi_falling": bot._oi_summary["falling"],
            "oi_rising": bot._oi_summary["rising"],
        },
        "params": {"hold_h": HOLD_HOURS_DEFAULT, "hold_s5_h": HOLD_HOURS_S5,
                   "cost_bps": COST_BPS, "stop_bps": STOP_LOSS_BPS,
                   "max_pos": MAX_POSITIONS},
        "uptime_s": (now - bot.started_at).total_seconds() if bot.started_at else 0,
        "last_price_s": time.time() - bot._last_price_fetch if bot._last_price_fetch else None,
        "last_scan_s": time.time() - bot._last_scan if bot._last_scan else None,
        "next_scan_s": max(0, SCAN_INTERVAL - (time.time() - bot._last_scan)) if bot._last_scan else 0,
        "scan_interval": SCAN_INTERVAL,
        "signal_drift": compute_signal_drift(bot.trades),
        "peak_balance": round(bot._peak_balance, 2),
        "drawdown_pct": round((balance - bot._peak_balance) / bot._peak_balance * 100, 2) if bot._peak_balance > 0 else 0,
        "capital_utilization_pct": round(sum(p.size_usdt for p in pos_snapshot.values()) / max(balance, 1) * 100, 1),
    }

def build_signals_response(bot) -> dict:
    """All symbols with their current features and signal status."""
    btc_f, alt_idx = bot._compute_btc_features(), bot._compute_alt_index()
    signals = {}
    for sym in TRADE_SYMBOLS:
        st = bot.states.get(sym)
        f = bot._get_cached_features(sym) or bot._compute_features(sym)
        if not st or not f:
            continue
        triggered = []
        if btc_f.get("btc_30d", 0) > 2000:
            triggered.append("S1:LONG")
        sd = bot._compute_sector_divergence(sym)
        if sd and abs(sd["divergence"]) >= S5_DIV_THRESHOLD and sd["vol_z"] >= S5_VOL_Z_MIN:
            triggered.append(f"S5:{'LONG' if sd['divergence'] > 0 else 'SHORT'}")
        if (f.get("drawdown", 0) < S8_DRAWDOWN_THRESH and f.get("vol_z", 0) > S8_VOL_Z_MIN
                and f.get("ret_24h", 0) < S8_RET_24H_THRESH
                and btc_f.get("btc_7d", 0) < S8_BTC_7D_THRESH):
            triggered.append("S8:LONG")
        oi_f = bot._compute_oi_features(sym)
        crowd = bot._compute_crowding_score(sym, oi_f=oi_f)
        pos = bot.positions.get(sym)
        signals[sym] = {
            "price": st.price, "ret_7d_bps": round(f.get("ret_42h", 0), 1),
            "vol_ratio": round(f.get("vol_ratio", 0), 2),
            "range_bps": round(f.get("range_pct", 0), 0),
            "sector": TOKEN_SECTOR.get(sym, "?"),
            "sector_div": round(sd["divergence"], 0) if sd else 0,
            "oi_delta_1h": oi_f["oi_delta_1h"], "funding_bps": oi_f["funding_bps"],
            "crowding": crowd, "triggered": triggered,
            "in_position": pos is not None,
            "position_strategy": pos.strategy if pos else None,
        }
    return {"signals": signals, "btc_30d": round(btc_f.get("btc_30d", 0), 0),
            "alt_index": round(alt_idx, 0)}

def build_trades_list(trades, limit: int = 50) -> list:
    """Return recent trades, newest first. Converts deque to list (no slicing on deque)."""
    tl = list(trades)
    return [t.__dict__ for t in tl[-limit:][::-1]]

def build_pnl_curve(trades) -> list:
    """Cumulative P&L curve for the dashboard chart."""
    cum, pts = 0.0, []
    for t in trades:
        cum += t.pnl_usdt
        pts.append({"time": t.exit_time, "cum_pnl": round(cum, 2),
                    "balance": round(CAPITAL_USDT + cum, 2)})
    return pts

# ── FastAPI App Factory ───────────────────────────────────────────────

def create_app(bot) -> FastAPI:
    """Create the FastAPI app with all routes wired to *bot*."""
    _security = HTTPBasic(auto_error=False)
    _deny = HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    def _check_auth(creds: HTTPBasicCredentials | None = Depends(_security)):
        if not DASHBOARD_USER: return
        if creds is None: raise _deny
        if not (_secrets.compare_digest(creds.username, DASHBOARD_USER)
                and _secrets.compare_digest(creds.password, DASHBOARD_PASS)):
            raise _deny

    app = FastAPI(dependencies=[Depends(_check_auth)] if DASHBOARD_USER else [])
    _html_cache: dict[str, str | None] = {"v": None}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if _html_cache["v"] is None:
            if os.path.exists(HTML_PATH):
                ml = "LIVE" if EXECUTION_MODE == "live" else "PAPER"
                mc = "#da3633" if EXECUTION_MODE == "live" else "#58a6ff"
                _html_cache["v"] = (Path(HTML_PATH).read_text()
                                    .replace("{{VERSION}}", VERSION)
                                    .replace("{{MODE}}", ml)
                                    .replace("{{MODE_COLOR}}", mc))
            else:
                _html_cache["v"] = (
                    f'<html><body style="background:#0d1117;color:#e6edf3;font-family:monospace">'
                    f"<h1>Multi-Signal Bot v{VERSION}</h1>"
                    f'<pre id="s"></pre><script>'
                    f"setInterval(()=>fetch('/api/state').then(r=>r.json()).then("
                    f"d=>document.getElementById('s').textContent=JSON.stringify(d,null,2)),5000);"
                    f"</script></body></html>")
        return _html_cache["v"]

    @app.get("/api/health")
    async def api_health():
        pa = time.time() - bot._last_price_fetch if bot._last_price_fetch else 9999
        sa = time.time() - bot._last_scan if bot._last_scan else 9999
        stale = pa > 300 or sa > 7200
        status = "stale" if stale else ("degraded" if bot._degraded else "ok")
        return JSONResponse({
            "status": status, "price_age_s": round(pa, 0), "scan_age_s": round(sa, 0),
            "exchange_ok": bot._exchange is not None if EXECUTION_MODE == "live" else True,
            "degraded": list(bot._degraded),
            "positions_count": len(bot.positions), "paused": bot._paused,
        }, status_code=503 if stale else 200)

    @app.get("/api/chart/{symbol}")
    async def api_chart(symbol: str, hours: int = 24):
        """Price history for chart: 4h candles + 60s ticks merged."""
        from .config import ALL_SYMBOLS as _all_syms
        if symbol not in _all_syms:
            return JSONResponse({"symbol": symbol, "points": [], "position": None})
        hours = max(1, min(hours, 168))

        def _build_chart():
            pts = []
            st = bot.states.get(symbol)
            if st and st.candles_4h:
                cutoff = time.time() - hours * 3600
                for c in st.candles_4h:
                    if c["t"] / 1000 >= cutoff:
                        pts.append({"ts": c["t"] // 1000, "price": c["c"]})
            if bot._db:
                try:
                    cutoff_ts = int(time.time() - hours * 3600)
                    rows = bot._db.execute(
                        "SELECT ts, mark_px FROM ticks WHERE symbol=? AND ts>? ORDER BY ts",
                        (symbol, cutoff_ts)).fetchall()
                    tick_start = rows[0][0] if rows else 0
                    pts_filtered = [p for p in pts if p["ts"] < tick_start]
                    pts_filtered.extend({"ts": r[0], "price": r[1]} for r in rows)
                    return pts_filtered
                except Exception:
                    pass
            return pts

        import asyncio
        pts = await asyncio.to_thread(_build_chart)
        pos_info = None
        pos = bot.positions.get(symbol)
        if pos:
            pos_info = {"entry_price": pos.entry_price, "direction": "LONG" if pos.direction == 1 else "SHORT",
                        "strategy": pos.strategy, "entry_ts": int(pos.entry_time.timestamp())}
        return JSONResponse({"symbol": symbol, "points": pts, "position": pos_info})

    @app.get("/api/state")
    def api_state(): return JSONResponse(build_state_response(bot))  # sync — numpy in threadpool
    @app.get("/api/signals")
    def api_signals(): return JSONResponse(build_signals_response(bot))  # sync — numpy in threadpool
    @app.get("/api/trades")
    async def api_trades(limit: int = 50): return JSONResponse(build_trades_list(bot.trades, limit))
    @app.get("/api/pnl")
    async def api_pnl(): return JSONResponse(build_pnl_curve(bot.trades))

    @app.post("/api/pause")
    def api_pause():  # sync -- FastAPI runs in threadpool
        now, failed = datetime.now(timezone.utc), []
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0:
                bot._close_position(sym, st.price, now, "manual_stop")
                if sym in bot.positions:
                    failed.append(sym)
        bot._paused = True
        bot._save_state()
        resp = {"status": "paused"}
        if failed:
            log.warning("PAUSE: %d positions failed to close: %s", len(failed), failed)
            resp["warning"] = f"failed to close: {failed}"
        return JSONResponse(resp)

    @app.post("/api/resume")
    async def api_resume():
        bot._paused, bot._last_scan = False, 0  # force immediate scan
        bot._save_state()
        return JSONResponse({"status": "resumed"})

    @app.post("/api/reset")
    def api_reset():  # sync -- FastAPI runs in threadpool
        now = datetime.now(timezone.utc)
        for sym in list(bot.positions.keys()):
            st = bot.states.get(sym)
            if st and st.price > 0: bot._close_position(sym, st.price, now, "reset")
        bot._total_pnl, bot._wins, bot._peak_balance = 0.0, 0, CAPITAL_USDT
        bot._consecutive_losses, bot._loss_streak_until = 0, 0
        bot._paused, bot._last_scan = False, 0
        for c in (bot._cooldowns, bot.trades, bot._degraded,
                  bot._feature_cache, bot._signal_first_seen): c.clear()
        bot._oi_summary = {"falling": 0, "rising": 0}
        if os.path.exists(TRADES_CSV):
            os.rename(TRADES_CSV, TRADES_CSV + f".bak.{int(time.time())}")
        bot._save_state()
        log.info("RESET: capital $%.0f, all state cleared", CAPITAL_USDT)
        return JSONResponse({"status": "reset"})

    return app
