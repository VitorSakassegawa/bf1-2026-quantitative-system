"""Leaderboard handler – real user rankings from the DB."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database import AsyncSessionLocal
from app.models.user import User
from app.telegram.formatters import escape_md


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /leaderboard – display user rankings by BF1 points."""
    if update.effective_message is None:
        return

    logger.info(f"/leaderboard from {update.effective_user}")

    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(
                select(User).order_by(User.total_points.desc()).limit(10)
            )
        ).scalars().all()

    lines = [
        "━━━━━━━━━━━━━━━━━",
        "🏆 *BF1 LEADERBOARD*",
        "━━━━━━━━━━━━━━━━━",
        "",
    ]

    if not users:
        lines.append("_No bets placed yet. Be the first with /bet_")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, u in enumerate(users):
            medal = medals[i] if i < 3 else f"  {i + 1}\\."
            name = u.username or f"User {u.telegram_id}"
            # Escaped for MarkdownV2 — an unescaped "." made /leaderboard fail
            # with 400 as soon as there was at least one user to show.
            points = escape_md(f"{u.total_points:.1f}")
            lines.append(
                f"{medal} {escape_md(name)} \\| "
                f"{points} pts \\| "
                f"{u.total_bets} bets"
            )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2"
    )
