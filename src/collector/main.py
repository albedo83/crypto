"""Collector entry point."""

from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.collector.engine import Engine


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reduce noise from libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    engine = Engine()
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
