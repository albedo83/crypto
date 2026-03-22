"""WebSocket connection manager with reconnect and 24h rotation."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import websockets
import websockets.asyncio.client

from src.config import settings

logger = logging.getLogger(__name__)

# 23 hours - rotate before Binance's 24h limit
ROTATION_INTERVAL = 23 * 3600
MAX_RECONNECT_DELAY = 60
INITIAL_RECONNECT_DELAY = 1


class WSManager:
    """Manages a single combined WebSocket connection to Binance Futures."""

    def __init__(self) -> None:
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._connected = False
        self._connect_time: float = 0
        self._reconnect_delay = INITIAL_RECONNECT_DELAY
        self._should_run = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def uptime_seconds(self) -> float:
        if not self._connected:
            return 0
        return time.monotonic() - self._connect_time

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        url = settings.ws_combined_url
        logger.info("Connecting to %s", url[:80] + "...")
        self._ws = await websockets.asyncio.client.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            max_size=10 * 1024 * 1024,  # 10MB
            close_timeout=5,
        )
        self._connected = True
        self._connect_time = time.monotonic()
        self._reconnect_delay = INITIAL_RECONNECT_DELAY
        logger.info("WebSocket connected")

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("WebSocket disconnected")

    def _needs_rotation(self) -> bool:
        """Check if connection should be rotated (approaching 24h limit)."""
        return self._connected and self.uptime_seconds > ROTATION_INTERVAL

    async def messages(self) -> AsyncIterator[bytes | str]:
        """Yield messages with auto-reconnect and rotation.

        This is the main loop - handles reconnection with exponential backoff
        and 24h rotation transparently.
        """
        self._should_run = True
        while self._should_run:
            try:
                if not self._connected:
                    await self.connect()

                async for msg in self._ws:
                    yield msg

                    # Check rotation
                    if self._needs_rotation():
                        logger.info("Rotating WebSocket (uptime=%.0fs)", self.uptime_seconds)
                        await self.disconnect()
                        break

                # Clean disconnect (server closed or rotation)
                if self._connected:
                    self._connected = False
                    logger.warning("WebSocket closed by server")

            except websockets.exceptions.ConnectionClosed as e:
                self._connected = False
                logger.warning("WebSocket connection closed: %s", e)
            except Exception:
                self._connected = False
                logger.exception("WebSocket error")

            if not self._should_run:
                break

            # Exponential backoff reconnect
            logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, MAX_RECONNECT_DELAY
            )

    async def stop(self) -> None:
        self._should_run = False
        await self.disconnect()
