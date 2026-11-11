"""Telegram bot setup and runner."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from time import time

from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    TypeHandler,
)

from app.config import settings
from app.telegram.handlers.analysis_handler import analysis_command
from app.telegram.handlers.bet_handler import (
    BET_CONFIRM,
    bet_cancel,
    bet_confirm,
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
                "Muitos comandos em sequência. Aguarde um minuto."
            )
        return True
    _rate_limits[uid].append(now)
    return False


async def _rate_limit_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler; stops the update when the user is over the limit.

    The limiter above existed but was never registered, so the bot had no rate
    limiting at all. Only commands count — button presses inside a
    conversation are not throttled.
    """
    message = update.effective_message
    if update.callback_query is not None or message is None:
        return
    if not (message.text or "").startswith("/"):
        return
    if await rate_limit_check(update, context):
        raise ApplicationHandlerStop


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the failure and tell the user, instead of leaving them on 'Analisando…'."""
    logger.opt(exception=context.error).error(f"Bot handler failed: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Algo deu errado ao processar esse comando. O erro foi registrado no log."
            )
        except Exception:
            pass


def setup_bot() -> Application:
    """Build and configure the Telegram bot application."""
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured")

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(TypeHandler(Update, _rate_limit_gate), group=-1)
    app.add_error_handler(_on_error)

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("analysis", analysis_command))
    app.add_handler(CommandHandler("simulate", simulate_command))
    app.add_handler(
        CommandHandler(["classificacao", "leaderboard"], leaderboard_command)
    )
    app.add_handler(CommandHandler("settings", settings_command))

    # Bet conversation handler
    bet_conv = ConversationHandler(
        entry_points=[CommandHandler("bet", bet_start)],
        states={
            # Patterns keep this conversation from swallowing unrelated button
            # presses (e.g. the /settings keyboard) while it is open.
            BET_CONFIRM: [CallbackQueryHandler(bet_confirm, pattern="^confirm_")],
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
    from app.utils.logger import setup_logging

    setup_logging()
    app = setup_bot()
    logger.info("Starting Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
