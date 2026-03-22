"""FastAPI application factory."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import orjson

from src.shared.db import get_pool, close_pool
from src.dashboard.routers import status, streams, data, metrics, alerts, paper
from src.dashboard.ws import router as ws_router

BASE_DIR = Path(__file__).resolve().parent


class ORJSONResponse(JSONResponse):
    """JSON response using orjson for Decimal/datetime support."""
    media_type = "application/json"

    def render(self, content) -> bytes:
        return orjson.dumps(content, default=_json_default)


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def create_app() -> FastAPI:
    app = FastAPI(title="Crypto Dashboard", docs_url="/docs", default_response_class=ORJSONResponse)

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.templates = templates

    # Static files
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    # API routers
    app.include_router(status.router, prefix="/api")
    app.include_router(streams.router, prefix="/api")
    app.include_router(data.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(paper.router, prefix="/api")
    app.include_router(ws_router)

    @app.on_event("startup")
    async def startup():
        app.state.pool = await get_pool()

    @app.on_event("shutdown")
    async def shutdown():
        await close_pool()

    # HTML pages
    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/streams")
    async def streams_page(request: Request):
        return templates.TemplateResponse("streams.html", {"request": request})

    @app.get("/data")
    async def data_page(request: Request):
        return templates.TemplateResponse("data.html", {"request": request})

    @app.get("/charts")
    async def charts_page(request: Request):
        return templates.TemplateResponse("charts.html", {"request": request})

    @app.get("/alerts")
    async def alerts_page(request: Request):
        return templates.TemplateResponse("alerts.html", {"request": request})

    @app.get("/paper")
    async def paper_page(request: Request):
        return templates.TemplateResponse("paper.html", {"request": request})

    return app
