"""Backward-compatible entry point. See analysis/bot/ for modules."""
import asyncio
from analysis.bot.main import run

if __name__ == "__main__":
    asyncio.run(run())
