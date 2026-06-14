"""Telegram bot setup and runner."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from time import time

from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config import settings
from app.telegram.handlers.analysis_handler import analysis_command
from app.telegram.handlers.bet_handler import (
    BET_CONFIRM,
    BET_SELECT_RACE,
    BET_SELECT_STRATEGY,
    bet_cancel,
    bet_confirm,
    bet_select_race,
    bet_select_strategy,
    bet_start,
)
from app.telegram.handlers.calendar_handler import calendar_command
from app.telegram.handlers.leaderboard_handler import leaderboard_command
from app.telegram.handlers.settings_handler import (
    aggressiveness_callback,
    settings_command,
)
from app.telegram.handlers.simulation_handler import simulate_command
from app.telegram.handlers.start import start_command

# Simple rate limiter: {user_id: [timestamps]}
_rate_limits: dict[int, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 60  # seconds


async def rate_limit_check(update: Update, _context) -> bool:
    """Return True if the user is rate-limited."""
    if update.effective_user is None:
        return False
    uid = update.effective_user.id
    now = time()
    _rate_limits[uid] = [t for t in _rate_limits[uid] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[uid]) >= RATE_LIMIT_MAX:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Rate limit exceeded. Please wait a minute."
            )
        return True
    _rate_limits[uid].append(now)
    return False


def setup_bot() -> Application:
    """Build and configure the Telegram bot application."""
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured")

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("analysis", analysis_command))
    app.add_handler(CommandHandler("simulate", simulate_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # Bet conversation handler
    bet_conv = ConversationHandler(
        entry_points=[CommandHandler("bet", bet_start)],
        states={
            BET_SELECT_RACE: [CallbackQueryHandler(bet_select_race)],
            BET_SELECT_STRATEGY: [CallbackQueryHandler(bet_select_strategy)],
            BET_CONFIRM: [CallbackQueryHandler(bet_confirm)],
        },
        fallbacks=[CommandHandler("cancel", bet_cancel)],
    )
    app.add_handler(bet_conv)

    # Aggressiveness callback
    app.add_handler(
        CallbackQueryHandler(aggressiveness_callback, pattern="^agg_")
    )

    logger.info("Telegram bot configured with all handlers")
    return app


def run_bot() -> None:
    """Run the bot in polling mode (blocking)."""
    app = setup_bot()
    logger.info("Starting Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
