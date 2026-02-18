"""Settings handler – user preferences and aggressiveness level."""

from __future__ import annotations

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.telegram.formatters import escape_md


async def settings_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /settings – show user preferences."""
    if update.effective_message is None:
        return

    logger.info(f"/settings from {update.effective_user}")

    keyboard = [
        [InlineKeyboardButton("Conservative", callback_data="agg_conservative")],
        [InlineKeyboardButton("Balanced", callback_data="agg_balanced")],
        [InlineKeyboardButton("Aggressive", callback_data="agg_aggressive")],
        [InlineKeyboardButton("Ultra Aggressive", callback_data="agg_ultra_aggressive")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    current = context.user_data.get("aggressiveness", "balanced") if context.user_data else "balanced"  # type: ignore[union-attr]

    await update.effective_message.reply_text(
        f"Current aggressiveness: *{escape_md(current)}*\n\n"
        "Select your preferred strategy level:",
        parse_mode="MarkdownV2",
        reply_markup=reply_markup,
    )


async def aggressiveness_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle aggressiveness selection callback."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    level = query.data.replace("agg_", "")  # type: ignore[union-attr]
    if context.user_data is not None:
        context.user_data["aggressiveness"] = level

    logger.info(f"Aggressiveness set to {level} by {update.effective_user}")

    await query.edit_message_text(
        f"Aggressiveness set to: *{escape_md(level)}*\n"
        "This will be used for future strategy suggestions\\.",
        parse_mode="MarkdownV2",
    )
