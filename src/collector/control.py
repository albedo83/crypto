"""PG LISTEN for dashboard commands."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

import asyncpg
import orjson

from src.shared.constants import CONTROL_CHANNEL

logger = logging.getLogger(__name__)

CommandCallback = Callable[[dict], Awaitable[None]]


class ControlListener:
    """Listens to PostgreSQL NOTIFY for dashboard control commands."""

    def __init__(self, pool: asyncpg.Pool, callback: CommandCallback) -> None:
        self._pool = pool
        self._callback = callback
        self._running = False
        self._task: asyncio.Task | None = None
        self._conn: asyncpg.Connection | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._listen_loop(), name="control_listener")
        logger.info("ControlListener started on channel '%s'", CONTROL_CHANNEL)

    async def stop(self) -> None:
        self._running = False
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(self) -> None:
        while self._running:
            try:
                self._conn = await self._pool.acquire()
                await self._conn.add_listener(CONTROL_CHANNEL, self._on_notify)
                logger.info("Listening on '%s'", CONTROL_CHANNEL)

                # Keep connection alive
                while self._running:
                    await asyncio.sleep(1)

            except Exception:
                logger.exception("Control listener error")
                await asyncio.sleep(5)
            finally:
                if self._conn:
                    try:
                        await self._conn.remove_listener(CONTROL_CHANNEL, self._on_notify)
                        await self._pool.release(self._conn)
                    except Exception:
                        pass
                    self._conn = None

    def _on_notify(
        self,
        conn: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        try:
            cmd = orjson.loads(payload)
            asyncio.create_task(self._callback(cmd))
        except Exception:
            logger.exception("Failed to process control command: %s", payload)
