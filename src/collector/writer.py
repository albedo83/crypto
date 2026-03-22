"""BatchWriter: async queues -> COPY batches to PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg

from src.config import settings

logger = logging.getLogger(__name__)

# Column definitions for COPY protocol
TABLE_COLUMNS: dict[str, list[str]] = {
    "trades_raw": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "agg_trade_id", "price", "qty", "first_trade_id", "last_trade_id",
        "is_buyer_maker", "notional", "aggressor_side",
    ],
    "book_tob": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "update_id", "bid_price", "bid_qty", "ask_price", "ask_qty",
        "mid_price", "spread_abs", "spread_bps",
    ],
    "book_levels": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "first_update_id", "last_update_id",
        "bids_price", "bids_qty", "asks_price", "asks_qty",
        "is_snapshot",
    ],
    "mark_index": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "mark_price", "index_price", "est_settle_price",
        "funding_rate", "next_funding_ts", "basis_abs", "basis_bps",
    ],
    "funding": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "funding_rate", "mark_price", "index_price",
    ],
    "open_interest": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "open_interest",
    ],
    "liquidations": [
        "exchange_ts", "recv_ts", "venue_id", "instrument_id",
        "side", "order_type", "orig_qty", "price", "avg_price",
        "status", "filled_qty", "notional",
    ],
}


class BatchWriter:
    """Buffers records in per-table queues and flushes via COPY protocol."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._queues: dict[str, asyncio.Queue] = {
            table: asyncio.Queue(maxsize=settings.queue_max_size)
            for table in TABLE_COLUMNS
        }
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats: dict[str, int] = {t: 0 for t in TABLE_COLUMNS}
        self._drop_stats: dict[str, int] = {t: 0 for t in TABLE_COLUMNS}

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._flush_loop(), name="batch_writer")
        logger.info("BatchWriter started (flush=%dms, max_batch=%d)",
                     settings.batch_flush_ms, settings.batch_max_size)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final drain
        await self._flush_all()
        logger.info("BatchWriter stopped. Stats: %s", self._stats)

    def enqueue(self, table: str, record: tuple[Any, ...]) -> None:
        """Enqueue a record for writing. Drops oldest if queue full."""
        q = self._queues.get(table)
        if q is None:
            logger.warning("Unknown table: %s", table)
            return
        if q.full():
            try:
                q.get_nowait()  # drop oldest
                self._drop_stats[table] += 1
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(record)
        except asyncio.QueueFull:
            self._drop_stats[table] += 1

    def queue_depths(self) -> dict[str, int]:
        return {t: q.qsize() for t, q in self._queues.items()}

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def _flush_loop(self) -> None:
        interval = settings.batch_flush_ms / 1000.0
        while self._running:
            await asyncio.sleep(interval)
            await self._flush_all()

    async def _flush_all(self) -> None:
        for table in TABLE_COLUMNS:
            q = self._queues[table]
            if q.empty():
                continue
            batch: list[tuple] = []
            while len(batch) < settings.batch_max_size:
                try:
                    batch.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch:
                continue
            try:
                async with self._pool.acquire() as conn:
                    await conn.copy_records_to_table(
                        table,
                        records=batch,
                        columns=TABLE_COLUMNS[table],
                    )
                self._stats[table] += len(batch)
            except Exception:
                logger.exception("COPY failed for %s (%d records), re-enqueuing once",
                                 table, len(batch))
                for rec in batch:
                    if not q.full():
                        try:
                            q.put_nowait(rec)
                        except asyncio.QueueFull:
                            break
