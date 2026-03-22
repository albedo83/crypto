"""Base handler ABC."""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Any


class BaseHandler(abc.ABC):
    """Abstract base for stream message handlers."""

    @abc.abstractmethod
    def handle(self, data: dict[str, Any], recv_ts: datetime) -> None:
        """Process a message and enqueue records for writing."""
        ...

    @staticmethod
    def ms_to_dt(ms: int | float) -> datetime:
        """Convert millisecond timestamp to timezone-aware datetime."""
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)
