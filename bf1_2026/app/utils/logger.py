"""Structured logging setup using loguru.

Call :func:`setup_logging` once from a process entry point (API, bot,
scheduler). Importing this module has no side effects: it used to configure
logging at import time, but nothing ever imported it, so the file sinks,
rotation and retention it describes were never active and the `logs` volume
mounted into every container stayed empty.
"""

import os
import sys

from loguru import logger

from app.config import settings

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

_configured = False


def setup_logging(log_dir: str | None = None) -> None:
    """Configure loguru: stderr always, daily files when the directory is writable."""
    global _configured
    if _configured:
        return
    _configured = True

    logger.remove()
    logger.add(sys.stderr, format=LOG_FORMAT, level=settings.log_level, colorize=True)

    log_dir = log_dir or os.getenv("LOG_DIR", "/app/logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        if not os.access(log_dir, os.W_OK):
            raise PermissionError(log_dir)
    except OSError as e:
        logger.info(f"File logging disabled ({e}); logging to stderr only")
        return

    logger.add(
        os.path.join(log_dir, "bf1_{time:YYYY-MM-DD}.log"),
        format=LOG_FORMAT, level="DEBUG", rotation="1 day", retention="30 days",
        compression="gz", enqueue=True,
    )
    logger.add(
        os.path.join(log_dir, "bf1_errors_{time:YYYY-MM-DD}.log"),
        format=LOG_FORMAT, level="ERROR", rotation="1 day", retention="90 days",
        compression="gz", enqueue=True,
    )
