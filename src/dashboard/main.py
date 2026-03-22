"""Dashboard entry point."""

from __future__ import annotations

import uvicorn

from src.config import settings
from src.dashboard.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run(
        "src.dashboard.main:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
