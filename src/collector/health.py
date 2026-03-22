"""Health monitoring: heartbeat, gap detection, latency tracking."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import asyncpg
import orjson
import psutil

from src.config import settings
from src.collector.writer import BatchWriter
from src.collector.ws_manager import WSManager

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.collector.handlers.book_levels import BookLevelsHandler

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Writes heartbeat records, detects gaps, refreshes matviews, runs retention."""

    MATVIEW_REFRESH_INTERVAL = 60       # refresh materialized views every 60s
    RETENTION_INTERVAL = 3600           # run retention cleanup every hour

    def __init__(
        self,
        pool: asyncpg.Pool,
        writer: BatchWriter,
        ws_manager: WSManager,
        book_handler: BookLevelsHandler | None = None,
    ) -> None:
        self._pool = pool
        self._writer = writer
        self._ws = ws_manager
        self._book_handler = book_handler
        self._running = False
        self._task: asyncio.Task | None = None
        self._maint_task: asyncio.Task | None = None
        self._process = psutil.Process(os.getpid())

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop(), name="health_monitor")
        self._maint_task = asyncio.create_task(self._maintenance_loop(), name="maintenance")
        logger.info("HealthMonitor started (interval=%ds)", settings.heartbeat_interval_s)

    async def stop(self) -> None:
        self._running = False
        for task in (self._task, self._maint_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(self) -> None:
        cycle = 0
        while self._running:
            try:
                await self._write_heartbeat()
            except Exception:
                logger.exception("Heartbeat write failed")
            # Drain book sequence gaps every cycle (~5s)
            try:
                await self._drain_book_gaps()
            except Exception:
                logger.exception("Book gap drain failed")
            cycle += 1
            if cycle % 2 == 0:  # every 10s (2 x 5s)
                try:
                    await self._update_symbol_status()
                except Exception:
                    logger.exception("Symbol status update failed")
            await asyncio.sleep(settings.heartbeat_interval_s)

    async def _maintenance_loop(self) -> None:
        """Periodically refresh matviews and run retention."""
        cycle = 0
        while self._running:
            await asyncio.sleep(self.MATVIEW_REFRESH_INTERVAL)
            cycle += 1
            try:
                await self._refresh_matviews()
            except Exception:
                logger.exception("Matview refresh failed")
            # Run retention every RETENTION_INTERVAL / MATVIEW_REFRESH_INTERVAL cycles
            if cycle % (self.RETENTION_INTERVAL // self.MATVIEW_REFRESH_INTERVAL) == 0:
                try:
                    await self._run_retention()
                except Exception:
                    logger.exception("Retention cleanup failed")

    async def _refresh_matviews(self) -> None:
        async with self._pool.acquire() as conn:
            for mv in ("trades_1m", "book_tob_1m", "order_flow_1m", "book_imbalance_1s"):
                populated = await conn.fetchval(
                    "SELECT ispopulated FROM pg_matviews WHERE matviewname = $1", mv
                )
                if populated:
                    await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
                else:
                    await conn.execute(f"REFRESH MATERIALIZED VIEW {mv}")
                    logger.info("Initial populate of %s", mv)
        logger.debug("Materialized views refreshed")

    async def _run_retention(self) -> None:
        """Drop old chunks for retention management."""
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT drop_chunks('trades_raw', INTERVAL '7 days')")
            await conn.execute("SELECT drop_chunks('book_tob', INTERVAL '3 days')")
            await conn.execute("SELECT drop_chunks('book_levels', INTERVAL '3 days')")
            await conn.execute("SELECT drop_chunks('mark_index', INTERVAL '30 days')")
            await conn.execute("SELECT drop_chunks('funding', INTERVAL '90 days')")
            await conn.execute("SELECT drop_chunks('open_interest', INTERVAL '30 days')")
            await conn.execute("SELECT drop_chunks('liquidations', INTERVAL '30 days')")
            await conn.execute("SELECT drop_chunks('heartbeat', INTERVAL '7 days')")
            await conn.execute("SELECT drop_chunks('collector_events', INTERVAL '30 days')")
        logger.info("Retention cleanup completed")

    async def _write_heartbeat(self) -> None:
        now = datetime.now(timezone.utc)
        mem = self._process.memory_info()
        cpu = self._process.cpu_percent(interval=None)
        depths = self._writer.queue_depths()

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO heartbeat (ts, collector_id, ws_connected, streams_active, queue_depths, memory_rss_mb, cpu_percent)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                now,
                settings.collector_id,
                self._ws.connected,
                len(settings.symbol_list) * 5,  # 5 streams per symbol
                orjson.dumps(depths).decode(),
                mem.rss / (1024 * 1024),
                cpu,
            )

    async def _update_symbol_status(self) -> None:
        """Update symbol_status from recent data."""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO symbol_status (updated_at, venue_id, instrument_id, is_collecting,
                    last_trade_ts, last_book_ts, last_mark_ts, msg_rate_1m, latency_p50_ms)
                SELECT
                    now(),
                    i.venue_id,
                    i.instrument_id,
                    i.is_active,
                    (SELECT max(exchange_ts) FROM trades_raw WHERE instrument_id = i.instrument_id),
                    (SELECT max(exchange_ts) FROM book_tob WHERE instrument_id = i.instrument_id),
                    (SELECT max(exchange_ts) FROM mark_index WHERE instrument_id = i.instrument_id),
                    coalesce((SELECT count(*) FROM trades_raw
                              WHERE instrument_id = i.instrument_id
                                AND exchange_ts > now() - INTERVAL '1 minute'), 0),
                    coalesce((SELECT extract(epoch from avg(recv_ts - exchange_ts))*1000
                              FROM trades_raw
                              WHERE instrument_id = i.instrument_id
                                AND exchange_ts > now() - INTERVAL '1 minute'), 0)
                FROM instruments i
                WHERE i.is_active = true
                ON CONFLICT (venue_id, instrument_id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    is_collecting = EXCLUDED.is_collecting,
                    last_trade_ts = EXCLUDED.last_trade_ts,
                    last_book_ts = EXCLUDED.last_book_ts,
                    last_mark_ts = EXCLUDED.last_mark_ts,
                    msg_rate_1m = EXCLUDED.msg_rate_1m,
                    latency_p50_ms = EXCLUDED.latency_p50_ms
            """)

    async def _drain_book_gaps(self) -> None:
        """Drain pending sequence gaps from BookLevelsHandler into session_gaps."""
        if not self._book_handler:
            return
        gaps = self._book_handler.drain_gaps()
        for gap in gaps:
            await self.log_gap(
                stream_name=f"depth:{gap.symbol}",
                gap_start=None,
                gap_end=gap.detected_at,
                reason=f"sequence_gap: expected={gap.expected_id} got={gap.received_id}",
            )

    async def log_event(
        self,
        event_type: str,
        severity: str = "info",
        message: str = "",
        details: dict | None = None,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO collector_events (ts, collector_id, event_type, severity, message, details)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    datetime.now(timezone.utc),
                    settings.collector_id,
                    event_type,
                    severity,
                    message,
                    details,
                )
        except Exception:
            logger.exception("Failed to log event")

    async def log_gap(
        self,
        stream_name: str,
        gap_start: datetime | None,
        gap_end: datetime | None,
        reason: str = "",
    ) -> None:
        duration_ms = None
        if gap_start and gap_end:
            duration_ms = int((gap_end - gap_start).total_seconds() * 1000)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO session_gaps (detected_at, collector_id, stream_name, gap_start_ts, gap_end_ts, gap_duration_ms, reason)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    datetime.now(timezone.utc),
                    settings.collector_id,
                    stream_name,
                    gap_start,
                    gap_end,
                    duration_ms,
                    reason,
                )
        except Exception:
            logger.exception("Failed to log gap")
