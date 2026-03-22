"""GET /api/data/{table} - Data browser."""

from __future__ import annotations

from fastapi import APIRouter, Request, Query

router = APIRouter(tags=["data"])

ALLOWED_TABLES = {
    "trades_raw", "book_tob", "book_levels", "mark_index",
    "funding", "open_interest", "liquidations",
}

TIME_COL = {
    "trades_raw": "exchange_ts",
    "book_tob": "exchange_ts",
    "book_levels": "exchange_ts",
    "mark_index": "exchange_ts",
    "funding": "exchange_ts",
    "open_interest": "exchange_ts",
    "liquidations": "exchange_ts",
}


@router.get("/data/{table}")
async def get_data(
    request: Request,
    table: str,
    symbol: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    if table not in ALLOWED_TABLES:
        return {"error": f"Table '{table}' not allowed"}

    pool = request.app.state.pool
    ts_col = TIME_COL[table]

    query = f"SELECT * FROM {table}"
    params: list = []
    conditions = []

    if symbol:
        conditions.append(
            "instrument_id = (SELECT instrument_id FROM instruments WHERE symbol = $1)"
        )
        params.append(symbol.upper())

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += f" ORDER BY {ts_col} DESC LIMIT ${len(params) + 1}"
    params.append(limit)

    rows = await pool.fetch(query, *params)

    return {
        "table": table,
        "count": len(rows),
        "rows": [dict(r) for r in rows],
    }


@router.get("/data/{table}/count")
async def get_count(
    request: Request,
    table: str,
    symbol: str | None = Query(None),
):
    if table not in ALLOWED_TABLES:
        return {"error": f"Table '{table}' not allowed"}

    pool = request.app.state.pool
    query = f"SELECT count(*) FROM {table}"
    params: list = []

    if symbol:
        query += " WHERE instrument_id = (SELECT instrument_id FROM instruments WHERE symbol = $1)"
        params.append(symbol.upper())

    row = await pool.fetchrow(query, *params)
    return {"table": table, "count": row["count"]}
