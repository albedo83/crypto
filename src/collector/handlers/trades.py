"""aggTrade handler -> trades_raw table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.collector.handlers.base import BaseHandler
from src.collector.writer import BatchWriter
from src.shared.instruments import get_instrument_id
from src.shared import constants


class TradesHandler(BaseHandler):
    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id

    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        symbol = data.get("s", "")
        instrument_id = get_instrument_id(symbol)
        if instrument_id is None:
            return

        price = Decimal(data["p"])
        qty = Decimal(data["q"])
        notional = price * qty

        is_buyer_maker = data["m"]
        aggressor_side = "SELL" if is_buyer_maker else "BUY"

        record = (
            self.ms_to_dt(data["T"]),       # exchange_ts
            recv_ts,                          # recv_ts
            self._venue_id,                   # venue_id
            instrument_id,                    # instrument_id
            data["a"],                        # agg_trade_id
            price,                            # price
            qty,                              # qty
            data.get("f"),                    # first_trade_id
            data.get("l"),                    # last_trade_id
            is_buyer_maker,                   # is_buyer_maker
            notional,                         # notional
            aggressor_side,                   # aggressor_side
        )
        self._writer.enqueue("trades_raw", record)
