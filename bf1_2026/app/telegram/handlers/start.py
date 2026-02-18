"""Start command handler – welcome message and user registration."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /help – welcome message + main menu."""
    if update.effective_user is None or update.effective_message is None:
        return

    user = update.effective_user
    logger.info(f"/start from {user.username} ({user.id})")

    welcome = (
        f"Welcome to *BF1\\-2026 Quantitative System*, {_escape_md(user.first_name)}\\!\n"
        "\n"
        "I'm your AI\\-powered F1 betting assistant\\. "
        "I analyze race data, run Monte Carlo simulations, "
        "and optimize your 15\\-token allocation\\.\n"
        "\n"
        "*Available Commands:*\n"
        "/calendar \\- 2026 Race Calendar\n"
        "/analysis \\- Race Analysis \\(next race\\)\n"
        "/simulate \\- Run Monte Carlo Simulation\n"
        "/bet \\- Place Your Token Allocation\n"
        "/leaderboard \\- User Rankings\n"
        "/settings \\- Your Preferences\n"
        "\n"
        "Use /analysis to see the upcoming race breakdown\\!"
    )

    await update.effective_message.reply_text(welcome, parse_mode="MarkdownV2")


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)
