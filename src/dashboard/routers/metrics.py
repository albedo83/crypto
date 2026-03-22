"""GET /api/metrics - Chart data from materialized views."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Query

router = APIRouter(tags=["metrics"])

TIMEFRAME_DELTAS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def _since(timeframe: str) -> datetime:
    delta = TIMEFRAME_DELTAS.get(timeframe, timedelta(hours=24))
    return datetime.now(timezone.utc) - delta


@router.get("/metrics/ohlcv")
async def get_ohlcv(
    request: Request,
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("24h"),
):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """SELECT bucket AS time, open, high, low, close, volume, notional_volume,
                  buy_volume, sell_volume, trade_count
           FROM trades_1m t
           JOIN instruments i ON i.instrument_id = t.instrument_id
           WHERE i.symbol = $1 AND t.bucket > $2
           ORDER BY t.bucket ASC""",
        symbol.upper(),
        _since(timeframe),
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": [dict(r) for r in rows]}


@router.get("/metrics/spread")
async def get_spread(
    request: Request,
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("24h"),
):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """SELECT bucket AS time, avg_spread_bps, max_spread_bps, min_spread_bps,
                  avg_mid_price, update_count
           FROM book_tob_1m b
           JOIN instruments i ON i.instrument_id = b.instrument_id
           WHERE i.symbol = $1 AND b.bucket > $2
           ORDER BY b.bucket ASC""",
        symbol.upper(),
        _since(timeframe),
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "data": [dict(r) for r in rows]}


@router.get("/metrics/funding")
async def get_funding(
    request: Request,
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("7d"),
):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """SELECT exchange_ts AS time, funding_rate, mark_price, index_price
           FROM funding f
           JOIN instruments i ON i.instrument_id = f.instrument_id
           WHERE i.symbol = $1 AND f.exchange_ts > $2
           ORDER BY f.exchange_ts ASC""",
        symbol.upper(),
        _since(timeframe),
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "data": [dict(r) for r in rows]}


@router.get("/metrics/oi")
async def get_oi(
    request: Request,
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("7d"),
):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """SELECT exchange_ts AS time, open_interest
           FROM open_interest oi
           JOIN instruments i ON i.instrument_id = oi.instrument_id
           WHERE i.symbol = $1 AND oi.exchange_ts > $2
           ORDER BY oi.exchange_ts ASC""",
        symbol.upper(),
        _since(timeframe),
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "data": [dict(r) for r in rows]}


@router.get("/metrics/basis")
async def get_basis(
    request: Request,
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("24h"),
):
    pool = request.app.state.pool
    rows = await pool.fetch(
        """SELECT time_bucket('1 minute', m.exchange_ts) AS time,
                  avg(m.basis_bps) AS avg_basis_bps,
                  avg(m.mark_price) AS avg_mark,
                  avg(m.index_price) AS avg_index
           FROM mark_index m
           JOIN instruments i ON i.instrument_id = m.instrument_id
           WHERE i.symbol = $1 AND m.exchange_ts > $2
           GROUP BY time
           ORDER BY time ASC""",
        symbol.upper(),
        _since(timeframe),
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe, "data": [dict(r) for r in rows]}
