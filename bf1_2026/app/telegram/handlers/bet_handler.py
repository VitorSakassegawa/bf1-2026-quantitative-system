"""Bet handler – multi-step conversation for placing token allocations."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from app.telegram.formatters import escape_md

# Conversation states
BET_SELECT_RACE = 0
BET_SELECT_STRATEGY = 1
BET_CONFIRM = 2


async def bet_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /bet – start the betting flow."""
    if update.effective_message is None:
        return ConversationHandler.END

    logger.info(f"/bet from {update.effective_user}")

    # Show available races
    keyboard = [
        [InlineKeyboardButton("Next Race", callback_data="race_next")],
        [InlineKeyboardButton("Select from Calendar", callback_data="race_calendar")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.effective_message.reply_text(
        "Select a race to place your bet:", reply_markup=reply_markup
    )
    return BET_SELECT_RACE


async def bet_select_race(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle race selection callback."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    context.user_data["selected_race"] = query.data  # type: ignore[index]

    keyboard = [
        [InlineKeyboardButton("Conservative", callback_data="strat_conservative")],
        [InlineKeyboardButton("Balanced (Recommended)", callback_data="strat_balanced")],
        [InlineKeyboardButton("Aggressive", callback_data="strat_aggressive")],
        [
            InlineKeyboardButton(
                "Ultra Aggressive", callback_data="strat_ultra_aggressive"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "Choose your strategy:", reply_markup=reply_markup
    )
    return BET_SELECT_STRATEGY


async def bet_select_strategy(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle strategy selection and show proposed allocation."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    strategy = query.data.replace("strat_", "")  # type: ignore[union-attr]
    context.user_data["strategy"] = strategy  # type: ignore[index]

    # Placeholder allocation (in production, call StrategyEngine)
    allocation = {
        "VER": 4, "NOR": 3, "LEC": 3, "HAM": 3, "PIA": 2,
    }
    context.user_data["allocation"] = allocation  # type: ignore[index]

    lines = [
        f"Strategy: *{escape_md(strategy.upper())}*\n",
        "Proposed Allocation:",
    ]
    for code, tokens in allocation.items():
        bar = "●" * tokens + "○" * (5 - tokens)
        lines.append(f"  {escape_md(code)}: \\[{escape_md(bar)}\\] {tokens}T")
    lines.append(f"\nTotal: 15 tokens")

    keyboard = [
        [InlineKeyboardButton("Confirm", callback_data="confirm_yes")],
        [InlineKeyboardButton("Cancel", callback_data="confirm_no")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup,
    )
    return BET_CONFIRM


async def bet_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle bet confirmation."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    if query.data == "confirm_yes":
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        allocation = context.user_data.get("allocation", {})  # type: ignore[union-attr]
        strategy = context.user_data.get("strategy", "balanced")  # type: ignore[union-attr]

        logger.info(
            f"Bet confirmed by {update.effective_user}: "
            f"strategy={strategy}, allocation={allocation}"
        )

        await query.edit_message_text(
            f"Bet confirmed at {now}\\!\n"
            f"Strategy: {escape_md(strategy)}\n"
            f"Good luck\\! 🏎",
            parse_mode="MarkdownV2",
        )
    else:
        await query.edit_message_text("Bet cancelled\\.", parse_mode="MarkdownV2")

    return ConversationHandler.END


async def bet_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /cancel during bet flow."""
    if update.effective_message:
        await update.effective_message.reply_text("Bet cancelled.")
    return ConversationHandler.END
