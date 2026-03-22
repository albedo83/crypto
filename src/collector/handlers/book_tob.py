"""bookTicker handler -> book_tob table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.collector.handlers.base import BaseHandler
from src.collector.writer import BatchWriter
from src.shared.instruments import get_instrument_id


class BookTobHandler(BaseHandler):
    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id

    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        symbol = data.get("s", "")
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        bid_price = Decimal(data["b"])
        bid_qty = Decimal(data["B"])
        ask_price = Decimal(data["a"])
        ask_qty = Decimal(data["A"])

        mid_price = (bid_price + ask_price) / 2
        spread_abs = ask_price - bid_price
        spread_bps = (spread_abs / mid_price * 10000) if mid_price else Decimal(0)

        exchange_ts = self.ms_to_dt(data["T"]) if "T" in data else recv_ts

        record = (
            exchange_ts,
            recv_ts,
            self._venue_id,
            instrument_id,
            data["u"],                        # update_id
            bid_price,
            bid_qty,
            ask_price,
            ask_qty,
            mid_price,
            spread_abs,
            spread_bps,
        )
        self._writer.enqueue("book_tob", record)
