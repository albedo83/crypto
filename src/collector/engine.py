"""Orchestrator: manages lifecycle of all collector components."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone

import aiohttp
import asyncpg

from src.config import settings
from src.shared.db import get_pool, close_pool
from src.shared.instruments import load_instruments
from src.shared import constants
from src.collector.ws_manager import WSManager
from src.collector.writer import BatchWriter
from src.collector.dispatcher import Dispatcher
from src.collector.health import HealthMonitor
from src.collector.control import ControlListener
from src.collector.handlers.open_interest import OIPoller

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = 300  # 5 minutes


class Engine:
    """Main collector engine - orchestrates all components."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._ws_manager = WSManager()
        self._writer: BatchWriter | None = None
        self._dispatcher: Dispatcher | None = None
        self._health: HealthMonitor | None = None
        self._control: ControlListener | None = None
        self._oi_poller: OIPoller | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._snapshot_task: asyncio.Task | None = None
        self._venue_id: int = 1
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Main entry point - run until shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler)

        try:
            await self._start()
            await self._collect()
        except asyncio.CancelledError:
            logger.info("Engine cancelled")
        finally:
            await self._stop()

    def _signal_handler(self) -> None:
        logger.info("Shutdown signal received")
        self._shutdown_event.set()

    async def _start(self) -> None:
        logger.info("Starting collector engine...")

        # Database
        self._pool = await get_pool()
        logger.info("Database pool created")

        # HTTP session for REST snapshots
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )

        # Load venue_id
        row = await self._pool.fetchrow(
            "SELECT venue_id FROM venues WHERE name = $1", settings.venue_name
        )
        if row:
            self._venue_id = row["venue_id"]
            constants.VENUE_ID = self._venue_id
        logger.info("Venue: %s (id=%d)", settings.venue_name, self._venue_id)

        # Load instruments
        instruments = await load_instruments(self._pool)
        logger.info("Loaded %d instruments: %s", len(instruments), list(instruments.keys()))

        # Components
        self._writer = BatchWriter(self._pool)
        self._dispatcher = Dispatcher(self._writer, self._venue_id)
        self._health = HealthMonitor(
            self._pool, self._writer, self._ws_manager,
            book_handler=self._dispatcher.book_levels_handler,
        )
        self._oi_poller = OIPoller(self._writer, self._venue_id)
        self._control = ControlListener(self._pool, self._handle_command)

        # Start components
        await self._writer.start()
        await self._health.start()
        await self._oi_poller.start()
        await self._control.start()
        self._snapshot_task = asyncio.create_task(
            self._snapshot_loop(), name="book_snapshot"
        )

        await self._health.log_event(
            constants.EVENT_START, "info",
            f"Collector started: {settings.symbol_list}",
        )

    async def _collect(self) -> None:
        """Main collection loop."""
        disconnect_ts: datetime | None = None

        async for msg in self._ws_manager.messages():
            if self._shutdown_event.is_set():
                break

            # Log reconnection gap
            if disconnect_ts and self._ws_manager.connected:
                now = datetime.now(timezone.utc)
                await self._health.log_gap(
                    "combined", disconnect_ts, now, "reconnection"
                )
                await self._health.log_event(
                    constants.EVENT_WS_RECONNECT, "info",
                    f"Reconnected after {(now - disconnect_ts).total_seconds():.1f}s",
                )
                disconnect_ts = None

                # Reset book sequence tracking and fetch fresh snapshots
                self._dispatcher.book_levels_handler.reset_state()
                asyncio.create_task(self._fetch_all_snapshots())

            self._dispatcher.dispatch(msg)

        # If we exited the loop due to disconnect
        if not self._ws_manager.connected:
            disconnect_ts = datetime.now(timezone.utc)

    async def _snapshot_loop(self) -> None:
        """Periodically fetch book depth snapshots via REST."""
        while not self._shutdown_event.is_set():
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            if self._ws_manager.connected:
                await self._fetch_all_snapshots()

    async def _fetch_all_snapshots(self) -> None:
        """Fetch depth snapshots for all configured symbols."""
        for symbol in settings.symbol_list:
            try:
                await self._fetch_book_snapshot(symbol)
            except Exception:
                logger.exception("Snapshot fetch failed for %s", symbol)

    async def _fetch_book_snapshot(self, symbol: str) -> None:
        """GET /fapi/v1/depth?symbol=X&limit=20 and insert as snapshot."""
        if not self._http_session:
            return
        url = f"{settings.rest_base_url}/fapi/v1/depth"
        params = {"symbol": symbol, "limit": "20"}
        async with self._http_session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning("Snapshot HTTP %d for %s", resp.status, symbol)
                return
            data = await resp.json()

        recv_ts = datetime.now(timezone.utc)
        handler = self._dispatcher.book_levels_handler
        handler.insert_snapshot(
            symbol=symbol,
            bids=data.get("bids", []),
            asks=data.get("asks", []),
            last_update_id=data.get("lastUpdateId", 0),
            recv_ts=recv_ts,
        )
        logger.debug("Book snapshot inserted for %s (updateId=%s)", symbol, data.get("lastUpdateId"))

    async def _stop(self) -> None:
        logger.info("Stopping collector engine...")

        if self._health:
            await self._health.log_event(
                constants.EVENT_STOP, "info", "Collector stopping"
            )

        await self._ws_manager.stop()

        if self._snapshot_task:
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass

        if self._control:
            await self._control.stop()
        if self._oi_poller:
            await self._oi_poller.stop()
        if self._health:
            await self._health.stop()
        if self._writer:
            await self._writer.stop()
        if self._http_session:
            await self._http_session.close()

        await close_pool()
        logger.info("Collector engine stopped. Writer stats: %s",
                     self._writer.stats if self._writer else "N/A")

    async def _handle_command(self, cmd: dict) -> None:
        """Handle control commands from dashboard."""
        action = cmd.get("action", "")
        logger.info("Control command: %s", cmd)

        if action == "status":
            # Could push status back via NOTIFY
            pass
        elif action == "restart_ws":
            logger.info("Restarting WebSocket by command")
            await self._ws_manager.disconnect()
        else:
            logger.warning("Unknown command: %s", action)
