"""depth@100ms handler -> book_levels table."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.collector.handlers.base import BaseHandler
from src.collector.writer import BatchWriter
from src.shared.instruments import get_instrument_id

logger = logging.getLogger(__name__)


@dataclass
class SequenceGap:
    """Represents a detected gap in the book update sequence."""
    instrument_id: int
    symbol: str
    expected_id: int
    received_id: int
    detected_at: datetime


class BookLevelsHandler(BaseHandler):
    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id
        # Sequence tracking: instrument_id -> last_update_id
        self._last_update_id: dict[int, int] = {}
        # Gaps detected since last drain (sync-safe list, drained by HealthMonitor)
        self._pending_gaps: list[SequenceGap] = []

    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        # depth stream uses the symbol from the stream name, not in data
        symbol = data.get("s", "")
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        exchange_ts = self.ms_to_dt(data["T"]) if "T" in data else recv_ts

        first_update_id = data.get("U", 0)
        last_update_id = data.get("u", 0)

        # Sequence gap detection using Binance "pu" (previous last_update_id).
        # depth10@100ms is a partial depth stream — update IDs are NOT contiguous,
        # so we compare pu against our tracked last_update_id.
        prev_u = data.get("pu")
        if prev_u is not None and instrument_id in self._last_update_id:
            expected_pu = self._last_update_id[instrument_id]
            if prev_u != expected_pu:
                gap = SequenceGap(
                    instrument_id=instrument_id,
                    symbol=symbol,
                    expected_id=expected_pu,
                    received_id=prev_u,
                    detected_at=recv_ts,
                )
                self._pending_gaps.append(gap)
                logger.warning(
                    "Book sequence gap: symbol=%s pu=%d expected_pu=%d",
                    symbol, prev_u, expected_pu,
                )
        self._last_update_id[instrument_id] = last_update_id

        bids = data.get("b", [])
        asks = data.get("a", [])

        bids_price = [Decimal(b[0]) for b in bids]
        bids_qty = [Decimal(b[1]) for b in bids]
        asks_price = [Decimal(a[0]) for a in asks]
        asks_qty = [Decimal(a[1]) for a in asks]

        record = (
            exchange_ts,
            recv_ts,
            self._venue_id,
            instrument_id,
            first_update_id,
            last_update_id,
            bids_price,
            bids_qty,
            asks_price,
            asks_qty,
            False,  # is_snapshot
        )
        self._writer.enqueue("book_levels", record)

    def insert_snapshot(
        self,
        symbol: str,
        bids: list[list[str]],
        asks: list[list[str]],
        last_update_id: int,
        recv_ts: datetime,
    ) -> None:
        """Insert a REST depth snapshot into book_levels."""
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        bids_price = [Decimal(b[0]) for b in bids]
        bids_qty = [Decimal(b[1]) for b in bids]
        asks_price = [Decimal(a[0]) for a in asks]
        asks_qty = [Decimal(a[1]) for a in asks]

        record = (
            recv_ts,           # exchange_ts (REST has no event ts, use recv_ts)
            recv_ts,           # recv_ts
            self._venue_id,
            instrument_id,
            last_update_id,    # first_update_id = last_update_id for snapshots
            last_update_id,    # last_update_id
            bids_price,
            bids_qty,
            asks_price,
            asks_qty,
            True,              # is_snapshot
        )
        self._writer.enqueue("book_levels", record)

    def reset_state(self) -> None:
        """Clear sequence tracking after a reconnect."""
        self._last_update_id.clear()
        logger.info("BookLevelsHandler state reset (sequence tracking cleared)")

    def drain_gaps(self) -> list[SequenceGap]:
        """Return and clear pending gaps. Called by HealthMonitor."""
        gaps = self._pending_gaps
        self._pending_gaps = []
        return gaps
