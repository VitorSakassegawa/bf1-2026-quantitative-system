"""Simulation handler – the simulated finishing order of the whole field."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.telegram.formatters import format_field
from app.telegram.handlers.common import NO_DATA_MESSAGE, resolve_race, strategy_for


async def simulate_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /simulate [race_id] – win/podium/top-10/11th/DNF odds per driver."""
    if update.effective_message is None:
        return

    logger.info(f"/simulate from {update.effective_user}")
    await update.effective_message.reply_text("Simulando…")

    async with AsyncSessionLocal() as session:
        race = await resolve_race(session, context.args or [])
        if race is None:
            await update.effective_message.reply_text(NO_DATA_MESSAGE)
            return
        strategy, _ = await strategy_for(session, race)

    await update.effective_message.reply_text(
        format_field(strategy), parse_mode="MarkdownV2"
    )
