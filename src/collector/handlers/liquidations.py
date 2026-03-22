"""forceOrder handler -> liquidations table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.collector.handlers.base import BaseHandler
from src.collector.writer import BatchWriter
from src.shared.instruments import get_instrument_id


class LiquidationsHandler(BaseHandler):
    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id

    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        order = data.get("o", {})
        symbol = order.get("s", "")
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        price = Decimal(order["p"])
        avg_price = Decimal(order["ap"])
        orig_qty = Decimal(order["q"])
        filled_qty = Decimal(order["z"])
        notional = avg_price * filled_qty if filled_qty else price * orig_qty

        record = (
            self.ms_to_dt(order["T"]),        # exchange_ts
            recv_ts,                           # recv_ts
            self._venue_id,                    # venue_id
            instrument_id,                     # instrument_id
            order["S"],                        # side (BUY/SELL)
            order["o"],                        # order_type
            orig_qty,                          # orig_qty
            price,                             # price
            avg_price,                         # avg_price
            order["X"],                        # status
            filled_qty,                        # filled_qty
            notional,                          # notional
        )
        self._writer.enqueue("liquidations", record)
