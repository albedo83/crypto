"""Paper trading dashboard API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["paper"])


@router.get("/paper/state")
async def get_state(request: Request):
    """Current bot state: positions, signals, P&L."""
    pool = request.app.state.pool
    state = await pool.fetchrow("SELECT * FROM paper_state ORDER BY id LIMIT 1")
    return dict(state) if state else {}


@router.get("/paper/trades")
async def get_trades(request: Request, limit: int = 50):
    """Recent paper trades."""
    pool = request.app.state.pool
    rows = await pool.fetch(
        "SELECT * FROM paper_trades ORDER BY exit_time DESC LIMIT $1", limit
    )
    return [dict(r) for r in rows]


@router.get("/paper/pnl")
async def get_pnl_curve(request: Request):
    """Cumulative P&L curve data."""
    pool = request.app.state.pool
    rows = await pool.fetch("""
        SELECT exit_time, symbol, direction, gross_pnl_bps, net_pnl_bps, composite_score,
               sum(gross_pnl_bps) OVER (ORDER BY exit_time) AS cum_gross,
               sum(net_pnl_bps) OVER (ORDER BY exit_time) AS cum_net
        FROM paper_trades
        ORDER BY exit_time
    """)
    return [dict(r) for r in rows]


@router.get("/paper/stats")
async def get_stats(request: Request):
    """Detailed statistics."""
    pool = request.app.state.pool
    overall = await pool.fetchrow("""
        SELECT count(*) AS total_trades,
               coalesce(sum(gross_pnl_bps), 0) AS total_gross,
               coalesce(sum(net_pnl_bps), 0) AS total_net,
               coalesce(avg(gross_pnl_bps), 0) AS avg_gross,
               coalesce(avg(net_pnl_bps), 0) AS avg_net,
               coalesce(avg(CASE WHEN gross_pnl_bps > 0 THEN 1.0 ELSE 0.0 END), 0) AS win_rate,
               coalesce(avg(hold_seconds), 0) AS avg_hold,
               coalesce(stddev(gross_pnl_bps), 0) AS std_gross
        FROM paper_trades
    """)
    by_symbol = await pool.fetch("""
        SELECT symbol,
               count(*) AS trades,
               sum(gross_pnl_bps) AS gross,
               sum(net_pnl_bps) AS net,
               avg(gross_pnl_bps) AS avg_gross,
               avg(CASE WHEN gross_pnl_bps > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM paper_trades GROUP BY symbol ORDER BY symbol
    """)
    by_direction = await pool.fetch("""
        SELECT direction,
               count(*) AS trades,
               avg(gross_pnl_bps) AS avg_gross,
               avg(CASE WHEN gross_pnl_bps > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM paper_trades GROUP BY direction
    """)
    return {
        "overall": dict(overall) if overall else {},
        "by_symbol": [dict(r) for r in by_symbol],
        "by_direction": [dict(r) for r in by_direction],
    }
