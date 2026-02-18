"""Structured logging setup using loguru."""

import sys

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    """Configure loguru for the application."""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.log_level,
        colorize=True,
    )

    logger.add(
        "/app/logs/bf1_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="DEBUG",
        rotation="1 day",
        retention="30 days",
        compression="gz",
        enqueue=True,
    )

    logger.add(
        "/app/logs/bf1_errors_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="ERROR",
        rotation="1 day",
        retention="90 days",
        compression="gz",
        enqueue=True,
    )


setup_logging()
