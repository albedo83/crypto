"""Route incoming WebSocket messages to the appropriate handler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import orjson

from src.collector.handlers.base import BaseHandler
from src.collector.handlers.trades import TradesHandler
from src.collector.handlers.book_tob import BookTobHandler
from src.collector.handlers.book_levels import BookLevelsHandler
from src.collector.handlers.mark_index import MarkIndexHandler
from src.collector.handlers.liquidations import LiquidationsHandler
from src.collector.writer import BatchWriter

logger = logging.getLogger(__name__)


class Dispatcher:
    """Parses combined stream messages and dispatches to handlers."""

    def __init__(self, writer: BatchWriter, venue_id: int) -> None:
        self._writer = writer
        self._venue_id = venue_id
        self._handlers: dict[str, BaseHandler] = {
            "aggTrade": TradesHandler(writer, venue_id),
            "bookTicker": BookTobHandler(writer, venue_id),
            "depthUpdate": BookLevelsHandler(writer, venue_id),
            "markPriceUpdate": MarkIndexHandler(writer, venue_id),
            "forceOrder": LiquidationsHandler(writer, venue_id),
        }
        self._msg_count = 0
        self._unknown_count = 0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    @property
    def book_levels_handler(self) -> BookLevelsHandler:
        return self._handlers["depthUpdate"]  # type: ignore[return-value]

    def dispatch(self, raw: bytes | str) -> None:
        """Parse and route a combined stream message."""
        recv_ts = datetime.now(timezone.utc)
        try:
            msg = orjson.loads(raw)
        except Exception:
            logger.warning("Failed to parse message: %s", raw[:200] if raw else "")
            return

        self._msg_count += 1

        # Combined stream format: {"stream": "btcusdt@aggTrade", "data": {...}}
        stream_name = msg.get("stream", "")
        data = msg.get("data")
        if data is None:
            return

        event_type = data.get("e", "")

        handler = self._handlers.get(event_type)
        if handler is not None:
            try:
                handler.handle(data, recv_ts)
            except Exception:
                logger.exception("Handler error for %s (stream=%s)", event_type, stream_name)
        else:
            self._unknown_count += 1
            if self._unknown_count <= 5:
                logger.debug("Unknown event type: %s (stream=%s)", event_type, stream_name)
