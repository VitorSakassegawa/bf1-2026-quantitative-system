"""Standings handler – the pool's real classification."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.collectors.pool_client import PoolError
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import pool_service
from app.telegram.formatters import (
    escape_md,
    format_classification,
    format_local_leaderboard,
)


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /classificacao (and /leaderboard).

    With the pool configured this is the pool's own table, read from its API,
    with your row and the gaps around it. Without it, it falls back to the
    scores of the bets recorded through this bot.
    """
    if update.effective_message is None:
        return
    logger.info(f"/classificacao from {update.effective_user}")

    note = ""
    if pool_service.pool_enabled():
        try:
            standings = await pool_service.fetch_standings()
            await update.effective_message.reply_text(
                format_classification(standings), parse_mode="MarkdownV2"
            )
            return
        except PoolError as e:
            logger.warning(f"Pool standings unavailable: {e}")
            note = "⚠️ " + escape_md(f"Não consegui ler o site do bolão ({e}).") + "\n\n"
    else:
        note = (
            escape_md(
                "O site do bolão não está configurado (POOL_BASE_URL, POOL_EMAIL, "
                "POOL_PASSWORD). Mostrando só as apostas registradas aqui."
            )
            + "\n\n"
        )

    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(
                select(User).order_by(User.total_points.desc()).limit(20)
            )
        ).scalars().all()
    await update.effective_message.reply_text(
        note + format_local_leaderboard(list(users)), parse_mode="MarkdownV2"
    )
