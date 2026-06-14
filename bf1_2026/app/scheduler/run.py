"""Standalone entrypoint for the scheduler process.

Run with:  python -m app.scheduler.run

AsyncIOScheduler.start() must be called inside a running event loop, so we
start it from within asyncio.run() and then keep the process alive.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from app.scheduler.jobs import scheduler


async def _main() -> None:
    scheduler.start()
    logger.info("Scheduler process started; waiting for jobs...")
    try:
        # Keep the process (and the event loop) alive indefinitely.
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler process stopping...")
    finally:
        if scheduler.running:
            scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(_main())
