"""WebSocket endpoint for live status push."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import orjson

router = APIRouter()
logger = logging.getLogger(__name__)

_clients: set[WebSocket] = set()


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    logger.info("WS client connected (%d total)", len(_clients))

    try:
        # Push heartbeat data every 2 seconds
        while True:
            try:
                pool = websocket.app.state.pool

                hb = await pool.fetchrow(
                    "SELECT ts, ws_connected, streams_active, queue_depths, memory_rss_mb, cpu_percent "
                    "FROM heartbeat ORDER BY ts DESC LIMIT 1"
                )

                prices = await pool.fetch(
                    """SELECT i.symbol, t.close AS price, t.volume, t.trade_count
                       FROM trades_1m t
                       JOIN instruments i ON i.instrument_id = t.instrument_id
                       WHERE t.bucket > now() - INTERVAL '2 minutes'
                       ORDER BY t.bucket DESC"""
                )

                payload = {
                    "type": "status",
                    "heartbeat": dict(hb) if hb else None,
                    "prices": [dict(p) for p in prices],
                }

                await websocket.send_bytes(orjson.dumps(payload, default=str))
                await asyncio.sleep(2)

            except WebSocketDisconnect:
                break
            except Exception:
                logger.exception("WS push error")
                await asyncio.sleep(5)

    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
        logger.info("WS client disconnected (%d remaining)", len(_clients))
