"""Async DB helper: fetch queries as DataFrames."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import asyncpg
import numpy as np
import pandas as pd

from src.config import settings

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(
            dsn=settings.dsn,
            min_size=1,
            max_size=4,
        )
    return _pool


async def _fetch_df(query: str, *args) -> pd.DataFrame:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        # Convert Decimal columns to float for numpy compatibility
        for col in df.columns:
            if df[col].dtype == object and len(df) > 0:
                sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else None
                if isinstance(sample, Decimal):
                    df[col] = df[col].astype(float)
        return df


def fetch_df(query: str, *args) -> pd.DataFrame:
    """Run query and return result as DataFrame (sync wrapper)."""
    return asyncio.get_event_loop().run_until_complete(_fetch_df(query, *args))


async def close_pool() -> None:
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()
        _pool = None
