"""Race analysis handler – the recommended bet for the next race."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.telegram.formatters import format_strategy
from app.telegram.handlers.common import NO_DATA_MESSAGE, resolve_race, strategy_for


async def analysis_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /analysis [race_id] – chips, 11th-place pick and what to expect."""
    if update.effective_message is None:
        return

    logger.info(f"/analysis from {update.effective_user}")
    await update.effective_message.reply_text("Analisando…")

    profile = (context.user_data or {}).get("aggressiveness", "balanced")
    async with AsyncSessionLocal() as session:
        race = await resolve_race(session, context.args or [])
        if race is None:
            await update.effective_message.reply_text(NO_DATA_MESSAGE)
            return
        strategy, pool = await strategy_for(session, race, profile)

    await update.effective_message.reply_text(
        format_strategy(strategy, pool), parse_mode="MarkdownV2"
    )
