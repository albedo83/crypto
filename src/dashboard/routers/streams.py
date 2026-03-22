"""GET/POST /api/streams - Stream management."""

from __future__ import annotations

from fastapi import APIRouter, Request
import orjson

from src.shared.constants import CONTROL_CHANNEL

router = APIRouter(tags=["streams"])


@router.get("/streams")
async def get_streams(request: Request):
    pool = request.app.state.pool

    rows = await pool.fetch(
        """SELECT i.instrument_id, i.symbol, i.is_active,
                  ss.is_collecting, ss.last_trade_ts, ss.last_book_ts,
                  ss.last_mark_ts, ss.msg_rate_1m, ss.latency_p50_ms,
                  ss.updated_at
           FROM instruments i
           LEFT JOIN symbol_status ss ON ss.instrument_id = i.instrument_id
           ORDER BY i.symbol"""
    )
    return {"streams": [dict(r) for r in rows]}


@router.post("/streams/{symbol}/toggle")
async def toggle_stream(symbol: str, request: Request):
    """Toggle collection for a symbol via PG NOTIFY."""
    pool = request.app.state.pool

    row = await pool.fetchrow(
        "SELECT instrument_id, is_active FROM instruments WHERE symbol = $1",
        symbol.upper(),
    )
    if not row:
        return {"error": "Symbol not found"}

    new_state = not row["is_active"]
    await pool.execute(
        "UPDATE instruments SET is_active = $1 WHERE symbol = $2",
        new_state, symbol.upper(),
    )

    # Notify collector
    cmd = orjson.dumps({"action": "toggle_symbol", "symbol": symbol.upper(), "active": new_state}).decode()
    await pool.execute(f"NOTIFY {CONTROL_CHANNEL}, $1", cmd)

    return {"symbol": symbol.upper(), "is_active": new_state}


@router.post("/streams/restart-ws")
async def restart_ws(request: Request):
    """Command collector to restart WebSocket."""
    pool = request.app.state.pool
    cmd = orjson.dumps({"action": "restart_ws"}).decode()
    await pool.execute(f"NOTIFY {CONTROL_CHANNEL}, $1", cmd)
    return {"status": "command_sent"}
