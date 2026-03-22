"""GET /api/alerts - Events, gaps, and alerts."""

from __future__ import annotations

from fastapi import APIRouter, Request, Query

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def get_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
):
    pool = request.app.state.pool

    events = await pool.fetch(
        """SELECT ts, collector_id, event_type, severity, message, details
           FROM collector_events
           ORDER BY ts DESC LIMIT $1""",
        limit,
    )

    gaps = await pool.fetch(
        """SELECT detected_at, collector_id, stream_name, gap_start_ts,
                  gap_end_ts, gap_duration_ms, reason
           FROM session_gaps
           ORDER BY detected_at DESC LIMIT $1""",
        limit,
    )

    return {
        "events": [dict(e) for e in events],
        "gaps": [dict(g) for g in gaps],
    }


@router.get("/alerts/recent")
async def get_recent_alerts(request: Request):
    """Recent alerts for htmx partial."""
    pool = request.app.state.pool

    events = await pool.fetch(
        """SELECT ts, event_type, severity, message
           FROM collector_events
           WHERE severity IN ('warning', 'error')
           ORDER BY ts DESC LIMIT 10"""
    )

    return {"alerts": [dict(e) for e in events]}
