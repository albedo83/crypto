"""Instrument registry: symbol -> instrument_id mapping."""

from __future__ import annotations

import asyncpg


_registry: dict[str, int] = {}


async def load_instruments(pool: asyncpg.Pool) -> dict[str, int]:
    """Load instrument_id mapping from DB."""
    global _registry
    rows = await pool.fetch(
        "SELECT instrument_id, symbol FROM instruments WHERE is_active = true"
    )
    _registry = {row["symbol"]: row["instrument_id"] for row in rows}
    return _registry


def get_instrument_id(symbol: str) -> int | None:
    """Get instrument_id for a symbol. Returns None if not found."""
    return _registry.get(symbol.upper())


def all_instruments() -> dict[str, int]:
    """Return full registry."""
    return dict(_registry)
