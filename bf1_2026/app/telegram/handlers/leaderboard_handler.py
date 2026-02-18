"""Leaderboard handler – show user rankings."""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.formatters import escape_md


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /leaderboard – display user rankings."""
    if update.effective_message is None:
        return

    logger.info(f"/leaderboard from {update.effective_user}")

    # Placeholder data (in production, query from DB)
    users = [
        {"rank": 1, "name": "User1", "points": 156.0, "bets": 8},
        {"rank": 2, "name": "User2", "points": 142.5, "bets": 8},
        {"rank": 3, "name": "User3", "points": 128.0, "bets": 7},
    ]

    lines = [
        "━━━━━━━━━━━━━━━━━",
        "🏆 *BF1 LEADERBOARD*",
        "━━━━━━━━━━━━━━━━━",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for u in users:
        medal = medals[u["rank"] - 1] if u["rank"] <= 3 else f"  {u['rank']}\\."
        lines.append(
            f"{medal} {escape_md(u['name'])} \\| "
            f"{u['points']:.1f} pts \\| "
            f"{u['bets']} bets"
        )

    lines.append("")
    lines.append("_Updated after each race_")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2"
    )
