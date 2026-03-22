"""REST poller for open interest -> open_interest table."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp

from src.collector.writer import BatchWriter
from src.config import settings
from src.shared.instruments import get_instrument_id

logger = logging.getLogger(__name__)


class OIPoller:
    """Polls Binance Futures REST API for open interest every 5 minutes."""

    POLL_INTERVAL = 300  # 5 minutes

    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id
        self._running = False
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._poll_loop(), name="oi_poller")
        logger.info("OI poller started (interval=%ds)", self.POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()

    async def _poll_loop(self) -> None:
        while self._running:
            for symbol in settings.symbol_list:
                try:
                    await self._fetch_oi(symbol)
                except Exception:
                    logger.exception("OI fetch failed for %s", symbol)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _fetch_oi(self, symbol: str) -> None:
        url = f"{settings.rest_base_url}/fapi/v1/openInterest"
        params = {"symbol": symbol}
        async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning("OI API returned %d for %s", resp.status, symbol)
                return
            data = await resp.json()

        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        now = datetime.now(timezone.utc)
        oi = Decimal(data["openInterest"])
        exchange_ts = datetime.fromtimestamp(data["time"] / 1000.0, tz=timezone.utc)

        record = (
            exchange_ts,
            now,
            self._venue_id,
            instrument_id,
            oi,
        )
        self._writer.enqueue("open_interest", record)
