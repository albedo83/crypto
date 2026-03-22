"""GET /api/status - Collector status and overview."""

from __future__ import annotations

from fastapi import APIRouter, Request
import orjson

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status(request: Request):
    pool = request.app.state.pool

    # Latest heartbeat
    hb = await pool.fetchrow(
        """SELECT ts, ws_connected, streams_active, queue_depths, memory_rss_mb, cpu_percent
           FROM heartbeat ORDER BY ts DESC LIMIT 1"""
    )

    # Per-symbol status
    symbols = await pool.fetch(
        """SELECT i.symbol, ss.is_collecting, ss.last_trade_ts, ss.last_book_ts,
                  ss.last_mark_ts, ss.msg_rate_1m, ss.latency_p50_ms
           FROM symbol_status ss
           JOIN instruments i ON i.instrument_id = ss.instrument_id
           ORDER BY i.symbol"""
    )

    # Latest prices from trades_1m
    prices = await pool.fetch(
        """SELECT i.symbol, t.close AS price, t.volume, t.trade_count
           FROM trades_1m t
           JOIN instruments i ON i.instrument_id = t.instrument_id
           WHERE t.bucket > now() - INTERVAL '2 minutes'
           ORDER BY t.bucket DESC"""
    )

    # Latest spreads
    spreads = await pool.fetch(
        """SELECT i.symbol, b.avg_spread_bps, b.avg_mid_price
           FROM book_tob_1m b
           JOIN instruments i ON i.instrument_id = b.instrument_id
           WHERE b.bucket > now() - INTERVAL '2 minutes'
           ORDER BY b.bucket DESC"""
    )

    # Table row counts (direct count for hypertables)
    counts = await pool.fetch(
        """SELECT t.table_name, t.row_count FROM (
               SELECT 'trades_raw' AS table_name, count(*) AS row_count FROM trades_raw UNION ALL
               SELECT 'book_tob', count(*) FROM book_tob UNION ALL
               SELECT 'book_levels', count(*) FROM book_levels UNION ALL
               SELECT 'mark_index', count(*) FROM mark_index UNION ALL
               SELECT 'funding', count(*) FROM funding UNION ALL
               SELECT 'open_interest', count(*) FROM open_interest UNION ALL
               SELECT 'liquidations', count(*) FROM liquidations
           ) t ORDER BY t.table_name"""
    )

    return {
        "heartbeat": dict(hb) if hb else None,
        "symbols": [dict(s) for s in symbols],
        "prices": [dict(p) for p in prices],
        "spreads": [dict(s) for s in spreads],
        "table_counts": [dict(c) for c in counts],
    }


@router.get("/status/heartbeat")
async def get_heartbeat(request: Request):
    """Latest heartbeat for htmx partial refresh."""
    pool = request.app.state.pool
    hb = await pool.fetchrow(
        "SELECT * FROM heartbeat ORDER BY ts DESC LIMIT 1"
    )
    return dict(hb) if hb else {}
