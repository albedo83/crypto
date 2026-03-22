"""markPrice handler -> mark_index + funding tables."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.collector.handlers.base import BaseHandler
from src.collector.writer import BatchWriter
from src.shared.instruments import get_instrument_id


class MarkIndexHandler(BaseHandler):
    """Handles markPrice@1s stream.

    Writes to mark_index every message.
    Writes to funding only when funding_rate changes (at funding time).
    """

    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id
        self._last_funding_ts: dict[int, int] = {}  # instrument_id -> last next_funding_ts

    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        symbol = data.get("s", "")
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        mark_price = Decimal(data["p"])
        index_price = Decimal(data["i"])
        est_settle = Decimal(data["P"]) if data.get("P") else None
        funding_rate = Decimal(data["r"]) if data.get("r") else None
        next_funding_ms = data.get("T")
        next_funding_ts = self.ms_to_dt(next_funding_ms) if next_funding_ms else None

        basis_abs = mark_price - index_price
        basis_bps = (basis_abs / index_price * 10000) if index_price else Decimal(0)

        exchange_ts = self.ms_to_dt(data["E"])

        record = (
            exchange_ts,
            recv_ts,
            self._venue_id,
            instrument_id,
            mark_price,
            index_price,
            est_settle,
            funding_rate,
            next_funding_ts,
            basis_abs,
            basis_bps,
        )
        self._writer.enqueue("mark_index", record)

        # Detect funding event: next_funding_ts changed -> funding was applied
        if next_funding_ms and instrument_id in self._last_funding_ts:
            if next_funding_ms != self._last_funding_ts[instrument_id] and funding_rate is not None:
                funding_record = (
                    exchange_ts,
                    recv_ts,
                    self._venue_id,
                    instrument_id,
                    funding_rate,
                    mark_price,
                    index_price,
                )
                self._writer.enqueue("funding", funding_record)

        if next_funding_ms:
            self._last_funding_ts[instrument_id] = next_funding_ms
